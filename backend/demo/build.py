"""
Build the demo athlete's data (auth-and-demo plan §3.2):

    python -m backend.demo.build --root <demo-root> [--seed 20261002] [--anchor 2026-10-03] [--weeks 52] [--small]
    python -m backend.demo.build --root <demo-root> --add-linked      # an existing base: add only the
                                                                      # planned interval / trail runs (linked.py)

Writes <demo-root>/base/<anchor>-<seed>/ (a full tenant folder, the layout of
~/.wko5coach) and then switches <demo-root>/base/current to it atomically.

The build runs the app itself on that folder (owner mode with WKO5COACH_HOME
= the new base, no WKO5 folder, no network): the FITs are imported by the
same code as a sync (file_service.scan_and_import), the settings are written
through the settings store, the A race's course goes through the event-GPX
upload, and the warm-up opens the pages' data routes (overview status, the
schedule — which generates the coming weeks' sessions — charts, race
calculator), so every cache is on disk before the first visitor.

It never touches ~/.wko5coach or ~/WKO5: it refuses a --root inside either,
and it runs the app in a child process whose environment points away from
both (WKO5_ATHLETE_DIR = a missing folder).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_SEED = 20261002
ENV_STAGE = "WKO5COACH_DEMO_BUILD_STAGE"       # set in the child process


def _refuse_owner(root: Path) -> None:
    from backend.demo.instance import DemoStartupError, _inside
    real = Path(os.environ.get("WKO5COACH_TEST_REAL_HOME") or Path.home())
    for owner in (real / ".wko5coach", real / "WKO5"):
        if _inside(root, owner):
            raise DemoStartupError(f"refusing to build demo data inside the owner's {owner}")


def child_env(base: Path) -> dict:
    env = dict(os.environ)
    for k in ("WKO5COACH_MODE", "WKO5COACH_ATHLETE_DIR", "WKO5_VIEWS_DIR", "WKO5COACH_ROUTES_DIR",
              "WKO5COACH_FIT_CACHE", "TP_CLIENT_ID", "TP_CLIENT_SECRET"):
        env.pop(k, None)
    env.update({
        "WKO5COACH_HOME": str(base),
        "WKO5_ATHLETE_DIR": str(base / "_no_wko5"),      # never look for the owner's WKO5 folder
        "WKO5COACH_NO_SCHEDULER": "1", "WKO5COACH_NO_AUTO_BACKUP": "1", "WKO5COACH_NO_WARMUP": "1",
        "TRC_DEFAULT_LOCALE": env.get("TRC_DEFAULT_LOCALE", "zh-TW"),
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def build(root: Path, seed: int = DEFAULT_SEED, anchor: dt.date | None = None, weeks: int = 52,
          small: bool = False, switch: bool = True, warm: bool = True) -> Path:
    root = Path(root).resolve()
    _refuse_owner(root)
    anchor = anchor or dt.date.today()
    name = f"{anchor.isoformat()}-{seed}" + ("-s" if small else "")
    base_root = root / "base"
    base_root.mkdir(parents=True, exist_ok=True)
    final = base_root / name
    tmp = base_root / f".{name}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t0 = time.time()
    cmd = [sys.executable, "-m", "backend.demo.build", "--stage", str(tmp), "--seed", str(seed),
           "--anchor", anchor.isoformat(), "--weeks", str(weeks)] + (["--small"] if small else []) + \
          ([] if warm else ["--no-warm"])
    r = subprocess.run(cmd, env=child_env(tmp), cwd=str(Path(__file__).resolve().parents[2]))
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"demo build failed (exit {r.returncode})")
    if final.exists():
        shutil.rmtree(final)
    os.replace(tmp, final)
    relocate_paths(final)
    if switch:
        from backend import tenancy
        from backend.demo import sandbox as SB
        old = os.environ.get(tenancy.ENV_HOME)
        os.environ[tenancy.ENV_HOME] = str(root)
        try:
            SB.set_current_base(name)
        finally:
            if old is None:
                os.environ.pop(tenancy.ENV_HOME, None)
            else:
                os.environ[tenancy.ENV_HOME] = old
    print(f"demo base {name}: {time.time() - t0:.0f} s", flush=True)
    return final


def relocate_paths(base: Path) -> int:
    """The DB's workout_files.file_path rows are absolute; the build imports in the
    staging folder and then renames it (and a demo root may be copied elsewhere), so
    point every row whose file is gone at the same file under `base` (the part from
    its fit/ folder on). Without this a later import (--add-linked) took every FIT
    for a new one. Returns the rows changed."""
    import sqlite3
    db = base / "wko5coach.db"
    if not db.is_file():
        return 0
    con = sqlite3.connect(str(db))
    try:
        n = 0
        for rid, fp in con.execute("SELECT id, file_path FROM workout_files").fetchall():
            if not fp or Path(fp).exists():
                continue
            norm = str(fp).replace("\\", "/")
            k = norm.rfind("/fit/")
            if k < 0:
                continue
            new = base / norm[k + 1:]
            if new.is_file():
                con.execute("UPDATE workout_files SET file_path = ? WHERE id = ?", (str(new), rid))
                n += 1
        con.commit()
        return n
    finally:
        con.close()


def rebuild_in_place() -> Path:
    """The demo process's weekly rebuild: a new base for today, then switch."""
    from backend import tenancy
    return build(tenancy.home_root(), anchor=dt.date.today())


