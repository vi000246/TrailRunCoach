"""SP-766: the manual's 「疊石 Cairn」 section (backend/manual/<locale>/race.md #cairn) and the 關於 page's
(api/about.py, about.json cairn.*) — what Cairn does, how a route goes between the two, that linking the
accounts is the person's own step and can be undone; both languages, no address or name in the words.
Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.api import manual as M
from backend.tests.test_about_page_sp495 import owner  # noqa: F401 — fixture used by name
from backend.tests.test_login_sp58 import HTML

STATIC = Path(__file__).resolve().parents[1] / "static"
NO_ADDRESS = re.compile(r"yichlin|https?://|\.com\b|\.tw\b|擁有者|\bowner\b", re.I)


def _section(loc: str):
    ch = M.chapter(loc, "race")
    return next(s for s in ch.sections if s.anchor == "cairn")


@pytest.mark.parametrize("loc,words", [
    # SP-1062: the family gets a link to the route before you set off; the live track only with a Garmin inReach
    ("zh-TW", ("時間軸", "3D 地形", "一條連結", "接上 Garmin inReach 之後還看得到即時軌跡", "揪團", "自動免費註冊",
               "從 Cairn 匯入 GPX", "分享連結", "推到 Cairn", "不會自動連結", "解除連結", "候補名單", "不用了", "精簡")),
    ("en", ("timeline", "3D terrain", "one link", "connect a Garmin inReach and they can follow your live track",
            "group sign-ups", "free account", "Import a GPX from Cairn",
            "share link", "Push to Cairn", "never linked on their own", "unlink", "waiting list", "No thanks", "Lean")),
])
def test_the_manual_has_a_cairn_section_with_what_it_does(loc, words):
    s = _section(loc)
    text = s.simple_text + " " + s.deep_text
    for w in words:
        assert w in text, (loc, w)
    assert not NO_ADDRESS.search(text), NO_ADDRESS.findall(text)
    # the race manager's editor and the hint-level settings point at it
    page = M._fill(loc, M.chapters(loc), M.chapter(loc, "race"))["main"]
    assert '<section class="m-sec" id="cairn"' in page and 'href="/manual/race#cairn"' in page
    settings = M._fill(loc, M.chapters(loc), M.chapter(loc, "settings"))["main"]
    assert 'href="/manual/race#cairn"' in settings


def test_the_about_page_introduces_cairn_in_both_languages(owner):  # noqa: F811
    for loc, h, must in (("zh-TW", "疊石 Cairn", ("登山行程服務", "不會自動連結", "操作手冊")),
                         ("en", "Cairn", ("planning hiking trips", "never linked on its own", "manual"))):
        cat = json.loads((STATIC / "i18n" / loc / "about.json").read_text("utf-8"))
        r = owner.get(f"/about?lang={loc}", headers=HTML)
        assert r.status_code == 200
        t = r.text
        assert f'<section class="ab-sec" id="cairn"><h2>{h}</h2>' in t
        assert '<a href="#cairn">' in t                                    # in the page's contents
        assert t.index('id="version"') < t.index('id="cairn"') < t.index('id="data"')   # SP-1067: no 計算方法 section
        for k in ("cairn.what", "cairn.routes", "cairn.link"):
            assert cat[k] and not NO_ADDRESS.search(cat[k]), (loc, k)
        words = " ".join(cat[k] for k in ("cairn.what", "cairn.routes", "cairn.link"))
        for w in must:
            assert w in words, (loc, w)
