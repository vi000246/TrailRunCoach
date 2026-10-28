"""SP-771: the screen's explanations say what the code does — six places, zh-TW and en.

1. 賽事 editor 背負 (races.ed.pack_tip): blank = 進階設定's value, else the own median after
   pack_defaults.MIN_TRIPS trips, else capacity.PACK_DEFAULT_SINGLE / _MULTI (SP-740).
2. 設定 › 基本資料 without a weight: the weight no longer sets the default pack (SP-738).
3. 進階設定 › 自動估算的參數: a sync refits at most every calibrate.CALIB_EVERY_DAYS days.
4. 傷病紀錄 ?: 復發 only within injuries.RECUR_DAYS days of the last 好了.
5. 手冊 入門: 下載失敗的活動 sits in the 資料同步 tab's 進階設定 fold, not the 進階設定 tab.
6. 設定 tab notes: what the 個人資料 / 偏好設定 / 資料同步 tabs hold (settings.html TABS).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.engine import calibrate, injuries
from backend.engine.racepower import capacity, pack_defaults

STATIC = Path(__file__).resolve().parents[1] / "static"
MANUAL = Path(__file__).resolve().parents[1] / "manual"


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _html(name: str) -> str:
    return (STATIC / name).read_text("utf-8")


def test_pack_tip_names_the_three_sources():
    n, single, multi = pack_defaults.MIN_TRIPS, capacity.PACK_DEFAULT_SINGLE, capacity.PACK_DEFAULT_MULTI
    zh, en = _cat("zh-TW", "races")["ed.pack_tip"], _cat("en", "races")["ed.pack_tip"]
    assert "進階設定" in zh and f"滿 {n} 趟" in zh and "中位數" in zh and f"單日 {single:g} kg" in zh and f"多日 {multi:g} kg" in zh
    assert "Advanced" in en and f"{n} trips" in en and "median" in en and f"{single:g} kg" in en and f"{multi:g} kg" in en
    # the page's own default text is the catalog's (the races page is rendered from it, the source stays in step)
    assert f'title="{zh}"' in _html("races.html")


def test_weight_no_longer_sets_the_pack():
    assert "背包" not in _cat("zh-TW", "settings")["general.no_weight"]
    assert "pack" not in _cat("en", "settings")["general.no_weight"]
    zh_help = _cat("zh-TW", "settings")["general.help"]
    assert "13 %" not in _html("settings.html") and f'title="{zh_help}"' in _html("settings.html")


def test_calib_intro_says_weekly():
    days = calibrate.CALIB_EVERY_DAYS
    zh, en = _cat("zh-TW", "settings")["calib.intro"], _cat("en", "settings")["calib.intro"]
    assert f"滿 {days} 天" in zh and "每次同步到新活動後重算" not in zh
    assert f"{days} days" in en and "once a week" in en
    assert 'data-i18n="settings.calib.intro"' in _html("settings.html")
    assert "每次同步到新活動後重算" not in _html("settings.html")
    assert _cat("en", "settings")["calib.summary"].startswith("Advanced")


def test_recurrence_help_says_42_days():
    page = _html("injuries.html")
    # SP-841: the title in its own data-i18n span, the ? beside it
    help_text = re.search(r'<h1><span data-i18n="injuries.title">傷病紀錄</span><button[^>]*data-tip="([^"]*)"', page).group(1)
    assert f"{injuries.RECUR_DAYS} 天內" in help_text and "復發" in help_text


def test_manual_start_points_at_the_data_tab():
    zh = (MANUAL / "zh-TW" / "start.md").read_text("utf-8")
    en = (MANUAL / "en" / "start.md").read_text("utf-8")
    assert "進階設定 › 下載失敗的活動" not in zh and "「資料同步」分頁的「進階設定」摺疊區" in zh
    assert "Advanced › failed downloads" not in en and "Data & sync tab" in en
    # the names the manual uses are the ones on the screen
    assert _cat("zh-TW", "settings")["syncfail.title"] in zh
    assert _cat("en", "settings")["syncfail.title"] in en and _cat("en", "settings")["tabs.data"] in en


def test_tab_notes_list_the_tabs_sections():
    zh, en = _cat("zh-TW", "settings"), _cat("en", "settings")
    for word in ("COROS", "Garmin", "intervals.icu", "TrainingPeaks"):
        assert word in zh["tabs.data_note"] and word in en["tabs.data_note"]
    for key in ("wsync.title", "arch.title", "device.title"):
        head = zh[key].split("（")[0]
        assert head in zh["tabs.data_note"], key
    for word in ("匯入", "備份", "課表訂閱"):
        assert word in zh["tabs.data_note"]
    for word in ("提示多寡", "跑步經驗", "帳號"):
        assert word in zh["tabs.personal_note"]
    for word in ("tips", "account"):
        assert word in en["tabs.personal_note"]
    # SP-1011: 外觀 and 地圖 moved to 偏好設定; each note names what its tab holds, and only that
    for word in ("外觀", "地圖"):
        assert word in zh["tabs.prefs_note"] and word not in zh["tabs.personal_note"]
    for word in ("appearance", "maps"):
        assert word in en["tabs.prefs_note"] and word not in en["tabs.personal_note"]
