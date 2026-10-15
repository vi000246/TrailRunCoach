"""
engine/adapt.py: the outcome-based adjustments, on synthetic plans (no DB, no
dataset, no COROS).
"""
import copy

from backend.engine import adapt as A
from backend.engine import plan_store as PS
from backend.engine import quality_gate as QG

WS = "2026-09-28"           # Mon; week to Sun 10-04
TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}


def g(id, kind, day, minutes=45, tss=None, done=False, done_by=None, title=None):
    return {"id": id, "kind": kind, "title": title or {"easy": "輕鬆跑", "long": "LSD", "quality": "閾值 3×10 分",
                                                       "strength": "肌力"}.get(kind, kind),
            "minutes": minutes, "target": "", "detail": "", "source": "",
            "tss": tss if tss is not None else minutes * 0.8, "day": day, "done": done, "done_by": done_by}


def st(gen_key, kind, day, state="active", edited=False, origin="auto", done_by=None, minutes=45, tss=None):
    return {"uid": f"u-{gen_key}-{day}", "week_start": WS, "gen_key": gen_key, "day": day, "kind": kind,
            "title": kind, "minutes": minutes, "target": "", "detail": "", "source": "",
            "tss": tss if tss is not None else minutes * 0.8, "origin": origin, "edited": edited,
            "provisional": False, "state": state, "done_by": done_by, "note": None}


def week(sessions, mode="base"):
    return [{"start": WS, "mode": mode, "provisional": False, "sessions": sessions}]


def ctx(today="2026-10-01", **kw):
    return {"today": today, "first_free": today, "thresholds": TH, "mode": "base", "load": {}, "reviews": {}, **kw}


def ids(weeks):
    return {s["id"]: s for s in weeks[0]["sessions"]}


# ---- A. missed easy --------------------------------------------------------

def test_missed_easy_is_dropped_not_made_up():
    gw = week([g("easy1", "easy", "2026-10-02"), g("easy2", "easy", "2026-10-03"), g("long", "long", "2026-10-04", 120)])
    stored = [st("easy2", "easy", "2026-09-29", state="missed")]
    out, adj, _ = A.adapt(gw, stored, ctx())
    assert "easy2" not in ids(out) and "easy1" in ids(out)
    assert adj[0]["rule"] == "missed_easy" and "不補" in adj[0]["reason"]
    assert "easy2" in ids(gw)                      # input untouched


def test_missed_easy_edited_by_user_untouched():
    gw = week([g("easy2", "easy", "2026-10-03")])
    stored = [st("easy2", "easy", "2026-09-29", state="missed"),
              st("easy2", "easy", "2026-10-03", edited=True)]
    out, adj, _ = A.adapt(gw, stored, ctx())
    assert "easy2" in ids(out) and adj == []


# ---- B. missed quality -----------------------------------------------------

def test_missed_quality_kept_when_spacing_holds():
    gw = week([g("quality", "quality", "2026-10-01", 60), g("easy1", "easy", "2026-10-02"),
               g("long", "long", "2026-10-04", 120)])
    stored = [st("quality", "quality", "2026-09-30", state="missed")]
    out, adj, _ = A.adapt(gw, stored, ctx())
    assert ids(out)["quality"]["day"] == "2026-10-01"
    assert adj[0]["action"] == "moved" and "延到週四" in adj[0]["reason"] and "仍有 3 天" in adj[0]["reason"]


def test_missed_quality_moved_to_keep_48h():
    # the generator put it on Sat, next to Sun's long run; Thu is open
    gw = week([g("quality", "quality", "2026-10-03", 60), g("long", "long", "2026-10-04", 120)])
    stored = [st("quality", "quality", "2026-09-30", state="missed")]
    out, adj, _ = A.adapt(gw, stored, ctx())
    assert ids(out)["quality"]["day"] == "2026-10-01"


def test_missed_quality_cancelled_when_no_spaced_day():
    # today Sat: only Sat / Sun left, long run Sun
    gw = week([g("quality", "quality", "2026-10-03", 60), g("long", "long", "2026-10-04", 120)])
    stored = [st("quality", "quality", "2026-10-01", state="missed")]
    out, adj, _ = A.adapt(gw, stored, ctx(today="2026-10-03"))
    assert "quality" not in ids(out)
    assert adj[0]["action"] == "removed" and "下週重複同一階" in adj[0]["reason"]


