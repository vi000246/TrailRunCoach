"""SP-349: 匯入匯出檔 — a platform's whole-account export (a ZIP) fills in the history a sync cannot
reach. Garmin's 「Export Your Data」 keeps every upload (FIT / TCX / GPX) in nested ZIPs under
DI_CONNECT/DI-Connect-Uploaded-Files; a ZIP of FIT files the user packed and a TrainingPeaks
folder (.fit / .fit.gz / .tcx / .gpx) go the same way. Every activity file goes through SP-323's
import (hash de-dup, dedup.py against the synced runs, the current tenant only); what is no
activity is skipped and listed; the archive is untrusted (ZIP bomb, path tricks, depth, entry
count); the work runs in the job queue in parts, in the background, with progress, and goes on
after a stop (retry). Synthetic files only (fit_builder); every tenant in tmp."""
from __future__ import annotations

import gzip
import io
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from backend import jobs, tenancy
from backend.db.models import WorkoutFile
from backend.files import track_fit
from backend.sync import archive as AR
from backend.sync import dedup
from backend.sync import upload as UP
from backend.tests.test_fit_upload_sp323 import (START, _as, _coros_act, _coros_sync, _db_rows, _monitoring,
                                                 _run, api)  # noqa: F401  (fixture)
from backend.tests.test_sync_e2e import make_session, run


# ---------------------------------------------------------------------------- synthetic files

def _iso(t: datetime, frac: bool = False) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S") + (".000" if frac else "") + "Z"


def _tcx(day=0, seconds=600, sport="Running", hr=140, speed=3.0) -> bytes:
    t0 = START + timedelta(days=day)
    pts = "".join(
        f"<Trackpoint><Time>{_iso(t0 + timedelta(seconds=i))}</Time><Position><LatitudeDegrees>"
        f"{25.0 + i * 0.000027:.6f}</LatitudeDegrees><LongitudeDegrees>121.5</LongitudeDegrees></Position>"
        f"<AltitudeMeters>{100 + i * 0.05:.1f}</AltitudeMeters><DistanceMeters>{i * speed:.1f}</DistanceMeters>"
        f"<HeartRateBpm><Value>{hr}</Value></HeartRateBpm><Extensions>"
        f'<TPX xmlns="http://www.garmin.com/xmlschemas/ActivityExtension/v2"><Speed>{speed}</Speed>'
        f"<RunCadence>88</RunCadence><Watts>250</Watts></TPX></Extensions></Trackpoint>"
        for i in range(seconds))
    return ('<?xml version="1.0" encoding="UTF-8"?><TrainingCenterDatabase '
            'xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities>'
            f'<Activity Sport="{sport}"><Id>{_iso(t0)}</Id><Lap StartTime="{_iso(t0)}"><Track>{pts}</Track></Lap>'
            "</Activity></Activities></TrainingCenterDatabase>").encode()


def _gpx(day=0, seconds=600, step=2, kind="running", times=True) -> bytes:
    t0 = START + timedelta(days=day)
    pts = "".join(
        f'<trkpt lat="{25.0 + i * 0.000027:.6f}" lon="121.5"><ele>{100 + i * 0.05:.1f}</ele>'
        + (f"<time>{_iso(t0 + timedelta(seconds=i), frac=True)}</time>" if times else "")
        + "<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>150</gpxtpx:hr><gpxtpx:cad>85</gpxtpx:cad>"
          "</gpxtpx:TrackPointExtension></extensions></trkpt>"
        for i in range(0, seconds, step))
    return ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="test" '
            'xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
            f"<trk><name>Run</name><type>{kind}</type><trkseg>{pts}</trkseg></trk></gpx>").encode()


