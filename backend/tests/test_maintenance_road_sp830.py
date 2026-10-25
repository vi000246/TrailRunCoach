"""
SP-830: a road runner (主要訓練項目 路跑, engine/primary_sport.py) rotates the 維持期 (SP-102) between 速度 and
閾值 — the 閾值 block's weekly session is the Zone 3 rung (flat), the 速度 block's Zone 5 flat reps, never an
uphill interval; a trail runner keeps 速度 / 爬坡. Synthetic data only.
"""
import datetime as dt
from datetime import date

from backend.engine import overview as O
from backend.engine import planning as P
from backend.engine import projection as PJ
from backend.engine import quality_gate as QG
from backend.tests.test_maintenance_sp102 import BOTH, _maint_week

RACE = P.Event(id="race", name="race", date="2026-12-20", priority="A", distance_km=50, climbing_m=3000)
M_START = date(2027, 1, 25) + dt.timedelta(weeks=P.MAINTAIN_AFTER_WEEKS)    # a Monday


def _z5_open(monkeypatch):
    monkeypatch.setattr(QG, "z5_track", lambda gate, monday=None, steps=None: {"open": True, "z3_ok": True,
                                                                               "aet_ok": True, "text": ""})


def _uphill(sessions) -> list:
    return [s for s in sessions if "上坡" in (s.get("title") or "") or s.get("hill")]


# ---- the rotation ---------------------------------------------------------------------------------

def test_road_rotates_speed_and_threshold():
    ph = P.auto_phases([RACE], date(2027, 1, 1), date(2028, 6, 1))
    weeks = [M_START + dt.timedelta(weeks=i) for i in range(12)]
    road = [P.maintain_week(ph, m, "road") for m in weeks]
    assert [w["focus"] for w in road] == ["speed"] * 4 + ["threshold"] * 4 + ["speed"] * 4
    assert [w["track"] for w in road[:8]] == ["z5"] * 4 + ["z3"] * 4
    t = road[4]
    assert t["label"] == "閾值" and "3 區的閾值課（平路）" in t["text"] and "上坡" not in t["text"]
    assert "之後換「速度」" in t["text"]
    assert "之後換「閾值」" in road[0]["text"] and "平路間歇" in road[0]["text"]
    assert all("上坡" not in w["text"] for w in road)
    # a trail runner (and no sport given): 速度 / 爬坡 as before
    for sport in ("trail", None):
        trail = [P.maintain_week(ph, m, sport)["focus"] for m in weeks]
        assert trail == ["speed"] * 4 + ["hill"] * 4 + ["speed"] * 4
    assert P.maintain_week(ph, weeks[4])["track"] == "z5"


def test_the_threshold_block_prefers_zone3_and_falls_back_to_zone5(monkeypatch):
    _z5_open(monkeypatch)
    mon = date(2027, 8, 9)
    picks = [QG.week_decision(BOTH, "base", "base", mon + dt.timedelta(weeks=i), prefer="z3")["track"]
             for i in range(4)]
    assert picks == ["z3"] * 4
    # 課表偏好 每週品質課 2: Zone 3 and Zone 5 both, so a session above VT2 stays
    two = QG.week_decision(BOTH, "base", "base", mon, n=2, prefer="z3")
    assert [it["track"] for it in two["items"]] == ["z3", "z5"]
    # Zone 3 not open: the Zone 5 session stands in
    locked = {**BOTH, "z3": {"open": False, "reason": "3 區還沒解鎖：x"}}
    assert QG.week_decision(locked, "base", "base", mon, prefer="z3")["track"] == "z5"


def test_why_says_speed_and_threshold_for_a_road_runner():
    assert "（速度、閾值）" in P.maintain_why("road") and "爬坡" not in P.maintain_why("road")
    assert "（速度、爬坡）" in P.maintain_why("trail") and P.maintain_why() == P.maintain_why("trail")


