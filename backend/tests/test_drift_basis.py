"""
配速／功率 basis toggle on the drift charts (backend/engine/wko5expr/basis.py)
and the Pw:HR variant of workout_review.drift_of.

The golden part (now realdata/test_real_drift_basis.py) recomputes Pa:HR and Pw:HR on real runs with plain loops
that do not use workout_review's helpers, and checks them against

  * the review card (drift_of v8: first 10 min excluded, ≥ 40 min of moving
    time after it, no fast finish, one window and halves for both bases) on
    the one road run in the data that passes (FAIR) — equal to 1e-9; the
    three RUNS it took until v7 have 33–35 min after the warm-up: refused;
  * the season drift charts (能力 心率飄移, periodization ②), which plot the
    card's number through drift("pace" | "power");
  * WKO5's stored pahr / pwhr (still plotted by the 耐久度 charts and WKO5's
    own imported charts). WKO5 splits the
    *whole* recording at half its length and keeps the warm-up and stops
    (docs/wko5-internals/workout-metrics.md, "Halves"). Recomputed that way
    the stored values match to 5e-5 (pahr is stored rounded to 4 decimals)
    and 1e-9 (pwhr is not rounded).

The two definitions are not the same number: with the first 10 minutes left
out, these runs move by up to 9.4 percentage points (see DEFINITION_GAP),
downwards on all three. During the warm-up HR lags behind speed / power,
so a first half that includes it has a high output-per-beat and the
whole-run drift reads larger. (It is not always downwards: on 2026-08-03
the card reads 5.1 % against a stored 2.7 %; the card also drops stops,
which WKO5 keeps.) That is why the card says 「前 10 分鐘不算」
and why the season chart and the card can disagree on one run.
"""
import datetime as dt
import re

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.engine.wko5expr import basis as BS
from backend.engine.wko5expr.customviews import CustomViewError, REPO_VIEWS, load_custom_views, parse_view


SEASON_TITLE = "輕鬆路跑的心率飄移"       # views/training.json 能力


def _t(minutes):
    return np.arange(0, minutes * 60 + 1, 1.0)


# ---------------------------------------------------------------------------
# drift_of: Pw:HR with the same rules
# ---------------------------------------------------------------------------

def test_power_drift_uses_the_same_halves_and_warmup():
    t = _t(60)
    hr = np.where(t < 600, 100.0, np.linspace(135, 150, len(t)))   # low only in the warm-up
    v = np.full(len(t), 10.0)
    p = np.full(len(t), 200.0)
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and r["pw_ok"]
    # constant speed and power: both bases see the same HR drift
    assert r["pw_drift"] == pytest.approx(r["drift"], abs=1e-12)
    assert r["pw_hr1"] == pytest.approx(r["hr1"]) and r["p1"] == pytest.approx(200.0)


def test_power_drift_differs_when_power_and_speed_do():
    t = _t(60)
    hr = np.full(len(t), 140.0)
    v = np.full(len(t), 10.0)
    # power falls 4 %, speed holds (v11: > 5 % between the halves is refused — drift v2)
    p = np.where(t < 1800 + 600, 220.0, 212.0)
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert abs(r["drift"]) < 1e-9 and r["pw_drift"] > 0.025


def test_no_power_says_so_instead_of_zero():
    t = _t(60)
    r = R.drift_of(t, np.full(len(t), 140.0), np.full(len(t), 10.0))
    assert r["ok"] and not r["pw_ok"] and r["pw_drift"] is None
    assert r["pw_reason"] == "這次沒有功率"
    assert R.basis_drift(r, "power") == (None, "這次沒有功率")
    assert R.basis_drift(r, "pace")[0] == r["drift"]


def test_an_unfair_run_is_refused_on_both_bases():
    t = _t(60)
    r = R.drift_of(t, np.full(len(t), 140.0), np.full(len(t), 10.0), np.full(len(t), 200.0), trail=True)
    assert not r["ok"] and not r["pw_ok"]
    assert R.basis_drift(r, "power")[1] == R.basis_drift(r, "pace")[1] == r["reason"]


def test_aerobic_lines_power_mode_never_counts_the_streak():
    m = {"drift": {"ok": True, "drift": 0.02, "hr1": 140, "pw_ok": True, "pw_drift": 0.03, "pw_hr1": 140},
         "category": "road"}
    pace = R.aerobic_lines("easy", m, streak=3)
    power = R.aerobic_lines("easy", m, streak=3, basis="power")
    # the unsourced 「連續 3 次」 streak is gone on both bases (engine/quality_gate.py is the gate)
    assert pace and not any("連續" in ln or "間歇" in ln for ln in pace)
    assert power and "心率飄移（功率）" in power[0] and not any("連續" in ln or "閾值下間歇" in ln for ln in power)


