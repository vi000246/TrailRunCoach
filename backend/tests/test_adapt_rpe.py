"""
SP-231: engine/adapt.py rule D's self-rating trigger — an easy / long run rated Hard or more
after the run pushes the next hard session < 48 h later back (else one step down); plus the
plan_auto side: the A-race confirm, the change log's reason and 復原. Synthetic plans, no
COROS (push_sessions replaced), the DB in memory.
"""
import copy

import pytest

from backend.api import plan_sessions as API
from backend.db.models import PlanChangeLog
from backend.engine import adapt as A
from backend.engine import coros_rpe as CR
from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.engine import quality_gate as QG
from backend.tests.test_adapt import ctx, g, ids, st, week
from backend.tests.test_coros_workouts import make_db, run

# Mon 09-28 … Sun 10-04; the easy run on Wed 09-30, the quality Thu 10-01 (< 48 h)
EASY = {"index": 7, "date": "2026-09-30", "category": "road", "tss": 36}


def rated(feel=None, rpe=None, source="coros"):
    if feel is not None:
        return {7: {"rpe": CR.TO_RPE.get(feel), "source": source, "coros_feel": feel}}
    return {7: {"rpe": rpe, "source": "watch", "coros_feel": None}}


def wk(long_day="2026-10-04", easy_day="2026-10-03", quality_title="閾值 3×10 分"):
    return week([g("easy0", "easy", "2026-09-30", 45, tss=36, done=True, done_by=EASY),
                 g("quality", "quality", "2026-10-01", 60, tss=70, title=quality_title),
                 g("easy1", "easy", easy_day, 40, tss=32),
                 g("long", "long", long_day, 120, tss=100)])


def c(**kw):
    return ctx(today="2026-10-01", first_free="2026-10-01", **kw)


def rpe_adj(adj):
    return [a for a in adj if a["rule"] == A.RPE_RULE]


def test_not_filled_does_nothing():
    # COROS 0 = not filled: no RPE, so not in ctx["rpe"] at all (api/plan_sessions._rated)
    out, adj, _ = A.adapt(wk(), [], c(rpe={}))
    assert ids(out)["quality"]["day"] == "2026-10-01" and adj == []
    from datetime import datetime
    from types import SimpleNamespace
    from backend.engine import activity_tags as AT
    t = datetime(2026, 9, 30, 6, 30)
    w = SimpleNamespace(entry=SimpleNamespace(start=t, file="/f/R0.fit"))
    rows = [{"start_local": AT.key_of(t), "file": "R0.fit", "rpe": None, "feel": 75, "coros_feel": 0, "source": None}]
    assert API._rated(rows, w) is None
    rows[0].update(rpe=7.0, coros_feel=4, source="coros")
    assert API._rated(rows, w) == {"rpe": 7.0, "source": "coros", "coros_feel": 4}


def test_rating_3_does_nothing():
    out, adj, _ = A.adapt(wk(), [], c(rpe=rated(3)))
    assert ids(out)["quality"]["day"] == "2026-10-01" and rpe_adj(adj) == []


def test_rating_4_moves_to_the_free_day_after_48h():
    # Fri 10-02 is free: 2 days after Wed's run and 2 days before Sun's long run
    out, adj, _ = A.adapt(wk(), [], c(rpe=rated(4)))
    s = ids(out)
    assert s["quality"]["day"] == "2026-10-02" and s["quality"]["title"] == "閾值 3×10 分"
    a = rpe_adj(adj)
    assert len(a) == 1 and a[0]["action"] == "moved" and a[0]["before_day"] == "2026-10-01"
    assert a[0]["reason"] == "週四 閾值 3×10 分延到週五：週三輕鬆跑自評 Hard，間隔不到 48 小時"
    assert "推估" in a[0]["src"]
    # nothing else: the done run, the long run and the easy runs are untouched
    assert s["easy0"]["done"] and s["long"]["minutes"] == 120 and s["easy1"]["minutes"] == 40


def test_rating_4_without_free_day_steps_down_one_level():
    # Fri taken by the easy run, Sat next to Sun's long run: no day ≥ 48 h from both
    title = QG.DOSE[1][1]
    out, adj, _ = A.adapt(wk(easy_day="2026-10-02", quality_title=title), [], c(rpe=rated(4)))
    q = ids(out)["quality"]
    # the same step-down as rule D step 2: the ladder one rung down, same day
    assert q["day"] == "2026-10-01" and q["kind"] == "quality" and q["title"] == QG.DOSE[0][1]
    a = rpe_adj(adj)
    assert len(a) == 1 and a[0]["action"] == "downgraded" and a[0]["before_title"] == title
    assert a[0]["reason"] == f"週四 {title}改成{QG.DOSE[0][1]}：週三輕鬆跑自評 Hard，本週沒有隔 48 小時的空日"


