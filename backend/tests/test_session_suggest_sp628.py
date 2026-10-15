"""SP-628: 新增課表 — picking a type fills in the session the automatic plan would give you
(engine/session_suggest.py, POST /sessions/suggest), not a fixed title read back into steps.
有氧間歇 = the Zone 3 ladder's next step, VO2max = the Zone 5 one (V1 marked 「還沒解鎖」 while the
track is closed: it never moves the ladder), 速度 = the best full speed template, 輕鬆跑 / LSD = the
generated week's. Synthetic only."""
from __future__ import annotations

import copy
from datetime import date

import pytest

from backend.engine import interval_library as IL
from backend.engine import plan_store as PS
from backend.engine import quality_gate as QG
from backend.engine import session_suggest as SS
from backend.engine import template_recs as TR

TH = {"cp": 260.0, "lthr": 165.0, "aet": 140.0}
GATE = {"state": "unlocked", "prefix": "",
        "dose": {"z3": {"step": 1, "done": 1, "met": 1}, "z5": {"step": 0, "done": 0, "met": 0}},
        "z5": {"open": False, "reason": "還沒有實測 AeT 通過"}}
GATE_OPEN = {"state": "unlocked", "prefix": "", "z3_recent": {"done": 4},
             "dose": {"z3": {"step": 2, "done": 3, "met": 2}, "z5": {"step": 1, "done": 1, "met": 1}},
             "z5": {"open": True, "label": "AeT 實測"}}


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

def test_aerobic_is_the_ladders_next_step():
    r = SS.ladder("aerobic", GATE, TH)
    s = r["session"]
    rung = QG.rung_now(GATE, "z3")
    assert r["locked"] is None and r["rung"] == rung
    key, _why = TR.ladder_pick(None, None, gate=GATE, track="z3")
    assert s["variant_key"] == key                  # the 插入範本 推薦 #1 = the generator's pick
    assert s["rung_key"] == rung and s["equiv"] is True and s["kind"] == "quality"
    assert "3×10" not in s["title"]


def test_the_default_aerobic_title_is_not_the_old_threshold_set():
    for t in (PS.DEFAULT_TITLES["quality"], PS.FAMILY_TITLES["aerobic"]):
        assert "3×10" not in t
    assert PS.FAMILY_TITLES["aerobic"] == "有氧間歇"


def test_vo2max_closed_gives_v1_marked_and_not_counted():
    r = SS.ladder("vo2max", GATE, TH)
    s = r["session"]
    assert s["variant_key"] == IL.canonical("z5a").key            # V1 5×2′
    assert IL.get(s["variant_key"]).n == 5 and IL.get(s["variant_key"]).work_s == 120
    assert s["equiv"] is False and s["progress"] is False and s["swap"] == SS.LOCKED
    assert r["locked"]["text"] == "還沒解鎖：手動加的，不算進階" and r["locked"]["why"]
    assert "還沒解鎖：手動加的，不算進階" in s["detail"]
    # no gate at all reads as closed, too
    assert SS.ladder("vo2max", {}, TH)["session"]["variant_key"] == IL.canonical("z5a").key


def test_vo2max_closed_still_v1_on_a_tight_day():
    s = SS.ladder("vo2max", GATE, TH, cap=25)["session"]
    assert IL.get(s["variant_key"]).rung == "z5a"


def test_vo2max_open_takes_the_zone5_rung():
    r = SS.ladder("vo2max", GATE_OPEN, TH)
    assert r["locked"] is None and r["rung"] == QG.rung_now(GATE_OPEN, "z5") == "z5b"
    assert r["session"]["equiv"] is True and r["session"]["swap"] == "user"


@pytest.mark.needs_core
def test_vo2max_under_personal_zones_is_recut_by_the_model():
    from backend.tests.test_personal_intervals_sp590 import CP, personal_pm
    th = {**TH, "cp": CP, "power_model": personal_pm()}
    s = SS.ladder("vo2max", GATE, th)["session"]
    want = IL.session_for(IL.fit("z5a", None), th)["variant_adj"]["opt"]
    assert s["variant_adj"]["opt"] == want
    assert s["variant_reps"] is None and "個人化間歇" in s["detail"]