def test_missed_quality_respects_a_done_hard_day():
    hard_done = {"index": 5, "date": "2026-10-02", "category": "road", "tss": 70}
    gw = week([g("test", "test", "2026-10-02", 50, done=True, done_by=hard_done),
               g("quality", "quality", "2026-10-03", 60), g("long", "long", "2026-10-06", 120)])
    gw[0]["sessions"][2]["day"] = None             # long not placed this week
    stored = [st("quality", "quality", "2026-09-30", state="missed")]
    out, adj, _ = A.adapt(gw, stored, ctx(today="2026-10-03"))
    # Sat is 1 day after Fri's test, Sun too close? Sun is 2 days: ok
    assert ids(out)["quality"]["day"] == "2026-10-04"


def test_missed_quality_respects_an_unplanned_hard_run():
    # an unplanned 高強度長跑 / Z5 run on Thu (ctx hard_days, plan_sessions._adapt_ctx): not Fri
    gw = week([g("quality", "quality", "2026-10-02", 60), g("easy1", "easy", "2026-10-03"),
               g("long", "long", "2026-10-06", 120)])
    gw[0]["sessions"][2]["day"] = None
    stored = [st("quality", "quality", "2026-09-30", state="missed")]
    out, _, _ = A.adapt(gw, stored, ctx(today="2026-10-02", hard_days=["2026-10-01"]))
    assert ids(out)["quality"]["day"] not in ("2026-10-02",)
    # without it Fri is fine
    out2, _, _ = A.adapt(copy.deepcopy(gw), stored, ctx(today="2026-10-02"))
    assert ids(out2)["quality"]["day"] == "2026-10-02"


# ---- C. missed long --------------------------------------------------------

def test_missed_long_moved_off_a_quality_neighbour_or_cancelled():
    gw = week([g("quality", "quality", "2026-10-02", 60), g("long", "long", "2026-10-03", 120)])
    stored = [st("long", "long", "2026-09-27", state="missed")]
    stored[0]["day"] = "2026-09-28"
    out, adj, _ = A.adapt(gw, stored, ctx(today="2026-10-01"))
    assert ids(out)["long"]["day"] == "2026-10-04"           # Sat is next to Fri's quality
    out, adj, _ = A.adapt(gw, stored, ctx(today="2026-10-03"))
    assert "long" in ids(out) and ids(out)["long"]["day"] == "2026-10-04"
    gw2 = week([g("quality", "quality", "2026-10-03", 60), g("long", "long", "2026-10-04", 120)])
    out, adj, _ = A.adapt(gw2, stored, ctx(today="2026-10-03"))
    assert "long" not in ids(out) and "不移到下週" in adj[-1]["reason"]


# ---- D. easy run done too hard (SP-301: 偏強 label only, 太強 = a hard session; more in test_adapt_overhard)

def test_overhard_detection_thresholds():
    # no heart-rate condition any more: power or TSS, either one
    assert A.overhard(36, {"avg_hr": 160, "aet": 150, "over_aet_s": 900, "hr_s": 3000}) is None
    assert "功率" in A.overhard(36, {"avg_power": 250, "cp": 300})
    assert A.overhard(36, {"avg_power": 230, "cp": 300}) is None
    assert "負荷" in A.overhard(36, {"tss": 45}) and "20%" in A.overhard(36, {"tss": 45})
    assert A.overhard(36, {"tss": 43}) is None


def _overhard_week(today="2026-09-30"):
    act = {"index": 7, "date": "2026-09-29", "category": "road", "tss": 60}
    return week([g("easy1", "easy", "2026-09-29", 45, tss=36, done=True, done_by=act),
                 g("quality", "quality", "2026-09-30", 60, tss=70),
                 g("easy2", "easy", "2026-10-01", 50, tss=40), g("easy3", "easy", "2026-10-03", 40, tss=32),
                 g("long", "long", "2026-10-04", 120, tss=100)])


