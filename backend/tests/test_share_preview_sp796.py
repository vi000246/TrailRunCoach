"""SP-796: a share link pasted into LINE / Facebook shows a preview card — the race's name, distance and
climb, the predicted finish (登山: the moving time) and 「用自己的資料算」 — from Open Graph / Twitter card
tags in the HTML the server sends (the crawlers run no script). The text comes from the stored snapshot
(never recomputed), in the page's language; the race's name is the athlete's own text and is escaped.
The page stays noindex; the built-in examples (/share/example-*) have a card too."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import pytest

from backend.tests.test_free_entry_sp124 import BODY, plans  # noqa: F401 — fixture used by name
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

TRAIL = {"title": "東眼山 50K", "type": "trail", "days": [],
         "summary": {"km": 49.94, "gain_m": 2768.8, "time_s": 22819.0, "power": 240.0}}
HIKE = {"title": "雪山三日", "type": "baiyue", "days": [{"day": 1}, {"day": 2}, {"day": 3}],
        "summary": {"km": 37.45, "gain_m": 3689.2, "time_s": 54994.2, "capacity_time_s": 54994.2, "clock_s": 68742.8}}


class _Metas(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")):
            self.tags[a.get("property") or a.get("name")] = a.get("content")


def metas(html: str) -> dict[str, str]:
    p = _Metas()
    p.feed(html)
    return p.tags


def _meta(snap, loc, url="https://coach.example.org/share/abc"):
    from backend.api.racepower import share_preview_meta
    return share_preview_meta(snap, url, loc)


# ---------------------------------------------------------------------------- the tags

def test_a_trail_race_card_in_chinese():
    m = metas(_meta(TRAIL, "zh-TW"))
    assert m["og:title"] == m["twitter:title"] == "東眼山 50K"
    assert m["og:description"] == m["twitter:description"] == "越野 · 49.9 km ↑2,769 m · 預估完賽 6:20:19 · 用自己的資料算 →"
    assert m["og:type"] == "website" and m["og:locale"] == "zh_TW" and m["twitter:card"] == "summary"
    assert m["og:url"] == "https://coach.example.org/share/abc" and m["og:site_name"]
    assert "og:image" not in m                                     # text first, no picture yet


def test_the_card_follows_the_pages_language():
    m = metas(_meta(TRAIL, "en"))
    assert m["og:title"] == "東眼山 50K"                            # the athlete's own name, as shared
    assert m["og:description"] == "Trail · 49.9 km ↑2,769 m · Predicted finish 6:20:19 · Use your own data →"
    assert m["og:locale"] == "en_US"


def test_a_hike_shows_its_days_and_moving_time():
    assert metas(_meta(HIKE, "zh-TW"))["og:description"] == "登山 · 3 天 · 37.5 km ↑3,689 m · 移動時間 15:16:34 · 用自己的資料算 →"
    assert metas(_meta(HIKE, "en"))["og:description"] == "Hiking · 3 days · 37.5 km ↑3,689 m · Moving time 15:16:34 · Use your own data →"


def test_missing_numbers_are_left_out_not_shown_as_none():
    m = metas(_meta({"title": "x", "type": "road", "summary": {}}, "zh-TW"))
    assert m["og:description"] == "路跑 · 用自己的資料算 →"
    assert "None" not in _meta({"title": "x", "summary": {"km": 10.0}}, "en")


def test_no_snapshot_a_generic_card_that_names_no_race():
    m = metas(_meta(None, "zh-TW"))
    assert m["og:title"] == "賽事計畫（分享）" and "用自己的資料算" in m["og:description"]
    assert "og:url" not in m
    assert metas(_meta(None, "en"))["og:title"] == "Race plan (shared)"


@pytest.mark.parametrize("title", [
    '"><script>alert(1)</script>',
    "A & B <b>x</b> 'quoted' \"double\"",
    '" property="og:image" content="https://evil.example/x.png',
])
def test_the_title_is_escaped(title):
    out = _meta({**TRAIL, "title": title}, "zh-TW")
    assert "<script" not in out and "<b>" not in out
    # every tag is a whole <meta …>: the title never opens another tag or attribute
    assert all(re.fullmatch(r'<meta (property|name)="[\w:]+" content="[^"<>]*">', t) for t in out.split("\n")), out
    m = metas(out)
    assert m["og:title"] == m["twitter:title"] == title           # and it reads back as written
    assert "og:image" not in m


def test_a_title_with_newlines_is_one_line():
    assert metas(_meta({**TRAIL, "title": "a\n  b\tc"}, "en"))["og:title"] == "a b c"


def test_the_share_page_has_the_marker_in_its_head():
    from backend.i18n import pages as P
    src = (P.STATIC / "share.html").read_text("utf-8")
    head = src[:src.index("</head>")]
    assert "<!--fill:og-->" in head
    assert '<meta name="robots" content="noindex, nofollow">' in head          # still noindex


# ---------------------------------------------------------------------------- served

def _share(cl, title: str) -> str:
    r = cl.post("/api/v1/racepower/share", json={**BODY, "share_title": title}, headers={"X-TRC-CSRF": csrf(cl)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_shared_link_carries_the_card_signed_out_from_the_snapshot(c, app, plans, monkeypatch):
    from backend.api import racepower as RP
    sign_up(c, "g-alice", "alice@example.com")
    sid = _share(c, '我的 10K <script>x</script>')

    def boom(body):
        raise AssertionError("the preview recomputed the plan")

    monkeypatch.setattr(RP, "make_plan", boom)
    monkeypatch.setenv("WKO5COACH_PUBLIC_URL", "https://coach.example.org")
    anyone = browser(app, ip="198.51.100.40")
    r = anyone.get(f"/share/{sid}", headers={**HTML, "Accept-Language": "zh-TW"})
    assert r.status_code == 200
    assert r.headers["x-robots-tag"] == "noindex, nofollow"                   # still noindex
    assert '<meta name="robots" content="noindex, nofollow">' in r.text
    assert "<script>x</script>" not in r.text
    m = metas(r.text)
    snap = anyone.get(f"/share/{sid}/data").json()
    assert m["og:title"] == snap["title"] == "我的 10K <script>x</script>"
    assert m["og:description"] == "路跑 · 10.0 km · 預估完賽 0:50:00 · 用自己的資料算 →"
    assert m["og:url"] == f"https://coach.example.org/share/{sid}"
    # the tags sit in <head>, before any script the crawler would not run anyway
    assert r.text.index('property="og:title"') < r.text.index("</head>")
    en = metas(anyone.get(f"/share/{sid}", headers={**HTML, "Accept-Language": "en"}).text)
    assert en["og:description"] == "Road · 10.0 km · Predicted finish 0:50:00 · Use your own data →"


def test_an_expired_or_unknown_share_names_nothing(c, app, plans, monkeypatch):
    from backend.engine.racepower import share as SH
    sign_up(c, "g-alice", "alice@example.com")
    sid = _share(c, "會過期的比賽")
    anyone = browser(app, ip="198.51.100.41")
    monkeypatch.setattr(SH, "expired", lambda snap, now=None: True)
    page = anyone.get(f"/share/{sid}", headers=HTML).text
    assert "會過期的比賽" not in page and metas(page)["og:title"] == "賽事計畫（分享）"
    page = anyone.get("/share/" + "A" * 22, headers=HTML).text
    assert metas(page)["og:title"] == "賽事計畫（分享）" and "og:url" not in metas(page)


@pytest.mark.parametrize("sid", ["example-trail-50k", "example-baiyue-3day", "example-road-half"])
def test_the_built_in_examples_have_a_card(c, sid):
    snap = c.get(f"/share/{sid}/data").json()
    r = c.get(f"/share/{sid}", headers={**HTML, "Accept-Language": "zh-TW"})
    m = metas(r.text)
    assert m["og:title"] == snap["title"] and "虛構" in m["og:title"]
    assert f"{snap['summary']['km']:.1f} km" in m["og:description"]
    assert ("移動時間" if snap["type"] == "baiyue" else "預估完賽") in m["og:description"]
    assert m["og:url"].endswith(f"/share/{sid}")
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    json.dumps(m)                                                  # plain strings
