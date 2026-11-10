#!/usr/bin/env bash
# Fetch the newest Deskhand image. If it changed: start it, wait until it reports healthy, and if it does not,
# go back to the previous image. Safe to run any time; the systemd timer runs it every 2 minutes.
# Everything it says goes to the journal:  sudo journalctl -u deskhand-pull -n 40 --no-pager
set -uo pipefail

DIR="${DESKHAND_DEPLOY_DIR:-$(cd "$(dirname "$0")" && pwd)}"
IMAGE="${DESKHAND_IMAGE:-ghcr.io/khanfawwaz/deskhand-server:latest}"
SERVICE="${DESKHAND_SERVICE:-deskhand}"
CONTAINER="${DESKHAND_CONTAINER:-deskhand_server}"
HEALTH_WAIT="${DESKHAND_HEALTH_WAIT:-90}"        # seconds to wait for "healthy" before going back
export DESKHAND_IMAGE="$IMAGE"

cd "$DIR" || exit 1
if command -v flock >/dev/null 2>&1; then          # never run two updates at once
  exec 9>"$DIR/.pull.lock"
  flock -n 9 || { echo "another update is already running"; exit 0; }
fi

rollback() {
  if [ "$before" = none ]; then
    echo "ROLLBACK NOT POSSIBLE: there was no earlier image. Check: docker logs $CONTAINER"
    return 1
  fi
  docker tag "deskhand-server:previous" "$IMAGE"
  docker compose up -d "$SERVICE" >/dev/null 2>&1
  echo "ROLLED BACK to the previous image ($before)"
}

before=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo none)
docker pull -q "$IMAGE" >/dev/null || { echo "pull failed (offline, or not signed in to ghcr.io?)"; exit 1; }
after=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo none)
[ "$before" = "$after" ] && exit 0                 # nothing new

echo "new image $after (was $before): updating"
[ "$before" != none ] && docker tag "$before" "deskhand-server:previous"
if ! docker compose up -d "$SERVICE" >/dev/null 2>&1; then
  echo "could not start the new image"
  rollback
  exit 1
fi

waited=0
while [ "$waited" -lt "$HEALTH_WAIT" ]; do
  status=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
  case "$status" in
    healthy) echo "updated and healthy ($after)"; exit 0 ;;
    unhealthy|missing) break ;;
  esac
  sleep 3
  waited=$((waited + 3))
done
echo "the new image did not become healthy (last status: ${status:-unknown})"
rollback
exit 1
