"""設定頁分頁 (SP-213): 個人資料 / 生理數據 / 資料同步 / 進階設定 on static/settings.html.
Reads the repo's page and catalogs only."""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = (STATIC / "settings.html").read_text("utf-8")


def _tabs() -> dict[str, list[str]]:
    js = PAGE[PAGE.index("const TABS = {"):]
    js = js[:js.index("};")]
    return {k: re.findall(r'"(\w+)"', ids) for k, ids in re.findall(r"(\w+): \[([^\]]*)\]", js)}


def _sections() -> list[str]:
    main = PAGE[PAGE.index("<main>"):PAGE.index("</main>")]
    return re.findall(r"<section\b[^>]*\bid=\"([\w-]+)\"", main)


def test_four_tabs_each_section_on_one():
    tabs = _tabs()
    assert list(tabs) == ["personal", "body", "data", "advanced"]
    placed = [i for ids in tabs.values() for i in ids]
    assert len(placed) == len(set(placed))                          # one tab per section
    secs = _sections()
    assert sorted(placed) == sorted(secs), set(placed) ^ set(secs)   # every section has an id and a tab
    # what the ticket groups: personal info / physiology (heart rate …) / advanced
    assert tabs["personal"][0] == "general" and "sport" in tabs["personal"]
    assert {"hr", "thresholds", "power"} <= set(tabs["body"])
    assert {"sync", "backup", "calendar"} == set(tabs["data"])
    assert {"mode", "calib", "fixes", "debugapi"} == set(tabs["advanced"])      # debugapi: SP-371
    # a section without an id would land on 進階設定 silently: there is none
    main = PAGE[PAGE.index("<main>"):PAGE.index("</main>")]
    assert len(re.findall(r"<section\b", main)) == len(secs)


def test_tab_bar_markup_and_catalogs():
    nav = PAGE[PAGE.index('<nav class="tabs"'):PAGE.index("</nav>")]
    assert 'role="tablist"' in nav
    btns = re.findall(r'role="tab" id="tab-(\w+)" data-tab="(\w+)" data-i18n="settings\.tabs\.(\w+)"', nav)
    assert [(a, b, c) for a, b, c in btns] == [(k, k, k) for k in _tabs()]
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "settings.json").read_text("utf-8"))
        for k in _tabs():
            assert cat.get(f"tabs.{k}") and cat.get(f"tabs.{k}_note"), (loc, k)
        assert cat.get("tabs.aria") and cat.get("general.title"), loc
    # the hash picks the tab (deep links #sync / #thresholds / #mode / #power / #sport keep working)
    assert "window.addEventListener(\"hashchange\", tabFromHash)" in PAGE and "el.closest(\"main > section\")" in PAGE
    # hidden by a class, not [hidden]: #calib toggles its own `hidden` when it has no items
    assert "main > section.tab-off { display: none; }" in PAGE and 'classList.toggle("tab-off"' in PAGE


def test_settings_links_to_the_login_open_the_sync_tab():
    sched = (STATIC / "schedule.html").read_text("utf-8")
    assert 'href="/api/v1/wko5/settings"' not in sched
    assert sched.count('/api/v1/wko5/settings#sync"') == 4
    for page, frag in (("plan.html", "#thresholds"), ("wko5_viewer.html", "#mode"),
                       ("session_banner.js", "#sync")):
        assert f"/api/v1/wko5/settings{frag}" in (STATIC / page).read_text("utf-8")


def _section_of(element_id: str) -> str:
    """The <section id> of main that holds the element with this id."""
    main = PAGE[PAGE.index("<main>"):PAGE.index("</main>")]
    at = main.index(f'id="{element_id}"')
    return re.findall(r"<section\b[^>]*\bid=\"([\w-]+)\"", main[:at])[-1]


def test_the_controls_of_the_other_branches_land_on_their_tab():
    """Integration 2026-10-06: what SP-67 / SP-69 / SP-211 / SP-215 added to the settings page sits
    in the section of the tab it belongs to (SP-213), and SP-191's 肌力動作 is a 課表偏好 control
    on the schedule page, inside 課表內容／目標 next to 每週肌力."""
    tab = {i: k for k, ids in _tabs().items() for i in ids}
    assert tab[_section_of("hr-cmp")] == "body"            # SP-67 app vs watch card, 心率
    assert tab[_section_of("calib-tip")] == "advanced"     # SP-69 calibrated constants, 自動估算的參數
    assert tab[_section_of("calib-rows")] == "advanced"
    assert tab[_section_of("p-age")] == "personal"         # SP-211 age, 基本資料
    assert tab[_section_of("profnow")] == "personal"       # SP-211 「還缺：…」
    # SP-215 「花了 …」 under the source's 上次結果, in 資料同步
    assert tab[_section_of("src-coros")] == "data"
    assert "syncTook(r.secs)" in PAGE
    sched = (STATIC / "schedule.html").read_text("utf-8")
    g3 = sched[sched.index('aria-labelledby="pf-g3"'):]
    g3 = g3[:g3.index("</section>")]
    assert 'id="pf-st"' in g3 and 'id="pf-sm"' in g3 and 'id="pf-mv"' in g3 and 'id="pf-ng"' in g3
