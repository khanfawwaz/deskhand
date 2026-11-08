#!/usr/bin/env bash
# Run once on the Linux server (Ubuntu/Debian, Python 3.11+), from inside the cloned repo:
#
#   sudo bash deploy/install_server.sh --domain deskhand.example.com     # public HTTPS, no Tailscale
#   sudo bash deploy/install_server.sh --auto-domain                      # no domain? use <your-ip>.sslip.io
#   sudo bash deploy/install_server.sh                                    # local only (127.0.0.1:8765)
#
# Sets up: a venv, the Deskhand service (starts at boot), a timer that pulls GitHub every 2 minutes and restarts the
# service when something new was pushed, and (with a domain) Caddy as the public HTTPS front door with a free
# Let's Encrypt certificate. Caddy only lets the app, the sign-in API and the helper endpoints through; the admin
# endpoints are NOT reachable from the internet (manage those over SSH on localhost).
set -euo pipefail

DOMAIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --auto-domain) IP=$(curl -fsS https://api.ipify.org); DOMAIN="${IP//./-}.sslip.io"; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"
cd "$REPO"
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
  echo "Python 3.11+ not found: installing python3.11 (deadsnakes)"
  apt-get update -qq
  apt-get install -y -qq software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -qq
  apt-get install -y -qq python3.11 python3.11-venv
  PY=python3.11
fi
# the venv module ships separately on Debian/Ubuntu
"$PY" -c 'import ensurepip, venv' 2>/dev/null || apt-get install -y -qq "$(basename "$PY")-venv"
echo "Using $($PY --version)"

"$PY" -m venv .venv
.venv/bin/pip install -q -e ".[server]"
[ -f server_policy.json ] || cp server_policy.example.json server_policy.json     # the server-side safety rules (edit to taste)

if [ ! -f /etc/deskhand.env ]; then
  KEY=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')
  printf 'TASKPILOT_ADMIN_KEY=%s\nNVIDIA_API_KEY=\n# Sign-in by email (see README): your Gmail + an app password\nTASKPILOT_SMTP_USER=\nTASKPILOT_SMTP_PASSWORD=\nTASKPILOT_PUBLIC_URL=\n' "$KEY" > /etc/deskhand.env
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

if [ -n "$DOMAIN" ]; then
  if ! command -v caddy >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
  fi
  sed "s#__DOMAIN__#$DOMAIN#g" "$REPO/deploy/Caddyfile" > /etc/caddy/Caddyfile
  # Oracle Cloud's Ubuntu images block 80/443 in the VM's own firewall too (besides the cloud console's security list)
  if command -v iptables >/dev/null && ! iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null; then
    iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
    iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
    command -v netfilter-persistent >/dev/null && netfilter-persistent save || true
  fi
  systemctl enable --now caddy
  systemctl reload caddy
  echo
  echo "Deskhand is live at:  https://$DOMAIN/app/"
  echo "If that does not load, open ports 80 and 443 in your cloud provider's firewall (Oracle: VCN > Security List > Ingress)."
else
  echo "Deskhand is running on 127.0.0.1:8765 only. Re-run with --domain your.domain (or --auto-domain) to publish it with HTTPS."
fi
echo "Admin key (for admin calls over SSH, never exposed publicly): sudo grep ADMIN_KEY /etc/deskhand.env"
