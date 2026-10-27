"""SP-540: 課表偏好 in tabs — 日期與時間 / 課表內容 / 肌力 / 賽事與測試 / 自動調整.

Presentation only: every setting stays one field of the one form (pfRead / pfFill /
PUT /prefs untouched), it just sits in the panel of its tab. The browser side (one panel
at a time, the last tab remembered, phone width) is in e2e/test_plan_prefs_tabs_sp540_e2e.py."""
from __future__ import annotations

import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = (STATIC / "schedule.html").read_text("utf-8")
DIALOG = PAGE[PAGE.index('<dialog class="sd wide" id="pf"'):]
DIALOG = DIALOG[:DIALOG.index("</dialog>")]

# the tab of every setting (form field name, or the id of a control the JS fills in)
WHERE = {
    "days": ["pf-days", "runs", "wh_mode", "weekly_hours", "cap_weekday", "cap_long", "cap_mode", "pf-pdays", "pf-pd-add", "pf-dw"],
    "content": ["quality", "terrain_easy", "terrain_long", "terrain_quality", "warmup_on", "warmup_min",
                "pf-gate", "quality_gate_weeks", "warmup_commute_min", "cooldown_min"],
    "strength": ["strength", "pf-sdays", "pf-sm", "pf-ng", "pf-mv"],
    "races": ["aet_test_days", "taper_days", "transition_weeks", "b2b"],   # SP-569: no heat rows
    "auto": ["ap-run", "ap_enabled", "ap_push", "ap_push_days", "ap_confirm_big", "ap_rpe_rule", "ap_notify", "ap-err"],
}


def _panels() -> dict[str, str]:
    out = {}
    for m in re.finditer(r'<div class="pf-tp" role="tabpanel" id="pf-p-(\w+)" aria-labelledby="pf-tab-\1" data-pfp="\1">', DIALOG):
        rest = DIALOG[m.end():]
        out[m.group(1)] = rest[:rest.index('\n    <div class="pf-tp"') if '\n    <div class="pf-tp"' in rest else rest.index('<div class="err" id="pf-err"')]
    return out


def test_five_tabs_in_order_each_with_its_panel():
    tabs = re.findall(r'<button type="button" role="tab" id="pf-tab-(\w+)" data-pft="\1" aria-controls="pf-p-\1"', DIALOG)
    assert tabs == ["days", "content", "strength", "races", "auto"]
    assert list(_panels()) == tabs
    assert 'role="tablist"' in DIALOG and 'data-i18n-attr="aria-label:schedule.pf.tabs"' in DIALOG
    # no more folded 進階 / 肌力動作: each tab shows all of its settings
    assert "<details" not in DIALOG and "pf-adv" not in PAGE


def test_every_setting_sits_in_its_tab():
    panels = _panels()
    for tab, keys in WHERE.items():
        for k in keys:
            hits = [t for t, html in panels.items() if f'name="{k}"' in html or f'id="{k}"' in html]
            assert hits == [tab], (k, hits)


def test_every_form_field_is_in_a_panel():
    """Nothing left outside the tabs (a field outside every panel would be shown under all of them)."""
    names = set(re.findall(r'\bname="(\w+)"', DIALOG))
    in_panels = set(re.findall(r'\bname="(\w+)"', "".join(_panels().values())))
    assert names == in_panels
    # and the JS reads the same fields as before (pfRead unchanged in what it sends)
    read = PAGE[PAGE.index("function pfRead()"):PAGE.index("function pfErrKey(")]
    for k in ("days:", "long_day:", "cap_weekday:", "runs:", "quality:", "strength:", "strength_days:", "weekly_hours:",
              "terrain_easy:", "target_basis:", "warmup_commute_min:", "warmup_on:", "pref_days:", "cp_test_protocol:",
              "aet_test_days:", "aet_test_protocol:", "quality_gate:", "b2b:", "transition_weeks:", "taper_days:",
              "strength_moves:", "strength_no_gear:"):
        assert k in read, k


