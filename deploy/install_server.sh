#!/usr/bin/env bash
# Run on the Linux server (Ubuntu/Debian), from inside the cloned repo:
#
#   sudo bash deploy/install_server.sh --check                          # look only: report conflicts, change nothing
#   sudo bash deploy/install_server.sh --domain deskhand.example.com     # public HTTPS, no Tailscale
#   sudo bash deploy/install_server.sh --auto-domain                      # no domain? use <your-ip>.sslip.io
#   sudo bash deploy/install_server.sh                                    # local only (127.0.0.1:8765)
#   options: --port 8765   (the port Deskhand listens on, on 127.0.0.1 only)
#
# This server may run other things. The script therefore NEVER stops, kills, restarts or reconfigures anything that is
# not Deskhand: if ports 80/443 are already in use by something else it leaves them alone (and tells you how to add
# Deskhand to that web server instead); an existing Caddy is only ever *reloaded*, never restarted, and its config is
# backed up and validated before and after.
set -euo pipefail

DOMAIN=""; CHECK=0; PORT=8765
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --auto-domain) IP=$(curl -fsS https://api.ipify.org); DOMAIN="${IP//./-}.sslip.io"; shift ;;
    --check) CHECK=1; shift ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"
cd "$REPO"

listening() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }
who_listens() { ss -ltnpH "sport = :$1" 2>/dev/null | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' | head -1; }

# ---------------------------------------------------------------- preflight (read-only)
PROBLEMS=0; CADDY_OK=1
echo "== Preflight: nothing is changed in this step"
if listening "$PORT" && ! systemctl is-active --quiet deskhand; then
  echo "  [!] 127.0.0.1:$PORT is already used by '$(who_listens "$PORT")'. Pick another with --port N. Deskhand will not touch it."
  PROBLEMS=1
fi
if [ -n "$DOMAIN" ]; then
  for p in 80 443; do
    if listening "$p"; then
      w=$(who_listens "$p")
      if [ "$w" = "caddy" ]; then
        echo "  [i] port $p is served by an existing Caddy: Deskhand will be added to it (reload only, nothing restarted)."
      else
        echo "  [!] port $p is in use by '$w'. Deskhand will NOT start Caddy or touch that service."
        CADDY_OK=0
      fi
    fi
  done
  [ "$CADDY_OK" = 1 ] || echo "      Use your existing web server instead: for nginx see deploy/nginx-deskhand.conf (a separate site, validated, graceful reload)."
fi
echo "  other services running on this machine (left alone):"
systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk '{print "    - " $1}' | head -15
if [ "$CHECK" = 1 ]; then echo "== --check: stopping here, nothing was changed."; exit "$PROBLEMS"; fi
[ "$PROBLEMS" = 0 ] || { echo "Fix the problem above, then run again."; exit 1; }

# ---------------------------------------------------------------- Deskhand itself
# Deskhand needs Python 3.11+. Ubuntu 22.04 ships 3.10, so install 3.11 from the deadsnakes archive when needed.
find_python() {
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY=$(find_python || true)
if [ -z "$PY" ]; then
  echo "Python 3.11+ not found: installing python3.11 (deadsnakes). The system python3 is left unchanged."
  apt-get update -qq
  apt-get install -y -qq software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -qq
  apt-get install -y -qq python3.11 python3.11-venv
  PY=python3.11
fi
"$PY" -c 'import ensurepip, venv' 2>/dev/null || apt-get install -y -qq "$(basename "$PY")-venv"
echo "Using $($PY --version)"

"$PY" -m venv .venv
.venv/bin/pip install -q -e ".[server]"
[ -f server_policy.json ] || cp server_policy.example.json server_policy.json     # the server-side safety rules (edit to taste)

if [ ! -f /etc/deskhand.env ]; then
  KEY=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')
  printf 'TASKPILOT_ADMIN_KEY=%s\nNVIDIA_API_KEY=\n# Sign-in by email (see README): your Gmail + an app password\nTASKPILOT_SMTP_USER=\nTASKPILOT_SMTP_PASSWORD=\nTASKPILOT_PUBLIC_URL=\n# Two models: a big text model decides, a vision model looks (see README). Delete these 4 lines for a single model.\nTASKPILOT_TEXT_MODEL=nvidia/nemotron-3-ultra-550b-a55b\nTASKPILOT_VISION_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b\nTASKPILOT_SPLIT_MODE=describe\nTASKPILOT_ALLOW_IMAGES=1\n' "$KEY" > /etc/deskhand.env
  chmod 600 /etc/deskhand.env
  echo "Created /etc/deskhand.env - put your NVIDIA_API_KEY (and Gmail app password) in it, then: sudo systemctl restart deskhand"
fi
if [ -n "$DOMAIN" ]; then
  if grep -q '^TASKPILOT_PUBLIC_URL=' /etc/deskhand.env; then
    sed -i "s#^TASKPILOT_PUBLIC_URL=.*#TASKPILOT_PUBLIC_URL=https://$DOMAIN#" /etc/deskhand.env
  else
    echo "TASKPILOT_PUBLIC_URL=https://$DOMAIN" >> /etc/deskhand.env
  fi
fi

cat > /etc/systemd/system/deskhand.service <<UNIT
[Unit]
Description=Deskhand server
After=network-online.target
[Service]
User=$USER_NAME
WorkingDirectory=$REPO
EnvironmentFile=/etc/deskhand.env
ExecStart=$REPO/.venv/bin/taskpilot-server --provider openai --model nvidia/nemotron-3.5-super-vl-120b-a12b --db $REPO/taskpilot.db --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=10
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
echo "$USER_NAME ALL=(root) NOPASSWD: /bin/systemctl restart deskhand, /usr/bin/systemctl restart deskhand" > /etc/sudoers.d/deskhand-restart
chmod 440 /etc/sudoers.d/deskhand-restart
systemctl daemon-reload
systemctl enable --now deskhand.service deskhand-update.timer

# ---------------------------------------------------------------- public HTTPS (only when it cannot disturb anything)
if [ -n "$DOMAIN" ] && [ "$CADDY_OK" = 1 ]; then
  if ! command -v caddy >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
  fi
  mkdir -p /etc/caddy
  sed "s#__DOMAIN__#$DOMAIN#g; s#127.0.0.1:8765#127.0.0.1:$PORT#g" "$REPO/deploy/Caddyfile" > /etc/caddy/deskhand.caddy
  if [ -s /etc/caddy/Caddyfile ] && ! grep -q "deskhand.caddy" /etc/caddy/Caddyfile; then
    cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak-deskhand"          # never lose someone else's config
    printf '\nimport /etc/caddy/deskhand.caddy\n' >> /etc/caddy/Caddyfile
    if ! caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
      cp /etc/caddy/Caddyfile.bak-deskhand /etc/caddy/Caddyfile
      echo "[!] The existing Caddy config did not validate with Deskhand added; restored it unchanged."
      CADDY_OK=0
    fi
  elif [ ! -s /etc/caddy/Caddyfile ]; then
    echo "import /etc/caddy/deskhand.caddy" > /etc/caddy/Caddyfile
  fi
  if [ "$CADDY_OK" = 1 ]; then
    # the VM's own firewall (Oracle images block 80/443 by default); only adds allow rules, removes nothing
    if command -v iptables >/dev/null && ! iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null; then
      iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
      iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
      command -v netfilter-persistent >/dev/null && netfilter-persistent save || true
    fi
    if systemctl is-active --quiet caddy; then
      systemctl reload caddy                                              # existing Caddy: reload only, never restart
    else
      systemctl enable --now caddy                                        # nothing was using 80/443, so this is safe
    fi
    sleep 2
    systemctl is-active --quiet caddy && echo "Deskhand is live at:  https://$DOMAIN/app/" \
      || { echo "[!] Caddy is not running. See why (read-only): sudo journalctl -u caddy -n 40 --no-pager"; }
    echo "If the address does not load, open ports 80 and 443 in your cloud provider's firewall (Oracle: VCN > Security List > Ingress)."
  fi
elif [ -n "$DOMAIN" ]; then
  echo "Skipped the public HTTPS part (see the preflight notes above); Deskhand itself is running on 127.0.0.1:$PORT."
else
  echo "Deskhand is running on 127.0.0.1:$PORT only. Re-run with --domain your.domain (or --auto-domain) to publish it with HTTPS."
fi
echo "Admin key (for admin calls over SSH, never exposed publicly): sudo grep ADMIN_KEY /etc/deskhand.env"
