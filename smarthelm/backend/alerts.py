"""
alerts.py — Alert state management and audio output.

Uses winsound.Beep (Windows stdlib) so no extra packages are needed.
Audio runs in a daemon thread so the inference loop is never blocked.

Supports:
  - alert trigger with cooldown (prevents beep spam)
  - alert clear
  - alert active state query
"""

import sys
import os
import time
import threading
import logging
import platform

sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)

# Runtime check — fall back gracefully on non-Windows
_IS_WINDOWS = platform.system() == "Windows"
if _IS_WINDOWS:
    import winsound


class AlertManager:
    """
    Manages drowsiness alert state and audio feedback.

    Thread-safe: may be called from the inference thread while Flask
    routes read is_active() from request threads.
    """

    def __init__(
        self,
        cooldown_seconds: float = None,
        freq: int = None,
        duration_ms: int = None,
        beep_count: int = None,
    ):
        self._cooldown = cooldown_seconds or config.ALERT_COOLDOWN_SECONDS
        self._freq = freq or config.ALERT_BEEP_FREQUENCY
        self._duration_ms = duration_ms or config.ALERT_BEEP_DURATION_MS
        self._beep_count = beep_count or config.ALERT_BEEP_COUNT

        self._alert_active = False
        self._last_beep_time: float = 0.0
        self._last_sms_time: float = 0.0
        self._beeping = threading.Event()  # set while a beep thread is running
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trigger(self, reason: str = "DROWSINESS"):
        """
        Mark alert as active.
        Fires audio if cooldown has elapsed and no beep is in progress.
        reason: logged string, e.g. "CONTINUOUS_CLOSURE" or "PERCLOS"
        """
        with self._lock:
            if not self._alert_active:
                logger.warning(f"ALERT TRIGGERED — reason: {reason}")
            self._alert_active = True

            now = time.time()
            if not self._beeping.is_set() and (now - self._last_beep_time) >= self._cooldown:
                self._last_beep_time = now
                t = threading.Thread(target=self._beep_async, daemon=True, name="alert-beep")
                t.start()

            if config.SMS_ENABLED:
                mins_elapsed = (now - self._last_sms_time) / 60.0
                if mins_elapsed >= config.SMS_COOLDOWN_MINUTES:
                    self._last_sms_time = now
                    t = threading.Thread(target=self._sms_worker, daemon=True, name="alert-sms")
                    t.start()

    def clear(self):
        """Mark alert as inactive."""
        with self._lock:
            if self._alert_active:
                logger.info("Alert cleared")
            self._alert_active = False

    def is_active(self) -> bool:
        """Thread-safe read."""
        with self._lock:
            return self._alert_active

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _beep_async(self):
        """
        Plays N beeps at the configured frequency.
        Runs in a daemon thread — does not block the inference loop.
        """
        self._beeping.set()
        try:
            for i in range(self._beep_count):
                if _IS_WINDOWS:
                    winsound.Beep(self._freq, self._duration_ms)
                else:
                    self._linux_beep()
                if i < self._beep_count - 1:
                    time.sleep(0.1)
        except Exception as e:
            logger.error(f"Beep failed: {e}")
        finally:
            self._beeping.clear()

    def _sms_worker(self):
        """Sends an SMS alert via SIM800L GSM module. Runs in daemon thread."""
        try:
            from gsmmodem.modem import GsmModem
            modem = GsmModem(config.SMS_PORT, config.SMS_BAUD)
            modem.connect()
            modem.sendSms(
                config.EMERGENCY_CONTACT,
                "SmartHelm Alert: Rider drowsiness detected. Please check in immediately."
            )
            modem.close()
            logger.info(f"SMS alert sent to {config.EMERGENCY_CONTACT}")
        except Exception as e:
            logger.warning(f"SMS failed: {e}")

    @staticmethod
    def _linux_beep():
        """Try aplay system sound, then speaker-test, then terminal BEL."""
        import subprocess
        _ALSA_SOUNDS = [
            "/usr/share/sounds/alsa/Front_Left.wav",
            "/usr/share/sounds/alsa/Noise.wav",
        ]
        for wav in _ALSA_SOUNDS:
            if os.path.exists(wav):
                try:
                    subprocess.run(["aplay", "-q", wav], timeout=1, capture_output=True)
                    return
                except Exception:
                    break
        # terminal BEL as last resort
        sys.stdout.write("\a")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mgr = AlertManager()
    print("Testing alert trigger — you should hear 3 beeps...")
    mgr.trigger(reason="TEST")
    time.sleep(2)
    print(f"Alert active: {mgr.is_active()}")
    mgr.clear()
    print(f"Alert active after clear: {mgr.is_active()}")
    print("Done.")
