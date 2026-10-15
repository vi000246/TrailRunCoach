"""
Where synced FIT files live — one folder per source:

    ~/.wko5coach/fit/coros/objects/<aa>/<sha256>.fit.gz   (SP-308, files/fitstore.py)
    ~/.wko5coach/fit/garmin/objects/<aa>/<sha256>.fit.gz  (the Garmin trial, SP-91)
    ~/.wko5coach/fit/tp/objects/<aa>/<sha256>.fit.gz
    ~/.wko5coach/fit/intervals/objects/<aa>/<sha256>.fit.gz  (intervals.icu, SP-346)
    ~/.wko5coach/fit/{coros,tp}/<year>/...                (before the fit_objects migration)

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
# folder / API name -> DB source (intervals: intervals.icu, SP-346)
SOURCES = {"coros": "coros", "garmin": "garmin", "tp": "trainingpeaks", "intervals": "intervals"}
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
    """File count and total bytes on disk (the date span comes from the DB): the source
    folder's files through the FIT store's listing (files/fitstore.py: gzip objects and
    legacy .fit alike; symlinks and half-written temporary files left out)."""
    from backend.files.fitstore import LocalDirStore
    check_source(source)
    count, size = 0, 0
    if source_dir(source).is_dir():
        for o in LocalDirStore(fit_root()).list(source):
            count += 1
            size += o.size
    return {"files": count, "bytes": size}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"