def test_overhard_by_tss_only_labels_the_done_run():
    gw = _overhard_week()
    out, adj, notes = A.adapt(gw, [], ctx(today="2026-09-30", reviews={7: {"tss": 60}}))
    s = ids(out)
    assert s["easy1"]["done"] is True                         # never voided
    assert notes[7] == "輕鬆跑偏強（負荷 60 > 計畫 36 的 +20%）：只標示，課表不變"
    # nothing moves, steps down or shrinks
    assert out == gw
    assert [a["action"] for a in adj] == ["note"] and adj[0]["rule"] == "overhard"


def test_overhard_downgrades_when_no_spaced_day():
    # 太強 (the classifier calls the easy run a Zone 3 session): the hard session next day steps down
    act = {"index": 7, "date": "2026-10-02", "category": "road", "tss": 40}
    hard = {7: {"session_type": "quality", "stimulus": "z3"}}
    gw = week([g("easy1", "easy", "2026-10-02", 45, tss=36, done=True, done_by=act),
               g("quality", "quality", "2026-10-03", 46, tss=46, title=QG.DOSE[1][1]),
               g("long", "long", "2026-10-04", 120)])
    out, adj, _ = A.adapt(gw, [], ctx(today="2026-10-03", reviews=hard))
    q = ids(out)["quality"]
    assert q["title"] == QG.DOSE[0][1] and q["day"] == "2026-10-03"
    assert any(a["action"] == "downgraded" for a in adj)
    # the Zone 3 track's first rung (A1) -> the 巡航版 T3 (interval_library.PREV_RUNG, SP-31) …
    gw[0]["sessions"][1]["title"] = QG.DOSE[0][1]
    out, adj, _ = A.adapt(gw, [], ctx(today="2026-10-03", reviews=hard))
    assert ids(out)["quality"]["title"] == QG.CRUISE[2][1]
    # … and the first 巡航版 rung -> an easy run
    gw[0]["sessions"][1]["title"] = QG.CRUISE[0][1]
    out, adj, _ = A.adapt(gw, [], ctx(today="2026-10-03", reviews=hard))
    assert ids(out)["quality"]["kind"] == "easy"
    # 偏強 by power on the same week: a label only, the session stays
    gw[0]["sessions"][1]["title"] = QG.DOSE[1][1]
    out, adj, _ = A.adapt(gw, [], ctx(today="2026-10-03", reviews={7: {"avg_power": 260, "cp": 300}}))
    assert ids(out)["quality"]["title"] == QG.DOSE[1][1] and [a["action"] for a in adj] == ["note"]


# ---- E. fatigue guard ------------------------------------------------------

def test_fatigue_ramp_removes_quality_and_cuts_easy():
    gw = week([g("quality", "quality", "2026-10-01", 60), g("easy1", "easy", "2026-10-02", 50),
               g("long", "long", "2026-10-04", 120)])
    out, adj, _ = A.adapt(gw, [], ctx(load={"ramp": 8.5}))          # Friel: ≥ 8 (B2)
    s = ids(out)
    assert "quality" not in s and s["easy1"]["minutes"] == 40 and s["long"]["minutes"] == 120
    assert all(a["rule"] == "fatigue" for a in adj)


def test_fatigue_tsb_skipped_when_already_recovery_week():
    gw = week([g("easy1", "easy", "2026-10-02", 50)])
    out, adj, _ = A.adapt(gw, [], ctx(load={"tsb": -35}, mode="recovery_week"))
    assert adj == [] and ids(out)["easy1"]["minutes"] == 50


def _red(gen_key, day, index, cat="road", moving_s=900, tss=10):
    return st(gen_key, "easy", day, state="done", minutes=45,
              done_by={"index": index, "date": day, "category": cat, "moving_s": moving_s, "tss": tss})


