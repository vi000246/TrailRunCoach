"""
Where the WKO5 athlete folder is (<Name>.wko5athlete + year/*.wko4).

  1. WKO5_ATHLETE_DIR (or WKO5COACH_ATHLETE_DIR, compare_sources' name) when set
  2. otherwise the first folder under one of DEFAULT_ROOTS (the root itself or
     one level below it) that holds a *.wko5athlete:
       ~/WKO5   where WKO5 keeps the library on the dev PC (moved here on
                2026-10-01, when ~/Projects/TrailRunCoach became the repo);
                docker-compose mounts it at the same path
  3. otherwise ~/WKO5 (a missing folder: the charts say there is no data)
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VARS = ("WKO5_ATHLETE_DIR", "WKO5COACH_ATHLETE_DIR")


def default_roots() -> list[Path]:
    home = Path.home()
    return [home / "WKO5"]


def _has_athlete(d: Path) -> bool:
    try:
        return d.is_dir() and any(d.glob("*.wko5athlete"))
    except OSError:
        return False


def find_athlete_dir(roots: list[Path]) -> Path | None:
    for root in roots:
        if _has_athlete(root):
            return root
        try:
            subs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        except OSError:
            subs = []
        for d in subs:
            if _has_athlete(d):
                return d
    return None


def athlete_dir() -> Path:
    for name in ENV_VARS:
        v = os.getenv(name)
        if v:
            return Path(v)
    roots = default_roots()
    return find_athlete_dir(roots) or roots[0]
