"""
SP-911: Apple Health's export.xml is read in parts and a restart goes on from the last checkpoint
(files/apple_health.py scan_export with a key, sync/archive.py SCAN_PART_BYTES). All synthetic
(SP-833's export.xml builder): no real export, no WKO5 folder.
"""
from __future__ import annotations

import io
import json
import tracemalloc
import zipfile
from datetime import timedelta

import pytest

from backend.files import apple_health as AH
from backend.sync import archive as AR
from backend.tests.test_export_formats_sp833 import _apple_export, _apple_xml, _reader
from backend.tests.test_export_import_sp349 import _counts, _process_all, _upload, imports  # noqa: F401
from backend.tests.test_fit_upload_sp323 import START
from backend.tests.test_sync_e2e import make_session, run


def _xml(n_hr=3000) -> bytes:
    """Two workouts (one with a route) after n_hr heart-rate samples, most outside them."""
    run_ = {"type": "HKWorkoutActivityTypeRunning", "start": START, "end": START + timedelta(seconds=600),
            "km": 1.8, "route": "route_a.gpx"}
    walk = {"type": "HKWorkoutActivityTypeWalking", "start": START + timedelta(days=1),
            "end": START + timedelta(days=1, seconds=900), "stats_km": 1.2}
    hr = [(START - timedelta(days=2) + timedelta(seconds=6 * i), 70) for i in range(n_hr)]
    hr += [(START + timedelta(seconds=i), 120 + i // 30) for i in range(0, 600, 5)]
    hr += [(START + timedelta(days=1, seconds=i), 100) for i in range(0, 900, 10)]
    return _apple_xml([run_, walk], hr)


def _junk_before(xml: bytes, at: int) -> bytes:
    """The file with everything before `at` garbled: parsing any of it again fails."""
    return b"\x00" * at + xml[at:]


def _strip(res):
    return {k: v for k, v in res.items() if k != "paused"}


def test_a_pass_in_parts_gives_what_one_pass_gives_and_parses_each_part_once(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 4096)
    monkeypatch.setattr(AH, "CHECK_BYTES", 50_000)
    xml = _xml()
    whole = AH.scan_export(_reader(xml)[0], tmp_path / "one", base="x/")
    s = tmp_path / "parts"
    stops, res = [], None
    for _ in range(100):
        # the bytes before the last pause are inflated again but never parsed: garbled, nothing changes
        data = _junk_before(xml, stops[-1]) if stops else xml
        res = AH.scan_export(_reader(data)[0], s, base="x/", key="k", budget=60_000)
        if not res.get("paused"):
            break
        stops.append(res["pos"])
        assert AH.progress(s) == res["pos"] and (s / "hr.bin").exists()
    assert len(stops) >= 3 and stops == sorted(stops) and stops[-1] < len(xml)
    # each pause is between two children of <HealthData>: the next part starts on a '<Record' / '<Workout'
    assert all(xml[p:p + 8] in (b"<Record ", b"<Workout") for p in stops)
    assert _strip(res) == whole and len(whole["workouts"]) == 2 and len(whole["workouts"][0]["hr"]) == 120
    assert not any(s.iterdir())                                           # the scratch files are gone


def test_a_restart_goes_on_from_the_last_checkpoint_without_counting_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 4096)
    monkeypatch.setattr(AH, "CHECK_BYTES", 40_000)
    xml = _xml()
    whole = AH.scan_export(_reader(xml)[0], tmp_path / "one")
    s = tmp_path / "s"
    read, _ = _reader(xml)

    def dies(n):                                                         # the worker dies two thirds in
        b = read(n)
        if dies.at + len(b) > 2 * len(xml) // 3:
            raise RuntimeError("worker died")
        dies.at += len(b)
        return b
    dies.at = 0
    with pytest.raises(RuntimeError):
        AH.scan_export(dies, s, key="k")
    at = AH.progress(s)
    ck = json.loads((s / "checkpoint.json").read_text("utf-8"))
    assert at and 40_000 <= at < 2 * len(xml) // 3
    assert (s / "hr.bin").stat().st_size == ck["pairs"]
    import numpy as np
    late = np.zeros(50, dtype=AH._PAIR)                                   # flushed after the checkpoint
    late["t"], late["v"] = START.timestamp() + 7, 200                    # (inside the run) before it died
    with open(s / "hr.bin", "ab") as f:
        f.write(late.tobytes())
    # 重試: from the checkpoint, not the start (the bytes before it garbled change nothing)
    res = AH.scan_export(_reader(_junk_before(xml, at))[0], s, key="k")
    assert _strip(res) == whole                                           # the late samples not twice


def test_a_checkpoint_of_another_file_or_a_broken_one_starts_over(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 4096)
    monkeypatch.setattr(AH, "CHECK_BYTES", 30_000)
    a, b = _xml(3000), _xml(2000)
    s = tmp_path / "s"
    assert AH.scan_export(_reader(a)[0], s, key="a", budget=40_000)["paused"]
    want = AH.scan_export(_reader(b)[0], tmp_path / "one")
    assert _strip(AH.scan_export(_reader(b)[0], s, key="b")) == want      # another file: from its start
    # broken: the heart-rate file shorter than the checkpoint says, then a checkpoint that is no JSON
    assert AH.scan_export(_reader(a)[0], s, key="a", budget=40_000)["paused"]
    with open(s / "hr.bin", "r+b") as f:
        f.truncate(10)
    assert _strip(AH.scan_export(_reader(a)[0], s, key="a")) == AH.scan_export(_reader(a)[0], tmp_path / "two")
    assert AH.scan_export(_reader(a)[0], s, key="a", budget=40_000)["paused"]
    (s / "checkpoint.json").write_text("{", "utf-8")
    assert _strip(AH.scan_export(_reader(a)[0], s, key="a")) == AH.scan_export(_reader(a)[0], tmp_path / "three")
    # the format of the scratch files changed: start over too
    assert AH.scan_export(_reader(a)[0], s, key="a", budget=40_000)["paused"]
    monkeypatch.setattr(AH, "FORMAT", "next")
    assert AH._load_checkpoint(s, "a") is None


def test_a_refused_export_leaves_no_scratch_files(tmp_path):
    for bad in (b"<x/>", b"not xml", b"<HealthData><Record type='a'/><Workout"):
        with pytest.raises(AH.AppleError):
            AH.scan_export(_reader(bad)[0], tmp_path / "s", key="k")
        assert not any((tmp_path / "s").iterdir())


def test_the_byte_index_is_put_right_past_4_gb():
    assert AH.unwrap(1234, 5000) == 1234                                  # a 64-bit index: as it is
    true = 5 * 1024 ** 3 + 77
    assert AH.unwrap(true % (1 << 32), true + 3_000_000) == true          # unsigned 32-bit
    true = 3 * 1024 ** 3                                                  # past 2 GB as a signed 32-bit long
    assert AH.unwrap(true - (1 << 32), true + 10) == true


def test_memory_stays_flat_when_the_pass_is_written_down(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "CHECK_BYTES", 256 * 1024)

    def peak(n):
        xml = _xml(n)
        tracemalloc.start()
        tracemalloc.reset_peak()
        AH.scan_export(_reader(xml)[0], tmp_path / f"s{n}", key="k")
        p = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return p, len(xml)
    small, big = peak(10_000), peak(200_000)
    assert big[1] > 15 * small[1]
    assert big[0] < small[0] + 8 * 1024 * 1024 and big[0] < 32 * 1024 * 1024


# ---------------------------------------------------------------------------- through the import

def test_an_apple_export_is_read_over_several_parts_and_imported_once(tmp_path, imports, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 1024)
    monkeypatch.setattr(AH, "CHECK_BYTES", 2000)
    monkeypatch.setattr(AR, "SCAN_PART_BYTES", 6000)

    async def go():
        s = await make_session(tmp_path)
        data, _ = _apple_export()
        size = zipfile.ZipFile(io.BytesIO(data)).getinfo("apple_health_export/export.xml").file_size
        imp = _upload(data)
        d = AR.root() / imp
        first = await AR.process(s, d, 1)                                  # one part: a share of export.xml
        assert first == {"more": 1, "added": 0, "stopped": False}
        assert not (d / "manifest.json").exists() and 6000 <= AH.progress(d / "apple-scratch") < size
        st = AR.status(imp)
        assert st["total"] is None and st["done"] == 0
        parts = await _process_all(s, imp)
        assert len(parts) >= 2 and all(p["more"] == 1 and p["added"] == 0 for p in parts[:-1])
        st = AR.status(imp)
        assert st["kind"] == "apple_health" and st["total"] == 2 and st["done"] == 2
        assert _counts(st) == (2, 0, 0, 1 + 1)       # the run and the walk; the lone route and export_cda.xml
        assert not (d / "apple-scratch").exists()
        assert AR._read_json(d / "manifest.json")["inflated"] == size      # export.xml counted once
    run(go())


def test_an_interrupted_read_goes_on_from_its_checkpoint_and_the_cap_clears_it(tmp_path, imports, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 1024)
    monkeypatch.setattr(AH, "CHECK_BYTES", 2000)
    monkeypatch.setattr(AR, "SCAN_PART_BYTES", 6000)

    async def go():
        s = await make_session(tmp_path)
        data, _ = _apple_export()
        imp = _upload(data)
        d = AR.root() / imp
        await AR.process(s, d, 1)
        at = AH.progress(d / "apple-scratch")
        real, began = AH.scan_export, []

        def scan(read, scratch, **kw):                                   # where each later part starts
            began.append(AH.progress(scratch))
            return real(read, scratch, **kw)
        monkeypatch.setattr(AH, "scan_export", scan)
        await _process_all(s, imp)                                       # 重試 after a restart: the same call
        assert at and began[0] == at and began == sorted(began)          # from the checkpoint, never from 0
        assert _counts(AR.status(imp))[:3] == (2, 0, 0)
        monkeypatch.setattr(AH, "scan_export", real)
        # past the inflate cap in a later part: listed as such, the scratch files gone
        monkeypatch.setattr(AR, "MAX_INFLATE_BYTES", 9000)
        imp2 = _upload(data, name="again.zip")
        d2 = AR.root() / imp2
        await AR.process(s, d2, 1)
        assert (d2 / "apple-scratch").exists()
        await _process_all(s, imp2)
        st = AR.status(imp2)
        assert st["total"] == 0 and st["skipped_files"]["too_large_inflated"]["n"] == 1
        assert not (d2 / "apple-scratch").exists()
    run(go())


def test_dropping_the_data_drops_the_scratch_files(tmp_path, imports, monkeypatch):
    monkeypatch.setattr(AR, "SCAN_PART_BYTES", 6000)
    monkeypatch.setattr(AH, "CHECK_BYTES", 2000)

    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_apple_export()[0])
        d = AR.root() / imp
        await AR.process(s, d, 1)
        assert (d / "apple-scratch").exists()
        AR._drop_data(d)
        assert not (d / "apple-scratch").exists()
    run(go())
