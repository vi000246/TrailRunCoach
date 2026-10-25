"""SP-764: a route brought from 疊石 Cairn (its 「用 TrailRunCoach 算這條」 opens the calculator with
?cairn=<share link>&from=cairn).

  - the calculator sends it on to 賽事管理 (SP-699: a route is a race, added there only), the link and
    from=cairn kept; the demo opens the calculator as before
  - POST /api/v1/app/xp/cairn/route reads the trip (name, start date, days — nothing saved, the token not
    sent back) to fill 新增賽事; a race already holding this route is named instead (no second race)
  - signed out: the sign-in keeps the whole address (Google, the terms, then back to it); a refusal for
    a full site from such a link says so (/login?error=full&from=cairn; test_xp_events_sp765)
  - the pages: 賽事管理 opens the filled editor, imports when saved and goes on to the calculator; the
    calculator says where the race came from, and, with no run or hike recorded, that the estimate is
    from the fitness typed in; the sign-in page has a line for a Cairn sign-in and for 額滿
Synthetic data only; a fake Cairn on 127.0.0.1 (test_cairn_import_sp537)."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.tests.test_cairn_import_sp537 import GID, TOKEN, cairn, plan_client, store  # noqa: F401
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, accept, app, browser, c, csrf, env, location, sign_in, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
CALC = "/api/v1/racepower/page"
RACES = "/api/v1/app/races/page"


@pytest.fixture
def client(plan_client):
    from backend.api import xp as XA
    plan_client.app.include_router(XA.router)
    return plan_client


# ---------------------------------------------------------------------------- the calculator's address

def test_the_calculator_sends_a_cairn_link_on_to_the_race_manager(client, cairn):
    r = client.get(CALC, params={"cairn": cairn.link, "from": "cairn"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["cache-control"] == "no-store"
    u = urlsplit(r.headers["location"])
    assert u.path == RACES and parse_qs(u.query) == {"cairn": [cairn.link], "from": ["cairn"]}
    assert client.get(CALC, follow_redirects=False).status_code == 200          # without one: the page


def test_the_demo_calculator_ignores_the_link(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        assert cl.get(CALC + "?cairn=x&from=cairn", headers=HTML).status_code == 200


# ---------------------------------------------------------------------------- the trip behind the link

def test_the_route_preview_fills_the_new_race_and_finds_one_already_there(client, cairn):
    r = client.post("/api/v1/app/xp/cairn/route", json={"url": cairn.link})
    assert r.status_code == 200, r.text
    j = r.json()
    assert TOKEN not in r.text
    assert {k: j[k] for k in ("name", "start_date", "days", "nights", "event_id")} == {
        "name": "嘉明湖三天", "start_date": "2027-03-01", "days": 3, "nights": 2, "event_id": None}
    assert j["has_records"] in (False, None)                    # nobody's activities in this test
    # imported into a race (賽事管理, SP-537): the same link now names that race
    assert client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link}).status_code == 200
    assert client.post("/api/v1/app/xp/cairn/route", json={"url": cairn.link}).json()["event_id"] == "jm"


def test_a_bad_or_dead_link_is_said_plainly(client, cairn):
    r = client.post("/api/v1/app/xp/cairn/route", json={"url": "https://evil.example/?gpx=a&share=b"})
    assert r.status_code == 400 and "分享連結" in r.json()["detail"]
    cairn.token = "another-token-000000"                       # the share was regenerated in Cairn
    r = client.post("/api/v1/app/xp/cairn/route", json={"url": cairn.link})
    assert r.status_code == 410 and "連結失效" in r.json()["detail"]


# ---------------------------------------------------------------------------- signed out

def test_signed_out_the_sign_in_keeps_the_cairn_address(c, app):
    link = f"https://map.example/?gpx={GID}&share={TOKEN}"
    anyone = browser(app, ip="198.51.100.40")
    r = anyone.get(CALC, params={"cairn": link, "from": "cairn"}, headers=HTML)
    assert r.status_code in (302, 303)
    nxt = parse_qs(urlsplit(location(r)).query)["next"][0]
    assert urlsplit(location(r)).path == "/login"
    assert parse_qs(urlsplit(nxt).query) == {"cairn": [link], "from": ["cairn"]}
    # Google, the terms, then back to that address — which goes on to the race manager
    r = sign_in(anyone, "g-new", "new@example.com", nxt=nxt)
    assert location(r) == "/signup"
    out = accept(anyone).json()
    assert out["ok"] is True and out["next"] == nxt
    r = anyone.get(nxt, headers=HTML)
    assert r.status_code == 303 and urlsplit(location(r)).path == RACES


# ---------------------------------------------------------------------------- the pages

def test_the_race_manager_fills_the_editor_and_goes_on_to_the_calculator():
    html = (STATIC / "races.html").read_text("utf-8")
    init = html[html.index('const cairnIn = q.get("cairn");'):]
    assert 'history.replaceState(null, "", location.pathname);' in init
    assert 'send("/api/v1/app/xp/cairn/route", "POST", { url: cairnIn })' in init
    assert 'if (r && r.event_id) { location.replace("/api/v1/racepower/page?"' in init
    assert 'kind: "baiyue", days: r?.days || 1' in init and "first: r?.has_records === false" in init
    assert '$("evcairn").value = fromAct?.cairn || "";' in html            # imported when saved
    assert "if (imported && S.fromCairn) {" in html
    assert 'new URLSearchParams({ event: id, from: "cairn", ...(S.fromCairn.first ? { first: "1" } : {}) })' in html


def test_the_calculator_and_the_sign_in_page_say_where_it_came_from():
    calc = (STATIC / "racepower.html").read_text("utf-8")
    assert '<div id="from-cairn" class="fromcairn" role="status" hidden></div>' in calc
    assert 'if (q.get("from") !== "cairn" || !eid) return;' in calc
    assert 'T("from_cairn.done", { name: ev.name }) + (q.get("first") === "1" ? " " + T("from_cairn.first") : "")' in calc
    login = (STATIC / "login.html").read_text("utf-8")
    assert 'err === "full" && fromCairn ? T("cairn.full") : T("error." + err)' in login
    assert '} else if (fromCairn) {' in login and 'T("cairn.next")' in login
    for loc in ("zh-TW", "en"):
        cat = {ns: json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))
               for ns in ("races", "racepower", "login")}
        for ns, k in (("races", "cairn.from_new"), ("races", "cairn.from_fail"), ("racepower", "from_cairn.done"),
                      ("racepower", "from_cairn.first"), ("login", "cairn.next"), ("login", "cairn.full")):
            assert cat[ns].get(k), (loc, ns, k)
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    assert zh["from_cairn.first"] == "還沒有手錶紀錄：先用你填的體能估；同步 COROS 或匯入 FIT 檔之後會更準。"
    zl = json.loads((STATIC / "i18n" / "zh-TW" / "login.json").read_text("utf-8"))
    assert "已經排進候補名單" in zl["cairn.full"] and "Cairn 的時刻推估照樣可以用" in zl["cairn.full"]
