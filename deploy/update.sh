#!/usr/bin/env bash
# Pull the latest code; restart Deskhand only if something changed. Safe to run any time.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse '@{u}')
[ "$LOCAL" = "$REMOTE" ] && exit 0
git merge --ff-only -q '@{u}'
.venv/bin/pip install -q -e ".[server]"
sudo /bin/systemctl restart deskhand
echo "updated $LOCAL -> $(git rev-parse HEAD)"
