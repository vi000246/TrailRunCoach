"""
SP-833: three more export formats become activities — TrainingPeaks .pwx and Polar's account
download (training-session JSON) through files/track_fit.py, Apple Health's export.zip (export.xml
streamed, files/apple_health.py) through the export import (sync/archive.py). All synthetic: built
from the public descriptions of the formats (no real export at hand — the SP-833 note asks for one).
"""
from __future__ import annotations

import json
import tracemalloc
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.files import apple_health as AH
from backend.files import track_fit
from backend.sync import archive as AR
from backend.sync import upload as UP
from backend.tests.test_export_import_sp349 import (_counts, _gpx, _process_all, _rows, _upload, _zip,  # noqa: F401
                                                    imports)
from backend.tests.test_fit_upload_sp323 import START, _coros_act, _coros_sync, _run, api  # noqa: F401
from backend.tests.test_sync_e2e import make_session, run

TAIPEI = ZoneInfo("Asia/Taipei")


def _local(t, fmt="%Y-%m-%dT%H:%M:%S") -> str:
    return t.astimezone(TAIPEI).strftime(fmt)


# ---------------------------------------------------------------------------- synthetic files

def _pwx(day=0, seconds=600, sport="Run", zone="") -> bytes:
    """PWX 1.0 as WKO / TrainingPeaks write it: the start in local time, samples by offset."""
    t0 = START + timedelta(days=day)
    samples = "".join(
        f"<sample><timeoffset>{i}</timeoffset><hr>145</hr><spd>3.0</spd><pwr>260</pwr><cad>90</cad>"
        f"<dist>{i * 3.0:.1f}</dist><lat>{25.0 + i * 0.000027:.6f}</lat><lon>121.5</lon>"
        f"<alt>{100 + i * 0.05:.1f}</alt></sample>" for i in range(seconds))
    return ('<?xml version="1.0" encoding="UTF-8"?><pwx xmlns="http://www.peaksware.com/PWX/1/0" version="1.0" '
            f'creator="WKO"><workout><athlete><name>me</name></athlete><sportType>{sport}</sportType>'
            f"<time>{_local(t0)}{zone}</time><summarydata><beginning>0</beginning><duration>{seconds}</duration>"
            f"</summarydata>{samples}</workout></pwx>").encode()


def _polar(day=0, seconds=600, sport="RUNNING", offset=480, exercises=1, samples=True) -> bytes:
    """Polar's account download: training-session-*.json, exportVersion 1.x (local times with the
    exercise's timezoneOffset in minutes; speed in km/h)."""
    t0 = START + timedelta(days=day)

    def at(i):
        return (t0 + timedelta(seconds=i)).astimezone(timezone(timedelta(minutes=offset))).strftime(
            "%Y-%m-%dT%H:%M:%S.000")
    exs = []
    for e in range(exercises):
        rng = range(e * seconds, (e + 1) * seconds)
        exs.append({
            "startTime": at(rng[0]), "stopTime": at(rng[-1]), "timezoneOffset": offset,
            "sport": sport if e == 0 else "CYCLING", "distance": seconds * 3.0,
            "samples": {
                "heartRate": [{"dateTime": at(i), "value": 150} for i in rng],
                "speed": [{"dateTime": at(i), "value": 10.8} for i in rng],
                "cadence": [{"dateTime": at(i), "value": 86} for i in rng],
                "altitude": [{"dateTime": at(i), "value": 100 + i * 0.05} for i in rng],
                "distance": [{"dateTime": at(i), "value": i * 3.0} for i in rng],
                "recordedRoute": [{"dateTime": at(i), "latitude": 25.0 + i * 0.000027, "longitude": 121.5,
                                   "altitude": 100 + i * 0.05} for i in rng],
            } if samples else {}})
    return json.dumps({"exportVersion": "1.6", "name": "Running", "startTime": at(0), "exercises": exs}).encode()


