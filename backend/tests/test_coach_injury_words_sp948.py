"""SP-948: with 「看傷病紀錄」 off (SP-822, the default), a coach viewing a student reads the plan's injury texts
without what the injury is — the body part, the injury type, the pain score and the illness type become 「傷病」,
the reason stays (「傷病進行中：先不跑下坡…」「傷病：心率 1 區…」); the box's injury rows aren't the coach's;
weekplan's injury_light and reentry's injury / illness are not sent. The plan is computed as before; the student,
and a coach with the switch on, read the original. English too (viewing.redact, the gate's redact_body).

Every person is a made-up test account (accounts_fixtures.many, SP-611) in sign-in mode (test_login_sp58's
app); the student's injury log is written through the API into their own test tenant (a right knee with
膝前痛, a cold); the pain marks and the box's rows are made up (no activities, no real data)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.tests.test_coach_injury_sp822 import INJ, SEE, _switch
from backend.tests.test_coach_view_sp791 import _pair, _view
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, started  # noqa: F401 — fixtures

WEEK = "/api/v1/overview/weekplan"
SESS = "/api/v1/overview/plan/sessions"
CAL = "/api/v1/overview/plan/calendar"
SUG = "/api/v1/overview/plan/suggestions"
AUTO = "/api/v1/overview/plan/auto"
FEAS = "/api/v1/overview/feasibility"
Z5 = "/api/v1/overview/z5"
# what names the injury, in each language (the student's: right knee, 膝前痛, a cold; the light's scores)
SECRET = {"zh-TW": ("右膝", "膝前痛", "感冒", "Kaulback", "Esculier", "上一次 6/10", "的 2/10", "傷病紀錄 #"),
          "en": ("Knee (right)", "Front-of-knee", "Mild cold", "Kaulback", "Esculier", "last run 6/10", "2/10 limit",
                 "injury log #", "right knee")}
TODAY = dt.date.today()


def _cal():
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    return f"{CAL}?start={mon.isoformat()}&end={(mon + dt.timedelta(days=27)).isoformat()}"


@pytest.fixture
def student(app, monkeypatch):
    """A coach and their student (switches as accepted: 「看傷病紀錄」 off) whose injury log has an open right
    knee with 膝前痛 (pause the intervals) and a cold; the knee's last two runs marked 痠 2/10 then 痛 6/10 (the
    light: yellow). The engine reads the tenant's own log (activity_tags' DB, patched off for tests); the plan
    inputs' caches start empty (the test accounts' tenant ids repeat from test to test: no plan of another)."""
    from backend.api import plan_sessions as PSA
    from backend.db.database import db_path
    from backend.engine import activity_tags as AT
    from backend.engine import injuries as I
    monkeypatch.setattr(AT, "_default_db", lambda: Path(db_path()))
    PSA._cache.clear()
    PSA._last.clear()
    coach, stu, link = _pair(app)
    r = stu.post(INJ, json={"area": "knee", "side": "right", "severity": "moderate", "condition": "pfp",
                            "pause_quality": True, "onset_date": (TODAY - dt.timedelta(days=6)).isoformat()},
                 headers=stu.tok)
    assert r.status_code == 200, r.text
    knee = r.json()["id"]
    r = stu.post(INJ, json={"category": "illness", "illness": "cold",
                            "onset_date": (TODAY - dt.timedelta(days=1)).isoformat()}, headers=stu.tok)
    assert r.status_code == 200, r.text

    def marks(ds, rows=None, since=None):
        d1, d2 = (TODAY - dt.timedelta(days=4)).isoformat(), (TODAY - dt.timedelta(days=2)).isoformat()
        return [{"date": d1, "key": d1 + "T07:00", "cat": "run", "pain": 1, "score": 2, "injury_id": knee,
                 "area": "knee", "minutes": 30.0},
                {"date": d2, "key": d2 + "T07:00", "cat": "run", "pain": 2, "score": 6, "injury_id": knee,
                 "area": "knee", "minutes": 30.0}]
    monkeypatch.setattr(I, "foot_log", marks)
    return coach, stu, link


def _get(cl, path, lang):
    r = cl.get(path + ("&" if "?" in path else "?") + f"lang={lang}")
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


def _text(doc) -> str:
    return json.dumps(doc, ensure_ascii=False)


def _leaks(doc, lang) -> list:
    t = _text(doc)
    return [s for s in SECRET[lang] if s in t]


def _plan(doc):
    """What the plan is (whoever reads it): the sessions' days, kinds and minutes."""
    return sorted((s.get("day") or "", s.get("kind") or "", s.get("minutes") or 0) for s in doc.get("sessions") or ())


# ---------------------------------------------------------------------------- the acceptance criteria

