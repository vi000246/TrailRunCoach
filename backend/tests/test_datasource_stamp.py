"""The FIT folder part of datasource.source_stamp is not rescanned on every _dataset() call
(SP-362): kept FILES_STAMP_TTL_S seconds while the folder / year folders keep their mtimes;
files_changed() (an import, a purge) drops it. Synthetic files in the conftest temp FIT root."""
import os
import time

from backend.engine.wko5expr import datasource as DS
from backend.sync import storage


def _setup(monkeypatch):
    calls = []
    real = DS._scan

    def counting(source, root):
        calls.append(source)
        return real(source, root)
    monkeypatch.setattr(DS, "_scan", counting)
    DS.files_changed()
    d = storage.year_dir("coros", 2026)
    (d / "1_2026-10-01_run.fit").write_bytes(b"x" * 20)
    return d, calls


def test_repeated_stamps_scan_the_folder_once(monkeypatch, tmp_path):
    _d, calls = _setup(monkeypatch)
    a = DS._files_stamp("coros", tmp_path)
    for _ in range(10):
        assert DS._files_stamp("coros", tmp_path) == a
    assert calls == ["coros"]


def test_a_new_removed_or_renamed_file_shows_at_once(monkeypatch, tmp_path):
    d, calls = _setup(monkeypatch)
    a = DS._files_stamp("coros", tmp_path)
    time.sleep(0.02)                              # a coarse directory mtime clock
    (d / "2_2026-10-02_run.fit").write_bytes(b"y" * 20)
    b = DS._files_stamp("coros", tmp_path)
    assert b != a and b.startswith("coros:2:")
    time.sleep(0.02)
    (d / "2_2026-10-02_run.fit").rename(d / "2_2026-10-02_trailrun.fit")
    c = DS._files_stamp("coros", tmp_path)
    assert c != b and c.startswith("coros:2:")
    time.sleep(0.02)
    (d / "1_2026-10-01_run.fit").unlink()
    assert DS._files_stamp("coros", tmp_path).startswith("coros:1:")


def test_an_in_place_rewrite_shows_after_the_ttl_or_files_changed(monkeypatch, tmp_path):
    d, calls = _setup(monkeypatch)
    f = d / "1_2026-10-01_run.fit"
    a = DS._files_stamp("coros", tmp_path)
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # same name, newer mtime
    assert DS._files_stamp("coros", tmp_path) == a                      # within the TTL: kept
    DS.files_changed()                                                  # an import says so
    b = DS._files_stamp("coros", tmp_path)
    assert b != a
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 9_000_000_000))
    monkeypatch.setattr(DS, "FILES_STAMP_TTL_S", 0.0)                   # the TTL ran out
    DS.files_changed()
    DS._files_stamp("coros", tmp_path)
    assert DS._files_stamp("coros", tmp_path) != b


def test_the_db_part_is_read_on_every_call(monkeypatch, tmp_path):
    _setup(monkeypatch)
    seen = iter(["db1", "db2"])
    monkeypatch.setattr(DS, "db_stamp", lambda: next(seen))
    assert DS._files_stamp("coros", tmp_path).endswith(":db1")
    assert DS._files_stamp("coros", tmp_path).endswith(":db2")
