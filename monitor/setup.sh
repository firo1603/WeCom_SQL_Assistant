#!/usr/bin/env bash
# Deploy corp-assistant-monitor to /opt/corp-assistant-monitor
# Run on 192.168.4.15 as root.
set -euo pipefail

INSTALL_DIR=/opt/corp-assistant-monitor
VENV=$INSTALL_DIR/venv
SERVICE=corp-assistant-monitor

echo "=== Corp Assistant Monitor — deploy ==="

# 1. Copy files
echo "[1/5] Copying files to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
rsync -a --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' \
  "$(dirname "$0")/" "$INSTALL_DIR/"

# 2. Create venv and install deps
echo "[2/5] Setting up Python venv ..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

# 3. Allow only 192.168.4.11 to reach port 8765
echo "[3/5] Configuring firewall ..."
if command -v ufw &>/dev/null; then
  ufw allow from 192.168.4.11 to any port 8765 comment "corp-assistant-monitor" 2>/dev/null || true
fi

# 4. Install and enable systemd service
echo "[4/5] Installing systemd service ..."
cp "$INSTALL_DIR/corp-assistant-monitor.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

# 5. Done
echo "[5/5] Done."
echo ""
systemctl status "$SERVICE" --no-pager
echo ""
echo "Monitor running at http://192.168.4.15:8765"
echo "Default login: admin / admin  (will be forced to change on first login)"
