"""
SP-79: 強度課's three families (有氧間歇 / VO2max 間歇 / 速度) as first-class — the generator's
titles, the old titles mapped on read, the stored `family` the editor sets (derived from the
steps when unset), the calendar / compliance split, and the push / feed names.
"""
import pytest
from sqlalchemy import select

from backend.db.models import PlanSession
from backend.engine import calendar_feed as CF
from backend.engine import compliance as C
from backend.engine import interval_library as IL
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import plan_store as PS
from backend.engine import quality_gate as QG
from backend.engine import reconcile as R
from backend.engine import workout_templates as WT
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import run
from backend.tests.test_plan_store import API, Env, _pin_real_today  # noqa: F401 — today = 2026-09-30

TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
PREFIX = {("aerobic", "tempo"): "有氧間歇", ("aerobic", "cruise"): "有氧間歇（巡航）",
          ("aerobic", "supra"): "有氧間歇（超閾值）", ("vo2max", None): "VO2max 間歇",
          ("vo2max", "short"): "VO2max 間歇", ("speed", None): "速度"}


def _old(v) -> str:
    return f"{IL.CLASS_LABEL[v.cls]} {IL.structure(v)}" + ("上坡" if v.terrain == "hill" else "")


def _every_variant():
    for v0 in list(IL.ALL.values()):
        for r in range(1, v0.n + 1):
            yield IL.with_reps(v0, r)


# ---- titles --------------------------------------------------------------------------------

def test_library_titles_name_the_family_the_classifier_reads():
    for v in _every_variant():
        f = WT.family_of_variant(v)
        assert IL.title(v).startswith(PREFIX[(f["id"], f["sub"])]), (v.key, v.n, IL.title(v), f)
        assert IL.renamed(_old(v)) == IL.title(v)            # a stored pre-SP-79 title reads the same
        assert IL.renamed(IL.title(v)) == IL.title(v)        # and today's is left alone
    assert IL.title(IL.canonical("a1")) == "有氧間歇 2×15 分"
    assert IL.title(IL.canonical("z3b")) == "有氧間歇（巡航）3×8 分"
    assert IL.title(IL.canonical("z5b")) == "VO2max 間歇 4×3 分"
    assert IL.title(IL.get("a4b")) == "有氧間歇 連續 30 分上坡"
    # 「N×M 分」 stays: the text parsers (workout_steps / coros_workouts / plan_prefs) still read it
    assert "3×8 分" in IL.title(IL.canonical("z3b"))


@pytest.mark.parametrize("old,new", [
    ("閾值 3×10 分", "有氧間歇（巡航）3×10 分"),
    ("閾值 3×8 分（平路）", "有氧間歇（巡航）3×8 分（平路）"),
    ("閾值 連續 30 分上坡", "有氧間歇 連續 30 分上坡"),
    ("近閾值 3×7 分", "有氧間歇（巡航）3×7 分"),
    ("閾值節奏 2×15 分（平路）", "有氧間歇 2×15 分（平路）"),
    ("節奏 2×8 分", "有氧間歇（巡航）2×8 分"),
    ("爬坡間歇 5×4 分", "VO2max 間歇 5×4 分上坡"),
    ("間歇 5×4 分（平路）", "VO2max 間歇 5×4 分（平路）"),
    ("VO2max 6×2:30", "VO2max 間歇 6×2:30"),
    ("閾值 3×6 分（只排閾值）", QG.SUB[1]),
    # not an old auto title: as written
    ("閾值下 3×8 分", "閾值下 3×8 分"), ("VO2max 間歇 4×4 分", "VO2max 間歇 4×4 分"),
    ("輕鬆跑", "輕鬆跑"), ("我的節奏跑", "我的節奏跑"), ("短強度喚醒", "短強度喚醒"),
    # the taper's short session, renamed 2026-10-05 (intensity unchanged)
    ("短強度 4×3 分", "有氧間歇（巡航）4×3 分"),
])
def test_renamed(old, new):
    assert IL.renamed(old) == new