def test_already_removed_by_rule_e_is_left_alone():
    # a CTL ramp at the block line: rule E removes the quality; the rating changes nothing
    out, adj, _ = A.adapt(wk(), [], c(rpe=rated(5), load={"ramp": 12.0}))
    assert "quality" not in ids(out)
    assert rpe_adj(adj) == [] and any(a["rule"] == "fatigue" and a["action"] == "removed" for a in adj)


def test_already_stepped_down_by_rule_e_is_not_stepped_again():
    red = [st(f"e{i}", "easy", d, state="done", minutes=45,
              done_by={"index": 90 + i, "date": d, "category": "road", "moving_s": 900, "tss": 10})
           for i, d in enumerate(("2026-09-28", "2026-09-29"))]
    out, adj, _ = A.adapt(wk(easy_day="2026-10-02"), red, c(rpe=rated(4)))
    assert ids(out)["quality"]["title"] == QG.RECOVERY[1] and rpe_adj(adj) == []


def test_user_edited_or_added_sessions_never_move():
    stored = [st("quality", "quality", "2026-10-01", edited=True, minutes=60)]
    out, adj, _ = A.adapt(wk(), stored, c(rpe=rated(5)))
    assert rpe_adj(adj) == []


def test_the_switch_turns_it_off():
    out, adj, _ = A.adapt(wk(), [], c(rpe=rated(5), rpe_rule=False))
    assert ids(out)["quality"]["day"] == "2026-10-01" and adj == []


def test_a_rated_long_run_and_a_fit_rpe_count_too():
    gw = week([g("long", "long", "2026-09-30", 120, tss=100, done=True, done_by=EASY),
               g("quality", "quality", "2026-10-01", 60, tss=70), g("easy1", "easy", "2026-10-03", 40)])
    out, adj, _ = A.adapt(gw, [], c(rpe=rated(rpe=8.0)))
    a = rpe_adj(adj)
    assert a and a[0]["action"] == "moved" and "週三長跑自評 RPE 8" in a[0]["reason"]
    assert ids(out)["quality"]["day"] == "2026-10-02"
    _, adj7, _ = A.adapt(gw, [], c(rpe=rated(rpe=6.5)))
    assert rpe_adj(adj7) == []                                 # FIT RPE < 7: below Hard


def test_hard_session_two_days_later_is_not_touched():
    gw = wk()
    gw[0]["sessions"][1]["day"] = "2026-10-02"                  # 48 h after Wed: fine as it is
    out, adj, _ = A.adapt(gw, [], c(rpe=rated(5)))
    assert ids(out)["quality"]["day"] == "2026-10-02" and rpe_adj(adj) == []


def test_idempotent_through_reconcile():
    gen = copy.deepcopy(wk()[0]["sessions"])
    inp = {"cur": {"week": {"start": "2026-09-28", "end": "2026-10-04", "today": "2026-10-01", "days_left": 4},
                   "mode": "base", "load": {}, "sessions": gen, "done": {"activities": [EASY]}},
           "weeks": [], "activities": [EASY], "today": "2026-10-01", "horizon_end": "2026-10-04",
           "thresholds": {"cp": 300.0, "lthr": 170.0, "aet": 150.0},
           "adapt": {"enabled": True, "reviews": {}, "first_free": "2026-10-01", "rpe": rated(4)}}
    adj: list = []
    new, ch = PS.reconcile_with_adapt([], inp, adjustments=adj)
    q = next(s for s in new if s.get("gen_key") == "quality" and s["state"] == "active")
    assert q["day"] == "2026-10-02" and rpe_adj(adj)
    again, ch2 = PS.reconcile_with_adapt(new, copy.deepcopy(inp))
    assert ch2 == []


# ---------------------------------------------------------------------------
# plan_auto: the log, 復原, the A-race confirm, the data stamp
# ---------------------------------------------------------------------------

class Push:
    def __init__(self):
        self.calls = []

    async def push(self, db, sessions, thresholds, today, *, stale_keys=(), missed_keys=(), **kw):
        self.calls.append([dict(s) for s in sessions])
        return {"sessions": [{"id": s["id"], "status": "pushed", "changed": True} for s in sessions], "removed": []}

    async def remove(self, db, keys, **kw):
        return []


