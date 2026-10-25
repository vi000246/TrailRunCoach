"""SP-908: why a session is not pushed to COROS (`coros.reason`) and a failed push's error in English.

sync/coros_workouts.py's reasons are msgids (zh-TW text unchanged); a status made in zh-TW (a background
push, a handler that stores texts in the source language) is shown in the request's language by the 課表
responses — `coros.reason` / `coros.error` only: the workout's name and steps on the watch are not touched
(what the watch gets is SP-908's open question). Synthetic data only."""
from __future__ import annotations

from datetime import date

import pytest

from backend import request_context as RC
from backend.engine import stored_text as ST
from backend.i18n import use_locale
from backend.scripts.i18n_extract import CJK
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env, g


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


STRENGTH = {"id": "s1", "kind": "strength", "title": "肌力維持", "minutes": 30, "day": "2026-10-01"}


def _reason(s, loc):
    with use_locale(loc):
        return CW.status_of(s, {"cp": 260, "lthr": 170, "aet": 150}, None, "2026-09-30")["reason"]


def test_the_reasons_are_the_same_chinese_and_english_in_english():
    assert _reason(STRENGTH, "zh-TW") == "COROS 肌力課要從動作庫挑動作，先不推"
    assert _reason(STRENGTH, "en") == "COROS strength workouts are built from its exercise library: not pushed for now"
    for s, zh in (({**STRENGTH, "kind": "heat_passive"}, "被動熱適應不推"),
                  ({**STRENGTH, "kind": "easy", "day": None}, "本週排不進去"),
                  ({**STRENGTH, "kind": "easy", "day": "2026-09-01"}, "日期已過"),
                  ({**STRENGTH, "kind": "quality", "title": "我的課"}, "看不懂間歇結構：我的課")):
        assert _reason(s, "zh-TW") == zh
        en = _reason(s, "en")
        assert en != zh and not CJK.search(en.replace("我的課", "")), en
        assert ST.shown(zh, "en") == en                     # a stored / zh-TW-made one reads the same


def test_a_failed_removal_reads_back():
    zh = "刪除失敗：COROS 500"
    assert ST.shown(zh, "en") == "Couldn't delete: COROS 500"
    assert ST.shown("更新時刪除舊的失敗：timeout", "en") == "Couldn't delete the old copy while updating: timeout"


def test_calendar_coros_reason_follows_the_language_the_watch_name_does_not(monkeypatch):
    with Env(monkeypatch) as e:
        e.c.app.add_middleware(RC.RequestContextMiddleware)
        e.inp["cur"]["sessions"].append(g("strength", "strength", "肌力維持", 30, "2026-10-01"))
        en = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04&lang=en").json()
        zh = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04&lang=zh-TW").json()
    s_en = next(s for s in en["sessions"] if s["kind"] == "strength")
    s_zh = next(s for s in zh["sessions"] if s["kind"] == "strength")
    assert s_zh["coros"]["reason"] == "COROS 肌力課要從動作庫挑動作，先不推"
    assert s_en["coros"]["reason"].startswith("COROS strength workouts")
    assert s_en["coros"]["title"] == s_zh["coros"]["title"] == "肌力維持"        # the watch's copy as it is
