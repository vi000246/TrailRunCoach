"""SP-622: the 介紹頁 — what a stranger sees at "/" before signing in.

- sign-in mode, signed out: "/" is the introduction (a public path, exactly "/"); signed in: the overview
  as before; the owner's app without sign-in and the demo instance keep their "/";
- /login links to it (「這是什麼？」); every other private path stays behind sign-in;
- the page in both languages, its sections in the decided order (hero with the three tags → the race
  calculator → schedule and season → every run → sources → privacy → FAQ → footer), the calculator's
  origin written as it is (ported from Palladino's SuperPower Calculator), one line on the price, no
  e-mail field;
- 「先試試看」: the public demo when TRC_DEMO_URL is set, else the built-in example share (a fictional
  runner's 50K, a repo file: no tenant, no accounts.db row);
- the screenshots it uses are there in both languages, light and dark, and come from the shot list.
The words it must not use are test_own_terms_sp391.test_public_pages_promise_nothing_and_name_no_trademark."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "static"
SHOTS = ROOT / "backend" / "scripts" / "intro_shots.json"
LOCALES = ("zh-TW", "en")


def _try(page: str):
    m = re.search(r"window\.TRC_TRY = (.*?);</script>", page)
    assert m, "the page carries no 「先試試看」 link"
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------- "/" and the public list

def test_signed_out_home_is_the_introduction(c):
    r = c.get("/", headers=HTML)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert 'id="hero-h"' in r.text and 'href="/login"' in r.text
    assert "private" in r.headers.get("cache-control", "")
    # exactly "/": the rest stays behind sign-in
    assert location(c.get("/api/v1/overview/page", headers=HTML)).startswith("/login?next=")
    assert c.get("/api/v1/plan").status_code == 401
    assert c.get("/intro", headers=HTML).status_code in (302, 401)


def test_signed_in_home_is_the_overview(c):
    sign_up(c, "g-alice", "alice@example.com")
    r = c.get("/", headers=HTML)
    assert r.status_code in (302, 303, 307) and location(r) == "/api/v1/overview/page"
    assert c.get("/api/v1/overview/page", headers=HTML).status_code == 200


def test_the_owners_app_without_sign_in_keeps_its_home(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend.tests.test_login_sp58 import _clean_env
    from backend.main import build_app
    _clean_env(monkeypatch, tmp_path)
    with TestClient(build_app(demo=False), follow_redirects=False) as cl:
        assert location(cl.get("/", headers=HTML)) == "/api/v1/overview/page"


def test_home_is_public_exactly():
    from backend.accounts import login as LG
    assert LG.is_public("/") and not LG.is_public("/x") and not LG.is_public("/intro")


def test_login_links_to_the_introduction(c):
    for loc, text in (("zh-TW", "這是什麼？"), ("en", "What is this?")):
        r = c.get(f"/login?lang={loc}")
        assert r.status_code == 200
        m = re.search(r'<a href="/" id="lg-what"[^>]*>([^<]*)</a>', r.text)
        assert m and m.group(1).startswith(text), loc


# ---------------------------------------------------------------------------- the page

def test_the_page_in_both_languages_in_the_decided_order(c):
    for loc in LOCALES:
        r = c.get(f"/?lang={loc}", headers=HTML)
        assert r.status_code == 200 and f'lang="{loc}"' in r.text
        cat = json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))
        for k in ("hero.h1", "tags.trail", "tags.baiyue", "tags.road", "race.h", "plan.h", "read.h", "faq.h",
                  "cta.price", "race.origin"):
            assert cat[k].replace("\"", "&quot;") in r.text or cat[k] in r.text, (loc, k)
        order = [r.text.index(x) for x in ('id="hero-h"', 'id="race"', 'id="plan"', 'id="read"', 'id="start"',
                                           'id="sources"', 'id="privacy"', 'id="faq"', "<footer>")]
        assert order == sorted(order), loc
    zh = c.get("/?lang=zh-TW", headers=HTML).text
    assert "<input" not in zh and "type=\"email\"" not in zh        # no e-mail field: the waitlist is the sign-in's


def test_the_calculator_is_credited_to_palladino():
    for loc, words in (("zh-TW", ("移植自 Steve Palladino 的 SuperPower Calculator", "不是我們發明的")),
                       ("en", ("ported from Steve Palladino's SuperPower Calculator", "not our invention"))):
        origin = json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))["race.origin"]
        for w in words:
            assert w in origin, (loc, w)


def test_faq_has_4_to_6_questions_each_to_an_existing_manual_section(c):
    from backend.api import manual as M
    html = (STATIC / "intro.html").read_text("utf-8")
    faq = html[html.index('id="faq"'):html.index('class="end"')]
    links = re.findall(r'href="/manual/([a-z-]+)#([a-z-]+)"', faq)
    assert 4 <= faq.count("<details>") <= 6 and len(links) == faq.count("<details>")
    for loc in LOCALES:
        anchors = {(ch.id, s.anchor) for ch in M.chapters(loc) for s in ch.sections}
        for ch, a in links:
            assert (ch, a) in anchors, (loc, ch, a)
    for path in set(re.findall(r'href="(/manual/[a-z-]+)"', html)) | {"/about"}:
        assert c.get(path, headers=HTML).status_code == 200, path


def test_footer_links_the_manual_about_and_legal_documents(c):
    from backend.accounts import terms
    html = (STATIC / "intro.html").read_text("utf-8")
    foot = html[html.index("<footer>"):]
    for href in ["/manual", "/about"] + [terms.URL_PREFIX + d.id for d in terms.DOCUMENTS]:
        assert f'href="{href}"' in foot, href
        assert c.get(href, headers=HTML).status_code == 200, href


def test_every_screenshot_is_there_in_both_languages_and_schemes_and_comes_from_the_shot_list():
    html = (STATIC / "intro.html").read_text("utf-8")
    used = set(re.findall(r'data-shot="([a-z0-9-]+)"', html))
    shots = json.loads(SHOTS.read_text("utf-8"))["shots"]
    listed = {s["name"] for s in shots}
    assert used and used <= listed, sorted(used - listed)
    for s in shots:
        assert s.get("schemes") == ["light", "dark"], s["name"]
    for name in used:
        for loc in LOCALES:
            for tail in ("", ".dark"):
                assert (STATIC / "intro" / f"{name}.{loc}{tail}.webp").is_file(), (name, loc, tail)
    on_disk = {p.name for p in (STATIC / "intro").iterdir()}
    want = {f"{n}.{loc}{tail}.webp" for n in listed for loc in LOCALES for tail in ("", ".dark")}
    assert on_disk == want, sorted(on_disk ^ want)


def test_the_shot_list_expands_to_each_language_and_scheme():
    from backend.scripts import manual_shots as MS
    from backend.tests.e2e import shots as S
    spec = S.check_spec(MS.spec_for(MS.load(which="intro"), "en", "SP-622"))
    names = [s["name"] for s in spec["shots"]]
    assert "intro-race.en" in names and "intro-race.en.dark" in names
    assert {s["scheme"] for s in spec["shots"]} == {"light", "dark"}
    assert all("lang=en" in s["path"] for s in spec["shots"])
    with pytest.raises(S.SpecError):
        S.check_spec({"ticket": "SP-1", "shots": [{"name": "x", "page": "overview", "scheme": "sepia"}]})


# ---------------------------------------------------------------------------- 「先試試看」

def test_try_goes_to_the_example_without_a_demo_url(c, monkeypatch):
    monkeypatch.delenv("TRC_DEMO_URL", raising=False)
    t = _try(c.get("/", headers=HTML).text)
    assert t == {"kind": "example", "url": "/share/example-trail-50k"}


def test_try_goes_to_the_demo_when_it_is_set(c, monkeypatch):
    monkeypatch.setenv("TRC_DEMO_URL", "https://demo.example.com")
    assert _try(c.get("/", headers=HTML).text) == {"kind": "demo", "url": "https://demo.example.com"}
    monkeypatch.setenv("TRC_DEMO_URL", "http://demo.example.com")          # not https: the example
    assert _try(c.get("/", headers=HTML).text)["kind"] == "example"
    for loc in LOCALES:
        assert json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))["cta.demo"]


def test_the_built_in_example_opens_signed_out_and_is_no_ones(c, app, monkeypatch):
    from backend import tenancy_mw as TM
    from backend.accounts import store
    from backend.engine.racepower import share as SH
    looked = []
    monkeypatch.setattr(store, "share_owner", lambda sid: looked.append(sid))
    assert TM._share_tenant("/share/" + SH.EXAMPLE_ID + "/data").kind == "anonymous"
    r = c.get("/share/" + SH.EXAMPLE_ID, headers=HTML)
    assert r.status_code == 200
    d = c.get("/share/" + SH.EXAMPLE_ID + "/data")
    assert d.status_code == 200 and looked == []
    snap = d.json()
    assert snap["example"] is True and "虛構" in snap["title"] and snap["expires"] is None
    assert snap["type"] == "trail" and snap["summary"]["km"] > 45 and snap["segments"]
    assert snap["weight"] is None and "w_per_kg" not in snap["summary"]       # the share's own privacy rules
    assert set(snap) - {"example"} <= set(SH.snapshot({}, title="x"))           # nothing but a snapshot's keys


def test_the_example_is_a_fixed_id_no_share_can_take():
    from backend.engine.racepower import share as SH
    assert SH.ID_RE.match(SH.EXAMPLE_ID) and len(SH.new_id()) != len(SH.EXAMPLE_ID)
    assert SH.load(SH.EXAMPLE_ID) == SH.example() and SH.example()["example"] is True
