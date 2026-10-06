"""
SP-301: rule D in two tiers (engine/adapt.py). 偏強 (avg power > 80 % CP or TSS > planned
+ 20 %) only labels the done run; 太強 (the session classifier puts the easy run in a hard
class: Zone 3 or harder) is handled as a hard session — the next hard session < 48 h later
moves / steps down, with the reason, undoable, A-race confirm. Coexists with SP-231's rule D′.
Synthetic plans, no COROS, the DB in memory.
"""
import copy

import pytest

from backend.db.models import PlanChangeLog
from backend.engine import adapt as A
from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.engine import workout_review as WR
from backend.tests.test_adapt import ctx, g, ids, st, week
from backend.tests.test_adapt_rpe import EASY, box, rated  # noqa: F401 — the fixture
from backend.tests.test_coros_workouts import make_db, run

# Mon 09-28 … Sun 10-04; the easy run on Wed 09-30, the quality Thu 10-01 (< 48 h)
Z3 = {7: {"session_type": "quality", "stimulus": "z3", "tss": 50}}


def wk(long_day="2026-10-04", easy_day="2026-10-03", quality_day="2026-10-01"):
    return week([g("easy0", "easy", "2026-09-30", 45, tss=36, done=True, done_by=EASY),
                 g("quality", "quality", quality_day, 60, tss=70, title="閾值 3×10 分"),
                 g("easy1", "easy", easy_day, 40, tss=32),
                 g("long", "long", long_day, 120, tss=100)])


def c(**kw):
    return ctx(today="2026-10-01", first_free="2026-10-01", **kw)


def changes(adj):
    return [a for a in adj if a["action"] != "note"]


def test_the_hard_types_are_the_classifiers():
    assert A.TOO_HARD_TYPES == WR.HARD_TYPES


def test_power_overhard_only_labels():
    out, adj, notes = A.adapt(wk(), [], c(reviews={7: {"avg_power": 250, "cp": 300, "tss": 30}}))
    assert out == wk()                                                  # every session as generated
    assert notes[7] == "輕鬆跑偏強（平均功率 250 W > 80% CP（240 W））：只標示，課表不變"
    assert changes(adj) == [] and adj[0]["reason"].startswith("週三輕鬆跑偏強")


def test_tss_overhard_only_labels_and_trims_nothing():
    out, adj, notes = A.adapt(wk(), [], c(reviews={7: {"tss": 60}}))
    assert out == wk() and changes(adj) == []
    assert notes[7].startswith("輕鬆跑偏強（TSS 60 > 計畫 36")


def test_high_hr_with_normal_power_and_tss_is_not_flagged():
    r = {7: {"avg_hr": 165, "aet": 150, "avg_power": 220, "cp": 300, "tss": 38}}
    out, adj, notes = A.adapt(wk(), [], c(reviews=r))
    assert notes == {} and adj == [] and out == wk()


def test_without_power_hr_over_94_percent_lthr_is_the_fallback():
    # no power (the user 2026-10-06): avg HR > 94 % LTHR (above Friel run Zone 3), no time share
    assert A.overhard(36, {"avg_hr": 159, "lthr": 170, "aet": 150, "tss": 30}) is None     # 93.5 %
    why = A.overhard(36, {"avg_hr": 161, "lthr": 170, "aet": 150, "tss": 30})
    assert why == "平均心率 161 > LTHR 的 94%（160 bpm；沒有功率才看心率）"
    assert A.overhard(36, {"avg_hr": 170, "aet": 150, "tss": 44}).startswith("TSS 44")        # no LTHR: TSS still
    # power in the row: HR is not read at all, however high
    assert A.overhard(36, {"avg_hr": 175, "lthr": 170, "avg_power": 220, "cp": 300, "tss": 30}) is None
    # a power value without CP is no power: the HR fallback applies
    assert A.overhard(36, {"avg_hr": 165, "lthr": 170, "avg_power": 220, "tss": 30}).startswith("平均心率 165")
    # label only, like the power one
    out, adj, notes = A.adapt(wk(), [], c(reviews={7: {"avg_hr": 165, "lthr": 170, "tss": 30}}))
    assert out == wk() and changes(adj) == [] and notes[7].startswith("輕鬆跑偏強（平均心率 165")


