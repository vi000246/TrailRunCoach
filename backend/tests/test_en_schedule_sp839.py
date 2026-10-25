"""SP-839: the English 課表 page's titles, reasons and type words from the server.

The stored sessions keep zh-TW text (the msgids; code reads 「N×M 分」 off a title) and the plan
inputs are one cache for every language, always built in zh-TW; GET /calendar shows the titles in the
request's language (engine/stored_text: one msgid, one msgid filled in, or pieces), keeps the stored one
as `title_src`, and a title sent back as shown is stored in zh-TW again. A title the user typed stays as
written. Synthetic data only."""
from __future__ import annotations

from datetime import date

import pytest

from backend import request_context as RC
from backend.api import plan_sessions
from backend.engine import stored_text as ST
from backend.i18n import DEFAULT_LOCALE, current_locale, use_locale
from backend.scripts.i18n_extract import CJK
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env, run


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


# ---- engine/stored_text ---------------------------------------------------------------------------

TITLES = {
    "輕鬆跑": "Easy run",
    "LSD（山路）": "LSD (trail)",
    "LSD（路跑）": "LSD (road)",
    "長爬坡反覆 2×20 分（8% 坡）": "Long climb repeats 2×20 min (8% grade)",
    "恢復週 fartlek 4×1 分": "Recovery week fartlek 4×1 min",
    "肌力維持（高踏階＋離心下階＋引體向上）＋平衡／腳踝":
        "Strength maintenance (high step-up + eccentric step-down + pull-up) + balance / ankle",
    "輕鬆越野跑＋坡道衝刺 8×10 秒": "Easy trail run + hill sprints 8×10 s",
    "有氧間歇 2×15 分（平路）": "Aerobic intervals 2×15 min (flat)",
    "VO2max 間歇 5×4 分上坡": "VO2max intervals 5×4 min uphill",
    "CP 測試 12 分 + 3 分": "CP test 12 min + 3 min",
    "CP 測試 20 分全力": "CP test 20 min all-out",
    "AeT 飄移測試 60 分": "AeT drift test 60 min",
    "長跑（減量期，≤ 90 分）": "Long run (taper, ≤ 90 min)",
    "B2B 第 1 天｜LSD（山路）": "B2B day 1 | LSD (trail)",
    "⚠ 課表待確認": "⚠ Plan to confirm",
}


def test_generated_titles_in_english():
    got = {t: ST.shown(t, "en") for t in TITLES}
    assert got == TITLES
    assert all(ST.shown(t, "zh-TW") == t for t in TITLES)            # Chinese unchanged


def test_a_race_name_in_a_generated_title_stays_as_written():
    assert ST.shown("賽事模擬｜示範山徑越野 50K", "en") == "Race simulation | 示範山徑越野 50K"
    assert ST.shown("賽事模擬｜示範山徑越野 50K 第 2 天", "en") == "Race simulation | 示範山徑越野 50K day 2"


def test_the_users_own_titles_stay_as_written():
    for t in ("早上河濱慢跑", "合歡山", "輕鬆跑 河濱", "我的課"):
        assert ST.shown(t, "en") == t


def test_a_shown_title_goes_back_to_the_stored_one():
    for zh, en in TITLES.items():
        assert ST.shown(zh, "en") == en
        assert ST.to_source(en, "en") == zh
    assert ST.to_source("My own run", "en") == "My own run" and ST.to_source("合歡山", "en") == "合歡山"
    assert ST.to_source("Easy run", "zh-TW") == "Easy run"            # a Chinese page sends what it shows


# ---- the plan inputs: one cache, built in zh-TW --------------------------------------------------

def test_the_inputs_are_built_in_zh_tw_whatever_the_request(monkeypatch):
    seen = []
    monkeypatch.setattr(plan_sessions, "_build_inputs_in", lambda *a, **k: seen.append(current_locale()) or {})
    with use_locale("en"):
        plan_sessions._build_inputs(("k",), None, date(2026, 9, 30), None, None, None, True, True, [])
        assert current_locale() == "en"
    assert seen == [DEFAULT_LOCALE]


# ---- GET /calendar in English ---------------------------------------------------------------------

def _extras(monkeypatch):
    acts = [{"index": 11, "date": "2026-09-29", "category": "road", "category_label": "路跑", "moving_s": 2400,
             "tss": 30, "session": {"type": "easy", "type_label": "輕鬆跑"}}]
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda s, e: {
        "activities": [a for a in acts if s <= a["date"] <= e],
        "phases": [{"kind": "base", "label": "基礎期", "start": "2026-09-01", "end": "2026-10-11"}]})


def _env(monkeypatch):
    e = Env(monkeypatch)
    e.c.app.add_middleware(RC.RequestContextMiddleware)
    return e


def test_calendar_titles_and_type_words_follow_the_language(monkeypatch):
    _extras(monkeypatch)
    with _env(monkeypatch) as e:
        zh = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-18&lang=zh-TW").json()
        en = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-18&lang=en").json()
        again = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-18&lang=zh-TW").json()
    assert again == zh                                                   # an English request changed nothing
    by = {s["uid"]: s for s in en["sessions"]}
    long = next(s for s in zh["sessions"] if s["day"] == "2026-10-18")
    assert long["title"] == "LSD（山路）" and "title_src" not in long
    assert by[long["uid"]]["title"] == "LSD (trail)" and by[long["uid"]]["title_src"] == "LSD（山路）"
    titles = [s["title"] for s in en["sessions"]]
    assert not [t for t in titles if CJK.search(t)], titles
    a = en["activities"][0]
    assert a["category_label"] == "Road" and a["session"]["type_label"] == "Easy run"
    assert en["default_titles"]["easy"] == "Easy run" and en["family_titles"]["vo2max"] == "VO2max intervals"
    assert zh["default_titles"]["easy"] == "輕鬆跑" and zh["activities"][0]["category_label"] == "路跑"


