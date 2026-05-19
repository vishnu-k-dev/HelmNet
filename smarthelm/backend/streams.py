"""
streams.py — MJPEG stream capture with auto-reconnect.

Three classes:
  MJPEGStream      — wraps OpenCV VideoCapture, reconnects on failure
  StaticFrameStream — returns a static placeholder (for offline cameras)
  make_stream()    — factory that applies webcam fallback logic from config
"""

import threading
import time
import logging
import urllib.request
import concurrent.futures
import cv2
import numpy as np
import sys
import os

# Add parent so this file can be run standalone during development
sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)


class MJPEGStream:
    """
    Thread-safe MJPEG capture with automatic reconnect.

    Usage:
        stream = MJPEGStream("http://192.168.1.101/stream", name="CAM1")
        ok, frame = stream.read()  # call in your loop
        stream.release()           # on shutdown
    """

    def __init__(self, source, name: str = "CAM", reconnect_delay: float = 2.0):
        """
        source: URL string for ESP32 or int index for webcam
        name: label used in log messages
        reconnect_delay: seconds between reconnect attempts
        """
        self.source = source
        self.name = name
        self.reconnect_delay = reconnect_delay

        self._cap = cv2.VideoCapture()
        self._lock = threading.Lock()
        self._connected = False
        self._last_good_frame = None
        self._last_frame_time = 0.0
        self._running = True

        # Open the capture immediately in the calling thread
        self._try_open()

        # Background daemon for reconnect
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True, name=f"{name}-reconnect"
        )
        self._reconnect_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self):
        """
        Returns (ok, frame).

        Tries cap.read() first. If that fails but we have a frame that is
        less than 1 second old, returns the stale frame (handles transient
        drops). Returns (False, None) only if stream has been dead longer
        than reconnect_delay.
        """
        with self._lock:
            if self._connected:
                ret, frame = self._cap.read()
                if ret and frame is not None:
                    self._last_good_frame = frame
                    self._last_frame_time = time.time()
                    return True, frame
                else:
                    # Mark disconnected; reconnect loop will handle it
                    self._connected = False
                    logger.warning(f"{self.name}: read() failed, will reconnect")

            # Return stale frame up to 1 second old
            if (
                self._last_good_frame is not None
                and (time.time() - self._last_frame_time) < 1.0
            ):
                return True, self._last_good_frame

            return False, None

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def release(self):
        """Stop reconnect loop and release VideoCapture."""
        self._running = False
        with self._lock:
            self._cap.release()
            self._connected = False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _try_open(self) -> bool:
        """Attempt to open the capture source. Returns True on success."""
        with self._lock:
            self._cap.release()
            # For URLs, hint OpenCV to use FFMPEG backend
            if isinstance(self.source, str):
                self._cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            else:
                self._cap = cv2.VideoCapture(self.source)

            # Give the stream a moment to negotiate
            time.sleep(0.5)
            if self._cap.isOpened():
                self._connected = True
                logger.info(f"{self.name}: connected to {self.source}")
                return True
            self._connected = False
            return False

    def _reconnect_loop(self):
        """Runs in daemon thread. Reconnects whenever _connected is False."""
        while self._running:
            with self._lock:
                need_reconnect = not self._connected
            if need_reconnect:
                logger.info(f"{self.name}: attempting reconnect to {self.source}")
                if not self._try_open():
                    logger.warning(
                        f"{self.name}: reconnect failed, retry in {self.reconnect_delay}s"
                    )
                    time.sleep(self.reconnect_delay)
            else:
                time.sleep(0.5)


# ---------------------------------------------------------------------------

