"""
SP-731: the activity list edits 跟團 / 自己 (hikes) and 地形難度 (trail runs and hikes) in place, with a
faded suggestion (engine/group_hint.py: SP-716's rule; the route's default 地形難度), a 「只看還沒標的健行」
filter, the two columns folded into one button on a phone, and a route default (訓練路線 › 我的路段) that
the activities on it take while they have no mark of their own. 跟團 / 自己 is the single truth of the
solo hikes (athlete.solo_hikes). tmp / in-memory stores only.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT
from backend.engine import group_hint as GH
from backend.engine import terrain_difficulty as TD
from backend.engine.racepower import athlete as A

ROOT = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- the 跟團 / 自己 mark and the solo hikes (one truth) ----------------------------------

def test_group_mark_is_one_tag():
    assert AT.group_of(["跟團"]) == "group" and AT.group_of(["x", "自己步調"]) == "solo"
    assert AT.with_group(["跟團"], "solo") == ["自己步調"] and AT.with_group(["跟團"], None) == []
    assert AT.validate(group="team") == "INVALID_GROUP" and AT.validate(group="solo") is None
    assert AT.exclusive_marks(["跟團", "a", "自己步調"]) == ["a", "自己步調"]


def test_solo_hikes_reads_the_mark_over_the_old_opt_in_file(tmp_path, monkeypatch):
    old = tmp_path / "solo.json"
    old.write_text(json.dumps({"files": ["a.fit", "b.fit"], "starts": {"a.fit": "2026-05-01T06:00",
                                                                       "b.fit": "2026-06-01T06:00"}}), "utf-8")
    monkeypatch.setattr(A, "_solo_hikes_path", lambda: old)
    tags = [{"start_local": "2026-06-01T06:00", "file": "b.fit", "tags_json": '["跟團"]'},     # the mark wins
            {"start_local": "2026-07-01T06:00", "file": "c.fit", "tags_json": '["自己步調"]'},
            {"start_local": "2026-08-01T06:00", "file": None, "tags_json": '["自己步調"]'}]
    s = A.solo_hikes(tags=tags)
    assert "a.fit" in s and "b.fit" not in s and "c.fit" in s
    assert A.TAG_KEY_PREFIX + "2026-08-01T06:00" in set(s)
    # an explicit path (the old file alone, as the older tests use it) ignores the marks
    assert set(A.solo_hikes(old)) == {"a.fit", "b.fit"}


def test_set_solo_hikes_writes_the_mark_and_shrinks_the_old_file(tmp_path, monkeypatch):
    db = tmp_path / "tags.db"
    old = tmp_path / "solo.json"
    old.write_text(json.dumps({"files": ["2026/a.fit"], "starts": {"2026/a.fit": "2026-05-01T06:00"}}), "utf-8")
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    monkeypatch.setattr(A, "_solo_hikes_path", lambda: old)
    monkeypatch.setattr(A, "SOLO_HIKES", None)
    from backend.engine import activity_key as AK
    monkeypatch.setattr(AK, "start_of_file", lambda f: {"2026/a.fit": dt.datetime(2026, 5, 1, 6, 0),
                                                        "2026/b.fit": dt.datetime(2026, 6, 1, 6, 0)}.get(f))
    s = A.set_solo_hikes(["2026/b.fit"])                       # b ticked, a un-ticked
    assert "2026/b.fit" in s and "2026/a.fit" not in s
    rows = {r["start_local"]: r for r in AT.load(db)}
    assert AT.group_of(rows["2026-06-01T06:00"]["tags"]) == "solo"
    assert json.loads(old.read_text("utf-8"))["files"] == []   # the old opt-in is gone, not a second truth


def test_own_paced_follows_the_group_mark_first():
    w = NS(idx=0, sport="run", sport_type="trail running", tags=[], entry=NS(start=dt.datetime(2026, 1, 1, 7), file="r.fit"))
    h = NS(idx=1, sport="hike", sport_type="hiking", tags=[], entry=NS(start=dt.datetime(2026, 1, 2, 7), file="h.fit"))
    grp = [{"start_local": "2026-01-01T07:00", "file": "r.fit", "tags_json": '["跟團"]'}]
    solo = [{"start_local": "2026-01-02T07:00", "file": "h.fit", "tags_json": '["自己步調"]'}]
    assert A.own_paced(w, [], set()) is True and A.own_paced(w, grp, set()) is False
    assert A.own_paced(h, [], set()) is False and A.own_paced(h, solo, set()) is True


# ---- the suggestion (SP-716's rule) ------------------------------------------------------

def test_group_rule_and_its_parts():
    ref = [130, 140, 145, 150, 155, 160, 165, 170]
    assert GH.hr_percentile(128, ref) == 0.0 and GH.hr_percentile(171, ref) == 1.0
    assert GH.hr_percentile(150, ref) == pytest.approx(3.5 / 8)
    assert GH.hr_percentile(150, ref[:3]) is None                       # too few own runs
    assert GH.suggest(0.20, 0.80) == "group" and GH.suggest(0.70, 1.05) == "solo"
    assert GH.suggest(0.40, 0.80) is None and GH.suggest(0.20, 0.95) is None and GH.suggest(0.6, 0.85) is None
    assert GH.suggest(None, 0.8) is None
    # expected VAM from the own climbs: ln VAM = a + b·HR + c·grade; a climb 20 % slower reads 0.8
    climbs = [{"vam": 600 * (1 + 0.01 * (h - 150)) * (1 + 2 * (g - 0.15)), "avg_hr": h, "grade": g}
              for h in range(140, 170, 2) for g in (0.10, 0.15, 0.20)]
    m = GH.fit_vam(climbs)
    exp = math_exp_pred(m, 150, 0.15)
    assert GH.vam_ratio(m, [{"vam": 0.8 * exp, "avg_hr": 150, "grade": 0.15}]) == pytest.approx(0.8, rel=0.01)
    assert GH.fit_vam(climbs[:5]) is None and GH.vam_ratio(None, climbs) is None


def math_exp_pred(m, hr, g):
    import math
    return math.exp(m["a"] + m["b"] * hr + m["c"] * g)


def test_compute_suggests_only_for_hikes(monkeypatch):
    t0 = dt.datetime(2026, 1, 1, 7)
    runs = [NS(idx=i, sport="run", sport_type="trail running", tags=[], entry=NS(start=t0 + dt.timedelta(days=i), file=f"r{i}.fit"))
            for i in range(8)]
    hikes = [NS(idx=8, sport="hike", sport_type="hiking", tags=[], entry=NS(start=t0 + dt.timedelta(days=9), file="h8.fit")),
             NS(idx=9, sport="hike", sport_type="hiking", tags=[], entry=NS(start=t0 + dt.timedelta(days=10), file="h9.fit"))]
    ds = NS(workouts=runs + hikes, memo={}, flush_series=lambda: None)
    hr = {i: 145 + 3 * i for i in range(8)} | {8: 130, 9: 175}
    climb = lambda h, f: [{"vam": f * 600 * (1 + 0.01 * (h - 150)) * (1 + 2 * (g - 0.15)), "avg_hr": h, "grade": g}  # noqa: E731
                          for g in (0.10, 0.12, 0.15, 0.18, 0.20)]
    monkeypatch.setattr(GH, "_hr", lambda ds, w: hr[w.idx])
    monkeypatch.setattr(GH, "_climbs", lambda ds, w: climb(hr[w.idx], 0.7 if w.idx == 8 else 1.0))
    monkeypatch.setattr(A, "outdoor", lambda w: w.sport == "run")
    monkeypatch.setattr(A, "is_trail", lambda w: True)
    monkeypatch.setattr(A, "solo_hikes", lambda path=None, tags=None: set())
    out = GH.compute(ds, [])
    assert set(out) == {8, 9}
    assert out[8]["suggest"] == "group" and out[8]["vam_ratio"] == pytest.approx(0.7, rel=0.05)   # extrapolated below the own HRs
    assert out[9]["suggest"] == "solo"


# ---- the API: one PATCH per click, the marks in the list, the hints -------------------------

@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def test_api_list_patch_and_hints(tmp_path, no_plan, monkeypatch):
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    i = next(w.idx for w in ds.workouts if w.entry.file == "2025/0.fit")
    key = AT.key_of(ds.workouts[i].entry.start)

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        item = V.BulkItem(key=key, file="2025/0.fit")
        await V.patch_activities(V.BulkBody(items=[item], terrain_difficulty="technical"))
        await V.patch_activities(V.BulkBody(items=[item], group="solo"))
        lst = V.activities_list()
        a = {x["file"]: x for x in lst["activities"]}["2025/0.fit"]
        assert a["terrain_difficulty"] == "technical" and a["group"] == "solo"
        assert lst["terrain_tags"] == AT.TERRAINS and lst["group_tags"] == AT.GROUPS
        await V.patch_activities(V.BulkBody(items=[item], terrain_difficulty=None))
        a = {x["file"]: x for x in V.activities_list()["activities"]}["2025/0.fit"]
        assert a["terrain_difficulty"] is None and a["group"] == "solo"
        r = await V.patch_activity(i, {"terrain_difficulty": "scramble"})
        assert r["terrain_difficulty"] == "scramble"
        with pytest.raises(HTTPException) as e:
            await V.patch_activity(i, {"group": "team"})
        assert e.value.status_code == 400
        await eng.dispose()
    _run(_inner())
    monkeypatch.setattr(GH, "compute", lambda ds, tags=None: {i: {"suggest": "group", "hr_pct": 0.2, "vam_ratio": 0.8}})
    monkeypatch.setattr(TD, "levels", lambda ds, tags=None, **kw: {i: {"level": "technical", "src": "route"}})
    h = _run(V.activities_hints())
    assert h["group"] == {key: {"suggest": "group", "hr_pct": 0.2, "vam_ratio": 0.8}}
    assert h["terrain_route"] == {key: "technical"}
    monkeypatch.setattr(TD, "levels", lambda ds, tags=None, **kw: {i: {"level": "technical", "src": "user"}})
    assert _run(V.activities_hints())["terrain_route"] == {}      # only route defaults are suggestions


def test_device_sync_carries_the_marks_as_columns():
    from backend.db import changelog as CL
    row = NS(start_local="2026-10-18T07:00", source=None, file=None, distance_km=None, label=None,
             activity_type=None, activity_type_overridden=False, effort=None, effort_overridden=False,
             note=None, exclusion=None, name=None, tags_json=json.dumps(["北插", "技術路段多", "跟團"], ensure_ascii=False))
    v = CL.tag_values(row)
    assert v["tags"] == ["北插"] and v["terrain_difficulty"] == "technical" and v["group"] == "group"
    assert "terrain_difficulty" in CL.TAG_SET_COLS and "group" in CL.TAG_SET_COLS
    with pytest.raises(CL.Refused):
        CL._check_tag("技術路段多")


def test_export_carries_the_marks_with_the_tags_json():
    """The export's activity_overrides rows are the activity_tags columns, tags_json included."""
    from backend.engine import debug_view as DV
    from backend.engine.planning import Plan
    tags = [{"start_local": "2026-10-18T07:00", "tags_json": '["技術路段多", "跟團"]'}]
    out = DV.export_config({}, Plan(), [], tags, [], set())
    got = out["blocks"]["activity_overrides"]["tags"][0]["tags_json"]
    assert AT.terrain_of(json.loads(got)) == "technical" and AT.group_of(json.loads(got)) == "group"