# ---------------------------------------------------------------------------
# the child process: runs inside the new base as the app's owner tenant
# ---------------------------------------------------------------------------

SETTINGS = {
    "charts.data_source": "source",
    "sync.primary": "coros",
    "athlete.timezone": "Asia/Taipei",
    "sync.auto_on_open.enabled": False,
}


def _stage(base: Path, seed: int, anchor: dt.date, weeks: int, small: bool, warm: bool) -> None:
    import asyncio
    from backend.demo.generate import generate
    t0 = time.time()
    manifest = generate(base, seed=seed, anchor=anchor, weeks=weeks, small=small)
    print(f"  generated {manifest['n_activities']} activities in {time.time() - t0:.0f} s", flush=True)
    # the AeT test is recognised by a plan AeT row on its day (workout_review.classify)
    from backend.engine.planning import Plan, Threshold
    aet = next((x for x in manifest["activities"] if x["kind"] == "aet_test"), None)
    if aet:
        plan = Plan.load(base / "plan.json")
        if not any(t.date == aet["date"] for t in plan.thresholds):
            plan.thresholds.append(Threshold(date=aet["date"], aethr=float(manifest["athlete"].get("aethr", 150)),
                                             note="AeT 測試（示範）"))
            plan.save(base / "plan.json")

    async def db_part():
        from backend.db import database
        from backend.db.current import ensure_athlete
        from backend.files.file_service import scan_and_import
        from backend.settings.repository import SettingsRepository, UnknownSetting
        await database.init_db()
        async with database.AsyncSessionLocal() as db:
            await ensure_athlete(db)
            await db.commit()
            repo = SettingsRepository(db, 1)
            for k, v in SETTINGS.items():
                try:
                    await repo.set(k, v)
                except (UnknownSetting, ValueError, KeyError):
                    pass
            for k in ("sync.coros.enabled", "sync.tp.enabled", "sync.schedule.enabled"):
                try:
                    await repo.set(k, False)
                except (UnknownSetting, ValueError, KeyError):
                    pass
            await db.commit()
            summary = await scan_and_import(db, 1, str(base / "fit"))
        print(f"  imported {summary}", flush=True)
        await database.dispose()
    t1 = time.time()
    asyncio.run(db_part())
    print(f"  DB + import: {time.time() - t1:.0f} s", flush=True)

    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=False), raise_server_exceptions=False) as c:
        def ok(r, what):
            if r.status_code >= 400:
                print(f"  ! {what}: {r.status_code} {r.text[:200]}", flush=True)
            return r
        # the course of each demo race goes on its event (event-GPX upload, as the plan page does)
        ids = {e.get("id") for e in c.get("/api/v1/plan").json().get("events", [])}
        for course, eid in (manifest.get("course_events") or {}).items():
            rel = (manifest.get("courses") or {}).get(course)
            if eid not in ids or not rel:
                continue
            p = base / rel
            ok(c.post(f"/api/v1/plan/events/{eid}/gpx",
                      files={"file": (p.name, p.read_bytes(), "application/gpx+xml")}), f"gpx {course}")
        # the activity titles (活動列表), as a watch would name them
        acts = c.get("/api/v1/wko5/workouts").json()
        rows = acts if isinstance(acts, list) else acts.get("workouts") or []
        by_file = {Path(str(r.get("file", ""))).name: r["index"] for r in rows if "index" in r}
        for a in manifest["activities"]:
            i = by_file.get(Path(a["file"]).name)
            if i is None or not a.get("name"):
                continue
            body = {"name": a["name"]}
            if a["kind"] in ("interval", "interval_short", "hill", "tempo"):
                # a FIT carries no plan: the 間歇 analysis is asked for as a user would (「當作間歇判讀」)
                from backend.engine.interval_eval import FLAG_TAG
                body["tags"] = [FLAG_TAG]
            if a["kind"].startswith("race"):
                body["activity_type"] = "race"
            ok(c.patch(f"/api/v1/wko5/workouts/{i}/activity", json=body), "name")
        # an interval run and a trail run done as the 課表 planned them, matched to their
        # sessions as after a COROS sync (backend/demo/linked.py)
        from backend.demo import linked
        linked.add(c, base, manifest, anchor, seed, small=small)
        if warm:
            show = [v for v in (manifest.get("showcase") or {}).values() if isinstance(v, str)]
            warm_base(c, anchor, show)
    (base / "demo_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")


