"""SP-908: the watch's text in the account's language.

The account's language (settings athlete.locale) is the interface language it was last used in — shell.js
reports it (POST /api/v1/sync/locale). The COROS push builds every workout in zh-TW (its fingerprint) and sends
the name, overview and step names in that language: a zh-TW account gets exactly what it got before, an
English one English, and changing the language re-pushes nothing (the owner 2026-10-27: names update when the
session changes for another reason). A user's own words stay as written. Against the fake Training Hub of
test_coros_workouts; synthetic data only, nothing is sent anywhere."""
from __future__ import annotations

from datetime import date

import httpx
import pytest
from fastapi import HTTPException

from backend.i18n import use_locale
from backend.scripts.i18n_extract import CJK
from backend.settings.repository import SettingsRepository
from backend.sync import coros_workouts as CW, http
from backend.tests.test_coros_workouts import TH, FakeHub, _push, make_db, plan, run, sess


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


DAY = "2026-10-03"
LONG = {**sess("long", "long", "LSD（山路）", 120, DAY, detail="全程心率壓在 AeT 150 bpm 以下"), "key": "k/long"}
QUALITY = {**sess("q", "quality", "閾值 3×10 分", 60, "2026-10-01", detail="休 2–3 分鐘；暖身 15 分、緩和 10 分"),
           "key": "k/q"}
STRIDES = {**sess("e", "easy", "輕鬆跑＋加速跑 6×20 秒", 45, "2026-10-02"), "key": "k/e"}
STEEP = {**sess("h", "hike", "陡坡健走 25%", 90, DAY), "key": "k/h"}         # its main step's name is an _() text


def _texts(payload):
    return [payload["name"], payload["overview"]] + [ex["name"] for ex in payload["exercises"]]


def _shape(payload):
    """Everything but the texts: what the watch does is the same in every language."""
    return [{k: v for k, v in ex.items() if k != "name"} for ex in payload["exercises"]]


@pytest.mark.parametrize("s", [LONG, QUALITY, STRIDES, STEEP])
def test_english_account_gets_english_names_and_the_same_workout(s):
    zh = CW.session_workout(s, TH, "2026-09-30")
    en = CW.session_workout(s, TH, "2026-09-30", loc="en")
    assert any(CJK.search(t) for t in _texts(zh.payload))
    assert not [t for t in _texts(en.payload) if CJK.search(t)], _texts(en.payload)
    assert _shape(en.payload) == _shape(zh.payload)
    assert en.fingerprint == zh.fingerprint                     # what was pushed never depends on the language
    assert en.name == en.payload["name"] and en.name.startswith("TRC ")


def test_zh_tw_account_is_unchanged():
    for s in (LONG, QUALITY, STRIDES, STEEP):
        a = CW.session_workout(s, TH, "2026-09-30")
        b = CW.session_workout(s, TH, "2026-09-30", loc="zh-TW")
        assert a.payload == b.payload and a.fingerprint == b.fingerprint
    assert CW.session_workout(LONG, TH).payload["name"] == "TRC LSD（山路） 10/3"


def test_the_page_language_never_changes_the_workout():
    """Built in zh-TW whatever the request's language: a push from an English page (or its status) is the
    same workout as from a Chinese one — before, a step name that was an _() text (陡坡健走's main step)
    followed the page and made the workout look changed."""
    for s in (LONG, QUALITY, STRIDES, STEEP):
        zh = CW.session_workout(s, TH, "2026-09-30")
        with use_locale("en"):
            from_en = CW.session_workout(s, TH, "2026-09-30")
        assert from_en.payload == zh.payload and from_en.fingerprint == zh.fingerprint


def test_a_users_own_words_stay():
    s = {**LONG, "title": "跟阿明跑象山"}
    en = CW.session_workout(s, TH, "2026-09-30", loc="en")
    assert en.payload["name"] == "TRC 跟阿明跑象山 10/3"


