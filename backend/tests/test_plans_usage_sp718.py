"""SP-718: the plans page shows this period's use of each quota (used / limit / when it resets; a
subscription: unlimited), its comparison follows the limits the admin set, 設定 has a one-line plan
summary, and a free user down to 20 % hears once on the page that uses the quota."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from backend import membership as MS, site_config as SC, site_settings as SS, tenancy
from backend.tests.test_membership_sp330 import _user, server  # noqa: F401 — fixtures

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "static"


@pytest.fixture(autouse=True)
def _fresh_site(monkeypatch):
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    SS.reset_cache()
    yield
    SS.reset_cache()


def _cat(lang: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / lang / f"{ns}.json").read_text("utf-8"))


# ---------------------------------------------------------------------------- 1. used / limit / resets

def test_usage_says_the_day_each_counter_starts_again(server):  # noqa: F811
    free = _user(server)
    # 2026-10-31 23:30 in Taiwan (15:30 UTC): the day counter resets on 11-01, the month's on 11-01
    late = dt.datetime(2026, 10, 31, 15, 30, tzinfo=UTC)
    with tenancy.use(free):
        MS.consume("calc", item="r1", now=late)
        c, ch = MS.usage("calc", now=late), MS.usage("charts", now=late)
    assert c["used"] == 1 and c["limit"] == 10 and c["period"] == "month" and c["resets_on"] == "2026-11-01"
    assert ch["used"] == 0 and ch["limit"] == 5 and ch["period"] == "day" and ch["resets_on"] == "2026-11-01"
    # mid-month, mid-day; December rolls over the year (Taiwan time, not UTC)
    with tenancy.use(free):
        assert MS.usage("charts", now=dt.datetime(2026, 10, 12, 4, tzinfo=UTC))["resets_on"] == "2026-10-13"
        assert MS.usage("calc", now=dt.datetime(2026, 12, 31, 17, tzinfo=UTC))["resets_on"] == "2027-02-01"
        assert MS.usage("calc", now=dt.datetime(2026, 12, 20, 4, tzinfo=UTC))["resets_on"] == "2027-01-01"


def test_the_billing_view_carries_this_periods_use_and_a_subscription_reads_unlimited(server):  # noqa: F811
    from backend.billing import service as SV
    free, paid = _user(server), _user(server, "subscriber")
    with tenancy.use(free):
        MS.consume("calc", item="r1")
        MS.consume("calc", item="r2")
    v = SV.view(server.get_user(int(free.id[1:])))
    # participants: SP-805's 團員腳程估算 (疊石 Cairn 揪團), a free-tier monthly quota like the calculator
    assert v["role"] == "user" and set(v["quotas"]) == {"calc", "charts", "participants"}
    assert v["quotas"]["calc"]["used"] == 2 and v["quotas"]["calc"]["limit"] == 10
    assert v["quotas"]["calc"]["resets_on"] and v["quotas"]["calc"]["extra"] == 0
    p = SV.view(server.get_user(int(paid.id[1:])))
    # a subscriber: only the back-test counts; the free tier's calculator and charts are lifted (unlimited)
    assert p["role"] == "subscriber" and set(p["quotas"]) == {"backtest"}
    assert set(p["limits"]["free"]) == {"calc", "charts", "participants"}


def test_extra_uses_show_inside_the_limit_on_the_plans_page(server):  # noqa: F811
    from backend.billing import service as SV
    free = _user(server)
    uid = int(free.id[1:])
    server.quota_extra_set(uid, MS.month_key(), 4)
    q = SV.view(server.get_user(uid))["quotas"]["calc"]
    assert q["limit"] == 14 and q["extra"] == 4


def test_the_plans_page_draws_a_bar_per_quota_and_says_unlimited():
    html = (STATIC / "plans.html").read_text("utf-8")
    assert 'id="pl-use"' in html and "renderUse" in html and "pl-bar" in html
    for k in ("resets_on", "use.extra", "use.unlimited", "use.resets_month", "use.resets_day"):
        assert k in html, k
    for lang in ("zh-TW", "en"):
        cat = _cat(lang, "plans")
        for k in ("use.h", "use.calc", "use.charts", "use.backtest", "use.of", "use.unlimited", "use.month",
                  "use.day", "use.resets_day", "use.resets_month", "use.extra", "use.q.calc", "use.q.charts",
                  "use.q.backtest", "use.q_aria"):
            assert cat.get(k), (lang, k)


# ---------------------------------------------------------------------------- 2. the comparison follows the admin

def test_the_plan_limits_follow_the_admin_page(server):  # noqa: F811
    assert MS.plan_limits() == {"free": {"calc": {"limit": 10, "period": "month"},
                                         "charts": {"limit": 5, "period": "day"},
                                         "participants": {"limit": 10, "period": "month"}},     # SP-805
                                "subscriber": {"backtest": {"limit": 2, "period": "month"}}}
    SC.set_values({"quota.free.calc": 20, "quota.free.charts": 8, "quota.subscriber.backtest": 4}, actor=1)
    lim = MS.plan_limits()
    assert lim["free"]["calc"]["limit"] == 20 and lim["free"]["charts"]["limit"] == 8
    assert lim["subscriber"]["backtest"]["limit"] == 4
    free = _user(server)
    with tenancy.use(free):                 # the same number the counter enforces
        assert MS.quota_for("calc").limit == 20 and MS.usage("calc")["limit"] == 20
    from backend.billing import service as SV
    assert SV.view(server.get_user(int(free.id[1:])))["limits"]["free"]["calc"]["limit"] == 20


def test_the_comparison_cells_take_their_numbers_from_the_limits():
    # SP-846: the rows are written into the page from membership.PLAN_ROWS (billing/plan_table.py)
    from backend.billing import plan_table as PT
    html = (STATIC / "plans.html").read_text("utf-8")
    assert "<!--fill:plan_rows-->" in html
    html += PT.rows_html("plans", "zh-TW", limits={})
    cells = dict(re.findall(r'data-limit="([a-z]+\.[a-z]+)" data-tpl="([\w.]+)"', html))
    assert cells == {"free.charts": "row.view.free_n", "free.calc": "row.calc.free_n",
                     "free.participants": "row.participants.free_n",      # SP-892
                     "subscriber.backtest": "row.backtest.paid_n"}
    assert "renderLimits(s.limits)" in html
    for lang in ("zh-TW", "en"):
        cat = _cat(lang, "plans")
        for tpl in cells.values():
            v = cat[tpl]
            text = v["other"] if isinstance(v, dict) else v
            assert "{n}" in text, (lang, tpl)
    zh = _cat("zh-TW", "plans")
    # SP-713's wording stays: one route a day counts once; the free tier keeps 1 year
    assert "同一天同一條路線算 1 次" in zh["row.calc.free_n"]
    assert zh["row.keep.free"] == "1 年（之後只留摘要）" and zh["row.keep.paid"] == "自選 2／3／5／10 年"


# ---------------------------------------------------------------------------- 3. 設定's summary

def test_settings_has_the_plan_summary_for_users_only():
    html = (STATIC / "settings.html").read_text("utf-8")
    i = html.index('id="plan-sum"')
    block = html[i:html.index("</script>", i)]
    assert 'href = "/plans"' in block
    assert 'ms.role === "user" || ms.role === "subscriber"' in block and "s.login" in block   # not the owner
    for lang in ("zh-TW", "en"):
        cat = _cat(lang, "settings")
        for k in ("plan_sum.free", "plan_sum.sub", "plan_sum.calc", "plan_sum.charts", "plan_sum.backtest",
                  "plan_sum.unlimited", "plan_sum.link", "plan_sum.title"):
            assert cat.get(k), (lang, k)


# ---------------------------------------------------------------------------- 4. the 20 % hint

def test_the_shell_hints_once_on_the_page_that_uses_the_quota():
    js = (STATIC / "shell.js").read_text("utf-8")
    i = js.index("// ---- low quota (SP-718)")
    block = js[i:js.index("// ---- cool-downs", i)]
    assert 'racepower: ["calc"]' in block and 'charts: ["charts"]' in block
    assert "u.limit * 0.2" in block                                  # 20 % or less left
    assert 'MS.role === "user"' in block and "SESSION.login" in block
    assert "localStorage" in block and "try {" in block              # once a period, private mode safe
    assert '"/plans"' in block and "quota.low.close" in block        # the way to upgrade, and a close
    assert "disabled" not in block                                   # never blocks
    for lang in ("zh-TW", "en"):
        cat = _cat(lang, "shell")
        for k in ("quota.low.calc", "quota.low.charts", "quota.low.upgrade", "quota.low.close"):
            assert cat.get(k), (lang, k)
