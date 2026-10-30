"""SP-846: 方案與價格 (/pricing) — public, signed in or not, linked from the 介紹頁. Mock A of the ticket:
two plan cards (the subscription's yearly / monthly price), the safety strip, the whole comparison folded
under 「看全部差別」, a few questions. Its comparison is the plans page's: both draw membership.PLAN_ROWS
through billing/plan_table.rows_html, numbered by the admin's limits.

The ticket's answers (Q1): the prices show while the subscription isn't on sale, its button then reads
「即將開放」 and does nothing (2a, NT$99 / 890); no trial (3a); the table sells what the plan does after an
import or a sync, not the sync itself (4b)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend import billing as B
from backend import membership as MS
from backend.billing import plan_table as PT
from backend.tests.test_billing_sp328 import billing_env, polar, runner  # noqa: F401 — fixtures used by name
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, app, browser, c, env, location, sign_in, sign_up, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCALES = ("zh-TW", "en")


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _tbody(page: str) -> str:
    m = re.search(r'<table class="p[lt][^"]*">.*?<tbody>(.*?)</tbody>', page, re.S)
    assert m, "no comparison table"
    return m.group(1)


def _paid_cta(page: str) -> str:
    i = page.index('id="paid-h"')
    return page[i:page.index('class="from"', i)]


# ---------------------------------------------------------------------------- public, linked from the 介紹頁

def test_pricing_is_public_and_plans_is_not():
    from backend.accounts import login as LG
    assert LG.is_public("/pricing") and not LG.is_public("/plans") and not LG.is_public("/pricing/x")


def test_signed_out_the_page_opens_in_both_languages(c):
    for loc in LOCALES:
        r = c.get(f"/pricing?lang={loc}", headers=HTML)
        assert r.status_code == 200 and f'lang="{loc}"' in r.text, loc
        assert "private" in r.headers.get("cache-control", "")
        cat = _cat(loc, "pricing")
        for k in ("h1", "free.h", "paid.h", "paid.badge", "safe.h", "faq.trial.q", "end.text"):
            assert cat[k] in r.text, (loc, k)
        assert "<!--fill:" not in r.text, loc            # every marker filled
        # the prices are billing.PRICES; the yearly saving and average follow from them
        p = PT.prices()
        assert (p["month"], p["year"], p["save"], p["pct"], p["avg"]) == (99, 890, 298, 25, 74)
        assert "NT$99" in r.text and "NT$890" in r.text and "NT$298" in r.text and "NT$74" in r.text
        # signed out: 「開始」 goes to sign-in (Google or e-mail)
        assert 'href="/login"' in r.text


def test_the_intro_page_links_to_it_in_the_bar_and_by_the_price(c):
    for loc in LOCALES:
        page = c.get(f"/?lang={loc}", headers=HTML).text
        top = page[page.index('<header class="top">'):page.index("</header>")]
        assert 'href="/pricing"' in top and 'class="link wide" href="/pricing"' not in top    # on a phone too
        assert page.count(f'<a href="/pricing" data-man data-i18n="intro.cta.pricing">{_cat(loc, "intro")["cta.pricing"]}</a>') == 2
        price = _cat(loc, "intro")["cta.price"]
        assert f"NT${B.PRICES[B.MONTH]}" in price and f"NT${B.PRICES[B.YEAR]}" in price, loc
        # no payments yet: the intro says subscriptions are coming, not that they can be bought (owner 2026-10-23)
        assert ("即將開放" if loc == "zh-TW" else "coming soon") in price, loc
    assert c.get("/pricing", headers=HTML).status_code == 200


# ---------------------------------------------------------------------------- the buttons: who looks, on sale or not

def test_not_on_sale_the_prices_show_and_subscribe_is_coming_soon(c):
    assert not B.open_for_sale()
    page = c.get("/pricing?lang=zh-TW", headers=HTML).text
    cta = _paid_cta(page)
    assert re.search(r'<button type="button" class="btn primary" id="pr-soon" disabled[^>]*>即將開放</button>', cta)
    assert "訂閱還沒開放" in cta and "<a " not in cta and "NT$890" in cta and "NT$99" in cta


def test_on_sale_signed_out_subscribe_signs_in_first(app, billing_env):
    assert B.open_for_sale()
    page = _client(app, "203.0.113.77").get("/pricing", headers=HTML).text
    cta = _paid_cta(page)
    assert 'href="/login?next=%2Fplans"' in cta and "開始訂閱" in cta and "disabled" not in cta


def test_on_sale_a_free_user_goes_to_the_plans_page_and_a_subscriber_manages(app, billing_env):
    cl, uid = runner(app)
    page = cl.get("/pricing", headers=HTML).text
    cta = _paid_cta(page)
    assert '<a class="btn primary" href="/plans">開始訂閱</a>' in cta
    top = page[page.index('<header class="top">'):page.index("</header>")]
    assert '<a class="btn small primary" href="/">打開 Trig 基石</a>' in top and 'href="/login"' not in top
    from backend.accounts import store
    store.set_plan(uid, "subscriber")
    cta = _paid_cta(cl.get("/pricing", headers=HTML).text)
    assert '<a class="btn" href="/plans">管理訂閱</a>' in cta


def test_signed_in_while_not_on_sale_is_still_coming_soon(app):
    cl, _uid = runner(app, "early")
    cta = _paid_cta(cl.get("/pricing", headers=HTML).text)
    assert "pr-soon" in cta and "disabled" in cta


def test_the_owners_single_user_app_has_no_pricing_page(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    _clean_env(monkeypatch, tmp_path)
    with TestClient(build_app(demo=False)) as cl:
        assert cl.get("/pricing").status_code == 404


# ---------------------------------------------------------------------------- one table for /plans and /pricing

def test_plans_and_pricing_show_the_same_table(app):
    cl, _uid = runner(app, "both")
    for loc in LOCALES:
        plans = _tbody(cl.get(f"/plans?lang={loc}", headers=HTML).text)
        pricing = _tbody(cl.get(f"/pricing?lang={loc}", headers=HTML).text)
        assert plans == pricing, loc
        rows = re.findall(r'<tr><td data-i18n="plans\.row\.(\w+)">', plans)
        assert rows == [r.key for r in MS.PLAN_ROWS], loc
        groups = re.findall(r'data-i18n="plans\.grp\.(\w+)"', plans)
        assert groups == list(MS.PLAN_GROUPS), loc


def test_every_row_has_its_words_in_both_languages():
    for loc in LOCALES:
        cat = _cat(loc, "plans")
        for g in MS.PLAN_GROUPS:
            assert cat.get(f"grp.{g}"), (loc, g)
        for r in MS.PLAN_ROWS:
            assert cat.get(f"row.{r.key}"), (loc, r.key)
            for side, kind, quota in (("free", r.free, r.free_limit), ("paid", r.paid, r.paid_limit)):
                if kind == "text":
                    assert cat.get(f"row.{r.key}.{side}"), (loc, r.key, side)
                if quota:
                    v = cat[f"row.{r.key}.{side}_n"]
                    assert "{n}" in (v["other"] if isinstance(v, dict) else v), (loc, r.key, side)
                    plan = MS.FREE if side == "free" else MS.PAID
                    assert quota in MS.QUOTAS[plan], (r.key, quota)
        assert cat.get("cell.yes") and cat.get("cell.no")


def test_the_table_sells_the_plan_after_a_sync_not_the_sync():
    """SP-846 Q1-4 (b): no 「自動同步」 row; the row is what the plan does after an import or a sync."""
    assert "sync" not in {r.key for r in MS.PLAN_ROWS}
    zh, en = _cat("zh-TW", "plans"), _cat("en", "plans")
    assert not any(k.startswith("row.sync") for k in zh) and not any(k.startswith("row.sync") for k in en)
    assert zh["row.auto"] == "匯入或同步後調整課表" and zh["row.auto.paid"] == "即時"
    assert "即時同步" not in zh["lead"] and "real-time sync" not in en["lead"]


def test_the_rows_follow_what_the_scopes_and_cairn_really_gate():
    """A ✓ only on the side that has it: the paid-only rows are the subscription's scopes / Cairn features."""
    from backend.accounts import links
    paid_only = {r.key for r in MS.PLAN_ROWS if r.free == "no" and r.paid != "no"}
    assert paid_only == {"backtest", "charts", "cairn"}
    assert "calc:backtest" not in MS.ROLE_SCOPES[MS.USER] and "charts:365d" not in MS.ROLE_SCOPES[MS.USER]
    assert {f for f, v in links.FEATURES.items() if v.get("plan") == "subscriber"} == {"link:calc", "link:gpx_sync"}
    assert "safety" in MS.ROLE_SCOPES[MS.USER]
    assert MS.PLAN_TABLE[-1][1] == "✓ 永遠免費"


# ---------------------------------------------------------------------------- the numbers follow the admin

def test_the_cards_and_the_table_take_the_admins_numbers(app):
    from backend import site_config as SC
    SC.set_values({"quota.free.calc": 20, "quota.free.charts": 1, "quota.subscriber.backtest": 4}, actor=1)
    c = _client(app, "203.0.113.88")
    zh = c.get("/pricing?lang=zh-TW", headers=HTML).text
    assert "賽事計算機每月 20 次" in zh and "每天 1 個圖表" in zh and "每月 4 次" in zh
    assert "每月 20 次（同一天同一條路線算 1 次）" in _tbody(zh)
    en = c.get("/pricing?lang=en", headers=HTML).text
    assert "Race calculator, 20 times a month" in en and "(1 chart a day)" in en
    assert "1 chart page a day" in _tbody(en)          # the plural's "one"


def test_the_comparison_is_folded_on_a_phone_and_counts_its_rows(c):
    page = c.get("/pricing?lang=zh-TW", headers=HTML).text
    assert '<details class="cmp" id="pr-all">' in page          # closed as served; opened from 900 px
    assert "(min-width: 900px)" in page
    assert f"看全部差別（{len(MS.PLAN_ROWS)} 項）" in page