def test_fatigue_two_reds_no_longer_trigger_sp398():
    # SP-398 (the user 2026-10-12): rule E looks at load, not at the completion colour — two red
    # sessions in a row (too short, a bike ride for a run, a walk for a run …) change nothing
    gw = lambda: week([g("quality", "quality", "2026-10-01", 60), g("easy3", "easy", "2026-10-02", 50)])  # noqa: E731
    cases = [
        [_red("easy1", "2026-09-28", 1), _red("easy2", "2026-09-29", 2)],                       # time / TSS red
        [_red("easy1", "2026-09-28", 1, "bike", 2700, 36), _red("easy2", "2026-09-29", 2, "bike", 2700, 36)],
        [_red("easy1", "2026-09-28", 1, "walk"), _red("easy2", "2026-09-29", 2, "hike")],
        [_red("easy1", "2026-09-28", 1, "walk", 2700, None), _red("easy2", "2026-09-29", 2, "hike", 2700, None)],
    ]
    for reds in cases:
        out, adj, _ = A.adapt(gw(), reds, ctx())
        assert not [a for a in adj if a["rule"] == "fatigue"]
        assert ids(out)["quality"]["title"] == "閾值 3×10 分" and ids(out)["easy3"]["minutes"] == 50


def test_fatigue_tsb_and_ramp_still_act_with_two_reds_sp398():
    reds = [_red("easy1", "2026-09-28", 1), _red("easy2", "2026-09-29", 2)]
    gw = lambda: week([g("quality", "quality", "2026-10-01", 60), g("easy3", "easy", "2026-10-02", 50)])  # noqa: E731
    for load, why in (({"tsb": -35, "ctl": 100}, "負荷平衡（Load balance）-35＝長期負荷的 -35% < −30%"),   # SP-670
                      ({"ramp": 8.5}, "")):
        out, adj, _ = A.adapt(gw(), reds, ctx(load=load))
        assert "quality" not in ids(out) and ids(out)["easy3"]["minutes"] == 40
        fat = [a for a in adj if a["rule"] == "fatigue"]
        assert fat and all(why in a["reason"] and "紅" not in a["reason"] for a in fat)


def test_two_red_trigger_is_gone_sp398():
    import re
    from backend.engine import compliance as C
    assert not [n for n in dir(A) + dir(C) if re.search(r"red_?streak|streak_?red", n, re.I)]
    assert "紅" not in A.SRC_FATIGUE


# ---- E. fatigue guard: the last two easy / long runs too hard (SP-404) -----

HARD_Z3 = {"session_type": "quality", "stimulus": "z3"}       # rule D's 太強 (the classifier: Zone 3)


def _run(gen_key, day, index, kind="easy", **extra):
    return g(gen_key, kind, day, 120 if kind == "long" else 45, tss=100 if kind == "long" else 36, done=True,
             done_by={"index": index, "date": day, "category": "road", "tss": 40, **extra})


def _streak_week(*runs):
    """Done runs early in the week (today Thu 10-01), then a quality Fri, an easy run Sat, the long run Sun."""
    return week(list(runs) + [g("quality", "quality", "2026-10-02", 60, tss=70), g("easy3", "easy", "2026-10-03", 50),
                              g("long", "long", "2026-10-04", 120, tss=100)])


def _fatigue_adj(adj):
    return [a for a in adj if a["rule"] == "fatigue"]


def _stepped_down(out, adj):
    s = ids(out)
    fat = _fatigue_adj(adj)
    return (s["quality"]["title"] == QG.RECOVERY[1] and s["easy3"]["minutes"] == 40 and s["long"]["minutes"] == 120
            and fat and all("連續 2 堂輕鬆課跑太強" in a["reason"] for a in fat)
            and {a["action"] for a in fat} == {"downgraded", "trimmed"})


def test_fatigue_two_too_hard_easy_runs_step_the_week_down_sp404():
    gw = _streak_week(_run("easy1", "2026-09-28", 1), _run("easy2", "2026-09-29", 2))
    out, adj, _ = A.adapt(gw, [], ctx(reviews={1: HARD_Z3, 2: HARD_Z3}))
    assert _stepped_down(out, adj)
    q = next(a for a in _fatigue_adj(adj) if a["gen_key"] == "quality")
    assert "恢復週 fartlek" in q["reason"] and "週一" in q["reason"] and "週二" in q["reason"]
    # one too hard is not a streak; TSB normal, nothing else
    out, adj, _ = A.adapt(_streak_week(_run("easy1", "2026-09-28", 1), _run("easy2", "2026-09-29", 2)), [],
                          ctx(reviews={2: HARD_Z3}))
    assert not _fatigue_adj(adj) and ids(out)["quality"]["title"] == "閾值 3×10 分"


