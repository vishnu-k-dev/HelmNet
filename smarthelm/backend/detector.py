"""
detector.py — Eye-state detection using MediaPipe Face Landmarker (Tasks API).

MediaPipe 0.10.14+ removed mp.solutions in favour of the Tasks API.
This module uses FaceLandmarker from mediapipe.tasks.python.vision.
The model file (~7 MB) is downloaded automatically on first run.

Supports two detection modes (config.DETECTION_MODE):
  "EAR"  — Eye Aspect Ratio thresholding (no extra model needed)
  "CNN"  — MediaPipe landmarks + ONNX classifier (Day 2 upgrade)

Standalone test: python detector.py
"""

import sys
import os
import math
import time
import logging
import collections
import urllib.request
from dataclasses import dataclass

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision

sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model path — auto-downloaded on first run
# ---------------------------------------------------------------------------
_MODEL_DIR  = os.path.join(os.path.dirname(__file__), "..", "models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "face_landmarker.task")
_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)


def _ensure_model():
    os.makedirs(_MODEL_DIR, exist_ok=True)
    if not os.path.exists(_MODEL_PATH):
        logger.info("Downloading MediaPipe face landmarker model (~7 MB)...")
        try:
            urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
            logger.info(f"Model saved to {_MODEL_PATH}")
        except Exception as e:
            logger.error(f"Model download failed: {e}")
            raise


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DetectionResult:
    eye_state: str          # "OPEN", "CLOSED", "UNKNOWN"
    confidence: float       # 0.0–1.0
    ear_left: float
    ear_right: float
    ear_avg: float
    ear_smoothed: float
    face_detected: bool
    annotated_frame: np.ndarray  # BGR frame with overlay drawn


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class EyeDetector:
    """
    Wraps MediaPipe FaceLandmarker (Tasks API) for real-time eye-state detection.
    One instance per inference thread — not thread-safe.
    """

    def __init__(self, mode: str = None):
        self.mode = mode or config.DETECTION_MODE

        _ensure_model()

        base_options = mp_tasks.BaseOptions(model_asset_path=_MODEL_PATH)
        options = mp_vision.FaceLandmarkerOptions(
            base_options=base_options,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        self._detector = mp_vision.FaceLandmarker.create_from_options(options)

        # EAR smoothing buffer
        self._ear_buffer = collections.deque(maxlen=config.EAR_SMOOTHING_WINDOW)

        # CNN session (loaded only in CNN mode)
        self._ort_session = None
        self._ort_input_name = None
        if self.mode == "CNN":
            self._load_cnn()

        logger.info(f"EyeDetector ready — mode='{self.mode}'")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def process(self, frame: np.ndarray) -> DetectionResult:
        """Run detection on a single BGR frame. Returns DetectionResult."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        detection = self._detector.detect(mp_image)

        h, w = frame.shape[:2]
        annotated = frame.copy()

        if not detection.face_landmarks:
            return DetectionResult(
                eye_state="UNKNOWN", confidence=0.0,
                ear_left=0.0, ear_right=0.0, ear_avg=0.0, ear_smoothed=0.0,
                face_detected=False,
                annotated_frame=self._draw_no_face(annotated),
            )

        landmarks = detection.face_landmarks[0]   # list of NormalizedLandmark

        # Always compute EAR (used for overlay even in CNN mode)
        ear_left  = self._compute_ear(landmarks, config.LEFT_EYE_INDICES,  w, h)
        ear_right = self._compute_ear(landmarks, config.RIGHT_EYE_INDICES, w, h)
        ear_avg   = (ear_left + ear_right) / 2.0
        self._ear_buffer.append(ear_avg)
        ear_smoothed = float(np.mean(self._ear_buffer))

        # Classify
        if self.mode == "CNN" and self._ort_session is not None:
            left_roi  = self._extract_eye_roi(frame, landmarks, config.LEFT_EYE_INDICES,  w, h)
            right_roi = self._extract_eye_roi(frame, landmarks, config.RIGHT_EYE_INDICES, w, h)
            eye_state, confidence = self._run_cnn(left_roi, right_roi)
        else:
            eye_state, confidence = self._classify_ear(ear_smoothed)

        result = DetectionResult(
            eye_state=eye_state, confidence=confidence,
            ear_left=ear_left, ear_right=ear_right,
            ear_avg=ear_avg, ear_smoothed=ear_smoothed,
            face_detected=True, annotated_frame=None,
        )
        result.annotated_frame = self._draw_overlay(annotated, result, landmarks, w, h)
        return result

    def release(self):
        self._detector.close()

    # ------------------------------------------------------------------
    # EAR helpers
    # ------------------------------------------------------------------

    def _compute_ear(self, landmarks, indices: tuple, w: int, h: int) -> float:
        """EAR = (||P2-P6|| + ||P3-P5||) / (2 * ||P1-P4||)"""
        pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in indices]
        p1, p2, p3, p4, p5, p6 = pts
        ear = (_dist(p2, p6) + _dist(p3, p5)) / (2.0 * _dist(p1, p4) + 1e-6)
        return float(ear)

    def _classify_ear(self, ear_smoothed: float):
        if ear_smoothed > config.EAR_OPEN_THRESHOLD:
            confidence = min(1.0, 0.5 + (ear_smoothed - config.EAR_OPEN_THRESHOLD) / 0.15)
            return "OPEN", confidence
        elif ear_smoothed < config.EAR_CLOSED_THRESHOLD:
            confidence = min(1.0, 0.5 + (config.EAR_CLOSED_THRESHOLD - ear_smoothed) / 0.15)
            return "CLOSED", confidence
        return "UNKNOWN", 0.0

    # ------------------------------------------------------------------
    # CNN helpers (Day 2)
    # ------------------------------------------------------------------

    def _load_cnn(self):
        try:
            import onnxruntime as ort
            if not os.path.exists(config.CNN_MODEL_PATH):
                raise FileNotFoundError(f"ONNX model not found: {config.CNN_MODEL_PATH}")
            self._ort_session = ort.InferenceSession(
                config.CNN_MODEL_PATH, providers=["CPUExecutionProvider"]
            )
            self._ort_input_name = self._ort_session.get_inputs()[0].name
            logger.info(f"CNN model loaded: {config.CNN_MODEL_PATH}")
        except Exception as e:
            logger.error(f"CNN load failed ({e}), falling back to EAR")
            self.mode = "EAR"

    def _extract_eye_roi(self, frame, landmarks, indices, w, h):
        xs = [landmarks[i].x * w for i in indices]
        ys = [landmarks[i].y * h for i in indices]
        pad = 10
        x1 = max(0, int(min(xs)) - pad)
        y1 = max(0, int(min(ys)) - pad)
        x2 = min(w, int(max(xs)) + pad)
        y2 = min(h, int(max(ys)) + pad)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return np.zeros((*config.CNN_INPUT_SIZE[::-1], 1), dtype=np.float32)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, config.CNN_INPUT_SIZE)
        return (resized.astype(np.float32) / 255.0)[:, :, np.newaxis]

    def _run_cnn(self, left_roi, right_roi):
        def _infer(roi):
            inp = roi.transpose(2, 0, 1)[np.newaxis, :]
            out = self._ort_session.run(None, {self._ort_input_name: inp})[0]
            return float(out[0][1]) if out.shape[-1] == 2 else float(out[0][0])
        closed_prob = (_infer(left_roi) + _infer(right_roi)) / 2.0
        if closed_prob >= config.CNN_CLOSED_THRESHOLD:
            return "CLOSED", closed_prob
        return "OPEN", 1.0 - closed_prob

    # ------------------------------------------------------------------
    # Overlay drawing
    # ------------------------------------------------------------------

    def _draw_overlay(self, frame, result, landmarks, w, h):
        color_map = {
            "OPEN":    (0, 220, 0),
            "CLOSED":  (0, 0, 220),
            "UNKNOWN": (120, 120, 120),
        }
        dot_color = color_map.get(result.eye_state, (120, 120, 120))

        for idx in (*config.LEFT_EYE_INDICES, *config.RIGHT_EYE_INDICES):
            lm = landmarks[idx]
            cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 2, dot_color, -1)

        lines = [
            f"EAR: {result.ear_smoothed:.3f}",
            f"State: {result.eye_state}",
            f"Conf:  {result.confidence:.2f}",
            f"Mode:  {self.mode}",
        ]
        for i, line in enumerate(lines):
            y = 24 + i * 22
            cv2.putText(frame, line, (9, y + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
            cv2.putText(frame, line, (8, y),     cv2.FONT_HERSHEY_SIMPLEX, 0.6, dot_color,  1)
        return frame

    def _draw_no_face(self, frame):
        cv2.putText(frame, "No face detected", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)
        return frame


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _dist(p1, p2) -> float:
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


# ---------------------------------------------------------------------------
# Standalone test: python detector.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("SmartHelm detector test — press Q to quit")
    cap = cv2.VideoCapture(config.WEBCAM_INDEX)
    detector = EyeDetector()
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        result = detector.process(frame)
        print(
            f"\rState={result.eye_state:7s}  EAR={result.ear_smoothed:.3f}  "
            f"Conf={result.confidence:.2f}  Face={result.face_detected}",
            end="", flush=True,
        )
        cv2.imshow("SmartHelm — Eye Detector", result.annotated_frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    detector.release()
    cv2.destroyAllWindows()
    print()
