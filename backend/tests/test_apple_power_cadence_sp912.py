"""
SP-912: Apple Health's import also carries the running power (HKQuantityTypeIdentifierRunningPower)
and the cadence (from HKQuantityTypeIdentifierStepCount spans) — files/apple_health.py. All synthetic
(the export.xml of SP-833's builder plus power / step Records): no real export, no WKO5 folder.
"""
from __future__ import annotations

import tracemalloc
from datetime import timedelta
from zoneinfo import ZoneInfo

from backend.files import apple_health as AH
from backend.sync import upload as UP
from backend.tests.test_export_formats_sp833 import _apple_xml, _reader
from backend.tests.test_export_import_sp349 import _gpx
from backend.tests.test_fit_upload_sp323 import START

TAIPEI = ZoneInfo("Asia/Taipei")
WATCH, PHONE = "Logan’s Apple Watch", "Logan’s iPhone"


def _d(t):
    return t.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S +0800")


def _rec(kind, src, t0, t1, value, unit):
    return (f'<Record type="HKQuantityTypeIdentifier{kind}" sourceName="{src}" unit="{unit}" '
            f'creationDate="{_d(t1)}" startDate="{_d(t0)}" endDate="{_d(t1)}" value="{value}"/>\n')


def _records(far=0) -> str:
    """The run (START, 600 s): power every 5 s (250 W), the watch's steps per minute (170 -> 85
    strides/min) and the phone's over the same minutes (5-minute spans, 180 steps/min); the indoor
    walk (a day later, 900 s): the phone's steps only (110 steps/min); power and steps outside both.
    `far`: that many more step samples a month before (the memory test)."""
    s = ""
    for i in range(0, 600, 5):
        t = START + timedelta(seconds=i)
        s += _rec("RunningPower", WATCH, t, t, 250, "W")
    for i in range(0, 600, 60):
        t = START + timedelta(seconds=i)
        s += _rec("StepCount", WATCH, t, t + timedelta(seconds=60), 170, "count")
    for i in range(0, 600, 300):
        t = START + timedelta(seconds=i)
        s += _rec("StepCount", PHONE, t, t + timedelta(seconds=300), 900, "count")
    walk = START + timedelta(days=1)
    for i in range(0, 900, 300):
        t = walk + timedelta(seconds=i)
        s += _rec("StepCount", PHONE, t, t + timedelta(seconds=300), 550, "count")
    out = START - timedelta(hours=5)
    s += _rec("RunningPower", WATCH, out, out, 300, "W")
    s += _rec("StepCount", WATCH, out, out + timedelta(seconds=60), 160, "count")
    s += _rec("StepCount", PHONE, out, out + timedelta(hours=2), 9000, "count")        # too long a span
    s += _rec("RunningPower", WATCH, START, START, 250, "kcal")                         # not watts
    old = START - timedelta(days=30)
    s += "".join(_rec("StepCount", PHONE, old + timedelta(seconds=20 * i), old + timedelta(seconds=20 * i + 20),
                      30, "count") for i in range(far))                  # all before the run (60,000: 14 days)
    return s


def _xml(far=0, run_source=WATCH) -> bytes:
    run_ = {"type": "HKWorkoutActivityTypeRunning", "start": START, "end": START + timedelta(seconds=598),
            "km": 1.8, "route": "route_a.gpx"}
    walk = {"type": "HKWorkoutActivityTypeWalking", "start": START + timedelta(days=1),
            "end": START + timedelta(days=1, seconds=900), "stats_km": 1.2}
    hr = [(START + timedelta(seconds=i), 140) for i in range(0, 600, 5)]
    xml = _apple_xml([run_, walk], hr, extra=_records(far))
    return xml.replace(b'sourceName="Apple Watch" startDate', f'sourceName="{run_source}" startDate'.encode())


