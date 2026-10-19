"""SP-388 完成條件 2、3: the daily ops report (backend/scripts/ops_report.py) from the logs the server already
writes — each job's perf.log line (tenant, class, CPU seconds, wait) and sync_events.jsonl (COROS / Garmin
pushing back). Synthetic files only."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from backend import applog
from backend.scripts import ops_report as R

DAY = dt.date(2026, 10, 18)


def _job(day, tenant, cpu, cls="single", wait=0.5, label="warm-up readiness (sync)"):
    return f"{day} 03:10:00,123\tjob\t1.234\t{label} cls={cls} tenant={tenant} cpu={cpu} wait={wait} status=done"


def _logs(tmp_path):
    (tmp_path / "perf.log.2026-10-17").write_text("\n".join([
        _job("2026-10-17", "u3", 400.0),                       # earlier this month
        _job("2026-09-30", "u3", 900.0),                       # last month: not in October's budget
        "2026-10-17 03:00:00,000\tchart\t0.5\tGET /api/v1/app/x status=200",   # not a job line
    ]) + "\n", "utf-8")
    (tmp_path / "perf.log").write_text("\n".join([
        _job("2026-10-18", "u3", 250.0),
        _job("2026-10-18", "u3", 10.0, label="dataset warm-up (deploy)", cls="full"),
        _job("2026-10-18", "u4", 30.0),
        _job("2026-10-18", "owner", 120.0),
        _job("2026-10-18", "u5", 5.0, cls="backtest", wait=12.0),
        _job("2026-10-18", "u3", 1.0, cls="backtest", wait=40.0),
        _job("2026-10-19", "u4", 999.0),                       # the next day: not in the 18th's report
    ]) + "\n", "utf-8")
    return tmp_path


def _at(h, m=0, day=DAY):
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=R.TZ).timestamp()


def _events(tmp_path):
    p = tmp_path / "sync_events.jsonl"
    rows = [
        {"at": _at(8), "source": "coros", "event": "http_429", "tenant": "u3"},
        {"at": _at(8, 5), "source": "coros", "event": "http_429", "tenant": "u4"},
        {"at": _at(9), "source": "coros", "event": "account_paused", "tenant": "u3", "reason": "429"},
        {"at": _at(15), "source": "coros", "event": "account_resumed", "tenant": "u3"},
        {"at": _at(10), "source": "garmin", "event": "http_403", "tenant": "u5"},
        {"at": _at(10, 1), "source": "garmin", "event": "account_paused", "tenant": "u5", "reason": "403"},
        {"at": _at(11), "source": "coros", "event": "sync_ok", "tenant": "u4"},       # not a block
        {"at": _at(9, day=DAY - dt.timedelta(days=1)), "source": "coros", "event": "http_429", "tenant": "u9"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", "utf-8")
    return p


def _accounts(tmp_path):
    db = tmp_path / "accounts.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, plan TEXT, plan_expires_at TEXT, is_test INTEGER)")
    con.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", [
        (3, "free", None, 0), (4, "subscriber", "2027-01-01", 0), (5, "subscriber", "2026-10-01", 0), (6, "free", None, 1)])
    con.commit()
    con.close()
    return db


def _report(tmp_path):
    folder = _logs(tmp_path)
    return R.report(DAY, R.jobs(R.perf_files(folder)), R.events(_events(tmp_path)), R.plans(_accounts(tmp_path), DAY))


def test_the_job_lines_are_the_ones_the_queue_writes(tmp_path, monkeypatch):
    # the format R parses is the one jobs._account writes through applog.perf
    from backend import jobs
    lines = []
    monkeypatch.setattr(applog, "perf", lambda area, what, seconds, **f: lines.append(
        f"2026-10-18 03:10:00,123\t{area}\t{seconds:.3f}\t{what}{applog._fields(f)}"))
    job = jobs.Job(id=1, fn=lambda j: None, cls="backtest", priority=0, label="race backtest", tenant="u7", key=None,
                   ctx=None, state="done", enqueued=0.0, started=2.5, finished=4.0, cpu_s=1.25)
    jobs._account(job)
    (tmp_path / "perf.log").write_text("\n".join(lines) + "\n", "utf-8")
    js = R.jobs(R.perf_files(tmp_path))
    assert js == [{"day": "2026-10-18", "label": "race backtest", "seconds": 1.5, "tenant": "u7", "cls": "backtest",
                   "cpu": 1.25, "wait": 2.5, "status": "done"}]


def test_cpu_seconds_per_tenant_on_the_day(tmp_path):
    r = _report(tmp_path)
    by = {t["tenant"]: t for t in r["tenants"]}
    assert by["u3"]["cpu_s"] == 261.0 and by["u3"]["jobs"] == 3
    assert by["u4"]["cpu_s"] == 30.0 and by["owner"]["cpu_s"] == 120.0 and by["u5"]["cpu_s"] == 5.0
    assert [t["tenant"] for t in r["tenants"]] == ["u3", "owner", "u4", "u5"]          # the most first
    assert r["cpu_total_s"] == 416.0
    assert by["u3"]["plan"] == "free" and by["u4"]["plan"] == "paid" and by["u5"]["plan"] == "free"  # u5 expired


def test_the_free_tier_over_its_monthly_budget(tmp_path):
    r = _report(tmp_path)
    # u3: 400 + 261 = 661 s in October (September's 900 not counted) > 600 s; u4 pays; u5 is far below
    assert r["free_over_budget"] == [{"tenant": "u3", "cpu_s_month": 661.0, "share": 1.1, "over": True}]
    assert R.FREE_CPU_S_PER_MONTH == 600.0


def test_backtest_queue_wait(tmp_path):
    assert _report(tmp_path)["backtest"] == {"jobs": 2, "wait_median_s": 26.0, "wait_max_s": 40.0}


def test_coros_and_garmin_blocks_and_recovery(tmp_path):
    s = _report(tmp_path)["sources"]
    assert s["coros"]["counts"] == {"http_429": 2, "account_paused": 1}                 # not the day before
    assert s["coros"]["paused"] == [{"tenant": "u3", "reason": "429", "recovered_after_h": 6.0}]
    assert s["garmin"]["counts"] == {"http_403": 1, "account_paused": 1}
    assert s["garmin"]["paused"] == [{"tenant": "u5", "reason": "403", "recovered_after_h": None}]


def test_without_accounts_db_everyone_but_the_owner_counts_as_free(tmp_path):
    folder = _logs(tmp_path)
    r = R.report(DAY, R.jobs(R.perf_files(folder)), [], R.plans(tmp_path / "none.db", DAY))
    assert {t["tenant"]: t["plan"] for t in r["tenants"]}["owner"] == "owner"
    assert {f["tenant"] for f in r["free_over_budget"]} == {"u3"}
    assert r["sources"]["coros"] == {"counts": {}, "paused": []}


def test_the_text_and_the_command_line(tmp_path, capsys):
    folder = _logs(tmp_path)
    ev, db = _events(tmp_path), _accounts(tmp_path)
    assert R.main(["--date", "2026-10-18", "--logs", str(folder), "--events", str(ev), "--accounts-db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "## 營運報表 2026-10-18" in out and "| u3 | free | 261.0 | 3 |" in out
    assert "u3：本月 11.0 分（110%），**超過**" in out
    assert "2 次；等待中位數 26.0 秒、最久 40.0 秒" in out
    assert "COROS：http_429 2 次、account_paused 1 次" in out and "6.0 小時後恢復" in out and "還沒恢復" in out
    assert R.main(["--date", "2026-10-18", "--days", "2", "--json", "--logs", str(folder), "--events", str(ev),
                   "--accounts-db", str(db)]) == 0
    days = json.loads(capsys.readouterr().out)
    assert [d["day"] for d in days] == ["2026-10-17", "2026-10-18"]
    # read-only: the logs and accounts.db are as they were
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 4
