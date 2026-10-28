"""設定頁分頁 (SP-213): 個人資料 / 偏好設定 (SP-1011) / 訓練依據 (SP-955; was 生理數據) / 資料同步 / 進階設定 on static/settings.html.
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


MAIN = PAGE[PAGE.index("<main>"):PAGE.index("</main>")]
# SP-955: 訓練依據 is one section (#basis) holding its cards as sections of their own (the old ids stay)
BASIS = MAIN[MAIN.index('<section id="basis">'):MAIN.index('<section id="sync">')]
BASIS_IDS = set(re.findall(r"<section\b[^>]*\bid=\"([\w-]+)\"", BASIS)) - {"basis"}


def _sections() -> list[str]:
    """The sections directly under <main> (the tab panels)."""
    return [s for s in re.findall(r"<section\b[^>]*\bid=\"([\w-]+)\"", MAIN) if s not in BASIS_IDS]


def test_four_tabs_each_section_on_one():
    """…and 疊石 Cairn (SP-666) and 教練 (SP-789), shown in sign-in mode only (test_cairn_tab_sp666.py,
    test_coaching_sp789.py)."""
    tabs = _tabs()
    assert list(tabs) == ["personal", "prefs", "body", "data", "advanced", "cairn", "coach"]
    assert tabs["cairn"] == ["cairn"]
    assert tabs["coach"] == ["coach"]
    placed = [i for ids in tabs.values() for i in ids]
    assert len(placed) == len(set(placed))                          # one tab per section
    secs = _sections()
    assert sorted(placed) == sorted(secs), set(placed) ^ set(secs)   # every section has an id and a tab
    # what the ticket groups: personal info / physiology (heart rate …) / advanced
    # SP-955: 主要訓練項目 moved to 訓練依據 (body), which is one section with the two columns' cards
    assert tabs["personal"][0] == "general" and "sport" not in tabs["personal"]
    # SP-1011: 外觀 and 地圖 are site preferences, not about you: their own tab right after 個人資料
    assert tabs["personal"] == ["general", "account"]
    assert tabs["prefs"] == ["appearance", "map"]
    assert tabs["body"] == ["basis"]
    assert {"equip", "thresholds", "overrides", "now", "physio"} == BASIS_IDS
    for i in ("sport", "hr", "hr-cmp", "pv-tiles", "b-aetp", "b-cpp", "b-basis", "b-climb", "hr-model", "pz-model"):
        assert f'id="{i}"' in BASIS, i
    assert 'id="power"' not in PAGE
    assert {"sync", "weightsync", "backup", "archive", "device", "calendar"} == set(tabs["data"])     # 運動紀錄保留 (SP-317),
    # 這台裝置的離線資料 (SP-322), 體重同步 (SP-472)
    assert {"mode", "calib", "fixes", "debugapi", "upstream", "askai"} == set(tabs["advanced"])   # debugapi: SP-371; upstream: SP-373; askai: SP-786
    # a section without an id would land on 進階設定 silently: there is none
    assert len(re.findall(r"<section\b", MAIN)) == len(secs) + len(BASIS_IDS)


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
    assert 'href="/api/v1/app/settings"' not in sched
    assert sched.count('/api/v1/app/settings#sync"') == 4
    for page, frag in (("plan.html", "#thresholds"), ("session_banner.js", "#sync")):
        assert f"/api/v1/app/settings{frag}" in (STATIC / page).read_text("utf-8")
    # SP-448: 圖表分析's 算式 pill (the link to #mode) is gone; the mode is picked in 設定 only
    assert "/api/v1/app/settings#mode" not in (STATIC / "charts.html").read_text("utf-8")


def _section_of(element_id: str) -> str:
    """The <section id> of main that holds the element with this id."""
    main = PAGE[PAGE.index("<main>"):PAGE.index("</main>")]
    at = main.index(f'id="{element_id}"')
    sec = re.findall(r"<section\b[^>]*\bid=\"([\w-]+)\"", main[:at])[-1]
    return "basis" if sec in BASIS_IDS else sec                   # SP-955: a card of 訓練依據


def test_the_controls_of_the_other_branches_land_on_their_tab():
    """Integration 2026-10-06: what SP-67 / SP-69 / SP-211 / SP-215 added to the settings page sits
    in the section of the tab it belongs to (SP-213), and SP-191's 肌力動作 is a 課表偏好 control
    on the schedule page, next to 每週肌力 (SP-540: both in the 肌力 tab)."""
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
    tab = sched[sched.index('id="pf-p-strength"'):]
    tab = tab[:tab.index('<div class="pf-tp"')]
    assert 'id="pf-st"' in tab and 'id="pf-sm"' in tab and 'id="pf-mv"' in tab and 'id="pf-ng"' in tab


def test_preferences_tab_holds_appearance_and_map_and_old_links_follow():
    """SP-1011: 偏好設定 (Preferences) right after 個人資料 holds 外觀 and 地圖; 個人資料 no longer does.
    The old deep links (#appearance, #map, or an id inside them such as #ap-theme / #m-base) pick the
    tab through the section that holds them (hashTab → tabOf), so they open 偏好設定 at that block."""
    nav = PAGE[PAGE.index('<nav class="tabs"'):PAGE.index("</nav>")]
    order = re.findall(r'data-tab="(\w+)"', nav)
    assert order[:2] == ["personal", "prefs"]
    assert '<button type="button" role="tab" id="tab-prefs" data-tab="prefs" data-i18n="settings.tabs.prefs">偏好設定</button>' in nav
    tab = {i: k for k, ids in _tabs().items() for i in ids}
    assert tab["appearance"] == tab["map"] == "prefs" and tab["account"] == "personal"
    for el in ("ap-theme", "ap-default", "m-base", "m-save"):               # ids inside the two blocks
        assert tab[_section_of(el)] == "prefs", el
    for el in ("p-age", "profnow"):
        assert tab[_section_of(el)] == "personal", el
    for loc, name in (("zh-TW", "偏好設定"), ("en", "Preferences")):
        cat = json.loads((STATIC / "i18n" / loc / "settings.json").read_text("utf-8"))
        assert cat["tabs.prefs"] == name
    # the hash → tab lookup the deep links go through
    assert 'const sec = el && el.closest("main > section");' in PAGE
    assert "return sec ? { tab: tabOf(sec), el } :" in PAGE