# ---------------------------------------------------------------------------
# the aerobic card on a fake dataset
# ---------------------------------------------------------------------------

def _run(day, power=None, minutes=50):
    from backend.tests.wko5_fakes import FakeWorkout
    t = _t(minutes)
    ch = {"elapsedtime": list(t), "heartrate": list(np.linspace(135, 140, len(t))),
          "speed": [10.0] * len(t), "elapseddistance": list(t * 10 / 3600.0)}
    if power is not None:
        ch["power"] = [float(power)] * len(t)
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": minutes * 60.0, "movingduration": minutes * 60.0,
                                "distance": minutes / 6, "climbing": 20.0})


def _card_text(card):
    return {s["name"]: s["data"]["value"] for s in card["series"] if s["data"]["kind"] == "value"}


def test_aerobic_card_shows_the_chosen_basis():
    from backend.tests.wko5_fakes import FakeDataset
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_run(today, power=200)], today, settings={"runthr": 160.0, "runftp": 300.0})
    w = ds.workouts[0]
    pace = _card_text(R.review(ds, w, "aerobic"))
    power = _card_text(R.review(ds, w, "aerobic", basis="power"))
    assert "心率飄移（配速）" in pace and "心率飄移（功率）" not in pace and "前半／後半速度" in pace
    assert "心率飄移（功率）" in power and "心率飄移（配速）" not in power and power["前半／後半功率"] == "200 → 200 W"
    # the summary card (and so the overview) stays on pace
    assert R.review(ds, w, "summary", basis="power")["series"] == R.review(ds, w, "summary")["series"]


def test_aerobic_card_without_power_in_power_mode():
    from backend.tests.wko5_fakes import FakeDataset
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_run(today)], today, settings={"runthr": 160.0, "runftp": 300.0})
    card = R.review(ds, ds.workouts[0], "aerobic", basis="power")
    text = _card_text(card)
    assert text["心率飄移（功率）"] == "這次沒有功率"
    assert not any("0.0%" in v for v in text.values())


# ---------------------------------------------------------------------------
# chart spec and server rewrite
# ---------------------------------------------------------------------------

CHART = {"title": "心率飄移 Pa:HR", "description": "每下心跳換到的速度（m/min per bpm）。",
         "basis": {"default": "pace", "choices": ["pace", "power"], "power_note": "越野只當參考。"},
         "series": [{"name": "路跑 Pa:HR", "basis": "pace", "expression": "pahr"},
                    {"name": "路跑 Pw:HR", "basis": "power", "expression": "pwhr"},
                    {"name": "5%", "expression": "(,0.05)"}]}


def _parsed(chart):
    return parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [chart]}]})["dashboards"][0]["charts"][0]


def test_apply_basis_keeps_the_matching_series_and_retitles():
    ch = _parsed(CHART)
    pace, info = BS.apply_basis(ch, None)
    assert [s["name"] for s in pace["series"]] == ["路跑 Pa:HR", "5%"]
    assert pace["title"] == "心率飄移 Pa:HR" and "越野" not in pace["description"]
    assert info == {"basis": "pace", "basis_default": "pace", "basis_choices": ["pace", "power"],
                    "basis_labels": {"pace": "配速", "power": "功率"}, "basis_toggle": True}
    power, info = BS.apply_basis(ch, "power")
    assert [s["name"] for s in power["series"]] == ["路跑 Pw:HR", "5%"]
    assert power["title"] == "心率飄移 Pw:HR"
    assert "功率" in power["description"] and "W per bpm" in power["description"]
    assert power["description"].endswith("越野只當參考。") and power["basis_chosen"] == "power"
    assert BS.apply_basis(ch, "nonsense")[1]["basis"] == "pace"
    assert ch["series"][0]["basis"] == "pace"          # the input chart is not changed


def test_basis_spec_is_validated():
    with pytest.raises(CustomViewError):
        _parsed({**CHART, "basis": {"default": "hr", "choices": ["pace", "hr"]}})
    with pytest.raises(CustomViewError):
        _parsed({**CHART, "basis": None})               # tagged series without a chart basis