def test_fatigue_streak_counts_rpe_hard_and_overhard_sp404():
    runs = lambda: _streak_week(_run("easy1", "2026-09-28", 1), _run("easy2", "2026-09-29", 2))  # noqa: E731
    # one 太強, one self-rated Hard (COROS 4 = RPE 7)
    out, adj, _ = A.adapt(runs(), [], ctx(reviews={1: HARD_Z3}, rpe={2: {"rpe": 7.0, "source": "coros", "coros_feel": 4}}))
    assert _stepped_down(out, adj)
    # one 太強, one 偏強 by TSS (60 > 36 + 20 %)
    out, adj, _ = A.adapt(runs(), [], ctx(reviews={1: HARD_Z3, 2: {"tss": 60}}))
    assert _stepped_down(out, adj)
    # a self-rated long run counts too (rule rpe_hard reads long runs)
    gw = _streak_week(_run("long0", "2026-09-27", 1, kind="long"), _run("easy2", "2026-09-29", 2))
    gw[0]["sessions"][0]["done_by"]["date"] = "2026-09-28"
    gw[0]["sessions"][0]["day"] = "2026-09-28"
    out, adj, _ = A.adapt(gw, [], ctx(reviews={2: HARD_Z3}, rpe={1: {"rpe": 8.0, "source": "watch", "coros_feel": None}}))
    assert ids(out)["quality"]["title"] == QG.RECOVERY[1]
    # switched off rpe_rule: the self-rating is not read
    out, adj, _ = A.adapt(runs(), [], ctx(reviews={1: HARD_Z3}, rpe={2: {"rpe": 7.0, "source": "coros", "coros_feel": 4}},
                                          rpe_rule=False))
    assert not _fatigue_adj(adj)


def test_fatigue_streak_is_the_last_two_easy_or_long_runs_sp404():
    hard = {1: HARD_Z3, 3: HARD_Z3}
    # a normal easy run between the two: no streak
    gw = _streak_week(_run("easy1", "2026-09-28", 1), _run("easy2", "2026-09-29", 2), _run("easy4", "2026-09-30", 3))
    out, adj, _ = A.adapt(gw, [], ctx(reviews=hard))
    assert not _fatigue_adj(adj) and ids(out)["easy3"]["minutes"] == 50
    # a hard session done between them (not rated too hard / not too hard): still a streak
    gw = _streak_week(_run("easy1", "2026-09-28", 1), _run("q0", "2026-09-29", 2, kind="quality"),
                      _run("easy4", "2026-09-30", 3))
    out, adj, _ = A.adapt(gw, [], ctx(reviews=hard))
    assert _stepped_down(out, adj)
    # a skipped (missed) easy run between them: still a streak
    gw = _streak_week(_run("easy1", "2026-09-28", 1), _run("easy4", "2026-09-30", 3))
    out, adj, _ = A.adapt(gw, [st("easy2", "easy", "2026-09-29", state="missed")], ctx(reviews=hard))
    assert _stepped_down(out, adj)
    # the user's own done easy runs count as well (stored, not generated)
    own = [st("u1", "easy", "2026-09-28", state="done", origin="custom", done_by={"index": 1, "date": "2026-09-28",
                                                                                   "category": "road", "tss": 40}),
           st("u2", "easy", "2026-09-29", state="done", origin="custom", done_by={"index": 3, "date": "2026-09-29",
                                                                                   "category": "road", "tss": 40})]
    out, adj, _ = A.adapt(_streak_week(), own, ctx(reviews=hard))
    assert _stepped_down(out, adj)


def test_fatigue_streak_skips_hr_only_overhard_when_hr_quality_is_low_sp404():
    # no power: 偏強 by HR alone (avg 165 > the easy cap 153 + 3, SP-565); TSS on plan
    hr = {"avg_hr": 165, "easy_cap": {"value": 153.0}, "tss": 38}
    gw = lambda q: _streak_week(_run("easy1", "2026-09-28", 1, hr_quality=q), _run("easy2", "2026-09-29", 2, hr_quality=q))  # noqa: E731
    out, adj, _ = A.adapt(gw("low"), [], ctx(reviews={1: hr, 2: hr}))
    assert not _fatigue_adj(adj) and ids(out)["quality"]["title"] == "閾值 3×10 分"
    # the same with trusted HR: a streak
    out, adj, _ = A.adapt(gw(None), [], ctx(reviews={1: hr, 2: hr}))
    assert _stepped_down(out, adj)
    # low HR quality but 偏強 by TSS too: still counts
    hr_tss = {**hr, "tss": 60}
    out, adj, _ = A.adapt(gw("low"), [], ctx(reviews={1: hr_tss, 2: hr_tss}))
    assert _stepped_down(out, adj)


