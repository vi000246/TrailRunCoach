"""Offline tests for backend/scripts/intervals_probe.py: session steps ->
intervals.icu workout text / event, and original-file decoding. No network."""
import gzip
import io
import zipfile

import pytest

from backend.scripts import intervals_probe as IP
from backend.sync import coros_workouts as CW

TH = {"cp": 300, "lthr": 170, "aet": 150}


def sess(kind, title, minutes, detail="", day="2026-10-01"):
    return {"id": kind, "kind": kind, "title": title, "minutes": minutes, "target": "",
            "detail": detail, "source": "", "tss": 30.0, "day": day, "done": False}


def test_easy_run_is_one_lthr_line():
    ev = IP.session_event(sess("easy", "越野輕鬆跑", 45), TH)
    assert ev["category"] == "WORKOUT" and ev["type"] == "Run"
    assert ev["start_date_local"] == "2026-10-01T00:00:00"
    assert ev["name"] == "TRC 越野輕鬆跑 10/1" and ev["external_id"] == "trc-easy"
    lo, hi = round(round(0.75 * 170) / 170 * 100), round(150 / 170 * 100)
    assert ev["description"] == f"- 主課 45m {lo}-{hi}% LTHR"


def test_threshold_intervals_repeat_block():
    s = sess("quality", "閾值 3×10 分", 60, detail="休 2–3 分鐘；暖身 15 分、緩和 10 分")
    text = IP.session_event(s, TH)["description"]
    lines = text.split("\n")
    assert lines[0].startswith("- 暖身 15m ") and lines[0].endswith("% LTHR")
    assert lines[1] == "" and lines[2] == "Main Set 3x"
    assert lines[3] == f"- 主課 10m {round(0.95 * 300)}-{round(1.01 * 300)}w"
    assert lines[4] == "- 恢復 3m"
    assert lines[5] == "" and lines[6].startswith("- 緩和 10m ")
    assert len(lines) == 7


def test_power_can_be_left_out():
    s = sess("quality", "閾值 3×10 分", 60, detail="休 2–3 分鐘；暖身 15 分、緩和 10 分")
    text = IP.session_event(s, TH, power_targets=False)["description"]
    assert "- 主課 10m" in text.split("\n") and not any(l.endswith("w") for l in text.split("\n"))


def test_no_lthr_means_no_hr_target():
    ev = IP.session_event(sess("long", "山路長跑", 90), {"cp": 300})
    assert ev["description"] == "- 主課 1h30m"


def test_durations():
    assert IP._duration(45) == "45s" and IP._duration(90) == "1m30s" and IP._duration(3600) == "1h"
    with pytest.raises(ValueError):
        IP._duration(0)


def test_cues_have_no_digits():
    # step names like 「10 分」 are dropped: digits in a cue could parse as a duration
    text = IP.session_event(sess("easy", "輕鬆跑＋6×20 秒衝刺", 40), TH)["description"]
    for line in text.split("\n"):
        if line.startswith("- "):
            cue = line[2:].split(" ")[0]
            assert not any(ch.isdigit() for ch in cue)
    assert "Main Set 6x" in text and "- 主課 20s" in text and "- 恢復 1m" in text


@pytest.mark.parametrize("kind", ["strength", "race", "rest", "heat_passive"])
def test_unsupported_kinds(kind):
    with pytest.raises(CW.Unsupported):
        IP.session_event(sess(kind, "x", 30), TH)


def test_the_push_test_event():
    ev = IP.test_event("2026-10-03", lthr=170)
    assert ev["name"] == IP.TEST_NAME == "TrailRunCoach 測試課表（可刪除）"
    assert ev["external_id"] == IP.EXTERNAL_ID
    assert ev["description"].split("\n") == ["- 暖身 5m 70-80% LTHR", "", "Main Set 2x",
                                             "- 主課 1m", "- 恢復 1m", "", "- 緩和 5m"]


def _fit(n=20):
    b = bytearray(n)
    b[0] = 14
    b[8:12] = b".FIT"
    return bytes(b)


def test_decode_file_plain_gzip_zip():
    assert IP.decode_file(_fit()) == (_fit(), ".fit")
    assert IP.decode_file(gzip.compress(_fit())) == (_fit(), ".fit")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.fit", _fit())
    assert IP.decode_file(buf.getvalue()) == (_fit(), ".fit")
    assert IP.decode_file(b"<?xml?><gpx version")[1] == ".gpx"
    assert IP.decode_file(b"nothing")[1] == ".bin"


def test_preview_runs_offline(capsys):
    assert IP.main(["--preview", "--date", "2026-10-03"]) == 0
    assert IP.TEST_NAME in capsys.readouterr().out