def _apple_xml(workouts, hr, extra="") -> bytes:
    """export.xml with Apple's internal DTD (elements and attributes, no entities), heart-rate
    Records before the Workouts as the export orders them."""
    def d(t):
        return t.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S +0800")
    recs = "".join(f'<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" '
                   f'creationDate="{d(t)}" startDate="{d(t)}" endDate="{d(t)}" value="{v}"/>\n' for t, v in hr)
    recs += ('<Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" '
             f'startDate="{d(START)}" endDate="{d(START)}" value="12"/>\n')
    wos = ""
    for w in workouts:
        inner = ""
        if w.get("stats_km") is not None:
            inner += (f'<WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning" '
                      f'startDate="{d(w["start"])}" endDate="{d(w["end"])}" sum="{w["stats_km"]}" unit="km"/>')
        if w.get("route"):
            inner += (f'<WorkoutRoute sourceName="Apple Watch"><FileReference path="/workout-routes/{w["route"]}"/>'
                      "</WorkoutRoute>")
        dist = f' totalDistance="{w["km"]}" totalDistanceUnit="km"' if w.get("km") is not None else ""
        wos += (f'<Workout workoutActivityType="{w["type"]}" duration="10" durationUnit="min"{dist} '
                f'sourceName="Apple Watch" startDate="{d(w["start"])}" endDate="{d(w["end"])}">'
                f'<MetadataEntry key="HKIndoorWorkout" value="0"/>{inner}</Workout>\n')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE HealthData [\n<!ELEMENT HealthData (ExportDate,Me,'
            '(Record|Workout)*)>\n<!ATTLIST HealthData locale CDATA #REQUIRED>\n<!ELEMENT Record EMPTY>\n'
            '<!ATTLIST Record type CDATA #REQUIRED value CDATA #IMPLIED>\n]>\n<HealthData locale="zh_TW">\n'
            '<ExportDate value="2026-10-01 08:00:00 +0800"/>\n<Me HKCharacteristicTypeIdentifierBiologicalSex="x"/>\n'
            + recs + extra + wos + "</HealthData>\n").encode()


def _apple_export() -> tuple[bytes, dict]:
    """export.zip: a run with its route (heart rate every 5 s), an indoor walk without a route, a
    route no workout refers to, export_cda.xml."""
    run_ = {"type": "HKWorkoutActivityTypeRunning", "start": START, "end": START + timedelta(seconds=598),
            "km": 1.8, "route": "route_2026-09-02_6.30am.gpx"}
    walk = {"type": "HKWorkoutActivityTypeWalking", "start": START + timedelta(days=1),
            "end": START + timedelta(days=1, seconds=900), "stats_km": 1.2}
    hr = [(START + timedelta(seconds=i), 120 + i // 30) for i in range(0, 600, 5)]
    hr += [(START + timedelta(days=1, seconds=i), 100) for i in range(0, 900, 10)]
    hr += [(START - timedelta(hours=3), 60), (START + timedelta(hours=5), 65)]      # outside any workout
    gpx = _gpx(0, step=1).replace(b"<gpxtpx:hr>150</gpxtpx:hr>", b"")          # Apple's routes: no heart rate
    files = {"apple_health_export/export.xml": _apple_xml([run_, walk], hr),
             "apple_health_export/export_cda.xml": b"<ClinicalDocument/>",
             "apple_health_export/workout-routes/route_2026-09-02_6.30am.gpx": gpx,
             "apple_health_export/workout-routes/route_2025-01-01_7.00am.gpx": _gpx(-200)}
    return _zip(files), {"run": run_, "walk": walk}


# ---------------------------------------------------------------------------- PWX

def test_a_pwx_becomes_a_fit_with_its_channels_and_a_local_start():
    fit = track_fit.to_fit(_pwx(), "pwx", TAIPEI)
    w = UP._parse(fit, "a.pwx")
    assert w.sport == "running" and w.start_time.replace(tzinfo=timezone.utc) == START   # 06:30 Taipei
    assert w.duration_s == 599 and abs(w.total_distance_m - 1797) < 1
    assert w.heart_rate_bpm[10] == 145 and w.power_w[10] == 260 and w.cadence_rpm[10] == 90
    assert abs(w.speed_ms[10] - 3.0) < 0.01
    assert track_fit.to_fit(_pwx(), "pwx", TAIPEI) == fit                   # the same file -> the same hash
    # a start with its own zone wins over the athlete's
    z = UP._parse(track_fit.to_fit(_pwx(zone="+08:00"), "pwx", ZoneInfo("Europe/London")), "a.pwx")
    assert z.start_time.replace(tzinfo=timezone.utc) == START
    assert UP._parse(track_fit.to_fit(_pwx(sport="Bike"), "pwx", TAIPEI), "a.pwx").sport == "cycling"


def test_a_pwx_without_samples_or_with_an_entity_is_refused():
    empty = b'<pwx xmlns="http://www.peaksware.com/PWX/1/0"><workout><time>2026-09-02T06:30:00</time></workout></pwx>'
    with pytest.raises(track_fit.TrackError) as e:
        track_fit.to_fit(empty, "pwx")
    assert e.value.reason == UP.NOT_ACTIVITY
    for bad in (b"<pwx/>"[:4], b'<!DOCTYPE pwx [<!ENTITY a "x">]><pwx/>', _gpx()):
        with pytest.raises(track_fit.TrackError):
            track_fit.to_fit(bad, "pwx")


# ---------------------------------------------------------------------------- Polar

def test_a_polar_training_session_becomes_a_fit_at_its_own_offset():
    fit = track_fit.to_fit(_polar(), "polar", ZoneInfo("Europe/London"))   # the file's offset wins
    w = UP._parse(fit, "training-session-2026-09-02-1-a.json")
    assert w.sport == "running" and w.start_time.replace(tzinfo=timezone.utc) == START
    assert w.duration_s == 599 and abs(w.total_distance_m - 1797) < 1
    assert w.heart_rate_bpm[10] == 150 and w.cadence_rpm[10] == 86 and abs(w.speed_ms[10] - 3.0) < 0.01
    (_t, lat, lon, *_rest), _sp = track_fit.parse(_polar(), "polar")
    assert abs(float(lat[10]) - (25.0 + 10 * 0.000027)) < 1e-9 and float(lon[10]) == 121.5   # the route
    # no offset in the file: the athlete's zone
    obj = json.loads(_polar())
    for ex in obj["exercises"]:
        ex.pop("timezoneOffset")
    w2 = UP._parse(track_fit.to_fit(json.dumps(obj).encode(), "polar", TAIPEI), "x.json")
    assert w2.start_time.replace(tzinfo=timezone.utc) == START
    # a multisport session: one activity, the first exercise's sport
    m = UP._parse(track_fit.to_fit(_polar(exercises=2), "polar"), "x.json")
    assert m.sport == "running" and m.duration_s == 1199


def test_a_polar_session_without_samples_is_no_activity_and_junk_is_refused():
    with pytest.raises(track_fit.TrackError) as e:
        track_fit.to_fit(_polar(samples=False), "polar")                   # a manual entry
    assert e.value.reason == UP.NOT_ACTIVITY
    for bad in (b"", b"[]", b"{", b"<x/>", b'{"exercises": "no"}', b'{"exercises": [1, null]}'):
        with pytest.raises(track_fit.TrackError):
            track_fit.to_fit(bad, "polar")


def test_the_names_the_import_takes(monkeypatch):
    assert track_fit.kind_of("polar-user-data-export/training-session-2024-05-01-123-abc.json") == "polar"
    assert track_fit.kind_of("x/account-data.json") is None and track_fit.kind_of("a.PWX.gz") == "pwx"
    assert UP.is_fit_name("TP/2026-09-06.pwx") and UP.is_fit_name("training-session-1.json")
    assert not UP.is_fit_name("account-data.json") and ".pwx" in UP.limits()["exts"]
    assert ".json" not in UP.limits()["exts"]               # the page's pick never offers JSON
    assert UP.max_bytes("a.pwx") == UP.MAX_TRACK_BYTES and UP.max_bytes("training-session-1.json") == UP.MAX_TRACK_BYTES


def test_a_trainingpeaks_folder_pick_takes_pwx_in_the_athletes_zone(tmp_path, monkeypatch):
    zones = []
    monkeypatch.setattr(UP, "_local_tz", lambda aid: zones.append(aid) or TAIPEI)

    async def go():
        s = await make_session(tmp_path)
        r = await UP.import_files(s, 1, [UP.Incoming("TP/2026-09-02_run.pwx", _pwx(0), 1),
                                         UP.Incoming("TP/2026-09-03_run.pwx.gz", __import__("gzip").compress(_pwx(1)), 1),
                                         UP.Incoming("TP/again.pwx", _pwx(0), 1)])
        assert (r["added"], r["duplicate"], r["failed"], r["skipped"]) == (2, 1, 0, 0)
        rows = await _rows(s)
        assert [x.workout_date.isoformat() for x in rows] == ["2026-09-02", "2026-09-03"]
        assert all(x.source == "upload" and x.file_format == "fit" for x in rows)
    run(go())
    assert zones and set(zones) == {1}


def test_the_local_zone_is_the_athletes(monkeypatch):
    from backend.engine import localtime
    monkeypatch.setattr(localtime, "zone", lambda uid: TAIPEI if uid == 7 else None)
    assert UP._local_tz(7) is TAIPEI
    monkeypatch.setattr(localtime, "zone", lambda uid: 1 / 0)
    assert UP._local_tz(7) is None                                         # no settings: the times as UTC


def test_a_polar_account_download_imports_its_training_sessions(tmp_path, imports, monkeypatch):
    monkeypatch.setattr(UP, "_local_tz", lambda aid: TAIPEI)

    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_zip({"polar-user-data-export/training-session-2026-09-02-1-a.json": _polar(0),
                            "polar-user-data-export/training-session-2026-09-03-2-b.json": _polar(1, sport="TRAIL_RUNNING"),
                            "polar-user-data-export/training-session-2026-09-04-3-c.json": _polar(2, samples=False),
                            "polar-user-data-export/account-data.json": b"{}",
                            "polar-user-data-export/activity-2026-09-02.json": b"{}"}), name="polar.zip")
        await _process_all(s, imp)
        st = AR.status(imp)
        assert st["kind"] == "polar" and st["total"] == 3
        assert _counts(st) == (2, 0, 0, 1 + 2)       # the manual entry; two JSON that are no session
        rows = await _rows(s)
        assert [(x.workout_date.isoformat(), x.sport) for x in rows] == [("2026-09-02", "running"),
                                                                         ("2026-09-03", "running")]
        manual = next(i for i in st["items"] if i["status"] == "skipped")
        assert manual["reason"] == "not_activity" and manual["name"].endswith("3-c.json")
    run(go())


# ---------------------------------------------------------------------------- Apple Health

def _reader(data: bytes, step=None):
    pos = [0]
    sizes = []

    def read(n):
        n = min(n, step or n)
        sizes.append(n)
        b = data[pos[0]:pos[0] + n]
        pos[0] += len(b)
        return b
    return read, sizes


def test_export_xml_is_streamed_in_pieces_and_each_workout_gets_its_heart_rate(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 4096)
    data, wk = _apple_export()
    import io
    import zipfile
    xml = zipfile.ZipFile(io.BytesIO(data)).read("apple_health_export/export.xml")
    read, sizes = _reader(xml)
    res = AH.scan_export(read, tmp_path / "s", base="apple_health_export/")
    assert max(sizes) == 4096 and len(sizes) > 3                          # never the whole file at once
    assert not (tmp_path / "s" / "hr.bin").exists()
    run_, walk = res["workouts"]
    assert run_["type"] == "HKWorkoutActivityTypeRunning" and run_["start"] == START.timestamp()
    assert run_["route"] == "apple_health_export/workout-routes/route_2026-09-02_6.30am.gpx"
    assert run_["distance_m"] == 1800 and walk["distance_m"] == 1200 and walk["route"] is None
    assert len(run_["hr"]) == 120 and run_["hr"][0] == [START.timestamp(), 120]
    assert len(walk["hr"]) == 90 and res["hr_samples"] == 120 + 90 + 2     # two outside every workout


def test_export_xml_with_an_entity_or_no_health_data_is_refused(tmp_path):
    lol = (b'<?xml version="1.0"?><!DOCTYPE HealthData [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
           b'<HealthData><Record type="HKQuantityTypeIdentifierHeartRate" value="&b;"/></HealthData>')
    for bad in (lol, b"<x/>", b"not xml", b""):
        with pytest.raises(AH.AppleError):
            AH.scan_export(_reader(bad)[0], tmp_path / "s")
    # an external DTD is never fetched (its entity is only skipped): nothing in it
    ext = b'<!DOCTYPE HealthData SYSTEM "http://127.0.0.1:9/x.dtd"><HealthData>&ext;</HealthData>'
    assert AH.scan_export(_reader(ext)[0], tmp_path / "s")["workouts"] == []


def test_memory_stays_flat_however_big_export_xml_is(tmp_path):
    """Twenty times the heart-rate samples outside the workouts: the peak memory barely moves (they
    go to the scratch file; only those inside a workout are kept)."""
    def peak(n):
        hr = [(START - timedelta(days=30) + timedelta(seconds=6 * i), 70) for i in range(n)]
        xml = _apple_xml([{"type": "HKWorkoutActivityTypeRunning", "start": START,
                           "end": START + timedelta(seconds=600)}], hr)
        read, _ = _reader(xml)
        tracemalloc.start()
        tracemalloc.reset_peak()
        AH.scan_export(read, tmp_path / f"s{n}")
        p = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return p, len(xml)
    small, big = peak(10_000), peak(200_000)
    assert big[1] > 15 * small[1]
    assert big[0] < small[0] + 8 * 1024 * 1024 and big[0] < 32 * 1024 * 1024


def test_an_apple_workout_becomes_a_fit_with_its_route_and_heart_rate():
    data, _ = _apple_export()
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    res = AH.scan_export(_reader(z.read("apple_health_export/export.xml"))[0],
                         __import__("pathlib").Path(__import__("tempfile").mkdtemp()), base="apple_health_export/")
    run_, walk = res["workouts"]
    w = UP._parse(AH.to_fit(run_, z.read(run_["route"])), "a.fit")
    assert w.sport == "running" and w.start_time.replace(tzinfo=timezone.utc) == START
    assert abs(w.total_distance_m - 598 * 3.0) < 20 and w.heart_rate_bpm[12] == 120 and w.heart_rate_bpm[300] == 130
    i = UP._parse(AH.to_fit(walk, None), "b.fit")                          # indoor: heart rate, the workout's distance
    assert i.sport == "walking" and i.duration_s >= 890 and abs(i.total_distance_m - 1200) < 1
    assert i.heart_rate_bpm[0] == 100
    assert AH.to_fit(run_, z.read(run_["route"])) == AH.to_fit(run_, z.read(run_["route"]))   # one hash
    assert AH.sport_of("HKWorkoutActivityTypeHiking") == (track_fit.HIKING, 0)
    assert AH.sport_of("HKWorkoutActivityTypeTraditionalStrengthTraining") == (track_fit.GENERIC, 0)
    assert AH.apple_time("2026-09-02 06:30:00 +0800") == START.timestamp()
    assert AH.apple_time("2026-09-02") is None and AH.apple_time("2026-13-02 06:30:00 +0800") is None


def test_an_apple_health_export_imports_its_workouts_once(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        await _coros_sync(s, [_coros_act("111", _run(0, power=240))])       # the same run from the watch
        data, _ = _apple_export()
        imp = _upload(data)
        await _process_all(s, imp, part_files=1)                           # one workout per part
        st = AR.status(imp)
        assert st["kind"] == "apple_health" and st["total"] == 2 and st["done"] == 2
        assert _counts(st) == (1, 1, 0, 1 + 1)       # the run = the COROS one; the lone route and export_cda.xml
        assert st["skipped_files"]["apple_health"]["names"] == [
            "apple_health_export/workout-routes/route_2025-01-01_7.00am.gpx"]
        assert st["skipped_files"]["not_activity_file"]["names"] == ["apple_health_export/export_cda.xml"]
        dup = next(i for i in st["items"] if i["status"] == "duplicate")
        assert dup["reason"] == "same_activity" and dup["of"] == "coros" and dup["name"].startswith("Apple Health › ")
        coros, apple_run, walk = await _rows(s)
        assert apple_run.duplicate_of == coros.id and walk.sport == "walking" and walk.source == "upload"
        AR._drop_data(AR.root() / imp)                                      # what the last part's _finish does
        assert not (AR.root() / imp / "apple").exists()
        # the same export again: the same FIT bytes, nothing new
        await _process_all(s, _upload(data, name="again.zip"))
        st2 = AR.status(None)
        assert _counts(st2)[:3] == (0, 2, 0) and len(await _rows(s)) == 3
        assert {i["reason"] for i in st2["items"]} == {"same_file"}
    run(go())


def test_an_unreadable_export_xml_is_listed_and_the_rest_goes_on(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_zip({"apple_health_export/export.xml": b"<HealthData><Workout",
                            "apple_health_export/workout-routes/route_a.gpx": _gpx(0)}))
        await _process_all(s, imp)
        st = AR.status(imp)
        assert st["kind"] == "apple_health" and st["total"] == 0
        assert st["skipped_files"]["apple_health_unreadable"]["n"] == 1
        assert st["skipped_files"]["apple_health"]["n"] == 1 and await _rows(s) == []
    run(go())


def test_export_xml_counts_toward_the_inflate_cap(tmp_path, imports, monkeypatch):
    monkeypatch.setattr(AR, "MAX_INFLATE_BYTES", 2000)

    async def go():
        s = await make_session(tmp_path)
        data, _ = _apple_export()
        imp = _upload(data)
        await _process_all(s, imp)
        st = AR.status(imp)
        assert st["total"] == 0 and st["skipped_files"]["too_large_inflated"]["n"] == 1
    run(go())


# ---------------------------------------------------------------------------- the page's words

@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_page_explains_how_to_get_the_polar_and_apple_exports_and_no_longer_says_not_yet(loc):
    from pathlib import Path
    static = Path(AR.__file__).resolve().parents[1] / "static"
    js = (static / "exportimport.js").read_text("utf-8")
    assert 'T("how.polar")' in js and 'T("how.apple")' in js and "how.later" not in js
    assert '"apple_health_unreadable"' in js
    common = json.loads((static / "i18n" / loc / "common.json").read_text("utf-8"))
    assert "expimport.how.later" not in common
    words = " ".join(common[k] for k in ("expimport.intro", "expimport.how.polar", "expimport.how.apple",
                                         "expimport.kind.polar", "expimport.kind.apple_health", "fitimport.limits"))
    assert not any(w in words for w in ("還不能匯入", "cannot be imported yet", "not supported yet"))
    assert "PWX" in common["fitimport.limits"] and "PWX" in common["expimport.reason.unsupported_format"]
    assert ("Polar" in common["expimport.intro"]) and ("Apple" in common["expimport.intro"])