def test_no_power_note():
    ch = BS.apply_basis(_parsed({"title": "每公里心率與速度", "kind": "workout",
                                 "basis": {"default": "pace", "choices": ["pace", "power"]},
                                 "series": [{"name": "平均心率", "expression": "heartrate"},
                                            {"name": "功率", "basis": "power", "expression": "power"},
                                            {"name": "AeT", "expression": "(,aethr)"}]}), "power")[0]
    res = {"kind": "workout", "series": [
        {"name": "平均心率", "expression": "heartrate", "data": {"kind": "points", "points": [[0, 140]]}},
        {"name": "功率", "expression": "power", "data": {"kind": "points", "points": []}},
        {"name": "AeT", "expression": "(,aethr)", "data": {"kind": "hline", "y": 140}}], "empty": None}
    out = BS.no_power_note(res, ch, has_power=False)
    assert [s["name"] for s in out["series"]] == ["平均心率", "AeT"] and out["basis_note"] == "這次沒有功率"
    assert BS.no_power_note(res, ch, has_power=True) is res
    only = {**res, "series": [res["series"][1], res["series"][2]]}
    assert BS.no_power_note(only, ch, has_power=False)["empty"] == "這次沒有功率"


def test_bundled_drift_charts_have_the_toggle():
    views = load_custom_views([REPO_VIEWS])
    want = {("我的訓練", SEASON_TITLE), ("我的訓練", "耐久度：長時間後段心率飄移"),
            ("周期化訓練", "長時間輕鬆跑的心率飄移"), ("周期化訓練", "耐久度：長時間後段心率飄移"),
            ("單次活動判讀", "飄移判讀")}
    found = {(name, c["title"]) for name, v in views.items() for d in v["dashboards"]
             for c in d["charts"] if c.get("basis")}
    assert want <= found, want - found
    for name, v in views.items():
        for d in v["dashboards"]:
            for c in d["charts"]:
                if not c.get("basis") or c["kind"] == "review":
                    continue
                for b in ("pace", "power"):
                    got = BS.apply_basis(c, b)[0]
                    assert any(s.get("basis") == b for s in got["series"]), (c["title"], b)
                trail_power = any(s.get("basis") == "power" and re.search(r'(?<!!)hastag\("runningtrail"\)',
                                                                          s["expression"]) for s in c["series"])
                if trail_power:
                    # trail drift in power mode carries the Stryd grade caveat, pace mode doesn't
                    assert "8% 坡度" in BS.apply_basis(c, "power")[0]["description"], c["title"]
                    assert "Stryd" not in (BS.apply_basis(c, "pace")[0]["description"] or ""), c["title"]


def test_season_drift_charts_use_the_card_definition():
    """The 能力 心率飄移 and periodization ② charts plot drift() (the card's
    definition) and say so; the 耐久度 charts stay on WKO5's stored values
    (trail runs are all refused by drift_of) and say that too."""
    views = load_custom_views([REPO_VIEWS])
    charts = {(name, c["title"]): c for name, v in views.items() for d in v["dashboards"] for c in d["charts"]}
    # periodization ② and the 能力 chart: verdict bars of drift() over both tiers (owner 2026-10-02:
    # plain words, no SE / 6-run mean / Pa:HR anywhere drift is shown)
    for key in (("周期化訓練", "長時間輕鬆跑的心率飄移"), ("我的訓練", SEASON_TITLE)):
        c = charts[key]
        assert c.get("drift_bars") is True, key
        drawn = [s for s in c["series"] if s.get("basis")]
        assert {s["basis"] for s in drawn} == {"pace", "power"} and all(s["type"] == "bar" for s in drawn)
        for s in drawn:
            assert f'drift("{s["basis"]}", "all")' in s["expression"] and "pahr" not in s["expression"], s
            assert "drift_avg" not in s["expression"]
        assert [s["name"] for s in drawn if s["basis"] == "pace"] == ["穩定（< 5%）", "有點飄（5–10%）", "飄很多（> 10%）"]
        refs = [s for s in c["series"] if not s.get("basis")]
        assert len(refs) == 1 and refs[0]["expression"] == "(,0.05)" and refs[0]["line_style"] == "dash"
        for word in ("標準誤", "SE", "回歸", "信賴", "Pa:HR", "次平均"):
            assert word not in c["description"] and word not in c["title"], word
        assert "怎麼用" in c["description"] and "Uphill Athlete" in c["description"]
    for key in (("我的訓練", "耐久度：長時間後段心率飄移"), ("周期化訓練", "耐久度：長時間後段心率飄移")):
        c = charts[key]
        # WKO5's stored value: named only in the description's last 方法 line; plain series names
        assert any("pahr" in s["expression"] for s in c["series"]) and "WKO5 存的 Pa:HR" in c["description"]
        assert c["description"].split("\n")[-1].startswith("方法：")
        assert all("Pa:HR" not in s["name"] and "Pw:HR" not in s["name"] for s in c["series"])