def test_a_long_english_name_keeps_its_date():
    s = {**LONG, "title": "長爬坡反覆 4×6 分（6% 坡）"}
    name = CW.session_workout(s, TH, "2026-09-30", loc="en").payload["name"]
    assert len(name) <= CW.MAX_NAME and name.startswith("TRC Long climb") and name.endswith(" 10/3")


def test_not_pushed_reasons_still_follow_the_page():
    s = {**LONG, "kind": "strength", "title": "肌力維持"}
    with use_locale("en"):
        with pytest.raises(CW.Unsupported) as ei:
            CW.session_workout(s, TH, "2026-09-30")
    assert str(ei.value).startswith("COROS strength workouts")


async def _set_locale(db, loc):
    await SettingsRepository(db, 1).set("athlete.locale", loc)
    await db.commit()


def test_push_sends_the_account_language_and_a_switch_pushes_nothing_again():
    db = run(make_db())
    fake = FakeHub()
    week = plan([sess("long", "long", "LSD（山路）", 120, DAY, detail="全程心率壓在 AeT 150 bpm 以下"),
                 sess("easy1", "easy", "輕鬆跑", 45, "2026-10-02")])
    run(_set_locale(db, "en"))
    res = _push(db, week, fake)
    assert {r["status"] for r in res["sessions"]} == {"pushed"}
    names = sorted(p["name"] for p in fake.live().values())
    assert names == ["TRC Easy run 10/2", "TRC LSD (trail) 10/3"]
    steps = [ex["name"] for p in fake.live().values() for ex in p["exercises"]]
    assert steps and not [t for t in steps if CJK.search(t)]
    adds = fake.adds()

    run(_set_locale(db, "zh-TW"))                          # back to Chinese: nothing is pushed again
    res = _push(db, week, fake)
    assert fake.adds() == adds and {r.get("changed") for r in res["sessions"]} == {False}

    week["sessions"][1]["minutes"] = 50                    # changed for another reason: pushed in zh-TW now
    res = _push(db, week, fake)
    assert fake.adds() == adds + 1
    assert sorted(p["name"] for p in fake.live().values()) == ["TRC LSD (trail) 10/3", "TRC 輕鬆跑 10/2"]


def test_no_language_reported_is_zh_tw():
    db = run(make_db())
    assert run(CW.watch_locale(db)) == "zh-TW"
    assert run(CW.watch_locale("not a db")) == "zh-TW"           # a test double without settings


def test_library_workout_follows_the_account_language():
    db = run(make_db())
    run(_set_locale(db, "en"))
    fake = FakeHub()
    s = {"id": "ev1", "key": "race/ev1", "kind": "race_plan", "title": "輕鬆跑", "day": None,
         "steps": {"items": [{"id": "s1", "kind": "work", "dur": {"type": "time", "value": 1800},
                              "target": {"type": "auto", "intent": "easy", "plo": 0.65, "phi": 0.8}, "note": ""}]}}
    with http.use_transport(httpx.MockTransport(fake)):
        out = run(CW.push_workout(db, s, TH, "2026-09-30"))
    assert out["status"] == "pushed" and out["name"] == "TRC Easy run"
    (p,) = fake.live().values()
    assert p["name"] == "TRC Easy run" and not [t for t in _texts(p) if CJK.search(t)]


def test_the_shell_reports_the_language():
    from backend.api.sync import AccountLocale, account_locale
    db = run(make_db())
    assert run(account_locale(AccountLocale(locale="en-US"), 1, db)) == {"locale": "en"}
    assert run(SettingsRepository(db, 1).get("athlete.locale")) == "en"
    assert run(CW.watch_locale(db)) == "en"
    assert run(account_locale(AccountLocale(locale="zh-Hant-TW"), 1, db)) == {"locale": "zh-TW"}
    assert run(CW.watch_locale(db)) == "zh-TW"
    with pytest.raises(HTTPException) as ei:
        run(account_locale(AccountLocale(locale="fr"), 1, db))
    assert ei.value.status_code == 400
    with pytest.raises(ValueError):
        run(SettingsRepository(db, 1).set("athlete.locale", "fr"))
