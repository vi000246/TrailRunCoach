"""The Zone 5 opening process, replayed (quality_gate.z5_history) and shown on
the overview card (quality_gate.z5_card) and the 基礎期 season chart (custom
view kind "z5gate"). Synthetic data only — never the WKO5 folder, the app DB,
the user's plan or COROS."""
import datetime as dt
from datetime import date

import pytest

from backend.engine import base_check as BC
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


@pytest.fixture(autouse=True)
def _steady_volume(monkeypatch):
    # the stable-volume precondition has its own tests (test_volume_precondition.py); the
    # every-other-day fixtures here alternate 3 / 4 runs a week, so pass it
    real = BC.volume_stable
    monkeypatch.setattr(BC, "volume_stable", lambda ds, day: {**real(ds, day), "ok": True})


# ---------------------------------------------------------------------------
# the replay agrees with the planner
# ---------------------------------------------------------------------------

def test_replay_opens_on_the_qualifying_90_min_run_and_ends_where_the_gate_is():
    xu_day = TODAY - dt.timedelta(days=10)
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(xu_day)])
    h = QG.z5_history(ds, ds.plan, TODAY - dt.timedelta(days=20), TODAY, PP.Prefs())
    by = {r["date"]: r for r in h["days"]}
    assert by[(xu_day - dt.timedelta(days=1)).isoformat()]["state"] == "unconfirmed"
    on = by[xu_day.isoformat()]
    # one of the three tests (the 90-min test) done and passed
    assert on["state"] == "confirmed" and on["path"] == "xu90" and on["since"] == xu_day.isoformat()
    assert _states(h) == ["unconfirmed", "confirmed"]
    # the last replayed day is exactly what evaluate() (the planner) says today
    g = _gate_z5(ds)
    assert h["current"]["state"] == g["state"] and h["current"]["since"] == g["since"]
    assert h["current"]["text"] == g["text"] and h["days"][-1]["date"] == TODAY.isoformat()
    # segments cover the range without gaps
    segs = h["segments"]
    assert segs[0]["start"] == h["begin"] and segs[-1]["end"] == (TODAY + dt.timedelta(days=1)).isoformat()
    assert all(a["end"] == b["start"] for a, b in zip(segs, segs[1:]))
    # events: the confirmation (path) and the 90-min run with its drift
    conf = [e for e in h["events"] if e["kind"] == "confirm"]
    assert conf == [{"date": xu_day.isoformat(), "kind": "confirm", "path": "xu90",
                     "label": "確認有氧基礎（徐國峰 90 分鐘飄移）"}]
    assert not [e for e in h["events"] if "三訊號" in e.get("label", "")]
    xu = [e for e in h["events"] if e["kind"] == "xu_run"]
    assert len(xu) == 1 and xu[0]["ok"] and xu[0]["drift"] == pytest.approx(0.06, abs=0.003)
    # weekly Zone 1 minutes, the 150–210 band and the pause line after the confirmation
    assert h["target"] == [150.0, 210.0]
    wk = {w["monday"]: w for w in h["weeks"]}
    xu_mon = BC.monday(xu_day).isoformat()
    assert wk[xu_mon]["z1_min"] >= 95 and wk[xu_mon]["keep_min"] == pytest.approx(wk[xu_mon]["level_min"] * 2 / 3)
    assert wk[min(wk)]["keep_min"] is None                         # before the confirmation: no line


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
    # runs every 2 days, a 90-min pass on 8/30, nothing 9/6–9/15 (10 days), back 9/16
    last, back = date(2026, 9, 5), date(2026, 9, 16)
    ds = _ds(_with_break(last, back) + [_xu_workout(date(2026, 8, 30))])
    h = QG.z5_history(ds, ds.plan, date(2026, 9, 1), TODAY, PP.Prefs())
    assert _states(h) == ["confirmed", "reentry", "paused"]
    by = {r["date"]: r for r in h["days"]}
    assert by["2026-09-20"]["state"] == "reentry" and "Daniels" in by["2026-09-20"]["reason"]
    assert by["2026-09-28"]["state"] == "paused" and "先完成 1 堂 3 區" in by["2026-09-28"]["reason"]
    brk = [e for e in h["events"] if e["kind"] == "break"]
    assert brk and brk[-1]["days"] == 10 and brk[-1]["return"] == back.isoformat() and not brk[-1]["reconfirm"]
    assert any(e["kind"] == "pause" and "3 區" in e["label"] for e in h["events"])
    g = _gate_z5(ds)
    assert (h["current"]["state"], h["current"]["reason"]) == (g["state"], g["reason"])
    # the week of the break carries no pause line once the state is not confirmed / paused-by-the-rule
    wk = {w["monday"]: w for w in h["weeks"]}
    assert wk["2026-09-14"]["keep_min"] is None