@pytest.fixture
def box(monkeypatch):
    from datetime import date
    from backend.sync import coros_workouts as CW
    from backend.tests.test_plan_store import cur_plan, inputs, next_week
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    push = Push()
    monkeypatch.setattr(CW, "push_sessions", push.push)
    monkeypatch.setattr(CW, "remove_keys", push.remove)

    class B:
        def make(self, feel=None, days_to_race=None):
            ss = copy.deepcopy(wk()[0]["sessions"])
            ss[0]["title"] = "輕鬆跑"
            inp = inputs(today="2026-09-30", cur=cur_plan(today="2026-09-30", sessions=ss), weeks=[next_week()],
                         acts=[EASY], horizon="2026-10-11")
            inp["days_to_next_a"] = days_to_race
            r = rated(feel) if feel else {}
            inp["adapt"] = {"enabled": True, "reviews": {}, "first_free": "2026-10-01", "rpe": r}
            inp["rpe_stamp"] = CR.stamp(r)
            self.inp = inp
            return inp
    b = B()
    b.make()
    monkeypatch.setattr(API, "_compute_inputs", lambda *a: b.inp)
    return b


def _q(ss):
    return next(s for s in ss if s.get("gen_key") == "quality" and s["state"] == "active" and s["week_start"] == "2026-09-28")


def test_rating_read_later_runs_logs_the_reason_and_undoes(box):
    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        assert _q(await PS.load(db))["day"] == "2026-10-01"
        # the rating arrives on a later sync (no new activity): the stamp changes, the run acts
        box.make(feel=4)
        r = await PA.run(db)
        assert r["status"] == "applied"
        assert _q(await PS.load(db))["day"] == "2026-10-02"
        e = PA.entry_dict(await db.get(PlanChangeLog, r["id"]))
        it = next(i for i in e["items"] if i["rule"] == A.RPE_RULE)
        assert it["reason"] == "週四 閾值 3×10 分延到週五：週三輕鬆跑自評 Hard，間隔不到 48 小時"
        assert e["can_undo"]
        # 復原: back on Thursday, pinned as the user's own, not redone
        await PA.undo(db, r["id"])
        q = _q(await PS.load(db))
        assert q["day"] == "2026-10-01" and q["edited"] is True
        st = (await PA.settings(db))["state"]
        st.pop("stamp")
        await PA._set_state(db, st)
        await PA.run(db)
        assert _q(await PS.load(db))["day"] == "2026-10-01"
    run(go())


def test_within_14_days_of_an_a_race_it_waits_for_approval(box):
    async def go():
        db = await make_db()
        box.make(days_to_race=10)
        await PA.run(db)
        box.make(feel=5, days_to_race=10)
        r = await PA.run(db)
        assert r["status"] == "pending"
        big = PA.entry_dict(await PA.pending(db))["big"]
        assert [x["rule"] for x in big] == ["rpe"] and "自評 Max Effort" in big[0]["text"]
        assert _q(await PS.load(db))["day"] == "2026-10-01"                 # held: the plan is unchanged
    run(go())


def test_far_from_the_race_it_applies_on_its_own(box):
    async def go():
        db = await make_db()
        box.make(days_to_race=40)
        await PA.run(db)
        box.make(feel=5, days_to_race=40)
        assert (await PA.run(db))["status"] == "applied"
    run(go())


def test_stamp_follows_the_rating():
    base = {"today": "2026-10-01", "activities": [EASY], "last_activity": "2026-09-30"}
    assert PA.stamp(base) == PA.stamp({**base, "rpe_stamp": ""})        # no rating: as before SP-231
    assert PA.stamp({**base, "rpe_stamp": CR.stamp(rated(4))}) != PA.stamp(base)
    assert CR.stamp(rated(4)) != CR.stamp(rated(3))


def test_settings_switch_round_trip(box):
    from backend.settings.repository import SettingsRepository

    async def go():
        db = await make_db()
        cfg = await PA.settings(db)
        assert cfg["rpe_rule"] is True
        await SettingsRepository(db).set("plan.auto.rpe_rule", False)
        await db.commit()
        assert (await PA.settings(db))["rpe_rule"] is False
        with pytest.raises(ValueError):
            await SettingsRepository(db).set("plan.auto.rpe_rule", "yes")
    run(go())
