"""Shared helpers for the demo-mode tests: a minimal demo root in tmp_path
(no generator: an initialised DB + a plan with one event), and a TestClient
on the demo app. Synthetic only — never ~/WKO5 or ~/.wko5coach."""
from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path


def make_base(root: Path, name: str = "b1") -> Path:
    from backend import tenancy
    from backend.db import database
    from backend.engine.planning import Plan
    d = root / "base" / name
    d.mkdir(parents=True, exist_ok=True)
    t = tenancy.demo_base(d)
    with tenancy.use(t):
        async def go():
            await database.init_db()
            await database.dispose(d / "wko5coach.db")
        asyncio.run(go())
        today = dt.date.today()
        plan = Plan()
        plan.upsert_event({"name": "示範越野 50K", "date": (today + dt.timedelta(days=40)).isoformat(),
                           "priority": "A", "kind": "trail"})
        plan.save(d / "plan.json")
    (d / "demo_manifest.json").write_text('{"anchor": "%s"}' % dt.date.today().isoformat(), "utf-8")
    (root / "base" / "current").write_text(name + "\n", "utf-8")
    return d


def demo_env(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("WKO5COACH_MODE", "demo")
    monkeypatch.setenv("WKO5COACH_HOME", str(root))
    monkeypatch.setenv("WKO5COACH_COOKIE_SECURE", "0")
    monkeypatch.delenv("WKO5COACH_DEMO_REBUILD", raising=False)
    # the conftest points the settings reads at no DB; the demo reads its tenant's
    from backend.engine.wko5expr import datasource
    from backend.db.database import db_path
    monkeypatch.setattr(datasource, "_db_path", lambda: db_path())
    # the conftest's fixed tmp folders off: everything follows the demo tenant (all inside `root`)
    from backend.sync import storage
    from backend.engine import routes, event_gpx, race_calc_store, activity_tags
    from backend.engine.racepower import athlete
    from backend.engine.wko5expr import fitcache
    monkeypatch.setattr(storage, "FIT_ROOT", None)
    monkeypatch.setattr(routes, "HOME", None)
    monkeypatch.setattr(event_gpx, "ROOT", None)
    monkeypatch.setattr(athlete, "HIKE_META", None)
    monkeypatch.delenv(fitcache.ENV_ROOT, raising=False)
    monkeypatch.setattr(event_gpx, "_default_db", lambda: db_path())
    monkeypatch.setattr(race_calc_store, "_default_db", lambda: db_path())
    monkeypatch.setattr(activity_tags, "_default_db", lambda: db_path())
    from backend import tenancy_mw as MW
    for b in (MW.REQ_BUCKET, MW.CREATE_HOUR, MW.CREATE_DAY, MW.WRITE_MIN, MW.HEAVY_IP):
        b.reset()


def csrf(client) -> str:
    r = client.get("/api/v1/session")
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


def tree_hash(d: Path) -> str:
    """sha256 over the base's DB and plan files (what a sandbox must never change)."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file() and (p.suffix in (".db", ".json") and "cache" not in p.parts):
            h.update(str(p.relative_to(d)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()