def test_fixed_sessions_are_named_by_their_family():
    for s, fam in ((O.ROAD_SPECIFIC_Q, "aerobic"), (O.TRAIL_SPECIFIC_Z5, "vo2max"), (O.TAPER_Z3, "aerobic"),
                   (O.TAPER_Q, "aerobic")):
        assert WT.session_family(s, TH)["id"] == fam
        assert s["title"].startswith(PREFIX[(fam, WT.session_family(s, TH)["sub"])])
    assert not any("閾值" in s["title"] for s in (O.ROAD_SPECIFIC_Q, O.TRAIL_SPECIFIC_Z5, O.TAPER_Z3))
    assert O.TAPER_Q["title"] == "有氧間歇（巡航）4×3 分" and "98–102% CP" in O.TAPER_Q["detail"]
    assert PS.DEFAULT_TITLES["quality"] == "有氧間歇（巡航）3×10 分"


def test_flat_terrain_turns_the_new_hill_title_flat():
    s = dict(O.TRAIL_SPECIFIC_Z5)
    PP._quality_terrain(s, PP.Prefs(terrain_quality="flat"))
    assert s["title"] == "VO2max 間歇 5×4 分（平路）" and s["terrain"] == "road"
    h = dict(O.TRAIL_SPECIFIC_Z5)
    PP._quality_terrain(h, PP.Prefs(terrain_quality="hill"))
    assert h["title"] == "VO2max 間歇 5×4 分上坡"           # already uphill: unchanged
    old = {**O.TRAIL_SPECIFIC_Z5, "title": "爬坡間歇 5×4 分"}
    PP._quality_terrain(old, PP.Prefs(terrain_quality="flat"))
    assert old["title"] == "間歇 5×4 分（平路）"             # a stored older row: as before


def test_ladder_matchers_read_old_and_new_titles_alike():
    cruise = QG.CRUISE[1]                                     # T2 3×8′
    for t in ("閾值 3×8 分", "有氧間歇（巡航）3×8 分"):
        assert QG.spec_by_title(t) is cruise
        assert QG.planned_spec(t, 0, "z3") == (cruise, True)
    # the old ladder's titles stay neutral in both spellings (「閾值 3×10 分」 is also A1's a1b)
    for t in ("閾值 3×10 分", "有氧間歇（巡航）3×10 分", "VO2max 間歇 4×4 分", "爬坡間歇 4×3 分"):
        assert QG.planned_spec(t, 0, "z3")[1] is True, t
    # 「VO2max 間歇 4×4 分」 is V4's title today but an old-ladder row's title stays unknown
    assert QG.spec_by_title("VO2max 間歇 4×4 分") is None
    assert QG.spec_by_title("VO2max 4×4 分") is QG.Z5[3]
    for t in ("閾值 3×6 分（只排閾值）", QG.SUB[1]):
        assert QG.planned_spec(t, 0, "z3") == (QG.SUB, True)


def test_a_renamed_generated_title_is_not_a_change():
    stored = R.session_from_gen({"id": "q", "kind": "quality", "title": "有氧間歇（巡航）3×10 分", "minutes": 60,
                                 "day": "2026-10-06"}, "2026-10-05", False, "u1")
    gen = {"id": "q", "kind": "quality", "title": "閾值 3×10 分", "minutes": 60, "day": "2026-10-06",
           "target": None, "detail": None, "source": None, "tss": None}
    out, changes = R.reconcile([stored], [{"start": "2026-10-05", "sessions": [gen]}], [], "2026-10-01")[:2]
    assert not [c for c in changes if c["action"] == "changed"]
    assert next(s for s in out if s["uid"] == "u1")["title"] == "有氧間歇（巡航）3×10 分"


def test_the_renamed_taper_session_is_not_a_change_and_parses_the_same():
    # a stored taper row (read through to_dict → display_title, as plan_store feeds reconcile)
    d = PS.to_dict(_row(title="短強度 4×3 分", gen_key="quality", minutes=45, detail=O.TAPER_Q["detail"],
                        source=O.TAPER_Q["source"], tss=O.TAPER_Q["tss"]))
    assert d["title"] == "有氧間歇（巡航）4×3 分"
    gen = {**O.TAPER_Q, "target": "", "day": "2026-10-06"}
    changes = R.reconcile([d], [{"start": "2026-10-05", "sessions": [gen]}], [], "2026-10-01")[1]
    assert not [c for c in changes if c["action"] in ("changed", "added", "removed")], changes
    # the 「N×M 分」 text parser reads both titles alike: 4 reps of 3 min at 98–102 % CP
    for t in ("短強度 4×3 分", O.TAPER_Q["title"]):
        st = CW.session_workout({"id": "u1", "key": "u1", "kind": "quality", "title": t, "minutes": 45,
                                 "target": "", "detail": O.TAPER_Q["detail"], "source": "", "day": "2026-10-06",
                                 "done": False, "basis": "power"}, TH)
        assert st.payload["estimatedTime"] > 0


