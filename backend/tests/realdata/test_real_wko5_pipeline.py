"""
End-to-end golden tests against the user's real WKO5 data:

    original FIT (embedded in .wko4) -> fit_to_channels -> NP / hrTSS
        -> must equal WKO5's own stored values
    WKO5 dataset -> TSS rules -> tl() -> CTL/ATL
        -> locked to the value derived independently from WKO5's disassembly
    .wko5chart dashboards -> render JSON -> no unexpected errors

Skipped when the athlete folder / view exports are absent.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import datetime as dt
import warnings
from pathlib import Path

import pytest

from backend.tests.realdata._paths import ATHLETE_DIR

ROOT = Path(__file__).resolve().parents[3]
SEASON = ROOT / "WKO5 Season View" / "WKO5 Season View.wko5chart"
TODAY = dt.date(2026, 9, 29)

needs_data = pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
pytestmark = pytest.mark.golden


@pytest.fixture(scope="module")
def ds():
    from backend.engine.wko5expr.dataset import Dataset
    return Dataset(ATHLETE_DIR, today=TODAY)


@needs_data
def test_fit_to_np_matches_wko5_when_the_time_axis_matches(ds):
    """COROS/Garmin path: FIT -> our channels -> our NP == WKO5's stored NP."""
    from backend.files.fit_to_channels import fit_to_channels
    from backend.engine.algorithms.wko5_power import normalized_power
    warnings.filterwarnings("ignore")
    checked = scanned = 0
    for w in ds.workouts:
        if w.metrics.get("np") is None:
            continue
        f = ds.wko4(w.idx)
        if f is None or f.original_type != "fit" or not f.original_bytes:
            continue
        scanned += 1
        if scanned > 120:
            break
        fc = fit_to_channels(f.original_bytes)
        wt = f.channels["elapsedtime"].values
        if fc.elapsedtime != wt or "power" not in fc.channels:
            continue  # time-axis rules still being finished (see test_fit_to_channels.py)
        np_, dur = normalized_power(fc.elapsedtime, fc.channels["power"])
        assert np_ == pytest.approx(w.metrics["np"], rel=1e-9), w.entry.file
        assert dur == w.metrics["tssduration"], w.entry.file
        checked += 1
        if checked >= 25:
            break
    # Raise once fit_to_channels' time-axis rules are complete (FIT parity work).
    assert checked >= 3


@needs_data
def test_fit_to_hrtss_matches_wko5_when_the_time_axis_matches(ds):
    from backend.files.fit_to_channels import fit_to_channels
    from backend.engine.algorithms.wko5_hr import hr_tss
    warnings.filterwarnings("ignore")
    checked = 0
    for w in ds.workouts[::7]:
        if w.metrics.get("hrtss") is None:
            continue
        f = ds.wko4(w.idx)
        if f is None or f.original_type != "fit" or not f.original_bytes:
            continue
        fc = fit_to_channels(f.original_bytes)
        if fc.elapsedtime != f.channels["elapsedtime"].values or "heartrate" not in fc.channels:
            continue
        s, iff = hr_tss(fc.elapsedtime, fc.channels["heartrate"], ds.sport_setting("thr", w))
        assert s == pytest.approx(w.metrics["hrtss"], rel=1e-9), w.entry.file
        assert iff == pytest.approx(w.metrics["hrif"], rel=1e-9), w.entry.file
        checked += 1
    assert checked >= 10


@needs_data
def test_pmc_matches_wko5s_own_athlete_bar_snapshot(ds):
    """The strongest end-to-end check: our CTL/ATL/TSB on the snapshot date vs
    the values WKO5 itself wrote into the athlete file (3403)."""
    from backend.engine.wko5expr.evaluator import Evaluator
    snap = ds.athlete.pmc_snapshot
    ev = Evaluator(ds, ds.today - 30, ds.today)
    ctl = ev.evaluate("tl(tss,ctlconstant)").at(ds.today)
    atl = ev.evaluate("tl(tss,atlconstant)").at(ds.today)
    assert ctl == pytest.approx(snap["ctl"], abs=0.02), "CTL drifted from WKO5"
    assert atl == pytest.approx(snap["atl"], abs=0.02), "ATL drifted from WKO5"
    assert ev.evaluate("tsb").at(ds.today) == pytest.approx(snap["tsb"], abs=0.03)


@needs_data
def test_trainingpeaks_tss_overrides_hrtss(ds):
    """WKO5 prefers a TP-synced TSS over its own hrTSS. The 49 h Mountaineering
    day is the clearest case: hrTSS ~1014 vs TP's 65."""
    big = [w for w in ds.workouts if w.sport_type == "mountaineering"
           and w.metrics.get("duration", 0) and w.metrics["duration"] > 100000]
    assert big, "expected the 49 h mountaineering workout"
    w = big[0]
    assert w.metrics["hrtss"] > 500
    assert w.metrics["tss"] < 200, "TP TSS should have replaced hrTSS"


@needs_data
def test_tss_source_follows_wko5_branch_order(ds):
    from backend.engine.algorithms.wko5_power import power_tss
    kinds = {"power": 0, "rtss": 0, "hrtss": 0}
    for w in ds.workouts:
        m = w.metrics
        if m["tssduration"] and m["np"] is not None:
            assert m["tss"] == pytest.approx(power_tss(m["np"], m["tssduration"], w.entry.ftp))
            kinds["power"] += 1
        elif w.sport == "run" and m["ngp"] and m["tss"] is not None and m["tss"] != m["hrtss"]:
            kinds["rtss"] += 1
        elif m["tss"] is not None:
            kinds["hrtss"] += 1      # hrTSS, or a TP-synced TSS in its place
    assert kinds["power"] > 300 and kinds["hrtss"] > 500


@needs_data
@pytest.mark.skipif(not SEASON.exists(), reason="season view export not present")
def test_load_and_recovery_dashboard_renders(ds):
    """負荷與恢復: every series evaluates except the known PD-model gap (TIS)."""
    from backend.files.wko5chart_reader import read_view
    from backend.engine.wko5expr.render import render_chart
    d = next(x for x in read_view(SEASON)["dashboards"] if x["title"] == "負荷與恢復")
    errors = []
    for c in d["charts"]:
        res = render_chart(c, ds, ds.today - 365, ds.today)
        for s in res["series"]:
            if s["data"]["kind"] == "error":
                errors.append((c["title"], s["name"], s["data"]["message"]))
    unexpected = [e for e in errors if "tisaerobic" not in e[2] and "tisanaerobic" not in e[2]]
    assert not unexpected, unexpected
