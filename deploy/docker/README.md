# Deskhand on the home server (Docker), Oracle as the front door

```
 browsers / phones / Windows helpers
        │ https / wss  deskhand.fawwazkhan.dev  (DNS -> Oracle's public IP; no change needed when moving servers)
        ▼
 Oracle: nginx (certificate, hides admin paths, friendly 503 page)     proxy_pass http://100.86.241.28:8766
        │ Tailscale (private link; nothing opened on the home router)
        ▼
 Home server: container deskhand_server (:8766 -> :8765), all processing here
        │ data: /data/deskhand  (taskpilot.db, server_policy.json, deskhand.env chmod 600)
        ▼
 NVIDIA API (key only in /data/deskhand/deskhand.env)
```

Same shape as Nextcloud (cloud.fawwazkhan.dev -> 100.86.241.28:8080).

## Run / update (on the home server)
```bash
cd ~/deskhand-src/deploy/docker && docker compose up -d --build     # build and (re)start
docker logs -f deskhand_server
```
Update the code: copy the new `src/`, `pyproject.toml` and `deploy/` into `~/deskhand-src`, then run the line above.

## Back up
`docker exec deskhand_server python -c "import sqlite3;s=sqlite3.connect('/data/taskpilot.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"`

## Roll back to Oracle
On Oracle: `sudo systemctl start deskhand` and change `proxy_pass` in `/etc/nginx/conf.d/deskhand.conf` back to `http://127.0.0.1:8765;`
(backup of the old file: `deskhand.conf.bak-20261109`), then `sudo nginx -t && sudo systemctl reload nginx`.
Copy the database back first if you want what was done on the home server.
