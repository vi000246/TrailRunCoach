"""SP-907: the 課表偏好 conflict texts (engine/plan_prefs.day_conflicts) in English.

The dialog gets them in the request's language; a week note that quotes one (place: 「… → 這週改排別天」)
is built with the shared plan cache in zh-TW and shown in the page's language (M_ texts). Synthetic data."""
from __future__ import annotations

import datetime as dt

import pytest

from backend import request_context as RC
from backend.engine import plan_prefs as PP
from backend.engine import stored_text as ST
from backend.i18n import use_locale
from backend.scripts.i18n_extract import CJK

MON = dt.date(2026, 10, 5)
WEEK = [MON + dt.timedelta(days=i) for i in range(7)]
FIELDS = ("text", "rule", "source", "action")

CASES = [
    PP.Prefs(long_day="thu", pref_days=(("quality", (4,)),)),                      # after_long
    PP.Prefs(long_day="thu", pref_days=(("quality", (2,)),)),                      # gap48
    PP.Prefs(long_day="sat", quality=2, pref_days=(("quality", (1, 2)),)),         # z5_twice
    PP.Prefs(long_day="thu", cap_weekday=45),                                      # cap_long
    PP.Prefs(long_day="sat", pref_days=(("aet_test", (6,)),)),                     # aet_weekday
    PP.Prefs(long_day="sat", pref_days=(("aet_test", (4,)),)),                     # aet_gap
    PP.Prefs(days=(False,) + (True,) * 6, long_day="sat",                          # not_allowed
             pref_days=(("quality", (0, 2)), ("cp_test", (0,)))),
]


def _ss():
    return [{"id": "long", "kind": "long", "title": "LSD", "minutes": 120, "day": None},
            {"id": "quality", "kind": "quality", "title": "閾值 3×8 分", "minutes": 45, "day": None},
            {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": None}]


@pytest.mark.parametrize("prefs", CASES)
def test_every_conflict_text_in_english(prefs):
    with use_locale("en"):
        cs = PP.day_conflicts(prefs)
    assert cs
    for c in cs:
        for k in FIELDS:
            assert c[k] and not CJK.search(c[k]), (c["code"], k, c[k])


def test_the_chinese_texts_are_unchanged():
    c = PP.day_conflicts(CASES[0])[0]
    assert c["text"] == "間歇排在長跑（週四）的隔天：長跑後腿還沒恢復（建議 ≥ 2 天，台灣教練）：要改到週二嗎？"
    assert (c["rule"], c["action"]) == ("長跑隔天不排強度課", "改到週二") and c["source"] == PP.SRC_GAP
    g = PP.day_conflicts(CASES[1])[0]
    assert g["text"] == "間歇和 LSD 只隔 1 天（建議 ≥ 2 天，台灣教練）：要改到週二嗎？"
    na = PP.day_conflicts(CASES[6])[0]
    assert na["text"] == "間歇偏好週一，但週一不是可練日" and na["rule"] == "可練日" and na["source"] == "課表偏好"


def test_words_and_counts():
    with use_locale("en"):
        g = PP.day_conflicts(CASES[1])[0]
        z = next(c for c in PP.day_conflicts(CASES[2]) if c["code"] == "z5_twice")
    assert g["text"] == ("Only 1 day between the intervals and the LSD (≥ 2 days recommended, Taiwanese coaches). "
                         "Move them to Tuesday?")
    assert g["action"] == "Moved to Tuesday"
    assert "Tuesday and Wednesday, are only 1 day apart" in z["text"]


def test_a_week_note_quoting_a_conflict_follows_the_page_language():
    # built in zh-TW (the shared plan cache), shown in English: the quoted conflict too
    p = PP.Prefs(long_day="thu", pref_days=(("quality", (4,)),))
    notes = []
    PP.place(_ss(), WEEK, 3, p, notes=notes)
    n = next(n for n in notes if "改排別天" in n["text"])
    assert "長跑（週四）的隔天" in n["text"]
    en = ST.shown(n["text"], "en")
    assert not CJK.search(en), en
    assert en.startswith("Intervals the day after the long run (Thursday)") and "another day this week" in en
    assert ST.shown(n["text"], "zh-TW") == str(n["text"])


def test_an_overlap_error_in_english():
    p = PP.Prefs(long_day="sat", pref_days=(("quality", (5,)),))
    with use_locale("en"), pytest.raises(ValueError) as e:
        PP.check(p)
    assert str(e.value) == ("Saturday is already assigned to LSD; can't also schedule Intervals "
                            "(one session type per day)")
    with pytest.raises(ValueError, match="週六已給LSD，不能再排間歇"):
        PP.check(p)


def test_api_conflicts_in_the_request_language(monkeypatch):
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        e.c.app.add_middleware(RC.RequestContextMiddleware)
        body = {"long_day": "thu", "pref_days": {"quality": [4]}}
        en = e.c.post(f"{API}/prefs/conflicts?lang=en", json=body).json()["day_conflicts"]
        zh = e.c.post(f"{API}/prefs/conflicts?lang=zh-TW", json=body).json()["day_conflicts"]
    assert [c["code"] for c in en] == [c["code"] for c in zh] == ["after_long"]
    assert not any(CJK.search(en[0][k]) for k in FIELDS), en[0]
    assert "長跑（週四）的隔天" in zh[0]["text"]