@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_the_week_plan_says_why_not_what(student, lang):
    """驗收 1, 5, 6: 總覽's week (notes, why, the base-climb / technical cards' why): the coach reads 「傷病」 and
    the reason, never the knee, 膝前痛, the cold or the scores; no injury_light, reentry without its injury;
    the student reads the original; the plan itself is the same."""
    coach, stu, _link = student
    mine = _get(stu, WEEK, lang)
    assert len(_leaks(mine, lang)) >= 5, _leaks(mine, lang)          # the student: all of it
    assert mine.get("injury_light")
    _view(coach, stu.user.id)
    theirs = _get(coach, WEEK, lang)
    assert _leaks(theirs, lang) == []
    assert "injury_light" not in theirs
    assert (theirs.get("reentry") or {}).get("injury") is None
    notes = [n["text"] for n in theirs["notes"]]
    if lang == "zh-TW":
        assert "傷病進行中：先不跑下坡；技術地形、長爬坡也先不排，越野長跑改平路" in notes, notes
        assert any(t.startswith("傷病進行中（第 7 天") for t in notes), notes
        assert any(t.startswith("疼痛黃燈（傷病）：上一次的疼痛分數超過上限。本週剩下的課不加量") for t in notes), notes
        assert any(t.startswith("傷病有症狀：只排心率 1 區的恢復跑") for t in notes), notes
    else:
        assert any(t.startswith("Injury ongoing: no running downhill for now; no technical-terrain") for t in notes), notes
        assert any(t.startswith("Injury ongoing (day 7") for t in notes), notes
        assert any("(Injury): last run's pain score above the limit" in t for t in notes), notes
    assert _plan(theirs) == _plan(mine)


@pytest.mark.parametrize("path", [SESS, "calendar"])
@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_the_schedule_says_why_not_what(student, path, lang):
    """驗收 2, 5, 6: 課表 (the stored sessions' titles and texts, the weeks' why) — 「傷病：心率 1 區…」 for the
    coach, the same days and minutes as the student's."""
    coach, stu, _link = student
    path = _cal() if path == "calendar" else path
    mine = _get(stu, path, lang)
    assert _leaks(mine, lang), "the student's schedule names the injury"
    _view(coach, stu.user.id)
    theirs = _get(coach, path, lang)
    assert _leaks(theirs, lang) == []
    t = _text(theirs)
    assert ("傷病：心率 1 區" in t) or ("Injury: heart-rate zone 1" in t), t[:2000]
    assert _plan(theirs) == _plan(mine)


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_the_switch_on_shows_the_original(student, lang):
    """驗收 5: the student turns 「看傷病紀錄」 on — the coach reads what the student reads."""
    coach, stu, link = student
    _switch(stu, link["id"], True)
    _view(coach, stu.user.id)
    for path in (WEEK, SESS, Z5):
        mine, theirs = _get(stu, path, lang), _get(coach, path, lang)
        assert _leaks(theirs, lang) == _leaks(mine, lang), path
    assert _get(coach, WEEK, lang).get("injury_light")


def test_the_status_gate_card_says_why_not_what(student):
    """驗收 1: the 5 區 card and the week's status gate (「本週沒排 3 區：傷病紀錄 #1（右膝）進行中：先不排間歇」)."""
    coach, stu, _link = student
    assert "右膝" in _text(_get(stu, Z5, "zh-TW")) + _text(_get(stu, WEEK, "zh-TW")["quality_gate"])
    _view(coach, stu.user.id)
    for doc in (_get(coach, Z5, "zh-TW"), _get(coach, WEEK, "zh-TW")["quality_gate"]):
        assert _leaks(doc, "zh-TW") == []
    assert "傷病進行中：先不排間歇" in _text(_get(coach, WEEK, "zh-TW")["quality_gate"])


@pytest.fixture
def box(monkeypatch):
    """The box offers the four injury rows and a test row (made up; whoever asks: the student's data)."""
    from backend.api import plan_sessions as PSA

    async def rows(db, inp):
        mk = lambda t, title: {"id": f"{t}:1", "type": t, "date": TODAY.isoformat(), "title": title}  # noqa: E731
        return [mk("injury_rest", "右膝：這幾天不排課？"), mk("injury_hold", "10/20 記了「痛」・膝"),
                mk("injury_done", "右膝看起來好了？"), mk("injury_pattern", "跟受傷前很像：右膝"),
                mk("test_cp", "排一次 CP 測試")]
    monkeypatch.setattr(PSA, "_all_suggestions", rows)


def test_the_box_has_no_injury_rows_for_the_coach(student, box):
    """驗收 3: the coach's box has no injury_rest / injury_hold / injury_done / injury_pattern (the count too);
    with the switch on, or the student, all of them."""
    coach, stu, link = student
    kinds = lambda cl: sorted(r["type"] for r in cl.get(SUG).json()["suggestions"])  # noqa: E731
    assert kinds(stu) == ["injury_done", "injury_hold", "injury_pattern", "injury_rest", "test_cp"]
    _view(coach, stu.user.id)
    doc = coach.get(SUG).json()
    assert [r["type"] for r in doc["suggestions"]] == ["test_cp"] and doc["count"] == 1
    assert "右膝" not in _text(doc)
    _switch(stu, link["id"], True)
    assert kinds(coach) == kinds(stu)