def test_a_locked_row_never_moves_the_zone5_ladder():
    row = {"variant_key": "v1a", "rung_key": "z5a", "swap": SS.LOCKED, "equiv": False, "date": "2026-10-01",
           "bouts": [{"power": 290.0, "duration_s": 120}] * 5, "cp": 260.0}
    assert QG.row_track(row) is None
    d5 = QG.dose_step([copy.deepcopy(row)], 140.0, "z5")
    assert d5["step"] == 0 and d5["done"] == 0
    # the same row, not locked: on the Zone 5 track as before
    assert QG.row_track({**row, "swap": "user"}) == "z5"


def test_speed_is_a_full_session_by_terrain_and_phase():
    from backend.engine import workout_steps as WS
    tpl = WS.templates()
    assert SS.speed(tpl, terrain="road")["row"]["key"] == "lib:daniels_r"
    assert SS.speed(tpl, terrain="trail")["row"]["key"] == "lib:ua_hill_sprints"
    assert SS.speed(tpl, terrain="trail", phase="taper")["row"]["key"] == "lib:daniels_r"   # no short hill work


def test_easy_and_long_from_the_generated_week():
    from backend.tests.test_plan_store import g, inputs, next_week
    inp = inputs(weeks=[next_week(), {**next_week(provisional=True), "start": "2026-10-12", "sessions": [
        g("long", "long", "LSD（山路）", 140, "2026-10-18")]}])
    assert SS.easy("long", inp, "2026-10-18")["minutes"] == 140
    assert SS.easy("easy", inp, "2026-10-07")["minutes"] == 50
    assert SS.easy("easy", inp, "2026-10-02")["minutes"] == 40          # this week's first
    assert SS.easy("easy", inp, "2026-10-14")["minutes"] == 40          # a week without one: this week's
    assert SS.easy("long", inp, "2026-10-04")["title"] == "LSD（山路）"
    assert SS.mountain_of(inp) is True


# ---------------------------------------------------------------------------
# the API: suggest, the structure, save, push
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    from backend.api import plan_sessions
    from backend.sync import coros_workouts as CW
    from backend.tests.test_plan_store import Env
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(plan_sessions, "_tpace", lambda: 280.0)
    monkeypatch.setattr(plan_sessions, "_speeds", lambda: {"v_easy": 10.0, "v_easy_src": "t", "ep_kmh": 7.0})
    with Env(monkeypatch) as e:
        e.inp["cur"]["quality_gate"] = copy.deepcopy(GATE)
        yield e


def _api():
    from backend.tests.test_plan_store import API
    return API


