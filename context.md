# SmartHelm — Full Project Context

## What Is This

SmartHelm is an AI-powered drowsiness detection system built into a motorcycle helmet. It detects when a delivery rider is falling asleep in real time using a rider-facing camera, fires an audio alert on the helmet, and sends an SMS to an emergency contact (family member or fleet manager).

Built for: **Unisys Innovation Program Year 17 — Phase 2 evaluation**
Deliverables: Working software demo + pitch deck + product video
GitHub: https://github.com/vishnu-k-dev/HelmNet

---

## The Problem

Delivery riders (Swiggy, Zomato, logistics) work 10–16 hour shifts. Drowsy driving causes thousands of deaths annually. No affordable, rider-specific early-warning system exists. The rider often doesn't know they're drowsy until it's too late.

---

## How It Works (Technical)

### Detection Pipeline
```
ESP32-CAM (rider-facing, inside helmet)
    ↓  WiFi MJPEG stream
Raspberry Pi 4 (on the bike)
    ↓  OpenCV frame capture
MediaPipe FaceLandmarker (468 facial landmarks)
    ↓  6 eye landmarks per eye
Eye Aspect Ratio (EAR) calculation
    EAR = (||P2-P6|| + ||P3-P5||) / (2 * ||P1-P4||)
    ↓
PERCLOS tracker (rolling 60-second window)
    ↓
Alert if: PERCLOS > 30% OR eyes closed > 1.5 seconds
    ↓
Audio beep (USB speaker) + SMS (SIM800L GSM module)
    ↓
Live dashboard at http://<pi-ip>:5000
    ↓
MQTT publish to smarthelm/CAM1/eye_state
```

### Eye State Classification
- EAR > 0.25 → OPEN
- EAR < 0.20 → CLOSED
- Between → UNKNOWN (transition, not counted as closed)
- Smoothed over 5-frame rolling average

### Alert Triggers (two independent)
1. **PERCLOS**: Eyes closed > 30% of frames in last 60 seconds
2. **Continuous closure**: Eyes closed continuously for ≥ 1.5 seconds

### Alert Actions
- Audio: 3 beeps at 1000Hz via USB speaker (3s cooldown between beeps)
- SMS: sent via SIM800L to emergency contact (5-minute cooldown)
- Dashboard: red pulsing ALERT banner
- MQTT: published to broker at 2Hz

---

## Hardware

### Phase 1 Demo Setup (minimal)
| Component | Role | Cost |
|---|---|---|
| Raspberry Pi 4 (4GB) | Main compute — runs all AI | ₹5,000 |
| MicroSD 32GB Class 10 | Pi storage | ₹400 |
| USB-C Power Supply (5V 3A) | Power the Pi | ₹500 |
| ESP32-CAM (AI Thinker) | Rider-facing camera, WiFi MJPEG | ₹450 |
| FTDI USB-to-Serial | Flash firmware onto ESP32-CAM (one-time) | ₹150 |
| SIM800L EVB (5V version) | GSM module for SMS alerts | ₹550 |
| Jumper wires (F-to-F) | SIM800L to Pi GPIO | ₹50 |
| Power Bank (10,000 mAh) | Powers Pi + SIM800L | ₹800 |
| USB Speaker/buzzer | Audio alert | ₹300 |
| Motorcycle helmet | Physical housing | — |

**Total: ~₹8,200**

### SIM800L Wiring (4 wires)
| SIM800L Pin | Raspberry Pi Pin |
|---|---|
| VCC | Power bank 5V (via SIM800L EVB onboard regulator) |
| GND | Pi Pin 6 (GND) |
| TX | Pi Pin 10 (GPIO 15 / RXD) |
| RX | Pi Pin 8 (GPIO 14 / TXD) |

### ESP32-CAM
- Board: AI Thinker ESP32-CAM
- Flash via FTDI: IO0 → GND during flash, disconnect before boot
- Stream URL: `http://<ESP32-IP>/stream`
- Resolution: SVGA (800×600)
- Connects to Pi over WiFi (same network/hotspot)

---

## Software Architecture

### Directory Structure
```
HelmNet/
├── smarthelm/
│   ├── backend/
│   │   ├── app.py           ← Flask server + inference thread hub
│   │   ├── config.py        ← All settings (single source of truth)
│   │   ├── detector.py      ← MediaPipe + EAR detection
│   │   ├── perclos.py       ← Drowsiness logic
│   │   ├── alerts.py        ← Audio beep + SMS
│   │   ├── streams.py       ← MJPEG stream with auto-reconnect
│   │   └── mqtt_client.py   ← MQTT publisher (paho)
│   ├── dashboard/
│   │   ├── templates/index.html   ← Live dashboard UI
│   │   └── static/
│   │       ├── dashboard.js       ← Polls /api/status every 500ms
│   │       └── style.css          ← Dark theme
│   ├── firmware/
│   │   ├── cam1/cam1.ino    ← ESP32-CAM firmware (rider-facing)
│   │   ├── cam2/cam2.ino    ← ESP32-CAM firmware (front road)
│   │   └── cam3/cam3.ino    ← ESP32-CAM firmware (rear traffic)
│   ├── models/              ← face_landmarker.task goes here (~7MB)
│   ├── tests/
│   │   └── test_perclos.py  ← 13 unit tests
│   └── requirements.txt
└── setup_rpi.sh             ← One-command RPi setup script
```

