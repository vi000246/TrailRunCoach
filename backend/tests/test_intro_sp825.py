"""SP-825: the 介紹頁's 給教練 — the coach's features (教練看板 SP-790, 代看 SP-791, the coach's changes SP-792) told
the way the other features are: a kicker, a title, one line and 3–4 short points on the left, one picture on the
right — a small 教練看板 with fictional students, marked as demo data.

- the section is a feature between 「讀懂每一趟」 and 「怎麼開始」, 3–4 points, the card says it is a demo;
- the card is static (no API, no account): fictional names only, the board's own marks (red / yellow chips,
  completion bars), red first like the board;
- its colours are the page's tokens (原本 light / dark, Trig 米白 / 深綠 through theme.css), no fixed colour;
- every word in both languages, no Chinese in English.
The browser side (one screen on a desktop, phone width, the palettes): e2e/test_intro_sp825_e2e.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "static"
LOCALES = ("zh-TW", "en")


def _page() -> str:
    return (STATIC / "intro.html").read_text("utf-8")


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))


def _section() -> str:
    html = _page()
    i = html.index('<section id="coach"')
    return html[i:html.index("</section>", i)]


def _css() -> str:
    html = _page()
    return html[html.index("<style>"):html.index("</style>")]


def test_the_section_is_a_feature_like_the_others():
    html = _page()
    order = [html.index(x) for x in ('id="read"', 'id="coach"', 'id="start"')]
    assert order == sorted(order)
    sec = _section()
    assert 'class="feature"' in sec and '<div class="text">' in sec and 'class="cbx reveal"' in sec
    assert 'data-i18n="intro.coach.kicker"' in sec and 'id="coach-h"' in sec and 'data-i18n="intro.coach.lead"' in sec
    text = sec[:sec.index("<figure")]
    assert 3 <= text.count("<li ") <= 4                                     # 3–4 short points
    # the text comes first (left on a desktop), the picture after it
    assert sec.index('<div class="text">') < sec.index("<figure")


def test_the_words_are_few():
    for loc in LOCALES:
        cat = _cat(loc)
        limit = 40 if loc == "zh-TW" else 90                     # characters: one line each on a desktop
        for k in ("coach.li.alerts", "coach.li.view", "coach.li.edit", "coach.li.consent"):
            assert len(cat[k]) <= limit, (loc, k, len(cat[k]))
        assert len(cat["coach.lead"]) <= (70 if loc == "zh-TW" else 180), loc


def test_the_card_is_a_demo_with_fictional_students():
    sec = _section()
    fig = sec[sec.index("<figure"):]
    assert 'data-i18n="intro.coach.card.sub"' in fig
    for loc in LOCALES:
        cat = _cat(loc)
        assert re.search(r"示範|虛構" if loc == "zh-TW" else r"[Dd]emo|[Ff]ictional", cat["coach.card.sub"]), loc
        assert re.search(r"示範" if loc == "zh-TW" else r"[Dd]emo", cat["coach.card.h"]), loc
    # static: no fetch, no account; four students, the board's marks, red first
    rows = re.findall(r'<li class="cbx-row(?: wide-only)?">(.*?)</li>', fig, re.S)
    assert len(rows) == 4 and fig.count('class="cbx-row wide-only"') == 1    # a phone shows three
    assert all('class="cbx-name"' in r and "cbx-bar" in r and 'class="cbx-chips"' in r for r in rows)
    assert "cbx-chip bad" in rows[0] and not any("cbx-chip bad" in r for r in rows[1:])
    assert "/api/" not in fig and "href=" not in fig
    # the races are the page's own fictional ones
    zh = _cat("zh-TW")
    assert "示範山徑越野 50K" in zh["coach.s1.race"] and zh["coach.s2.race"].startswith("示範")


def test_the_colours_are_the_themes_tokens():
    css = _css()
    i = css.index("/* SP-825")
    block = css[i:css.index("[hidden]", i)]
    # the card's own rules use tokens; fixed colours only in the three token definitions (原本 light, dark ×2)
    rules = block.split(":root[data-palette]")[1]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", rules), "a fixed colour in the card's rules"
    assert ":root[data-palette] { --cb-ok: var(--ok); --cb-warn: var(--warn); --cb-err: var(--err); }" in block
    assert '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"])' in block
    assert ':root[data-theme="dark"]' in block
    theme = (STATIC / "theme.css").read_text("utf-8")
    for pal in ("trig-cream", "trig-badge"):
        rule = theme[theme.index(f':root[data-palette="{pal}"] {{'):]
        rule = rule[:rule.index("}")]
        for tok in ("--ok:", "--warn:", "--err:", "--panel:", "--line:", "--muted:", "--soft:", "--accent:"):
            assert tok in rule, (pal, tok)


def test_a_phone_gets_two_columns_and_no_fixed_widths():
    css = _css()
    i = css.index("/* SP-825")
    block = css[i:css.index("[hidden]", i)]
    phone = block[block.index("@media (max-width: 560px)"):]
    assert ".cbx .cbx-row.head { display: none; }" in phone and ".cbx .cbx-row.wide-only { display: none; }" in phone
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)" in phone
    # nothing wider than a phone: no px widths but the small bar's max
    assert not re.search(r"(?<!max-)width:\s*\d{3,}px", block)


def test_every_coach_word_is_in_both_languages():
    zh, en = _cat("zh-TW"), _cat("en")
    ck = {k for k in zh if k.startswith("coach.")}
    assert ck and ck == {k for k in en if k.startswith("coach.")}
    used = set(re.findall(r'"intro\.(coach\.[\w.]+)"', _page()))
    assert used == ck, (used ^ ck)
    assert not re.search(r"[一-鿿]", json.dumps({k: en[k] for k in ck}, ensure_ascii=False))


def test_the_page_opens_with_the_section(c):
    for loc in LOCALES:
        r = c.get(f"/?lang={loc}", headers=HTML)
        assert r.status_code == 200 and 'id="coach"' in r.text
        assert _cat(loc)["coach.h"] in r.text, loc
