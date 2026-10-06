"""SP-266: the HR-quality check in the drift window (workout_review.drift_of / aet_test.analyze).
Bad HR makes a strict drift reference-only; nothing is changed or interpolated. Synthetic data only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import aet_test as AT
from backend.engine import drift_agg as DA
from backend.engine import quality_gate as QG
from backend.engine import workout_review as R
from backend.i18n import use_locale
from backend.tests.test_quality_gate import TODAY, _ds, _plan
from backend.tests.test_workout_review import _run


def _steady(minutes=60):
    """A fair 60-min run: 10 km/h, HR 140 → 146 with a ±1.5 bpm wobble (not a flat line)."""
    t = np.arange(0, minutes * 60, 1.0)
    hr = 140.0 + 6.0 * t / t[-1] + 1.5 * np.sin(t / 11.0)
    return t, hr, np.full(len(t), 10.0)


def test_a_clean_run_is_unchanged(monkeypatch):
    t, hr, v = _steady()
    r = R.drift_of(t, hr, v)
    assert r["ok"] and r["tier"] == "test" and not r.get("hr_ref")
    assert r["hr_quality"]["suspect_s"] == 0 and not r["hr_quality"]["downgrade"]
    monkeypatch.setattr(R, "hr_quality_check", lambda *a, **k: None)       # the drift before SP-266
    old = R.drift_of(t, hr, v)
    assert {k: v_ for k, v_ in r.items() if k != "hr_quality"} == {k: v_ for k, v_ in old.items() if k != "hr_quality"}


def test_more_than_3_percent_suspect_seconds_downgrades():
    t, hr, v = _steady()
    for k in range(12):                       # 12 spikes of 10 s in the window: 120 s of ~2900 = 4 %
        a = 900 + k * 200
        hr[a:a + 10] += 40.0
    raw = hr.copy()
    r = R.drift_of(t, hr, v)
    assert r["hr_ref"] and r["hr_quality"]["spike_n"] == 12 and r["hr_quality"]["share"] > R.HRQ_MAX_SHARE
    assert not r["ok"] and r["ref_ok"] and r["tier"] == "ref" and r["drift"] is not None
    assert r["reason"] == "這次心率有 12 段突然跳動，飄移只當參考"
    assert R.basis_drift(r, "pace")[0] is None                               # gates: strict tier only
    assert R.basis_drift(r, "pace", ref=True)[0] == pytest.approx(r["drift"])
    assert np.array_equal(hr, raw)                                           # marked only: the data is untouched
    # 3 spikes (≈ 1 %): flagged, under the threshold — the drift is read as usual
    t, hr, v = _steady()
    for a in (900, 1500, 2100):
        hr[a:a + 10] += 40.0
    r = R.drift_of(t, hr, v)
    assert r["ok"] and not r.get("hr_ref") and r["hr_quality"]["spike_n"] == 3


def test_one_moving_step_downgrades():
    t, hr, v = _steady()
    hr[2400:] -= 20.0                         # the reading drops a level while running and stays there
    r = R.drift_of(t, hr, v)
    assert r["hr_ref"] and r["hr_quality"]["step_n"] == 1 and not r["ok"] and r["ref_ok"]
    assert r["reason"] == "這次心率有 1 次突然跳一階沒有回來，飄移只當參考"


def test_the_text_is_plain_and_translated():
    hq = {"downgrade": True, "spike_n": 2, "step_n": 1, "lock_s": 150, "flat_s": 0}
    # cadence lock is never a downgrade reason (the user, 2026-10-06): not in the text
    assert R.hr_quality_text(hq) == "這次心率有 2 段突然跳動、1 次突然跳一階沒有回來，飄移只當參考"
    assert R.hr_quality_parts(hq)[-1] == "2 分鐘跟著步頻走"                  # still listed as information
    assert R.hr_quality_text(hq, aet=True).endswith("測試結果只當參考，不建議套用")
    assert R.hr_quality_text({**hq, "downgrade": False}) is None
    with use_locale("en"):
        assert R.hr_quality_text({"downgrade": True, "spike_n": 2}) == \
            "This run's HR had 2 sudden spikes: the drift is for reference only"
        assert R.hr_quality_text({"downgrade": True, "spike_n": 1}) == \
            "This run's HR had 1 sudden spike: the drift is for reference only"


def _spiky(day, minutes=80, hr=140.0):
    w = _run(day, minutes=minutes, hr=hr)
    h = np.asarray(w.channels["heartrate"], float) + 1.5 * np.sin(np.arange(len(w.channels["heartrate"])) / 11.0)
    for k in range(15):
        a = 900 + k * 250
        h[a:a + 10] += 40.0
    w.channels["heartrate"] = list(h)
    w.channels["speed"] = list(10.0 + 0.3 * np.sin(np.arange(len(h)) / 7.0))
    return w


def test_a_downgraded_run_unlocks_nothing_and_is_not_an_aet_point():
    plan = _plan(aethr=140, lthr=165)
    w = _spiky(TODAY - dt.timedelta(days=5))
    ds = _ds([w], plan)
    m = R.measure(ds, ds.workouts[0])
    assert m["drift"]["hr_ref"] and m["drift"]["tier"] == "ref"
    assert QG.friel_check(ds, TODAY, 140.0)["state"] == "missing"            # no interval unlock
    assert R.classify(ds, ds.workouts[0])["type"] != "test_aet"              # no AeT test from it
    assert DA.aet_points(ds, TODAY) == []                                    # no threshold write
    # the card says why, in plain words
    res = R.review(ds, ds.workouts[0], "aerobic")
    rows = {s["name"]: s["data"]["value"] for s in res["series"]}
    assert rows["可信度"].startswith("這次心率有 15 段突然跳動") and rows["心率品質"] == rows["可信度"]
    assert any(c.get("id") == "hr_quality" for c in res["cards"])


def test_the_aet_test_is_shown_but_not_offered_to_apply():
    t, hr, v = _steady(80)
    hr = 140.0 + 1.5 * np.sin(t / 11.0) + 4.5 * (t > 900) * (t - 900) / t[-1]   # ~4 % over the block
    ok = AT.analyze(t, hr, v, None, warm_s=900)
    assert ok["ok"] and not ok["hr_ref"]
    for k in range(15):
        a = 1200 + k * 200
        hr[a:a + 10] += 40.0
    r = AT.analyze(t, hr, v, None, warm_s=900)
    assert r["ok"] and r["hr_ref"] and r["hr_quality"]["spike_n"] == 15
    assert AT.lines(r)[-1].startswith("這次心率有 15 段突然跳動") and AT.lines(r)[-1].endswith("不建議套用")


def test_cadence_lock_alone_never_downgrades_but_is_shown():
    t, hr, v = _steady()
    cad = 143.0 + 3.0 * np.sin(t / 7.0)       # (a slow cadence near the HR, so no jump into the lock)
    hr[1200:1500] = cad[1200:1500] + 0.5      # 5 min locked on the cadence: ~10 % of the window
    r = R.drift_of(t, hr, v, cadence_spm=cad)
    hq = r["hr_quality"]
    assert hq["lock_s"] >= 240 and hq["share"] > R.HRQ_MAX_SHARE              # counted as information …
    assert hq["downgrade_share"] <= R.HRQ_MAX_SHARE and not hq["downgrade"]    # … not toward the threshold
    assert r["ok"] and r["tier"] == "test" and not r.get("hr_ref")
    assert "跟著步頻走" in R.hr_quality_parts(hq)[0]
    # with spikes over 3 %, the downgrade's row still mentions the lock as information
    for k in range(12):
        a = 2000 + k * 70
        hr[a:a + 10] += 40.0
    r = R.drift_of(t, hr, v, cadence_spm=cad)
    assert r["hr_ref"] and "跟著步頻走" not in r["reason"]
