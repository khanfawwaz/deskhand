"""Every non-Python file the server reads at runtime must be listed in pyproject's package-data, or a normal (non-editable)
install, such as the Docker image, leaves it out and the route that reads it answers 500."""
import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "taskpilot"


def _package_data() -> list[str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return cfg["tool"]["setuptools"]["package-data"]["taskpilot"]


def _covered(rel: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, g) for g in globs)


def test_install_template_is_packaged():
    from taskpilot.distribution import TEMPLATE
    rel = TEMPLATE.relative_to(PKG).as_posix()
    assert TEMPLATE.is_file()
    assert _covered(rel, _package_data()), f"{rel} is read by GET /i/<code> but is not in package-data"


def test_every_non_python_file_in_the_package_is_covered():
    globs = _package_data()
    missing = [p.relative_to(PKG).as_posix() for p in PKG.rglob("*")
               if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts
               and not _covered(p.relative_to(PKG).as_posix(), globs)]
    assert not missing, f"not in package-data, so missing from a normal install: {missing}"
