"""SP-377: 「同意，套用並推送」 clicked twice — the second click (sent while the first still pushes
to the watch) must not fail with 「找不到待確認的提案」 nor apply the proposal a second time."""
import asyncio

import pytest

from backend.db.models import PlanChangeLog
from backend.engine import plan_auto as PA
from backend.tests.test_coros_workouts import make_db, run
from backend.tests.test_plan_auto import Box, Push, _log, _pin, base_inputs  # noqa: F401 (_pin: autouse date pin)


async def _held(b):
    db = await make_db()
    await PA.run(db)
    b.inp = base_inputs(new=1, easy_minutes=200)          # a big change: held for the user
    assert (await PA.run(db))["status"] == "pending"
    return db, await PA.pending(db)


def test_approving_again_after_it_applied_says_already_approved(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db, p = await _held(b)
        assert (await PA.approve(db, p.id))["status"] == "applied"
        n_push, n_log = len(b.push.calls), len(await _log(db))
        r = await PA.approve(db, p.id)
        assert r == {"status": "already_approved", "id": p.id}
        assert len(b.push.calls) == n_push and len(await _log(db)) == n_log      # nothing again
    run(go())


def test_two_approves_at_once_apply_it_once(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db, p = await _held(b)
        n_log = len(await _log(db))
        r1, r2 = await asyncio.gather(PA.approve(db, p.id), PA.approve(db, p.id))
        assert sorted([r1["status"], r2["status"]]) == ["already_approved", "applied"]
        log = await _log(db)
        assert len([e for e in log[n_log:] if e.status == "applied"]) == 1
        assert (await db.get(PlanChangeLog, p.id)).status == "approved"
    run(go())


def test_a_rejected_or_unknown_proposal_still_fails(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db, p = await _held(b)
        await PA.reject(db, p.id)
        with pytest.raises(ValueError, match="找不到待確認的提案"):
            await PA.approve(db, p.id)
        with pytest.raises(ValueError, match="找不到待確認的提案"):
            await PA.approve(db, 99999)
    run(go())


def test_the_page_locks_the_buttons_while_a_decision_runs():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "autoplan.js").read_text("utf-8")
    assert "if (busy) return;" in js and 'b.disabled = true' in js
    assert 'T("common.autoplan.approving")' in js and "already_approved" in js
    import json
    for loc in ("zh-TW", "en"):
        cat = json.loads((Path(__file__).resolve().parents[1] / "static" / "i18n" / loc / "common.json").read_text("utf-8"))
        for k in ("approving", "rejecting", "undoing", "already_approved"):
            assert cat[f"autoplan.{k}"]
