"""Serves the Deskhand app (the React app in the web-app/ folder at the top of the repository) at /app/.

How it fits together
  * `python scripts/build_web.py` (or the Docker build) builds web-app/ and copies the result to src/taskpilot/webapp/.
  * This module serves that folder. It does nothing if the folder is missing, so a checkout that was never built still runs.
  * It is a single-page app: any address that looks like a page (no file extension) gets index.html and the app's own
    router takes over, so /app/chat/abc works when typed or reloaded. A missing file with an extension is a real 404.
  * Files in assets/ have a content hash in their name, so they are cached for a year; everything else (index.html,
    the manifest, icons) is re-checked each time, so a new release shows up at once.

  * A few files from before the React app still live in src/taskpilot/web/ (the privacy and terms pages, their stylesheet,
    the Google verification file) and keep their old /app/ addresses, because those are printed where they cannot be
    changed (a store listing). The build is looked in first, then that folder.
  * Old /app-next/ links (the React app's address while it was a preview) redirect to the same page under /app/.
  * With no build at all (a checkout that was never built) /app/ shows a short page saying how to build it.
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

MOUNT = "/app"                           # keep in step with APP_BASE in web-app/app-base.ts
DEFAULT_DIR = Path(__file__).parent / "webapp"
LEGACY_DIR = Path(__file__).parent / "web"   # the legal pages and other files that kept their /app/ address
OLD_MOUNT = "/app-next"
NOT_BUILT = ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>Deskhand</title>"
             "<p>The Deskhand app has not been built on this server. Run <code>python scripts/build_web.py</code>.</p></html>")
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


def install_spa(app: FastAPI, dist: Path | None = None, mount: str = MOUNT, legacy: Path | None = LEGACY_DIR) -> bool:
    """Add the routes. Returns whether a build was found (without one, /app/ says how to build it)."""
    root = (Path(dist) if dist else webapp_dir()).resolve()
    index = root / "index.html"
    built = index.is_file()
    folders = [f for f in (root if built else None, Path(legacy).resolve() if legacy else None) if f and f.is_dir()]

    def index_page() -> Response:
        if built:
            return FileResponse(index, media_type="text/html; charset=utf-8", headers={"Cache-Control": REVALIDATE})
        return HTMLResponse(NOT_BUILT, status_code=503, headers={"Cache-Control": REVALIDATE})

    def find(path: str) -> Path | None:
        for folder in folders:
            target = (folder / path).resolve()
            if folder != target and folder not in target.parents:     # ../ tricks never leave the folder
                continue
            if target.is_file():
                return target
        return None

    async def bare() -> RedirectResponse:
        return RedirectResponse(mount + "/", status_code=308)

    async def spa(path: str) -> Response:
        if not path or path == "index.html":
            return index_page()
        target = find(path)
        if target:
            cache = IMMUTABLE if path.startswith("assets/") else REVALIDATE
            return FileResponse(target, headers={"Cache-Control": cache})
        if Path(path).suffix:                                      # a missing image or script is a 404, not a page
            raise HTTPException(404)
        return index_page()                                        # a page address: the app's router decides

    async def old_address(request: Request, path: str = "") -> RedirectResponse:
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"{mount}/{path}{query}", status_code=308)

    app.add_api_route(mount, bare, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route(mount + "/{path:path}", spa, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route(OLD_MOUNT, old_address, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route(OLD_MOUNT + "/{path:path}", old_address, methods=["GET", "HEAD"], include_in_schema=False)
    return built
