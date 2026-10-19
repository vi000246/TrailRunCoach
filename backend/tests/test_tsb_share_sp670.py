"""SP-670 (SP-664 Q4 = A, owner 2026-10-15): TSB is read as a share of CTL everywhere — the status
light, the planner's guardrails, the race-day targets and the charts — so one day never gets two
answers. A heart-rate TSS scale change multiplies CTL and TSB alike; only TSB ÷ CTL survives it.
Under CTL 30 the share is taken of 30 (a point floor), so a small CTL doesn't blow noise up.
Synthetic data only."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from backend.engine import adapt as A
from backend.engine import b2b as B2B
from backend.engine import load_guard as LG
from backend.engine import quality_gate as QG
from backend.engine import specific_phase as SP
from backend.engine import steep_hill as SH
from backend.engine.panels import plan_projection as PJ
from backend.engine.panels.recommend import recommend

ROOT = Path(__file__).resolve().parents[2]


# ---- the one helper -------------------------------------------------------------------------

def test_share_is_tsb_over_ctl_and_survives_a_tss_scale_change():
    assert LG.tsb_share(-12.0, 40.0) == pytest.approx(-0.30)
    assert LG.tsb_share(-30.0, 100.0) == pytest.approx(-0.30)
    assert LG.tsb_share(-12.0 * 1.4, 40.0 * 1.4) == pytest.approx(LG.tsb_share(-12.0, 40.0))
    assert LG.tsb_share(None, 50.0) is None


@pytest.mark.parametrize("ctl, base", [(0.0, 30.0), (None, 30.0), (15.0, 30.0), (29.9, 30.0), (30.0, 30.0),
                                       (45.0, 45.0), (120.0, 120.0)])
def test_under_ctl_30_the_share_is_of_30(ctl, base):
    assert LG.TSB_CTL_MIN == 30.0
    assert LG.tsb_base(ctl) == pytest.approx(base)
    assert LG.tsb_line(-0.30, ctl) == pytest.approx(-0.30 * base)
    assert LG.tsb_share(-9.0, ctl) == pytest.approx(-9.0 / base)


def test_the_owners_lines():
    assert (LG.TSB_DEEP, LG.TSB_HOLD) == (-0.30, -0.20)
    # Q1-2 (owner 2026-10-19): 比賽狀態 starts at the A race's +10 %, was +5 %
    assert LG.TSB_PRODUCTIVE == (-0.30, -0.10) and LG.TSB_FRESH == 0.10 and LG.TSB_STALE == 0.35
    assert LG.TSB_RACE == {"A": (0.10, 0.35), "B": (-0.05, 0.05)}
    assert LG.TSB_FRESH == LG.TSB_RACE["A"][0]


@pytest.mark.parametrize("share, zone", [(-0.31, "deep"), (-0.30, "productive"), (-0.11, "productive"),
                                         (-0.10, "recovering"), (0.04, "recovering"), (0.05, "recovering"),
                                         (0.09, "recovering"), (0.10, "fresh"),
                                         (0.34, "fresh"), (0.35, "stale"), (0.80, "stale")])
def test_zone(share, zone):
    assert LG.tsb_zone(share) == zone


@pytest.mark.parametrize("tsb, ctl, rule", [
    (-14.0, 40.0, "recovery"),      # −35 %: the old −30 points never fired here
    (-31.0, 200.0, None),           # −15.5 %: the old −30 points made it a recovery week
    (-25.0, 100.0, "hold"),
    (-35.0, 150.0, "hold"),         # −23 %
    (-8.0, 20.0, "hold"),           # CTL 20 → of 30: −27 %
    (-10.0, 20.0, "recovery"),      # of 30: −33 %
    (-5.0, 20.0, None),
])
def test_week_rule(tsb, ctl, rule):
    assert LG.week_rule(tsb, ctl) == rule


def test_pct_text_says_when_the_floor_is_used():
    assert "−30%" in LG.tsb_pct_text(-12.0, 40.0) or "-30%" in LG.tsb_pct_text(-12.0, 40.0)
    low = LG.tsb_pct_text(-9.0, 15.0)
    assert "30" in low and "不到" in low


@pytest.mark.parametrize("share, text", [
    (-0.303, "-30.3"),      # rounds onto −30 % but is below it: one decimal (Q1-5, owner 2026-10-19)
    (-0.297, "-29.7"),      # rounds onto −30 % but is above it
    (-0.30, "-30"),         # on the line: whole
    (-0.2049, "-20.5"),
    (0.096, "+9.6"),        # rounds onto +10 % (比賽狀態 / A race) but isn't there yet
    (0.346, "+34.6"),
    (-0.22, "-22"),         # nowhere near a line: whole
    (-0.276, "-28"),
    (0.0, "+0"),
])
def test_pct_text_has_one_decimal_only_when_it_rounds_onto_a_line(share, text):
    assert LG.tsb_pct_number(share) == text


def test_pct_text_the_owners_example():
    """「−17（長期負荷的 −30%），低於 −30%」 at −30.3 % (圖 5) now says −30.3%."""
    assert str(LG.tsb_pct_text(-30.3, 100.0)) == "長期負荷的 -30.3%"
    assert str(LG.tsb_pct_text(-30.0, 100.0)) == "長期負荷的 -30%"
    assert str(LG.tsb_pct_text(-9.09, 15.0)).startswith("-30.3%（長期負荷不到 30")
    assert set(LG.TSB_LINES_PCT) == {-30, -25, -20, -10, -5, 5, 10, 35}


def test_pmc_chart_js_lines_match_the_helper():
    js = (ROOT / "backend/static/pmc_chart.js").read_text("utf-8")
    m = re.search(r"const LINES = \[([^\]]*)\]", js)
    assert m and {int(x) for x in m.group(1).split(",")} == set(LG.TSB_LINES_PCT)


# ---- the guardrails (acceptance 2: every absolute line is gone) -------------------------------

def test_quality_gate_hold_is_a_share():
    assert QG.guard(tsb=-25.0, tsb_ctl=100.0)["rule"] == "tsb"
    assert QG.guard(tsb=-25.0, tsb_ctl=150.0)["rule"] == ""                 # −17 %
    assert QG.guard(tsb=-10.0, tsb_ctl=40.0)["rule"] == "tsb"               # −25 %; was nothing at −10
    assert "%" in QG.guard(tsb=-25.0, tsb_ctl=100.0)["verdict"]


def test_long_climb_b2b_and_steep_walk_wait_on_the_share():
    from backend.tests.test_b2b import ctx as b2b_ctx
    from backend.tests.test_steep_hill import MON as SH_MON, _ev as sh_ev
    # B2B
    assert any("%" in b for b in b2b_ctx(tsb=-10.0, ctl=40.0)["blocked"])           # −25 %
    assert not any("負荷平衡" in b for b in b2b_ctx(tsb=-25.0, ctl=150.0)["blocked"])  # −17 %
    # steep walk
    on = dict(kind="specific", mode="build", monday=SH_MON, event=sh_ev(5), weight=68.0)
    assert not SH.week_context(**on, tsb=-10.0, ctl=40.0)["active"]
    assert SH.week_context(**on, tsb=-25.0, ctl=150.0)["active"]
    # 專項期 long climb: the reason names the share
    race = {"start": "2026-11-07", "features": {"climb": {"dz": 600}}, "summit": False}
    sp = SP.week_context(kind="specific", mode="specific", monday=dt.date(2026, 9, 28), race=race,
                         tsb=-10.0, ctl=40.0)
    assert "%" in (sp.get("climb_why") or "") and not sp.get("climb")
    sp2 = SP.week_context(kind="specific", mode="specific", monday=dt.date(2026, 9, 28), race=race,
                          tsb=-25.0, ctl=150.0)
    assert "負荷平衡" not in (sp2.get("climb_why") or "")


def test_b2b_exemption_reads_the_share():
    assert B2B.tsb_exempt({"due": True}, -25.0, ctl=150.0) is None          # −17 %: nothing to exempt
    assert B2B.tsb_exempt({"due": True}, -10.0, ctl=40.0)                    # −25 %


def _gw():
    from backend.tests.test_adapt import g, week
    return week([g("quality", "quality", "2026-10-01", 60), g("easy3", "easy", "2026-10-02", 50)])


def test_adapt_rule_e_reads_the_share():
    from backend.tests.test_adapt import ctx, ids
    out, adj, _ = A.adapt(_gw(), [], ctx(load={"tsb": -14.0, "ctl": 40.0}))  # −35 %
    assert "quality" not in ids(out)
    fat = [a for a in adj if a["rule"] == "fatigue"]
    assert fat and "%" in fat[0]["reason"]
    out, adj, _ = A.adapt(_gw(), [], ctx(load={"tsb": -35.0, "ctl": 200.0}))  # −17.5 %: was cut at −35
    assert "quality" in ids(out) and not [a for a in adj if a["rule"] == "fatigue"]


def test_recommend_easy_day_first_reads_the_share():
    focus = {"buckets": [{"key": "high", "label": "高強度", "state": "short", "pct": 5, "target": [15, 25]}]}
    r = recommend(None, focus, "base", tsb=-12.0, ctl=40.0)                  # −30 %
    assert any("輕鬆" in x and "%" in x for x in r["reasons"])
    r = recommend(None, focus, "base", tsb=-30.0, ctl=150.0)                 # −20 %
    assert not any("強度課之前" in x for x in r["reasons"])


# ---- the week plan (overview.week_plan) ----------------------------------------------------

def test_week_plan_high_ctl_minus_31_points_is_a_hold_not_a_recovery_week(monkeypatch):
    from backend.tests import test_b2b as TB
    today = dt.date(2026, 8, 19)
    # yesterday's start CTL 150 / ATL 200 → TSB ≈ −31 on CTL ≈ 148: −21 %
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: {"date": "2026-08-18", "ctl": 150.0, "atl": 200.0})
    _, _, st, wp = TB._week(today=today, last_week="recovery", tph=1.2, b2b_tph=1.2)
    tsb, ctl = wp["load"]["tsb_today"], wp["load"]["ctl_yesterday"]
    assert tsb < -30 and LG.tsb_share(tsb, ctl) > -0.30
    assert wp["mode"] != "recovery_week"
    assert any("維持量" in w and "%" in w for w in wp["why"])
    form = next(i for i in st.indicators if i.id == "form")
    assert form.extra["share"] == pytest.approx(LG.tsb_share(form.value, form.extra["ctl"]))
    assert form.level == "good"                                                # −21 %: productive, not BAD


# ---- race-day targets (acceptance 1: the light, the projection and the chart agree) -----------

def test_race_bands_are_shares_and_drawn_in_points():
    assert PJ.tsb_range("A") == (0.10, 0.35) and PJ.tsb_range("B") == (-0.05, 0.05) and PJ.tsb_range("C") is None
    r = PJ._race({"date": "2026-11-07", "name": "x", "grade": "A"}, {"ctl": 60.0, "tsb": 12.0, "ctl_prev": 60.0})
    assert r["band"] == pytest.approx([6.0, 21.0])                       # the TSB axis is in points
    assert r["band_pct"] == pytest.approx([0.10, 0.35])
    assert "%" in r["band_text"] and "%" in r["text"]
    low = PJ._race({"date": "2026-11-07", "name": "x", "grade": "B"}, {"ctl": 20.0, "tsb": 0.0, "ctl_prev": 20.0})
    assert low["band"] == pytest.approx([-1.5, 1.5])                     # CTL < 30: of 30


def _view_bands(path: str) -> list[str]:
    v = json.loads((ROOT / path).read_text("utf-8"))
    out = []

    def walk(x):
        if isinstance(x, dict):
            e = x.get("expression")
            if isinstance(e, str) and "tsb" in e and "ctl" in e:
                out.append(e)
            for y in x.values():
                walk(y)
        elif isinstance(x, list):
            for y in x:
                walk(y)
    walk(v)
    return out


@pytest.mark.parametrize("path", ["views/training.json", "views/periodization.json"])
def test_chart_zones_use_the_same_lines_and_floor(path):
    exprs = _view_bands(path)
    assert exprs
    nums = set()
    for e in exprs:
        assert "shift(ctl, 1)" not in e.replace("if(shift(ctl, 1) < 30, 30, shift(ctl, 1))", "")   # the floor
        nums |= {float(x) for x in re.findall(r"(?<![\w.])-?\d*\.\d+", e)}
    assert {-0.3, -0.1, 0.1, 0.35} <= nums and 0.25 not in nums and 0.05 not in nums   # Q1-2: +10 %, not +5 %


def test_pmc_chart_js_bands_match():
    js = (ROOT / "backend/static/pmc_chart.js").read_text("utf-8")
    assert "[0.35," in js and "[0.25," not in js and "30" in js
    assert '[0.1, "#a9a1e6", "grey"]' in js and "[0.05," not in js


def test_chart_expression_equals_the_helper(monkeypatch):
    """The view's share expression, evaluated, is LG.tsb_share of yesterday's CTL — low CTL included."""
    from backend.engine.wko5expr.evaluator import Evaluator
    from backend.tests.test_load_guard import _ev_ds
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: {"date": "2026-09-20", "ctl": 20.0, "atl": 40.0})
    ds = _ev_ds(tss=15.0)
    ev = Evaluator(ds, ds.today - 30, ds.today)
    share = ev.evaluate("tsb / if(shift(ctl, 1) < 30, 30, shift(ctl, 1))")
    tsb, ctl = ev.evaluate("tsb"), ev.evaluate("ctl")
    today = int(ds.today)
    for d in range(today - 8, today + 1):
        assert share.at(d) == pytest.approx(LG.tsb_share(tsb.at(d), ctl.at(d - 1)))