### Threading Model
```
Main process (app.py)
├── Thread 0: Flask HTTP server (threaded=True)
├── Thread 1: inference_loop daemon — reads cam1, runs detection, writes SharedState
├── Threads 2-4: MJPEGStream reconnect daemons (one per camera)
└── Thread 5: paho-mqtt network loop
```

### Key Config Values (config.py)
```python
USE_WEBCAM_FALLBACK = True       # True = use laptop webcam (testing)
                                  # False = use ESP32-CAM URLs (hardware)
CAM1_URL = "http://192.168.1.101/stream"
CAM2_URL = "http://192.168.1.102/stream"
CAM3_URL = "http://192.168.1.103/stream"

EAR_OPEN_THRESHOLD = 0.25
EAR_CLOSED_THRESHOLD = 0.20
EAR_SMOOTHING_WINDOW = 5

PERCLOS_THRESHOLD = 30.0         # % → alert
PERCLOS_WINDOW_SECONDS = 60
CLOSED_DURATION_THRESHOLD = 1.5  # seconds → immediate alert

SMS_ENABLED = False              # set True when SIM800L is wired
SMS_PORT = "/dev/ttyS0"          # UART port on Pi
EMERGENCY_CONTACT = "+91XXXXXXXXXX"
SMS_COOLDOWN_MINUTES = 5.0

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
FLASK_PORT = 5000
```

### Flask Routes
| Route | Description |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /video_feed/cam1` | MJPEG — annotated (eye landmarks drawn) |
| `GET /video_feed/cam2` | MJPEG — raw (no inference) |
| `GET /video_feed/cam3` | MJPEG — raw (no inference) |
| `GET /api/status` | JSON: eye_state, perclos, alert, fps, latency, face_detected |
| `GET /api/events` | JSON: last 20 alert events |

---

## Running the System

### Laptop / Testing (webcam mode)
```bash
git clone https://github.com/vishnu-k-dev/HelmNet.git
cd HelmNet
pip install -r smarthelm/requirements.txt

# Download model (one time):
# Windows: browser download from URL below, save to smarthelm/models/face_landmarker.task
# Linux/Mac: curl -L -o smarthelm/models/face_landmarker.task \
#   https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

cd smarthelm/backend
python app.py
# Open: http://localhost:5000
```

### Raspberry Pi (plug and play)
```bash
git clone https://github.com/vishnu-k-dev/HelmNet.git
cd HelmNet
chmod +x setup_rpi.sh
./setup_rpi.sh         # installs everything, downloads model, creates systemd service

# Start now:
./start_smarthelm.sh

# Or enable autostart on boot:
sudo systemctl enable smarthelm
sudo systemctl start smarthelm
```

### Switch to ESP32-CAM (hardware mode)
1. Flash firmware to each ESP32-CAM (see firmware/cam1/cam1.ino — fill in WiFi credentials)
2. Note each camera's IP address from Serial Monitor
3. Edit `smarthelm/backend/config.py`:
   ```python
   USE_WEBCAM_FALLBACK = False
   CAM1_URL = "http://192.168.x.x/stream"   # actual ESP32 IP
   ```
4. Restart SmartHelm

### Enable SMS (SIM800L)
1. Wire SIM800L to Pi (see wiring table above)
2. Enable UART on Pi: `sudo raspi-config` → Interface Options → Serial Port → disable shell, enable hardware
3. Insert active SIM card into SIM800L
4. In config.py:
   ```python
   SMS_ENABLED = True
   EMERGENCY_CONTACT = "+91XXXXXXXXXX"
   ```
5. Restart SmartHelm

---

## Detection Modes

**Current: EAR mode (Day 1)**
- MediaPipe detects 468 landmarks
- 6 landmarks per eye used for EAR formula
- Works immediately, no extra model needed

**Upgrade path: CNN mode (Day 2)**
- Same MediaPipe landmarks
- Eye ROI cropped → 64×64 grayscale
- ONNX MobileNetV2 binary classifier
- Switch: set `DETECTION_MODE = "CNN"` in config.py, place model in `models/`

---

## Verification Checklist

| Test | Expected |
|---|---|
| Eyes open | Green OPEN badge |
| Eyes closed | Red CLOSED badge |
| Eyes closed 2 seconds | Beep fires, ALERT banner appears |
| PERCLOS > 30% | ALERT banner appears |
| SMS_ENABLED=True + alert | SMS sent to emergency contact |
| Disconnect ESP32-CAM | Dashboard shows placeholder, reconnects within 5s |
| `/api/status` in browser | Valid JSON |
| 10-minute run | No crash, FPS 8-15 |
| `mosquitto_sub -t "smarthelm/#" -v` | JSON events appear |

---

## Known Limitations (Phase 1)

- Inference runs only on CAM1 (rider-facing). CAM2/CAM3 are raw feeds only.
- No GPS — SMS alert has no location. Future: add GPS module.
- EAR thresholds may need tuning per rider (glasses, lighting conditions).
- MQTT requires Mosquitto broker running locally (`sudo systemctl start mosquitto`).
- SMS requires active SIM with balance.

---

## Pitch Deck

File: `SmartHelm Pitch Deck.html` (self-contained, 20 slides)
Open in any browser. Navigate with arrow keys or click.

Key slides:
- Slide 4: "He was fine when he left. We had no warning." — emotional hook
- Slide 7: Sense → Detect → Act — how the system works
- Slide 10: PERCLOS algorithm explanation
- Slide 15: Hardware BOM + deployment diagram

---

## Contact

Vishnu K — vishnuk2006@protonmail.com