def test_run_classified_as_threshold_moves_the_quality():
    out, adj, notes = A.adapt(wk(), [], c(reviews=Z3))
    s = ids(out)
    assert s["quality"]["day"] == "2026-10-02" and s["quality"]["title"] == "閾值 3×10 分"
    a = changes(adj)
    assert len(a) == 1 and a[0]["rule"] == "overhard" and a[0]["action"] == "moved"
    assert a[0]["before_day"] == "2026-10-01" and a[0]["activity"] == 7
    assert a[0]["reason"] == "週四 閾值 3×10 分延到週五：週三輕鬆跑跑成閾值課，間隔不到 48 小時"
    assert "推估" in a[0]["src"] and "間隔至少 2 天" in a[0]["src"]
    assert notes[7] == "輕鬆跑跑成強度課（閾值課）：已調整之後的強度課"
    # nothing else: the long run and the easy runs keep their minutes (no trim any more)
    assert s["long"]["minutes"] == 120 and s["easy1"]["minutes"] == 40
    # a Zone 5 / 高強度長跑 run says so
    _, adj5, _ = A.adapt(wk(), [], c(reviews={7: {"session_type": "quality", "stimulus": "z5"}}))
    assert "跑成VO2max 課" in changes(adj5)[0]["reason"]
    _, adjl, _ = A.adapt(wk(), [], c(reviews={7: {"session_type": "hard_long", "stimulus": "z3"}}))
    assert "跑成高強度長跑" in changes(adjl)[0]["reason"]


def test_too_hard_with_no_hard_session_near_only_notes():
    out, adj, notes = A.adapt(wk(quality_day="2026-10-02"), [], c(reviews=Z3))
    assert ids(out)["quality"]["day"] == "2026-10-02" and changes(adj) == []
    assert notes[7] == "輕鬆跑跑成強度課（閾值課）：48 小時內沒有強度課，課表不用動"


def test_the_generators_own_move_gets_the_reason():
    # week_plan already spaced the quality from Wed's hard run (hard_done): stored Thu, generated Fri
    stored = [st("quality", "quality", "2026-10-01", minutes=60)]
    out, adj, _ = A.adapt(wk(quality_day="2026-10-02"), stored, c(reviews=Z3, hard_days=["2026-09-30"]))
    a = changes(adj)
    assert ids(out)["quality"]["day"] == "2026-10-02"
    assert len(a) == 1 and a[0]["action"] == "moved" and a[0]["before_day"] == "2026-10-01"
    assert a[0]["reason"] == "週四 閾值 3×10 分延到週五：週三輕鬆跑跑成閾值課，間隔不到 48 小時"
    # stored already on Fri (the next run): nothing to say
    _, adj2, _ = A.adapt(wk(quality_day="2026-10-02"), [st("quality", "quality", "2026-10-02", minutes=60)],
                         c(reviews=Z3, hard_days=["2026-09-30"]))
    assert changes(adj2) == []


def test_user_edited_sessions_and_other_rules_blocks_are_left_alone():
    stored = [st("quality", "quality", "2026-10-01", edited=True, minutes=60)]
    _, adj, _ = A.adapt(wk(), stored, c(reviews=Z3))
    assert changes(adj) == []
    # rule E removed the quality (CTL ramp at the block line): 太強 adds nothing back
    out, adj, _ = A.adapt(wk(), [], c(reviews=Z3, load={"ramp": 12.0}))
    assert "quality" not in ids(out)
    assert [a["rule"] for a in changes(adj) if a.get("gen_key") == "quality"] == ["fatigue"]


def test_with_the_self_rating_rule_it_adjusts_once():
    out, adj, _ = A.adapt(wk(), [], c(reviews=Z3, rpe=rated(5)))
    a = changes(adj)
    assert len(a) == 1 and a[0]["rule"] == "overhard" and ids(out)["quality"]["day"] == "2026-10-02"
    # 偏強 only (a label): the self-rating rule still moves the session, once
    out, adj, _ = A.adapt(wk(), [], c(reviews={7: {"tss": 60}}, rpe=rated(5)))
    a = changes(adj)
    assert len(a) == 1 and a[0]["rule"] == A.RPE_RULE


