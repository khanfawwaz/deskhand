#!/usr/bin/env bash
# Run ON THE ORACLE SERVER. Makes Oracle hand Deskhand's traffic to your laptop through an SSH tunnel.
# It only touches: the tunnel's line in ~/.ssh/authorized_keys, and Deskhand's own nginx site. No other site or process.
#
#   bash deploy/laptop/oracle-setup.sh authorize "ssh-ed25519 AAAA... deskhand-laptop"   # step 1: allow the laptop's key (tunnel only)
#   bash deploy/laptop/oracle-setup.sh check                                             # step 2: is the tunnel up?
#   bash deploy/laptop/oracle-setup.sh switch                                            # step 3: point nginx at the laptop
#   bash deploy/laptop/oracle-setup.sh back                                              # undo step 3: point nginx at this server again
set -euo pipefail
PORT_LAPTOP=18765          # opened on THIS machine's loopback by the laptop's tunnel
PORT_LOCAL=8765            # where the Deskhand on this server listens
CONF=/etc/nginx/conf.d/deskhand.conf

apply_conf() {             # $1 = the port nginx should send Deskhand's traffic to
  sudo cp "$CONF" "$CONF.bak.$(date +%s)"
  sudo sed -i -E "s#proxy_pass http://127.0.0.1:[0-9]+;#proxy_pass http://127.0.0.1:$1;#g" "$CONF"
  if ! sudo grep -q "deskhand_down" "$CONF"; then
    echo "NOTE: your nginx site predates the friendly offline page (error_page 502 503 504 and @deskhand_down in deploy/nginx-deskhand.conf)."
  fi
  sudo nginx -t
  sudo systemctl reload nginx
}

case "${1:-}" in
  authorize)
    key="${2:?pass the laptop PUBLIC key, in quotes}"
    mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
    if grep -qF "$key" ~/.ssh/authorized_keys; then echo "that key is already authorized"; exit 0; fi
    # this key can do exactly one thing: open 127.0.0.1:$PORT_LAPTOP here. No shell, no other forwarding.
    printf 'restrict,port-forwarding,permitlisten="127.0.0.1:%s",command="/bin/false" %s\n' "$PORT_LAPTOP" "$key" >> ~/.ssh/authorized_keys
    echo "authorized (tunnel only). Now start the tunnel on the laptop."
    ;;
  check)
    if curl -fsS --max-time 5 "http://127.0.0.1:$PORT_LAPTOP/v1/version" > /dev/null 2>&1; then
      echo "OK: the laptop answers through the tunnel"
      curl -s "http://127.0.0.1:$PORT_LAPTOP/v1/version"
      echo
    else
      echo "NOT YET: nothing answers on 127.0.0.1:$PORT_LAPTOP (are the laptop's Deskhand and tunnel running?)"
      exit 1
    fi
    ;;
  switch)
    "$0" check
    apply_conf "$PORT_LAPTOP"
    echo "nginx now sends Deskhand to the laptop. The old Deskhand here is still installed: stop it with  sudo systemctl stop deskhand"
    ;;
  back)
    apply_conf "$PORT_LOCAL"
    echo "nginx sends Deskhand to this server again. Start it if needed:  sudo systemctl start deskhand"
    ;;
  *)
    sed -n 2,9p "$0"
    exit 2
    ;;
esac
