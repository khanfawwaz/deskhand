"""Clean addresses for the legal pages: https://<server>/privacy and /terms (no ".html").

The files themselves live with the web app, and the old addresses (/app/privacy.html, /app/terms.html) keep working,
because they are already printed in places that cannot be edited later (for example a store listing).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

WEB = Path(__file__).parent / "web"
PAGES = {"privacy": "privacy.html", "terms": "terms.html"}


def install_legal_pages(app: FastAPI) -> None:
    def page_for(filename: str):
        async def page():
            return FileResponse(WEB / filename, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})
        return page

    def slash_to_clean(slug: str):
        async def redirect():
            return RedirectResponse(f"/{slug}", status_code=308)
        return redirect

    for slug, filename in PAGES.items():
        app.add_api_route(f"/{slug}", page_for(filename), methods=["GET"], include_in_schema=False)
        app.add_api_route(f"/{slug}/", slash_to_clean(slug), methods=["GET"], include_in_schema=False)
