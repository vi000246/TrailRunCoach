"""Two tiers of drift_of (workout_review): 嚴格 / test (≥ 40 min after the
10-min warm-up, UA) and 參考 / reference (≥ 30 min, 推估). Gates and
thresholds read only the strict tier; display shows the reference tier,
labelled. Synthetic data only — never the user's plan or DB."""
import datetime as dt
import re

import numpy as np
import pytest

from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.engine import workout_review as R
from backend.engine.status import Status
from backend.tests.test_quality_gate import TODAY, _ds, _plan
from backend.tests.test_workout_review import _run, _warmup_run


def _with_power(minutes):
    t, hr, v = _warmup_run(minutes)
    return t, hr, v, np.full(len(t), 200.0)


# ---------------------------------------------------------------------------
# drift_of: the tier by minutes after the warm-up
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("after, tier", [(35, "ref"), (45, "test"), (25, None)])
def test_tier_by_minutes_after_the_warmup(after, tier):
    t, hr, v, p = _with_power(10 + after)
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["tier"] == tier and R.drift_tier(r) == tier
    assert r["ok"] is (tier == "test") and r["pw_ok"] is (tier == "test")
    assert r["ref_ok"] is (tier == "ref") and r["pw_ref_ok"] is (tier == "ref")
    strict, ref = R.basis_drift(r, "pace"), R.basis_drift(r, "pace", ref=True)
    if tier == "test":
        assert strict[0] is not None and strict == ref and "暖身後只有" not in r["reason"]
    elif tier == "ref":
        # the value is there for display, the strict tier still refuses with UA's reason
        assert strict[0] is None and "< 40 分" in strict[1] and "UA 不建議採用" in strict[1]
        assert ref[0] == pytest.approx(r["drift"]) and r["drift"] > 0.01
        assert R.basis_drift(r, "power")[0] is None
        assert R.basis_drift(r, "power", ref=True)[0] == pytest.approx(r["pw_drift"])
        assert r["measured_s"] == pytest.approx(35 * 60, abs=2)
    else:
        assert r["drift"] is None and ref[0] is None and "< 30 分" in r["reason"]


def test_a_hot_run_keeps_its_tier_and_carries_its_band():
    # heat bands: a hot run is kept in both tiers, tagged with its temperature band
    for minutes, tier in ((45, "ref"), (55, "test")):
        t, hr, v = _warmup_run(minutes)
        c = R.drift_of(t, hr, v, temp_c=24.0, temp_src="watch")
        assert c["tier"] == tier and c["temp_band"] == "cool" and not c["heat"]
        r = R.drift_of(t, hr, v, temp_c=27.0, temp_src="watch")
        assert r["tier"] == tier and r["temp_band"] == "warm" and r["heat"]
        assert R.basis_drift(r, "pace", ref=True)[0] == c["drift"]
        # heat_band re-applied on a cached result: idempotent, the drift unchanged
        cool = R.drift_of(t, hr, v)
        hot = R.heat_band(cool, 30.0, "route_weather")
        assert hot["tier"] == tier and hot["temp_band"] == "hot" and hot["temp_src"] == "route_weather"
        assert R.heat_band(hot, 30.0, "route_weather") == hot


@pytest.mark.parametrize("t, band", [(None, "none"), (18.0, "cool"), (25.0, "cool"), (25.1, "warm"),
                                     (28.0, "warm"), (28.1, "hot"), (34.0, "hot")])
def test_temperature_band_cut_offs(t, band):
    assert R.temp_band(t) == band
    assert R.is_heat(band) == (band in ("warm", "hot"))


@pytest.mark.parametrize("kw, word", [(dict(climb_m_per_km=30.0), "有坡"), (dict(trail=True), "有坡")])
def test_other_refusals_apply_to_the_reference_tier(kw, word):
    t, hr, v = _warmup_run(45)
    r = R.drift_of(t, hr, v, **kw)
    assert r["tier"] is None and word in r["reason"]
    # a fast finish too
    t, hr, v = _warmup_run(45)
    v[t > 600 + 0.9 * (t[-1] - 600)] *= 1.08
    r = R.drift_of(t, hr, v)
    assert r["tier"] is None and "快速結尾" in r["reason"]


# ---------------------------------------------------------------------------
# gates and thresholds ignore the reference tier
# ---------------------------------------------------------------------------

def _as_ref(monkeypatch):
    """measure() with every fair drift demoted to the reference tier."""
    real = R.measure

    def fake(ds, w):
        m = real(ds, w)
        if m and (m.get("drift") or {}).get("ok"):
            m = {**m, "drift": {**m["drift"], "ok": False, "pw_ok": False, "ref_ok": True,
                                "pw_ref_ok": True, "tier": "ref"}}
        return m
    monkeypatch.setattr(R, "measure", fake)


