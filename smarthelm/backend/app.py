"""
app.py — SmartHelm Flask server and inference integration hub.

Start the system:
    cd smarthelm/backend
    python app.py

Then open: http://localhost:5000

Threading model:
  Thread 0 (main): Flask HTTP server (threaded=True)
  Thread 1 (daemon): inference_loop — reads cam1, runs detection, updates SharedState
  Threads 2-4 (daemon): MJPEGStream internal reconnect loops
  Thread 5 (daemon): paho-mqtt network loop

IMPORTANT: use_reloader=False prevents Flask's dev reloader from forking
the process and starting the inference thread twice.
"""

import sys
import os
import time
import csv
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from flask import Flask, Response, render_template, jsonify

# Ensure backend/ is on the path when run from project root
sys.path.insert(0, os.path.dirname(__file__))

import config
from streams import make_streams_parallel
from detector import EyeDetector
from perclos import PerclosTracker
from alerts import AlertManager
from mqtt_client import MQTTPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("smarthelm.app")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

@dataclass
class SharedState:
    """
    Single source of truth between the inference thread and Flask threads.
    ALL mutable fields must be read/written under self.lock.
    """
    lock: threading.Lock = field(default_factory=threading.Lock)

    # Detection
    eye_state: str = "UNKNOWN"
    confidence: float = 0.0
    ear_smoothed: float = 0.0
    face_detected: bool = False

    # Drowsiness
    perclos: float = 0.0
    continuous_closure_seconds: float = 0.0
    alert_active: bool = False

    # Performance
    fps: float = 0.0
    inference_latency_ms: float = 0.0

    # Video frame (annotated, BGR)
    latest_frame: Optional[np.ndarray] = None  # always .copy() under lock

    # Event history (last 20 alert events)
    events: list = field(default_factory=list)

    # Detection mode label (read-only after init)
    detection_mode: str = config.DETECTION_MODE


# Global instances — created in create_app(), referenced by route handlers
_state = SharedState()
_cam1_stream = None   # created first so it wins the webcam fallback
_cam2_stream = None
_cam3_stream = None

# Pre-generated black placeholder JPEG for when stream is not yet ready
_placeholder_jpeg: bytes = b""


