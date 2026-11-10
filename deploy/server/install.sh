#!/usr/bin/env bash
# One-time setup on the home server (run as the normal user; it asks for sudo only to install the timer):
#   bash install.sh
# It does NOT start or stop Deskhand: switching over from the old build is a separate, deliberate step (see docs/DEPLOY.md).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$DIR/deskhand-pull.sh"
sed "s#__USER__#$(id -un)#; s#__DIR__#$DIR#g" "$DIR/deskhand-pull.service" | sudo tee /etc/systemd/system/deskhand-pull.service >/dev/null
sudo cp "$DIR/deskhand-pull.timer" /etc/systemd/system/deskhand-pull.timer
sudo systemctl daemon-reload
sudo systemctl enable --now deskhand-pull.timer
echo "installed. Watch it with:  sudo journalctl -u deskhand-pull -n 40 --no-pager"
