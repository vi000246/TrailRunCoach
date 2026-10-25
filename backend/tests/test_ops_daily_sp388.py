"""SP-388 (Q1-3 / Q1-4, the user 2026-10-20): the daily ops report written by the app itself — every day after
03:30 local for the day before, into <data folder>/logs/ops/YYYY-MM-DD.md, 31 days kept — the member
signal (members against the sign-up cap, flagged from 80 %) in the report, in metrics.json for Telegraf,
and the admin page's 營運報表 tab (backend/ops_daily.py, api/admin.py)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3

import pytest

from backend import ops_daily as OD
from backend.scripts import ops_report as R

TZ = R.TZ


def _at(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=TZ)


@pytest.fixture
def ops(tmp_path, monkeypatch):
    """The ops folder in tmp, a perf.log with one job of the 19th, no sync events, single-user mode."""
    from backend import applog
    from backend.sync import events as EV
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "perf.log").write_text(
        "2026-10-19 10:00:00,000\tjob\t2.000\twarm-up cls=single tenant=u3 cpu=42.5 wait=1.0 status=done\n", "utf-8")
    monkeypatch.setattr(applog, "perf_file", lambda: logs / "perf.log")
    monkeypatch.setattr(EV, "PATH", str(logs / "sync_events.jsonl"))
    monkeypatch.setattr(OD, "_last_metrics", {"at": 0.0})
    monkeypatch.setattr(OD, "members", lambda: None)
    return logs / "ops"


# ---------------------------------------------------------------------------- the member signal

def test_member_signal_from_80_percent_of_the_cap():
    assert R.MEMBERS_WARN_SHARE == 0.8
    assert R.member_signal(79, 100) == {"count": 79, "cap": 100, "share": 0.79, "near": False, "full": False}
    assert R.member_signal(80, 100)["near"] is True and R.member_signal(80, 100)["full"] is False
    assert R.member_signal(400, 500)["near"] is True                       # the later cap (500)
    assert R.member_signal(100, 100) == {"count": 100, "cap": 100, "share": 1.0, "near": True, "full": True}
    assert R.member_signal(None, 100) is None and R.member_signal(5, None) is None


def test_the_member_count_is_the_caps_count_read_only(tmp_path):
    db = tmp_path / "accounts.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, plan TEXT, plan_expires_at TEXT, is_test INTEGER)")
    con.executemany("INSERT INTO users VALUES (?, 'free', NULL, ?)", [(1, 0), (2, 0), (3, 1)])
    con.commit()
    con.close()
    assert R.member_count(db) == 2                                          # never a test account
    assert R.member_count(tmp_path / "none.db") is None and R.member_count(None) is None


def test_the_report_text_says_near_and_full():
    base = R.report(dt.date(2026, 10, 19), [], [], {})
    assert base["members"] is None and "會員數" not in R.text(base)       # owner mode: no section
    near = R.text(R.report(dt.date(2026, 10, 19), [], [], {}, R.member_signal(85, 100)))
    assert "### 會員數（封頂的 80% 起提醒）" in near and "85 / 100 人（85%），**接近封頂**" in near
    ok = R.text(R.report(dt.date(2026, 10, 19), [], [], {}, R.member_signal(10, 100)))
    assert "10 / 100 人（10%）" in ok and "接近" not in ok
    full = R.text(R.report(dt.date(2026, 10, 19), [], [], {}, R.member_signal(100, 100)))
    assert "**已滿**" in full


def test_the_command_line_reports_the_members_too(tmp_path, monkeypatch, capsys):
    from backend import site_config as SC, site_settings as SS
    from backend.tests.test_ops_report_sp388 import _accounts, _logs
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "4")
    SS.reset_cache()
    db = _accounts(tmp_path)                                                # 4 accounts, one of them a test one
    assert R.main(["--date", "2026-10-18", "--logs", str(_logs(tmp_path)), "--events", str(tmp_path / "none.jsonl"),
                   "--accounts-db", str(db)]) == 0
    assert "3 / 4 人（75%）" in capsys.readouterr().out
    SS.reset_cache()


def test_members_counts_the_accounts_against_the_cap(tmp_path, monkeypatch):
    from backend import accounts, site_config as SC, site_settings as SS
    from backend.tests.test_membership_sp330 import _user
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    monkeypatch.delenv("WKO5COACH_MAX_USERS", raising=False)
    SS.reset_cache()
    monkeypatch.delenv(accounts.ENV_ENABLED, raising=False)
    assert OD.members() is None                                             # single-user mode
    from backend.accounts import store, tenants as TN
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    store.migrate_owner()
    monkeypatch.setenv(accounts.ENV_ENABLED, "1")
    for _ in range(3):
        _user(store)
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "5")
    SS.reset_cache()
    m = OD.members()
    assert m == {"count": 4, "cap": 5, "share": 0.8, "near": True, "full": False}    # the owner counts, as the cap
    SS.reset_cache()


# ---------------------------------------------------------------------------- the schedule

def test_due_after_0330_for_yesterday_once(ops):
    assert OD.RUN_AT == dt.time(3, 30)
    assert OD.due(_at(2026, 10, 20, 3, 29), ops) is None                    # not yet
    assert OD.due(_at(2026, 10, 20, 3, 30), ops) == dt.date(2026, 10, 19)
    assert OD.due(_at(2026, 10, 20, 22, 0), ops) == dt.date(2026, 10, 19)  # down at 03:30: caught up later
    p = OD.run_due(_at(2026, 10, 20, 3, 31), ops)
    assert p == ops / "2026-10-19.md"
    text = p.read_text("utf-8")
    assert text.startswith("## 營運報表 2026-10-19") and "| u3 | free | 42.5 | 1 |" in text
    assert OD.due(_at(2026, 10, 20, 4, 0), ops) is None                     # written: not again
    assert OD.run_due(_at(2026, 10, 20, 4, 0), ops) is None
    assert [x.name for x in ops.iterdir() if not x.name.startswith(".")] == ["2026-10-19.md"]


def test_the_report_carries_the_member_signal_and_metrics_json(ops, monkeypatch):
    monkeypatch.setattr(OD, "members", lambda: R.member_signal(82, 100))
    p = OD.run_due(_at(2026, 10, 20, 3, 30), ops)
    assert "82 / 100 人（82%），**接近封頂**" in p.read_text("utf-8")
    m = json.loads((ops / "metrics.json").read_text("utf-8"))
    assert {k: m[k] for k in ("members", "cap", "share", "near", "full")} == \
        {"members": 82, "cap": 100, "share": 0.82, "near": 1, "full": 0}
    assert isinstance(m["at"], int)
    # hourly, not every minute
    monkeypatch.setattr(OD, "members", lambda: R.member_signal(90, 100))
    OD.run_due(_at(2026, 10, 20, 3, 31), ops)
    assert json.loads((ops / "metrics.json").read_text("utf-8"))["members"] == 82


def test_no_metrics_in_single_user_mode(ops):
    assert OD.write_metrics(ops) is None and not (ops / "metrics.json").exists()


def test_off_without_the_log_file(monkeypatch):
    monkeypatch.setenv("WKO5COACH_LOG_FILE", "0")
    assert OD.folder() is None
    assert OD.due(_at(2026, 10, 20, 5, 0)) is None and OD.write(dt.date(2026, 10, 19)) is None
    assert OD.reports() == [] and OD.read("2026-10-19") is None


def test_31_days_kept_like_perf_log(ops):
    from backend import applog
    assert OD.KEEP_DAYS == applog.PERF_KEEP_DAYS == 31
    ops.mkdir()
    today = dt.date(2026, 10, 20)
    for n in (1, 30, 31, 32, 60):
        (ops / f"{(today - dt.timedelta(days=n)).isoformat()}.md").write_text("x", "utf-8")
    (ops / "notes.md").write_text("mine", "utf-8")
    (ops / "metrics.json").write_text("{}", "utf-8")
    gone = OD.prune(ops, today=today)
    assert gone == ["2026-08-21.md", "2026-09-18.md"]                       # 60 and 32 days before
    assert sorted(p.name for p in ops.iterdir()) == ["2026-09-19.md", "2026-09-20.md", "2026-10-19.md",
                                                     "metrics.json", "notes.md"]


def test_the_retention_runs_after_each_report(ops):
    ops.mkdir()
    (ops / "2026-08-01.md").write_text("old", "utf-8")
    OD.run_due(_at(2026, 10, 20, 3, 30), ops)
    assert not (ops / "2026-08-01.md").exists() and (ops / "2026-10-19.md").exists()


def test_reports_and_read(ops):
    ops.mkdir()
    for d in ("2026-10-18", "2026-10-19"):
        (ops / f"{d}.md").write_text(f"## {d}\n", "utf-8")
    (ops / "x.md").write_text("no", "utf-8")
    assert [r["day"] for r in OD.reports(ops)] == ["2026-10-19", "2026-10-18"]
    assert OD.read("2026-10-18", ops) == "## 2026-10-18\n"
    assert OD.read("2026-10-17", ops) is None and OD.read("../x", ops) is None


def test_the_scheduler_loop_runs_the_ops_tick(monkeypatch):
    from backend import tenancy
    from backend.accounts import tenants as TN
    from backend.engine import fit_archive
    from backend.sync import check, purge, scheduler
    from backend.api import rain_backfill, weight as W
    seen = []

    async def nothing(*a, **k):
        return None

    async def ops_tick():
        seen.append("ops")

    async def stop(_s):
        raise asyncio.CancelledError
    monkeypatch.setattr(scheduler, "tick_all", nothing)
    monkeypatch.setattr(check, "STARTUP_DELAY_S", 0)
    monkeypatch.setattr(check, "weekly_tick", nothing)
    monkeypatch.setattr(purge, "cleanup_tick", nothing)
    monkeypatch.setattr(fit_archive, "tick_async", nothing)
    monkeypatch.setattr(W, "tick", nothing)
    monkeypatch.setattr(TN, "sync_tenants", lambda: [tenancy.owner()])
    monkeypatch.setenv("WKO5COACH_NO_AUTO_BACKUP", "1")
    monkeypatch.setattr(rain_backfill, "tick", nothing)
    monkeypatch.setattr(OD, "tick", ops_tick)
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.loop(session_factory=lambda: None))
    assert seen == ["ops"]


# ---------------------------------------------------------------------------- deploy/ops-monitoring

def test_the_monitoring_files_match_the_thresholds_and_the_metrics_file():
    from pathlib import Path
    d = Path(__file__).resolve().parents[2] / "deploy" / "ops-monitoring"
    conf = (d / "telegraf.conf").read_text("utf-8")
    assert 'container_name_include = ["trailruncoach"]' in conf and '"/trc-ops/metrics.json"' in conf
    assert 'name_override = "trc_members"' in conf and "INFLUX_TOKEN" in conf
    assert ":/trc-ops:ro" in (d / "docker-compose.yml").read_text("utf-8")
    group = json.loads((d / "grafana-alert-rules.json").read_text("utf-8"))
    assert group["interval"] == 3600
    by = {r["uid"]: r for r in group["rules"]}
    thr = {u: r["data"][2]["model"]["conditions"][0]["evaluator"]["params"][0] for u, r in by.items()}
    assert thr == {"trc-cpu-week": 2, "trc-mem-week": 2.5, "trc-members-near-cap": 0.799}
    for u in ("trc-cpu-week", "trc-mem-week"):
        q = by[u]["data"][0]["model"]["query"]
        assert "subDuration(d: 7d" in q and "r.days >= 7" in q and "every: 5m, fn: mean" in q
    assert 'r._field == "share"' in by["trc-members-near-cap"]["data"][0]["model"]["query"]
    # routed by the notification policy (label project=trailruncoach), not a receiver named in the rule: the
    # NAS's Grafana has no such contact point yet (applied 2026-10-20); Grafana 11 wants the panel link in pairs
    assert all("notification_settings" not in r and r["labels"]["project"] == "trailruncoach" for r in group["rules"])
    assert {r["uid"]: r["annotations"]["__panelId__"] for r in group["rules"]} == \
        {"trc-cpu-week": "4", "trc-mem-week": "5", "trc-members-near-cap": "6"}
    assert all(r["annotations"]["__dashboardUid__"] == "trailruncoach-ops" for r in group["rules"])
    dash = json.loads((d / "grafana-dashboard.json").read_text("utf-8"))
    assert dash["uid"] == "trailruncoach-ops" and len(dash["panels"]) == 7
    # the field the alert reads is one metrics.json has
    assert R.member_signal(1, 2).keys() >= {"share"}
    readme = (d / "README.md").read_text("utf-8")
    for ph in ("<TRC_DATA_DIR>", "<INFLUX_BUCKET>", "<INFLUX_DS_UID>", "<FOLDER_UID>", "<CONTACT_POINT>"):
        assert ph in readme


# ---------------------------------------------------------------------------- the admin page

from backend.tests.test_admin_sp351 import F, P, _fresh_site, client  # noqa: E402,F401 — fixtures
from backend.tests.test_login_sp58 import (  # noqa: E402,F401 — fixtures
    HTML, OWNER_EMAIL, accept, app, browser, c, csrf, env, location, sign_in, sign_up, started)
from backend.tests.test_membership_sp330 import server  # noqa: E402,F401 — fixtures


def test_the_admin_page_lists_the_reports_and_the_member_signal(client, tmp_path, monkeypatch):
    where = tmp_path / "ops"
    where.mkdir()
    (where / "2026-10-19.md").write_text("## 營運報表 2026-10-19\n", "utf-8")
    (where / "2026-10-18.md").write_text("## 營運報表 2026-10-18\n", "utf-8")
    monkeypatch.setattr(OD, "folder", lambda: where)
    r = client.get("/api/v1/admin/ops")
    assert r.status_code == 200
    d = r.json()
    assert [x["day"] for x in d["reports"]] == ["2026-10-19", "2026-10-18"]
    assert d["on"] is True and d["run_at"] == "03:30" and d["keep_days"] == 31 and d["warn_share"] == 0.8
    # the owner and the two users of the fixture, against the default cap
    assert d["accounts"] is True and d["members"] == {"count": 3, "cap": 100, "share": 0.03, "near": False, "full": False}
    r = client.get("/api/v1/admin/ops/2026-10-19.md")
    assert r.status_code == 200 and r.text == "## 營運報表 2026-10-19\n"
    assert r.headers["content-type"].startswith("text/plain") and r.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/admin/ops/2026-10-17.md").status_code == 404
    assert client.get("/api/v1/admin/ops/..%2Fx.md").status_code in (404, 422)
    # near the cap: the admin's lower cap
    client.put("/api/v1/admin/config", json={"values": {"members.max_users": 3}})
    assert client.get("/api/v1/admin/ops").json()["members"]["full"] is True


def test_only_an_admin_sees_the_reports(client, tmp_path, monkeypatch):
    where = tmp_path / "ops"
    where.mkdir()
    (where / "2026-10-19.md").write_text("secret-ish\n", "utf-8")
    monkeypatch.setattr(OD, "folder", lambda: where)
    for h in (F, P):
        for path in ("/api/v1/admin/ops", "/api/v1/admin/ops/2026-10-19.md"):
            r = client.get(path, headers=h)
            assert r.status_code == 403 and "secret-ish" not in r.text, path


def test_the_page_has_the_tab_in_both_languages():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "admin.html").read_text("utf-8")
    assert 'data-tab="ops"' in html and "/api/v1/admin/ops/" in html and "ops: showOps" in html
    for lang in ("zh-TW", "en"):
        d = json.loads((root / "i18n" / lang / "admin.json").read_text("utf-8"))
        for k in ("tab.ops", "ops.members_h", "ops.members_count", "ops.members_ok", "ops.members_near",
                  "ops.members_full", "ops.members_off", "ops.reports_h", "ops.lead", "ops.none", "ops.off"):
            assert d.get(k), (lang, k)
