"""SP-817 (owner 2026-10-20, SP-646 Q1-3: 「有結構就一律用結構算」): a custom session added without a stored
structure or TSS (POST /sessions with a title and minutes only) was priced minutes × the kind's TSS / h on
its card (the calendar's tss_est, the session dialog's 計畫訓練負荷, the week's total) while the structure
editor priced the structure it derives from the text — two numbers for one session (SP-646 圖 2c: 129 vs
164). Now est_tss prices such a session by its structure (session_structure.figures, the generator's own
rule, SP-437) whenever it has one; without one, minutes × the rate as before. A stored TSS still wins
(the generator's and the editor's rows carry their structure's), so nothing stored changes and nothing
already pushed is pushed again. Synthetic only."""
from __future__ import annotations

import pytest

from backend.api import plan_sessions as PSA
from backend.tests.test_plan_store import _pin_real_today  # noqa: F401  (autouse: the plan's today)
from backend.tests.test_plan_store import API, Env


def _editor_tss(e, s: dict) -> float:
    """The structure editor's 預估 TSS of a stored session: the structure it derives (POST /steps/derive),
    priced by POST /steps/check — what the editor shows."""
    d = e.c.post(f"{API}/steps/derive", json={"uid": s["uid"]}).json()
    assert d["steps"], d
    r = e.c.post(f"{API}/steps/check", json={"uid": s["uid"], "steps": d["steps"]})
    assert r.status_code == 200, r.text
    return float(r.json()["totals"]["tss"])


def _add(e, **body) -> dict:
    e.c.get(f"{API}/sessions")                                  # the plan initialized (its today)
    r = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", **body})
    assert r.status_code == 200, r.text
    s = r.json()
    assert not s.get("tss") and not s.get("steps")             # stored without a structure or a TSS
    return s


def _rates(e) -> dict:
    every = e.c.get(f"{API}/sessions").json()["sessions"]
    return PSA.plan_rates(e.inp, every)


@pytest.mark.parametrize("body", [
    {"kind": "quality", "title": "閾值 3×8 分", "minutes": 45},
    {"kind": "long", "title": "長跑＋馬拉松配速 40 分", "minutes": 110},
])
def test_a_custom_session_without_a_structure_is_priced_as_the_editor_prices_it(monkeypatch, body):
    with Env(monkeypatch) as e:
        s = _add(e, **body)
        rates = _rates(e)
        old = s["minutes"] / 60.0 * rates[s["kind"]]            # what the card said before
        editor = _editor_tss(e, s)
        assert editor != pytest.approx(old, abs=1.0)            # the two numbers of the bug
        assert PSA.est_tss(s, rates) == pytest.approx(editor, abs=0.5)


def test_the_weeks_total_counts_the_structures_tss(monkeypatch):
    with Env(monkeypatch) as e:
        week0 = e.c.get(f"{API}/sessions").json()["summary"]["tss"]
        s = _add(e, kind="quality", title="閾值 3×8 分", minutes=45)
        week1 = e.c.get(f"{API}/sessions").json()["summary"]["tss"]
        assert week1 - week0 == pytest.approx(_editor_tss(e, s), abs=0.5)


def test_the_activity_reviews_plan_card_reads_the_same_number(monkeypatch):
    # workout_review's 課表 card (session_est_tss, SP-370): the thresholds of the tenant's newest inputs
    from backend import tenancy
    with Env(monkeypatch) as e:
        s = _add(e, kind="quality", title="閾值 3×8 分", minutes=45)
        editor = _editor_tss(e, s)
        monkeypatch.setattr(PSA, "_last", {tenancy.current().id: (("k",), e.inp, 0.0)})
        assert PSA.session_est_tss(s, None, []) == pytest.approx(editor, abs=0.5)
        monkeypatch.setattr(PSA, "_last", {})
        monkeypatch.setattr(PSA, "inputs_key_now", lambda: ("nothing cached",))
        assert PSA.session_est_tss(s, None, []) == pytest.approx(45 / 60.0 * 70.0)   # none: as before


def test_without_a_structure_it_is_minutes_times_the_rate_as_before(monkeypatch):
    with Env(monkeypatch) as e:
        s = _add(e, kind="strength", title="肌力", minutes=40)          # no structure: not a run session
        rates = _rates(e)
        assert PSA.est_tss(s, rates) == pytest.approx(40 / 60.0 * rates["strength"])

def test_an_all_easy_structure_keeps_the_kinds_rate(monkeypatch):
    # SP-437's rule for an all-easy session: its time × the 課表's TSS / h of the kind (as before)
    with Env(monkeypatch) as e:
        s = _add(e, kind="easy", title="輕鬆跑", minutes=50)
        rates = _rates(e)
        assert PSA.est_tss(s, rates) == pytest.approx(50 / 60.0 * rates["easy"], abs=0.1)


def test_a_stored_tss_still_wins(monkeypatch):
    with Env(monkeypatch) as e:
        s = _add(e, kind="quality", title="閾值 3×8 分", minutes=45)
        rates = _rates(e)
        assert PSA.est_tss({**s, "tss": 33.0}, rates) == 33.0
        # a plain rates dict (no plan inputs: callers outside the calendar) keeps minutes × the rate
        assert PSA.est_tss(s, {"quality": 70.0}) == pytest.approx(45 / 60.0 * 70.0)
