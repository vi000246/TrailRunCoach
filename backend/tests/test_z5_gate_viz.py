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
    # the run also completes 三訊號 (the easy weeks give ②), which wins the tie (base_check)
    assert on["state"] == "confirmed" and on["path"] == "xu_signals" and on["since"] == xu_day.isoformat()
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
    assert conf == [{"date": xu_day.isoformat(), "kind": "confirm", "path": "xu_signals",
                     "label": "確認有氧基礎（三訊號）"}]
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
    monkeypatch.setattr(BC, "three_signals", lambda ds, today: {"ok": False, "text": "還沒", "s1": {}})
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

def sig_ok(card):
    return next(p for p in card["paths"] if p["key"] == "xu_signals")["ok"]


def test_card_checklist_from_the_gate():
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(TODAY - dt.timedelta(days=10))])
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert c["state"] == "confirmed" and c["headline"].startswith("已確認（") and "三訊號" in c["headline"]
    assert sig_ok(c)
    keys = [p["key"] for p in c["paths"]]
    assert keys == ["xu_signals", "xu90", "aet"]                 # auto: any one path opens Zone 5
    sig = c["paths"][0]
    assert [i["label"][0] for i in sig["items"]] == ["①", "②", "③"]
    one, two, three = sig["items"]
    assert one["ok"] and "6.0%" in one["value"] and "徐國峰" in one["src"]
    assert two["need"].startswith("150–210 分") and "推估" in two["src"]
    assert "分" in two["value"]
    assert three["ok"] is not None and "推估" in three["src"]
    assert c["paths"][1]["ok"]                                   # the 90-min test passed
    ua = c["paths"][2]["items"][0]
    assert ua["ok"] is None and "沒有實測 AeT" in ua["value"]
    assert c["z3"] == {"done": 0, "need": 3, "ok": False, "src": QG.SRC_Z5["z3"]}
    assert c["keep"] and c["keep"]["line_min"] == pytest.approx(c["keep"]["level_min"] * 2 / 3)


def test_card_without_any_long_run_and_in_a_forced_mode():
    ds = _ds(_easy(range(2, 40, 2)))
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert c["state"] == "unconfirmed" and c["headline"] == "未確認"
    one = c["paths"][0]["items"][0]
    assert one["ok"] is False and one["value"] == "—（8 週內沒有符合的長跑）"
    assert c["paths"][1]["items"][0]["ok"] is None
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(quality_gate="ua_gap"), GOOD_BY, BASE)
    assert [p["key"] for p in QG.z5_card(g, TODAY)["paths"]] == ["aet"]


def test_card_in_a_reentry_block_counts_the_days_left():
    last, back = date(2026, 9, 18), date(2026, 9, 29)          # 10 days off, back yesterday
    ds = _ds(_with_break(last, back))
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    c = QG.z5_card(g, TODAY)
    assert c["state"] == "reentry" and c["headline"] == "恢復期"
    r = c["reentry"]
    assert r["days"] == 10 and r["days_left"] == (date.fromisoformat(r["quality_from"]) - TODAY).days > 0
    assert "Daniels" in r["src"] and c["paths"][0]["empty"] == "恢復期內不判斷"


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
