"""SP-906: the structured editor's server texts (POST /steps/derive, /steps/check) in English.

Display texts are made in the request's language (engine/workout_steps `_()`); the 目標 summary the
dialog writes into a session comes with `summary_src` (zh-TW, what is stored); a session saved from an
English page keeps its zh-TW 目標. The step names are the watch's (SP-908) and stay as stored.
Synthetic data only."""
from __future__ import annotations

from datetime import date

import pytest

from backend import request_context as RC
from backend.api import plan_sessions
from backend.engine import plan_store as PS
from backend.scripts.i18n_extract import CJK
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env, run


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(plan_sessions, "_tpace", lambda: 280.0)
    monkeypatch.setattr(plan_sessions, "_speeds", lambda: {"v_easy": 10.0, "v_easy_src": "t", "ep_kmh": 7.0})


def _env(monkeypatch):
    e = Env(monkeypatch)
    e.c.app.add_middleware(RC.RequestContextMiddleware)
    return e


def _quality(e):
    return next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["kind"] == "quality")


def _strings(x, skip=("name", "note", "group", "steps", "items", "policy", "summary_src")):
    """Every string of a response but the watch's own names and the stored copies."""
    if isinstance(x, dict):
        return [s for k, v in x.items() if k not in skip for s in _strings(v, skip)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v, skip)]
    return [x] if isinstance(x, str) else []


RPE_STEP = {"id": "r1", "kind": "work", "dur": {"type": "time", "value": 1200},
            "target": {"type": "rpe", "lo": 6, "hi": 7, "up": 300}}
LOAD_STEP = {"id": "l1", "kind": "work", "dur": {"type": "load", "value": 40}, "target": {"type": "power", "mode": "pct", "lo": 0.9, "hi": 0.95}}


def test_derive_and_check_in_english(monkeypatch):
    with _env(monkeypatch) as e:
        q = _quality(e)
        d = e.c.post(f"{API}/steps/derive?lang=en", json={"uid": q["uid"]}).json()
        ctx = d["context"]
        assert ctx["basis_label"].startswith("Targets by: ") and not CJK.search(ctx["basis_label"])
        assert ctx["kinds"]["warm"] == "Warm-up" and ctx["types"]["hr"] == "Heart rate"
        assert not [s for s in _strings(ctx["zones"]) if CJK.search(s)]
        steps = d["steps"]
        steps["items"] += [RPE_STEP, LOAD_STEP]
        en = e.c.post(f"{API}/steps/check?lang=en", json={"uid": q["uid"], "steps": steps}).json()
        zh = e.c.post(f"{API}/steps/check?lang=zh-TW", json={"uid": q["uid"], "steps": steps}).json()
    left = [s for s in _strings(en) if CJK.search(s)]
    assert not left, left
    assert CJK.search(zh["structure"]) and zh["structure"].startswith("暖身")
    assert en["structure"].startswith("Warm-up") and "min" in en["structure"]
    assert en["summary_src"] == zh["summary"] and "summary_src" not in zh
    assert [x["kind"] for x in en["watch"]["lines"] if "kind" in x][0] == "Warm-up"


def test_a_summary_saved_from_an_english_page_is_stored_in_zh_tw(monkeypatch):
    with _env(monkeypatch) as e:
        q = _quality(e)
        steps = e.c.post(f"{API}/steps/derive?lang=en", json={"uid": q["uid"]}).json()["steps"]
        r = e.c.patch(f"{API}/sessions/{q['uid']}?lang=en", json={"steps": steps})
        assert r.status_code == 200, r.text
        stored = {s["uid"]: s for s in run(PS.load(e.db))}[q["uid"]]
    assert CJK.search(stored["target"]) and stored["target"].startswith(("功率", "心率"))


def test_the_easy_cap_is_a_flag_not_a_word(monkeypatch):
    with _env(monkeypatch) as e:
        q = _quality(e)
        steps = e.c.post(f"{API}/steps/derive", json={"uid": q["uid"]}).json()["steps"]
        steps["items"].append({"id": "c9", "kind": "cool", "dur": {"type": "time", "value": 600},
                               "target": {"type": "hr", "mode": "zone", "zone": "aet"}})
        en = e.c.post(f"{API}/steps/check?lang=en", json={"uid": q["uid"], "steps": steps}).json()
    r = en["resolved"]["c9"]
    assert r["ecap"] is True and r["sub"] == "≤ easy-run cap"


def test_derive_step_names_stay_as_the_watch_gets_them(monkeypatch):
    # SP-908 (open): the watch's step names are not translated here
    with _env(monkeypatch) as e:
        q = _quality(e)
        en = e.c.post(f"{API}/steps/derive?lang=en", json={"uid": q["uid"]}).json()["steps"]
        zh = e.c.post(f"{API}/steps/derive?lang=zh-TW", json={"uid": q["uid"]}).json()["steps"]
    assert en == zh
