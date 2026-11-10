# Run Deskhand on your laptop, with Oracle as the public front door

```
 phones, browsers, Windows helpers
              │  https / wss  (deskhand.fawwazkhan.dev)
              ▼
   Oracle server: nginx (TLS certificate, public address, hides admin pages)
              │  proxy_pass 127.0.0.1:18765          ← this port only exists while the laptop's tunnel is up
              ▼
        SSH tunnel   ◄── the laptop dials OUT to Oracle (nothing is opened on your home network)
              ▼
   Laptop: Deskhand server on 127.0.0.1:8765, its database, the shared model queue
              │  https
              ▼
   NVIDIA API  (your paid key lives ONLY on the laptop)
```

Why this shape: the laptop sits behind your home router, so nobody can connect *in* to it. Instead it connects *out* to
Oracle, which already has the domain name, the HTTPS certificate and a fixed address. The tunnel key can do exactly one
thing on Oracle (open that one local port): no shell, no other access.

If the laptop is off, asleep or loses internet, visitors see a short "Deskhand is offline for a moment" page and your
Windows helpers reconnect by themselves when it is back.

## One-time setup

### 1. The laptop
Linux (Ubuntu) laptop:
```bash
sudo useradd -m -s /bin/bash deskhand
sudo git clone https://github.com/khanfawwaz/deskhand.git /opt/deskhand && sudo chown -R deskhand /opt/deskhand
sudo -u deskhand sh -c 'cd /opt/deskhand && python3.11 -m venv .venv && .venv/bin/pip install -e ".[server]"'
sudo mkdir -p /var/lib/deskhand && sudo chown deskhand /var/lib/deskhand
sudo cp /opt/deskhand/deploy/laptop/laptop.env.example /etc/deskhand.env && sudo chmod 600 /etc/deskhand.env && sudo nano /etc/deskhand.env
sudo cp /opt/deskhand/deploy/laptop/deskhand-server.service /etc/systemd/system/
```
Windows laptop: install Python 3.11, clone the repo, `python -m venv .venv`, `.venv\Scripts\pip install -e ".[server]"`, then run
`deploy\laptop\windows-install.ps1 -OracleIp <oracle address>` (it guides you through the rest).

In the env file: copy the secrets you use on Oracle now (`/etc/deskhand.env` there: admin key, sign-in e-mail settings), and add the
NVIDIA key, the two model names, the prices and the spend cap (explained in the file).

### 2. The tunnel key (Linux; the Windows script does this for you)
On the laptop:
```bash
sudo -u deskhand ssh-keygen -t ed25519 -f /home/deskhand/.ssh/deskhand_tunnel -N "" -C deskhand-laptop-tunnel
sudo cat /home/deskhand/.ssh/deskhand_tunnel.pub
sudo -u deskhand ssh-keyscan -H ORACLE_IP | sudo -u deskhand tee -a /home/deskhand/.ssh/known_hosts
```
On Oracle, allow that key for the tunnel only (paste the line printed above):
```bash
bash deploy/laptop/oracle-setup.sh authorize "ssh-ed25519 AAAA... deskhand-laptop-tunnel"
```

## Cut over (about 3 minutes of downtime)
1. **Oracle:** stop the old server so no more data changes, and take a safe copy of its database:
   ```bash
   sudo systemctl stop deskhand
   sqlite3 ~/deskhand/taskpilot.db ".backup /tmp/deskhand.db"
   ```
2. **Copy it to the laptop** (from the laptop): `scp ubuntu@ORACLE_IP:/tmp/deskhand.db /var/lib/deskhand/deskhand.db`
   (Windows: `scp ubuntu@ORACLE_IP:/tmp/deskhand.db "$env:ProgramData\Deskhand\deskhand.db"`). If you use MongoDB instead of the file, copy it
   with `mongodump` / `mongorestore` and put the `mongodb://` address in `TASKPILOT_DB`.
3. **Laptop:** start the server and the tunnel.
   - Linux: `sudo systemctl enable --now deskhand-server`, edit `ORACLE_IP` in `deploy/laptop/deskhand-tunnel.service`, copy it to `/etc/systemd/system/`, then `sudo systemctl enable --now deskhand-tunnel`.
   - Windows: `deploy\laptop\windows-install.ps1 -OracleIp <oracle address>`.
   - Check: `curl http://127.0.0.1:8765/v1/version`.
4. **Oracle:** confirm the tunnel, then switch nginx:
   ```bash
   bash deploy/laptop/oracle-setup.sh check
   bash deploy/laptop/oracle-setup.sh switch
   ```
5. Open https://deskhand.fawwazkhan.dev and sign in. Your tasks, skills and devices are all there. The Windows helpers reconnect by themselves.

**Roll back any time:** `bash deploy/laptop/oracle-setup.sh back && sudo systemctl start deskhand` puts everything on Oracle again
(it will not have anything done on the laptop since the switch, so only use it early or copy the database back).

Once it has run well for a week, remove the old install on Oracle (`sudo systemctl disable --now deskhand`). Keep its database file as a backup.

## Keeping it healthy
- **Sleep:** the laptop must stay awake. Power settings: never sleep when plugged in; set "lid closed: do nothing". Keep it on its charger.
- **Updates:** Linux: run `deploy/update.sh` from a timer (the same one Oracle uses; it restarts `deskhand-server` if you rename the service in the script, or just `git pull && sudo systemctl restart deskhand-server`). Windows: `git pull` then restart the "Deskhand Server" task.
- **Backups (nightly):** `sqlite3 /var/lib/deskhand/deskhand.db ".backup /somewhere/else/deskhand-$(date +%F).db"`. Copy to another drive or cloud storage; a laptop disk can die.
- **Watching it:** the owner view (admin key, only reachable from the laptop itself) shows spend, tokens, the queue and the busiest workspaces:
  `curl -H "X-API-Key: $TASKPILOT_ADMIN_KEY" http://127.0.0.1:8765/usage`
- **Spend cap:** set `TASKPILOT_DAILY_BUDGET_USD` (with the two prices) or `TASKPILOT_DAILY_TOKEN_CAP`. When it is reached, tasks stop with a plain message and everything resumes at midnight UTC. Also set a spend alert on the NVIDIA dashboard.
- **Sign-up** is open: anyone with an e-mail address can make a workspace, with 30 tasks a day each and at most 1000 workspaces. Change it in the env file, or `TASKPILOT_SIGNUP=closed` to stop new people.

## What a laptop can and cannot do
An i5 with 16 GB is plenty: the server's own work is small, and the real limits are your API key's rate limit and the model's speed.
The weak points are not the hardware but the home setup: power cuts, Wi-Fi drops and the laptop being closed. A wired connection
and a charger make a big difference.
