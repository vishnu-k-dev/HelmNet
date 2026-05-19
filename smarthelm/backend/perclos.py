"""
perclos.py — PERCLOS tracker and continuous-closure detector.

PERCLOS = (closed_frames / total_frames) * 100 over a rolling time window.

Two independent alert triggers:
  1. alert_perclos     — PERCLOS >= PERCLOS_THRESHOLD over 60-second window
  2. alert_continuous  — Eyes closed continuously for >= 1.5 seconds

Standalone test:
    python perclos.py
"""

import sys
import os
import time
import collections
import logging
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)


class PerclosTracker:
    """
    Maintains a rolling window of eye-state samples and evaluates
    both drowsiness triggers on every update call.

    Not thread-safe — own one instance per inference thread.
    """

    def __init__(
        self,
        window_seconds: int = None,
        perclos_threshold: float = None,
        closure_duration_threshold: float = None,
        alert_clear_delay: float = None,
    ):
        self._window_seconds = window_seconds or config.PERCLOS_WINDOW_SECONDS
        self._perclos_threshold = perclos_threshold or config.PERCLOS_THRESHOLD
        self._closure_threshold = closure_duration_threshold or config.CLOSED_DURATION_THRESHOLD
        self._clear_delay = alert_clear_delay or config.ALERT_CLEAR_DELAY

        # Deque of (timestamp: float, is_closed: bool)
        self._window: collections.deque = collections.deque()

        # Continuous closure tracking
        self._closure_start: Optional[float] = None
        self._continuous_closure_sec: float = 0.0
        self._prev_state: str = "UNKNOWN"

        # Alert auto-clear tracking
        self._open_since: Optional[float] = None  # when eyes last opened
        self._alert_was_active: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, eye_state: str, timestamp: Optional[float] = None) -> dict:
        """
        Call once per frame with the current eye_state.

        eye_state: "OPEN", "CLOSED", or "UNKNOWN"
        timestamp: defaults to time.time()

        Returns a dict:
          {
            "perclos": float,                  # 0–100
            "continuous_closure_sec": float,
            "alert_perclos": bool,
            "alert_continuous": bool,
            "alert_active": bool,              # OR of both
            "window_sample_count": int,
          }
        """
        now = timestamp if timestamp is not None else time.time()

        # 1. Record sample (UNKNOWN is NOT counted as closed)
        is_closed = eye_state == "CLOSED"
        self._window.append((now, is_closed))
        self._prune_window(now)

        # 2. Update continuous closure timer
        self._update_closure_timer(eye_state, now)

        # 3. Compute PERCLOS
        total = len(self._window)
        closed_count = sum(1 for _, c in self._window if c)
        perclos = (closed_count / total * 100.0) if total > 0 else 0.0

        # 4. Evaluate alert triggers
        alert_perclos = perclos >= self._perclos_threshold
        alert_continuous = self._continuous_closure_sec >= self._closure_threshold

        # 5. Apply auto-clear delay (eyes must be open for clear_delay before alert resets)
        alert_active = alert_perclos or alert_continuous
        if alert_active:
            self._open_since = None
            self._alert_was_active = True
        elif self._alert_was_active:
            # Eyes are now open — start grace timer
            if self._open_since is None:
                self._open_since = now
            elapsed_open = now - self._open_since
            if elapsed_open >= self._clear_delay:
                self._alert_was_active = False
                self._open_since = None
            else:
                # Still within grace period — keep alert active
                alert_active = True

        self._prev_state = eye_state

        return {
            "perclos": round(perclos, 2),
            "continuous_closure_sec": round(self._continuous_closure_sec, 2),
            "alert_perclos": alert_perclos,
            "alert_continuous": alert_continuous,
            "alert_active": alert_active,
            "window_sample_count": total,
        }

    def get_perclos(self) -> float:
        """Return current PERCLOS without updating state."""
        total = len(self._window)
        if total == 0:
            return 0.0
        closed_count = sum(1 for _, c in self._window if c)
        return round(closed_count / total * 100.0, 2)

    def reset(self):
        """Clear all state. Useful between test sessions."""
        self._window.clear()
        self._closure_start = None
        self._continuous_closure_sec = 0.0
        self._prev_state = "UNKNOWN"
        self._open_since = None
        self._alert_was_active = False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _prune_window(self, now: float):
        """Remove samples older than window_seconds from the left."""
        cutoff = now - self._window_seconds
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def _update_closure_timer(self, eye_state: str, now: float):
        """
        Track how long eyes have been continuously closed.
        UNKNOWN frames do NOT reset the timer (conservative — avoids
        false clears during brief tracking loss).
        """
        if eye_state == "CLOSED":
            if self._closure_start is None:
                self._closure_start = now
            self._continuous_closure_sec = now - self._closure_start
        elif eye_state == "OPEN":
            self._closure_start = None
            self._continuous_closure_sec = 0.0
        # UNKNOWN: leave timer as-is


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tracker = PerclosTracker(window_seconds=10, perclos_threshold=30.0, closure_duration_threshold=2.0)

    print("Simulating 10-second sequence: 3s open, 3s closed, 4s open")
    start = time.time()
    t = start

    sequence = [
        (3.0, "OPEN"),
        (3.0, "CLOSED"),
        (4.0, "OPEN"),
    ]
    for duration, state in sequence:
        end = t + duration
        while t < end:
            result = tracker.update(state, timestamp=t)
            t += 0.1  # simulate 10 FPS
        print(
            f"After {state} for {duration}s: "
            f"PERCLOS={result['perclos']:.1f}%  "
            f"closure={result['continuous_closure_sec']:.1f}s  "
            f"alert={result['alert_active']}"
        )
