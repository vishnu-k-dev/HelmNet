"""
tests/test_perclos.py — Unit tests for PerclosTracker.

Run: python -m pytest tests/ -v
  OR: python tests/test_perclos.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from perclos import PerclosTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def feed_frames(tracker, eye_state: str, count: int, fps: float = 10.0, start_t: float = 0.0):
    """Feed `count` frames of eye_state at `fps`. Returns last result and end timestamp."""
    dt = 1.0 / fps
    result = None
    t = start_t
    for _ in range(count):
        result = tracker.update(eye_state, timestamp=t)
        t += dt
    return result, t


# ---------------------------------------------------------------------------
# PERCLOS tests
# ---------------------------------------------------------------------------

def test_zero_perclos_when_all_open():
    tracker = PerclosTracker(window_seconds=10, perclos_threshold=30.0)
    result, _ = feed_frames(tracker, "OPEN", 100, fps=10.0, start_t=0.0)
    assert result["perclos"] == 0.0
    assert result["alert_perclos"] is False


def test_100_perclos_when_all_closed():
    tracker = PerclosTracker(window_seconds=10, perclos_threshold=30.0)
    result, _ = feed_frames(tracker, "CLOSED", 100, fps=10.0, start_t=0.0)
    assert result["perclos"] == 100.0
    assert result["alert_perclos"] is True


def test_perclos_threshold_triggered_at_30_percent():
    """30% of frames closed → alert_perclos should be True."""
    tracker = PerclosTracker(window_seconds=60, perclos_threshold=30.0)
    t = 0.0
    # Feed 70 open frames
    result, t = feed_frames(tracker, "OPEN", 70, fps=10.0, start_t=t)
    assert result["alert_perclos"] is False
    # Feed 30 closed frames (total 100 frames = 30% closed)
    result, t = feed_frames(tracker, "CLOSED", 30, fps=10.0, start_t=t)
    assert result["perclos"] >= 30.0
    assert result["alert_perclos"] is True


def test_perclos_below_threshold_no_alert():
    """29% closed → no PERCLOS alert."""
    tracker = PerclosTracker(window_seconds=60, perclos_threshold=30.0)
    t = 0.0
    result, t = feed_frames(tracker, "OPEN", 71, fps=10.0, start_t=t)
    result, t = feed_frames(tracker, "CLOSED", 29, fps=10.0, start_t=t)
    assert result["perclos"] < 30.0
    assert result["alert_perclos"] is False


def test_perclos_rolling_window_drops_old_frames():
    """
    Fill 5s window with CLOSED, then feed 5s+ of OPEN.
    PERCLOS should drop back to 0% once all old closed frames expire.
    """
    tracker = PerclosTracker(window_seconds=5, perclos_threshold=30.0)
    t = 0.0
    # 5 seconds of CLOSED at 10fps = 50 frames
    result, t = feed_frames(tracker, "CLOSED", 50, fps=10.0, start_t=t)
    assert result["perclos"] == 100.0

    # 6 seconds of OPEN (more than window_seconds) — all old frames should expire
    result, t = feed_frames(tracker, "OPEN", 60, fps=10.0, start_t=t)
    assert result["perclos"] == 0.0
    assert result["alert_perclos"] is False


def test_unknown_frames_not_counted_as_closed():
    """UNKNOWN frames should not inflate PERCLOS."""
    tracker = PerclosTracker(window_seconds=10, perclos_threshold=30.0)
    t = 0.0
    result, t = feed_frames(tracker, "UNKNOWN", 100, fps=10.0, start_t=t)
    assert result["perclos"] == 0.0
    assert result["alert_perclos"] is False


# ---------------------------------------------------------------------------
# Continuous closure tests
# ---------------------------------------------------------------------------

def test_no_continuous_alert_for_short_closure():
    """0.5s closure < 1.5s threshold → no continuous alert."""
    tracker = PerclosTracker(closure_duration_threshold=1.5)
    t = 0.0
    result, t = feed_frames(tracker, "CLOSED", 5, fps=10.0, start_t=t)  # 0.5s
    assert result["alert_continuous"] is False
    assert result["continuous_closure_sec"] < 1.5


def test_continuous_alert_after_threshold():
    """2 seconds of continuous closure → alert_continuous=True."""
    tracker = PerclosTracker(closure_duration_threshold=1.5)
    t = 0.0
    result, t = feed_frames(tracker, "CLOSED", 20, fps=10.0, start_t=t)  # 2s
    assert result["alert_continuous"] is True
    assert result["continuous_closure_sec"] >= 1.5


def test_continuous_timer_resets_on_open():
    """Eyes open after closure → continuous_closure_sec resets to 0."""
    tracker = PerclosTracker(closure_duration_threshold=1.5)
    t = 0.0
    result, t = feed_frames(tracker, "CLOSED", 20, fps=10.0, start_t=t)  # 2s → alert
    assert result["alert_continuous"] is True

    result, t = feed_frames(tracker, "OPEN", 1, fps=10.0, start_t=t)
    assert result["continuous_closure_sec"] == 0.0


def test_normal_blink_no_alert():
    """A 200ms blink (< 1.5s threshold) should not trigger any alert."""
    tracker = PerclosTracker(closure_duration_threshold=1.5, perclos_threshold=30.0)
    t = 0.0
    # 3s open
    result, t = feed_frames(tracker, "OPEN", 30, fps=10.0, start_t=t)
    # 200ms blink
    result, t = feed_frames(tracker, "CLOSED", 2, fps=10.0, start_t=t)
    # Return to open
    result, t = feed_frames(tracker, "OPEN", 30, fps=10.0, start_t=t)
    assert result["alert_continuous"] is False
    assert result["perclos"] < 30.0


# ---------------------------------------------------------------------------
# Alert combined
# ---------------------------------------------------------------------------

def test_alert_active_is_or_of_both_triggers():
    """alert_active should be True if either trigger fires."""
    tracker = PerclosTracker(
        window_seconds=10, perclos_threshold=30.0, closure_duration_threshold=1.5
    )
    t = 0.0
    result, t = feed_frames(tracker, "CLOSED", 20, fps=10.0, start_t=t)  # 2s → continuous only
    assert result["alert_continuous"] is True
    assert result["alert_active"] is True


def test_get_perclos_returns_float():
    tracker = PerclosTracker()
    tracker.update("CLOSED", timestamp=0.0)
    val = tracker.get_perclos()
    assert isinstance(val, float)
    assert 0.0 <= val <= 100.0


def test_reset_clears_all_state():
    tracker = PerclosTracker(closure_duration_threshold=1.5)
    t = 0.0
    feed_frames(tracker, "CLOSED", 30, fps=10.0, start_t=t)
    tracker.reset()
    assert tracker.get_perclos() == 0.0
    result = tracker.update("OPEN", timestamp=0.0)
    assert result["perclos"] == 0.0
    assert result["continuous_closure_sec"] == 0.0
    assert result["alert_active"] is False


# ---------------------------------------------------------------------------
# Entry point for direct run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_zero_perclos_when_all_open,
        test_100_perclos_when_all_closed,
        test_perclos_threshold_triggered_at_30_percent,
        test_perclos_below_threshold_no_alert,
        test_perclos_rolling_window_drops_old_frames,
        test_unknown_frames_not_counted_as_closed,
        test_no_continuous_alert_for_short_closure,
        test_continuous_alert_after_threshold,
        test_continuous_timer_resets_on_open,
        test_normal_blink_no_alert,
        test_alert_active_is_or_of_both_triggers,
        test_get_perclos_returns_float,
        test_reset_clears_all_state,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} tests passed")
    if failed:
        sys.exit(1)
