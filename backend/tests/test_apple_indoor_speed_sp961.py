"""
SP-961: an indoor Apple Health run (no route) carries the speed the watch measured
(HKQuantityTypeIdentifierRunningSpeed) on its points, so its pace shows — files/apple_health.py.
Q1 (b): each point's distance is the speed integrated, scaled to the workout's total, so the
per-km splits show and the total stays the 「健身」app's. With no speed nothing is made up (SP-833).
All synthetic (SP-833's export.xml builder plus speed Records): no real export.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np

from backend.files import apple_health as AH
from backend.files.fit_to_channels import fit_to_channels
from backend.sync import upload as UP
from backend.tests.test_apple_power_cadence_sp912 import PHONE, WATCH, _rec
from backend.tests.test_export_formats_sp833 import _apple_xml, _reader
from backend.tests.test_export_import_sp349 import _gpx
from backend.tests.test_fit_upload_sp323 import START

INDOOR = START + timedelta(days=1)


def _records() -> str:
    """The route run (START, 600 s): the watch's speed every 5 s (2.5 m/s); the indoor run (a day
    later, 900 s): the watch's speed every 5 s — 3.0 m/s the first 450 s, 12.6 km/hr (3.5 m/s) after
    — and the phone's over the same time (another source, 1.0 m/s); speed outside both, of a wrong
    unit, too fast."""
    s = ""
    for i in range(0, 600, 5):
        t = START + timedelta(seconds=i)
        s += _rec("RunningSpeed", WATCH, t, t, 2.5, "m/s")
    for i in range(0, 900, 5):
        t = INDOOR + timedelta(seconds=i)
        s += (_rec("RunningSpeed", WATCH, t, t, 3.0, "m/s") if i < 450
              else _rec("RunningSpeed", WATCH, t, t + timedelta(seconds=2), 12.6, "km/hr"))
        s += _rec("RunningSpeed", PHONE, t, t, 1.0, "m/s")
    out = INDOOR - timedelta(hours=5)
    s += _rec("RunningSpeed", WATCH, out, out, 3.3, "m/s")
    s += _rec("RunningSpeed", WATCH, INDOOR, INDOOR, 3.0, "count/min")             # not a speed
    s += _rec("RunningSpeed", WATCH, INDOOR, INDOOR, 25.0, "m/s")                  # 90 km/h: not running
    return s


def _xml() -> bytes:
    run_ = {"type": "HKWorkoutActivityTypeRunning", "start": START, "end": START + timedelta(seconds=598),
            "km": 1.8, "route": "route_a.gpx"}
    indoor = {"type": "HKWorkoutActivityTypeRunning", "start": INDOOR, "end": INDOOR + timedelta(seconds=900),
              "stats_km": 2.9}
    hr = [(INDOOR + timedelta(seconds=i), 150) for i in range(0, 900, 5)]
    xml = _apple_xml([run_, indoor], hr, extra=_records())
    return xml.replace(b'sourceName="Apple Watch" startDate', f'sourceName="{WATCH}" startDate'.encode())


def test_an_indoor_run_gets_its_own_watchs_speed(tmp_path):
    res = AH.scan_export(_reader(_xml())[0], tmp_path / "s")
    run_, indoor = res["workouts"]
    # the watch's only (not the phone's), not the wrong unit, not the too fast one, not outside it
    assert len(indoor["speed"]) == 180
    assert {v for _t, v in indoor["speed"][:90]} == {3.0} and {v for _t, v in indoor["speed"][90:]} == {3.5}
    assert indoor["speed"][0][0] == INDOOR.timestamp()
    assert indoor["speed"][90][0] == INDOOR.timestamp() + 450 + 1                    # the middle of its span
    assert len(run_["speed"]) == 120
    assert not (tmp_path / "s" / "more.bin").exists()


def test_the_indoor_fit_shows_the_measured_pace_and_keeps_the_workouts_distance(tmp_path):
    _run, indoor = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    fit = AH.to_fit(indoor, None)
    w = UP._parse(fit, "a.fit")
    assert abs(w.speed_ms[100] - 3.0) < 0.01 and abs(w.speed_ms[700] - 3.5) < 0.01
    assert w.heart_rate_bpm[100] == 150
    # the total stays Apple's (2.9 km), not the integral (3.0×450 + 3.5×450 ≈ 2.92 km)
    assert abs(w.total_distance_m - 2900) < 1
    ch = fit_to_channels(fit)                                     # what the activity page reads: the pace
    sp = [v for v in ch.channels["speed"] if v is not None]
    assert sp and abs(sp[50] - 10.8) < 0.05 and abs(sp[-10] - 12.6) < 0.05
    # the distance channel (km; WKO5's model starts it at the first sample after the start, 5 s in:
    # ~15 m less) — the splits run to about Apple's total
    assert abs(ch.channels["elapseddistance"][-1] - (2.9 - 0.015)) < 0.002


def test_each_point_has_its_distance_from_the_speed_scaled_to_the_workouts(tmp_path):
    _run, indoor = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    w = UP._parse(AH.to_fit(indoor, None), "a.fit")
    t, d = np.asarray(w.time_s, dtype=float), np.asarray(w.distance_m, dtype=float)
    assert d[0] == 0 and np.all(np.diff(d) >= 0) and abs(d[-1] - 2900) < 0.02
    # the distance follows the measured speed: 3.0 m/s, then 3.5 m/s — the same factor on both halves
    first = (np.interp(400, t, d) - np.interp(100, t, d)) / 300
    second = (np.interp(800, t, d) - np.interp(500, t, d)) / 300
    assert abs(second / first - 3.5 / 3.0) < 0.001
    scale = 2900 / (3.0 * 450 + 3.5 * 450)                          # ≈ 0.99: the integral was ~2.92 km
    assert abs(first - 3.0 * scale) < 0.02 and abs(second - 3.5 * scale) < 0.02
    # the measured speed itself is not scaled
    assert abs(w.speed_ms[100] - 3.0) < 0.01
    # the first kilometre: about 1000 / (3.0 × scale) s in
    assert abs(np.interp(1000, d, t) - t[0] - 1000 / (3.0 * scale)) < 3


def test_a_gap_in_the_speed_adds_no_distance_and_no_total_keeps_the_integral():
    t0 = INDOOR.timestamp()
    speed = [[t0 + i, 3.0] for i in range(0, 300, 5)] + [[t0 + i, 3.0] for i in range(600, 900, 5)]
    hr = [[t0 + i, 140] for i in range(0, 900, 5)]
    run_ = {"type": "HKWorkoutActivityTypeRunning", "start": t0, "end": t0 + 900, "distance_m": 1800.0,
            "hr": hr, "power": [], "cad": [], "rpm": [], "speed": speed}
    w = UP._parse(AH.to_fit(run_, None), "a.fit")
    t, d = np.asarray(w.time_s, dtype=float), np.asarray(w.distance_m, dtype=float)
    # 300 s → 600 s no speed sample (past 30 s of one): no distance there, the rest scaled to 1800 m
    assert np.interp(560, t, d) - np.interp(340, t, d) == 0 and abs(d[-1] - 1800) < 0.02
    assert abs(w.total_distance_m - 1800) < 1
    # no workout total (None): the integral as it is — a point takes a sample up to 30 s away (to
    # 325 s, from 570 s), half a step on each edge: 3.0 × (325 + 2.5 + 2.5 + 325)
    w = UP._parse(AH.to_fit({**run_, "distance_m": None}, None), "a.fit")
    assert abs(w.total_distance_m - 1965) < 0.05
    assert abs(np.nanmax(np.asarray(w.distance_m, dtype=float)) - 1965) < 0.05


def test_a_run_with_a_route_keeps_the_routes_speed(tmp_path):
    run_, _indoor = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    gpx = _gpx(0, step=1)
    assert run_["speed"] and AH.to_fit(run_, gpx) == AH.to_fit({**run_, "speed": []}, gpx)


def test_an_indoor_run_without_speed_is_as_before(tmp_path):
    _run, indoor = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    bare = {**indoor, "speed": []}
    old = {k: v for k, v in bare.items() if k not in ("speed", "rpm")}           # SP-912's workout
    assert AH.to_fit(bare, None) == AH.to_fit(old, None)
    w = UP._parse(AH.to_fit(bare, None), "a.fit")
    assert not np.nanmax(np.nan_to_num(w.speed_ms)) > 0 and abs(w.total_distance_m - 2900) < 1


def test_speed_survives_a_pass_in_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 2048)
    monkeypatch.setattr(AH, "CHECK_BYTES", 10_000)
    xml = _xml()
    whole = AH.scan_export(_reader(xml)[0], tmp_path / "one")
    s, at, res = tmp_path / "parts", 0, None
    for _ in range(400):
        res = AH.scan_export(_reader(b"\x00" * at + xml[at:])[0], s, key="k", budget=12_000)
        if not res.get("paused"):
            break
        at = res["pos"]
    assert at > 0 and {k: v for k, v in res.items() if k != "paused"} == whole
