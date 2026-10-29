"""SP-1018: GET /cairn/route/<gpxId> — 疊石 Cairn's 「在 Trig 看賽事天氣 ↗」 (Cairn SP-554, shown on a route its
owner synced with TRC; the address is <TRC_BASE_URL>/cairn/route/<Cairn's route id>).

  - the signed-in person's race for that route — pushed to that Cairn trip (cairn_push.trip_id) or its GPX
    imported from it (event_gpx source gpx_id, SP-537 / SP-538) — opens on its weather: 303 to the
    calculator ?event=<id>#env (the 環境 card); several: the next to come
  - none: a page 「這條路線沒有對應的賽事」 in both languages (200, never a bare 404)
  - signed out: the sign-in, then back to the same address
  - only the person's own tenant: the id picks nobody else's race

SP-58's fake Google; no Cairn is called at all (the route reads this side only)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.tests.test_cairn_curve_sp653 import GPX
from backend.tests.test_cairn_push_sp655 import _as, _event, stores  # noqa: F401 — fixtures used by name
from backend.tests.test_login_sp58 import HTML  # noqa: F401
from backend.tests.test_link_sp329 import (  # noqa: F401
    _limits, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
CALC = "/api/v1/racepower/page"
TRIP = "g7Kq2"


def route(gpx_id: str = TRIP) -> str:
    return f"/cairn/route/{gpx_id}"


def _pushed(sub: str, eid: str, trip: str = TRIP):
    from backend.accounts import cairn_push as CP
    with _as(sub):
        CP.remember(eid, trip, "0" * 64)


def _imported(sub: str, eid: str, gpx_id: str = TRIP, date: str = "2027-03-01"):
    from backend.engine import event_gpx as EG
    from backend.engine.planning import Event, Plan
    with _as(sub):
        plan = Plan.load()
        plan.events = [e for e in plan.events if e.id != eid] + [Event(id=eid, name=f"賽事 {eid}", date=date,
                                                                         kind="baiyue", days=2)]
        plan.save()
        EG.save(eid, GPX, "r.gpx", source={"kind": "cairn", "gpx_id": gpx_id, "name": "合歡北西峰"})


def _route_data(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="route">(.*?)</script>', html, re.S)
    assert m, "the route's data"
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------- a race for the route

def test_a_race_pushed_to_that_trip_opens_on_its_weather(c, app, stores):
    sign_up(c, "g-a", "alice@example.com")
    _event("g-a", "race-1")
    _pushed("g-a", "race-1")
    r = c.get(route(), headers=HTML)
    assert r.status_code == 303, r.text
    assert location(r) == f"{CALC}?event=race-1#env"
    assert r.headers["cache-control"] == "no-store"


def test_a_race_imported_from_that_route_opens_on_its_weather(c, app, stores):
    sign_up(c, "g-a", "alice@example.com")
    _imported("g-a", "race-2")
    r = c.get(route(), headers=HTML)
    assert r.status_code == 303 and location(r) == f"{CALC}?event=race-2#env"


def test_several_races_the_next_to_come(c, app, stores):
    sign_up(c, "g-a", "alice@example.com")
    _imported("g-a", "past", date="2020-05-01")
    _imported("g-a", "later", date="2099-05-01")
    _imported("g-a", "next", date="2098-05-01")
    assert location(c.get(route(), headers=HTML)) == f"{CALC}?event=next#env"
    # all of them past: the latest
    from backend.engine.planning import Plan
    with _as("g-a"):
        plan = Plan.load()
        plan.events = [e for e in plan.events if e.id == "past"]
        plan.save()
    _imported("g-a", "old", date="2019-05-01")
    assert location(c.get(route(), headers=HTML)) == f"{CALC}?event=past#env"


def test_a_race_deleted_from_the_plan_is_no_race(c, app, stores):
    from backend.engine.planning import Plan
    sign_up(c, "g-a", "alice@example.com")
    _event("g-a", "race-1")
    _pushed("g-a", "race-1")
    with _as("g-a"):
        plan = Plan.load()
        plan.events = []
        plan.save()
    r = c.get(route(), headers=HTML)
    assert r.status_code == 200 and "這條路線沒有對應的賽事" in r.text


# ---------------------------------------------------------------------------- no race: a page, both languages

def test_no_race_is_a_page_in_both_languages_not_a_404(c, app, stores):
    sign_up(c, "g-a", "alice@example.com")
    _imported("g-a", "race-2", gpx_id="otherRoute")
    r = c.get(route(), headers=HTML)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert ">這條路線沒有對應的賽事</title>" in r.text and "到賽事管理" in r.text
    assert 'href="/api/v1/app/races/page"' in r.text
    data = _route_data(r.text)
    assert data["id"] == TRIP and data["cairn"].endswith(f"?gpx={TRIP}") and data["signin"] is True
    en = c.get(route() + "?lang=en", headers=HTML)
    assert en.status_code == 200 and ">No race for this route</title>" in en.text
    assert "Go to the race manager" in en.text and "這條路線沒有對應的賽事" not in en.text


def test_a_route_id_that_is_no_cairn_id_is_the_same_page(c, app, stores):
    sign_up(c, "g-a", "alice@example.com")
    r = c.get(route("not.an%20id"), headers=HTML)
    assert r.status_code == 200 and "這條路線沒有對應的賽事" in r.text
    data = _route_data(r.text)
    assert data["id"] == "" and data["cairn"] == ""                        # nothing of it echoed back


# ---------------------------------------------------------------------------- signed out, other tenants

def test_signed_out_the_sign_in_comes_back_to_the_route(c, app, stores):
    sign_up(c, "g-a", "alice@example.com")
    _event("g-a", "race-1")
    _pushed("g-a", "race-1")
    anyone = browser(app, ip="198.51.100.41")
    r = anyone.get(route(), headers=HTML)
    assert r.status_code in (302, 303) and urlsplit(location(r)).path == "/login"
    nxt = parse_qs(urlsplit(location(r)).query)["next"][0]
    assert nxt == route()
    assert anyone.get(route()).status_code == 401                           # a script, not a page
    r = sign_in(anyone, "g-a", "alice@example.com", nxt=nxt)
    assert r.status_code == 303 and location(r) == nxt
    r = anyone.get(nxt, headers=HTML)
    assert r.status_code == 303 and location(r) == f"{CALC}?event=race-1#env"


def test_only_the_persons_own_races(c, app, stores):
    sign_up(c, "g-b", "bob@example.com")
    _event("g-b", "bobs")
    _pushed("g-b", "bobs")
    _imported("g-b", "bobs2")
    alice = browser(app, ip="198.51.100.42")
    sign_up(alice, "g-a", "alice@example.com")
    r = alice.get(route(), headers=HTML)
    assert r.status_code == 200 and "這條路線沒有對應的賽事" in r.text           # g-b's races are not hers
    assert "bobs" not in r.text
    assert location(c.get(route(), headers=HTML)) == f"{CALC}?event=bobs#env"


# ---------------------------------------------------------------------------- the pages

def test_the_calculator_opens_on_the_environment_card():
    calc = (STATIC / "racepower.html").read_text("utf-8")
    assert '<section id="env">' in calc
    tail = calc[calc.index('if (location.hash === "#env")'):]
    assert '$("env").scrollIntoView({ block: "start" });' in tail
    assert 'document.querySelector(\'#tabs button[data-tab="calc"]\').click()' in tail
    assert "#env { scroll-margin-top:" in calc


def test_the_no_race_page_in_both_languages():
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "cairn_route.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "cairn_route.json").read_text("utf-8"))
    assert set(zh) == set(en)
    for k in zh:
        assert zh[k] and en[k] and zh[k] != en[k], k
    html = (STATIC / "cairn_route.html").read_text("utf-8")
    keys = set(re.findall(r'data-i18n="cairn_route\.([\w.]+)"', html)) | {"route_id"}
    assert keys == set(zh)
    assert "{id}" in zh["route_id"] and "{id}" in en["route_id"]