# ---- the stored family --------------------------------------------------------------------

def _row(**kw) -> PlanSession:
    d = {"uid": "u1", "week_start": "2026-10-05", "day": "2026-10-06", "kind": "quality", "title": "閾值 3×8 分",
         "minutes": 60, "origin": "auto", "edited": False, "provisional": False, "state": "active", **kw}
    return PlanSession(athlete_id=1, **d)


def test_to_dict_maps_the_title_and_carries_the_family():
    d = PS.to_dict(_row())
    assert d["title"] == "有氧間歇（巡航）3×8 分" and d["family"] is None
    assert PS.to_dict(_row(family="vo2max"))["family"] == "vo2max"
    assert PS.to_dict(_row(kind="easy", title="節奏 2×8 分"))["title"] == "節奏 2×8 分"    # only a 強度課's


def test_clean_validates_the_family():
    assert PS._clean({"family": "speed"}, "2026-10-01") == {"family": "speed"}
    assert PS._clean({"family": "auto"}, "2026-10-01") == {"family": None}
    with pytest.raises(PS.PlanError):
        PS._clean({"family": "anaerobic"}, "2026-10-01")


def test_session_family_prefers_the_stored_one_and_derives_otherwise():
    s = {"kind": "quality", "title": "閾值 3×8 分", "minutes": 60, "detail": "休 2 分；暖身 15 分、緩和 10 分",
         "target": "功率 270–285 W"}
    got = WT.session_family(s, TH)
    assert (got["id"], got["sub"]) == ("aerobic", "cruise")               # old row, no family: derived
    assert WT.session_family({**s, "family": "aerobic"}, TH) == got        # agrees: keeps the sub-type
    own = WT.session_family({**s, "family": "speed"}, TH)
    assert own["id"] == "speed" and own["sub"] is None and own["text"] == "速度"
    assert WT.steps_family({**s, "family": "speed"}, TH)["id"] == "aerobic"    # what the steps read as
    assert WT.session_family({"kind": "easy", "family": "speed"}, TH) is None


def test_api_family_edit_view_and_kind_change(monkeypatch):
    with Env(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        q = next(s for s in ss if s["kind"] == "quality")
        assert q["title"] == "有氧間歇（巡航）3×10 分" and q["family"] is None
        assert q["quality_family"]["id"] == "aerobic" and q["steps_family"]["id"] == "aerobic"
        r = e.c.patch(f"{API}/sessions/{q['uid']}", json={"family": "vo2max"})
        assert r.status_code == 200 and r.json()["family"] == "vo2max" and r.json()["edited"]
        v = next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["uid"] == q["uid"])
        assert v["quality_family"]["id"] == "vo2max" and v["steps_family"]["id"] == "aerobic"   # → the hint
        assert e.c.patch(f"{API}/sessions/{q['uid']}", json={"family": "nope"}).status_code == 400
        r = e.c.patch(f"{API}/sessions/{q['uid']}", json={"kind": "easy"})
        assert r.json()["family"] is None                                  # only a 強度課 has one
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "quality", "family": "speed",
                                              "title": "速度", "minutes": 40})
        assert r.status_code == 200 and r.json()["family"] == "speed"
        cal = e.c.get(f"{API}/calendar", params={"start": "2026-09-28", "end": "2026-10-11"}).json()
        assert cal["family_titles"] == PS.FAMILY_TITLES
        new = next(s for s in cal["sessions"] if s["uid"] == r.json()["uid"])
        assert new["quality_family"]["id"] == "speed"


