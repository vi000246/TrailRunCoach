"""SP-1009: the daily ops report's 夜間重算 section — the whole-Dataset jobs (`cls=full`) of the night
01:00–09:00 as 同步暖機 / 教練彙總 / 部署暖機: how many, wall-clock and CPU seconds, the last end and the
longest wait; flagged when the queue ran past 07:00. From the perf.log `job` lines the queue already
writes (their time is when the job ended). Synthetic files only."""
from __future__ import annotations

import datetime as dt
import json

from backend.scripts import ops_report as R

DAY = dt.date(2026, 10, 28)


def _job(end, label, seconds, cpu, wait=0.0, cls="full", tenant="u2", day="2026-10-28"):
    return f"{day} {end},123\tjob\t{seconds:.3f}\t{label} cls={cls} tenant={tenant} cpu={cpu} wait={wait} status=done"


def _night(tmp_path, lines):
    (tmp_path / "perf.log").write_text("\n".join(lines) + "\n", "utf-8")
    return R.jobs(R.perf_files(tmp_path))


def test_the_three_kinds_with_their_sums_last_end_and_longest_wait(tmp_path):
    js = _night(tmp_path, [
        _job("03:24:41", "dataset warm-up (sync-bg-coros)", 1003.0, 959.0, wait=2.0),
        _job("03:26:30", "warm-up calibration (sync-bg-coros)", 100.0, 90.0, wait=12.5),
        _job("04:30:00", "coach summary (night)", 30.0, 20.0, wait=300.0, tenant="u7"),
        _job("04:31:00", "coach summary (night)", 40.0, 25.0, wait=330.0, tenant="u8"),
        _job("02:10:00", "dataset warm-up (deploy)", 225.0, 206.0, tenant="owner"),
        _job("02:05:00", "dataset warm-up (startup)", 50.0, 40.0, wait=1.0),
        _job("03:28:09", "activity auto-classification", 130.0, 120.0, wait=5.0),
        _job("03:30:00", "overview status", 18.0, 12.0, cls="single"),           # not a whole-Dataset job
        _job("12:00:00", "dataset warm-up (sync-coros)", 79.0, 42.0),            # daytime
        _job("00:30:00", "dataset warm-up (sync-bg-coros)", 60.0, 50.0),         # before the night
    ])
    n = R.report(DAY, js, [], {})["night"]
    assert n["kinds"]["sync"] == {"jobs": 2, "wall_s": 1103.0, "cpu_s": 1049.0, "last_end": "03:26:30",
                                  "wait_max_s": 12.5}
    assert n["kinds"]["coach"] == {"jobs": 2, "wall_s": 70.0, "cpu_s": 45.0, "last_end": "04:31:00",
                                   "wait_max_s": 330.0}
    assert n["kinds"]["deploy"] == {"jobs": 2, "wall_s": 275.0, "cpu_s": 246.0, "last_end": "02:10:00",
                                    "wait_max_s": 1.0}
    assert n["kinds"]["other"]["jobs"] == 1
    assert n["total"] == {"jobs": 7, "wall_s": 1578.0, "cpu_s": 1460.0, "last_end": "04:31:00", "wait_max_s": 330.0}
    assert n["late"] is False


def test_a_job_running_across_the_window_edges_counts(tmp_path):
    js = _night(tmp_path, [
        _job("01:05:00", "dataset warm-up (sync-bg-coros)", 600.0, 500.0),     # started 00:55
        _job("09:20:00", "coach summary (night)", 1800.0, 1500.0),             # started 08:50, ended 09:20
        _job("09:40:00", "coach summary (night)", 600.0, 500.0),               # started 09:30: the morning
    ])
    n = R.night(DAY, js)
    assert n["kinds"]["sync"]["jobs"] == 1 and n["kinds"]["coach"]["jobs"] == 1
    assert n["total"]["last_end"] == "09:20:00" and n["late"] is True


def test_the_other_days_and_lines_without_a_time_are_left_out(tmp_path):
    js = _night(tmp_path, [
        _job("03:00:00", "coach summary (night)", 30.0, 20.0, day="2026-10-27"),
        _job("03:00:00", "coach summary (night)", 30.0, 20.0, day="2026-10-29"),
    ])
    js.append({"day": "2026-10-28", "label": "coach summary (night)", "seconds": 5.0, "tenant": "u1",
               "cls": "full", "cpu": 1.0, "wait": 0.0, "status": "done"})        # no `end`
    n = R.night(DAY, js)
    assert n["total"]["jobs"] == 0 and n["total"]["last_end"] is None and n["late"] is False


def test_the_text_flags_a_queue_past_seven(tmp_path):
    js = _night(tmp_path, [
        _job("04:30:00", "coach summary (night)", 30.0, 20.0, wait=300.0),
        _job("07:42:10", "coach summary (night)", 900.0, 800.0, wait=11000.0),
    ])
    out = R.text(R.report(DAY, js, [], {}))
    assert "### 夜間重算（01:00～09:00" in out
    assert "| 教練彙總 | 2 | 930 | 820 | 07:42:10 | 11000 |" in out
    assert "| 同步暖機 | 0 | 0 | 0 | — | — |" in out and "| 部署暖機 | 0 |" in out
    assert "| 其他 |" not in out                                                     # only when there are some
    assert "| 合計 | 2 | 930 | 820 | 07:42:10 | 11000 |" in out
    assert "**夜間佇列拖過 07:00**：最晚 07:42:10 才做完" in out


def test_the_text_of_a_quiet_night_and_one_done_by_seven(tmp_path):
    out = R.text(R.report(DAY, [], [], {}))
    assert "這晚沒有整份 Dataset 的工作" in out and "拖過" not in out
    js = _night(tmp_path, [_job("03:28:09", "dataset warm-up (sync-bg-coros)", 1003.0, 959.0, wait=2.0),
                           _job("06:59:59", "coach summary (night)", 30.0, 20.0)])
    out = R.text(R.report(DAY, js, [], {}))
    assert "最晚 06:59:59 做完（07:00 前）" in out and "拖過" not in out


def test_the_json_and_the_daily_file_carry_it(tmp_path, capsys, monkeypatch):
    (tmp_path / "perf.log").write_text(_job("07:30:00", "coach summary (night)", 30.0, 20.0) + "\n", "utf-8")
    assert R.main(["--date", "2026-10-28", "--json", "--logs", str(tmp_path), "--events", str(tmp_path / "none"),
                   "--accounts-db", str(tmp_path / "none.db")]) == 0
    n = json.loads(capsys.readouterr().out)["night"]
    assert n["late"] is True and n["kinds"]["coach"]["jobs"] == 1
    # the app's own daily file (ops_daily.build) is this text
    from backend import applog, ops_daily
    monkeypatch.setattr(applog, "perf_file", lambda: tmp_path / "perf.log")
    monkeypatch.setattr(ops_daily, "members", lambda: None)
    monkeypatch.setattr(ops_daily, "revenue", lambda at=None: None)
    from backend.sync import events as EV
    monkeypatch.setattr(EV, "path", lambda: tmp_path / "none.jsonl")
    assert "**夜間佇列拖過 07:00**" in ops_daily.build(DAY)
