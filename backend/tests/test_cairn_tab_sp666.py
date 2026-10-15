"""SP-666: 設定頁獨立的 Cairn 分頁 — the link, each feature's switch (what it lets Cairn read or
write), the trips pushed (SP-655's cairn_push), the plans and the daily pushes, the row a Cairn
link_url asks about highlighted (SP-654).

The API side: GET /api/v1/account/links tells each scope's direction (read / write) and a plain
sentence of what it lets Cairn do; Cairn's row also carries the plan here and Cairn's pushes per day
for its plan there. GET /api/v1/account/links/cairn/pushes lists the races pushed, newest first,
each with its 「在 Cairn 打開」 link. The page side (static/settings.html): the tab, its section, the
deep links, the catalogs. A fake Cairn (httpx MockTransport): nothing reaches the network."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.tests.test_cairn_push_sp655 import (  # noqa: F401 — fixtures used by name
    SCOPE, _as, _event, _push, _ready, cairn, stores,
)
from backend.tests.test_link_curves_sp654 import _subscribe
from backend.tests.test_link_sp329 import (  # noqa: F401 — fixtures used by name
    LINKS_URL, _limits, _link, app, browser, c, csrf, env, sign_up, started,
)

PUSHES_URL = f"{LINKS_URL}/cairn/pushes"
STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = (STATIC / "settings.html").read_text("utf-8")


def _cairn_row(cl) -> dict:
    r = cl.get(LINKS_URL)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["clients"] if x["id"] == "cairn")


# ---------------------------------------------------------------------------- what each switch does

def test_every_scope_says_whether_cairn_reads_or_writes_and_what(c, app, cairn):
    from backend.accounts import links as LK
    assert set(LK.SCOPE_KIND) == set(LK.CAIRN.scopes) == set(LK.SCOPE_DETAIL)
    assert LK.SCOPE_KIND["link:calc"] == "read" and LK.SCOPE_KIND["link:gpx_sync"] == "write"
    assert LK.SCOPE_KIND["link:account"] == "read" and LK.SCOPE_KIND["link:weather"] == "read"
    sign_up(c, "g-a", "alice@example.com")
    row = _cairn_row(c)
    feats = {f["scope"]: f for f in row["features"]}
    assert set(feats) == {"link:calc", "link:gpx_sync"}
    assert feats["link:calc"]["kind"] == "read" and feats["link:gpx_sync"]["kind"] == "write"
    for f in list(feats.values()) + row["scopes"]:
        assert f["detail"] and f["text"] and f["kind"] in ("read", "write"), f
    # link:weather is still given by the link (no route yet): listed with what linking allows
    assert {s["scope"]: s["kind"] for s in row["scopes"]} == {"link:account": "read", "link:weather": "read"}


def test_the_details_have_english(c, app, cairn):
    from backend.accounts import links as LK
    en = json.loads((Path(__file__).resolve().parents[1] / "i18n" / "locales" / "en.json").read_text("utf-8"))
    for s, msg in LK.SCOPE_DETAIL.items():
        assert en.get(msg) and not re.search(r"[一-鿿]", en[msg]), s


# ---------------------------------------------------------------------------- the plans and the daily pushes

def test_cairns_row_carries_the_plan_here_and_cairns_daily_pushes(c, app, cairn):
    sign_up(c, "g-a", "alice@example.com")
    row = _cairn_row(c)
    assert row["plan_here"] == {"role": "user", "plan_ok": False, "upgrade": True}
    assert "remote" not in row or row["remote"] is None              # not linked: Cairn not asked
    _link(c)
    _subscribe("g-a")
    row = _cairn_row(c)
    assert row["plan_here"]["plan_ok"] is True and row["plan_here"]["upgrade"] is False
    assert row["remote"]["plan"] == "basic" and row["remote"]["push_per_day"] == 20
    cairn.people["g-a"] = "free"
    assert _cairn_row(c)["remote"]["push_per_day"] == 0
    cairn.people["g-a"] = "owner"
    r = _cairn_row(c)["remote"]
    assert "push_per_day" in r and r["push_per_day"] is None           # no limit
    cairn.people["g-a"] = "someday-plan"
    assert "push_per_day" not in _cairn_row(c)["remote"]               # not known: no number shown


# ---------------------------------------------------------------------------- the trips pushed

def test_the_races_pushed_newest_first_with_their_cairn_link(c, app, cairn, stores):
    from backend.engine.planning import Plan
    _ready(c)
    assert c.get(PUSHES_URL).json() == {"pushes": []}
    _event("g-a", "race-1", name="合歡北西峰 兩天")
    assert _push(c, "race-1").status_code == 200
    _event("g-a", "race-2", name="嘉明湖 三天")
    assert _push(c, "race-2").status_code == 200
    r = c.get(PUSHES_URL)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    got = r.json()["pushes"]
    assert [(x["event_id"], x["name"], x["trip_id"]) for x in got] == [
        ("race-2", "嘉明湖 三天", "trip-2"), ("race-1", "合歡北西峰 兩天", "trip-1")]
    for x in got:
        assert x["at"] and x["date"] == "2027-03-01" and x["url"].endswith(f"?gpx={x['trip_id']}")
        assert x["gone"] is False
        assert "token" not in json.dumps(x).lower()
    # a race deleted from the season plan: its trip is still in Cairn, still listed
    with _as("g-a"):
        plan = Plan.load()
        plan.events = [e for e in plan.events if e.id != "race-1"]
        plan.save()
    got = c.get(PUSHES_URL).json()["pushes"]
    gone = next(x for x in got if x["event_id"] == "race-1")
    assert gone["gone"] is True and gone["name"] == "" and gone["url"].endswith("?gpx=trip-1")


def test_only_the_persons_own_pushes(c, app, cairn, stores):
    _ready(c)
    _event("g-a")
    assert _push(c).status_code == 200
    with browser(app) as b:
        sign_up(b, "g-b", "bob@example.com")
        assert b.get(PUSHES_URL).json() == {"pushes": []}


def test_the_pushes_need_sign_in(c, app, cairn):
    with browser(app, "203.0.113.77") as b:
        r = b.get(PUSHES_URL)
        assert r.status_code == 401


# ---------------------------------------------------------------------------- the page

def _tabs() -> dict[str, list[str]]:
    js = PAGE[PAGE.index("const TABS = {"):]
    js = js[:js.index("};")]
    return {k: re.findall(r'"(\w+)"', ids) for k, ids in re.findall(r"(\w+): \[([^\]]*)\]", js)}


def test_the_page_has_a_cairn_tab_of_its_own():
    assert _tabs()["cairn"] == ["cairn"]
    nav = PAGE[PAGE.index('<nav class="tabs"'):PAGE.index("</nav>")]
    # the tab is there only when the account and Cairn are: hidden until loadLinks shows it
    assert re.search(r'<button type="button" role="tab" id="tab-cairn" data-tab="cairn" '
                     r'data-i18n="settings\.tabs\.cairn" hidden>', nav)
    main = PAGE[PAGE.index("<main>"):PAGE.index("</main>")]
    assert re.search(r'<section id="cairn" hidden>', main)
    sec = main[main.index('<section id="cairn"'):]
    sec = sec[:sec.index("</section>")]
    for el in ("cairn-ask", "cairn-status", "cairn-features", "cairn-plan", "cairn-pushes", "cairn-msg"):
        assert f'id="{el}"' in sec, el
    # the old anchor (#links, every link_url Cairn already has) lands on the tab
    assert 'id="links"' in sec
    # other linked services stay in 帳號; Cairn's row is not drawn there
    acct = main[main.index('<section id="account"'):]
    acct = acct[:acct.index("</section>")]
    assert 'id="acct-links-list"' in acct and 'id="links"' not in acct
    assert 'c.id !== "cairn"' in PAGE or "c.id !== 'cairn'" in PAGE


def test_a_link_url_opens_the_tab():
    # ?link=cairn (with or without &allow=) picks the Cairn tab whatever the hash
    assert 'get("link") === "cairn"' in PAGE
    # a hidden tab (owner mode, the demo) is never shown: personal instead
    assert "tabHidden(" in PAGE


def test_the_catalogs_have_the_tab_in_both_languages():
    keys = set(re.findall(r'data-i18n="settings\.([\w.]+)"', PAGE[PAGE.index('<section id="cairn"'):]
                          [:PAGE[PAGE.index('<section id="cairn"'):].index("</section>")]))
    keys |= {k for k in re.findall(r't\("settings\.(cairn\.[\w.]+)"', PAGE)}
    keys |= {"tabs.cairn", "tabs.cairn_note"}
    assert len(keys) > 10
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "settings.json").read_text("utf-8"))
        missing = sorted(k for k in keys if not cat.get(k))
        assert not missing, (loc, missing)
    en = json.loads((STATIC / "i18n" / "en" / "settings.json").read_text("utf-8"))
    assert not [k for k in keys if re.search(r"[一-鿿]", en[k])]
