"""SP-453: one session, one TSS — the card (the stored `tss`), the week's total (the plan summary, est_tss
over the stored rows) and the editor's 預估 TSS (the structure's totals, POST /steps/check) after a
right-click swap (LSD → 長爬坡有氧, SP-87); the calculator's 匯出至課表 (SP-43): the preview's TSS is the one
the 課表 session carries. Synthetic only."""
from __future__ import annotations

import pytest

from backend.api import plan_sessions as PSA
from backend.tests.test_race_calculator import _export_env  # noqa: F401
from backend.tests.test_race_calculator import client  # noqa: F401  (fixture)


def _check_tss(e, api: str, s: dict) -> float:
    """The editor's 預估 TSS of a stored session: its structure through POST /steps/check."""
    r = e.c.post(f"{api}/steps/check", json={"uid": s["uid"], "steps": s["steps"]})
    assert r.status_code == 200, r.text
    return float(r.json()["totals"]["tss"])


def test_a_swapped_long_run_has_one_tss(monkeypatch):
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        got = e.c.get(f"{API}/sessions").json()
        lng = next(s for s in got["sessions"] if s["kind"] == "long" and s["day"] == "2026-10-04")
        week0 = got["summary"]["tss"]
        s = e.c.patch(f"{API}/sessions/{lng['uid']}", json={"swap_to": "climb"}).json()
        editor = _check_tss(e, API, s)
        after = e.c.get(f"{API}/sessions").json()
        card = next(x for x in after["sessions"] if x["uid"] == s["uid"])
        assert s["tss"] == pytest.approx(editor, abs=0.05)               # the PATCH's answer
        assert card["tss"] == pytest.approx(editor, abs=0.05)            # the card (the calendar's row)
        assert PSA.est_tss(card, {}) == pytest.approx(editor, abs=0.05)  # what the week's total counts for it
        assert after["summary"]["tss"] - week0 == pytest.approx(card["tss"] - PSA.est_tss(lng, {}), abs=0.05)


def test_a_swap_takes_the_tss_from_the_structure_not_from_the_long_run(monkeypatch):
    """The long run's stored TSS is not carried over into the body (before SP-437 it won over the structure's:
    card 37 vs 對話框 42, SP-87 圖 5–6); _with_steps sets it from the new structure, open steps or not."""
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        lng = next(s for s in ss if s["kind"] == "long" and s["day"] == "2026-10-04")
        body = PSA._with_swap({"swap_to": "climb"}, e.inp, {**lng, "tss": 37.0})
        assert "tss" not in body and body["steps"] and body["minutes"] == lng["minutes"]


def test_the_calculator_export_carries_the_previews_tss(client, monkeypatch):   # noqa: F811
    from backend.engine import plan_store as PS
    db, run, body = _export_env(client, monkeypatch)
    url = "/api/v1/racepower/export/plan"
    pv = client.post(url, json=body).json()
    w = client.post(url, json={**body, "push": True}).json()
    s = run(PS.load(db))[0]
    assert pv["tss"] == w["tss"] == s["tss"] == pv["tss_info"]["tss"]
    assert PSA.est_tss(s, {}) == pytest.approx(pv["tss"])            # the week's total counts the same number


# ---- SP-453 Q1 → A: an export as exported keeps the calculator's TSS on the 課表 (labelled); a changed
# structure takes the structure's ------------------------------------------------------------------------


def test_the_export_marks_its_structure_and_the_calendar_says_whose_tss_it_is(client, monkeypatch):   # noqa: F811
    from backend.engine import plan_store as PS
    from backend.engine import workout_steps as WS
    db, run, body = _export_env(client, monkeypatch)
    w = client.post("/api/v1/racepower/export/plan", json={**body, "push": True}).json()
    s = run(PS.load(db))[0]
    assert s["steps"]["origin"] == WS.RACECALC_ORIGIN == "racecalc"     # kept through normalize and the store
    assert PS.calc_tss(s) and s["tss"] == w["tss"]
    row = PSA._decorate([s], [], {}, s["day"], s["day"])[0]
    assert row["tss_by"] == "calc" and row["tss_est"] == pytest.approx(w["tss"], abs=0.05)
    # the same export again is still unchanged (the origin mark is part of what was exported)
    again = client.post("/api/v1/racepower/export/plan", json={**body, "push": True}).json()
    assert again["action"] == "unchanged" and not again["previous"]["user_edited"]


def test_a_title_edit_keeps_the_calculators_tss_a_structure_edit_does_not(client, monkeypatch):   # noqa: F811
    from backend.engine import plan_store as PS
    db, run, body = _export_env(client, monkeypatch)
    client.post("/api/v1/racepower/export/plan", json={**body, "push": True})
    s = run(PS.load(db))[0]
    retitled = {**s, "title": s["title"] + "（改名）"}
    assert PS.user_edited(retitled) and PS.calc_tss(retitled)             # the structure is still the exported one
    # the 課表 editor saves a changed structure as origin "user" (_with_steps): the structure's TSS from then on
    restructured = {**s, "steps": {**s["steps"], "origin": "user", "items": s["steps"]["items"][:-1]}}
    assert not PS.calc_tss(restructured)
    assert "tss_by" not in PSA._decorate([restructured], [], {}, s["day"], s["day"])[0]


def test_an_export_from_before_the_mark_counts_while_unchanged():
    from backend.engine import plan_store as PS
    s = {"ext_key": "racecalc:e1", "tss": 120.0, "day": "2099-05-01", "kind": "race", "title": "賽事 x",
         "minutes": 300, "target": "", "detail": "d", "steps": {"v": 1, "origin": "user", "items": []}}
    s["ext_sig"] = PS.ext_signature(s)
    assert PS.calc_tss(s)
    assert not PS.calc_tss({**s, "detail": "changed"})                   # changed on the 課表 since: unknown → structure
    assert not PS.calc_tss({**s, "ext_key": None})                       # not an export
    assert not PS.calc_tss({**s, "tss": 0})
