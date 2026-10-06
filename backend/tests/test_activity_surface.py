"""路況 mark of an activity and the dry / wet trail technicality (SP-250;
docs/research/wet-muddy-terrain.md §5 #2, §6): 乾 / 濕 / 未標 stored as one
of two free-form tags like the pole mark, never detected; the technicality
factor splits only when both marked groups have ≥ 30 windows, and the
calculator offers 路況 only then and only when the back-test kept it.
tmp / in-memory DBs only."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pytest import approx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT
from backend.engine.racepower import backtest as BT
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL

ROOT = Path(__file__).resolve().parents[1]
DRY, WET = AT.SURFACES["dry"], AT.SURFACES["wet"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- the tag helpers / store ----------------------------------------------------

def test_surface_helpers_and_validate():
    assert AT.SURFACES == {"dry": "乾路", "wet": "濕路"}
    assert AT.surface_of([]) is None and AT.surface_of(["雨天", "泥濘"]) is None   # synonyms do not count
    assert AT.surface_of(["冬訓", WET]) == "wet" and AT.surface_of([DRY, WET]) == "wet"
    assert AT.with_surface(["a", DRY], "wet") == ["a", WET]
    assert AT.with_surface(["a", DRY, WET], None) == ["a"]
    assert AT.exclusive_surface(["a", DRY, "b", WET]) == ["a", "b", WET]
    assert AT.validate(surface="dry") is None and AT.validate(surface="muddy") == "INVALID_SURFACE"


def test_upsert_surface_next_to_the_pole_mark(tmp_path):
    db = tmp_path / "t.db"
    k = "2025-05-17T09:36"
    AT.upsert(db, start_local=k, tags=["冬訓"], poles="with")
    AT.upsert(db, start_local=k, surface="wet")
    r = AT.load(db)[0]
    assert r["tags"] == ["冬訓", AT.POLES["with"], WET]
    AT.upsert(db, start_local=k, surface="dry")                          # switch: never both
    assert AT.load(db)[0]["tags"] == ["冬訓", AT.POLES["with"], DRY]
    AT.upsert(db, start_local=k, tags=["冬訓", DRY, WET])                 # both sent: one kept
    assert AT.load(db)[0]["tags"] == ["冬訓", WET]
    AT.upsert(db, start_local=k, surface=None)                           # 未標
    assert AT.load(db)[0]["tags"] == ["冬訓"]
    with pytest.raises(ValueError):
        AT.upsert(db, start_local=k, surface="mud")
    assert AT.merge({"activity_type": "training", "effort": "easy"}, {"tags_json": f'["{WET}"]'})["surface"] == "wet"
    assert AT.merge({"activity_type": "training", "effort": "easy"}, None)["surface"] is None   # no auto rule


@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def test_api_save_list_and_bulk(tmp_path, no_plan, monkeypatch):
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    i = next(w.idx for w in ds.workouts if w.entry.file == "2025/0.fit")

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(D, "AsyncSessionLocal", maker)
        r = await V.patch_activity(i, {"tags": ["冬訓"]})
        assert r["surface"] is None
        r = await V.patch_activity(i, {"surface": "wet", "poles": "without"})
        assert r["surface"] == "wet" and r["poles"] == "without" and r["tags"] == ["冬訓", AT.POLES["without"], WET]
        lst = V.activities_list()
        a = {x["file"]: x for x in lst["activities"]}
        assert a["2025/0.fit"]["surface"] == "wet" and a["2025/1.fit"]["surface"] is None
        assert lst["surface_tags"] == AT.SURFACES
        with pytest.raises(HTTPException) as e:
            await V.patch_activity(i, {"surface": "mud"})
        assert e.value.status_code == 400
        item = V.BulkItem(key="2025-12-14T01:00", file="2025/1.fit")
        await V.patch_activities(V.BulkBody(items=[item], surface="dry"))
        assert {x["file"]: x for x in V.activities_list()["activities"]}["2025/1.fit"]["surface"] == "dry"
        await eng.dispose()
    _run(_inner())


def test_workout_files_activity_update_carries_surface():
    from backend.api.workouts import ActivityUpdate, update_activity
    from backend.tests.test_activity_tags import _session

    async def _inner():
        s = await _session()
        r = await update_activity(10, ActivityUpdate(surface="dry"), s)
        assert r["surface"] == "dry" and r["tags"] == [DRY]
        r = await update_activity(10, ActivityUpdate(surface=None), s)
        assert r["surface"] is None and r["tags"] == []
    _run(_inner())


# ---- the dry / wet technicality --------------------------------------------------

def _samples(n_dry, n_wet, n_unmarked=40, wet_ratio=0.80, dry_ratio=1.0):
    """Trail running windows on a −5 % grade (RE = ratio × the Minetti prior at RE_flat 1),
    activity 1 = dry, 2 = wet, 3 = unmarked."""
    prior = GM.re_prior(-0.05, 1.0)
    out = []
    for a, n, r in ((1, n_dry, dry_ratio), (2, n_wet, wet_ratio), (3, n_unmarked, 0.95)):
        out += [{"g": -0.05, "re": prior * r, "v": 3.0, "a": a, "run": 1.0, "trail": True} for _ in range(n)]
    return out


SURF = {1: "dry", 2: "wet"}


def test_no_split_below_30_windows_in_either_group():
    for nd, nw in ((29, 100), (100, 29), (0, 0)):
        g = GM.fit_gait_re(_samples(nd, nw), 1.0, surfaces=SURF)
        assert not g.surface_split and g.tech_surface["n"] == {"dry": nd, "wet": nw}
        t = g.for_trail()
        assert t.for_surface("wet").re(-0.05) == t.re(-0.05)          # behaviour unchanged
        assert t.for_surface("wet").surface is None
    # unmarked activities never enter a group
    g = GM.fit_gait_re(_samples(0, 0, n_unmarked=200), 1.0, surfaces={})
    assert g.tech_surface["n"] == {"dry": 0, "wet": 0}


def test_split_with_30_windows_each_and_the_shrinkage_toward_the_pooled_bin():
    g = GM.fit_gait_re(_samples(30, 30), 1.0, surfaces=SURF)
    assert g.surface_split
    lab = GM.tech_bin(-0.05)
    pooled = g.tech_bins[lab]["f"]
    wet, dry = g.tech_surface["wet"][lab], g.tech_surface["dry"][lab]
    ref = GM.re_prior(-0.05, 1.0) / g.run.re(-0.05)                      # the ratio is against the fitted RE(g)
    assert wet["raw"] == approx(0.80 * ref) and dry["raw"] == approx(1.0 * ref) and wet["n"] == 30
    assert wet["f"] == approx((30 * wet["raw"] + 30 * pooled) / 60)   # n/(n + 30) toward the pooled bin
    assert dry["f"] == approx(min(GM.TECH_BOUNDS[1], (30 * dry["raw"] + 30 * pooled) / 60))
    t = g.for_trail()
    base = t.re(-0.05)
    assert t.tech_at(-0.05) == (approx(pooled), "bin")
    w = t.for_surface("wet")
    assert w.tech_at(-0.05) == (approx(wet["f"]), "bin_wet") and w.re(-0.05) < base
    assert t.for_surface("dry").re(-0.05) > base
    assert t.for_surface(None).re(-0.05) == base
    assert t.for_surface("wet").re(0.06) == t.re(0.06)                  # climbs (g > +2 %) untouched
    # a bin the wet group never ran: the pooled bin
    assert w.tech_at(-0.20)[1] != "bin_wet"
    j = g.to_json()["tech_surface"]
    assert j["split"] and j["n"] == {"dry": 30, "wet": 30} and j["min_n"] == 30
    off = g.without_surface_split("backtest")
    assert not off.surface_split and off.tech_surface["gate"] == "backtest"
    assert off.for_trail().for_surface("wet").re(-0.05) == base


def _gaitre(split: bool):
    g = GM.fit_gait_re([], 1.0)
    bins = {lab: {"f": 0.80, "raw": 0.80, "n": 50} for lab in GM.TECH_BIN_LABELS}
    g.tech_surface = {"split": split, "n": {"dry": 50, "wet": 50}, "min_n": 30,
                      "dry": {lab: {"f": 1.0, "raw": 1.0, "n": 50} for lab in GM.TECH_BIN_LABELS}, "wet": bins}
    return g


def test_planner_surface_choice_only_with_the_split():
    from backend.tests.test_racepower_v2 import fake_v1, synthetic_track
    tr = synthetic_track({"len": 12000, "z": lambda x: 200 + (x * 0.05 if x < 6000 else (12000 - x) * 0.05)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 12.0, c["totals"]["gain_m"])

    def plan(g, surface):
        return PL.plan_run(v1=v1, course=c, grade_re=g, opts={"mode": "power", "target_power": 250, "surface": surface},
                           validated={"trail": True}, effort_validated=False)
    off = _gaitre(False)
    base = plan(off, None)
    assert base["summary"]["surface"] is None                           # no split: no choice offered
    assert plan(off, "wet")["summary"]["time_s"] == base["summary"]["time_s"]   # and the choice is ignored
    on = _gaitre(True)
    none = plan(on, None)
    wet, dry = plan(on, "wet"), plan(on, "dry")
    assert none["summary"]["surface"]["available"] and none["summary"]["surface"]["used"] is None
    assert wet["summary"]["surface"]["used"] == "wet" and dry["summary"]["surface"]["used"] == "dry"
    assert wet["summary"]["time_s"] > dry["summary"]["time_s"]
    assert none["summary"]["time_s"] == base["summary"]["time_s"]        # no choice: as before


# ---- the back-test gate ------------------------------------------------------------

def _row(surface, segs, split=True):
    return {"category": "trail", "err_v2": 0.0, "surface": surface, "surface_split": split, "segments": segs}


def test_surface_split_summary_and_flag(tmp_path):
    seg = lambda cls, e, es: {"cls": cls, "err": e, "err_surf": es}       # noqa: E731
    better = [_row("wet", [seg("down", 0.20, 0.05), seg("up", 0.1, 0.1)]), _row("dry", [seg("steep_down", -0.1, -0.08)])]
    s = BT.surface_split_summary(better)
    assert s["cases"] == 2 and s["by_surface"] == {"dry": 1, "wet": 1}
    assert s["downhill"]["pooled"]["n"] == 2 and s["no_worse"]
    worse = [_row("wet", [seg("down", 0.05, 0.20)])]
    assert not BT.surface_split_summary(worse)["no_worse"]
    assert not BT.surface_split_summary([_row("wet", [seg("down", 0.1, 0.1)], split=False)])["no_worse"]   # no split
    assert not BT.surface_split_summary([])["no_worse"]
    p = tmp_path / "bt.json"
    assert BT.surface_split_flag(p) is False                             # nothing stored → not offered
    p.write_text(json.dumps({"terrain": {"surface_split": s}}), "utf-8")
    assert BT.surface_split_flag(p) is True
    p.write_text(json.dumps({"terrain": {"surface_split": BT.surface_split_summary(worse)}}), "utf-8")
    assert BT.surface_split_flag(p) is False


# ---- the pages -----------------------------------------------------------------------

def test_editor_choice_calculator_row_and_i18n():
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert 'data-surface="${v}"' in page and 'const SURFACE_OPTS = ["dry", "wet", ""]' in page
    assert page.index('T("f_poles")') < page.index('T("f_surface")') < page.index('T("f_tags")')
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text(encoding="utf-8"))
        for k in ("f_surface", "help.surface", "surface.dry", "surface.wet", "surface.none"):
            assert cat.get(k), (loc, k)
        rp = json.loads((ROOT / "static" / "i18n" / loc / "racepower.json").read_text(encoding="utf-8"))
        for k in ("surface.label", "surface.dry", "surface.wet", "surface.meta", "surface.meta_hr"):
            assert rp.get(k), (loc, k)
    zh = json.loads((ROOT / "static" / "i18n" / "zh-TW" / "activity.json").read_text(encoding="utf-8"))
    assert (zh["surface.dry"], zh["surface.wet"], zh["surface.none"]) == ("乾", "濕", "未標")
    rp = (ROOT / "static" / "racepower.html").read_text(encoding="utf-8")
    assert 'id="surface-row"' in rp and 'class="row hidden" id="surface-row"' in rp   # hidden until the plan offers it
    assert "PLAN?.summary?.surface" in rp


def test_surface_marks_reads_only_marked_runs():
    import datetime as dt
    from types import SimpleNamespace as NS
    from backend.engine.racepower import athlete as A
    t0 = dt.datetime(2025, 5, 17, 9, 36)
    runs = [NS(idx=i, entry=NS(start=t0 + dt.timedelta(hours=i), file=f"2025/{i}.fit")) for i in range(3)]
    tags = [{"start_local": AT.key_of(t0), "file": "2025/0.fit", "tags_json": f'["{WET}"]'},
            {"start_local": AT.key_of(t0 + dt.timedelta(hours=1)), "file": "2025/1.fit", "tags_json": '["雨天"]'},
            {"start_local": AT.key_of(t0 + dt.timedelta(hours=2)), "file": "2025/2.fit", "tags_json": f'["{DRY}"]'}]
    assert A.surface_marks(None, runs, tags) == {0: "wet", 2: "dry"}
    assert A.surface_marks(None, runs, []) == {}
