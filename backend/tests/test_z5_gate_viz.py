"""The Zone 5 opening process, replayed (quality_gate.z5_history) and shown on
the overview card (quality_gate.z5_card) and the 基礎期 season chart (custom
view kind "z5gate"). Synthetic data only — never the WKO5 folder, the app DB,
the user's plan or COROS."""
import datetime as dt
from datetime import date

import pytest

from backend.engine import base_check as BC
from backend.engine import interval_library as IL
from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.engine.wko5expr.customviews import CustomViewError, parse_view
from backend.tests.test_base_check_reentry import _xu_workout
from backend.tests.test_quality_gate import BASE, GOOD_BY, TODAY, _ds, _run


def _easy(days):
    return [_run(TODAY - dt.timedelta(days=d), minutes=52, hr=135.0, power=180.0) for d in days]


def _with_break(last: date, back: date, n=60):
    """Easy runs every 2 days for `n` days, plus `last` and `back`, none in between."""
    ago = {d for d in range(2, n + 1, 2)} | {(TODAY - last).days, (TODAY - back).days}
    return _easy(sorted(d for d in ago if not last < TODAY - dt.timedelta(days=d) < back))


def _states(h):
    """The distinct states in order (consecutive repeats folded)."""
    out = []
    for r in h["days"]:
        if not out or out[-1] != r["state"]:
            out.append(r["state"])
    return out


def _gate_z5(ds):
    return QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)["z5"]


@pytest.fixture(autouse=True)
def _no_plan_db(monkeypatch):
    # dose_history reads the stored plan's titles; keep the tests off the app DB
    from backend.engine import plan_store
    monkeypatch.setattr(plan_store, "done_titles", lambda: {})


# ---------------------------------------------------------------------------
# the replay agrees with the planner
# ---------------------------------------------------------------------------

def _aet_plan(day, aethr=150, lthr=160, note=""):
    """A tested AeT + a measured LTHR on `day` (gap 6.7 % ≤ 10 %: the Zone 5 gate's UA path)."""
    from backend.tests.test_quality_gate import _plan
    return _plan(aethr=aethr, lthr=lthr, day=day.isoformat(), note=note)


def test_replay_opens_on_the_measured_aet_not_on_the_90_min_run():
    # SP-39: the 90-min run passes (a Zone 3 test) but Zone 5 opens only on the measured AeT + LTHR
    xu_day, aet_day = TODAY - dt.timedelta(days=15), TODAY - dt.timedelta(days=6)
    plan = _aet_plan(aet_day)
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(xu_day)], plan)
    h = QG.z5_history(ds, plan, TODAY - dt.timedelta(days=20), TODAY, PP.Prefs())
    by = {r["date"]: r for r in h["days"]}
    assert by[xu_day.isoformat()]["state"] == "unconfirmed"
    assert by[(aet_day - dt.timedelta(days=1)).isoformat()]["state"] == "unconfirmed"
    on = by[aet_day.isoformat()]
    assert on["state"] == "confirmed" and on["path"] == "aet_ua_gap" and on["since"] == aet_day.isoformat()
    assert _states(h) == ["unconfirmed", "confirmed"]
    # the last replayed day is exactly what evaluate() (the planner) says today
    g = _gate_z5(ds)
    assert h["current"]["state"] == g["state"] and h["current"]["since"] == g["since"]
    assert h["current"]["text"] == g["text"] and h["days"][-1]["date"] == TODAY.isoformat()
    segs = h["segments"]
    assert segs[0]["start"] == h["begin"] and segs[-1]["end"] == (TODAY + dt.timedelta(days=1)).isoformat()
    assert all(a["end"] == b["start"] for a, b in zip(segs, segs[1:]))
    conf = [e for e in h["events"] if e["kind"] == "confirm"]
    assert conf == [{"date": aet_day.isoformat(), "kind": "confirm", "path": "aet_ua_gap",
                     "label": "確認有氧基礎（實測 AeT（UA 差距法））"}]
    xu = [e for e in h["events"] if e["kind"] == "xu_run"]
    assert len(xu) == 1 and xu[0]["ok"]                     # still listed (it opens Zone 3)
    assert h["target"] == [150.0, 210.0]
    wk = {w["monday"]: w for w in h["weeks"]}
    m = BC.monday(aet_day).isoformat()
    assert wk[m]["keep_min"] == pytest.approx(wk[m]["level_min"] * 2 / 3)
    assert wk[min(wk)]["keep_min"] is None