def test_fatigue_tsb_or_ramp_wins_over_the_streak_sp404():
    for load in ({"tsb": -35}, {"ramp": 8.5}):
        gw = _streak_week(_run("easy1", "2026-09-28", 1), _run("easy2", "2026-09-29", 2))
        out, adj, _ = A.adapt(gw, [], ctx(reviews={1: HARD_Z3, 2: HARD_Z3}, load=load))
        s, fat = ids(out), _fatigue_adj(adj)
        assert "quality" not in s and s["easy3"]["minutes"] == 40
        assert sorted(a["action"] for a in fat) == ["removed", "trimmed"]          # one change per session
        assert not any("太強" in a["reason"] for a in fat)


def test_rule_e_docstring_names_the_streak_sp404():
    doc = A.__doc__
    e = doc[doc.index("  E. fatigue guard"):]
    assert "連續" in e and "推估" in e and "hr_quality" in e and A.OVERHARD_STREAK == 2


# ---- round trip through reconcile: idempotent ------------------------------

def _inputs(gw_sessions, acts=(), today="2026-10-01", reviews=None):
    cur = {"week": {"start": WS, "end": "2026-10-04", "today": today, "days_left": 4}, "mode": "base",
           "load": {}, "sessions": gw_sessions, "done": {"activities": list(acts)}}
    return {"cur": cur, "weeks": [], "activities": list(acts), "today": today, "horizon_end": "2026-10-04",
            "thresholds": TH, "adapt": {"enabled": True, "reviews": reviews or {}, "first_free": today}}


def test_adapt_then_reconcile_twice_is_a_no_op():
    gen = [g("easy1", "easy", "2026-10-02"), g("easy2", "easy", "2026-10-03"), g("long", "long", "2026-10-04", 120)]
    stored = [st("easy2", "easy", "2026-09-29", state="missed"), st("easy1", "easy", "2026-10-02"),
              st("long", "long", "2026-10-04", minutes=120)]
    inp = _inputs(gen)
    adj: list = []
    new, changes = PS.reconcile_with_adapt(stored, inp, adjustments=adj)
    assert not [s for s in new if s["state"] == "active" and s["gen_key"] == "easy2"]
    assert any(a["rule"] == "missed_easy" for a in adj)
    again, changes2 = PS.reconcile_with_adapt(new, copy.deepcopy(inp))
    assert changes2 == []
    assert sorted((s["uid"], s["day"], s["minutes"]) for s in again) == sorted((s["uid"], s["day"], s["minutes"]) for s in new)


def test_overhard_note_lands_on_the_done_session_and_is_idempotent():
    act = {"index": 7, "date": "2026-09-29", "category": "road", "tss": 60, "moving_s": 2700}
    gen = [g("easy1", "easy", "2026-09-29", 45, tss=36, done=True, done_by=act),
           g("easy2", "easy", "2026-10-02", 50, tss=40), g("easy3", "easy", "2026-10-03", 40, tss=32)]
    inp = _inputs(gen, [act], reviews={7: {"avg_power": 260, "cp": 300}})
    new, ch = PS.reconcile_with_adapt([], inp)
    done = next(s for s in new if s["gen_key"] == "easy1")
    assert done["state"] == "done" and done["note"].startswith("輕鬆跑偏強")
    again, ch2 = PS.reconcile_with_adapt(new, copy.deepcopy(inp))
    assert ch2 == []
    # a stale note (the old wording, or the run no longer over the line) is cleared
    inp2 = _inputs(gen, [act], reviews={7: {"avg_power": 200, "cp": 300, "tss": 38}})
    stale = copy.deepcopy(new)
    next(s for s in stale if s["gen_key"] == "easy1")["note"] = "輕鬆跑偏強（平均心率 158 > AeT+3（153））：已調整之後的課表"
    cleared, _ = PS.reconcile_with_adapt(stale, inp2)
    assert next(s for s in cleared if s["gen_key"] == "easy1")["note"] is None