def _zip(files: dict, method=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _garmin_export() -> bytes:
    """Garmin's 「Export Your Data」 as the public guides describe it (推估: no real export here):
    DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip holds the uploads in their
    original format; the rest of the account is JSON."""
    part1 = _zip({"me@example.com_111.fit": _run(0), "me@example.com_112.tcx": _tcx(1),
                  "me@example.com_113.gpx": _gpx(2), "me@example.com_114.fit": _monitoring(),
                  "me@example.com_115.txt": b"uploaded note"})
    return _zip({
        "DI_CONNECT/DI-Connect-Fitness/me@example.com_0_summarizedActivities.json": b"[]",
        "DI_CONNECT/DI-Connect-User/user_profile.json": b"{}",
        "DI_CONNECT/DI-Connect-Wellness/2026-09-01_sleepData.json": b"[]",
        "DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip": part1,
        "DI_CONNECT/DI-Connect-Uploaded-Files/": b"",
    })


# ---------------------------------------------------------------------------- helpers

@pytest.fixture
def imports(tmp_path, monkeypatch, _fit_root_in_tmp):
    root = tmp_path / "imports"
    monkeypatch.setattr(AR, "ROOT", root)
    return root


def _upload(data: bytes, name="export.zip", chunk=None) -> str:
    st = AR.create(name, len(data), 1)
    step = chunk or AR.CHUNK_BYTES
    for off in range(0, len(data), step):
        st = AR.append(st["id"], off, data[off:off + step])
    assert st["received"] == len(data)
    return st["id"]


async def _process_all(s, imp_id: str, **kw) -> list[dict]:
    """Every part in turn (what the job queue does), the per-part results."""
    parts = []
    while True:
        r = await AR.process(s, AR.root() / imp_id, 1, **kw)
        parts.append(r)
        if not r["more"] or r["stopped"]:
            return parts


async def _rows(s) -> list[WorkoutFile]:
    return list((await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars())


def _counts(st) -> tuple:
    return st["added"], st["duplicate"], st["failed"], st["skipped"]


# ---------------------------------------------------------------------------- TCX / GPX -> FIT

def test_a_tcx_becomes_a_fit_with_its_channels():
    fit = track_fit.to_fit(_tcx(), "tcx")
    w = UP._parse(fit, "a.tcx")
    assert w.sport == "running" and w.start_time.replace(tzinfo=timezone.utc) == START
    assert w.duration_s == 599 and abs(w.total_distance_m - 1797) < 1
    assert w.heart_rate_bpm[10] == 140 and w.cadence_rpm[10] == 88 and w.power_w[10] == 250
    assert abs(w.speed_ms[10] - 3.0) < 0.01 and w.session["total_ascent"] == 30
    assert track_fit.to_fit(_tcx(), "tcx") == fit                         # the same file -> the same hash


def test_a_gpx_gets_distance_and_speed_from_its_positions_and_its_sport_from_the_type():
    w = UP._parse(track_fit.to_fit(_gpx(step=2), "gpx"), "a.gpx")
    assert w.sport == "running" and w.duration_s == 598
    assert abs(w.total_distance_m - 598 * 3.0) < 20                       # great circle: ~3 m/s north
    assert abs(float(w.speed_ms[100]) - 3.0) < 0.1 and w.heart_rate_bpm[5] == 150
    for word, sport in (("cycling", "cycling"), ("9", "running"), ("1", "cycling"), ("trail_running", "running"),
                        ("hiking", "hiking"), ("", "generic")):
        assert UP._parse(track_fit.to_fit(_gpx(kind=word), "gpx"), "a.gpx").sport == sport, word


def test_a_route_without_times_or_an_entity_declaration_is_refused():
    with pytest.raises(track_fit.TrackError) as e:
        track_fit.to_fit(_gpx(times=False), "gpx")                        # a course: no activity
    assert e.value.reason == UP.NOT_ACTIVITY
    lol = (b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
           b"<gpx><trk><name>&b;</name></trk></gpx>")
    with pytest.raises(track_fit.TrackError) as e:
        track_fit.to_fit(lol, "gpx")                                      # never expanded
    assert e.value.reason == UP.UNREADABLE
    for bad in (b"", b"PK\x03\x04", b"<gpx", _tcx()):                     # a TCX named .gpx: no gpx root
        with pytest.raises(track_fit.TrackError):
            track_fit.to_fit(bad, "gpx")


def test_a_trainingpeaks_folder_of_fit_fit_gz_tcx_and_gpx_goes_through_the_folder_pick(tmp_path):
    """TrainingPeaks exports each workout file in the format it was uploaded in (.fit, a .fit.gz,
    .tcx, .gpx; its own .pwx): SP-323's folder pick takes them. SP-833: a .pwx is read too (a real
    one: test_export_formats_sp833.py); one without samples is no activity."""
    async def go():
        s = await make_session(tmp_path)
        r = await UP.import_files(s, 1, [
            UP.Incoming("TP/2026-09-02_run.fit", _run(0), 1), UP.Incoming("TP/2026-09-03_run.fit.gz", gzip.compress(_run(1)), 1),
            UP.Incoming("TP/2026-09-04_run.tcx", _tcx(3), 1), UP.Incoming("TP/2026-09-05_run.gpx.gz", gzip.compress(_gpx(4)), 1),
            UP.Incoming("TP/2026-09-06_old.pwx", b"<pwx/>", 6), UP.Incoming("TP/again.tcx", _tcx(3), 1)])
        assert (r["added"], r["duplicate"], r["failed"], r["skipped"]) == (4, 1, 1, 0)
        assert [i["status"] for i in r["items"]] == ["added"] * 4 + ["failed", "duplicate"]
        assert r["items"][4]["reason"] == UP.NOT_ACTIVITY
        rows = await _rows(s)
        assert [x.workout_date.isoformat() for x in rows] == ["2026-09-02", "2026-09-03", "2026-09-05", "2026-09-06"]
        assert all(x.source == "upload" and x.file_format == "fit" for x in rows)
    run(go())


def test_a_track_file_has_its_own_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(UP, "MAX_TRACK_BYTES", 5000)

    async def go():
        s = await make_session(tmp_path)
        r = await UP.import_files(s, 1, [UP.Incoming("big.tcx", _tcx(), 1),
                                         UP.Incoming("bomb.gpx.gz", gzip.compress(b" " * 50000), 1)])
        assert [i["reason"] for i in r["items"]] == ["too_large", "too_large"]
    run(go())
    assert UP.max_bytes("a.GPX") == 5000 and UP.max_bytes("a.fit.gz") == UP.MAX_FILE_BYTES


# ---------------------------------------------------------------------------- the archive

def test_a_garmin_export_imports_every_activity_in_its_nested_zip_and_lists_the_rest(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_garmin_export())
        await _process_all(s, imp)
        st = AR.status(imp)
        assert st["kind"] == "garmin" and st["total"] == 4 and st["done"] == 4
        assert _counts(st) == (3, 0, 0, 1 + 4)       # the monitoring FIT; three JSON and the .txt
        rows = await _rows(s)
        assert [x.workout_date.isoformat() for x in rows] == ["2026-09-02", "2026-09-03", "2026-09-04"]
        assert all(x.source == "upload" and x.duplicate_of is None for x in rows)
        part = "DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip › "
        assert {i["name"]: (i["status"], i["reason"]) for i in st["items"]} == {
            part + "me@example.com_114.fit": ("skipped", "not_activity")}
        sk = st["skipped_files"]["not_activity_file"]
        assert sk["n"] == 4 and part + "me@example.com_115.txt" in sk["names"]
        assert "DI_CONNECT/DI-Connect-Fitness/me@example.com_0_summarizedActivities.json" in sk["names"]
    run(go())


def test_a_zip_of_fit_files_the_user_packed_imports_too(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_zip({"runs/a.fit": _run(0), "runs/2024/b.FIT": _run(1), "runs/c.fit.gz": gzip.compress(_run(2)),
                            "__MACOSX/runs/._a.fit": b"\0\5\26\7", "runs/.DS_Store": b"x"}), name="my runs.ZIP")
        await _process_all(s, imp)
        st = AR.status(imp)
        assert st["kind"] == "generic" and _counts(st) == (3, 0, 0, 0)      # the Mac's metadata: not even listed
        assert len(await _rows(s)) == 3
    run(go())


def test_a_run_already_synced_and_the_same_export_again_are_counted_once(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        await _coros_sync(s, [_coros_act("111", _run(0, power=240))])        # the watch's own file
        data = _garmin_export()
        await _process_all(s, _upload(data))
        st = AR.status(None)
        assert _counts(st)[:3] == (2, 1, 0)
        dup = next(i for i in st["items"] if i["status"] == "duplicate")
        assert dup["reason"] == "same_activity" and dup["of"] == "coros"
        coros, up, *_ = await _rows(s)
        assert up.duplicate_of == coros.id                                  # kept as a copy, counted once
        counted = (await s.execute(select(WorkoutFile.id).where(await dedup.in_use(s, 1)))).scalars().all()
        assert len(counted) == 3
        # the same export once more: every file is the same content (sha256), nothing new
        await _process_all(s, _upload(data, name="export again.zip"))
        st2 = AR.status(None)
        assert _counts(st2)[:3] == (0, 3, 0) and len(await _rows(s)) == 4
        assert {i["reason"] for i in st2["items"] if i["status"] == "duplicate"} == {"same_file"}
    run(go())


def test_bad_files_fail_alone(tmp_path, imports):
    good, broken = _run(0), bytearray(_run(1))
    broken[20:60] = b"\xff" * 40
    raw = _zip({"a.fit": good, "b.fit": b"not a fit at all", "c.tcx": b"<TrainingCenterDatabase/>",
                "d.gpx": b"<gpx><trk/></gpx>", "e.fit": _run(2)}, method=zipfile.ZIP_STORED)
    # a CRC error in one entry (its bytes changed inside the ZIP)
    i = raw.index(_run(2)[30:60])
    raw = raw[:i] + bytes(30) + raw[i + 30:]

    async def go():
        s = await make_session(tmp_path)
        imp = _upload(raw)
        await _process_all(s, imp)
        st = AR.status(imp)
        assert _counts(st) == (1, 0, 2, 2)
        why = {i["name"]: (i["status"], i["reason"]) for i in st["items"]}
        assert why == {"b.fit": ("failed", "not_fit"), "c.tcx": ("skipped", "not_activity"),
                       "d.gpx": ("skipped", "not_activity"), "e.fit": ("failed", "unreadable")}
    run(go())


def test_polar_and_apple_health_exports_are_recognised_and_said_so(tmp_path, imports):
    """Recognised by their layout. SP-833: their workouts are imported (test_export_formats_sp833.py);
    an empty training session is no activity, a route no workout refers to is listed."""
    async def go():
        s = await make_session(tmp_path)
        polar = _upload(_zip({"polar-user-data-export/training-session-2024-05-01-123-abc.json": b"{}",
                              "polar-user-data-export/account-data.json": b"{}"}), name="polar.zip")
        await _process_all(s, polar)
        st = AR.status(polar)
        assert st["kind"] == "polar" and st["total"] == 1 and _counts(st) == (0, 0, 0, 2)
        apple = _upload(_zip({"apple_health_export/export.xml": b"<HealthData/>",
                              "apple_health_export/export_cda.xml": b"<x/>",
                              "apple_health_export/workout-routes/route_2024-05-01_6.30am.gpx": _gpx(0)}),
                        name="export.zip")
        await _process_all(s, apple)
        st = AR.status(apple)
        assert st["kind"] == "apple_health" and st["total"] == 0
        assert st["skipped_files"]["apple_health"]["n"] == 1 and await _rows(s) == []
    run(go())


# ---------------------------------------------------------------------------- untrusted archives

def test_entry_names_never_leave_the_folder(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_zip({"../../evil.fit": _run(0), "/abs/x.fit": _run(1), "C:\\win\\y.fit": _run(2),
                            "../../evil.zip": _zip({"../z.txt": b"z"})}))
        await _process_all(s, imp)
        st = AR.status(imp)
        assert _counts(st) == (3, 0, 0, 1)
        assert st["skipped_files"]["not_activity_file"]["names"] == ["evil.zip › z.txt"]
        d = AR.root() / imp
        written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()
                   and "fitroot" not in p.parts}
        assert all(w.startswith(f"imports/{imp}/") for w in written), written
        assert not list(tmp_path.rglob("evil*")) and not list(tmp_path.parent.glob("evil*"))
        assert {p.name for p in d.iterdir()} <= {"state.json", "manifest.json", "items.jsonl", "nested", "archive.zip"}
    run(go())


def test_a_zip_bomb_is_never_inflated_past_the_caps(tmp_path, imports, monkeypatch):
    monkeypatch.setattr(UP, "MAX_FILE_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(AR, "MAX_NESTED_BYTES", 1024 * 1024)
    zeros = b"\0" * (8 * 1024 * 1024)                     # compresses to ~8 KB
    reads = []
    real = AR.read_capped

    def spy(zf, info, cap, out=None):
        r = real(zf, info, cap, out)
        reads.append((info.filename, cap))
        return r
    monkeypatch.setattr(AR, "read_capped", spy)

    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_zip({"bomb.fit": zeros, "bomb.json": zeros * 4, "inner.zip": _zip({"x.fit": zeros[:2 * 1024 * 1024]}, method=zipfile.ZIP_STORED),
                            "ok.fit": _run(0)}))
        await _process_all(s, imp)
        st = AR.status(imp)
        why = {i["name"]: i["reason"] for i in st["items"]}
        assert why == {"bomb.fit": "too_large"} and st["added"] == 1
        assert st["skipped_files"]["too_large_inflated"]["names"] == ["inner.zip"]
        assert st["skipped_files"]["not_activity_file"]["names"] == ["bomb.json"]     # never inflated
        assert [n for n, _c in reads] == ["inner.zip", "bomb.fit", "ok.fit"]
        assert not (AR.root() / imp / "nested" / "1.zip").exists()
    run(go())


def test_too_many_entries_or_nesting_too_deep_are_refused(tmp_path, imports, monkeypatch):
    async def go():
        s = await make_session(tmp_path)
        deep = _zip({"l1.zip": _zip({"l2.zip": _zip({"l3.zip": _zip({"a.fit": _run(0)})})}), "b.fit": _run(1)})
        imp = _upload(deep)
        await _process_all(s, imp)
        st = AR.status(imp)
        assert st["added"] == 1 and st["skipped_files"]["too_deep"]["names"] == ["l1.zip › l2.zip › l3.zip"]
        monkeypatch.setattr(AR, "MAX_ENTRIES", 3)
        many = _upload(_zip({f"{i}.fit": _run(i) for i in range(5)}), name="many.zip")
        with pytest.raises(AR.ArchiveError) as e:
            await AR.process(s, AR.root() / many, 1)
        assert e.value.reason == "too_many_entries"
        assert AR.entry_count(AR.root() / many / "archive.zip") == 5
    run(go())


def test_the_upload_resumes_at_what_was_received_and_refuses_what_is_no_zip(tmp_path, imports, monkeypatch):
    data = _zip({"a.fit": _run(0)})
    st = AR.create("export.zip", len(data), 1)
    st = AR.append(st["id"], 0, data[:100])
    assert (st["state"], st["received"]) == ("uploading", 100)
    with pytest.raises(AR.ArchiveError) as e:
        AR.append(st["id"], 0, data[:100])                                  # a piece sent twice
    assert e.value.reason == "offset" and e.value.params["received"] == 100
    again = AR.create("export.zip", len(data), 1)                         # the page reloaded: the same file
    assert again["id"] == st["id"] and again["received"] == 100
    st = AR.append(st["id"], 100, data[100:])
    assert st["start"] is True                     # complete and a ZIP: the caller queues it (start())
    assert st["state"] == "interrupted"            # until it does, the status says no job holds it
    with pytest.raises(AR.ArchiveError) as e:
        AR.create("notes.txt", 10, 1)
    assert e.value.reason == "not_zip"
    monkeypatch.setattr(AR, "MAX_ARCHIVE_BYTES", 1000)
    with pytest.raises(AR.ArchiveError) as e:
        AR.create("big.zip", 1001, 1)
    assert e.value.reason == "too_large"
    bad = AR.create("fake.zip", 50, 1)                                    # a new import replaces the last one
    st = AR.append(bad["id"], 0, b"x" * 50)
    assert st["start"] is False and st["state"] == "failed" and st["error"] == "not_zip"
    assert not (AR.root() / bad["id"] / "archive.zip").exists()
    assert [p.name for p in AR.root().iterdir()] == [bad["id"]]


def test_the_work_goes_in_parts_and_goes_on_after_a_stop(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_zip({f"{i}.fit": _run(i) for i in range(5)}))
        d = AR.root() / imp
        r = await AR.process(s, d, 1, part_files=2)
        assert r == {"more": 3, "added": 2, "stopped": False}
        assert AR.status(imp)["done"] == 2
        # the server died while writing the third result: that line is cut short
        with open(d / "items.jsonl", "a", encoding="utf-8") as f:
            f.write('{"i": 2, "name": "2.fi')
        parts = await _process_all(s, imp, part_files=2)
        assert [p["added"] for p in parts] == [2, 1]
        st = AR.status(imp)
        assert _counts(st) == (5, 0, 0, 0) and st["done"] == 5 and len(await _rows(s)) == 5
    run(go())


def test_files_that_failed_for_a_passing_reason_are_tried_again(tmp_path, imports, monkeypatch):
    real = UP.import_one
    calls = {"n": 0}

    async def flaky(db, aid, f, folder):
        calls["n"] += 1
        if f.name == "b.fit" and calls["n"] < 4:
            return {"name": f.name, "status": "failed", "reason": UP.ERROR, "date": None, "sport": None, "of": None}
        return await real(db, aid, f, folder)
    monkeypatch.setattr(UP, "import_one", flaky)

    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_zip({"a.fit": _run(0), "b.fit": _run(1), "c.fit": _run(2)}))
        await _process_all(s, imp)
        st = AR.status(imp)
        assert _counts(st) == (2, 0, 1, 0) and st["retryable"] == 1
        AR._finish(imp)                                                     # what the last part does
        assert (AR.root() / imp / "archive.zip").exists()                   # kept: a retry needs it
        with AR._LOCK:
            j = json.loads((AR.root() / imp / "state.json").read_text())
            j["state"] = "done"
            (AR.root() / imp / "state.json").write_text(json.dumps(j))
        assert AR.status(imp)["can_retry"] is True
        monkeypatch.setattr(AR, "start", lambda imp_id, loop=None: None)    # the queue: the test runs the part
        AR.retry(imp, None)
        await _process_all(s, imp)
        st = AR.status(imp)
        assert _counts(st) == (3, 0, 0, 0) and st["retryable"] == 0 and len(await _rows(s)) == 3
    run(go())


def test_defaults_are_sane():
    assert AR.CHUNK_BYTES <= 32 * 2**20                     # far below a proxy's ~100 MB request cap
    assert AR.MAX_ARCHIVE_BYTES >= 2 * 2**30                # Garmin's export of many years
    assert AR.MAX_INFLATE_BYTES >= AR.MAX_ARCHIVE_BYTES and AR.MAX_DEPTH == 2
    # one part: ~2 s per MB here, ~8.5× on the NAS -> about a minute there
    assert AR.PART_BYTES * 2 * 8.5 / 2**20 <= 90 and AR.PART_FILES <= 200
    assert UP.MAX_FILE_BYTES < UP.MAX_TRACK_BYTES <= 100 * 2**20


# ---------------------------------------------------------------------------- the API, the queue

URL = "/api/v1/sync/upload/archive"


def _send(api, who, data: bytes, name="export.zip", chunk=None) -> dict:
    r = api.post(URL, json={"name": name, "size": len(data)}, headers=_as(who))
    assert r.status_code == 200, r.text
    st = r.json()
    step = chunk or st["limits"]["chunk_bytes"]
    off = st["received"]
    while off < len(data):
        r = api.put(f"{URL}/{st['id']}?offset={off}", content=data[off:off + step],
                    headers={**_as(who), "Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        st = r.json()
        off = st["received"]
    return st


def _wait(api, who, imp_id, until=("done", "failed", "interrupted"), limit=30.0) -> dict:
    end = time.monotonic() + limit
    while True:
        st = api.get(f"{URL}/{imp_id}", headers=_as(who)).json()
        if st["state"] in until or time.monotonic() > end:
            return st
        time.sleep(0.05)


def test_an_export_goes_up_in_pieces_and_is_imported_in_the_background_into_its_tenant_only(api, monkeypatch):
    monkeypatch.setattr(AR, "CHUNK_BYTES", 2048)
    monkeypatch.setattr(AR, "PART_FILES", 1)                 # one queue job per file
    seen = []
    real = jobs.submit

    def spy(fn, **kw):
        seen.append((kw.get("cls"), kw.get("priority"), kw.get("label")))
        return real(fn, **kw)
    monkeypatch.setattr(jobs, "submit", spy)
    data = _garmin_export()
    assert len(data) > 3 * 2048
    st = _send(api, "A", data)
    assert st["state"] in ("queued", "running", "done")
    st = _wait(api, "A", st["id"])
    assert st["state"] == "done" and (st["added"], st["duplicate"], st["failed"]) == (3, 0, 0)
    assert st["total"] == st["done"] == 4
    assert seen == [("single", "sync_hot", "export import")] * 4
    a, b = api.tenants["A"], api.tenants["B"]
    assert [row[0] for row in _db_rows(a)] == ["upload"] * 3 and _db_rows(b) == []
    assert not (a.root / "imports" / st["id"] / "archive.zip").exists()    # the archive went; the results stay
    assert not (b.root / "imports").exists() and not (tenancy.home_root() / "imports").exists()
    assert api.get(URL, headers=_as("B")).json()["import"] is None
    assert api.get(URL, headers=_as("A")).json()["import"]["id"] == st["id"]
    end = time.monotonic() + 5
    while not api.after and time.monotonic() < end:
        time.sleep(0.05)
    assert api.after == [("uA", "upload", {"status": "ok", "downloaded": 3, "trigger": "upload"})]   # once


def test_an_interrupted_import_says_so_and_goes_on_by_retry(api, monkeypatch):
    data = _zip({"a.fit": _run(0), "b.fit": _run(1)})
    monkeypatch.setattr(AR, "start", lambda imp_id, loop=None: None)   # the server "restarts" before the job
    st = _send(api, "A", data)
    assert st["state"] == "interrupted" and st["can_retry"] is True
    monkeypatch.undo()
    from backend.tests.test_fit_upload_sp323 import runner
    after = []

    async def fake_after(db, source, result, athlete_id=1):
        after.append(result["downloaded"])
    monkeypatch.setattr(runner, "after_import", fake_after)
    r = api.post(f"{URL}/{st['id']}/retry", headers=_as("A"))
    assert r.status_code == 200, r.text
    st = _wait(api, "A", st["id"])
    assert st["state"] == "done" and st["added"] == 2
    assert api.post(f"{URL}/{st['id']}/retry", headers=_as("A")).json()["detail"]["code"] == "ARCHIVE_NOTHING_TO_RETRY"


def test_one_import_at_a_time_and_clearing_it(api, monkeypatch):
    gate = {"open": False}
    real = AR.run_part

    def slow(imp_id, progress=None):
        end = time.monotonic() + 10
        while not gate["open"] and time.monotonic() < end:
            time.sleep(0.02)
        return real(imp_id)
    monkeypatch.setattr(AR, "run_part", slow)
    st = _send(api, "A", _zip({"a.fit": _run(0)}))
    w = _wait(api, "A", st["id"], until=("running",), limit=3)
    assert w["state"] == "running", w
    r = api.post(URL, json={"name": "other.zip", "size": 100}, headers=_as("A"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ARCHIVE_BUSY"
    assert api.post(URL, json={"name": "other.zip", "size": 100}, headers=_as("B")).status_code == 200
    assert api.delete(f"{URL}/{st['id']}", headers=_as("A")).json() == {"deleted": True}
    assert api.get(URL, headers=_as("A")).json()["import"] is None        # gone at once for the page
    gate["open"] = True
    end = time.monotonic() + 10
    while (api.tenants["A"].root / "imports" / st["id"]).exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert not (api.tenants["A"].root / "imports" / st["id"]).exists()


def test_the_api_needs_the_upload_capability_and_checks_sizes(api, monkeypatch):
    for call in (lambda: api.get(URL, headers=_as("nocap")),
                 lambda: api.post(URL, json={"name": "a.zip", "size": 10}, headers=_as("nocap"))):
        r = call()
        assert r.status_code == 403 and r.json()["detail"]["code"] == "UPLOAD_FIT_FORBIDDEN"
    assert not (api.tenants["nocap"].root / "imports").exists()
    r = api.post(URL, json={"name": "a.txt", "size": 10}, headers=_as("A"))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "ARCHIVE_NOT_ZIP"
    r = api.post(URL, json={"name": "a.zip", "size": AR.MAX_ARCHIVE_BYTES + 1}, headers=_as("A"))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "ARCHIVE_TOO_LARGE"
    st = api.post(URL, json={"name": "a.zip", "size": 10}, headers=_as("A")).json()
    r = api.put(f"{URL}/{st['id']}?offset=5", content=b"12345", headers=_as("A"))
    assert r.status_code == 409 and r.json()["detail"]["params"]["received"] == 0
    monkeypatch.setattr(AR, "CHUNK_BYTES", 4)
    r = api.put(f"{URL}/{st['id']}?offset=0", content=b"12345", headers=_as("A"))
    assert r.status_code == 413
    assert api.get(f"{URL}/0123456789abcdef", headers=_as("A")).status_code == 404
    assert api.get(f"{URL}/..%2F..%2Fx", headers=_as("A")).status_code == 404


def test_a_part_runs_in_a_worker_process_where_they_are_on(tmp_path, monkeypatch, imports):
    from backend import workproc
    calls = []
    monkeypatch.setattr(workproc, "enabled", lambda: True)

    def fake_run(target, *args, progress=None, tenant=None):
        calls.append(target)
        return {"more": 0, "added": 0, "stopped": False}
    monkeypatch.setattr(workproc, "run", fake_run)
    imp = _upload(_zip({"a.fit": _run(0)}))
    AR._part_job(imp)
    assert calls == ["backend.sync.archive:run_part"] and AR.status(imp)["state"] == "done"
    mod, fn = calls[0].split(":")
    assert callable(getattr(__import__(mod, fromlist=[fn]), fn))


def test_the_demo_never_takes_an_export():
    from backend import tenancy_mw as MW
    from backend.main import build_app
    from backend.tests.test_fit_upload_sp323 import _paths
    assert not MW.allowed_write("POST", URL) and not MW.allowed_write("PUT", URL + "/0123456789abcdef")
    assert URL not in _paths(build_app(demo=True)) and URL in _paths(build_app(demo=False))


def test_the_registry_knows_the_import_folder():
    from backend import data_registry as R
    e = R.classify("imports/0123456789abcdef/archive.zip") if hasattr(R, "classify") else None
    pats = [f.pattern for f in R.FILES]
    assert "imports/**" in pats
    f = R.FILES[pats.index("imports/**")]
    assert f.cls == R.DERIVED and f.where == R.ROOT and f.backup == R.NEVER
    assert e is None or e.pattern == "imports/**"
