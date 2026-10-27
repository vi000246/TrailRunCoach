"""
SP-957: while Apple Health's export.xml is read in parts (SP-911) the import says how far — the
status's `reading` ({pos, size}: the checkpoint's position, export.xml's size from its key) and the
page's 「正在讀取 Apple 健康資料 N%」 instead of 「排隊中」. No checkpoint (not begun, read, not an
Apple export): no `reading`, the page as before. All synthetic (SP-833's export.xml builder).
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from backend.files import apple_health as AH
from backend.sync import archive as AR
from backend.tests.test_apple_resume_sp911 import _xml
from backend.tests.test_export_formats_sp833 import _apple_export, _reader
from backend.tests.test_export_import_sp349 import _garmin_export, _process_all, _upload, imports  # noqa: F401
from backend.tests.test_sync_e2e import make_session, run

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")


# ---------------------------------------------------------------------------- the checkpoint's head

def test_reading_gives_the_position_and_key_of_the_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 4096)
    monkeypatch.setattr(AH, "CHECK_BYTES", 50_000)
    s = tmp_path / "s"
    assert AH.reading(s) is None and AH.progress(s) is None                    # no pass yet
    key = 'apple_health_export/匯出 "a|b"/export.xml|123|45|6|7'                # escapes, a '|', not ASCII
    res = AH.scan_export(_reader(_xml())[0], s, key=key, budget=60_000)
    assert res["paused"] and AH.reading(s) == (res["pos"], key) and AH.progress(s) == res["pos"]


def test_reading_takes_only_the_head_of_a_big_checkpoint(tmp_path):
    s = tmp_path / "s"
    s.mkdir()
    workouts = [{"start": i, "end": i + 600, "type": "Running", "route": f"route_{i}.gpx"} for i in range(20_000)]
    (s / AH.CHECKPOINT).write_text(json.dumps({"key": f"{AH.FORMAT}|x|900", "pos": 300, "pairs": 0,
                                               "workouts": workouts}), "utf-8")
    assert (s / AH.CHECKPOINT).stat().st_size > 64 * 1024
    assert AH.reading(s) == (300, "x|900")


@pytest.mark.parametrize("text", [
    "",                                                        # empty
    "{not json",
    json.dumps({"pos": 5, "key": f"{AH.FORMAT}|x|9"}),         # not as save() writes it
    json.dumps({"key": "1|x|9", "pos": 5}),                    # another format
    json.dumps({"key": 7, "pos": 5}),
])
def test_a_broken_or_foreign_checkpoint_is_no_reading(tmp_path, text):
    s = tmp_path / "s"
    s.mkdir()
    (s / AH.CHECKPOINT).write_text(text, "utf-8")
    assert AH.reading(s) is None and AH.progress(s) is None


# ---------------------------------------------------------------------------- the status

def test_the_status_says_how_far_export_xml_is_read_and_drops_it_once_listed(tmp_path, imports, monkeypatch):
    monkeypatch.setattr(AH, "READ_STEP", 1024)
    monkeypatch.setattr(AH, "CHECK_BYTES", 2000)
    monkeypatch.setattr(AR, "SCAN_PART_BYTES", 6000)

    async def go():
        s = await make_session(tmp_path)
        data, _ = _apple_export()
        size = zipfile.ZipFile(io.BytesIO(data)).getinfo("apple_health_export/export.xml").file_size
        imp = _upload(data)
        d = AR.root() / imp
        assert AR.status(imp)["reading"] is None                               # not begun: no checkpoint
        seen = []
        while True:
            r = await AR.process(s, d, 1)
            st = AR.status(imp)
            if st["total"] is not None:
                break
            assert r["more"] == 1
            assert st["reading"]["pos"] == AH.progress(d / "apple-scratch")     # the checkpoint's position
            seen.append(st["reading"])
        assert len(seen) >= 2 and all(x["size"] == size and 0 < x["pos"] < size for x in seen)
        assert [x["pos"] for x in seen] == sorted(x["pos"] for x in seen) and seen[0]["pos"] < seen[-1]["pos"]
        assert st["reading"] is None                                           # listed: the count as before
        await _process_all(s, imp)
        assert AR.status(imp)["reading"] is None and AR.status(imp)["total"] == 2
    run(go())


def test_an_export_that_is_not_apple_has_no_reading(tmp_path, imports):
    async def go():
        s = await make_session(tmp_path)
        imp = _upload(_garmin_export())
        assert AR.status(imp)["reading"] is None
        await _process_all(s, imp)
        assert AR.status(imp)["reading"] is None
    run(go())


def test_a_checkpoint_of_a_broken_key_is_no_reading(tmp_path, imports):
    imp = _upload(_garmin_export())
    scratch = AR.root() / imp / "apple-scratch"
    scratch.mkdir()
    for key, want in ((f"{AH.FORMAT}|export.xml|x|1|2|3", None), (f"{AH.FORMAT}|export.xml|0|1|2|3", None),
                      (f"{AH.FORMAT}|a|b|export.xml|500|1|2|3", {"pos": 200, "size": 500}),
                      (f"{AH.FORMAT}|export.xml|100|1|2|3", {"pos": 100, "size": 100})):   # past the end: capped
        (scratch / AH.CHECKPOINT).write_text(json.dumps({"key": key, "pos": 200}), "utf-8")
        assert AR._reading(AR.root() / imp) == want, key


# ---------------------------------------------------------------------------- the page

LINE = r"""
const vm = require("vm"), fs = require("fs");
const w = { I18N: { t: (k, p) => k.replace("common.expimport.", "") + (p ? " " + JSON.stringify(p) : "") } };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window: w, setTimeout, Promise, JSON });
const L = w.ExportImport.line;
console.log(JSON.stringify({
  reading: L({ state: "running", total: null, reading: { pos: 256, size: 1024 } }),
  queued_reading: L({ state: "queued", total: null, position: 2, reading: { pos: 1023, size: 1024 } }),
  start: L({ state: "running", total: null, reading: { pos: 0, size: 1024 } }),
  none: L({ state: "running", total: null, reading: null }),
  absent: L({ state: "queued", total: null, position: 3 }),
  zero: L({ state: "running", total: null, reading: { pos: 5, size: 0 } }),
  listed: L({ state: "running", total: 8, done: 3, reading: null }),
}));
"""


@pytest.fixture(scope="module")
def lines():
    if NODE is None:
        pytest.skip("node not installed")
    p = subprocess.run([NODE, "-e", LINE, str(STATIC / "exportimport.js")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_page_says_how_far_while_reading_and_as_before_otherwise(lines):
    assert lines["reading"] == {"v": 25, "max": 100, "text": 'reading {"pct":25}'}
    assert lines["queued_reading"] == {"v": 99, "max": 100, "text": 'reading {"pct":99}'}   # never 100 before the list
    assert lines["start"] == {"v": 0, "max": 100, "text": 'reading {"pct":0}'}
    assert lines["none"] == {"v": 0, "max": None, "text": "queued"}                         # no checkpoint: as before
    assert lines["absent"] == {"v": 0, "max": None, "text": 'queued_n {"n":3}'}
    assert lines["zero"] == {"v": 0, "max": None, "text": "queued"}
    assert lines["listed"] == {"v": 3, "max": 8, "text": 'working {"done":3,"total":8}'}


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_reading_text_takes_the_percentage(loc):
    common = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
    assert "{pct}%" in common["expimport.reading"] and "Apple" in common["expimport.reading"]
