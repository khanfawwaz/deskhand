# Deploying Deskhand: GitHub builds it, the server fetches it

```
 you push to main
        │
        ▼
 GitHub Actions  "tests"      Python tests (Linux + Windows) and the React app (types, lint, unit tests, build)
        │ green only
        ▼
 GitHub Actions  "publish"    builds the Docker image, publishes ghcr.io/khanfawwaz/deskhand-server:latest (+ :sha-<commit>)
        │
        ▼                     the home server cannot be reached from the internet, so it comes and gets the image:
 Home server     deskhand-pull.timer  → every 2 minutes: pull; if the image changed, start it,
                                        wait for "healthy", and go back to the previous image if it never gets there
```

Why the server pulls instead of GitHub pushing: nothing is opened on the home router, no SSH key or server password is stored
in GitHub, and a bad build can never take the server down because the server checks health and rolls itself back.
The only secret the workflows use is the automatic `GITHUB_TOKEN`. The image contains no secrets: the database and the
keys stay on the server in `/data/deskhand` (`deskhand.env`).

## What is where
| File | Job |
|---|---|
| `.github/workflows/ci.yml` | tests on every push and pull request |
| `.github/workflows/publish.yml` | after green tests on `main`, build and publish the image |
| `deploy/docker/Dockerfile` | the image (Node stage builds the React app, Python stage runs the server) |
| `deploy/server/docker-compose.yml` | how the server runs the published image |
| `deploy/server/deskhand-pull.sh` | the update step with the health check and rollback |
| `deploy/server/deskhand-pull.service` / `.timer` | runs the script every 2 minutes |
| `deploy/server/install.sh` | one-time install of the timer |

## One-time setup
1. Push to `main`. When "tests" and then "publish" are green, the image exists under *Packages* on the GitHub profile.
2. **Package visibility.** Easiest: set the `deskhand-server` package to **Public** (the image has no secrets; the code is the repository's).
   If you keep it private, sign the server in once: `docker login ghcr.io -u khanfawwaz` with a token that has `read:packages`.
3. **Switch the server from the hand-deployed build to the published image** (about 15 seconds without the app):
   ```bash
   mkdir -p ~/deskhand-deploy && cp deploy/server/* ~/deskhand-deploy/        # copy the folder from the repository
   docker pull ghcr.io/khanfawwaz/deskhand-server:latest
   cd ~/deskhand-src/deploy/docker && docker compose down                      # stops only the old deskhand_server
   cd ~/deskhand-deploy && docker compose up -d
   curl -s http://localhost:8766/v1/version                                    # shows the commit now running
   bash install.sh                                                             # starts the 2-minute update timer
   ```
4. Nothing else. Every green push to `main` is live within a few minutes.

## Everyday use
- **Which commit is live?** `https://deskhand.fawwazkhan.dev/v1/version` → `commit` is the short id GitHub built.
- **What did the updater do?** `sudo journalctl -u deskhand-pull -n 40 --no-pager`
- **Update right now:** `bash ~/deskhand-deploy/deskhand-pull.sh`
- **Pause updates:** `sudo systemctl stop deskhand-pull.timer` (start it again with `start`).
- **Go back by hand:** `docker tag deskhand-server:previous ghcr.io/khanfawwaz/deskhand-server:latest && cd ~/deskhand-deploy && docker compose up -d`
- **Go back to the old hand-built way:** `cd ~/deskhand-src/deploy/docker && docker compose up -d --build`

## Safety notes
- A new image that does not report healthy within 90 seconds is replaced by the previous one automatically; the journal says so.
- Only the commit whose tests passed is published (`publish.yml` builds the exact commit of the passing run).
- Pull requests from forks run the tests but never publish.
- The server's data (`/data/deskhand`) is not part of the image and is never touched by an update.