def _suggest(e, **kw):
    r = e.c.post(f"{_api()}/sessions/suggest", json={"day": "2026-10-03", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _body(sug: dict, family: str) -> dict:
    v = sug["variant"]
    return {"day": "2026-10-03", "kind": "quality", "family": family, "title": sug["title"],
            "minutes": sug["minutes"], "target": sug["target"], "detail": sug["detail"],
            "variant_key": v["key"], "suggest": family}


def test_api_aerobic_suggest_structure_save_and_push(env):
    e = env
    sug = _suggest(e, kind="quality", family="aerobic")
    v = sug["variant"]
    assert sug["locked"] is None and v["key"] in {x.key for x in IL.LIBRARY[QG.rung_now(GATE, "z3")]}
    assert "3×10" not in sug["title"] and sug["minutes"] > 0
    # the structure: the variant's own (not the title's), its work steps on 自動 (target_policy by type)
    d = e.c.post(f"{_api()}/steps/derive", json={"kind": "quality", "title": sug["title"], "minutes": sug["minutes"],
                                                 "variant_key": v["key"], "variant_reps": v["reps"],
                                                 "variant_blocks": v["blocks"], "variant_adj": v["adj"]}).json()
    assert d["steps"] and d["steps"]["origin"] == "derived"
    chk = e.c.post(f"{_api()}/steps/check", json={"kind": "quality", "steps": d["steps"]}).json()
    works = [o["id"] for o in chk["order"] if o["kind"] == "work"]
    assert works and {chk["resolved"][i]["type"] for i in works} == {"power"}   # a road interval: power
    r = e.c.post(f"{_api()}/sessions", json=_body(sug, "aerobic"))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["variant_key"] == v["key"] and s["rung_key"] == QG.rung_now(GATE, "z3")
    assert s["equiv"] is True and s["swap"] == "user" and s["kind"] == "quality"
    pv = e.c.get(f"{_api()}/sessions/{s['uid']}/coros-preview").json()
    assert pv["pushed"] and pv["lines"]


def test_api_vo2max_closed_is_v1_marked_and_saved_as_not_counted(env):
    e = env
    sug = _suggest(e, kind="quality", family="vo2max")
    assert sug["variant"]["key"] == IL.canonical("z5a").key
    assert sug["locked"]["text"] == "還沒解鎖：手動加的，不算進階"
    # the editor says nothing about progression for it (no 「這堂算進階」)
    v = sug["variant"]
    d = e.c.post(f"{_api()}/steps/derive", json={"kind": "quality", "minutes": sug["minutes"], "variant_key": v["key"],
                                                 "variant_blocks": v["blocks"], "variant_adj": v["adj"]}).json()
    chk = e.c.post(f"{_api()}/steps/check", json={"kind": "quality", "variant_key": v["key"], "steps": d["steps"]}).json()
    assert not chk.get("equiv") and "算進階" not in str(chk)
    s = e.c.post(f"{_api()}/sessions", json=_body(sug, "vo2max")).json()
    assert s["variant_key"] == "v1a" and s["equiv"] is False and s["swap"] == SS.LOCKED
    chk2 = e.c.post(f"{_api()}/steps/check", json={"uid": s["uid"], "steps": d["steps"]}).json()
    assert "算進階" not in str(chk2)
    assert s["kind"] == "quality"                    # still a 強度課: budget and 48 h count it
    # editing its text later keeps the mark
    s2 = e.c.patch(f"{_api()}/sessions/{s['uid']}", json={"title": "我的 VO2max"}).json()
    assert s2["swap"] == SS.LOCKED and s2["equiv"] is False
    # a Zone 5 variant inserted by hand (插入範本) while closed: the same mark
    t = e.c.post(f"{_api()}/sessions", json={"day": "2026-10-03", "kind": "quality", "variant_key": "v2a"}).json()
    assert t["equiv"] is False and t["swap"] == SS.LOCKED


def test_api_vo2max_open_counts(env):
    e = env
    e.inp["cur"]["quality_gate"] = copy.deepcopy(GATE_OPEN)
    sug = _suggest(e, kind="quality", family="vo2max")
    assert sug["locked"] is None and IL.get(sug["variant"]["key"]).rung == "z5b"
    s = e.c.post(f"{_api()}/sessions", json=_body(sug, "vo2max")).json()
    assert s["equiv"] is True and s["swap"] == "user" and s["rung_key"] == "z5b"
    d = e.c.post(f"{_api()}/steps/derive", json={"uid": s["uid"]}).json()
    chk = e.c.post(f"{_api()}/steps/check", json={"uid": s["uid"], "steps": d["steps"]}).json()
    assert "算進階" in str(chk)                   # an open track: the progression note as before


def test_api_speed_and_easy(env):
    e = env
    sp = _suggest(e, kind="quality", family="speed", terrain="road")
    assert sp["template_key"] == "lib:daniels_r" and sp["variant"] is None and sp["title"]
    lg = _suggest(e, kind="long", day="2026-10-18")
    assert lg["minutes"] == 140 and lg["title"] == "LSD（山路）" and lg["variant"] is None
    ez = _suggest(e, kind="easy", day="2026-10-07")
    assert ez["minutes"] == 50
    assert _suggest(e, kind="strength")["minutes"] is None              # nothing to suggest
