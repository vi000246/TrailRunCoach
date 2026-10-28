"""SP-803: this month's subscription income against the 免辦稅籍 line (SP-321 拍板 2026-10-07: under NT$50,000 a
month; ask an accountant before it gets close) — estimated from the app's own subscription rows
(backend/billing/revenue.py; no Polar call, no key), in the daily ops report, in revenue.json for the Grafana
alert (deploy/ops-monitoring/) and on the admin page's 營運報表 tab. A database that can't be read is unknown,
never NT$0."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from backend import billing as B, ops_daily as OD
from backend.billing import revenue as RV
from backend.scripts import ops_report as R

TZ = RV.TZ
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=TZ)          # mid-November, Taipei


def _row(interval, status, end, cancel=0):
    return {"interval": interval, "status": status, "current_period_end": end, "cancel_at_period_end": cancel}


# ---------------------------------------------------------------------------- the estimate

def test_the_line_and_near_are_sp321s():
    assert RV.TAX_LINE_TWD == 50_000 and RV.WARN_SHARE == 0.8
    assert B.PRICES == {"month": 99, "year": 890}


def test_charged_is_a_period_that_began_this_month_up_to_now():
    rows = [
        _row("month", "active", "2026-12-03T02:00:00+00:00"),     # began 3 Nov: charged
        _row("year", "active", "2027-11-05T02:00:00+00:00"),      # began 5 Nov 2026: charged
        _row("month", "active", "2026-11-28T02:00:00+00:00"),     # began 28 Oct: last month; renews the 28th
        _row("year", "active", "2027-03-01T00:00:00+00:00"),      # began in March: nothing this month
        _row("month", "active", "2026-12-25T00:00:00+00:00"),     # begins 25 Nov: after now, not yet
    ]
    e = RV.estimate(rows, NOW)
    assert e["month"] == "2026-11" and e["charged"] == 99 + 890 and e["paid"] == 2
    assert e["upcoming"] == 99 and e["renewing"] == 1                # the one renewing on the 28th
    assert e["projected"] == 99 + 890 + 99 and e["line"] == 50_000
    assert e["near"] is False and e["over"] is False


def test_the_month_is_taipeis():
    oct20 = dt.datetime(2026, 10, 20, 12, 0, tzinfo=TZ)
    # began 30 Sep 17:00 UTC = 1 Oct 01:00 in Taipei: October's
    assert RV.estimate([_row("month", "active", "2026-10-30T17:00:00+00:00")], oct20)["charged"] == 99
    # began 30 Sep 15:00 UTC = 30 Sep 23:00 in Taipei: September's
    assert RV.estimate([_row("month", "active", "2026-10-30T15:00:00+00:00")], oct20)["charged"] == 0
    s, e = RV.month_of(dt.datetime(2026, 12, 31, 23, 0, tzinfo=TZ))
    assert (s.isoformat(), e.isoformat()) == ("2026-12-01T00:00:00+08:00", "2027-01-01T00:00:00+08:00")


def test_a_canceled_revoked_or_past_due_one_was_still_charged_a_trial_was_not():
    end = "2026-12-03T02:00:00+00:00"                              # began 3 Nov
    for st in ("canceled", "revoked", "past_due"):
        assert RV.estimate([_row("month", st, end, cancel=1)], NOW)["charged"] == 99, st
    assert RV.estimate([_row("month", "trialing", end)], NOW)["charged"] == 0


def test_upcoming_only_renewals_that_will_happen_this_month():
    soon = "2026-11-28T02:00:00+00:00"
    assert RV.estimate([_row("month", "active", soon)], NOW)["upcoming"] == 99
    assert RV.estimate([_row("month", "trialing", soon)], NOW)["upcoming"] == 99         # a trial ending: first charge
    assert RV.estimate([_row("month", "active", soon, cancel=1)], NOW)["upcoming"] == 0  # set to end
    for st in ("past_due", "canceled", "revoked"):
        assert RV.estimate([_row("month", st, soon)], NOW)["upcoming"] == 0, st
    assert RV.estimate([_row("month", "active", "2026-12-01T00:00:00+08:00")], NOW)["upcoming"] == 0  # next month


def test_near_from_80_percent_of_the_month_end_over_on_what_was_charged():
    began = "2026-12-02T00:00:00+00:00"                            # charged 2 Nov
    renews = "2026-11-25T00:00:00+00:00"                           # renews later this month
    near = [_row("month", "active", began)] * 300 + [_row("month", "active", renews)] * 105
    e = RV.estimate(near, NOW)
    assert e["charged"] == 29_700 and e["projected"] == 40_095 and e["share"] == 0.802
    assert e["near"] is True and e["over"] is False                 # projected crosses 80 % before charged does
    below = RV.estimate([_row("month", "active", began)] * 404, NOW)
    assert below["projected"] == 39_996 and below["near"] is False
    over = RV.estimate([_row("year", "active", "2027-11-02T00:00:00+00:00")] * 57, NOW)
    assert over["charged"] == 50_730 and over["over"] is True and over["near"] is True


def test_rows_it_cannot_price_are_left_out():
    rows = [_row(None, "active", "2026-12-03T02:00:00+00:00"), _row("month", "active", None),
            _row("week", "active", "2026-12-03T02:00:00+00:00"), _row("month", "active", "not a date")]
    assert RV.estimate(rows, NOW)["charged"] == 0


def test_month_ends_clamp():
    assert RV._minus(dt.datetime(2026, 3, 31, tzinfo=UTC), "month") == dt.datetime(2026, 2, 28, tzinfo=UTC)
    assert RV._minus(dt.datetime(2026, 1, 15, tzinfo=UTC), "month") == dt.datetime(2025, 12, 15, tzinfo=UTC)
    assert RV._minus(dt.datetime(2029, 2, 28, tzinfo=UTC), "year") == dt.datetime(2028, 2, 28, tzinfo=UTC)
    assert RV._minus(dt.datetime(2028, 2, 29, tzinfo=UTC), "year") == dt.datetime(2027, 2, 28, tzinfo=UTC)


# ---------------------------------------------------------------------------- reading accounts.db

def _db(tmp_path, rows):
    db = tmp_path / "accounts.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, plan TEXT, plan_expires_at TEXT, is_test INTEGER)")
    con.execute("CREATE TABLE billing_subscriptions (user_id INTEGER PRIMARY KEY, interval TEXT, status TEXT, "
                "current_period_end TEXT, cancel_at_period_end INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")
    for uid, test, interval, status, end in rows:
        con.execute("INSERT INTO users VALUES (?, 'subscriber', NULL, ?)", (uid, test))
        con.execute("INSERT INTO billing_subscriptions VALUES (?, ?, ?, ?, 0, 'x')", (uid, interval, status, end))
    con.commit()
    con.close()
    return db


def test_the_rows_are_read_only_and_never_a_test_accounts(tmp_path):
    db = _db(tmp_path, [(2, 0, "month", "active", "2026-12-03T02:00:00+00:00"),
                        (3, 1, "year", "active", "2027-11-05T02:00:00+00:00")])      # a test account's
    before = db.read_bytes()
    s = RV.signal(db, NOW)
    assert s["ok"] is True and s["charged"] == 99 and s["paid"] == 1
    assert db.read_bytes() == before


def test_unreadable_is_unknown_never_zero(tmp_path):
    for db in (None, tmp_path / "none.db"):
        s = RV.signal(db, NOW)
        assert s == {"ok": False, "month": "2026-11", "line": 50_000}
        assert "charged" not in s and "share" not in s
    no_table = tmp_path / "old.db"
    sqlite3.connect(no_table).execute("CREATE TABLE users (id INTEGER PRIMARY KEY, is_test INTEGER)").connection.close()
    assert RV.signal(no_table, NOW)["ok"] is False
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database at all" * 100)
    assert RV.signal(broken, NOW)["ok"] is False


def test_the_real_schema_is_what_it_reads(tmp_path, monkeypatch):
    """The columns the query names are the app's (accounts/store.py's billing_subscriptions)."""
    from backend.accounts import store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(store, "_READY", set())
    store.init()
    with store.connect() as con:
        cols = {r[1] for r in con.execute("PRAGMA table_info(billing_subscriptions)")}
        ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    assert {"user_id", "interval", "status", "current_period_end", "cancel_at_period_end"} <= cols
    assert "is_test" in ucols
    assert RV.signal(tmp_path / "accounts.db", NOW) == {"ok": True, **RV.estimate([], NOW)}


# ---------------------------------------------------------------------------- the daily report

def test_the_report_says_the_month_near_and_unknown():
    day = dt.date(2026, 11, 19)
    assert "訂閱收入" not in R.text(R.report(day, [], [], {}))                       # owner mode: no section
    rows = [_row("month", "active", "2026-12-02T00:00:00+00:00")] * 300 + \
        [_row("month", "active", "2026-11-25T00:00:00+00:00")] * 105
    near = R.text(R.report(day, [], [], {}, None, {"ok": True, **RV.estimate(rows, NOW)}))
    assert "### 本月訂閱收入（估計；免辦稅籍門檻 NT$50,000 的 80% 起提醒）" in near
    assert "2026-11 已收 NT$29,700（300 筆）；照現在的訂閱，月底 NT$40,095（門檻的 80%，之後還有 105 筆續訂）" in near
    assert "**接近門檻**：先問會計師要不要登記營業" in near and "inReachLiveTrack" in near
    ok = R.text(R.report(day, [], [], {}, None, {"ok": True, **RV.estimate(rows[:3], NOW)}))
    assert "NT$297" in ok and "接近門檻" not in ok and "超過" not in ok
    over = R.text(R.report(day, [], [], {}, None, {"ok": True, **RV.estimate(
        [_row("year", "active", "2027-11-02T00:00:00+00:00")] * 57, NOW)}))
    assert "**已超過門檻**" in over
    unknown = R.text(R.report(day, [], [], {}, None, RV.signal(None, NOW)))
    assert "2026-11：**讀不到訂閱紀錄**" in unknown and "不是 NT$0" in unknown and "已收" not in unknown


def test_the_report_of_a_day_counts_its_month_up_to_that_day():
    assert R.day_end(dt.date(2026, 10, 31), NOW) == dt.datetime(2026, 10, 31, 23, 59, 59, tzinfo=TZ)
    assert R.day_end(dt.date(2026, 11, 20), NOW) == NOW                                # today: up to now


def test_the_command_line_reports_the_revenue(tmp_path, monkeypatch, capsys):
    from backend import site_config as SC, site_settings as SS
    from backend.tests.test_ops_report_sp388 import _logs
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    SS.reset_cache()
    db = _db(tmp_path, [(2, 0, "year", "active", "2027-10-05T02:00:00+00:00")])    # charged 5 Oct 2026
    assert R.main(["--date", "2026-10-18", "--logs", str(_logs(tmp_path)), "--events", str(tmp_path / "none.jsonl"),
                   "--accounts-db", str(db)]) == 0
    assert "2026-10 已收 NT$890（1 筆）" in capsys.readouterr().out
    SS.reset_cache()


# ---------------------------------------------------------------------------- revenue.json (Telegraf → Grafana)

@pytest.fixture
def ops(tmp_path, monkeypatch):
    monkeypatch.setattr(OD, "_last_metrics", {"at": 0.0})
    monkeypatch.setattr(OD, "members", lambda: None)
    monkeypatch.setattr(OD, "write", lambda day, where=None: None)                 # the report is SP-388's
    return tmp_path / "ops"


def test_revenue_json_for_the_alert(ops, monkeypatch):
    rows = [_row("month", "active", "2026-12-02T00:00:00+00:00")] * 300 + \
        [_row("month", "active", "2026-11-25T00:00:00+00:00")] * 105
    monkeypatch.setattr(OD, "revenue", lambda at=None: {"ok": True, **RV.estimate(rows, NOW)})
    OD.run_due(dt.datetime(2026, 11, 20, 3, 0, tzinfo=TZ), ops)
    m = json.loads((ops / "revenue.json").read_text("utf-8"))
    assert {k: v for k, v in m.items() if k != "at"} == {
        "ok": 1, "charged": 29_700, "upcoming": 10_395, "projected": 40_095, "line": 50_000, "share": 0.802,
        "near": 1, "over": 0}
    assert isinstance(m["at"], int)


def test_revenue_json_unknown_has_no_amount(ops, monkeypatch):
    monkeypatch.setattr(OD, "revenue", lambda at=None: RV.signal(None, NOW))
    OD.write_revenue(ops, now=1792900000)
    assert json.loads((ops / "revenue.json").read_text("utf-8")) == {"ok": 0, "at": 1792900000}


def test_no_revenue_json_in_single_user_mode(ops, monkeypatch):
    monkeypatch.delenv("WKO5COACH_ACCOUNTS", raising=False)
    assert OD.revenue() is None
    assert OD.write_revenue(ops) is None and not (ops / "revenue.json").exists()


def test_a_failing_revenue_never_stops_the_members_metrics(ops, monkeypatch):
    def boom(at=None):
        raise RuntimeError("x")
    monkeypatch.setattr(OD, "revenue", boom)
    monkeypatch.setattr(OD, "members", lambda: R.member_signal(5, 100))
    OD.run_due(dt.datetime(2026, 11, 20, 3, 0, tzinfo=TZ), ops)
    assert (ops / "metrics.json").exists() and not (ops / "revenue.json").exists()


def test_the_monitoring_files_read_revenue_json():
    d = Path(__file__).resolve().parents[2] / "deploy" / "ops-monitoring"
    conf = (d / "telegraf.conf").read_text("utf-8")
    assert '"/trc-ops/revenue.json"' in conf and 'name_override = "trc_revenue"' in conf
    group = json.loads((d / "grafana-alert-rules.json").read_text("utf-8"))
    rule = next(r for r in group["rules"] if r["uid"] == "trc-revenue-near-tax-line")
    q = rule["data"][0]["model"]["query"]
    assert 'r._measurement == "trc_revenue"' in q and 'r._field == "share"' in q
    assert rule["data"][2]["model"]["conditions"][0]["evaluator"]["params"] == [RV.WARN_SHARE - 0.001]
    assert rule["labels"] == {"project": "trailruncoach", "severity": "warning"}           # SP-388's routing
    assert "notification_settings" not in rule and "會計師" in rule["annotations"]["summary"]
    assert rule["noDataState"] == "OK"
    dash = json.loads((d / "grafana-dashboard.json").read_text("utf-8"))
    panels = {p["id"]: p for p in dash["panels"]}
    assert rule["annotations"]["__panelId__"] == "8" and "trc_revenue" in panels[8]["targets"][0]["query"]
    assert ['r._field == "charged"' in panels[9]["targets"][0]["query"],
            'r._field == "projected"' in panels[9]["targets"][1]["query"]] == [True, True]
    # the fields the alert and the panels read are ones revenue.json has
    assert {"share", "charged", "projected"} <= set(RV.estimate([], NOW))
    assert "revenue.json" in (d / "README.md").read_text("utf-8")


# ---------------------------------------------------------------------------- the admin page

from backend.tests.test_admin_sp351 import F, P, _fresh_site, client  # noqa: E402,F401 — fixtures
from backend.tests.test_login_sp58 import (  # noqa: E402,F401 — fixtures
    HTML, OWNER_EMAIL, accept, app, browser, c, csrf, env, location, sign_in, sign_up, started)
from backend.tests.test_membership_sp330 import server  # noqa: E402,F401 — fixtures


def test_the_admin_page_shows_the_months_income(client, tmp_path, monkeypatch):
    from backend.accounts import store
    monkeypatch.setattr(OD, "folder", lambda: tmp_path / "ops")
    monkeypatch.setattr(RV, "_now", lambda: NOW)
    d = client.get("/api/v1/admin/ops").json()
    assert d["revenue_warn_share"] == 0.8
    assert d["revenue"] == {"ok": True, **RV.estimate([], NOW)}                      # nobody pays yet: NT$0, known
    store.init()
    with store.connect() as con:
        uid = con.execute("SELECT id FROM users WHERE id != 1 ORDER BY id LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO billing_subscriptions(user_id, interval, status, current_period_end, updated_at) "
                    "VALUES (?, 'year', 'active', '2027-11-05T02:00:00+00:00', 'x')", (uid,))
    r = client.get("/api/v1/admin/ops").json()["revenue"]
    assert r["ok"] is True and r["charged"] == 890 and r["paid"] == 1 and r["month"] == "2026-11"
    for h in (F, P):
        assert client.get("/api/v1/admin/ops", headers=h).status_code == 403


def test_the_admin_page_says_unknown_when_the_records_cant_be_read(client, tmp_path, monkeypatch):
    monkeypatch.setattr(OD, "folder", lambda: tmp_path / "ops")
    monkeypatch.setattr(RV, "rows", lambda db: None)
    r = client.get("/api/v1/admin/ops").json()["revenue"]
    assert r["ok"] is False and "charged" not in r


def test_the_tab_has_the_section_in_both_languages():
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "admin.html").read_text("utf-8")
    assert 'id="revenue"' in html and "revenueLine(d)" in html and "ops.revenue_unknown" in html
    for lang in ("zh-TW", "en"):
        d = json.loads((root / "i18n" / lang / "admin.json").read_text("utf-8"))
        for k in ("ops.revenue_h", "ops.revenue_charged", "ops.revenue_projected", "ops.revenue_ok", "ops.revenue_near",
                  "ops.revenue_over", "ops.revenue_unknown", "ops.revenue_note"):
            assert d.get(k), (lang, k)
    zh = json.loads((root / "i18n" / "zh-TW" / "admin.json").read_text("utf-8"))
    assert "會計師" in zh["ops.revenue_near"] and "不是 NT$0" in zh["ops.revenue_unknown"]