def test_disabled_is_the_plain_generator():
    gen = [g("easy2", "easy", "2026-10-03")]
    stored = [st("easy2", "easy", "2026-09-29", state="missed")]
    inp = _inputs(gen)
    inp["adapt"]["enabled"] = False
    new, _ = PS.reconcile_with_adapt(stored, inp)
    assert [s for s in new if s["state"] == "active" and s["gen_key"] == "easy2"]


def test_done_session_counts_its_actual_tss():
    s = st("easy1", "easy", "2026-09-29", state="done", tss=36, done_by={"index": 7, "tss": 60})
    assert PS.session_tss(s) == 60.0
    assert PS.session_tss({**s, "kind": "notice"}) == 0.0
    assert PS.session_tss({**s, "state": "active"}) == 36.0


def test_a_downgraded_rpe_quality_session_drops_its_rpe_structure():
    """SP-87: the 專項期 技術地形 RPE 6–7 session is kind quality now, so adapt's downgrades reach it — the
    recovery run / easy run it becomes must not keep the RPE 6–7 technical structure (pushed to the
    watch otherwise)."""
    from backend.engine import adapt as A
    from backend.engine import technical as T
    steps = T._steps(55, T.SPEC_RPE)
    g = {"id": "tech", "kind": "quality", "title": "技術地形 80′（RPE 6–7）", "minutes": 80, "steps": steps,
         "day": "2026-10-08", "tss": 80.0}
    became = A._downgrade(dict(g), {})
    d = dict(g)
    A._downgrade(d, {})
    assert became == "輕鬆跑" and d["kind"] == "easy" and d.get("steps") is None
    r = dict(g)
    A._to_recovery(r, {})
    assert r.get("steps") is None and "技術地形" not in r["title"]


def test_fatigue_streak_reads_sp402s_hr_quality_from_the_activity_row_sp404(monkeypatch):
    # SP-402 writes the run's HR quality on the activity row (overview.activity_row ->
    # workout_review.run_hr_quality: "low" / "ok"), which becomes the done session's done_by; the
    # completion grading (compliance.session_compliance: `uncertain`) and rule E's streak read the same field
    import datetime as dt
    from backend.engine import compliance as C
    from backend.engine import overview as O
    from backend.engine import workout_review as WR
    from backend.tests.wko5_fakes import FakeDataset, FakeWorkout
    ws = [FakeWorkout(start=dt.datetime(2026, 9, d, 7), sport="run", tags=["running"],
                      metrics={"distance": 8.0, "duration": 2700, "movingduration": 2700, "tss": 38.0}) for d in (28, 29)]
    ds = FakeDataset(ws, dt.date(2026, 10, 1))
    hr = {"avg_hr": 165, "easy_cap": {"value": 153.0}, "tss": 38}   # 偏強 by HR alone (no power, > cap 153 + 3), TSS on plan

    def run(quality):
        monkeypatch.setattr(WR, "run_hr_quality", lambda ds, w: quality)
        rows = [O.activity_row(w, ds) for w in ds.workouts]
        assert [r["hr_quality"] for r in rows] == [quality, quality]
        runs = [g(f"easy{i}", "easy", r["date"], 45, tss=36, done=True, done_by=r) for i, r in enumerate(rows, 1)]
        out, adj, _ = A.adapt(_streak_week(*runs), [], ctx(reviews={r["index"]: hr for r in rows}))
        s = {"kind": "easy", "state": "done", "minutes": 45, "tss": 36, "done_by": {**rows[0], "moving_s": 2700, "tss": 20}}
        return out, adj, C.session_compliance(s)

    out, adj, comp = run("low")
    assert comp["uncertain"] is True                              # SP-402 sees it …
    assert not _fatigue_adj(adj) and ids(out)["quality"]["title"] == "閾值 3×10 分"   # … and so does SP-404
    out, adj, comp = run("ok")
    assert comp["uncertain"] is False and _stepped_down(out, adj)