def test_gates_and_the_aet_test_ignore_the_reference_tier(monkeypatch):
    plan = _plan(aethr=140, lthr=165)
    flat = _run(TODAY - dt.timedelta(days=5), minutes=80, hr=140.0)
    ds = _ds([flat], plan)
    assert QG.friel_check(ds, TODAY, 140.0)["state"] == "unlocked"
    steady = _run(TODAY - dt.timedelta(days=3), minutes=70, hr=135.0)
    assert R.classify(_ds([steady]), _ds([steady]).workouts[0])["type"] == "test_aet"
    _as_ref(monkeypatch)
    assert QG.friel_check(_ds([flat], plan), TODAY, 140.0)["state"] == "missing"
    long90 = _run(TODAY - dt.timedelta(days=4), minutes=100, hr=135.0)
    assert QG.xu_check(_ds([long90]), TODAY)["state"] == "missing"
    ds2 = _ds([steady])
    assert R.classify(ds2, ds2.workouts[0])["type"] != "test_aet"     # no AeT test from a reference drift


def test_the_drift_indicator_shows_the_reference_tier_but_never_warns_on_it():
    # 45′ runs (35′ after the warm-up), HR 115 → 165: reference-tier drift ≈ 12 %
    ws = [_run(TODAY - dt.timedelta(days=d), minutes=45, hr=115.0, hr_end=165.0) for d in (3, 6, 9)]
    for w in ws:
        w.metrics["tss"] = 40.0
    plan = _plan(lthr=165, day="2026-09-01")
    ds = _ds(ws, plan)
    pts = R.drift_series(ds, TODAY)
    assert len(pts) == 3 and all(p["drift"] is None for p in pts)          # strict: nothing
    pts = R.drift_series(ds, TODAY, ref=True)
    assert [p["tier"] for p in pts] == ["ref"] * 3 and all(p["drift"] > 0.10 for p in pts)
    assert R.drift_streak(ds, TODAY)["streak"] == 0                        # the legacy streak stays strict
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    d = next(i for i in st.indicators if i.id == "drift")
    assert d.extra["ref"] == 3 and d.extra["test"] == 0 and d.extra["ref_label"] == R.REF_LABEL
    # plain words (owner 2026-10-02): 「飄很多 · 12.0%」, the caveat in words, the method only in the ?
    assert d.text.startswith("飄很多 · ") and d.text.endswith("%") and "±" not in d.text
    assert "都是比較短的跑步，只當參考" in d.why and "推估" in d.extra["ref_tip"]
    assert d.extra["verdict_level"] == "bad" and "方法：" in d.extra["tip"]
    for word in ("Pa:HR", "±", "pp", "標準誤", "參考級"):
        assert word not in d.text + d.verdict + d.why, word
    assert d.level != "bad" and not d.action                               # gate levels read strict only


def test_the_card_labels_a_reference_drift_with_a_hover():
    ds = _ds([_run(TODAY - dt.timedelta(days=2), minutes=45, power=200.0)])
    w = ds.workouts[0]
    for basis, name in (("pace", "心率飄移（配速）"), ("power", "心率飄移（功率）")):
        rows = {s["name"]: s["data"] for s in R.review(ds, w, "aerobic", basis=basis)["series"]}
        # 「穩定 · 0.0%（…）」: verdict word + %, the caveat in its own plain row, the method last in the ?
        assert re.match(r"(穩定|有點飄|飄很多) · -?\d+\.\d%（", rows[name]["value"]) and "±" not in rows[name]["value"]
        assert rows[name]["tip"].startswith(R.REF_TIP) and rows[name]["tip"].split("\n")[-1].startswith("方法：")
        assert rows["可信度"]["value"] == R.REF_LABEL == "暖身後不到 40 分鐘，只當參考"
        verdict = " ".join(s["data"]["value"] for s in R.review(ds, w, "aerobic", basis=basis)["series"]
                           if s["name"] in ("判讀", ""))
        assert "只當參考" in verdict
    ds = _ds([_run(TODAY - dt.timedelta(days=2), minutes=52)])
    rows = {s["name"]: s["data"] for s in R.review(ds, ds.workouts[0], "aerobic")["series"]}
    # the strict tier's hover: the plain precision note, then the method with this run's ± (drift v2)
    tip = rows["心率飄移（配速）"]["tip"]
    assert tip.startswith(R.SE_TIP.split("\n")[0]) and "方法：" in tip and "個百分點" in tip
    assert rows["可信度"]["value"] == "暖身後跑滿 40 分鐘，可以判讀"


def test_aerobic_lines_keep_the_aet_test_bands_strict():
    base = {"aet": 140.0, "avg_hr": 135.0, "hr_s": 3000, "over_aet_s": 0, "category": "road"}
    ref = {**base, "drift": {"ok": False, "ref_ok": True, "tier": "ref", "drift": 0.042, "hr1": 146.0,
                             "reason": "暖身後只有 35 分鐘（< 40 分，UA 不建議採用），飄移不採用"}}
    assert not any("就是 AeT" in ln for ln in R.aerobic_lines("test_aet", ref))
    assert any("< 40 分" in ln for ln in R.aerobic_lines("test_aet", ref))
    easy = R.aerobic_lines("easy", ref)
    assert any("心率飄移 穩定 · 4.2%" in ln for ln in easy) and any(R.REF_LABEL in ln for ln in easy)
