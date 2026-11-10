#!/usr/bin/env bash
# Pull the latest code; restart Deskhand only if something changed. Safe to run any time (the timer runs it every 2 minutes).
# Everything it says goes to the journal:  sudo journalctl -u deskhand-update -n 30 --no-pager
set -uo pipefail
cd "$(dirname "$0")/.."
SERVICE="${DESKHAND_SERVICE:-deskhand}"      # the laptop runs it as deskhand-server
git fetch -q origin || { echo "git fetch failed"; exit 1; }
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse '@{u}')
[ "$LOCAL" = "$REMOTE" ] && exit 0
git merge --ff-only -q '@{u}' || { echo "git merge failed (local changes in the way?)"; exit 1; }
.venv/bin/pip install -q -e ".[server]" || echo "pip install failed; restarting anyway"
if sudo -n /bin/systemctl restart "$SERVICE" 2>/dev/null || sudo -n /usr/bin/systemctl restart "$SERVICE" 2>/dev/null; then
  echo "updated $LOCAL -> $(git rev-parse HEAD) and restarted"
else
  echo "UPDATED THE FILES BUT COULD NOT RESTART: the sudo rule for systemctl restart $SERVICE is missing. Fix: re-run deploy/install_server.sh"
  exit 1
fi
