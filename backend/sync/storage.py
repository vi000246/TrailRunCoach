"""
Where synced FIT files live — one folder per source:

    ~/.wko5coach/fit/coros/<year>/...
    ~/.wko5coach/fit/tp/<year>/...

Everything that deletes files goes through `confined()`, which resolves the
path and refuses symlinks and anything outside the source's own folder, so a
bad DB row can never make the app delete from the WKO5 folder, the other
source, or anywhere else.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

FIT_ROOT = None      # fixed folder (tests); None = the tenant's shared fit/
SOURCES = {"coros": "coros", "tp": "trainingpeaks"}          # folder / API name -> DB source
DB_TO_FOLDER = {v: k for k, v in SOURCES.items()}


class PathNotConfined(ValueError):
    pass


def fit_root() -> Path:
    """<tenant shared>/fit (backend/tenancy.py): the owner's ~/.wko5coach/fit."""
    if FIT_ROOT is not None:
        return Path(FIT_ROOT)
    from backend import tenancy
    return tenancy.shared_path("fit")


def check_source(source: str) -> str:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {sorted(SOURCES)}")
    return source


def source_dir(source: str) -> Path:
    return fit_root() / check_source(source)


def year_dir(source: str, year) -> Path:
    d = source_dir(source) / str(year or "unknown")
    d.mkdir(parents=True, exist_ok=True)
    return d


def confined(path: Path | str, source: str) -> Path:
    """The resolved path if it is a regular file (or missing) strictly inside
    the source folder and not a symlink anywhere below it; else raise."""
    root = source_dir(source).resolve()
    p = Path(path)
    # refuse symlinks on the way (the file itself or any parent under root)
    cur = p
    while True:
        if cur.is_symlink():
            raise PathNotConfined("symlinks are not followed")
        if cur.parent == cur or cur.resolve() == root:
            break
        cur = cur.parent
    rp = p.resolve()
    if rp == root or root not in rp.parents:
        raise PathNotConfined("path is outside the source folder")
    return rp


def folder_stats(source: str) -> dict:
    """File count and total bytes on disk (the date span comes from the DB)."""
    root = source_dir(source)
    count, size = 0, 0
    if root.exists():
        for p in root.rglob("*"):
            if p.is_symlink() or not p.is_file():
                continue
            count += 1
            size += p.stat().st_size
    return {"files": count, "bytes": size}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"
