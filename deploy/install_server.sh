#!/usr/bin/env bash
# Needs Python 3.11+ (python3 --version). Run once on the Linux server, from inside the cloned repo:   sudo bash deploy/install_server.sh
# Sets up: a venv, the Deskhand service (starts at boot), and a timer that pulls GitHub every 2 minutes and restarts
# the service when something new was pushed. Laptops then update themselves from this server.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"
cd "$REPO"
python3 -m venv .venv
.venv/bin/pip install -q -e ".[server]"
[ -f server_policy.json ] || cp server_policy.example.json server_policy.json     # the server-side safety rules (edit to taste)
[ -f /etc/deskhand.env ] || {
  KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
  printf 'TASKPILOT_ADMIN_KEY=%s\nNVIDIA_API_KEY=\n# Sign-in by email (see README): your Gmail + an app password, and the address people use\nTASKPILOT_SMTP_USER=\nTASKPILOT_SMTP_PASSWORD=\nTASKPILOT_PUBLIC_URL=\n' "$KEY" > /etc/deskhand.env
  chmod 600 /etc/deskhand.env
  echo "Created /etc/deskhand.env - put your NVIDIA_API_KEY in it, then: sudo systemctl restart deskhand"
}
cat > /etc/systemd/system/deskhand.service <<UNIT
[Unit]
Description=Deskhand server
After=network-online.target
[Service]
User=$USER_NAME
WorkingDirectory=$REPO
EnvironmentFile=/etc/deskhand.env
ExecStart=$REPO/.venv/bin/taskpilot-server --provider openai --model nvidia/nemotron-3.5-super-vl-120b-a12b --db $REPO/taskpilot.db --host 127.0.0.1 --port 8765
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/deskhand-update.service <<UNIT
[Unit]
Description=Pull Deskhand updates from GitHub
[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$REPO
ExecStart=/usr/bin/env bash $REPO/deploy/update.sh
UNIT
cat > /etc/systemd/system/deskhand-update.timer <<UNIT
[Unit]
Description=Check GitHub for Deskhand updates
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
# the update service restarts deskhand, which needs permission: allow exactly that one command, nothing else
echo "$USER_NAME ALL=(root) NOPASSWD: /bin/systemctl restart deskhand" > /etc/sudoers.d/deskhand-restart
chmod 440 /etc/sudoers.d/deskhand-restart
systemctl daemon-reload
systemctl enable --now deskhand.service deskhand-update.timer
echo "Deskhand is running on 127.0.0.1:8765. For HTTPS over Tailscale (needed for voice + app install), run:"
echo "  sudo tailscale serve --bg 8765"
