"""
SP-724: past hikes marked 跟團 / 自己 — the suggestion gets the stops as a third vote; the group's pace is
learnt apart from the altitude and the pack (capacity.group_factor: actual ÷ the capacity model's time of
the same day), used for the 跟團時間 only when the back-test found it better on the athlete's own group days
(backtest.group_factor_flag), with the altitude / pack residual check. Synthetic data and tmp files only.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from pytest import approx

from backend.engine import group_hint as GH
from backend.engine.racepower import backtest as BT
from backend.engine.racepower import capacity as CAP
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_capacity import _synthetic_cap, _v1
from backend.tests.test_racepower_v2 import synthetic_track

ROOT = Path(__file__).resolve().parents[1]


# ---- the suggestion: three votes ------------------------------------------------------------

# SP-716's diary-checked group trips (hr_pct, vam_ratio, stop ÷ moving) — no dates, only the ratios
DIARY_GROUP = [(0.06, 0.77, 1.22), (0.04, 0.91, 2.01), (0.54, 0.64, 0.85), (0.04, 0.68, 1.49)]


def test_the_stops_vote_finds_all_four_diary_group_trips():
    assert [GH.suggest(h, v) for h, v, _s in DIARY_GROUP].count("group") == 2      # the two-sign rule
    assert [GH.suggest(h, v, s) for h, v, s in DIARY_GROUP] == ["group"] * 4
    assert GH.suggest(0.48, 0.92, 0.44) == "solo"            # the owner's average trail run (SP-716)
    assert GH.suggest(0.40, 0.95, 0.90) is None              # HR between: one vote each side
    assert GH.suggest(0.60, 0.80, 0.50) == "solo"            # 2 solo votes beat 1 group vote
    assert GH.suggest(None, 0.8, 1.2) is None                # no HR percentile: nothing, as before
    assert GH.suggest(0.20, 0.80, float("nan")) == "group"   # no stops: the two-sign rule


def test_stop_share_sums_the_days(monkeypatch):
    from backend.engine.racepower import athlete as A
    monkeypatch.setattr(A, "nonmoving_day_rows", lambda ds, w: [{"moving_s": 3600, "stopped_s": 1800},
                                                                {"moving_s": 7200, "stopped_s": 7200}, None])
    assert GH.stop_share(None, None) == approx(9000 / 10800)
    monkeypatch.setattr(A, "nonmoving_day_rows", lambda ds, w: (_ for _ in ()).throw(ValueError("no samples")))
    assert GH.stop_share(None, None) is None


# ---- the 跟團係數 ---------------------------------------------------------------------------

class AltPackCap:
    """A capacity model with a known altitude and pack effect: v = 1.2 m/s × (1 − 0.08 per km above 0)
    × (1 − 0.01 per kg), slower uphill (× e^(−3g) on climbs)."""

    def v(self, g, load, eta=1.0, z=None, h=0.0, n_day=1, accl="unacclimatised"):
        a = 1.0 - 0.08 * max(0.0, (z or 0.0)) / 1000.0
        return 1.2 * a * (1.0 - 0.01 * load) * (math.exp(-3.0 * g) if g > 0 else 1.0)


def _day(trip, z, pack, factor, solo=False, km=10.0, gain=800.0):
    segs = [[0.16, km * 500.0, z], [-0.16, km * 500.0, z]]
    t_cap = CAP.day_capacity_time(AltPackCap(), segs, pack, 1)
    return {"trip": trip, "date": f"2026-0{1 + trip % 9}-01", "day": 1, "segs": segs, "t_act": t_cap * factor,
            "pack_kg": pack, "solo": solo, "z_mean": z, "km": km, "gain_m": gain}


def test_day_capacity_time_adds_the_segments_at_the_model_speed():
    segs = [[0.0, 1200.0, 0.0], [0.1, 600.0, 1000.0]]
    cap = AltPackCap()
    want = 1200.0 / cap.v(0.0, 5.0, z=0.0) + 600.0 / cap.v(0.1, 5.0, z=1000.0)
    assert CAP.day_capacity_time(cap, segs, 5.0) == approx(want)


def test_group_factor_is_the_group_pace_whatever_the_altitude_and_pack():
    days = [_day(0, 500, 4, 1.30), _day(1, 3200, 10, 1.30), _day(2, 1800, 6, 1.30), _day(3, 3000, 12, 1.30),
            _day(4, 900, 3, 0.95, solo=True)]
    gf = CAP.group_factor(AltPackCap(), days)
    assert gf["median"] == approx(1.30) and gf["p25"] == approx(1.30) and gf["n"] == 4 and gf["trips"] == 4
    assert all(r["r"] == approx(1.30) for r in gf["rows"])            # the solo day is not in it
    assert CAP.group_factor(AltPackCap(), days[:2]) is None            # < 3 group days
    assert CAP.group_factor(None, days) is None


def test_plan_hike_group_time_from_the_factor_only_when_given():
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    c = CO.build_course(tr)
    cap = _synthetic_cap()
    inp = {"hiking": {"days": [{"ep_per_h": x, "days": 1, "solo": False} for x in (2.4, 2.8, 3.0, 3.2, 3.5, 3.9)]}}
    base = {"mode": "auto", "day_splits_km": [8.0]}
    old = PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp=inp, opts=dict(base),
                       validated={}, capacity=cap)
    assert old["summary"]["group_time_s"]["method"] == "eph"
    gf = {"median": 1.3, "p25": 1.2, "p75": 1.45, "n": 6, "trips": 4}
    new = PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp=inp,
                       opts={**base, "group_factor": gf}, validated={}, capacity=cap)
    s, g = new["summary"], new["summary"]["group_time_s"]
    assert g["method"] == "factor" and g["factor"] == [1.2, 1.3, 1.45] and g["eph"] is None
    assert g["p50"] == approx(s["capacity_time_s"] * 1.3) and g["p25"] == approx(s["capacity_time_s"] * 1.2)
    assert s["main"] == "group" and s["capacity_time_s"] == approx(old["summary"]["capacity_time_s"])
    # each day: that day's capacity moving time × the factor
    for d in new["days"]:
        assert d["group_h"] == approx(d["moving_h"] * 1.3) and d["group_h_range"] == approx([d["moving_h"] * 1.2, d["moving_h"] * 1.45])
    assert any("跟團係數 ×1.30" in w for w in new["warnings"])
    # a manual course (no capacity segments): the group days' EP/h, as before
    man = CO.manual_course(16.0, 800.0)
    m = PL.plan_hike(v1=_v1(), course=man, hike_speed=GM.fit_hike_speed([]), inp=inp,
                     opts={"mode": "auto", "group_factor": gf}, validated={}, capacity=cap)
    assert m["summary"]["group_time_s"]["method"] == "eph"


# ---- the back-test ------------------------------------------------------------------------

def test_residual_slopes_recover_what_the_model_misses():
    rng = np.random.default_rng(3)
    rows = []
    for i in range(30):
        z, p = rng.uniform(300, 3500), rng.uniform(3, 12)
        rows.append({"z_mean": z, "pack_kg": p, "r": 1.3 * math.exp(0.05 * z / 1000.0 - 0.01 * p + rng.normal(0, 0.01))})
    s = BT._resid_slopes(rows)
    assert s["n"] == 30
    assert s["alt_pct_per_km"] == approx((math.exp(0.05) - 1) * 100, abs=0.6)
    assert s["pack_pct_per_kg"] == approx((math.exp(-0.01) - 1) * 100, abs=0.2)
    one_pack = [{**r, "pack_kg": 4.0} for r in rows]               # no pack spread: the altitude only
    assert BT._resid_slopes(one_pack)["pack_pct_per_kg"] is None
    assert BT._resid_slopes(rows[:4]) is None


def test_group_factor_backtest_beats_the_eph_when_altitude_and_pack_differ(monkeypatch):
    from backend.engine.racepower import athlete as A
    noise = [1, 1, -1, -1, 1, 1, -1, -1]              # ± 2 %, balanced over the high and the low days
    days = [_day(i, z, p, 1.3 + 0.02 * noise[i]) for i, (z, p) in
            enumerate([(400, 4), (3300, 11), (800, 5), (3000, 10), (1500, 6), (3500, 12), (600, 4), (2800, 9)])]
    days.append(_day(9, 1000, 4, 1.0, solo=True))
    monkeypatch.setattr(A, "group_day_rows", lambda ds, today, tags=None, pack_of=None: days)
    out = BT.group_factor_backtest(None, None, {"pack_of": None}, lambda trip: AltPackCap(), AltPackCap())
    assert out["n"] == 8 and out["trips"] == 8
    assert out["factor"]["median_abs"] < 0.05 < out["eph"]["median_abs"] and out["better"] is True
    assert out["factor_full"]["median"] == approx(1.3, abs=0.03)
    assert out["residual"]["n"] == 8
    assert out["solo_capacity_err"]["n"] == 1 and out["solo_capacity_err"]["bias"] == approx(0.0, abs=1e-9)
    # the model has the right altitude and pack effect: nothing left in the ratios (exact days)
    exact = [{**d, "t_act": d["t_act"] / (1.3 + 0.02 * noise[i]) * 1.3} for i, d in enumerate(days[:8])]
    monkeypatch.setattr(A, "group_day_rows", lambda ds, today, tags=None, pack_of=None: exact)
    res = BT.group_factor_backtest(None, None, {"pack_of": None}, lambda trip: AltPackCap(), AltPackCap())["residual"]
    assert res["alt_pct_per_km"] == approx(0.0, abs=1e-6) and res["pack_pct_per_kg"] == approx(0.0, abs=1e-6)


def test_the_flag_reads_the_stored_capacity_back_test(tmp_path):
    p = tmp_path / "bt.json"
    assert BT.group_factor_flag(p) is False                           # nothing stored
    BT.store_capacity({"passed": False, "group_factor": {"better": False}}, p)
    assert BT.group_factor_flag(p) is False
    BT.store_capacity({"passed": False, "group_factor": {"better": True}}, p)
    assert BT.group_factor_flag(p) is True


# ---- the page --------------------------------------------------------------------------------

def test_activity_editor_has_the_group_row_and_the_calculator_tile_reads_the_method():
    html = (ROOT / "static" / "activity.html").read_text("utf-8")
    assert "data-grp-fld" in html and "function grpWhy" in html and 'T("help.grp")' in html
    assert "ed.querySelectorAll(\"button[data-grp-ed]\")" in html and "data-grp-ed=" in html
    for loc in ("zh-TW", "en"):
        act = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text("utf-8"))
        assert act["help.grp"] and "{stop}" in act["grp.why"] and "{vam}" in act["grp.why"]
        rp = json.loads((ROOT / "static" / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert "{f}" in rp["gf.sub"] and rp["gf.tip"]
    rp_html = (ROOT / "static" / "racepower.html").read_text("utf-8")
    assert 'g.method === "factor"' in rp_html and 'T("gf.sub"' in rp_html
