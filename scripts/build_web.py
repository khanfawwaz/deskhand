"""Build the React app and put it where the server serves it from.

    python scripts/build_web.py                 npm ci, type-check, build, copy to src/taskpilot/webapp/
    python scripts/build_web.py --skip-install  reuse web-app/node_modules (faster while working on it)

The Docker image does the same thing in its own Node stage (see deploy/docker/Dockerfile), so a server built from
the repository always carries a fresh build. src/taskpilot/webapp/ is generated output and is not kept in git.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web-app"
DEST = ROOT / "src" / "taskpilot" / "webapp"


def npm(*args: str) -> None:
    exe = shutil.which("npm")
    if not exe:
        raise SystemExit("npm was not found: install Node.js 20 or newer (https://nodejs.org)")
    subprocess.run([exe, *args], cwd=APP, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-install", action="store_true", help="do not run npm ci first")
    args = ap.parse_args()

    if not args.skip_install:
        npm("ci")
    npm("run", "build")

    dist = APP / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit(f"the build did not produce {dist / 'index.html'}")
    shutil.rmtree(DEST, ignore_errors=True)
    shutil.copytree(dist, DEST)
    files = [p for p in DEST.rglob("*") if p.is_file()]
    print(f"copied {len(files)} files ({sum(p.stat().st_size for p in files) // 1024} KB) to {DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
