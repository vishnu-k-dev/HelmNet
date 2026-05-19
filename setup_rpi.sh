#!/usr/bin/env bash
# =============================================================================
# SmartHelm — Raspberry Pi Plug-and-Play Setup Script
# =============================================================================
# Run once after imaging your Pi:
#   chmod +x setup_rpi.sh
#   ./setup_rpi.sh
#
# Tested on: Raspberry Pi 4/5, Raspberry Pi OS 64-bit (Bookworm/Bullseye)
# Requires: internet connection, ~1.5 GB free disk space
# =============================================================================

set -e  # exit on first error

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ---------------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------------
info "SmartHelm RPi setup starting..."

[[ $(uname -m) == aarch64 ]] || warn "Not aarch64 — MediaPipe wheels may not be available for $(uname -m)"
[[ $(id -u) -ne 0 ]] || error "Do NOT run as root. Run as the 'pi' user: ./setup_rpi.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMARTHELM_DIR="$SCRIPT_DIR/smarthelm"

[[ -d "$SMARTHELM_DIR" ]] || error "smarthelm/ not found in $SCRIPT_DIR. Clone the project first."
info "Project root: $SCRIPT_DIR"

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
info "Installing system packages (requires sudo)..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libatlas-base-dev \
    alsa-utils \
    mosquitto mosquitto-clients \
    --no-install-recommends -qq

sudo systemctl enable mosquitto
sudo systemctl start mosquitto
success "System packages installed, Mosquitto broker started"

# ---------------------------------------------------------------------------
# 2. Python virtual environment
# ---------------------------------------------------------------------------
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual env already exists at $VENV_DIR — skipping creation"
else
    info "Creating Python virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# Activate for this script
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python3 -m pip install --upgrade pip wheel -q
success "Virtual environment ready"

# ---------------------------------------------------------------------------
# 3. Python dependencies
# ---------------------------------------------------------------------------
info "Installing Python packages (this takes 3-5 minutes on Pi 4)..."

# Use headless OpenCV on RPi (no GUI dependencies needed — Flask serves video)
pip install --upgrade \
    "flask>=3.0.0" \
    "opencv-python-headless>=4.9.0" \
    "numpy>=1.26.0" \
    "paho-mqtt>=2.0.0" \
    "onnxruntime>=1.17.0" \
    "python-gsmmodem-new>=0.9" \
    -q

# MediaPipe — ARM64 wheels are available from 0.10.9+
info "Installing MediaPipe (large download, ~200 MB)..."
pip install "mediapipe>=0.10.9" -q

success "All Python packages installed"

# ---------------------------------------------------------------------------
# 4. Download face landmarker model (~7 MB)
# ---------------------------------------------------------------------------
MODEL_DIR="$SMARTHELM_DIR/models"
MODEL_PATH="$MODEL_DIR/face_landmarker.task"
MODEL_URL="https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

mkdir -p "$MODEL_DIR"
if [[ -f "$MODEL_PATH" ]]; then
    success "Face landmarker model already present"
else
    info "Downloading face landmarker model (~7 MB)..."
    curl -L -o "$MODEL_PATH" "$MODEL_URL" --progress-bar
    success "Model downloaded to $MODEL_PATH"
fi

# ---------------------------------------------------------------------------
# 5. Generate launch scripts
# ---------------------------------------------------------------------------
LAUNCH_SCRIPT="$SCRIPT_DIR/start_smarthelm.sh"
cat > "$LAUNCH_SCRIPT" << EOF
#!/usr/bin/env bash
# SmartHelm launcher — activates venv and starts the Flask server
set -e
cd "$SMARTHELM_DIR/backend"
source "$VENV_DIR/bin/activate"
exec python3 app.py
EOF
chmod +x "$LAUNCH_SCRIPT"
success "Launch script created: $LAUNCH_SCRIPT"

# ---------------------------------------------------------------------------
# 6. Systemd service (optional autostart on boot)
# ---------------------------------------------------------------------------
SERVICE_FILE="/etc/systemd/system/smarthelm.service"
CURRENT_USER="$(id -un)"

info "Creating systemd service for autostart..."
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=SmartHelm Drowsiness Detection Server
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SMARTHELM_DIR/backend
ExecStart=$VENV_DIR/bin/python3 $SMARTHELM_DIR/backend/app.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
success "Systemd service created: smarthelm.service"

# ---------------------------------------------------------------------------
# 7. Network info
# ---------------------------------------------------------------------------
PI_IP=$(hostname -I | awk '{print $1}')

# ---------------------------------------------------------------------------
# 8. Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  SmartHelm setup complete!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e "  ${CYAN}Start now:${NC}        $LAUNCH_SCRIPT"
echo -e "  ${CYAN}Dashboard:${NC}        http://$PI_IP:5000"
echo ""
echo -e "  ${CYAN}Enable autostart:${NC} sudo systemctl enable smarthelm"
echo -e "  ${CYAN}Start service:${NC}    sudo systemctl start smarthelm"
echo -e "  ${CYAN}View logs:${NC}        journalctl -u smarthelm -f"
echo ""
echo -e "  ${CYAN}MQTT monitor:${NC}     mosquitto_sub -t 'smarthelm/#' -v"
echo ""
echo -e "  ${YELLOW}Next step:${NC} Edit smarthelm/backend/config.py to set"
echo -e "           your ESP32-CAM IP addresses (CAM1_URL, CAM2_URL, CAM3_URL)"
echo -e "           and set USE_WEBCAM_FALLBACK=False when hardware is ready."
echo ""