def test_a_workout_gets_its_power_and_its_own_watchs_cadence(tmp_path):
    res = AH.scan_export(_reader(_xml())[0], tmp_path / "s")
    run_, walk = res["workouts"]
    assert run_["source"] == WATCH
    assert len(run_["power"]) == 120 and {v for _t, v in run_["power"]} == {250}       # not the kcal one
    assert run_["power"][0][0] == START.timestamp()
    # the watch's steps only (the phone's over the same minutes are not the cadence twice)
    assert len(run_["cad"]) == 10 and {c for _a, _b, c in run_["cad"]} == {85}
    assert run_["cad"][0][:2] == [START.timestamp(), START.timestamp() + 60]
    # the walk has none of its own source's: the phone's steps; no power
    assert walk["power"] == [] and len(walk["cad"]) == 3 and {c for *_x, c in walk["cad"]} == {55}
    assert res["more_samples"] == (120 + 1) + (10 + 2 + 3 + 1)          # the out-of-span ones too, not the bad ones
    assert not (tmp_path / "s" / "more.bin").exists()


def test_a_workout_of_another_app_takes_every_sources_steps(tmp_path):
    run_, _walk = AH.scan_export(_reader(_xml(run_source="Strava"))[0], tmp_path / "s")["workouts"]
    assert len(run_["cad"]) == 10 + 2 and {c for *_x, c in run_["cad"]} == {85, 90}
    assert len(run_["power"]) == 120


def test_the_fit_carries_the_power_and_the_cadence_with_a_route_and_indoors(tmp_path):
    run_, walk = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    gpx = _gpx(0, step=1).replace(b"<gpxtpx:hr>150</gpxtpx:hr><gpxtpx:cad>85</gpxtpx:cad>", b"")
    w = UP._parse(AH.to_fit(run_, gpx), "a.fit")
    assert w.has_power and w.power_w[10] == 250 and w.power_w[300] == 250
    assert w.cadence_rpm[10] == 85 and w.cadence_rpm[500] == 85 and w.heart_rate_bpm[10] == 140
    i = UP._parse(AH.to_fit(walk, None), "b.fit")                      # indoor: no route, the steps' times
    assert i.has_cadence and i.cadence_rpm[0] == 55 and not i.has_power and abs(i.total_distance_m - 1200) < 1
    assert AH.to_fit(run_, gpx) == AH.to_fit(run_, gpx)                # one hash
    # a route with its own cadence keeps it
    own = UP._parse(AH.to_fit(run_, _gpx(0, step=1).replace(b"<gpxtpx:cad>85<", b"<gpxtpx:cad>77<")), "c.fit")
    assert own.cadence_rpm[10] == 77 and own.power_w[10] == 250


def test_a_workout_without_power_or_steps_is_as_before(tmp_path):
    run_, _walk = AH.scan_export(_reader(_xml())[0], tmp_path / "s")["workouts"]
    bare = {**run_, "power": [], "cad": []}
    old = {k: v for k, v in bare.items() if k not in ("power", "cad")}          # SP-833's workout
    assert AH.to_fit(bare, None) == AH.to_fit(old, None)
    w = UP._parse(AH.to_fit(bare, None), "a.fit")
    assert not w.has_power and not w.has_cadence and w.heart_rate_bpm[0] == 140


def test_power_and_steps_survive_a_pass_in_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 2048)
    monkeypatch.setattr(AH, "CHECK_BYTES", 10_000)
    xml = _xml()
    whole = AH.scan_export(_reader(xml)[0], tmp_path / "one")
    s, at, res = tmp_path / "parts", 0, None
    for _ in range(200):
        res = AH.scan_export(_reader(b"\x00" * at + xml[at:])[0], s, key="k", budget=12_000)
        if not res.get("paused"):
            break
        at = res["pos"]
    assert at > 0 and {k: v for k, v in res.items() if k != "paused"} == whole


def test_memory_stays_flat_with_many_step_samples(tmp_path):
    def peak(n):
        xml = _xml(far=n)
        tracemalloc.start()
        tracemalloc.reset_peak()
        res = AH.scan_export(_reader(xml)[0], tmp_path / f"s{n}")
        p = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert res["more_samples"] == 137 + n                           # all of them went to the scratch file
        return p, len(xml)
    small, big = peak(3_000), peak(60_000)
    assert big[1] > 15 * small[1]
    assert big[0] < small[0] + 8 * 1024 * 1024 and big[0] < 32 * 1024 * 1024
