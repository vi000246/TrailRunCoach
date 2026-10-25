"""SP-905: a session's 目標 / 說明 / 出處 (target, detail, source) and the test / template rows' tip and
source in English.

The generators keep writing zh-TW (stored, read by code: 「；」 parts, 「心率 x–y bpm」, 「目標配速」) — their
texts are msgids now, and engine/stored_text reads a stored one back in the request's language: one msgid,
one filled in, pieces, or parts joined by 「；」「。」「·」 (a run of parts may be one msgid; a 「、」 list in a
field item by item). The 課表 responses keep the stored text as `<key>_src`; a text sent back as shown is
stored in zh-TW again; handlers that make stored texts run in zh-TW (plan_sessions._stored_texts).
Synthetic data only."""
from __future__ import annotations

from datetime import date

import pytest

from backend import request_context as RC
from backend.api import plan_sessions
from backend.engine import aet_test as AT
from backend.engine import cp_protocols as CPP
from backend.engine import hr_profile as HP
from backend.engine import interval_library as IL
from backend.engine import overview as O
from backend.engine import stored_text as ST
from backend.engine import strength_plan as STP
from backend.i18n import use_locale
from backend.scripts.i18n_extract import CJK
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env, g, run

TH = {"cp": 250.0, "lthr": 170.0, "aet": 150.0, "aet_measured": True}
FIELDS = ("target", "detail", "source")


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def _texts(s: dict, keys=FIELDS) -> list[str]:
    return [s[k] for k in keys if isinstance(s.get(k), str) and CJK.search(s[k])]


def _en(t: str) -> str:
    ST._MEMO.clear()
    return ST.shown(t, "en")


def _left(texts) -> list:
    return [(t, _en(t)) for t in texts if CJK.search(_en(t))]


# ---- the generators: zh-TW stored, English shown --------------------------------------------------

def test_cp_and_aet_test_sessions_read_in_english():
    texts = []
    for p in CPP.PROTOCOLS:
        texts += _texts(CPP.session_for(p) or {})
    for proto in AT.PROTOCOLS:
        texts += _texts(AT.session(TH, AT.start_hr(None, TH["lthr"]), AT.start_power(TH["cp"]), None, proto, None))
        texts.append(AT.protocol_tip(proto))
    assert texts and not _left(texts)


def test_interval_library_sessions_read_in_english():
    texts = []
    for key in sorted(IL.ALL)[:40]:
        texts += _texts(IL.variant_patch(key, None, TH))
    assert texts and not _left(texts)


def test_long_runs_and_the_easy_cap_read_in_english():
    texts = []
    for kind in ("base", "specific"):
        texts += _texts(O.road_long_session(120, kind, 150, 50.0, aet_measured=True))
    texts += [HP.easy_cap_hr(150, True), HP.below(HP.easy_cap_label(None, 148, False))]
    assert texts and not _left(texts)
    assert _en(HP.easy_cap_hr(150, True)) == "HR ≤ easy-run cap 150 bpm (measured AeT)"
    assert HP.easy_cap_hr(150, True) == "心率 ≤ 輕鬆跑上限 150 bpm（實測 AeT）"          # zh-TW unchanged


def test_a_strength_session_reads_in_english():
    s = STP.session(None)
    assert _texts(s) and not _left(_texts(s))


def test_parts_a_list_and_a_users_name():
    # parts joined by 「；」, a run of parts that is one msgid, a 「、」 list inside a field
    assert _en("平路或緩坡；全程心率壓在輕鬆跑上限 150 bpm 以下") == \
        "Flat or gently rolling ground; keep your heart rate under the easy-run cap 150 bpm the whole way"
    t = ("2–4 個動作各 2 組，不做到力竭、不加新動作：高踏階 8 下／腳（可背包）、離心下階 8–10 下／腳、引體向上（留 2 下）、"
         "棒式 30 秒；安排在輕鬆日或跑完後；組數次數是推估")
    assert _en(t) == ("2–4 moves, 2 sets each, not to failure, nothing new: high step-up 8 reps / leg (a pack is fine), "
                      "eccentric step-down 8–10 reps / leg, pull-up (2 in reserve), plank 30 s; on an easy day or after a "
                      "run; sets and reps are an estimate")
    # a user's own text in a session stays as written
    assert _en("早上跟朋友跑河濱") == "早上跟朋友跑河濱"


