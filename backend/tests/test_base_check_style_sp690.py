"""SP-690: the 基礎檢查 card's colours (static/readiness.js). 強度分配 uses the 區間時數 chart's 3-zone colours (the
heart-rate red ramp, a dark-theme value for each), not saturated green / amber / red; 每週增量 is drawn like the
負荷平衡 % chart: tinted bands named in the right margin, no lines between them, 0 the one solid line, no dashed
goal / 注意 lines."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "readiness.js").read_text("utf-8")


def _ramp() -> str:
    i = JS.index("function rampFig(")
    return JS[i:JS.index("\n  }\n", i)]


def test_intensity_colours_are_the_zone_ramp_tokens_with_dark_values():
    assert 'const ZC = ["var(--rd-z1)", "var(--rd-z2)", "var(--rd-z3)"]' in JS
    for hexv in ("#16a34a", "#f59e0b", "#dc2626", "#2563eb"):                  # the old saturated marks
        assert hexv not in JS, hexv
    assert re.search(r"\.rd \{ --rd-z1: #faa39c; --rd-z2: #b94642; --rd-z3: #570e10;", JS)
    assert re.search(r'@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme="light"\]\) \.rd \{ --rd-z1:', JS)
    assert re.search(r':root\[data-theme="dark"\] \.rd \{ --rd-z1:', JS)


def test_ramp_is_drawn_like_the_load_balance_chart():
    r = _ramp()
    assert "stroke-dasharray" not in r                                        # no dashed goal / 注意 lines
    assert 'class="ref"' in r                                                 # the 0 line
    assert r.count("<line") == 1                                              # …and no other line
    assert 'T("ramp_z_" + z)' in r and "x1 + 4" in r                          # band names in the right margin
    assert "#fff" not in r                                                    # dots ringed in the panel colour


def test_band_names_are_translated():
    import json
    for lang in ("zh-TW", "en"):
        d = json.loads((ROOT / "static" / "i18n" / lang / "viewer.json").read_text("utf-8"))
        for z in ("low", "ok", "fast", "high"):
            assert d.get(f"readiness.ramp_z_{z}"), (lang, z)
        assert "readiness.ramp_goal" not in d and "readiness.ramp_watch" not in d