def test_steps_check_reads_the_family_of_the_steps(monkeypatch):
    work = {"kind": "work", "dur": {"type": "time", "value": 180},
            "target": {"type": "power", "mode": "pct", "lo": 1.06, "hi": 1.12}}
    items = [work, {"kind": "rest", "dur": {"type": "time", "value": 180}, "target": {"type": "auto", "intent": "open"}}, work]
    with Env(monkeypatch) as e:
        body = {"kind": "quality", "title": "x", "minutes": 60, "day": "2026-10-06",
                "steps": {"origin": "user", "items": items}}
        r = e.c.post(f"{API}/steps/check", json=body)
        assert r.status_code == 200 and r.json()["family"]["id"] == "vo2max"
        assert "family" not in e.c.post(f"{API}/steps/check", json={**body, "kind": "easy"}).json()


# ---- calendar / compliance ----------------------------------------------------------------

def _due(uid, fam, state="done", tss=50.0):
    s = {"uid": uid, "day": "2026-09-29", "kind": "quality", "title": "x", "minutes": 60, "tss": tss, "state": state,
         "quality_family": WT.label(fam) if fam else None,
         "done_by": {"tss": tss, "moving_s": 3600} if state == "done" else None,
         "compliance": {"level": "green", "pct": 100} if state == "done" else None}
    return s


def test_compliance_splits_quality_by_family():
    ss = [_due("a", "aerobic"), _due("b", "aerobic", "missed"), _due("c", "vo2max"), _due("d", None),
          {**_due("e", None), "kind": "easy"}]
    out = C.dashboard(ss, [], "2026-10-01", "2026-09-28", "2026-10-04")
    rows = {(k["kind"], k.get("family")): k for k in out["by_kind"]}
    assert (rows[("quality", "aerobic")]["due"], rows[("quality", "aerobic")]["completed"]) == (2, 1)
    assert rows[("quality", "vo2max")]["completed"] == 1 and rows[("quality", "vo2max")]["planned_tss"] == 50.0
    assert rows[("quality", None)]["due"] == 1                          # no interval work: 強度課 as before
    assert ("quality", "speed") not in rows                              # none planned: no row
    order = [(k["kind"], k.get("family")) for k in out["by_kind"]]
    assert order.index(("quality", "aerobic")) < order.index(("quality", "vo2max")) < order.index(("quality", None))
    assert next(r for r in out["sessions"] if r["uid"] == "c")["family"] == "vo2max"


# ---- the watch and the calendar feed --------------------------------------------------------

def test_push_name_and_feed_summary_use_todays_title():
    d = PS.to_dict(_row(title="閾值 3×8 分", detail="休 2 分；暖身 15 分、緩和 10 分", day="2026-10-06"))
    s = {"id": "u1", "key": "u1", "kind": "quality", "title": d["title"], "minutes": 60, "target": "",
         "detail": d["detail"], "source": "", "day": "2026-10-06", "done": False, "basis": "power"}
    spec = CW.session_workout(s, TH)
    assert spec.name.startswith(f"{CW.NAME_PREFIX} 有氧間歇（巡航）3×8 分")
    # the name is in the fingerprint: a session pushed under its old name is 需更新 once, then stable
    old = CW.session_workout({**s, "title": "閾值 3×8 分"}, TH)
    assert old.fingerprint != spec.fingerprint
    assert CW.session_workout(dict(s), TH).fingerprint == spec.fingerprint
    assert CF.summary(d) == "有氧間歇（巡航）3×8 分"


def test_stored_old_title_reaches_the_watch_renamed(monkeypatch):
    with Env(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        q = next(s for s in ss if s["kind"] == "quality")

        async def legacy():
            r = (await e.db.execute(select(PlanSession).where(PlanSession.uid == q["uid"]))).scalar_one()
            r.title = "閾值 3×10 分"                             # as stored before SP-79
            await e.db.commit()
        run(legacy())
        v = next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["uid"] == q["uid"])
        assert v["title"] == "有氧間歇（巡航）3×10 分"
        e.c.post(f"{API}/push-coros?scope=day&day={q['day']}")
        assert any(p["name"].startswith(f"{CW.NAME_PREFIX} 有氧間歇（巡航）3×10 分") for p in e.fake.programs.values())
        assert not any("閾值" in p["name"] for p in e.fake.programs.values())