def test_status_and_overview_say_it_by_the_primary_sport(monkeypatch):
    from backend.engine import primary_sport as PSP
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY, _daily, _ds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    ds = _ds(_daily(), plan)
    for sport, want, not_want in (("road", "（速度、閾值）", "爬坡"), ("trail", "（速度、爬坡）", "閾值")):
        monkeypatch.setattr(PSP, "effective", lambda *a, _s=sport, **k: _s)
        st = Status(ds, plan, TODAY)
        st.phase = P.Phase("base", "2026-08-03", "2027-06-30", maintain_from="2026-08-03", note="x")
        st.kind = "base"
        assert want in st.i_phase().why and not_want not in st.i_phase().why
        assert want in st.to_dict()["phase"]["why"]
    # the sport can't be read: the trail wording, never an error
    monkeypatch.setattr(PSP, "effective", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert "（速度、爬坡）" in st.to_dict()["phase"]["why"]


# ---- the week plan --------------------------------------------------------------------------------

def _road_week(monkeypatch, block_start: date):
    """_maint_week with sport road, the 維持期 starting so that today's week is in the block wanted."""
    from backend.tests.test_quality_gate import TODAY
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    orig = O.maintain_context

    def ctx(status, phs, monday, sport=None):
        status.phase.maintain_from = block_start.isoformat()
        return orig(status, phs, monday, sport)
    monkeypatch.setattr(O, "maintain_context", ctx)
    return _maint_week(BOTH, sport="road"), mon


def test_road_week_plan_in_the_threshold_block(monkeypatch):
    _z5_open(monkeypatch)
    from backend.tests.test_quality_gate import TODAY
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    wp, _ = _road_week(monkeypatch, mon - dt.timedelta(weeks=4))       # week 5 of the 維持期: 閾值
    m = wp["maintain"]
    assert m["focus"] == "threshold" and m["track"] == "z3"
    assert any(n.get("src") == "maintain" and "閾值" in n["text"] for n in wp["notes"])
    q = [s for s in wp["sessions"] if s["kind"] == "quality" and s["id"] != "tech"]
    if wp["mode"] != "recovery_week":
        assert q and not q[0]["rung_key"].startswith(("v", "z5"))     # the Zone 3 rung, not Zone 5
    assert not _uphill(wp["sessions"])
    # the 「5 區、3 區先不排」 note is the Zone 5 block's only
    assert not any("3 區這段時間先不排" in (n.get("text") or "") for n in wp["notes"])


def test_road_week_plan_in_the_speed_block_is_flat(monkeypatch):
    _z5_open(monkeypatch)
    from backend.tests.test_quality_gate import TODAY
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    wp, _ = _road_week(monkeypatch, mon)                                # week 1: 速度
    assert wp["maintain"]["focus"] == "speed" and wp["maintain"]["track"] == "z5"
    q = [s for s in wp["sessions"] if s["kind"] == "quality" and s["id"] != "tech"]
    if wp["mode"] != "recovery_week":
        assert q and q[0]["rung_key"].startswith(("v", "z5"))
    assert not _uphill(wp["sessions"])


def test_no_zone3_in_the_threshold_block_says_why():
    assert "先排 5 區那一堂" in O.maintain_no_z3_note(True)
    assert "只排輕鬆跑和長跑" in O.maintain_no_z3_note(False)


# ---- the projected weeks --------------------------------------------------------------------------

def test_projected_road_weeks_rotate_speed_and_threshold_without_uphill(monkeypatch):
    _z5_open(monkeypatch)
    from backend.tests.test_quality_gate import TODAY
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    wp, _ = _road_week(monkeypatch, mon)
    assert wp.get("primary_sport") == "road"
    phases = [P.phase_json(P.Phase("base", (mon - dt.timedelta(weeks=1)).isoformat(), "2027-06-30",
                                   maintain_from=mon.isoformat(), note="沒有 A 賽事已超過 24 週"))]
    weeks = PJ.project_weeks(wp, phases, mon + dt.timedelta(weeks=8))
    assert len(weeks) >= 7
    texts = [n["text"] for w in weeks for n in w.get("notes") or () if n.get("src") == "maintain"]
    assert any("「閾值」" in t for t in texts) and not any("爬坡" in t or "上坡" in t for t in texts)
    zones = {}
    for w in weeks:
        assert not _uphill(w.get("sessions") or ()), w["start"]
        block = (P._d(w["start"]) - mon).days // 7 // P.MAINTAIN_BLOCK_WEEKS
        for s in w.get("sessions") or ():
            if s["kind"] == "quality" and s.get("rung_key") and w["mode"] != "recovery_week":
                zones.setdefault(block, set()).add("z5" if s["rung_key"].startswith(("v", "z5")) else "z3")
    assert zones.get(0) == {"z5"} and zones.get(1) == {"z3"}           # 速度 weeks 1–4, 閾值 weeks 5–8


# ---- English --------------------------------------------------------------------------------------

def test_the_new_texts_are_in_english():
    from backend.i18n import use_locale
    ph = P.auto_phases([RACE], date(2027, 1, 1), date(2028, 6, 1))
    with use_locale("en"):
        assert "(speed, threshold)" in P.maintain_why("road")
        t = P.maintain_week(ph, M_START + dt.timedelta(weeks=4), "road")["text"]
        assert t.startswith("Maintenance") and "zone 3 threshold" in t and "\"Threshold\"" in t
        assert O.maintain_no_z3_note(True).startswith("These maintenance weeks")
        assert O.maintain_no_z3_note(False).startswith("These maintenance weeks")