def warm_base(c, anchor: dt.date, show: list[str], max_acts: int = 8, charts_for: int = 1) -> None:
    """Open the pages' data routes (overview, schedule, charts, race calculator) so the
    caches are on disk; every chart of the views for the first `charts_for` of the
    `show` activities (FIT paths), the review of up to `max_acts` of them."""
    def ok(r, what):
        if r.status_code >= 400:
            print(f"  ! {what}: {r.status_code} {r.text[:200]}", flush=True)
        return r
    t1 = time.time()
    for url in ("/api/v1/wko5/dataset/status", "/api/v1/overview/status", "/api/v1/overview/summary",
                "/api/v1/overview/pmc", "/api/v1/overview/plan/sessions?scope=week",
                f"/api/v1/overview/plan/calendar?start={anchor - dt.timedelta(days=35)}&end={anchor + dt.timedelta(days=28)}",
                f"/api/v1/overview/plan/compliance?start={anchor - dt.timedelta(days=84)}&end={anchor}",
                "/api/v1/wko5/workouts", "/api/v1/plan", "/api/v1/racepower/inputs",
                "/api/v1/racepower/goal-basis", "/api/v1/racepower/heat-status",
                "/api/v1/routes", "/api/v1/wko5/views"):
        t2 = time.time()
        ok(c.get(url), url)
        print(f"    {url.split('?')[0]}: {time.time() - t2:.1f} s", flush=True)
    # the next weeks' sessions (the schedule generates a week on its first visit)
    for w in range(1, 4):
        day = (anchor + dt.timedelta(weeks=w)).isoformat()
        ok(c.get(f"/api/v1/overview/plan/sessions?scope=week&day={day}"), f"week +{w}")
    # every chart of the views, for the showcase activities (render cache)
    acts = c.get("/api/v1/wko5/workouts").json()
    rows = acts if isinstance(acts, list) else acts.get("workouts") or []
    names = {s.split("/")[-1] for s in show}
    idxs = [r["index"] for r in rows if str(r.get("file", "")).replace("\\", "/").split("/")[-1] in names][:max_acts]
    views = c.get("/api/v1/wko5/views").json()
    n = 0
    for v in views if isinstance(views, list) else []:
        if v.get("error"):
            continue
        for d in v.get("dashboards", []):
            for ch in d.get("charts", []):
                url = f"/api/v1/wko5/views/{v['name']}/dashboards/{d['index']}/charts/{ch['index']}"
                for i in (idxs[:charts_for] or [None]):
                    c.get(url, params={"workout": i} if i is not None else None)
                    n += 1
    for i in idxs:
        c.get(f"/api/v1/wko5/workouts/{i}/review")
    print(f"  warmed {n} charts in {time.time() - t1:.0f} s", flush=True)


def add_linked(root: Path, warm: bool = True) -> Path:
    """Add the two linked sessions' activities (backend/demo/linked.py) to the current
    base of an existing demo root, in place: only the new FITs are imported and only
    what they change is recomputed (the FIT / dataset caches are per file)."""
    root = Path(root).resolve()
    _refuse_owner(root)
    name = (root / "base" / "current").read_text("utf-8").strip()
    base = root / "base" / name
    if not (base / "demo_manifest.json").is_file():
        raise RuntimeError(f"{base} is not a demo base (no demo_manifest.json)")
    t0 = time.time()
    print(f"  relocated {relocate_paths(base)} workout_files paths", flush=True)
    cmd = [sys.executable, "-m", "backend.demo.build", "--stage-linked", str(base)] + ([] if warm else ["--no-warm"])
    r = subprocess.run(cmd, env=child_env(base), cwd=str(Path(__file__).resolve().parents[2]))
    if r.returncode != 0:
        raise RuntimeError(f"adding the linked sessions failed (exit {r.returncode})")
    print(f"demo base {name}: linked sessions added in {time.time() - t0:.0f} s", flush=True)
    return base


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", help="the demo root (WKO5COACH_HOME of the demo process)")
    ap.add_argument("--stage", help=argparse.SUPPRESS)
    ap.add_argument("--stage-linked", help=argparse.SUPPRESS)
    ap.add_argument("--add-linked", action="store_true",
                    help="add only the planned-and-done interval / trail sessions to the current base of --root "
                         "(an existing build), in place")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--anchor", help="the last day of the data (default: today)")
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--small", action="store_true", help="a few weeks of short activities (tests)")
    ap.add_argument("--no-warm", action="store_true")
    a = ap.parse_args(argv)
    anchor = dt.date.fromisoformat(a.anchor) if a.anchor else dt.date.today()
    if a.stage:
        _stage(Path(a.stage), a.seed, anchor, a.weeks, a.small, not a.no_warm)
        return 0
    if a.stage_linked:
        from backend.demo import linked
        linked.stage(Path(a.stage_linked), dt.date.fromisoformat(a.anchor) if a.anchor else None, None,
                     warm=not a.no_warm)
        return 0
    if not a.root:
        ap.error("--root is required")
    if a.add_linked:
        add_linked(Path(a.root), warm=not a.no_warm)
        return 0
    build(Path(a.root), a.seed, anchor, a.weeks, a.small, warm=not a.no_warm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
