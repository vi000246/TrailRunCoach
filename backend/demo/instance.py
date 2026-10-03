"""
The demo process (WKO5COACH_MODE=demo): start-up checks and its background
loop (auth-and-demo plan §3.1, §3.3).

startup_checks() refuses to start when
  1. WKO5COACH_HOME is unset, or is / is inside the owner's ~/.wko5coach
     (or ~/WKO5);
  2. the demo data has not been built (<demo-root>/base/current);
and it empties secrets.SEALED_FILES (the demo never unseals the repo's TP client).
settings/paths.athlete_dir() never looks for a WKO5 folder in the demo.

loop(): every 10 minutes the sandbox janitor; with WKO5COACH_DEMO_REBUILD=1
(the Docker image) a new base is built once the current one is 7 days old
(so the last activity is always recent) and switched in atomically.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path

from backend import tenancy

log = logging.getLogger(__name__)
JANITOR_S = 600
REBUILD_DAYS = 7


class DemoStartupError(RuntimeError):
    pass


def _inside(p: Path, root: Path) -> bool:
    try:
        p = p.resolve()
        root = root.resolve()
    except OSError:
        return False
    return p == root or root in p.parents


def check_home() -> Path:
    v = os.environ.get(tenancy.ENV_HOME, "").strip()
    if not v:
        raise DemoStartupError("demo mode needs WKO5COACH_HOME (its own data folder)")
    home = Path(v)
    real = Path(os.environ.get("WKO5COACH_TEST_REAL_HOME") or Path.home())
    for owner in (real / ".wko5coach", real / "WKO5"):
        if _inside(home, owner):
            raise DemoStartupError(f"demo mode refuses WKO5COACH_HOME={home}: it is the owner's {owner}")
    return home


def startup_checks() -> None:
    check_home()
    from backend.demo import sandbox as SB
    try:
        SB.current_base_dir()
    except SB.DemoNotReady as e:
        raise DemoStartupError(str(e))
    from backend.settings import secrets
    secrets.SEALED_FILES = []
    os.environ["WKO5COACH_NO_SCHEDULER"] = "1"


def base_anchor() -> dt.date | None:
    from backend.demo import sandbox as SB
    try:
        m = json.loads((SB.current_base_dir() / "demo_manifest.json").read_text("utf-8"))
        return dt.date.fromisoformat(m["anchor"])
    except (OSError, ValueError, KeyError, SB.DemoNotReady):
        return None


def rebuild_due(today: dt.date | None = None) -> bool:
    a = base_anchor()
    today = today or dt.date.today()
    return a is None or (today - a).days >= REBUILD_DAYS


async def loop() -> None:
    from backend.demo import sandbox as SB
    while True:
        try:
            await asyncio.to_thread(SB.janitor)
        except Exception as e:          # noqa: BLE001
            log.warning("demo janitor failed: %s", type(e).__name__)
        if os.environ.get("WKO5COACH_DEMO_REBUILD") == "1" and rebuild_due():
            try:
                from backend.demo import build as B
                await asyncio.to_thread(B.rebuild_in_place)
            except Exception as e:      # noqa: BLE001
                log.warning("demo rebuild failed: %s", type(e).__name__)
        await asyncio.sleep(JANITOR_S)