def test_sp955_moved_settings_are_gone_and_sent_back_as_stored():
    """SP-955: 目標依據, 可跑坡目標, AeT／CP 測試方式 live in 設定 › 訓練依據 now — not in the dialog; the dialog
    sends them back as stored (PUT /prefs is the whole set); 主要訓練項目 sits first, above the tabs."""
    for gone in ('name="target_basis"', 'name="climb_target"', 'id="pf-cptp"', 'id="pf-aetp"'):
        assert gone not in DIALOG, gone
    assert 'href="/api/v1/app/settings#overrides"' in DIALOG and 'href="/api/v1/app/settings#thresholds"' in DIALOG
    assert DIALOG.index('id="pf-sport"') < DIALOG.index('class="pf-tabs"')
    read = PAGE[PAGE.index("function pfRead()"):PAGE.index("function pfErrKey(")]
    for k in ("target_basis", "climb_target", "cp_test_protocol", "aet_test_protocol"):
        assert f"{k}: PF_KEEP.{k}" in read, k
    op = PAGE[PAGE.index("async function openPrefs("):]
    assert "PF_KEEP = Object.fromEntries(PF_MOVED.map((k) => [k, b.prefs[k]]))" in op[:op.index("\n}\n")]
    # the sport is saved (the one setting both pages write) before the preferences
    sub = PAGE[PAGE.index('PFF.addEventListener("submit"'):]
    assert sub.index('sj("PUT", "/api/v1/sync/settings", { primary_sport: sport })') < sub.index('sj("PUT", `${PAPI}/prefs`, p)')


def test_last_tab_remembered_in_local_storage_with_try_catch():
    assert 'const ls = { get(k, d) { try { const v = localStorage.getItem("schedule." + k)' in PAGE
    assert 'set(k, v) { try { localStorage.setItem("schedule." + k, JSON.stringify(v)); } catch {} }' in PAGE
    body = PAGE[PAGE.index("function pfTab(tab, focus)"):]
    body = body[:body.index("\n}\n")]
    assert 'ls.set("pf.tab", tab)' in body
    op = PAGE[PAGE.index("async function openPrefs("):]
    op = op[:op.index("\n}\n")]
    assert 'ls.get("pf.tab", "days")' in op and 'pfTab("strength")' in op


def test_errors_point_at_their_tab():
    m = re.search(r"const PF_ERR_TAB = \{(.+?)\};", PAGE, re.S)
    tab = dict(re.findall(r"(\w+): \"(\w+)\"", m.group(1)))
    keys = set(re.findall(r'return \["(\w+)"', PAGE[PAGE.index("function pfErrKey("):PAGE.index("function pfError(")]))
    assert keys == set(tab), keys ^ set(tab)
    assert tab["no_day"] == "days" and tab["gate_weeks"] == "content" and tab["push_days"] == "auto"
    assert "pfErrShow(ek)" in PAGE and "pfErrTabMark(ek)" in PAGE


def test_i18n_both_languages():
    for lang in ("zh-TW", "en"):
        d = json.loads((STATIC / "i18n" / lang / "schedule.json").read_text("utf-8"))
        for k in ("pf.tabs", "pf.tab.days", "pf.tab.content", "pf.tab.strength", "pf.tab.races", "pf.tab.auto",
                  "pf.g.interval", "pf.g.strength", "pf.g.tests", "pf.g.race"):
            assert d.get(k), (lang, k)
        assert "pf.g.adv" not in d
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "schedule.json").read_text("utf-8"))
    assert [zh[f"pf.tab.{k}"] for k in ("days", "content", "strength", "races", "auto")] == \
        ["日期與時間", "課表內容", "肌力", "賽事與測試", "自動調整"]
    assert "進階" not in zh["pf.quality_tip"] and "間歇細節" in zh["pf.quality_tip"]


def test_phone_width_tabs_wrap_instead_of_scrolling():
    css = PAGE[PAGE.index(".pf-tabs {"):]
    css = css[:css.index("}")]
    assert "flex-wrap: wrap" in css and "overflow-x" not in css
