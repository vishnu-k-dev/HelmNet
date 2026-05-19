# SmartHelm — AI Drowsiness Detection for Motorcycle Helmets

Detects rider drowsiness in real time using eye tracking. Fires an audio alert and sends an SMS when the rider is falling asleep.

---

## Run it on your laptop (webcam mode)

### Requirements
- Python 3.10 or higher
- A webcam (built-in or USB)
- Windows / macOS / Linux

---

### Step 1 — Clone the repo
```bash
git clone https://github.com/vishnu-k-dev/HelmNet.git
cd HelmNet
```

### Step 2 — Install dependencies
```bash
pip install -r smarthelm/requirements.txt
```
> First time takes ~3 minutes (MediaPipe is a large package)

### Step 3 — Download the face detection model
```bash
mkdir -p smarthelm/models
curl -L -o smarthelm/models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```
> On Windows, use a browser to download and save to `smarthelm/models/face_landmarker.task`

### Step 4 — Run
```bash
cd smarthelm/backend
python app.py
```

### Step 5 — Open the dashboard
Open your browser and go to:
```
http://localhost:5000
```

---

## What you should see

| Action | Result |
|---|---|
| Eyes open | Green **OPEN** badge |
| Eyes closed | Red **CLOSED** badge |
| Eyes closed for 1.5 seconds | 🔔 Beep alert + red **DROWSINESS ALERT** banner |
| PERCLOS > 30% over 60s | Drowsiness alert triggers |

---

## Project Structure

```
smarthelm/
├── backend/
│   ├── app.py          ← Flask server (start here)
│   ├── config.py       ← All settings (camera IPs, thresholds)
│   ├── detector.py     ← Eye detection (MediaPipe + EAR)
│   ├── perclos.py      ← Drowsiness logic (PERCLOS algorithm)
│   ├── alerts.py       ← Audio + SMS alerts
│   ├── streams.py      ← Camera stream management
│   └── mqtt_client.py  ← IoT data publishing
├── dashboard/
│   ├── templates/index.html   ← Live dashboard UI
│   └── static/               ← CSS + JavaScript
├── firmware/
│   ├── cam1/cam1.ino   ← ESP32-CAM firmware (rider-facing)
│   ├── cam2/cam2.ino   ← ESP32-CAM firmware (front road)
│   └── cam3/cam3.ino   ← ESP32-CAM firmware (rear traffic)
├── models/             ← Place face_landmarker.task here
└── tests/
    └── test_perclos.py
setup_rpi.sh            ← One-command Raspberry Pi setup
```

---

## Hardware Setup (Full Deployment)

For full helmet deployment with Raspberry Pi + ESP32-CAM, run:
```bash
./setup_rpi.sh
```
Then set your camera IPs in `smarthelm/backend/config.py` and set `USE_WEBCAM_FALLBACK = False`.

See the hardware guide in the project documentation.