def test_an_estimated_aet_or_lthr_does_not_open_zone5():
    day = TODAY - dt.timedelta(days=6)
    for plan in (_aet_plan(day, note="AeT 自動估算"),                       # an applied AeT estimate
                 _aet_plan(day, note="LTHR 自動估算")):                     # an applied LTHR estimate
        ds = _ds(_easy(range(2, 40, 2)), plan)
        g = QG.evaluate(ds, plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
        assert g["z5"]["state"] == "unconfirmed" and not g["z5_gate"]["open"]
    # a tested AeT stays the gate's AeT when an estimate is applied after it
    plan = _aet_plan(TODAY - dt.timedelta(days=20))
    from backend.engine.planning import Threshold
    plan.thresholds.append(Threshold((TODAY - dt.timedelta(days=3)).isoformat(), aethr=148.0, note="AeT 自動估算"))
    g = QG.evaluate(_ds(_easy(range(2, 40, 2)), plan), plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    assert g["z5"]["state"] == "confirmed" and not g["aet"]["tested"]
    assert g["z5"]["aet_tested"]["value"] == 150.0


def test_replay_is_the_same_as_calling_the_gate_each_day():
    ds = _ds(_easy(range(2, 30, 2)) + [_xu_workout(TODAY - dt.timedelta(days=6))])
    h = QG.z5_history(ds, ds.plan, TODAY - dt.timedelta(days=8), TODAY, PP.Prefs())
    for r in h["days"]:
        d = date.fromisoformat(r["date"])
        z = QG.evaluate(ds, ds.plan, d, PP.Prefs(), GOOD_BY, BASE)["z5"]
        assert (r["state"], r["since"], r["path"]) == (z["state"], z["since"], z["path"]), r["date"]


def test_replay_step_days_keeps_the_last_day():
    ds = _ds(_easy(range(2, 30, 2)))
    h = QG.z5_history(ds, ds.plan, TODAY - dt.timedelta(days=20), TODAY, PP.Prefs(), step_days=7)
    assert [r["date"] for r in h["days"]][-1] == TODAY.isoformat() and len(h["days"]) == 4
    assert _states(h) == ["unconfirmed"] and h["segments"][0]["label"] == "未確認"


# ---------------------------------------------------------------------------
# a break: re-entry block, then Zone 3 first
# ---------------------------------------------------------------------------

def test_replay_through_a_break_shows_the_block_and_the_zone3_wait():
    # runs every 2 days, a measured AeT on 8/30, nothing 9/6–9/15 (10 days), back 9/16
    last, back = date(2026, 9, 5), date(2026, 9, 16)
    plan = _aet_plan(date(2026, 8, 30))
    ds = _ds(_with_break(last, back), plan)
    h = QG.z5_history(ds, plan, date(2026, 9, 1), TODAY, PP.Prefs())
    assert _states(h) == ["confirmed", "reentry", "paused"]
    by = {r["date"]: r for r in h["days"]}
    assert by["2026-09-20"]["state"] == "reentry" and "Daniels" in by["2026-09-20"]["reason"]
    assert by["2026-09-28"]["state"] == "paused" and "先完成 1 堂 3 區" in by["2026-09-28"]["reason"]
    brk = [e for e in h["events"] if e["kind"] == "break"]
    assert brk and brk[-1]["days"] == 10 and brk[-1]["return"] == back.isoformat() and not brk[-1]["reconfirm"]
    assert any(e["kind"] == "pause" and "3 區" in e["label"] for e in h["events"])
    g = _gate_z5(ds)
    assert (h["current"]["state"], h["current"]["reason"]) == (g["state"], g["reason"])
    wk = {w["monday"]: w for w in h["weeks"]}
    assert wk["2026-09-14"]["keep_min"] is None


def test_replay_pause_by_the_zone1_rule(monkeypatch):
    since = TODAY - dt.timedelta(days=40)
    cut = TODAY - dt.timedelta(days=9)

    def mt(ds, today, since, brk=None):
        ok = today < cut
        return {"ok": ok, "why": "" if ok else "連續 3 週 1 區時間 < 確認時的 2/3（Hickson 1982）",
                "z1_level_min": 180.0, "weeks": []}
    monkeypatch.setattr(BC, "maintenance", mt)
    plan = _aet_plan(since)
    ds = _ds(_easy(range(1, 60, 2)), plan)
    h = QG.z5_history(ds, plan, TODAY - dt.timedelta(days=30), TODAY, PP.Prefs())
    assert _states(h) == ["confirmed", "paused"]
    p = [e for e in h["events"] if e["kind"] == "pause"]
    assert p[0]["date"] == cut.isoformat() and "Hickson" in p[0]["label"]
    assert all(w["keep_min"] == pytest.approx(120.0) for w in h["weeks"])
    assert not [e for e in h["events"] if e["kind"] == "confirm"]


def test_measured_aet_rows_are_events():
    from backend.tests.test_quality_gate import _plan
    plan = _plan(aethr=142, lthr=160, day="2026-09-20", note="飄移測試")
    ds = _ds(_easy(range(2, 30, 2)), plan)
    h = QG.z5_history(ds, plan, TODAY - dt.timedelta(days=14), TODAY, PP.Prefs())
    a = [e for e in h["events"] if e["kind"] == "aet"]
    assert a == [{"date": "2026-09-20", "kind": "aet", "value": 142.0, "label": "AeT 142 bpm（飄移測試）"}]
    # LTHR 160 / AeT 142 = 12.7 % > 10 %: the UA gap path stays closed
    assert h["current"]["state"] == "unconfirmed"


# ---------------------------------------------------------------------------
# the overview card
# ---------------------------------------------------------------------------

def test_card_two_aet_tests_and_the_soft_zone3_condition():
    plan = _aet_plan(TODAY - dt.timedelta(days=10))
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(TODAY - dt.timedelta(days=12))], plan)
    g = QG.evaluate(ds, plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert c["state"] == "confirmed" and c["aet_ok"] and not c["open"]       # AeT passed, 0 Zone 3 yet
    assert c["headline"].startswith("AeT 已通過（") and "UA 差距法" in c["headline"]
    b = c["base"]
    assert b["label"] == "實測 AeT（二選一，做了且達標）" and b["ok"]
    assert [t["key"] for t in b["tests"]] == ["aet_ua_gap", "aet_friel_drift"]      # no 90-min test
    ua = b["tests"][0]
    assert ua["ok"] and "150" in ua["value"] and "160" in ua["value"] and "7%" in ua["value"]
    assert c["z3"]["done"] == 0 and c["z3"]["need"] == QG.Z5_Z3_NEED == 2 and not c["z3"]["ok"]
    # the Zone 3 gate (SP-31): consistency and the 90-min test
    assert c["z3_gate"]["open"] and [t["ok"] for t in c["z3_gate"]["tests"]][:2] == [True, True]
    assert [(s["key"], s["status"]) for s in c["steps"]] == [("base", "done"), ("z3", "active"), ("z5", "todo")]
    assert c["next"]["kind"] == "missing" and "近 6 週再 2 堂 3 區" in c["next"]["text"]
    # two Zone 3 sessions in the last 6 weeks (done, 達標 or not) → the Zone 5 track opens
    g2 = {**g, "z3_recent": {"done": 2, "need": 2}}
    g2["z5_gate"] = QG.z5_track(g2)
    c2 = QG.z5_card(g2, TODAY)
    assert c2["open"] and c2["headline"] == "已解鎖" and c2["next"]["kind"] == "done"
    assert g2["z5_gate"]["text"].startswith("Zone 5：已解鎖（實測 AeT（UA 差距法）")


def test_card_without_a_measured_aet_and_in_a_forced_mode():
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(TODAY - dt.timedelta(days=10))])
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    # the 90-min pass opens Zone 3 but not Zone 5
    assert g["z3"]["open"] and c["state"] == "unconfirmed" and c["headline"] == "未解鎖" and not c["open"]
    ua, fr = c["base"]["tests"]
    assert ua["ok"] is None and ua["value"].startswith("—（還沒做過") and ua["missing"] == "aet"
    assert fr["ok"] is None and fr["missing"] == "aet"
    n = c["next"]["text"]
    assert n.startswith("還缺：做一次 AeT 測試") and "90 分鐘測試不算" in n and "2 堂 3 區" in n
    # a measured AeT but the LTHR is the WKO5 default / an estimate: the UA path asks for an LTHR test
    plan = _aet_plan(TODAY - dt.timedelta(days=10), note="LTHR 自動估算")
    g = QG.evaluate(_ds(_easy(range(2, 40, 2)), plan), plan, TODAY, PP.Prefs(quality_gate="ua_gap"), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert [t["key"] for t in c["base"]["tests"]] == ["aet_ua_gap"] and c["base"]["tests"][0]["missing"] == "lthr"
    assert c["base"]["label"] == "實測 AeT（實測 AeT＋實測 LTHR）" and "LTHR 測試" in c["next"]["text"]
    f = c["flow"]["tracks"][1]
    assert f["here"]["action"]["href"].endswith("?add=lib%3Afriel_lthr30&proto=race")


def _lthr_card(lthr_days_ago, runs=None, monkeypatch=None, evidence=()):
    from backend.engine import threshold_confidence as TC
    from backend.engine.planning import Plan, Threshold
    if monkeypatch is not None:
        monkeypatch.setattr(TC, "lthr_evidence", lambda ds, plan, today, runs=None: list(evidence))
    p = Plan()
    p.thresholds += [Threshold((TODAY - dt.timedelta(days=lthr_days_ago)).isoformat(), lthr=160.0),
                     Threshold((TODAY - dt.timedelta(days=10)).isoformat(), aethr=150.0)]
    g = QG.evaluate(_ds(runs if runs is not None else _easy(range(2, 40, 2)), p), p, TODAY,
                    PP.Prefs(quality_gate="ua_gap"), GOOD_BY, BASE)
    return g, QG.z5_card(g, TODAY)


def test_a_measured_lthr_has_no_age_limit_for_zone5(monkeypatch):
    """Owner 2026-10-05 (replaces SP-39's 12-week LTHR_FRESH_DAYS): the time since the LTHR test
    alone never drops it from the UA path — only an event does (lthr_invalid)."""
    for ago in (30, 85, 200, 400):
        g, c = _lthr_card(ago, monkeypatch=monkeypatch)
        assert g["lthr"]["invalid"] is None
        assert g["z5"]["state"] == "confirmed" and g["z5"]["path"] == "aet_ua_gap"
    assert not hasattr(QG, "LTHR_FRESH_DAYS")
    assert QG.z5_ua_gap({"value": 150.0, "date": "2026-09-01"}, {"value": 160.0, "measured": True,
                                                                  "date": None})["ok"]


def test_a_running_break_of_4_weeks_after_the_test_invalidates_the_lthr(monkeypatch):
    # runs up to 50 days ago, then 39 days off (≥ 4 weeks: a 29–56-day block), back 10 days ago
    runs = _easy(list(range(2, 11, 2)) + list(range(50, 80, 2)))
    g, c = _lthr_card(80, runs, monkeypatch)
    inv = g["lthr"]["invalid"]
    assert inv["code"] == "break" and "停跑 39 天" in inv["text"]
    assert "aet_ua_gap" not in g["z5"]["aet_paths"]
    t = c["base"]["tests"][0]
    assert t["missing"] == "lthr" and "停跑 39 天" in t["value"] and "要重測 30 分鐘 LTHR" in t["value"]
    # a break before the test doesn't count: the test was done after it
    g, _c = _lthr_card(8, runs, monkeypatch)
    assert g["lthr"]["invalid"] is None


def test_evidence_against_the_lthr_invalidates_it(monkeypatch):
    from backend.engine import threshold_confidence as TC
    sig = TC.signal("cp_change", "lthr", "weak", "LTHR 定下以後 CP 變了 +7%（250 → 268 W）", "up")
    g, c = _lthr_card(60, monkeypatch=monkeypatch, evidence=[sig])
    assert g["lthr"]["invalid"]["code"] == "evidence" and g["lthr"]["invalid"]["signals"] == ["cp_change"]
    assert g["z5"]["state"] == "unconfirmed" and "aet_ua_gap" not in g["z5"]["aet_paths"]
    t = c["base"]["tests"][0]
    assert t["missing"] == "lthr" and "CP 變了 +7%" in t["value"]
    f = c["flow"]["tracks"][1]
    assert "CP 變了 +7%" in c["next"]["text"]
    assert f["here"]["next"].startswith("重測 1 次 30 分鐘 LTHR（") and f["here"]["action"]["href"].endswith("?add=lib%3Afriel_lthr30&proto=race")


def test_an_aet_shift_or_move_invalidates_the_lthr(monkeypatch):
    from backend.engine import threshold_confidence as TC
    monkeypatch.setattr(TC, "lthr_evidence", lambda ds, plan, today, runs=None: [])
    ds = _ds([])
    lt = {"value": 160.0, "default": False, "measured": True, "date": "2026-08-01"}
    ae = {"measured": True, "value": 150.0, "validity": {"value": 150.0, "se": 2.0, "shift_bpm": 6.0,
                                                         "reason": "最近 6 次往上偏 6 bpm"}}
    assert QG.lthr_invalid(ds, ds.plan, TODAY, lt, ae)["code"] == "aet_shift"
    ae = {**ae, "validity": {"value": 156.0, "se": 2.0, "shift_bpm": None}}
    assert QG.lthr_invalid(ds, ds.plan, TODAY, lt, ae)["code"] == "aet_moved"
    ae = {**ae, "validity": {"value": 151.0, "se": 2.0, "shift_bpm": 1.0}}
    assert QG.lthr_invalid(ds, ds.plan, TODAY, lt, ae) is None
    assert QG.lthr_invalid(ds, ds.plan, TODAY, {**lt, "measured": False}, ae) is None


def test_lthr_evidence_reads_only_runs_after_the_test_and_no_age():
    from backend.engine import threshold_confidence as TC
    from backend.engine.planning import Plan, Threshold
    p = Plan()
    p.thresholds.append(Threshold((TODAY - dt.timedelta(days=100)).isoformat(), lthr=160.0))
    ds = _ds([], p)
    d = lambda ago: (TODAY - dt.timedelta(days=ago)).isoformat()
    hard = lambda ago, temp="cool": {"date": d(ago), "hr60": 165.0, "temp": temp}
    assert TC.lthr_evidence(ds, p, TODAY, runs=[]) == []                          # 100 days: age alone is nothing
    assert TC.lthr_evidence(ds, p, TODAY, runs=[hard(105)]) == []                 # before the test
    assert TC.lthr_evidence(ds, p, TODAY, runs=[hard(20, "hot")]) == []           # hot: a hint only
    ev = TC.lthr_evidence(ds, p, TODAY, runs=[hard(20)])
    assert [s["id"] for s in ev] == ["long_effort"] and ev[0]["level"] == "strong"


def test_card_next_line_when_paused_by_the_zone1_rule():
    gate = {"mode": "auto", "dose": {"step": 0}, "aet": {}, "lthr": {},
            "z5": {"state": "paused", "label": "暫停", "open": False, "since": "2026-08-01", "path": "aet_ua_gap",
                   "path_label": "實測 AeT（UA 差距法）", "reason": "連續 3 週…", "pause": {"kind": "z1", "at": "2026-09-14"},
                   "aet_paths": {"aet_ua_gap": "2026-08-01"}, "aet_tested": {"value": 150.0, "date": "2026-08-01"},
                   "maintenance": {"z1_level_min": 210.0, "weeks": [], "ok": False}}}
    c = QG.z5_card(gate, TODAY)
    assert not c["base"]["ok"]
    assert c["next"]["kind"] == "paused" and "重新做 AeT 測試" in c["next"]["text"] and "140 分" in c["next"]["text"]
    assert [s["status"] for s in c["steps"]] == ["active", "active", "paused"]


def test_card_in_a_reentry_block_counts_the_days_left():
    last, back = date(2026, 9, 18), date(2026, 9, 29)          # 10 days off, back yesterday
    ds = _ds(_with_break(last, back))
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert c["state"] == "reentry" and c["headline"] == "恢復期"
    r = c["reentry"]
    assert r["days"] == 10 and r["days_left"] == (date.fromisoformat(r["quality_from"]) - TODAY).days > 0
    assert "Daniels" in r["src"] and c["base"]["empty"] == "恢復期內不判斷"
    assert c["next"]["kind"] == "reentry" and c["next"]["text"].startswith(f"恢復期還剩 {r['days_left']} 天")
    assert c["steps"][0]["status"] == "wait" and c["steps"][2]["status"] == "paused"


# ---------------------------------------------------------------------------
# the stage flow (z5_card["flow"]): presentation of the same flags
# ---------------------------------------------------------------------------

def _tr(f, key):
    return next(t for t in f["tracks"] if t["key"] == key)


def _st(t):
    return {s["key"]: s["status"] for s in t["stages"]}


def test_flow_zone3_gate_locked_is_the_first_stage(monkeypatch):
    # SP-31: the Zone 3 gate — a run every 4th day is < 3 runs a week: no consistency yet, no
    # 90-min test, no AeT: Zone 3 not open, three ways in; the Zone 5 track is its own gate
    ds = _ds(_easy(range(2, 40, 4)))
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    assert not g["z3"]["open"] and g["z3"]["weeks"] < QG.Z3_WEEKS_NEED and "3 區還沒解鎖" in g["z3"]["reason"]
    f = QG.z5_card(g, TODAY)["flow"]
    assert [t["key"] for t in f["tracks"]] == ["z3", "z5"]
    t3 = _tr(f, "z3")
    assert _st(t3) == {"z3_gate": "current", "z3": "locked"} and t3["here"]["stage"] == "z3_gate" and not t3["open"]
    gate3 = t3["stages"][0]
    assert [i["text"].split("：")[0] for i in gate3["any"]] == [
        "連續 4 週，每週跑 ≥ 3 次、沒有 ≥ 7 天沒跑（預設，推估）", "徐國峰 90 分鐘測試", "UA 差距法"]
    assert gate3["any_label"] and all(i["todo"] for i in gate3["any"]) and "推估" in gate3["any"][0]["tip"]
    # 安排課表: the tests carry an action (the consistency path doesn't — it isn't a session)
    acts = [i["action"] for i in gate3["any"]]
    assert acts[0] is None and acts[1]["href"].endswith("?test=aet&proto=xu90")
    assert acts[2]["href"].endswith("?test=aet&proto=ua60")
    assert "每週 ≥ 3 次" in t3["here"]["next"]
    t5 = _tr(f, "z5")
    assert _st(t5) == {"z5_gate": "current", "z5": "locked"}
    soft = next(i for i in t5["stages"][0]["items"] if i["text"].startswith("近 6 週"))
    assert soft["ok"] is False and soft["todo"] == "先解鎖 3 區" and soft["action"] is None
    assert t5["here"]["action"]["href"].endswith("?test=aet&proto=ua60")      # the AeT test first
    d = QG.week_decision(g, "base", "base")
    assert not d["allow"] and d["z3_note"].startswith("本週沒排 3 區（還沒解鎖）：連續")
    t = QG.indicator(g)
    assert t["text"] == "3 區未開" and t["verdict"] == d["z3_note"]


def test_zone3_consistency_path_and_the_21_day_relock():
    # SP-31 (owner 2026-10-04): 4 complete weeks of ≥ 3 runs and no 7-day gap — sticky; a break of
    # ≥ 21 days without running re-locks, 6–20 days doesn't (the re-entry block handles those)
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    wk = lambda i, ks=(0, 2, 4): [mon - dt.timedelta(weeks=i) + dt.timedelta(days=k) for k in ks]
    four = sorted(d for i in range(1, 5) for d in wk(i))
    c = QG.z3_consistency(four, TODAY)
    assert c["open"] and c["since"] == mon.isoformat() and c["weeks"] == 4 and c["break"] is None
    assert not QG.z3_consistency(four[3:], TODAY)["open"]                       # 3 weeks
    two_a_week = sorted(d for i in range(1, 6) for d in wk(i, (1, 5)))
    assert not QG.z3_consistency(two_a_week, TODAY)["open"]
    # a 7-day stretch without running inside the window (Sun → the next Mon week later)
    gap = sorted(d for i in range(1, 5) for d in (wk(i, (0, 1, 2)) if i != 2 else wk(i, (4, 5, 6))))
    gap = [d for d in gap if not (mon - dt.timedelta(weeks=3) < d < mon - dt.timedelta(weeks=2) + dt.timedelta(days=4))] \
        + wk(3, (0,))
    assert not QG.z3_consistency(sorted(gap), TODAY)["open"]
    # sticky: met weeks ago, then a 16-day break → still open (the re-entry block handles it)
    c = QG.z3_consistency(sorted(d for i in range(4, 8) for d in wk(i)) + wk(1), TODAY)
    assert c["open"] and c["break"] is None and c["weeks"] == 1
    old = sorted(d for i in range(6, 10) for d in wk(i))
    # ≥ 21 days off → re-locked until 4 new weeks after the return
    c = QG.z3_consistency(old, TODAY)                                         # off since 5 weeks
    assert not c["open"] and c["break"] and c["break"]["return"] is None
    back = old + sorted(d for i in range(1, 3) for d in wk(i))
    c = QG.z3_consistency(back, TODAY)
    assert not c["open"] and c["break"]["days"] >= QG.Z3_RELOCK_DAYS


def test_flow_two_parallel_tracks_with_their_own_next_step(monkeypatch):
    from backend.tests.test_quality_gate import _weeks_ok
    monkeypatch.setattr(QG, "run_days", lambda ds, today, days=QG.Z3_HISTORY_DAYS: _weeks_ok(4))
    ds = _ds(_easy(range(2, 40, 2)))
    f = QG.z5_card(QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)["flow"]
    t3, t5 = _tr(f, "z3"), _tr(f, "z5")
    last = t3["stages"][0]["items"][-1]
    assert last["text"] == "3 區已解鎖" and last["ok"] and last["value"].startswith("連續 4 週規律訓練")
    assert _st(t3) == {"z3_gate": "done", "z3": "current"} and t3["open"]
    lad = t3["stages"][1]["items"]
    assert [i["text"] for i in lad] == [r[1] for r in QG.Z3] and not any(i["ok"] for i in lad)
    # only the current rung gets 安排課表 (its canonical variant), the later rungs don't
    assert lad[0]["action"]["type"] == "variant" and lad[0]["action"]["href"].endswith("?add=a1a")
    assert all(i["action"] is None for i in lad[1:])
    assert t3["here"]["stage"] == "z3" and QG.Z3[0][1] in t3["here"]["next"] and t3["here"]["action"]
    # the Zone 5 track: its own gate is current — two AeT tests (二選一) and the soft Zone 3 line
    assert _st(t5) == {"z5_gate": "current", "z5": "locked"} and not t5["open"]
    g5 = t5["stages"][0]
    assert [i["text"] for i in g5["any"]] == ["AeT＋LTHR 實測：差距 ≤ 10%", "AeT 附近 Friel 飄移 < 5%"]
    assert all(i["todo"] and i["action"] for i in g5["any"]) and g5["any_label"]
    soft = next(i for i in g5["items"] if i["text"].startswith("近 6 週"))
    assert soft["action"]["href"].endswith("?add=a1a")                 # a Zone 3 session ticks it
    assert t5["here"]["stage"] == "z5_gate" and "AeT 測試" in t5["here"]["next"]
    assert "3 區" in t5["here"]["also"]
    assert f["full"].startswith("還缺：")


def test_flow_confirmed_ticks_the_aet_and_shows_the_keep_line():
    plan = _aet_plan(TODAY - dt.timedelta(days=10))
    ds = _ds(_easy(range(2, 40, 2)), plan)
    c = QG.z5_card(QG.evaluate(ds, plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)
    t5 = _tr(c["flow"], "z5")
    g5 = t5["stages"][0]
    assert g5["items"][0]["ok"] is True and g5["any"] == [] and g5["status"] == "current"
    assert any(i["text"].startswith("維持：每週 1 區 ≥") for i in g5["items"])
    assert t5["here"]["next"].startswith("完成 1 堂 3 區")


def test_flow_open_zone5_moves_to_the_z5_ladder():
    gate = {"mode": "auto", "dose": {"z3": {"step": 4, "met": 4, "done": 4}, "z5": {"step": 1, "done": 1}},
            "aet": {}, "lthr": {}, "z3_recent": {"done": 3, "need": 2},
            "z5": {"state": "confirmed", "label": "已確認", "open": True, "since": "2026-08-01", "path": "aet_ua_gap",
                   "path_label": "實測 AeT（UA 差距法）"}}
    f = QG.z5_card(gate, TODAY)["flow"]
    t5 = _tr(f, "z5")
    assert _st(t5) == {"z5_gate": "done", "z5": "current"} and t5["open"]
    z5 = t5["stages"][1]["items"]
    assert [i["ok"] for i in z5] == [True, False, False, False] and QG.Z5[1][1] in z5[1]["todo"]
    assert z5[1]["action"]["type"] == "variant" and z5[1]["action"]["key"] == IL.canonical("z5b").key
    assert t5["here"]["stage"] == "z5" and QG.Z5[1][1] in t5["here"]["next"]
    # the flow and week_decision read the same flag
    d = QG.week_decision({**gate, "z3": {"open": True}, "guard": {}, "state": "none"}, "base", "base", n=2)
    assert [it["track"] for it in d["items"]] == ["z3", "z5"]


def test_flow_paused_and_reentry():
    gate = {"mode": "auto", "dose": {"step": 0}, "aet": {}, "lthr": {},
            "z5": {"state": "paused", "label": "暫停", "open": False, "since": "2026-08-01", "path": "aet_ua_gap",
                   "path_label": "實測 AeT（UA 差距法）", "reason": "連續 3 週…", "pause": {"kind": "z1", "at": "2026-09-14"},
                   "aet_tested": {"value": 150.0, "date": "2026-08-01"},
                   "maintenance": {"z1_level_min": 210.0, "weeks": [], "ok": False}}}
    t5 = _tr(QG.z5_card(gate, TODAY)["flow"], "z5")
    g5 = t5["stages"][0]
    assert g5["status"] == "current" and "140 分" in g5["note"] and "重新做 AeT 測試" in g5["note"]
    assert g5["any"][0]["ok"] is None or g5["any"][0]["ok"] is False
    last, back = date(2026, 9, 18), date(2026, 9, 29)
    ds = _ds(_with_break(last, back))
    c = QG.z5_card(QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)
    for t in c["flow"]["tracks"]:
        assert t["stages"][0]["status"] == "current" and t["here"]["stage"].endswith("_gate")
        assert all(s["status"] == "locked" for s in t["stages"][1:])
    assert f"還剩 {c['reentry']['days_left']} 天" in _tr(c["flow"], "z3")["here"]["next"]


def test_plan_auto_logs_the_zone5_track_unlock_and_relock():
    from backend.engine import plan_auto as PA
    shut = {"open": False, "aet_ok": False, "reason": "還沒有實測 AeT 通過", "text": "Zone 5：未解鎖（還沒有實測 AeT 通過）"}
    on = {"open": True, "aet_ok": True, "reason": "", "text": "Zone 5：已解鎖（實測 AeT（UA 差距法）；近 6 週 2/2 堂 3 區）"}
    state: dict = {}
    inp = {"cur": {"quality_gate": {"z5_gate": shut}}}
    assert PA.state_changes(inp, state) == []                          # the first closed record: nothing new
    inp["cur"]["quality_gate"]["z5_gate"] = on
    assert PA.state_changes(inp, state) == [on["text"]]
    assert PA.state_changes(inp, state) == []
    inp["cur"]["quality_gate"]["z5_gate"] = {**shut, "aet_ok": True, "reason": "還差 3 區：近 6 週 1/2 堂 3 區（推估）"}
    assert PA.state_changes(inp, state) == ["Zone 5：重新上鎖（還差 3 區：近 6 週 1/2 堂 3 區（推估））"]


# ---------------------------------------------------------------------------
# the custom view panel
# ---------------------------------------------------------------------------

def test_custom_view_accepts_the_z5gate_panel():
    v = parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [
        {"title": "5 區開放流程", "kind": "z5gate", "description": "…"}]}]})
    c = v["dashboards"][0]["charts"][0]
    assert c["kind"] == "z5gate" and c["series"] == []
    with pytest.raises(CustomViewError):
        parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [{"title": "t", "kind": "nope"}]}]})