def test_a_title_sent_back_as_shown_is_stored_in_zh_tw(monkeypatch):
    _extras(monkeypatch)
    with _env(monkeypatch) as e:
        en = e.c.get(f"{API}/calendar?start=2026-10-12&end=2026-10-18&lang=en").json()
        long = next(s for s in en["sessions"] if s["day"] == "2026-10-18")
        r = e.c.patch(f"{API}/sessions/{long['uid']}?lang=en", json={"title": long["title"], "minutes": 150})
        assert r.status_code == 200 and r.json()["title"] == "LSD (trail)"
        mine = e.c.patch(f"{API}/sessions/{long['uid']}?lang=en", json={"title": "Hills with friends"}).json()
        assert mine["title"] == "Hills with friends"
        from backend.engine import plan_store as PS
        stored = {s["uid"]: s for s in run(PS.load(e.db))}
    assert stored[long["uid"]]["title"] == "Hills with friends"


def test_the_shown_title_is_stored_as_the_generated_one(monkeypatch):
    _extras(monkeypatch)
    with _env(monkeypatch) as e:
        en = e.c.get(f"{API}/calendar?start=2026-10-12&end=2026-10-18&lang=en").json()
        long = next(s for s in en["sessions"] if s["day"] == "2026-10-18")
        e.c.patch(f"{API}/sessions/{long['uid']}?lang=en", json={"title": long["title"], "minutes": 150})
        from backend.engine import plan_store as PS
        stored = {s["uid"]: s for s in run(PS.load(e.db))}
    assert stored[long["uid"]]["title"] == "LSD（山路）" and stored[long["uid"]]["minutes"] == 150


def test_new_session_with_a_shown_default_title(monkeypatch):
    _extras(monkeypatch)
    with _env(monkeypatch) as e:
        en = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04&lang=en").json()
        r = e.c.post(f"{API}/sessions?lang=en", json={"day": "2026-10-03", "kind": "easy", "minutes": 30,
                                                       "title": en["default_titles"]["easy"]})
        assert r.status_code == 200 and r.json()["title"] == "Easy run"
        from backend.engine import plan_store as PS
        stored = next(s for s in run(PS.load(e.db)) if s["uid"] == r.json()["uid"])
    assert stored["title"] == "輕鬆跑"


# ---- the week notes the inputs cache: M_() texts, shown in the request's language --------------------

def test_week_notes_follow_the_language():
    import datetime as dt
    from backend.engine import blackouts as BL
    from backend.engine import plan_prefs as PP
    with use_locale("zh-TW"):
        b = BL.Blackout(id="b1", start="2026-10-01", end="2026-10-04", label="連假出遊")
        n = BL.week_note(BL.blocked([b]), [dt.date(2026, 10, d) for d in (1, 2, 3, 4)], 3.57)
        step = BL.step_note([dt.date(2026, 9, 24)], 2.0, 2.5)
        pref = PP.blocked_pref_notes(PP.Prefs(pref_days=(("quality", (1,)),)), dt.date(2026, 9, 28), {"2026-09-29": b})
    assert n["text"] == "10/1–10/4 不排課（連假出遊），本週少 3.6 小時"            # Chinese as before
    assert ST.shown(n["text"], "en") == "No training 10/1–10/4 (連假出遊): this week is 3.6 h shorter"
    assert ST.shown(step["text"], "en").startswith("Last week had no training 9/24 and you trained only 2.0 h")
    assert [x["text"] for x in pref] == ["間歇偏好的週二（9/29）是不排課日期：這週改排別天"]
    assert ST.shown(pref[0]["text"], "en") == \
        "Your preferred Tuesday for Intervals (9/29) is a no-training day: another day this week"


def test_template_recommendation_reasons_in_english():
    from backend.engine import template_recs as TR
    row = {"key": "x", "items": [{"kind": "work", "dur": {"type": "time", "value": 3600}}]}
    s = {"kind": "long", "cap": 30, "minutes": 60.0, "terrain": "road", "phase": "base",
         "z5_open": False, "rung": None, "ladder_key": None, "ladder_reason": "", "sport": "road"}
    with use_locale("en"):
        why = TR._score(row, "trail", None, s)
    words = [w for _p, w in why.plus + why.minus]
    assert not [w for w in words if CJK.search(w)], words
    assert {"Over the day's cap of 30 min (needs 60 min)", "Your main sport is road running"} <= set(words)
    with use_locale("zh-TW"):
        assert "主要訓練項目是路跑" in [w for _p, w in TR._score(row, "trail", None, s).minus]


def test_insert_template_names_and_groups_in_english():
    names = ["有出處的課表", "間歇庫（進階階梯）", "Pfitzinger 恢復跑 30′", "快步跑 4×20 秒（間隔慢跑 40 秒）",
             "A1 有氧間歇 2×15 分上坡 · 慢跑或走下坡恢復（約 3 分）", "陡坡健走 13%（模擬背 5% 體重）",
             "VO2max 間歇 2 組 × 13×30 秒／15 秒（每趟 < 2 分，不算進階）"]
    out = [ST.shown(t, "en") for t in names]
    assert not [t for t in out if CJK.search(t)], out
    assert out[4] == "A1 Aerobic intervals 2×15 min uphill · jog or walk down to recover (about 3 min)"
