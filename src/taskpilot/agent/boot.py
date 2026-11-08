"""Run a Deskhand module out of one specific version folder.

    Deskhand.exe boot.py <version folder> <module> [arguments...]
    Deskhand.exe boot.py --root <install folder> <module> [arguments...]     # use the CURRENT version of that install

An embedded Python ignores PYTHONPATH, so the version folder is put on sys.path here instead. With --root the install
folder is put on sys.path too (for the stable launcher module) and PILOT_HOME / PILOT_VERSION are set, so the Deskhand
app can start straight from the scheduled task without a separate launcher process. Standard library only.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    args = sys.argv[1:]
    if len(args) >= 3 and args[0] == "--root":
        root = Path(args[1])
        try:
            version = (root / "apps" / "current.txt").read_text().strip()
        except OSError:
            raise SystemExit("no installed version found")
        vdir = root / "apps" / version
        os.environ["PILOT_HOME"], os.environ["PILOT_VERSION"] = str(root), version
        sys.path.insert(0, str(root))                    # the stable launcher.py lives here
        module, rest = args[2], args[3:]
    elif len(args) >= 2:
        vdir, module, rest = Path(args[0]), args[1], args[2:]
    else:
        raise SystemExit("usage: boot.py <version folder> <module> [args...]  |  boot.py --root <install folder> <module> [args...]")
    sys.path.insert(0, str(vdir))
    sys.argv = ["deskhand", *rest]
    runpy.run_module(module, run_name="__main__", alter_sys=False)


if __name__ == "__main__":
    main()