def test_replay_pause_by_the_zone1_rule(monkeypatch):
    since = TODAY - dt.timedelta(days=40)
    monkeypatch.setattr(BC, "xu_runs", lambda ds, today, days=182: [
        {"idx": 0, "date": since.isoformat(), "ok": True, "drift": 0.05, "hr10": 128.0, "hr90": 134.4, "why": []}]
        if today >= since else [])
    cut = TODAY - dt.timedelta(days=9)

    def mt(ds, today, since, brk=None):
        ok = today < cut
        return {"ok": ok, "why": "" if ok else "連續 3 週 1 區時間 < 確認時的 2/3（Hickson 1982）",
                "z1_level_min": 180.0, "weeks": []}
    monkeypatch.setattr(BC, "maintenance", mt)
    ds = _ds(_easy(range(1, 60, 2)))
    h = QG.z5_history(ds, ds.plan, TODAY - dt.timedelta(days=30), TODAY, PP.Prefs())
    assert _states(h) == ["confirmed", "paused"]
    p = [e for e in h["events"] if e["kind"] == "pause"]
    assert p[0]["date"] == cut.isoformat() and "Hickson" in p[0]["label"]
    assert all(w["keep_min"] == pytest.approx(120.0) for w in h["weeks"])
    # a confirmation dated before the range is a state, not an event
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

def test_card_one_step_three_tests_from_the_gate():
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(TODAY - dt.timedelta(days=10))])
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert c["state"] == "confirmed" and c["headline"].startswith("已確認（") and "90 分鐘" in c["headline"]
    assert "paths" not in c and "三訊號" not in str(c)
    b = c["base"]
    assert b["label"] == "確認有氧基礎（三選一，做了且達標）" and b["ok"]
    # auto: the three tests, the 90-min test exactly once
    assert [t["key"] for t in b["tests"]] == ["xu90", "aet_ua_gap", "aet_friel_drift"]
    xu, ua, fr = b["tests"]
    assert xu["ok"] and "6.0%" in xu["value"] and "徐國峰" in xu["src"]
    assert ua["ok"] is None and "沒有實測 AeT" in ua["value"]
    assert fr["ok"] is None
    assert c["z3"] == {"done": 0, "need": 3, "ok": False, "src": QG.SRC_Z5["z3"]}
    assert c["keep"] and c["keep"]["line_min"] == pytest.approx(c["keep"]["level_min"] * 2 / 3)
    # the tracker and the 「還缺什麼」 line
    assert [(s["key"], s["status"]) for s in c["steps"]] == [("base", "done"), ("z3", "active"), ("z5", "todo")]
    assert c["next"] == {"kind": "missing", "text": "還缺：再 3 堂 3 區達標（0/3；3 區只要護欄通過就照排）"}


def test_card_without_any_long_run_and_in_a_forced_mode():
    ds = _ds(_easy(range(2, 40, 2)))
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert c["state"] == "unconfirmed" and c["headline"] == "未確認"
    xu = c["base"]["tests"][0]
    assert xu["ok"] is None and xu["value"].startswith("—（還沒做過")
    assert c["steps"][0]["status"] == "active" and c["steps"][1]["status"] == "todo"
    n = c["next"]["text"]
    assert n.startswith("還缺：做一次 90 分鐘平路 1 區測試") and "25 °C" in n and "AeT 測試" in n
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(quality_gate="ua_gap"), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert [t["key"] for t in c["base"]["tests"]] == ["aet_ua_gap"]
    assert c["base"]["label"] == "確認有氧基礎（UA 差距法）" and "AeT 測試" in c["next"]["text"]


def test_card_next_line_for_a_failed_90_min_test():
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(TODAY - dt.timedelta(days=10), rise=0.12)])
    c = QG.z5_card(QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)
    assert c["base"]["tests"][0]["ok"] is False
    assert "上次" in c["next"]["text"] and "飄移" in c["next"]["text"]


def test_card_next_line_when_paused_by_the_zone1_rule():
    gate = {"mode": "auto", "dose": {"step": 0}, "aet": {}, "lthr": {},
            "z5": {"state": "paused", "label": "暫停", "open": False, "since": "2026-08-01", "path": "xu90",
                   "path_label": "徐國峰 90 分鐘飄移", "reason": "連續 3 週…", "pause": {"kind": "z1", "at": "2026-09-14"},
                   "xu_last": {"date": "2026-08-01", "ok": True, "drift": 0.05, "hr10": 128, "hr90": 134, "why": []},
                   "maintenance": {"z1_level_min": 210.0, "weeks": [], "ok": False}}}
    c = QG.z5_card(gate, TODAY)
    assert not c["base"]["ok"] and c["base"]["tests"][0]["ok"] is False   # the old pass is before the pause
    assert c["next"]["kind"] == "paused" and "重新確認" in c["next"]["text"] and "140 分" in c["next"]["text"]
    assert [s["status"] for s in c["steps"]] == ["active", "todo", "paused"]


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