def test_the_periodization_view_has_the_panel_in_the_base_dashboard():
    import json
    from backend.engine.wko5expr.customviews import REPO_VIEWS
    v = parse_view(json.loads((REPO_VIEWS / "periodization.json").read_text(encoding="utf-8")))
    base = next(d for d in v["dashboards"] if "基礎期" in d["title"])
    assert any(c["kind"] == "z5gate" for c in base["charts"])


# ---------------------------------------------------------------------------
# 「安排課表」 (SP-39): every action deep-links to a session the 課表 page can preselect
# ---------------------------------------------------------------------------

def _actions(flow):
    for t in flow["tracks"]:
        yield t["here"].get("action")
        for s in t["stages"]:
            for i in s["items"] + (s.get("any") or []):
                yield i.get("action")


def test_every_action_points_at_a_template_or_a_test_protocol_the_page_knows():
    from pathlib import Path
    from urllib.parse import parse_qs, urlparse
    from backend.engine import aet_test as AT
    from backend.engine import cp_protocols as CPP
    from backend.engine import workout_steps as WS
    keys = {r["key"]: g["cat"] for g in WS.templates()["groups"] for r in g["rows"]}
    flows = []
    ds = _ds(_easy(range(2, 40, 4)))                                        # Zone 3 locked
    flows.append(QG.z5_card(QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)["flow"])
    plan = _aet_plan(TODAY - dt.timedelta(days=10), note="LTHR 自動估算")    # the LTHR test
    ds = _ds(_easy(range(2, 40, 2)), plan)
    flows.append(QG.z5_card(QG.evaluate(ds, plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)["flow"])
    seen = set()
    for f in flows:
        for a in filter(None, _actions(f)):
            u = urlparse(a["href"])
            assert u.path == QG.SCHEDULE_PAGE
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if "test" in q:
                assert q["test"] in ("aet", "cp") and q["proto"] in (AT.PROTOCOLS if q["test"] == "aet" else CPP.PROTOCOLS)
            else:
                assert q["add"] in keys, q
                assert keys[q["add"]] == ("quality" if a["type"] == "variant" else "test")
            seen.add(a["type"])
    assert seen == {"variant", "test", "template"}
    # the page handles the deep link: the new-session dialog with the session preselected
    static = Path(QG.__file__).resolve().parents[1] / "static"
    page = (static / "schedule.html").read_text(encoding="utf-8")
    assert 'qp.get("add")' in page and 'qp.get("test")' in page and "openPreset(" in page
    assert "WE.applyKey(" in page and "async applyKey(" in (static / "workout_editor.js").read_text(encoding="utf-8")
    assert "zf-act" in (static / "z5flow.js").read_text(encoding="utf-8")


def test_a_variant_action_saves_with_its_rung():
    # POST /sessions with the action's variant_key → interval_library.variant_patch stores the rung,
    # so the session counts on its ladder (dose_history → row_track)
    a = QG._rung_action("z5a")
    v = IL.get(a["key"])
    assert v is not None and v.rung == "z5a" and IL.track_of(v.rung) == "z5"
    assert QG.row_track({"variant_key": a["key"]}) == "z5"
    assert QG.row_track({"variant_key": QG._rung_action("a1")["key"]}) == "z3"


def test_zone3_consistency_skips_transition_days():
    # SP-73 (owner 2026-10-05): a 轉換期 of only cross-training is no running gap — no 7-day
    # stretch, no 21-day re-lock, and its weeks neither count nor break the run of weeks
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    wk = lambda i, ks=(0, 2, 4): [mon - dt.timedelta(weeks=i) + dt.timedelta(days=k) for k in ks]
    span = lambda a, b: {mon - dt.timedelta(weeks=a) + dt.timedelta(days=k) for k in range(7 * (a - b + 1))}
    before = sorted(d for i in range(5, 9) for d in wk(i))           # weeks 8–5 ok, 4–2 nothing, week 1 ok
    runs = before + wk(1)
    c = QG.z3_consistency(runs, TODAY)
    assert not c["open"] and c["break"]["days"] >= QG.Z3_RELOCK_DAYS                # re-locked without it
    c = QG.z3_consistency(runs, TODAY, skip=span(4, 2))
    assert c["open"] and c["break"] is None and c["weeks"] == 5                      # the transition is see-through
    assert [r.get("transition", False) for r in c["rows"]] == [True, True, True, False]
    assert all(r["ok"] for r in c["rows"])
    # 2 weeks before + a 3-week transition + 2 weeks after = 4 weeks across it
    runs = sorted(d for i in (7, 6) for d in wk(i)) + sorted(d for i in (2, 1) for d in wk(i))
    assert not QG.z3_consistency(runs, TODAY)["open"]
    c = QG.z3_consistency(runs, TODAY, skip=span(5, 3))
    assert c["open"] and c["since"] == mon.isoformat() and c["weeks"] == 4
    # a transition week with 3 runs counts as usual
    runs = sorted(d for i in (5, 4, 3, 1) for d in wk(i))
    c = QG.z3_consistency(runs, TODAY, skip=span(3, 2))
    assert c["open"] and c["weeks"] == 4
    assert not QG.z3_consistency(runs, TODAY)["open"]                                # week 2 breaks it


def test_zone3_gate_reads_the_plans_transition():
    from backend.engine.planning import Phase
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    ds = _ds([])
    ds.plan.phases = [Phase("transition", (mon - dt.timedelta(weeks=4)).isoformat(),
                            (mon - dt.timedelta(weeks=1, days=1)).isoformat(), auto=False)]
    days = [mon - dt.timedelta(weeks=8)]
    s = QG._transition_skip(ds, days, TODAY)
    assert len(s) == 21 and min(s) == mon - dt.timedelta(weeks=4)
    assert QG._transition_skip(_ds([]), days, TODAY) == set()
    # SP-73 (owner 2026-10-05): the A race's 恢復期 before it is skipped the same way
    ds.plan.phases = [Phase("recovery", (mon - dt.timedelta(weeks=6)).isoformat(),
                            (mon - dt.timedelta(weeks=4, days=1)).isoformat(), auto=False)] + ds.plan.phases
    s = QG._transition_skip(ds, days, TODAY)
    assert len(s) == 35 and min(s) == mon - dt.timedelta(weeks=6)
