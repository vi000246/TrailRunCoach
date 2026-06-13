import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.engine.ai.zones import compute_zones


def test_zones_from_settings():
    z = compute_zones(run_ftp_w=192, lthr=182)
    power = {p["zone"]: p for p in z["power"]}
    hr = {h["zone"]: h for h in z["hr"]}
    assert len(z["power"]) == 7 and len(z["hr"]) == 5
    # zone4 threshold power: 0.90–1.05 × 192 = 172.8–201.6
    assert abs(power[4]["low_w"] - 172.8) < 0.5
    assert abs(power[4]["high_w"] - 201.6) < 0.5
    assert power[4]["name"] == "Threshold"
    # zone4 HR threshold: 0.95–1.00 × 182
    assert abs(hr[4]["low_bpm"] - 172.9) < 0.5
    # last zone open upper bound → None
    assert power[7]["high_w"] is None
    assert hr[5]["high_bpm"] is None


def test_zones_missing_inputs():
    z = compute_zones(run_ftp_w=None, lthr=None)
    assert z["power"] == [] and z["hr"] == []


def test_zones_hr_only():
    z = compute_zones(run_ftp_w=None, lthr=182)
    assert z["power"] == []
    assert len(z["hr"]) == 5