def _make_placeholder_jpeg(label: str = "CONNECTING...") -> bytes:
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(img, label, (180, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (80, 80, 80), 2)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return buf.tobytes()


# ---------------------------------------------------------------------------
# Inference loop (runs in background daemon thread)
# ---------------------------------------------------------------------------

def inference_loop(
    stream,
    state: SharedState,
    mqtt_pub: MQTTPublisher,
    alert_mgr: AlertManager,
):
    """
    Captures frames from cam1, runs eye detection, updates SharedState.
    Loops forever until process exits.
    stream is pre-created in create_app() so CAM1 wins the webcam fallback.
    """
    logger.info("Inference loop starting...")
    detector = EyeDetector(mode=config.DETECTION_MODE)
    perclos_tracker = PerclosTracker()

    fps_alpha = 0.1           # EMA smoothing factor for FPS
    last_log_time = 0.0
    last_mqtt_time = 0.0
    frame_start = time.time()
    mqtt_interval = 1.0 / config.MQTT_PUBLISH_HZ

    while True:
        try:
            ok, frame = stream.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue

            t0 = time.time()

            # --- Detection ---
            result = detector.process(frame)

            # --- Drowsiness ---
            perclos_result = perclos_tracker.update(result.eye_state)

            # --- Alerts ---
            reason = (
                "CONTINUOUS_CLOSURE"
                if perclos_result["alert_continuous"]
                else "PERCLOS"
            )
            if perclos_result["alert_active"]:
                alert_mgr.trigger(reason=reason)
            else:
                alert_mgr.clear()

            # --- Update shared state ---
            t1 = time.time()
            elapsed = t1 - t0
            latency_ms = elapsed * 1000.0

            # FPS: exponential moving average of inter-frame interval
            frame_interval = t1 - frame_start
            frame_start = t1
            current_fps = 1.0 / max(frame_interval, 0.001)

            with state.lock:
                # Smooth FPS
                if state.fps == 0.0:
                    state.fps = current_fps
                else:
                    state.fps = fps_alpha * current_fps + (1 - fps_alpha) * state.fps

                state.eye_state = result.eye_state
                state.confidence = result.confidence
                state.ear_smoothed = result.ear_smoothed
                state.face_detected = result.face_detected
                state.perclos = perclos_result["perclos"]
                state.continuous_closure_seconds = perclos_result["continuous_closure_sec"]
                state.inference_latency_ms = latency_ms

                prev_alert = state.alert_active
                state.alert_active = alert_mgr.is_active()

                # Log new alert events
                if state.alert_active and not prev_alert:
                    event = {
                        "ts": int(time.time()),
                        "type": reason if perclos_result["alert_active"] else "ALERT",
                        "perclos": perclos_result["perclos"],
                        "continuous_sec": perclos_result["continuous_closure_sec"],
                    }
                    state.events.append(event)
                    state.events = state.events[-20:]  # keep last 20

                # Store annotated frame
                if result.annotated_frame is not None:
                    state.latest_frame = result.annotated_frame.copy()

            # --- MQTT (throttled) ---
            if t1 - last_mqtt_time >= mqtt_interval:
                with state.lock:
                    es = state.eye_state
                    cf = state.confidence
                    pc = state.perclos
                    al = state.alert_active
                mqtt_pub.publish("CAM1", es, cf, pc, al)
                last_mqtt_time = t1

            # --- CSV logging (throttled to 1 Hz) ---
            if config.LOG_TO_CSV and (t1 - last_log_time) >= config.LOG_INTERVAL_SECONDS:
                _log_csv(state)
                last_log_time = t1

        except Exception as e:
            logger.error(f"Inference loop error: {e}", exc_info=True)
            time.sleep(0.1)


def _log_csv(state: SharedState):
    """Append one row to logs/events.csv."""
    try:
        log_path = os.path.join(config.LOG_DIR, config.LOG_CSV_FILENAME)
        os.makedirs(config.LOG_DIR, exist_ok=True)
        write_header = not os.path.exists(log_path)
        with state.lock:
            row = [
                int(time.time()),
                state.eye_state,
                round(state.confidence, 3),
                round(state.ear_smoothed, 4),
                round(state.perclos, 2),
                state.alert_active,
                round(state.continuous_closure_seconds, 2),
                round(state.fps, 1),
            ]
        with open(log_path, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "timestamp", "eye_state", "confidence", "ear_smoothed",
                    "perclos", "alert", "continuous_closure_sec", "fps",
                ])
            writer.writerow(row)
    except Exception as e:
        logger.warning(f"CSV log error: {e}")


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "..", "dashboard", "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "..", "dashboard", "static"),
)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video_feed/cam1")
def video_feed_cam1():
    """MJPEG stream of annotated CAM1 frames from the inference thread."""
    return Response(
        _gen_cam1_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/video_feed/cam2")
def video_feed_cam2():
    """Raw MJPEG proxy for CAM2 (no inference)."""
    return Response(
        _gen_raw_frames(_cam2_stream, "CAM2"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/video_feed/cam3")
def video_feed_cam3():
    """Raw MJPEG proxy for CAM3 (no inference)."""
    return Response(
        _gen_raw_frames(_cam3_stream, "CAM3"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/status")
def api_status():
    with _state.lock:
        data = {
            "eye_state": _state.eye_state,
            "confidence": round(_state.confidence, 3),
            "ear_smoothed": round(_state.ear_smoothed, 4),
            "perclos": round(_state.perclos, 2),
            "alert": _state.alert_active,
            "continuous_closure_sec": round(_state.continuous_closure_seconds, 2),
            "face_detected": _state.face_detected,
            "fps": round(_state.fps, 1),
            "latency_ms": round(_state.inference_latency_ms, 1),
            "detection_mode": _state.detection_mode,
            "timestamp": int(time.time()),
        }
    return jsonify(data)


@app.route("/api/events")
def api_events():
    with _state.lock:
        events = list(_state.events)
    return jsonify(events)


# ---------------------------------------------------------------------------
# MJPEG generators
# ---------------------------------------------------------------------------

def _gen_cam1_frames():
    """Yield annotated frames from SharedState as multipart JPEG."""
    while True:
        with _state.lock:
            frame = _state.latest_frame.copy() if _state.latest_frame is not None else None

        if frame is None:
            jpeg = _placeholder_jpeg
        else:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            jpeg = buf.tobytes() if ok else _placeholder_jpeg

        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        time.sleep(config.MJPEG_FRAME_DELAY)


def _gen_raw_frames(stream, name: str):
    """Yield raw frames from a camera stream as multipart JPEG."""
    while True:
        if stream is None:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + _placeholder_jpeg + b"\r\n"
            time.sleep(0.1)
            continue

        ok, frame = stream.read()
        if not ok or frame is None:
            jpeg = _placeholder_jpeg
        else:
            ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            jpeg = buf.tobytes() if ok2 else _placeholder_jpeg

        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        time.sleep(config.MJPEG_FRAME_DELAY)


# ---------------------------------------------------------------------------
# App factory + entry point
# ---------------------------------------------------------------------------

def create_app():
    """
    Initialize all subsystems, start the inference thread, return the Flask app.
    Call this once before app.run().
    """
    global _cam1_stream, _cam2_stream, _cam3_stream, _placeholder_jpeg

    logger.info("SmartHelm starting up...")

    _placeholder_jpeg = _make_placeholder_jpeg()

    # MQTT
    mqtt_pub = MQTTPublisher()
    mqtt_pub.connect()

    # Alerts
    alert_mgr = AlertManager()

    # Probe all 3 camera URLs in parallel (1s timeout each → ~1s total, not ~9s).
    # CAM1 is first in the list so it wins the webcam fallback priority.
    cam1, cam2, cam3 = make_streams_parallel(
        sources=[config.CAM1_URL, config.CAM2_URL, config.CAM3_URL],
        names=["CAM1", "CAM2", "CAM3"],
    )
    _cam1_stream, _cam2_stream, _cam3_stream = cam1, cam2, cam3

    # Inference thread — pass the already-created stream
    t = threading.Thread(
        target=inference_loop,
        args=(_cam1_stream, _state, mqtt_pub, alert_mgr),
        daemon=True,
        name="inference",
    )
    t.start()
    logger.info("Inference thread started")
    logger.info(f"Dashboard: http://localhost:{config.FLASK_PORT}")

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        threaded=True,
        use_reloader=False,  # MUST be False — reloader forks the process
        debug=False,
    )
