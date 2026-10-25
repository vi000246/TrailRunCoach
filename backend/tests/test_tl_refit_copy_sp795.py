"""SP-795: the COROS TL conversion is refitted inside engine/calibrate.calibrate, so a sync
refits it at most every calibrate.CALIB_EVERY_DAYS days (run_safe / due) — the three places
that said "after each sync" say so too, zh-TW and en:

1. 設定 › 生理數據 訓練負荷 ? (settings.tl.help, and settings.html's default title).
2. The push-to-COROS note on a 訓練負荷 step (engine/workout_steps.py msgid, en.json).
3. 手冊 FAQ › COROS 的訓練負荷（TL）.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.engine import calibrate

BACKEND = Path(__file__).resolve().parents[1]
STATIC = BACKEND / "static"
MANUAL = BACKEND / "manual"
LOCALES = BACKEND / "i18n" / "locales"

OLD_ZH = ("每次同步後用你自己有 TL", "每次同步後用你的活動重新校正")
OLD_EN = ("after each sync",)


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "settings.json").read_text("utf-8"))


def test_settings_tl_help_says_weekly():
    days = calibrate.CALIB_EVERY_DAYS
    zh, en = _cat("zh-TW")["tl.help"], _cat("en")["tl.help"]
    assert f"滿 {days} 天" in zh and "最多每週一次" in zh and "現在重新估算" in zh
    assert not any(s in zh for s in OLD_ZH)
    assert f"{days} days" in en and "once a week" in en and "Re-estimate now" in en
    assert not any(s in en for s in OLD_EN)
    # the page's default title is the catalog's
    page = (STATIC / "settings.html").read_text("utf-8")
    assert f'title="{zh}"' in page and not any(s in page for s in OLD_ZH)


def test_push_note_says_weekly():
    days = calibrate.CALIB_EVERY_DAYS
    # the msgid is split over two literals in the source; joined it is the en.json key
    src = (BACKEND / "engine" / "workout_steps.py").read_text("utf-8").replace('"\n                      "', "")
    assert not any(s in src for s in OLD_ZH)
    catalog = json.loads((LOCALES / "en.json").read_text("utf-8"))
    msgid = next(k for k in catalog if k.startswith("「{x}」段：這裡填的是訓練負荷"))
    assert f"最多每 {days} 天" in msgid and msgid in src
    assert f"every {days} days" in catalog[msgid] and not any(s in catalog[msgid] for s in OLD_EN)
    assert msgid in json.loads((LOCALES / "en.meta.json").read_text("utf-8"))


def test_manual_faq_says_weekly():
    days = calibrate.CALIB_EVERY_DAYS
    zh = (MANUAL / "zh-TW" / "faq.md").read_text("utf-8")
    en = (MANUAL / "en" / "faq.md").read_text("utf-8")
    zh_tl = zh.split("### COROS 的訓練負荷（TL）", 1)[1].split("\n:::", 1)[0]
    en_tl = en.split("### COROS's Training Load (TL)", 1)[1].split("\n:::", 1)[0]
    assert f"滿 {days} 天" in zh_tl and "最多每週一次" in zh_tl and "每次同步後" not in zh_tl
    assert f"{days} days" in en_tl and "once a week" in en_tl and "after each sync" not in en_tl
