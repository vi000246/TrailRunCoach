"""
The editor's API (api/plan_sessions.py /steps/*, coros-preview, PATCH steps) on a
fake DB and the test plan inputs; COROS is never called.
"""
from datetime import date

import pytest

from backend.api import plan_sessions
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(plan_sessions, "_tpace", lambda: 280.0)


def _quality(e):
    return next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["kind"] == "quality")


def test_derive_check_save_and_preview(monkeypatch):
    with Env(monkeypatch) as e:
        q = _quality(e)
        r = e.c.post(f"{API}/steps/derive", json={"uid": q["uid"]})
        assert r.status_code == 200
        b = r.json()
        assert b["derived"] is True and b["steps"]["items"]
        ctx = b["context"]
        assert ctx["thresholds"]["tpace"] == 280.0 and ctx["zones"]["hr"][0]["id"] == "aet"
        assert ctx["basis_label"].startswith("目標用：")
        # nothing stored by opening it
        assert next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["uid"] == q["uid"])["steps"] is None
        steps = b["steps"]
        chk = e.c.post(f"{API}/steps/check", json={"uid": q["uid"], "steps": steps}).json()
        assert chk["order"] and chk["totals"]["sec"] > 0 and chk["watch"]["lines"]
        assert {l["key"] for l in chk["watch"]["limits"]} == {"watts", "one", "ramp"}
        # 目標用 心率 turns the auto work steps into HR
        hr = e.c.post(f"{API}/steps/check", json={"uid": q["uid"], "steps": steps, "target_basis": "hr"}).json()
        work_ids = [o["id"] for o in hr["order"] if o["kind"] == "work"]
        assert all(hr["resolved"][i]["type"] == "hr" for i in work_ids)
        # override one step, save: a user edit with minutes / target from the structure
        first = steps["items"][0]
        first["target"] = {"type": "power", "mode": "pct", "lo": 0.7, "hi": 0.75}
        r = e.c.patch(f"{API}/sessions/{q['uid']}", json={"steps": steps})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["edited"] and s["steps"]["origin"] == "user" and s["minutes"] > 0
        assert s["steps"]["items"][0]["target"]["type"] == "power"
        again = e.c.post(f"{API}/steps/derive", json={"uid": q["uid"]}).json()
        assert again["derived"] is False and again["steps"] == s["steps"]
        pv = e.c.get(f"{API}/sessions/{q['uid']}/coros-preview").json()
        assert pv["pushed"] and pv["derived"] is False and pv["lines"][0]["target"].startswith("功率")


def test_patch_refuses_errors_unless_forced(monkeypatch):
    with Env(monkeypatch) as e:
        q = _quality(e)
        bad = {"items": [{"kind": "repeat", "times": 8, "items": [
            {"kind": "work", "dur": {"type": "time", "value": 60},
             "target": {"type": "power", "mode": "pct", "lo": 1.1, "hi": 1.2}},
            {"kind": "rest", "dur": {"type": "time", "value": 60}, "target": {"type": "none"}}]}]}
        r = e.c.patch(f"{API}/sessions/{q['uid']}", json={"steps": bad})
        assert r.status_code == 422 and any("2 分鐘" in x for x in r.json()["detail"]["errors"])
        r = e.c.patch(f"{API}/sessions/{q['uid']}", json={"steps": bad, "steps_force": True})
        assert r.status_code == 200 and r.json()["minutes"] == 16
        r = e.c.post(f"{API}/steps/check", json={"steps": {"items": [{"kind": "x"}]}})
        assert r.status_code == 400 and r.json()["detail"]["errors"]


def test_templates_and_new_session_with_steps(monkeypatch):
    with Env(monkeypatch) as e:
        gs = e.c.get(f"{API}/steps/templates").json()["groups"]
        row = next(r for g in gs for r in g["rows"] if r["key"] == "t2a")
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "easy", "title": "自己排",
                                              "steps": {"items": row["full"]}})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["steps"] and s["origin"] == "custom" and s["minutes"] == 45      # warm-up 12 + 3×8′ with 2×2′ rests + cool-down 5
        assert s["target"].startswith("心率")              # an easy-kind session: 自動 = HR
        d = e.c.post(f"{API}/steps/derive", json={"kind": "easy", "minutes": 40, "title": "輕鬆跑"}).json()
        assert d["derived"] and d["steps"]["items"][0]["kind"] == "work"
        none = e.c.post(f"{API}/steps/derive", json={"kind": "strength", "minutes": 30}).json()
        assert none["steps"] is None and none["reason"]
