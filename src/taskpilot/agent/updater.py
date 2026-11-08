"""Fetch a newer agent from the Pilot server and stage it. The launcher switches to it on the next restart.

Layout under the install folder:   apps/<version>/taskpilot/...   apps/current.txt   apps/previous.txt
A version is only made 'current' after the download matched the server's published SHA-256 and unpacked cleanly.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import httpx


def http_base(ws_url: str) -> str:
    return ws_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1).rsplit("/agent/ws", 1)[0]


def stage_update(ws_url: str, device_id: str, token: str, apps: Path, current: str, timeout: float = 60) -> str | None:
    """Returns the new version if one was staged, else None. Raises on a bad download (nothing is switched)."""
    base = http_base(ws_url)
    hdr = {"X-Device-Id": device_id, "X-Device-Token": token}
    with httpx.Client(timeout=timeout) as c:
        info = c.get(f"{base}/agent/version", headers=hdr)
        info.raise_for_status()
        meta = info.json()
        new = meta["version"]
        if new == current or new in _bad(apps):                    # same version, or one the launcher rolled back from
            return None
        if (apps / new / "taskpilot").is_dir():                    # downloaded earlier (e.g. before a rollback)
            return new if _flip(apps, current, new) else None
        r = c.get(f"{base}/agent/bundle.zip", headers=hdr)
        r.raise_for_status()
    if hashlib.sha256(r.content).hexdigest() != meta["sha256"]:
        raise ValueError("update download does not match the published checksum; ignored")
    tmp = apps / f".{new}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for n in z.namelist():                                    # refuse paths that escape the folder
            if n.startswith(("/", "\\")) or ".." in Path(n).parts:
                raise ValueError("unsafe path in update")
        z.extractall(tmp)
    (tmp / "taskpilot" / "__init__.py").stat()                     # sanity: a package really is in there
    shutil.rmtree(apps / new, ignore_errors=True)
    tmp.rename(apps / new)
    return new if _flip(apps, current, new) else None


def _flip(apps: Path, current: str, new: str) -> bool:
    (apps / "previous.txt").write_text(current)
    (apps / "current.txt").write_text(new)
    return True


def _bad(apps: Path) -> set[str]:
    f = apps / "bad.txt"
    return set(f.read_text().split()) if f.exists() else set()