def test_the_auto_log_says_why_not_what(student, monkeypatch):
    """驗收 2: 自動調整紀錄 — the stored lines (a re-entry 「傷停 12 天（右膝，傷病紀錄 #1）」, a light with its
    scores) read 「傷停 12 天」 and 「上一次的疼痛分數比再前一次高」 to the coach."""
    from backend.engine import plan_auto as PA
    from backend.engine import reentry as RE
    coach, stu, _link = student
    ret, end = TODAY, TODAY + dt.timedelta(days=12)
    re_text = RE.text_of(12, "6-13", ret, end, injury={"id": 1, "label": "右膝"})
    lines = [f"恢復期：{re_text}（從活動資料偵測）", "疼痛黃燈（右膝）：上一次 6/10，比再前一次（4/10）高 2 分"]

    async def entries(db, limit=10):
        return [{"id": i + 1, "created_at": TODAY.isoformat(), "trigger": "sync", "status": "applied",
                 "summary": s, "items": []} for i, s in enumerate(lines)]
    monkeypatch.setattr(PA, "entries", entries)
    assert [e["summary"] for e in _get(stu, AUTO, "zh-TW")["log"]] == lines
    _view(coach, stu.user.id)
    got = [e["summary"] for e in _get(coach, AUTO, "zh-TW")["log"]]
    assert got[0].startswith("恢復期：傷停 12 天：") and "右膝" not in got[0] and "#1" not in got[0], got
    assert got[1] == "疼痛黃燈（傷病）：上一次的疼痛分數比再前一次高", got
    en = [e["summary"] for e in _get(coach, AUTO, "en")["log"]]
    assert _leaks(en, "en") == [] and _leaks(en, "zh-TW") == [], en


def test_the_feasibility_and_charts_cards_say_why_not_what(student, monkeypatch):
    """驗收 4: 賽季計畫's 百岳 multi-day difficulty 「傷停（右膝）」 and the charts' 賽前完備 「進行中：右膝」 — the
    texts as engine/phase_checks, race_readiness and baiyue_multiday write them, through the gate (feasibility)
    and redact (the charts' answers go through the same gate: REDACT_READS)."""
    from backend.accounts import viewing as VW
    from backend.engine import race_feasibility as RF
    from backend.engine import injuries as I
    from backend.engine import phase_checks as PC
    from backend.i18n import use_locale
    coach, stu, _link = student
    knee = {"id": 1, "area": "knee", "side": "right", "condition": "pfp", "severity": "moderate",
            "onset_date": (TODAY - dt.timedelta(days=6)).isoformat(), "status": "active"}
    texts = {}
    for loc in ("zh-TW", "en"):
        with use_locale(loc):
            texts[loc] = [PC._injury([knee], TODAY)[1], I.full_label("knee", "right")]
    texts["en"].append("right knee")                          # baiyue_multiday's English label (the keys)

    def races(plan, ds, today, event_id=None):
        return [{"event_id": "e1", "name": "百岳", "interruptions": [{"kind": "injury", "label": t} for t in texts["zh-TW"]]}]
    monkeypatch.setattr(RF, "races", races)
    assert "右膝" in _text(stu.get(FEAS).json())
    _view(coach, stu.user.id)
    doc = coach.get(FEAS).json()
    assert [x["label"] for x in doc["races"][0]["interruptions"]] == ["進行中：傷病", "傷病"]
    assert VW.redact(texts["en"], events=[knee]) == ["Open: Injury", "Injury", "injury"]
    for p in ("/api/v1/app/views/x/dashboards/0/charts/1", "/api/v1/app/races/readiness/x", FEAS, WEEK, SESS):
        assert VW.redacts("GET", p, {}) and not VW.redacts("GET", p, SEE) and not VW.redacts("POST", p, {})


def test_texts_without_the_injury_are_untouched():
    """Nothing else changes: a strength session's 「膝主導」, a date 「12/10」, a text with no injury word."""
    from backend.accounts import viewing as VW
    knee = {"id": 1, "area": "knee", "side": None}
    same = ["膝主導＋臀中肌；安排在輕鬆日或跑完後", "TRC 長爬坡有氧 40′ 12/10", "Knee-dominant + glute med",
            "冷啟動（cold start）", "f()；"]
    assert VW.redact(same, events=[knee]) == same
    assert VW.redact(["膝進行中（第 2 天，輕：照練）"], events=[knee]) == ["傷病進行中（第 2 天，輕：照練）"]
