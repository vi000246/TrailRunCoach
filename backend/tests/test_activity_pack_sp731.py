"""
SP-731 ⑥: the activity list types the 背負 (kg) of a hike in place — PATCH /activities pack_kg writes the
same 「這次背多少」 record as the activity card (racepower_hike_meta.json), the calculator caches go, the
placeholder is that kind's 預設背負 (SP-740), and on a phone the column folds into the row's 「＋」.
tmp stores only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.engine import calibrate as CAL
from backend.engine.racepower import athlete as A
from backend.engine.racepower import pack_defaults as PD

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)
    PD.forget()
    yield
    PD.forget()


def test_list_patch_pack_in_place(tmp_path, monkeypatch):
    import asyncio
    import backend.db.database as D
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.api import racepower as RP
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.engine import activity_tags as AT
    from backend.tests.test_activity_edit import _fit_ds
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    w = next(w for w in ds.workouts if w.entry.file == "2025/0.fit")
    key = AT.key_of(w.entry.start)
    with RP._lock:
        RP._cache["inputs"] = "stale"
        RP._cache["grade"] = "stale"

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        item = V.BulkItem(key=key, file="2025/0.fit")
        r = await V.patch_activities(V.BulkBody(items=[item], pack_kg=6.5))
        assert r["saved"] == [key]
        with pytest.raises(HTTPException) as e:
            await V.patch_activities(V.BulkBody(items=[item], pack_kg=55))
        assert e.value.status_code == 400
        await eng.dispose()
    asyncio.new_event_loop().run_until_complete(_inner())
    rec = A._trip_of(A.hike_meta(), w)
    assert rec["pack_kg"] == 6.5 and rec["days"] == 1 and rec["hike"] is False        # a run: never learned from
    with RP._lock:
        assert RP._cache.get("inputs") is None and RP._cache.get("grade") is None     # calculator caches dropped
    lst = V.activities_list()
    a = {x["file"]: x for x in lst["activities"]}["2025/0.fit"]
    assert a["pack_kg"] == 6.5 and a["pack_days"] == 1
    assert lst["pack_defaults"]["single"]["kg"] == 4.0 and lst["pack_defaults"]["multi"]["kg"] == 9.0
    assert V.activities_pack_defaults()["single"]["source"] == "default"


def test_list_page_pack_column_and_phone_fold():
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert '{ k: "pack", cls: "c-pack num"' in page and "const packRow = (a) => isHikeRow(a) && a.index != null" in page
    assert "pack_kg: kg" in page and "/activities/pack-defaults" in page
    assert ".c-pack { display: none; }" in page                                    # phone: folded into ＋
    assert '(packRow(a) ? `<span class="mkl">${esc(T("col.pack"))}</span>${packCtl(a)}` : "")' in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text(encoding="utf-8"))
        for k in ("col.pack", "pack.tip", "pack_range"):
            assert cat.get(k), (loc, k)
        assert "{note}" in cat["pack.tip"]
