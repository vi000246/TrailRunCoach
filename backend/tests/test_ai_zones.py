import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.engine.ai.zones import compute_zones


def test_zones_from_settings():
    z = compute_zones(run_ftp_w=192, lthr=182)
    power = {p["zone"]: p for p in z["power"]}
    hr = {h["zone"]: h for h in z["hr"]}
    # Palladino's 10 running zones (% CP), not Coggan's cycling 7
    assert len(z["power"]) == 10 and len(z["hr"]) == 5
    assert abs(power["3A"]["low_w"] - 0.88 * 192) < 0.5 and abs(power["3A"]["high_w"] - 0.95 * 192) < 0.5
    assert abs(power["5"]["low_w"] - 1.06 * 192) < 0.5
    assert power["1A"]["low_w"] == 0.0
    # zone4 HR threshold: 0.95–1.00 × 182
    assert abs(hr[4]["low_bpm"] - 172.9) < 0.5
    # last zone open upper bound → None
    assert power["7"]["high_w"] is None
    assert hr[5]["high_bpm"] is None


def test_zones_missing_inputs():
    z = compute_zones(run_ftp_w=None, lthr=None)
    assert z["power"] == [] and z["hr"] == []


def test_zones_hr_only():
    z = compute_zones(run_ftp_w=None, lthr=182)
    assert z["power"] == []
    assert len(z["hr"]) == 5
