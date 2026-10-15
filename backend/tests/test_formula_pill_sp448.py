"""SP-448: the 算式 pill at the top right of 圖表分析 is gone. The formula mode (我的算式 /
WKO5 對照模式) is picked on the settings page only (設定 › 計算模式, #mode), which keeps
working as before."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
VIEWER = (STATIC / "charts.html").read_text("utf-8")
SETTINGS = (STATIC / "settings.html").read_text("utf-8")


def test_the_viewer_has_no_formula_pill():
    # the markup, its link to the settings tab and the script that filled it
    assert 'id="modepill"' not in VIEWER
    assert '$("modepill")' not in VIEWER
    assert "/api/v1/app/settings#mode" not in VIEWER
    assert "算式：" not in VIEWER
    # what existed only for it: the calculator icon and the .pill styles
    assert 'id="i-calc"' not in VIEWER and "#i-calc" not in VIEWER
    assert not re.search(r"^\.pill\b", VIEWER, re.M)
    assert 'class="pill' not in VIEWER and '"pill ' not in VIEWER


def test_no_page_links_the_formula_pill_any_more():
    for f in sorted(STATIC.glob("*.html")) + sorted(STATIC.glob("*.js")):
        assert "modepill" not in f.read_text("utf-8"), f.name


def test_the_viewer_still_reads_the_mode_for_its_charts():
    # the charts are still drawn in the chosen mode; only the pill showing it is gone
    assert "S.parity = !!cfg.config.parity" in VIEWER
    assert "parity=${S.parity}" in VIEWER


def test_the_settings_page_still_offers_both_modes():
    main = SETTINGS[SETTINGS.index("<main>"):SETTINGS.index("</main>")]
    sec = main[main.index('<section id="mode">'):]
    sec = sec[:sec.index("</section>")]
    assert re.findall(r'<button class="mode" data-parity="(\w+)">', sec) == ["false", "true"]
    assert "我的算式" in sec and "對照模式" in sec and "WKO5" not in sec
    # the buttons save the choice and show which one is on
    assert 'document.querySelectorAll(".mode").forEach((b) => b.onclick' in SETTINGS
    assert 'method: "PUT"' in SETTINGS[SETTINGS.index("async function loadMode()"):][:1200]
    assert "loadMode()" in SETTINGS


def test_switching_the_mode_through_the_settings_api(tmp_path, monkeypatch):
    """What the settings page's two buttons call: PUT /config, read back by GET /config."""
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import config as C
    monkeypatch.setattr(C, "CONFIG_PATH", tmp_path / "engine.json")
    WV.put_config({"parity": True})
    assert WV.get_config()["config"]["parity"] is True
    preset = WV.get_config()["mountain_preset"]
    WV.put_config({**preset, "parity": False})                      # what 我的算式 sends
    cfg = WV.get_config()["config"]
    assert cfg["parity"] is False
    assert cfg["hr_tss_moving_only"] is True and cfg == {**preset, "parity": False}
