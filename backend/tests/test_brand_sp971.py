"""
The product's name and badge (SP-971): Trig — 「Trig 基石」 in Chinese, 「Trig」 in English.
The name lives in one key (shell.brand); every other catalog text that names the product writes
{brand} (backend/i18n/pages.py fills it in). The badge: backend/static/icons/trig-logo.svg (64 px and
up) and trig-logo-small.svg (32 px and below; icon.svg, the tab's, is the same drawing).
Synthetic only: no WKO5 folder, no ~/.wko5coach.
"""
import json
import re
from pathlib import Path

from backend.i18n import pages as P
from backend.tests.test_login_sp58 import (  # noqa: F401 — the sign-in fixtures (fake Google)
    HTML, _clean_env, _client, app, c, env, started, sign_up)

STATIC = Path(__file__).resolve().parents[1] / "static"
ICONS = STATIC / "icons"
LOGO = '<img src="/api/v1/static/icons/trig-logo-small.svg" alt=""'


def _ns(loc: str) -> list[str]:
    return sorted(p.stem for p in (STATIC / "i18n" / loc).glob("*.json"))


def test_the_name_is_one_key():
    assert P.brand("zh-TW") == "Trig 基石" and P.brand("en") == "Trig"
    for loc in ("zh-TW", "en"):
        cat = P.page_catalog(_ns("zh-TW"), loc)
        flat = json.dumps(cat, ensure_ascii=False)
        assert "{brand}" not in flat, loc                                   # every mark filled in
        assert cat["shell.brand"] == P.brand(loc)
        assert cat["pricing.title"].endswith(" · " + P.brand(loc))
        # the name is written once: no other catalog file spells it out
        for p in (STATIC / "i18n" / loc).glob("*.json"):
            if p.stem != "shell":
                assert "Trig" not in p.read_text("utf-8"), p
        # the old name is gone from the catalogs (the legal page and the trademark notice: SP-1002)
        kept = {k for k, v in cat.items() if "TrailRunCoach" in json.dumps(v, ensure_ascii=False)}
        assert kept == set(), (loc, kept)
    zh = P.page_catalog(("about", "login"), "zh-TW")
    assert zh["about.h1"] == "關於 Trig 基石" and zh["login.cairn.full"].startswith("Trig 基石目前")


def test_the_headers_show_the_badge_and_the_name():
    for page in ("intro", "login", "pricing"):
        zh, en = P.render(page, "zh-TW", False), P.render(page, "en", False)
        assert LOGO in zh and '<span data-i18n="shell.brand">Trig 基石</span>' in zh, page
        assert '<span data-i18n="shell.brand">Trig</span>' in en, page
        brand = re.search(r'class="brand"[^>]*>(.*?)</(a|div)>', zh, re.S).group(1)
        assert "<svg" not in brand and "TrailRunCoach" not in brand, page        # the old calendar square is gone
    assert re.search(r"<title[^>]*>Trig 基石 · ", P.render("intro", "zh-TW", False))
    assert re.search(r"<title[^>]*>Trig · ", P.render("intro", "en", False))
    # the share page: the badge drawn inline (a visitor gets /share/*, not /api/v1/static)
    share = P.render("share", "zh-TW", False)
    head = share[share.index("<header>"):share.index("</header>")]
    assert '<a class="brand" href="/"><svg aria-hidden="true"' in head and ">Trig 基石</span>" in head
    assert "/api/v1/static/icons" not in head
    # the signed-in nav (shell.js): the badge instead of the blue calendar square
    js = (STATIC / "shell.js").read_text("utf-8")
    assert "CAL_ICON" not in js and LOGO in js and '${LOGO}<span>${T("brand")}</span>' in js


def test_the_logo_files():
    big, small = (ICONS / "trig-logo.svg").read_text("utf-8"), (ICONS / "trig-logo-small.svg").read_text("utf-8")
    assert (ICONS / "icon.svg").read_text("utf-8") == small                  # the tab's icon: the small drawing
    for svg in (big, small):
        assert 'fill="#1f4a52"' in svg and 'stroke="#f2c46d"' in svg       # the badge, its gold ring
    # the small one: three legs of switchback (four points), a thicker trail
    trail = re.search(r'<path d="(M[^"]+)" fill="none" stroke="#f3efe4" stroke-width="([\d.]+)"', small)
    assert trail and len(re.findall(r"[ML]", trail.group(1))) == 4 and float(trail.group(2)) >= 4


def _png(name: str) -> tuple[int, int]:
    b = (ICONS / name).read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(b[16:20], "big"), b[25]                            # width, colour type


def test_the_app_icons(c):
    # round badge on a transparent ground (RGBA); maskable and the iOS icon opaque squares (RGB)
    assert _png("icon-192.png") == (192, 6) and _png("icon-512.png") == (512, 6)
    assert _png("maskable-512.png") == (512, 2) and _png("apple-touch-icon.png") == (180, 2)
    zh = c.get("/manifest.webmanifest", headers={"Accept-Language": "zh-TW"}).json()
    en = c.get("/manifest.webmanifest", headers={"Accept-Language": "en"}).json()
    assert zh["name"] == zh["short_name"] == "Trig 基石" and en["name"] == en["short_name"] == "Trig"
    svg = [i for i in zh["icons"] if i["type"] == "image/svg+xml"]
    assert svg == [{"src": "/api/v1/static/icons/trig-logo.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]
    assert c.get(svg[0]["src"]).status_code == 200