# ---- the route default (訓練路線 › 我的路段) ----------------------------------------------------

def test_route_store_keeps_the_default_and_the_registry_backs_it_up(tmp_path):
    from backend import data_registry as R
    from backend.engine.routes import RouteStore
    st = RouteStore(tmp_path)
    st.set_terrain("r1", "scramble")
    st.set_terrain("r2", "technical", aliases=("old",))
    assert st.terrain() == {"r1": "scramble", "r2": "technical"}
    st.set_terrain("r1", None)
    assert st.terrain() == {"r2": "technical"}
    assert TD.route_defaults(st) == {"r2": "technical"}
    f = next(f for f in R.FILES if f.pattern == "routes/terrain.json")
    assert f.cls == R.USER and f.backup == R.ALWAYS


def test_mine_rows_carry_the_route_default_only_on_routes():
    from backend.engine import route_library as RL
    view = {"items": {"r1": {"id": "r1", "kind": "route", "n_activities": 3, "auto_name": "路線"},
                      "c1": {"id": "c1", "kind": "climb", "n_activities": 3, "auto_name": "爬坡"}}}
    rows = {r["id"]: r for r in RL.mine_rows(view, {}, {}, {"r1": "technical", "c1": "scramble"})}
    assert rows["r1"]["terrain"] == "technical" and "terrain" not in rows["c1"]


