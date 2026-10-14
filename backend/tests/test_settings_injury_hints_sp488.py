"""SP-488 (docs/research/ui-hint-audit.md §7 T7): 設定 and 傷病紀錄 follow 提示多寡 and say each thing once.

The browser half (what shows in each level, the ≤ 8 count, one disclaimer, the red light / walk-run in every
level) is backend/tests/e2e/test_settings_injury_hints_sp488_e2e.py. Here: the pain-monitoring rule's one line
keeps the studies' own numbers (the rules themselves are not touched), the texts in both languages, and the
markup / script facts the browser test leans on."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from backend.engine import injuries as INJ
from backend.i18n import use_locale

STATIC = Path(__file__).resolve().parents[1] / "static"
SETTINGS = (STATIC / "settings.html").read_text("utf-8")
INJURIES = (STATIC / "injuries.html").read_text("utf-8")
CJK = re.compile(r"[一-鿿]")


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


# ---------------------------------------------------------------------------- 傷病: the monitoring rule

@pytest.mark.parametrize("cond, area, nums", [
    ("achilles", "achilles", [f"≤ {INJ.SILBERNAGEL['during_max']}/10", f"≤ {INJ.SILBERNAGEL['after_max']}/10"]),
    ("pfp", "knee", [f"≤ {INJ.ESCULIER['during_max']}/10", f"{INJ.ESCULIER['after_min']} 分鐘"]),
    ("itb", "knee", ["不能越跑越痛", "不能改變跑姿", "隔天不能更痛"]),
    ("plantar_fascia", "foot", ["不能越跑越痛", "不能改變跑姿", "隔天不能更痛"]),
    (None, "ankle", ["不能越跑越痛", "不能改變跑姿", "隔天不能更痛"]),
])
def test_the_one_line_rule_says_what_the_full_text_says(cond, area, nums):
    """IJ3: the same thresholds in the line and in the full text (which keeps the study for 「詳細」)."""
    m = INJ.monitor({"condition": cond, "area": area})
    for n in nums:
        assert n in m["rule"] and n in m["text"], (n, m)
    assert m["src"] in m["text"] and m["src"] not in m["rule"]          # the study goes to 「詳細」
    assert m["rule"].startswith("疼痛監測：") and len(m["rule"]) < len(m["text"])


def test_the_shin_line_still_says_see_a_doctor_first():
    m = INJ.monitor({"condition": None, "area": "shin_calf"})
    assert "先給醫師看" in m["rule"] and "先給醫師看" in m["text"]
    other = INJ.monitor({"condition": "other", "area": "shin_calf"})
    assert "先給醫師看" in other["rule"]


def test_the_list_carries_the_rule_and_an_illness_none():
    today = dt.date(2026, 10, 14)
    j = INJ.event_json({"id": 1, "area": "knee", "condition": "pfp", "severity": "mild", "status": "active",
                        "onset_date": "2026-10-01"}, today)
    assert j["monitor_rule"] == INJ.monitor({"condition": "pfp", "area": "knee"})["rule"]
    ill = INJ.event_json({"id": 2, "category": "illness", "illness": "cold", "status": "active",
                          "onset_date": "2026-10-10", "area": "unknown"}, today)
    assert ill["monitor"] is None and ill["monitor_rule"] is None


def test_the_rules_in_english():
    with use_locale("en"):
        for cond, area in (("achilles", "achilles"), ("pfp", "knee"), (None, "ankle"), (None, "shin_calf")):
            r = INJ.monitor({"condition": cond, "area": area})["rule"]
            assert r.startswith(("Pain monitoring:", "Shin / calf:")) and not CJK.search(r), r
        assert "≤ 2/10" in INJ.monitor({"condition": "pfp"})["rule"] and "60 minutes" in INJ.monitor({"condition": "pfp"})["rule"]


# ---------------------------------------------------------------------------- 傷病: the page

def test_the_disclaimer_is_only_in_the_footer():
    """IJ7: one copy on the page — the footer (its text from the server's meta)."""
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", INJURIES, flags=re.S)
    assert html.count("這不是醫療診斷") == 1 and 'id="disc" data-hint-id="IJ7"' in html
    assert "disclaimer" not in INJURIES.split("<script>")[1].replace('$("disc").textContent = S.meta.disclaimer', "")


def test_the_severity_scale_is_explained_in_the_dialog_only():
    """IJ6: the header ?, the cards' title and the analysis filter no longer repeat 「輕＝照練…」."""
    head = INJURIES[INJURIES.index("<header>"):INJURIES.index("</header>")]
    assert "照練" not in head
    dlg = INJURIES[INJURIES.index('<dialog id="dlg"'):INJURIES.index("</dialog>")]
    assert "輕＝照練" in dlg                                              # the dialog's ?
    js = INJURIES.split("<script>")[1]
    uses = [m.start() for m in re.finditer(r"severity_help", js)]
    render_dlg = js.index("function renderDlg()"), js.index("document.querySelectorAll(\"#f-cat")
    assert uses and all(render_dlg[0] < u < render_dlg[1] for u in uses)   # the dialog's chips 「輕・照練」 only


def test_the_light_the_walkrun_button_and_the_rule_are_not_level_dependent():
    """IJ1–IJ3 are class 1: the page draws them without reading 提示多寡 at all."""
    js = INJURIES.split("<script>")[1]
    assert "hint-level" not in INJURIES and "Hints." not in js
    for hid in ("IJ1", "IJ2", "IJ3"):
        assert f'data-hint-id="{hid}"' in js
    assert 'title="${esc(lab.walkrun_help' not in js and 'data-tip="${esc(lab.walkrun_help)}"' in js


def test_painpicker_has_no_dead_help():
    """IJ10: the HELP constant nobody used is gone."""
    assert "HELP" not in (STATIC / "painpicker.js").read_text("utf-8")


# ---------------------------------------------------------------------------- 設定

def test_the_mode_sentence_is_in_one_place():
    """ST15: 「只在我的算式生效」 once, in 計算模式 (was 4: two notes, #thr-q's help and the tab note's twin)."""
    mode = SETTINGS[SETTINGS.index('<section id="mode">'):]
    mode = mode[:mode.index("</section>")]
    assert SETTINGS.count('data-i18n="settings.mode.only_mine"') == 1 and 'data-i18n="settings.mode.only_mine"' in mode
    for loc in ("zh-TW", "en"):
        st = _cat(loc, "settings")
        assert "thr.help_nomode" not in st
        assert not re.search(r"我的算式|My formulas", st["thr.help"])


def test_st10_is_one_red_line():
    js = SETTINGS[SETTINGS.index("function renderThr() {"):SETTINGS.index("const cell = (i, f, v)")]
    # the WKO5 line's red only when the LTHR in use is not itself the default (that one says it)
    assert "WKO5_RED = !!(w && w.is_default && !ef.lthr.wko5_default);" in js
    for loc in ("zh-TW", "en"):
        t = _cat(loc, "settings")["thr.lthr_default"]
        assert ("出廠預設值" if loc == "zh-TW" else "factory default") in t


def test_the_tab_note_is_full_only_and_the_level_redraws():
    js = SETTINGS[SETTINGS.index("function tabNote() {"):]
    assert '$("tab-note").hidden = !FULL();' in js
    assert "function applyLevel()" in SETTINGS and "setLevel(s.level);" in SETTINGS


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_new_texts_in_both_languages(loc):
    st, cm = _cat(loc, "settings"), _cat(loc, "common")
    keys = ["hr.cmp_same", "thr.conf_high", "thr.conf_medium", "thr.conf_low", "thr.conf_tip_none", "thr.more_title",
            "thr.more_same", "thr.more_diff", "thr.more_none", "thr.more_wko5"]
    for k in keys:
        assert st.get(k), k
        if loc == "en":
            assert not CJK.search(st[k]), k
    used = set(re.findall(r't\("settings\.((?:hr|thr)\.(?:cmp_same|conf_\w+|more_\w+))"', SETTINGS))
    used |= {f"thr.more_{s}" for s in ("same", "diff", "none")}            # t(`settings.thr.more_${state}`)
    used |= {f"thr.conf_{c}" for c in ("medium", "low")}                    # t(`settings.thr.conf_${…}`)
    assert used <= set(st), used - set(st)
    for k in ("setup.line", "setup.f_weight", "setup.f_sex", "setup.f_age", "setup.sep"):
        assert cm.get(k), k
    assert "{list}" in cm["setup.line"]


# ---------------------------------------------------------------------------- R14 首次精靈

def test_the_wizard_interrupts_on_overview_and_settings_only():
    js = (STATIC / "setup_wizard.js").read_text("utf-8")
    assert 'const WIZARD_PAGES = ["home", "settings"];' in js
    assert "if (!WIZARD_PAGES.includes(page)) { missingLine(prof); return; }" in js
    assert '(window.TRC_SESSION || {}).mode === "demo"' in js            # the demo has no 設定 to link to
    shell = (STATIC / "shell.js").read_text("utf-8")
    assert 'data-page="home"' in (STATIC / "overview.html").read_text("utf-8")
    assert 'data-page="settings"' in SETTINGS and "setup_wizard.js" in shell