def _st(f):
    return {s["key"]: s["status"] for s in f["stages"]}


def test_flow_unconfirmed_z3_is_current_and_the_test_runs_alongside():
    ds = _ds(_easy(range(2, 40, 2)))
    f = QG.z5_card(QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)["flow"]
    assert [s["key"] for s in f["stages"]] == ["base", "z3", "confirm", "unlock", "z5"]
    assert _st(f) == {"base": "done", "z3": "current", "confirm": "parallel", "unlock": "locked", "z5": "locked"}
    z3 = f["stages"][1]
    assert [i["text"] for i in z3["items"]] == [r[1] for r in QG.Z3] and not any(i["ok"] for i in z3["items"])
    assert f["here"]["stage"] == "z3" and QG.Z3[0][1] in f["here"]["next"]
    conf = f["stages"][2]
    assert [i["text"] for i in conf["any"]][0] == "90 分鐘飄移測試" and conf["any_label"]
    assert all(i["todo"] for i in conf["any"])                   # each untried test says what to do
    assert "AeT 測試" in f["here"]["also"] and f["here"]["also_title"] == "有氧基礎確認"
    assert f["here"]["full"].startswith("還缺：")


def test_flow_confirmed_ticks_the_confirmation_and_shows_the_keep_line():
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(TODAY - dt.timedelta(days=10))])
    c = QG.z5_card(QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)
    f = c["flow"]
    assert _st(f) == {"base": "done", "z3": "current", "confirm": "done", "unlock": "locked", "z5": "locked"}
    conf = f["stages"][2]
    assert conf["items"][0]["ok"] is True and conf["any"] == []
    assert any(i["text"].startswith("維持：每週 1 區 ≥") for i in conf["items"])
    assert f["here"]["also"] == "" and [i["ok"] for i in f["stages"][3]["items"]] == [True, False]


def test_flow_open_zone5_moves_to_the_z5_ladder():
    gate = {"mode": "auto", "dose": {"step": 4}, "aet": {}, "lthr": {},
            "z5": {"state": "confirmed", "label": "已確認", "open": True, "since": "2026-08-01", "path": "xu90",
                   "path_label": "徐國峰 90 分鐘飄移", "xu_last": {"date": "2026-08-01", "ok": True, "drift": 0.05,
                                                            "hr10": 128, "hr90": 134, "why": []}}}
    f = QG.z5_card(gate, TODAY)["flow"]
    assert _st(f) == {"base": "done", "z3": "done", "confirm": "done", "unlock": "done", "z5": "current"}
    z5 = f["stages"][4]["items"]
    assert [i["ok"] for i in z5] == [True, False, False, False] and QG.Z5[1][1] in z5[1]["todo"]
    assert f["here"]["stage"] == "z5" and QG.Z5[1][1] in f["here"]["next"]


def test_flow_paused_and_reentry():
    gate = {"mode": "auto", "dose": {"step": 0}, "aet": {}, "lthr": {},
            "z5": {"state": "paused", "label": "暫停", "open": False, "since": "2026-08-01", "path": "xu90",
                   "path_label": "徐國峰 90 分鐘飄移", "reason": "連續 3 週…", "pause": {"kind": "z1", "at": "2026-09-14"},
                   "xu_last": {"date": "2026-08-01", "ok": True, "drift": 0.05, "hr10": 128, "hr90": 134, "why": []},
                   "maintenance": {"z1_level_min": 210.0, "weeks": [], "ok": False}}}
    f = QG.z5_card(gate, TODAY)["flow"]
    conf = f["stages"][2]
    assert conf["status"] == "parallel" and "140 分" in conf["note"] and conf["any"][0]["ok"] is False
    assert "再做 1 次 90 分鐘測試" in conf["any"][0]["todo"]
    last, back = date(2026, 9, 18), date(2026, 9, 29)
    ds = _ds(_with_break(last, back))
    c = QG.z5_card(QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE), TODAY)
    f = c["flow"]
    assert f["stages"][0]["status"] == "current" and f["here"]["stage"] == "base"
    assert f"還剩 {c['reentry']['days_left']} 天" in f["here"]["next"]
    assert all(s["status"] in ("locked", "done") for s in f["stages"][1:])


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
