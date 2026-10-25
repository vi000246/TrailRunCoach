"""SP-763: the 疊石 Cairn card (static/xp_card.js, api/xp.py) — only where a multi-day trip is planned.

  - GET /api/v1/app/xp/cairn: shown by default, with Cairn's front page marked from=trc (the button opens
    it in a new tab); hidden after 「不用了」 (kept in the tenant's settings: every device), for 提示多寡
    「精簡」, and for an account linked with Cairn — each with its reason; 設定 brings it back
  - the demo has no route (no account to remember 「不用了」 on): the card and the settings row stay hidden
  - the pages: 賽事管理's editor (a race of 2 days or more, under 「從 Cairn 匯入」), the calculator's
    multi-day result (under the day split), the settings row under 提示多寡; nowhere else
  - the strings in both languages
Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.tests.test_link_sp329 import SECRET, _limits  # noqa: F401 — autouse fixture used by name
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    _clean_env, _client, app, c, csrf, env, sign_up, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
URL = "/api/v1/app/xp/cairn"


@pytest.fixture
def owner(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.db.database import db_path
    from backend.engine.wko5expr import datasource
    from backend.main import build_app
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRC_CAIRN_ORIGIN", "https://cairn.example.test")
    monkeypatch.setattr(datasource, "_db_path", lambda: db_path())     # the settings the routes read (conftest: off)
    with TestClient(build_app(demo=False)) as cl:
        yield cl


def test_the_card_shows_by_default_and_goes_after_no_thanks_on_every_device(owner):
    st = owner.get(URL).json()
    assert st == {"show": True, "why": None, "off": False, "linked": False,
                  "url": "https://cairn.example.test/?from=trc"}
    r = owner.post(URL + "/dismiss")
    assert r.status_code == 200 and r.json()["show"] is False and r.json()["why"] == "off"
    assert owner.get(URL).json()["why"] == "off"                 # kept server side, not in the browser
    # 設定 › 提示多寡: 「重新顯示」
    assert owner.put(URL, json={"off": False}).json()["show"] is True
    assert owner.get(URL).json()["show"] is True
    assert owner.put(URL, json={"off": "yes"}).status_code == 422


def test_lean_tips_hide_the_card(owner):
    assert owner.put("/api/v1/plan/profile/hint-level", json={"level": "lean"}).status_code == 200
    st = owner.get(URL).json()
    assert st["show"] is False and st["why"] == "lean" and st["off"] is False
    owner.put("/api/v1/plan/profile/hint-level", json={"level": None})        # automatic is never 精簡
    assert owner.get(URL).json()["show"] is True


def test_an_account_linked_with_cairn_sees_no_introduction(c, app, monkeypatch):
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    sign_up(c, "g-a", "alice@example.com")
    assert c.get(URL).json()["show"] is True
    c.post("/api/v1/account/links/cairn", json={"linked": True}, headers={"X-TRC-CSRF": csrf(c)})
    st = c.get(URL).json()
    assert st["show"] is False and st["why"] == "linked" and st["linked"] is True
    c.post("/api/v1/account/links/cairn", json={"linked": False}, headers={"X-TRC-CSRF": csrf(c)})
    assert c.get(URL).json()["show"] is True


def test_the_demo_has_no_card(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.tests import demo_fixtures as F
    from backend.main import build_app
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as cl:
        assert cl.get(URL).status_code == 404
        assert cl.post("/api/v1/app/xp/event", json={"event": "xp-card-shown"}).status_code in (403, 404)


# ---------------------------------------------------------------------------- the pages

def test_the_card_is_only_in_the_multi_day_places():
    races = (STATIC / "races.html").read_text("utf-8")
    calc = (STATIC / "racepower.html").read_text("utf-8")
    assert '<script src="/api/v1/static/xp_card.js"></script>' in races
    assert '<script src="/api/v1/static/xp_card.js"></script>' in calc
    # 賽事管理: inside the editor's Cairn section, right under the import; days ≥ 2 only
    sec = races.split('id="ed-cairn"', 1)[1].split('id="cp-wrap"', 1)[0]
    assert sec.index('id="ci-msg"') < sec.index('id="xp-cairn-ed"')
    # (not for someone who just came from Cairn with this route, SP-764)
    assert 'if ((Number(EVF().days.value) || 1) >= 2 && !S.fromCairn) XpCard.show(el, { where: "races" }); ' \
           'else XpCard.hide(el);' in races
    assert races.count("xpCard();") >= 2                          # opening the editor, and after a Cairn import
    # the calculator: under the day split, a multi-day (登山 with a GPX) result only, the short card
    assert calc.index('id="split-cands"') < calc.index('id="xp-cairn-calc"') < calc.index('id="coros-dlg"')
    assert 'if (on && n >= 2) XpCard.show($("xp-cairn-calc"), { where: "calc", compact: true }); ' \
           'else XpCard.hide($("xp-cairn-calc"));' in calc
    # nowhere else (總覽, 課表, 活動分析, 傷病 …): one card a screen at most
    for p in STATIC.glob("*.html"):
        if p.name not in ("races.html", "racepower.html"):
            assert 'src="/api/v1/static/xp_card.js"' not in p.read_text("utf-8"), p.name


def test_the_card_script_counts_and_remembers_on_the_server():
    js = (STATIC / "xp_card.js").read_text("utf-8")
    assert 'fetch(`${API}/cairn`' in js and 'fetch(`${API}/cairn/dismiss`, { method: "POST"' in js
    assert 'event("xp-card-click")' in js and 'event("xp-card-shown")' in js
    assert "localStorage" not in js                               # 「不用了」 is kept with the account
    assert 'target="_blank" rel="noopener"' in js                 # Cairn opens in a new tab
    assert "counted.has(where)" in js                             # shown once a page per place


def test_the_settings_row_turns_it_back_on():
    html = (STATIC / "settings.html").read_text("utf-8")
    hint = html.split('<div id="hint-level">', 1)[1]
    assert hint.index('id="hint-suggest"') < hint.index('id="xp-cairn-row"')
    assert 'const XP_API = "/api/v1/app/xp/cairn";' in html
    assert 'JSON.stringify({ off: !s.off })' in html


@pytest.mark.parametrize("ns,keys", [
    ("common", ("xp.cairn.title", "xp.cairn.body", "xp.cairn.open", "xp.cairn.no", "xp.cairn.note", "xp.cairn.short")),
    ("settings", ("xp.cairn", "xp.cairn_on", "xp.cairn_off", "xp.cairn_show", "xp.cairn_hide", "xp.cairn_lean",
                  "xp.cairn_linked")),
])
def test_the_words_in_both_languages(ns, keys):
    zh = json.loads((STATIC / "i18n" / "zh-TW" / f"{ns}.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / f"{ns}.json").read_text("utf-8"))
    for k in keys:
        assert zh.get(k) and en.get(k), k
        assert not re.search(r"[一-鿿]", en[k]), k
    if ns == "common":
        assert zh["xp.cairn.open"] == "打開疊石 Cairn ↗" and "自動免費註冊" in zh["xp.cairn.note"]
        assert not re.search(r"yichlin|\.com|\.tw", zh["xp.cairn.body"] + zh["xp.cairn.note"])   # no address in the words