def test_idempotent_through_reconcile():
    gen = copy.deepcopy(wk()[0]["sessions"])
    inp = {"cur": {"week": {"start": "2026-09-28", "end": "2026-10-04", "today": "2026-10-01", "days_left": 4},
                   "mode": "base", "load": {}, "sessions": gen, "done": {"activities": [EASY]}},
           "weeks": [], "activities": [EASY], "today": "2026-10-01", "horizon_end": "2026-10-04",
           "thresholds": {"cp": 300.0, "lthr": 170.0, "aet": 150.0},
           "adapt": {"enabled": True, "reviews": Z3, "first_free": "2026-10-01"}}
    adj: list = []
    new, ch = PS.reconcile_with_adapt([], inp, adjustments=adj)
    q = next(s for s in new if s.get("gen_key") == "quality" and s["state"] == "active")
    assert q["day"] == "2026-10-02"
    done = next(s for s in new if s.get("gen_key") == "easy0")
    assert done["note"] == "輕鬆跑跑成強度課（閾值課）：已調整之後的強度課"
    again, ch2 = PS.reconcile_with_adapt(new, copy.deepcopy(inp))
    assert ch2 == []


def test_note_prefixes_cover_english():
    from backend.i18n import translate
    assert translate(A.NOTE_TOO_HARD, "en") in A._note_prefixes()
    assert translate("輕鬆跑跑成強度課（{label}）：{what}", "en", label="x", what="y").startswith(
        translate(A.NOTE_TOO_HARD, "en"))
    assert translate("輕鬆跑偏強（{why}）：只標示，課表不變", "en", why="x").startswith(
        translate(A.NOTE_PREFIX, "en"))


# ---- plan_auto: reason in the log, 復原, the A-race confirm --------------------

def _q(ss):
    return next(s for s in ss if s.get("gen_key") == "quality" and s["state"] == "active" and s["week_start"] == "2026-09-28")


def _too_hard(box, days_to_race=None):
    box.make(days_to_race=days_to_race)
    box.inp["adapt"]["reviews"] = Z3
    box.inp["rpe_stamp"] = "z3"          # a new data stamp (in real use: the new activity) so the run acts


def test_logs_the_reason_and_undoes(box):
    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        assert _q(await PS.load(db))["day"] == "2026-10-01"
        _too_hard(box)
        r = await PA.run(db)
        assert r["status"] == "applied" and _q(await PS.load(db))["day"] == "2026-10-02"
        e = PA.entry_dict(await db.get(PlanChangeLog, r["id"]))
        it = next(i for i in e["items"] if i["rule"] == "overhard")
        assert it["reason"] == "週四 閾值 3×10 分延到週五：週三輕鬆跑跑成閾值課，間隔不到 48 小時"
        assert e["can_undo"]
        await PA.undo(db, r["id"])
        q = _q(await PS.load(db))
        assert q["day"] == "2026-10-01" and q["edited"] is True
    run(go())


def test_within_14_days_of_an_a_race_it_waits_for_approval(box):
    async def go():
        db = await make_db()
        box.make(days_to_race=10)
        await PA.run(db)
        _too_hard(box, days_to_race=10)
        r = await PA.run(db)
        assert r["status"] == "pending"
        big = PA.entry_dict(await PA.pending(db))["big"]
        assert [x["rule"] for x in big] == ["too_hard"] and "跑成閾值課" in big[0]["text"]
        assert _q(await PS.load(db))["day"] == "2026-10-01"
        assert "too_hard" in PA.BIG_TEXT
    run(go())


@pytest.mark.parametrize("days", [40])
def test_far_from_the_race_it_applies_on_its_own(box, days):
    async def go():
        db = await make_db()
        box.make(days_to_race=days)
        await PA.run(db)
        _too_hard(box, days_to_race=days)
        assert (await PA.run(db))["status"] == "applied"
    run(go())
