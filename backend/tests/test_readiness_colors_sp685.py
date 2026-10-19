"""SP-685: the 減量檢查 card is a light bright cyan, not purple; the 專項期 card's 賽前第幾週 row has its own band;
the filled chips and the table's lines stay readable on the dark theme (static/readiness.js). The browser side
(computed colours, both themes): e2e/test_readiness_colours_sp685_e2e.py."""
from __future__ import annotations

import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "static" / "readiness.js").read_text("utf-8")
CSS = JS[JS.index("st.textContent = `"):JS.index("document.head.appendChild(st);")]


def test_no_purple_left():
    code = re.sub(r"/\*.*?\*/", "", JS, flags=re.S).lower()          # the CSS comment says what it replaced
    for purple in ("#7c3aed", "#8b5cf6", "#a78bfa", "#6d28d9", "violet", "purple"):
        assert purple not in code, purple


def test_the_taper_colour_is_cyan_with_a_dark_theme_value():
    light = re.search(r"--rd-tap: (#[0-9a-f]{6})", CSS).group(1)
    assert light.lower() in ("#22c3dd", "#22d3ee", "#06b6d4")
    # the dark theme redefines it (both the media query and data-theme="dark", as the pages' own tokens)
    assert re.search(r'@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme="light"\]\) \.rd \{[^}]*--rd-tap:', CSS)
    assert re.search(r':root\[data-theme="dark"\] \.rd \{[^}]*--rd-tap:', CSS)
    # the taper's text and marks use the readable ink, the fills carry dark text
    assert "color: var(--rd-tap-on)" in CSS and ".rd-bar .pt" in CSS and "background: var(--rd-tap-ink)" in CSS


def test_the_weeks_row_has_its_own_band():
    rule = re.search(r"\.rd-cal thead th \{([^}]*)\}", CSS).group(1)
    assert "background: var(--rd-head)" in rule and "border-bottom: 2px solid" in rule
    assert "--rd-head: var(--accent-soft)" in CSS


def test_filled_chips_do_not_put_white_text_on_light_fills():
    # the dark theme's fills (--ok #4ade80, --accent #60a5fa, --warn #fbbf24) are light: their text is the panel colour
    assert "color: #fff" not in CSS
    for sel in (".rd-c.s-done", ".rd-gate.passed .num", ".rd-sched", ".rd-days span.race"):
        rule = re.search(re.escape(sel) + r" \{([^}]*)\}", CSS).group(1)
        assert "var(--rd-on)" in rule, sel