# ---- the 課表 responses -------------------------------------------------------------------------------

def _env(monkeypatch, sessions):
    e = Env(monkeypatch)
    e.c.app.add_middleware(RC.RequestContextMiddleware)
    e.inp["cur"]["sessions"] = sessions
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda s, e_: {"activities": [], "phases": []})
    return e


LONG = O.road_long_session(120, "base", 150, 50.0, aet_measured=True)


def _sessions():
    return [g("long", "long", "LSD（路跑）", 120, "2026-10-04", target=HP.easy_cap_hr(150, True),
              detail=LONG["detail"]) | {"source": LONG["source"]},
            g("mine", "easy", "輕鬆跑", 40, "2026-10-02", detail="早上跟朋友跑河濱")]


def test_calendar_shows_target_detail_source_with_the_stored_one(monkeypatch):
    with _env(monkeypatch, _sessions()) as e:
        zh = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04&lang=zh-TW").json()
        en = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04&lang=en").json()
    long_zh = next(s for s in zh["sessions"] if s["day"] == "2026-10-04")
    long_en = next(s for s in en["sessions"] if s["uid"] == long_zh["uid"])
    for k in FIELDS:
        assert long_en[k] and not CJK.search(long_en[k]), (k, long_en[k])
        assert f"{k}_src" not in long_zh
        assert long_en.get(f"{k}_src", long_en[k]) == long_zh[k]          # (a source in English as stored: none)
    assert long_en["detail_src"] == long_zh["detail"] and long_en["target_src"] == long_zh["target"]
    mine = next(s for s in en["sessions"] if s["day"] == "2026-10-02")
    assert mine["detail"] == "早上跟朋友跑河濱" and "detail_src" not in mine


def test_a_shown_target_and_detail_sent_back_are_stored_in_zh_tw(monkeypatch):
    from backend.engine import plan_store as PS
    with _env(monkeypatch, _sessions()) as e:
        en = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04&lang=en").json()
        s = next(x for x in en["sessions"] if x["day"] == "2026-10-04")
        r = e.c.patch(f"{API}/sessions/{s['uid']}?lang=en",
                      json={"title": s["title"], "target": s["target"], "detail": s["detail"], "minutes": 110})
        assert r.status_code == 200 and not CJK.search(r.json()["detail"])
        stored = {x["uid"]: x for x in run(PS.load(e.db))}[s["uid"]]
    assert stored["target"] == HP.easy_cap_hr(150, True) and stored["detail"] == LONG["detail"]
    assert stored["minutes"] == 110


def test_test_templates_in_english_made_in_zh_tw(monkeypatch):
    with _env(monkeypatch, _sessions()) as e:
        en = e.c.get(f"{API}/test-templates?lang=en").json()
        zh = e.c.get(f"{API}/test-templates?lang=zh-TW").json()
    for kind in ("cp", "aet"):
        for row_en, row_zh in zip(en[kind], zh[kind]):
            for k in ("detail", "source", "tip", "target"):
                if row_zh.get(k):
                    assert not CJK.search(row_en[k]), (kind, k, row_en[k])
            if row_zh.get("detail"):
                assert row_en["detail_src"] == row_zh["detail"]
    # zh-TW: made in the source language whatever the request (the dialog stores them)
    with use_locale("en"):
        made = plan_sessions._test_templates(TH, None)
    assert made["cp"][0]["detail"] == zh["cp"][0]["detail"]
