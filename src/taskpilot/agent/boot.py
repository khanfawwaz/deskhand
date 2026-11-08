"""Run a Deskhand module out of one specific version folder.

    Deskhand.exe boot.py <version folder> <module> [arguments...]

An embedded Python ignores PYTHONPATH, so the version folder is put on sys.path here instead. Standard library only.
"""
from __future__ import annotations

import runpy
import sys


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: boot.py <version folder> <module> [args...]")
    vdir, module, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    sys.path.insert(0, vdir)
    sys.argv = ["deskhand", *rest]
    runpy.run_module(module, run_name="__main__", alter_sys=False)


if __name__ == "__main__":
    main()
