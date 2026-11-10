# Running the Deskhand server in Docker

```
 browsers / phones / Windows helpers
        │ https / wss
        ▼
 A small public server (any VPS): nginx with the certificate, hides the admin paths, shows a friendly page if the app is down
        │ a private link (for example Tailscale or a VPN); nothing is opened on the home router
        ▼
 Your own machine: the Deskhand container (port 8765 inside, any port outside), all processing happens here
        │ data: a folder on the big disk (database, server policy, secrets file with owner-only permissions)
        ▼
 The AI model provider (the key lives only in that secrets file)
```

Nothing in this repository contains an address, key or password for any real machine: those live in the secrets file and
in the front server's configuration.

## Run it from source (development or first try)
```bash
cd deploy/docker
docker compose up -d --build
docker logs -f deskhand_server
```
The compose file expects a data folder (`/data/deskhand`) with `deskhand.env` (your secrets) and `server_policy.json`.
See `deploy/laptop/laptop.env.example` for the settings.

## Run it from the published image (production)
The GitHub workflows build and publish the image after the tests pass, and the server updates itself with a health
check and automatic rollback. See `docs/DEPLOY.md` and the files in `deploy/server/`.

## Back up
```bash
docker exec deskhand_server python -c "import sqlite3;s=sqlite3.connect('/data/taskpilot.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"
```
