"""Serves the built React app (the web-app/ folder at the top of the repository) at /app-next/.

How it fits together
  * `python scripts/build_web.py` (or the Docker build) builds web-app/ and copies the result to src/taskpilot/webapp/.
  * This module serves that folder. It does nothing if the folder is missing, so a checkout that was never built still runs.
  * It is a single-page app: any address that looks like a page (no file extension) gets index.html and the app's own
    router takes over, so /app-next/chat/abc works when typed or reloaded. A missing file with an extension is a real 404.
  * Files in assets/ have a content hash in their name, so they are cached for a year; everything else (index.html,
    the manifest, icons) is re-checked each time, so a new release shows up at once.

The old app keeps /app/ untouched until the switch-over; at that point MOUNT here and APP_BASE in web-app/app-base.ts
change together.
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

MOUNT = "/app-next"                      # keep in step with APP_BASE in web-app/app-base.ts
DEFAULT_DIR = Path(__file__).parent / "webapp"
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

# Windows reads file types from the registry and can call .js "text/plain": say it explicitly
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"),
                    (".webmanifest", "application/manifest+json"), (".json", "application/json"), (".png", "image/png")):
    mimetypes.add_type(_type, _ext)


def webapp_dir() -> Path:
    """Where the built app is. DESKHAND_WEBAPP_DIR overrides it (tests, or serving a build from somewhere else)."""
    override = os.environ.get("DESKHAND_WEBAPP_DIR")
    return Path(override) if override else DEFAULT_DIR


def install_spa(app: FastAPI, dist: Path | None = None, mount: str = MOUNT) -> bool:
    """Add the routes. Returns False (and adds nothing) when there is no build to serve."""
    root = Path(dist) if dist else webapp_dir()
    index = root / "index.html"
    if not index.is_file():
        return False
    root = root.resolve()

    def index_page() -> FileResponse:
        return FileResponse(index, media_type="text/html; charset=utf-8", headers={"Cache-Control": REVALIDATE})

    async def bare() -> RedirectResponse:
        return RedirectResponse(mount + "/", status_code=308)

    async def spa(path: str) -> FileResponse:
        if not path:
            return index_page()
        target = (root / path).resolve()
        if root != target and root not in target.parents:          # ../ tricks never leave the build folder
            raise HTTPException(404)
        if target.is_file():
            cache = IMMUTABLE if path.startswith("assets/") else REVALIDATE
            return FileResponse(target, headers={"Cache-Control": cache})
        if Path(path).suffix:                                      # a missing image or script is a 404, not a page
            raise HTTPException(404)
        return index_page()                                        # a page address: the app's router decides

    app.add_api_route(mount, bare, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route(mount + "/{path:path}", spa, methods=["GET", "HEAD"], include_in_schema=False)
    return True
