# SmartHelm Phase 1 — Central Configuration
# All tuneable constants live here. No magic numbers elsewhere.

# ---------------------------------------------------------------------------
# Camera sources
# ---------------------------------------------------------------------------
# Set USE_WEBCAM_FALLBACK=True while testing without ESP32 hardware.
# The factory in streams.py will try the URL first; if it can't connect
# within 1 second it falls back to WEBCAM_INDEX (your laptop camera).
USE_WEBCAM_FALLBACK: bool = True
WEBCAM_INDEX: int = 0

# Update these IPs once ESP32 boards are assigned addresses on your network.
CAM1_URL: str = "http://192.168.1.101/stream"
CAM2_URL: str = "http://192.168.1.102/stream"
CAM3_URL: str = "http://192.168.1.103/stream"

# ---------------------------------------------------------------------------
# MediaPipe Face Mesh eye landmark indices (468-point model)
# Each tuple is (P1, P2, P3, P4, P5, P6) for the EAR formula:
#   EAR = (||P2-P6|| + ||P3-P5||) / (2 * ||P1-P4||)
# ---------------------------------------------------------------------------
LEFT_EYE_INDICES: tuple = (362, 385, 387, 263, 373, 380)
RIGHT_EYE_INDICES: tuple = (33, 160, 158, 133, 153, 144)

# ---------------------------------------------------------------------------
# Eye Aspect Ratio thresholds
# ---------------------------------------------------------------------------
EAR_OPEN_THRESHOLD: float = 0.25    # EAR above this → OPEN
EAR_CLOSED_THRESHOLD: float = 0.20  # EAR below this → CLOSED
# Between the two thresholds → UNKNOWN (transition / uncertain)
EAR_SMOOTHING_WINDOW: int = 5       # rolling average window (frames)

# ---------------------------------------------------------------------------
# Drowsiness detection
# ---------------------------------------------------------------------------
PERCLOS_THRESHOLD: float = 30.0          # % closed in rolling window → alert
PERCLOS_WINDOW_SECONDS: int = 60         # rolling window size
CLOSED_DURATION_THRESHOLD: float = 1.5  # seconds continuous closure → immediate alert

# Alert auto-clear: eyes must be open this long before alert resets
ALERT_CLEAR_DELAY: float = 2.0  # seconds

# ---------------------------------------------------------------------------
# Detection mode
# "EAR"  → MediaPipe landmarks + EAR thresholding (Day 1, works immediately)
# "CNN"  → MediaPipe landmarks + ONNX eye-state CNN (Day 2 upgrade)
# ---------------------------------------------------------------------------
DETECTION_MODE: str = "EAR"

# CNN settings (only used when DETECTION_MODE == "CNN")
CNN_MODEL_PATH: str = "models/eye_state_mobilenetv2.onnx"
CNN_INPUT_SIZE: tuple = (64, 64)      # (width, height) of eye ROI fed to model
CNN_CLOSED_THRESHOLD: float = 0.5    # closed probability above this → CLOSED

# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------
MQTT_BROKER: str = "localhost"
MQTT_PORT: int = 1883
# Use .format(cam="CAM1") to build the actual topic string
MQTT_TOPIC_TEMPLATE: str = "smarthelm/{cam}/eye_state"
MQTT_PUBLISH_HZ: float = 2.0  # max publish rate (frames per second)

# ---------------------------------------------------------------------------
# Flask / Dashboard
# ---------------------------------------------------------------------------
FLASK_HOST: str = "0.0.0.0"
FLASK_PORT: int = 5000
MJPEG_FRAME_DELAY: float = 0.04  # 25 FPS ceiling for dashboard video feed

# ---------------------------------------------------------------------------
# Alerts (Windows: winsound.Beep — stdlib, no install needed)
# ---------------------------------------------------------------------------
ALERT_COOLDOWN_SECONDS: float = 3.0  # minimum gap between consecutive beeps
ALERT_BEEP_FREQUENCY: int = 1000     # Hz
ALERT_BEEP_DURATION_MS: int = 300    # ms per beep
ALERT_BEEP_COUNT: int = 3            # number of beeps per trigger

# ---------------------------------------------------------------------------
# SMS Alerts (SIM800L GSM module via UART)
# Requires: pip install python-gsmmodem-new
# Wiring: SIM800L TX→Pi GPIO15, RX→Pi GPIO14, GND→Pi GND
# ---------------------------------------------------------------------------
SMS_ENABLED: bool = False                  # set True once SIM800L is wired
SMS_PORT: str = "/dev/ttyS0"              # UART port on Raspberry Pi
SMS_BAUD: int = 9600
EMERGENCY_CONTACT: str = "+91XXXXXXXXXX"  # number to alert (include country code)
SMS_COOLDOWN_MINUTES: float = 5.0         # minimum gap between SMS alerts

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR: str = "logs"
LOG_CSV_FILENAME: str = "events.csv"
LOG_TO_CSV: bool = True
LOG_INTERVAL_SECONDS: float = 1.0   # write to CSV at most once per second