def test_route_terrain_api(tmp_path, monkeypatch):
    from backend.api import route_library as RLA
    from backend.api import routes as RA
    from backend.engine.routes import RouteStore
    st = RouteStore(tmp_path)
    monkeypatch.setattr(RA, "STORE", st)
    view = {"items": {"r1": {"id": "r1", "kind": "route", "efforts": [], "n_activities": 2, "auto_name": "路線"},
                      "c1": {"id": "c1", "kind": "climb", "efforts": [], "n_activities": 2, "auto_name": "爬坡"}},
            "aliases": {"old": "r1"}}
    monkeypatch.setattr(RA, "_index", lambda: view)
    assert _run(RLA.mine_terrain("r1", {"level": "technical"})) == {"id": "r1", "terrain": "technical"}
    assert st.terrain() == {"r1": "technical"}
    assert RLA._mine_terrain(view) == {"r1": "technical"}
    with pytest.raises(HTTPException) as e:
        _run(RLA.mine_terrain("c1", {"level": "technical"}))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        _run(RLA.mine_terrain("r1", {"level": "hard"}))
    _run(RLA.mine_terrain("r1", {"level": None}))
    assert st.terrain() == {}


# ---- the pages -----------------------------------------------------------------------------

def test_list_page_columns_filter_phone_fold_and_i18n():
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert '{ k: "grp", cls: "c-grp"' in page and '{ k: "tdiff", cls: "c-tdiff"' in page
    assert 'id="f-unmarked"' in page and "unmarkedOnly" in page
    assert ".c-grp, .c-tdiff { display: none; }" in page and "tr.mkrow { display: table-row; }" in page
    assert "/activities/hints" in page and 'class="mkc${s ? " sug" : ""}"' in page
    assert 'saveMark(a, "group"' in page and 'saveMark(a, "terrain_difficulty"' in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text(encoding="utf-8"))
        for k in ("col.grp", "col.tdiff", "grp.group", "grp.solo", "grp.sug_tip", "tdiff.route_opt", "f.unmarked",
                  "help.f_unmarked", "mk.tip", "f_tdiff", "help.tdiff", "tdiff.technical", "tdiff.scramble"):
            assert cat.get(k), (loc, k)
        # SP-693: the route default moved with 「我的路段」 to 路線 › 我跑過的 (routes.html)
        rl = json.loads((ROOT / "static" / "i18n" / loc / "routes.json").read_text(encoding="utf-8"))
        for k in ("tdiff.label", "tdiff.help", "tdiff.none", "tdiff.technical"):
            assert rl.get(k), (loc, k)
        rp = json.loads((ROOT / "static" / "i18n" / loc / "racepower.json").read_text(encoding="utf-8"))
        for k in ("tdiff.label", "tdiff.normal", "tdiff.technical", "tdiff.scramble", "tdiff.tip", "tdiff.how"):
            assert rp.get(k), (loc, k)
    zh = json.loads((ROOT / "static" / "i18n" / "zh-TW" / "activity.json").read_text(encoding="utf-8"))
    assert zh["col.tdiff"] == "地形難度" and "路線難度" not in json.dumps(zh, ensure_ascii=False)
    rl = (ROOT / "static" / "routes.html").read_text(encoding="utf-8")
    assert 'id="rt-tdiff"' in rl and "/terrain`" in rl
    rp = (ROOT / "static" / "racepower.html").read_text(encoding="utf-8")
    assert 'id="tdiff-row"' in rp and "terrain_difficulty: type === \"trail\"" in rp
