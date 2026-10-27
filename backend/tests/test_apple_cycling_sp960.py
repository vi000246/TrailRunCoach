"""
SP-960: Apple Health's import also carries a ride's power (HKQuantityTypeIdentifierCyclingPower) and
cadence (HKQuantityTypeIdentifierCyclingCadence, revolutions per minute) — files/apple_health.py, by
SP-912's way of the running power and the steps. All synthetic (SP-833's export.xml builder plus
cycling Records): no real export.
"""
from __future__ import annotations

import tracemalloc
from datetime import timedelta

from backend.files import apple_health as AH
from backend.sync import upload as UP
from backend.tests.test_apple_power_cadence_sp912 import PHONE, WATCH, _rec
from backend.tests.test_export_formats_sp833 import _apple_xml, _reader
from backend.tests.test_export_import_sp349 import _gpx
from backend.tests.test_fit_upload_sp323 import START


def _records(far=0) -> str:
    """The ride (START, 600 s): the watch's power every 5 s (210 W) and cadence every 5 s (88 rpm,
    over a 5 s span), the phone's steps over the same minutes (pocket steps, not the cadence) and a
    second power meter's power (another source); the indoor ride (a day later, 900 s): power and
    cadence of another app only (Zwift); cycling samples outside both, of a wrong unit, too long a
    span. `far`: that many more cadence samples a month before (the memory test)."""
    s = ""
    for i in range(0, 600, 5):
        t = START + timedelta(seconds=i)
        s += _rec("CyclingPower", WATCH, t, t, 210, "W")
        s += _rec("CyclingPower", "Stages", t, t, 400, "W")
        s += _rec("CyclingCadence", WATCH, t, t + timedelta(seconds=5), 88, "count/min")
    for i in range(0, 600, 60):
        t = START + timedelta(seconds=i)
        s += _rec("StepCount", PHONE, t, t + timedelta(seconds=60), 40, "count")
    indoor = START + timedelta(days=1)
    for i in range(0, 900, 3):
        t = indoor + timedelta(seconds=i)
        s += _rec("CyclingPower", "Zwift", t, t, 180, "W")
        s += _rec("CyclingCadence", "Zwift", t, t, 92, "count/min")
    out = START - timedelta(hours=5)
    s += _rec("CyclingPower", WATCH, out, out, 300, "W")
    s += _rec("CyclingCadence", WATCH, out, out, 70, "count/min")
    s += _rec("CyclingCadence", WATCH, START, START, 88, "count/s")                   # not per minute
    s += _rec("CyclingPower", WATCH, START, START, 210, "kcal")                       # not watts
    s += _rec("CyclingCadence", WATCH, START, START + timedelta(seconds=700), 60, "count/min")   # too long
    old = START - timedelta(days=30)
    s += "".join(_rec("CyclingCadence", WATCH, old + timedelta(seconds=i), old + timedelta(seconds=i), 85,
                      "count/min") for i in range(far))
    return s


def _xml(far=0) -> bytes:
    ride = {"type": "HKWorkoutActivityTypeCycling", "start": START, "end": START + timedelta(seconds=598),
            "km": 5.4, "route": "route_a.gpx"}
    indoor = {"type": "HKWorkoutActivityTypeCycling", "start": START + timedelta(days=1),
              "end": START + timedelta(days=1, seconds=900), "stats_km": 7.5}
    hr = [(START + timedelta(seconds=i), 130) for i in range(0, 600, 5)]
    xml = _apple_xml([ride, indoor], hr, extra=_records(far))
    return xml.replace(b'sourceName="Apple Watch" startDate', f'sourceName="{WATCH}" startDate'.encode(), 1)


def test_a_ride_gets_its_own_watchs_power_and_cadence(tmp_path):
    res = AH.scan_export(_reader(_xml())[0], tmp_path / "s")
    ride, indoor = res["workouts"]
    assert ride["source"] == WATCH
    # the watch's power only (not the second meter's), not the kcal one, at the time it was taken
    assert len(ride["power"]) == 120 and {v for _t, v in ride["power"]} == {210}
    assert ride["power"][0][0] == START.timestamp()
    # the cadence as recorded (revolutions per minute, not halved), at the middle of each 5 s span
    assert len(ride["rpm"]) == 120 and {v for _t, v in ride["rpm"]} == {88}
    assert ride["rpm"][0][0] == START.timestamp() + 2.5
    assert ride["cad"] == []                                        # the phone's pocket steps are not its cadence
    # another app's ride: every source's (Zwift's)
    assert len(indoor["power"]) == 300 and {v for _t, v in indoor["power"]} == {180}
    assert len(indoor["rpm"]) == 300 and {v for _t, v in indoor["rpm"]} == {92}
    assert not (tmp_path / "s" / "more.bin").exists()


def test_the_fit_carries_the_rides_power_and_cadence_with_a_route_and_indoors(tmp_path):
    ride, indoor = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    gpx = _gpx(0, step=1, kind="cycling").replace(b"<gpxtpx:hr>150</gpxtpx:hr><gpxtpx:cad>85</gpxtpx:cad>", b"")
    w = UP._parse(AH.to_fit(ride, gpx), "a.fit")
    assert w.sport == "cycling"
    assert w.has_power and w.power_w[10] == 210 and w.power_w[500] == 210
    assert w.has_cadence and w.cadence_rpm[10] == 88 and w.cadence_rpm[500] == 88 and w.heart_rate_bpm[10] == 130
    i = UP._parse(AH.to_fit(indoor, None), "b.fit")                 # indoor: no route, the samples' times
    assert i.power_w[0] == 180 and i.cadence_rpm[0] == 92 and i.cadence_rpm[600] == 92
    assert abs(i.total_distance_m - 7500) < 1
    # a route with its own cadence keeps it
    own = UP._parse(AH.to_fit(ride, _gpx(0, step=1, kind="cycling").replace(b"<gpxtpx:cad>85<", b"<gpxtpx:cad>77<")),
                    "c.fit")
    assert own.cadence_rpm[10] == 77 and own.power_w[10] == 210


def test_a_ride_without_cycling_samples_is_as_before(tmp_path):
    ride, _indoor = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    bare = {**ride, "power": [], "cad": [[START.timestamp(), START.timestamp() + 60, 20.0]], "rpm": []}
    old = {k: v for k, v in bare.items() if k != "rpm"}                  # SP-912's workout
    assert AH.to_fit(bare, None) == AH.to_fit(old, None)
    assert UP._parse(AH.to_fit(bare, None), "a.fit").cadence_rpm[0] == 20     # the steps' cadence, as before


def test_cycling_samples_survive_a_pass_in_parts(tmp_path, monkeypatch):
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


def test_memory_stays_flat_with_many_cadence_samples(tmp_path):
    def peak(n):
        xml = _xml(far=n)
        tracemalloc.start()
        tracemalloc.reset_peak()
        res = AH.scan_export(_reader(xml)[0], tmp_path / f"s{n}")
        p = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert res["workouts"][0]["rpm"] and res["more_samples"] >= n
        return p, len(xml)
    small, big = peak(3_000), peak(60_000)
    assert big[1] > 10 * small[1]
    assert big[0] < small[0] + 8 * 1024 * 1024 and big[0] < 32 * 1024 * 1024