class StaticFrameStream:
    """
    Returns a static placeholder frame.

    Used for CAM2/CAM3 when hardware is unavailable and there is no
    webcam fallback. Same interface as MJPEGStream so callers are unaware.
    """

    def __init__(self, label: str, width: int = 640, height: int = 480):
        self.name = label
        self._frame = self._make_placeholder(label, width, height)

    def read(self):
        return True, self._frame.copy()

    def is_connected(self) -> bool:
        return True

    def release(self):
        pass

    @staticmethod
    def _make_placeholder(label: str, w: int, h: int) -> np.ndarray:
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # Grey border
        cv2.rectangle(img, (2, 2), (w - 2, h - 2), (60, 60, 60), 2)
        # Camera icon hint
        text1 = label
        text2 = "OFFLINE"
        font = cv2.FONT_HERSHEY_SIMPLEX
        (tw1, th1), _ = cv2.getTextSize(text1, font, 1.0, 2)
        (tw2, th2), _ = cv2.getTextSize(text2, font, 0.7, 1)
        cv2.putText(img, text1, ((w - tw1) // 2, h // 2 - 10), font, 1.0, (120, 120, 120), 2)
        cv2.putText(img, text2, ((w - tw2) // 2, h // 2 + th2 + 10), font, 0.7, (80, 80, 80), 1)
        return img


# ---------------------------------------------------------------------------

def _probe_url(url: str, timeout: float = 1.0) -> bool:
    """HTTP probe — True if server responds within timeout."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def make_stream(source, name: str, _pre_probed: bool = None):
    """
    Factory function. Applies webcam fallback logic from config.

    Rules:
    - If source is an int, use it directly as a webcam index.
    - If source is a URL and USE_WEBCAM_FALLBACK is True:
        * Probe the URL (1-second HTTP timeout).
        * On success → MJPEGStream with that URL.
        * On failure → MJPEGStream with WEBCAM_INDEX (laptop cam).
          Only one webcam available, so second/third cameras that fail
          over to webcam get StaticFrameStream instead.
    - If USE_WEBCAM_FALLBACK is False, always use the URL.
    - _pre_probed: if provided (True/False), skips the probe step
      (used by make_streams_parallel to avoid double-probing).
    """
    if isinstance(source, int):
        logger.info(f"{name}: using webcam index {source}")
        return MJPEGStream(source, name=name)

    if not config.USE_WEBCAM_FALLBACK:
        return MJPEGStream(source, name=name)

    # Probe URL (unless result pre-supplied by parallel probe)
    if _pre_probed is None:
        logger.info(f"{name}: probing {source} (1s timeout)...")
        reachable = _probe_url(source, timeout=1.0)
    else:
        reachable = _pre_probed

    if reachable:
        logger.info(f"{name}: URL reachable, using MJPEG stream")
        return MJPEGStream(source, name=name)

    # Webcam fallback — only give it to the first camera that requests it
    if not _webcam_claimed():
        _claim_webcam()
        logger.warning(f"{name}: URL unreachable, falling back to webcam {config.WEBCAM_INDEX}")
        return MJPEGStream(config.WEBCAM_INDEX, name=name)

    logger.warning(f"{name}: URL unreachable and webcam already claimed, using placeholder")
    return StaticFrameStream(name)


def make_streams_parallel(sources: list, names: list) -> list:
    """
    Probe all URLs concurrently (1s max), then create streams in order.
    Order matters: first stream wins the webcam fallback.

    sources: [cam1_url, cam2_url, cam3_url]
    names:   ["CAM1",   "CAM2",   "CAM3"]
    Returns: list of stream objects in same order.
    """
    url_sources = {
        i: s for i, s in enumerate(sources)
        if isinstance(s, str) and config.USE_WEBCAM_FALLBACK
    }

    probe_results = {}
    if url_sources:
        logger.info(f"Probing {len(url_sources)} camera URL(s) in parallel (1s timeout)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(url_sources)) as ex:
            futures = {ex.submit(_probe_url, src, 1.0): i for i, src in url_sources.items()}
            for fut in concurrent.futures.as_completed(futures):
                i = futures[fut]
                probe_results[i] = fut.result()

    streams = []
    for i, (src, name) in enumerate(zip(sources, names)):
        pre = probe_results.get(i)  # None if not a URL or webcam fallback disabled
        streams.append(make_stream(src, name=name, _pre_probed=pre))
    return streams


# Single-use webcam tracking so only one stream gets the fallback
_webcam_claimed_flag = False
_webcam_lock = threading.Lock()


def _webcam_claimed() -> bool:
    with _webcam_lock:
        return _webcam_claimed_flag


def _claim_webcam():
    global _webcam_claimed_flag
    with _webcam_lock:
        _webcam_claimed_flag = True
