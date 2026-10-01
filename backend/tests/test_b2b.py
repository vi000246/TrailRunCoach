"""連續兩天長天（B2B, engine/b2b.py; docs/research/back-to-back-and-long-day.md):
when it is scheduled, the session structure, the TSB exception (week_plan +
adapt) and the day-2-vs-day-1 evaluation. Synthetic data only — never the
WKO5 folder, the app DB, the user's plan or COROS."""
import datetime as dt
from datetime import date

import numpy as np
import pytest

from backend.engine import adapt as A
from backend.engine import b2b as B2B
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine.planning import Event, Plan, Threshold
from backend.engine.status import Status
from backend.tests.test_quality_gate import TODAY, _ds
from backend.tests.test_workout_review import _run
from backend.tests.wko5_fakes import FakeWorkout

MON = date(2026, 9, 28)                     # TODAY (Wed 9/30) is in this week


def ev(start="2026-11-28", days=2, kind="baiyue", est_hours=None, priority="A", distance_km=None):
    return Event(id="e1", name="測試百岳", date=start, kind=kind, priority=priority, days=days,
                 est_hours=est_hours, distance_km=distance_km)


def evj(**kw):
    return B2B.event_json(ev(**kw))


def ctx(**kw):
    base = dict(kind="specific", mode="specific", monday=MON, event=evj(), last_recovery=True,
                state={"last": None, "three_done": False, "count": 0}, tsb=-5.0, ramp=3.0, guard_ok=True)
    base.update(kw)
    return B2B.week_context(**base)


# ---------------------------------------------------------------------------
# when
# ---------------------------------------------------------------------------

def test_event_must_be_multi_day_or_six_hours():
    assert B2B.qualifies(ev(days=2)) and B2B.qualifies(ev(days=1, est_hours=7.0))
    assert not B2B.qualifies(ev(days=1, est_hours=5.0)) and not B2B.qualifies(ev(days=1, kind="road"))
    assert B2B.qualifies(ev(days=1, distance_km=50))          # is_long without est_hours: ≥ 42 km
    assert not ctx(event=evj(days=1, est_hours=4.0))["candidate"]
    assert ctx(event=evj(days=1, est_hours=8.0, kind="race"))["candidate"]
    assert not ctx(event=None)["candidate"]


def test_target_event_is_the_next_a_event():
    evs = [ev(start="2026-10-20", priority="B"), Event(id="a", name="A", date="2027-01-10", days=3),
           Event(id="old", name="old", date="2026-09-01", days=2)]
    assert B2B.target_event(evs, TODAY).id == "a"


def test_only_in_the_specific_phase_and_not_in_recovery_weeks():
    assert not ctx(kind="base")["candidate"]
    assert "只在專項期排" in ctx(kind="base")["blocked"]
    assert not ctx(mode="recovery_week")["candidate"] and not ctx(mode="reentry")["candidate"]
    assert ctx()["candidate"]


def test_last_b2b_at_least_three_weeks_before_the_event():
    # the week's Sunday is 10/04; the event 10/24 is 20 days later
    assert not ctx(event=evj(start="2026-10-24"))["candidate"]
    assert ctx(event=evj(start="2026-10-25", days=1, est_hours=7))["candidate"]       # 21 days


def test_first_build_week_after_recovery_and_spacing():
    assert not ctx(last_recovery=False)["candidate"]                    # mid-cycle: no
    near = ctx(state={"last": "2026-09-20", "three_done": False, "count": 1})
    assert not near["candidate"] and any("不到 2 週" in b for b in near["blocked"])
    assert ctx(state={"last": "2026-09-06", "three_done": False, "count": 1})["index"] == 2


def test_three_day_version_once_four_to_six_weeks_before_a_multi_day_event():
    c = ctx(event=evj(start="2026-11-07", days=3))                      # 34 days after the Sunday
    assert c["candidate"] and c["days"] == 3
    assert ctx(event=evj(start="2026-11-07", days=3),
               state={"last": "2026-09-06", "three_done": True, "count": 1})["days"] == 2
    # 55 / 62 days: a 2-day now would sit < 2 weeks before the 3-day window → kept for the 3-day
    for start in ("2026-11-28", "2026-12-05"):
        c = ctx(event=evj(start=start, days=3))
        assert not c["candidate"] and any("留給 3 天版本" in b for b in c["blocked"])
    c = ctx(event=evj(start="2026-12-06", days=3))                          # 63 days: 2-day, room after
    assert c["candidate"] and c["days"] == 2
    assert ctx(event=evj(start="2026-11-07", days=2))["days"] == 2      # a 2-day trip: 2-day B2B
    assert ctx(event=evj(start="2026-11-07", days=1, est_hours=8))["days"] == 2   # single day: never 3
    # inside the window: the first week that can take it, recovery week before or not (推估)
    c = ctx(event=evj(start="2026-11-02", days=3), last_recovery=False)      # 29 days
    assert c["candidate"] and c["days"] == 3


def test_guardrails_tsb_ramp_gate_and_base_built():
    assert not ctx(tsb=-22.0)["candidate"]
    assert not ctx(ramp=8.5)["candidate"]
    assert not ctx(guard_ok=False)["candidate"]
    c = B2B.finalize(ctx(), long_min=180, longest_before=150, total_min=480)
    assert not c["due"] and any("基礎還不夠" in b for b in c["blocked"])        # 150 < 0.87 × 180
    c = B2B.finalize(ctx(), long_min=180, longest_before=160, total_min=480)
    assert c["due"] and c["minutes"] == [180, 120]


def test_recovery_week_detection():
    assert B2B.last_was_recovery([5.0, 5.2, 5.5, 3.6])
    assert not B2B.last_was_recovery([5.0, 5.2, 5.5, 5.4])
    assert not B2B.last_was_recovery([5.0, 3.0])


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------

def test_day_minutes_two_thirds_pair_cap_and_single_day_clamp():
    assert B2B.minutes(180, 600, 2, {"days": 2}) == [180, 120]               # CTS 30:20
    assert B2B.minutes(180, 600, 3, {"days": 3}) == [180, 120, 120]          # 30 + 20 + 20
    # the pair ≤ 70 % of the week: 300 + 200 > 0.7 × 480 → both scaled
    m = B2B.minutes(300, 480, 2, {"days": 2})
    assert sum(m) <= 0.7 * 480 + 5 and m[1] == pytest.approx(m[0] * 0.67, abs=5)
    assert B2B.minutes(240, 900, 2, {"days": 1}) == [240, 150]               # single ≥ 6 h: 1.5–2.5 h
    assert B2B.minutes(100, 900, 2, {"days": 1}) == [100, 90]
    assert B2B.minutes(80, 150, 2, {"days": 2}) is None                     # day 2 < 60 min


def test_pack_progression_5_10_15_percent_up_to_9_kg():
    e = {"kind": "baiyue"}
    # weeks before the trip: 10–9 → 5 %, 8–7 → 10 %, 6–3 → 15 % (loaded-carry-training.md table)
    assert [B2B.pack_kg(w, 60.0, e)["kg"] for w in (10, 9, 8, 7, 6, 3)] == [3.0, 3.0, 6.0, 6.0, 9.0, 9.0]
    assert B2B.pack_kg(4, 70.0, e)["kg"] == 9.0                              # 10.5 kg → the 9 kg trip pack
    assert B2B.pack_kg(4, 70.0, {**e, "pack_kg": 7.0})["kg"] == 7.0          # the event's own pack
    assert B2B.pack_kg(8, None, e)["kg"] is None and B2B.pack_kg(8, 60.0, {"kind": "race"}) is None


def _wk(long_day="2026-10-03", easy=("2026-09-29", "2026-10-01", "2026-10-04"), quality="2026-09-30"):
    ss = [{"id": "long", "kind": "long", "title": "長時間輕鬆（山路）", "minutes": 180, "tss": 180.0,
           "day": long_day, "done": False, "target": "功率 200 W", "detail": "", "source": ""},
          {"id": "long2", "kind": "long", "title": "B2B 第 2 天", "minutes": 120, "tss": 120.0, "day": None,
           "done": False, "target": "", "detail": "", "source": ""}]
    if quality:
        ss.append({"id": "quality", "kind": "quality", "title": "爬坡間歇 5×4 分", "minutes": 60, "day": quality,
                   "done": False})
    for i, d in enumerate(easy):
        ss.append({"id": f"easy{i + 1}", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": d, "done": False})
    ss.append({"id": "strength1", "kind": "strength", "title": "肌力", "minutes": 35, "day": "2026-09-29", "done": False})
    return ss


def test_place_day_two_the_day_after_and_easy_swapped_away():
    ss = _wk()
    ss[1]["day"] = "2026-09-28"                     # the generic placement put it on the first free day
    kept = B2B.place(ss, MON, MON, set(), None, [])
    by = {s["id"]: s["day"] for s in kept}
    assert by["long"] == "2026-10-03" and by["long2"] == "2026-10-04"
    assert by["easy3"] == "2026-09-28"              # Sunday's easy run took the day long2 left
    assert len({by[k] for k in ("easy1", "easy2", "easy3", "long", "long2", "quality")}) == 6


def test_place_falls_back_to_the_day_before_and_drops_without_two_days():
    notes: list = []
    blocked = {"2026-10-04"}                        # Sunday 不排課
    kept = B2B.place(_wk(), MON, MON, blocked, None, notes)
    by = {s["id"]: s["day"] for s in kept}
    assert (by["long"], by["long2"]) == ("2026-10-02", "2026-10-03")
    # 課表偏好: only Wed and Sat allowed → no two consecutive days: day 2 dropped, the long run stays
    allowed = lambda d: d.weekday() in (2, 5)
    kept = B2B.place(_wk(), MON, MON, set(), allowed, notes)
    assert not any(s["id"] == "long2" for s in kept) and any(s["id"] == "long" for s in kept)
    assert any("連續 2 天" in n["text"] for n in notes)


def test_place_moves_quality_off_the_b2b_48h():
    ss = _wk(long_day="2026-10-01", quality="2026-09-29", easy=("2026-09-28", "2026-10-02", "2026-10-03"))
    kept = B2B.place(ss, MON, MON, set(), None, [])
    by = {s["id"]: s["day"] for s in kept}
    assert (by["long"], by["long2"]) == ("2026-10-01", "2026-10-02")
    q = dt.date.fromisoformat(by["quality"])
    assert all(abs((q - dt.date.fromisoformat(by[k])).days) >= 2 for k in ("long", "long2"))


def test_decorate_texts_targets_caps_and_sources():
    info = {"index": 2, "event": evj(), "days": 2, "weeks_out": 8}
    ss = _wk()
    B2B.decorate(ss, info, 142.0, long_cap=None, weight=62.0)
    l1, l2 = ss[0], ss[1]
    assert l1["title"].startswith("B2B 第 1 天｜") and l2["title"].startswith("B2B 第 2 天｜")
    assert l1["target"] == l2["target"] == "心率 ≤ AeT 142 bpm"           # HR only (trail)
    assert "30–60 g" in l1["detail"] and "Burke 2011" in l1["detail"]
    assert "6.2 kg" in l1["detail"] and "10%" in l1["detail"]                # week 8: 10 % of 62 kg
    assert "2/3" in l2["detail"] and "下坡" in l2["detail"]
    assert "推估" in l1["source"] and "Koop" in l1["source"]
    # a 課表偏好 long-day cap: day 2 ≤ the cap and ≤ 2/3 of a capped day 1
    ss = _wk()
    ss[0].update(minutes=150, tss=150.0)
    B2B.decorate(ss, info, 142.0, long_cap=150)
    assert ss[1]["minutes"] == 100 and ss[1]["tss"] == pytest.approx(100.0)        # day 1's TSS / min
    # the shaping turned the long run into a hike: day 2 follows
    ss = _wk()
    ss[0].update(kind="hike", terrain="hike")
    B2B.decorate(ss, info, 142.0)
    assert ss[1]["kind"] == "hike" and ss[1]["terrain"] == "hike"


# ---------------------------------------------------------------------------
# detection and the exception
# ---------------------------------------------------------------------------

def test_detect_consecutive_long_days():
    rows = [(date(2026, 9, 26), 200, 1), (date(2026, 9, 27), 130, 2), (date(2026, 9, 27), 30, 3),
            (date(2026, 9, 20), 120, 4), (date(2026, 9, 22), 100, 5)]
    d = B2B.detect(rows)
    assert len(d) == 1 and d[0]["start"] == "2026-09-26" and d[0]["idx"] == [1, 2] and d[0]["minutes"] == [200, 160]
    assert B2B.detect([(date(2026, 9, 26), 200, 1), (date(2026, 9, 27), 60, 2)]) == []


def test_post_b2b_week_and_tsb_exempt():
    c = ctx(last_recovery=False, post_from={"start": "2026-09-26", "end": "2026-09-27"})
    assert c["post"]["until"] == "2026-10-01"                               # 4 easy days
    assert "不改成恢復週" in B2B.tsb_exempt(c, -34.0)
    assert B2B.tsb_exempt(c, -34.0, ramp=9.0) is None                       # ramp beyond the limit
    assert B2B.tsb_exempt(c, -10.0) is None
    assert B2B.tsb_exempt({"due": True}, -31.0)
    assert B2B.tsb_exempt({}, -31.0) is None
    # an older B2B is not "just before this week"
    assert ctx(post_from={"start": "2026-09-19", "end": "2026-09-20"})["post"] is None


def _gw(day_quality="2026-10-02"):
    return [{"start": "2026-09-28", "mode": "specific", "provisional": False, "sessions": [
        {"id": "quality", "kind": "quality", "title": "爬坡間歇 5×4 分", "minutes": 60, "tss": 75.0,
         "day": day_quality, "done": False},
        {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": 50, "tss": 40.0, "day": "2026-10-01",
         "done": False}]}]


def _actx(**kw):
    c = {"today": "2026-09-30", "first_free": "2026-09-30", "blocked": {}, "allowed_days": None,
         "thresholds": {}, "mode": "specific", "load": {"tsb": -34.0}, "reviews": {}}
    c.update(kw)
    return c


def test_adapt_fatigue_guard_skips_the_expected_b2b_drop_and_logs_why():
    post = {"post": {"start": "2026-09-26", "end": "2026-09-27", "until": "2026-10-01"}}
    out, adj, _ = A.adapt(_gw(), [], _actx(b2b=post))
    assert [s["minutes"] for s in out[0]["sessions"]] == [60, 50]
    assert any(a["rule"] == "b2b" and a["action"] == "note" and "預期" in a["reason"] for a in adj)
    # without the B2B the same TSB cuts the week
    out, adj, _ = A.adapt(_gw(), [], _actx())
    assert not any(s["kind"] == "quality" for s in out[0]["sessions"])
    # after the easy days: no exception any more
    out, adj, _ = A.adapt(_gw(), [], _actx(b2b=post, today="2026-10-02", first_free="2026-10-02"))
    assert not any(s["kind"] == "quality" for s in out[0]["sessions"])
    # beyond the expected drop: the ramp still acts
    out, adj, _ = A.adapt(_gw(), [], _actx(b2b=post, load={"tsb": -34.0, "ramp": 9.0}))
    assert not any(s["kind"] == "quality" for s in out[0]["sessions"])


# ---------------------------------------------------------------------------
# the generator end to end (synthetic athlete, synthetic 嘉明湖)
# ---------------------------------------------------------------------------

def _history(today, last_week="recovery"):
    """Build weeks (Tue 50 / Wed 60 / Thu 50 / Sat 150 / Sun 60), then last
    week a 3:1 recovery week or a B2B week (Sat 180 / Sun 120) after one."""
    import types
    from backend.tests.test_workout_review import SETTINGS
    from backend.tests.wko5_fakes import FakeDataset
    monday = today - dt.timedelta(days=today.weekday())
    last = monday - dt.timedelta(days=7)
    ws, d = [], date(2026, 8, 3)
    while d < today:
        if d >= monday:
            m = 45 if d.weekday() == 1 else 0
        elif d >= last and last_week == "recovery":
            m = {1: 40, 3: 40, 5: 80}.get(d.weekday(), 0)
        elif d >= last:
            m = {1: 45, 2: 50, 3: 45, 5: 180, 6: 120}.get(d.weekday(), 0)
        elif d >= last - dt.timedelta(days=7) and last_week == "b2b":
            m = {1: 40, 3: 40, 5: 80}.get(d.weekday(), 0)
        else:
            m = {1: 50, 2: 60, 3: 50, 5: 150, 6: 60}.get(d.weekday(), 0)
        if m:
            w = _run(d, minutes=m, power=180.0)
            hard_b2b = last_week == "b2b" and last <= d < monday and d.weekday() >= 5
            w.metrics["tss"] = m * (1.3 if hard_b2b else 0.8)       # mountain B2B days: a higher TSS / h
            ws.append(w)
        d += dt.timedelta(days=1)
    ds = FakeDataset(ws, today, settings=SETTINGS)
    ds.aethr = lambda w: 0.89 * 160.0
    ds.cp = lambda w: 250.0
    ds.mftp_run = None
    ds.config = types.SimpleNamespace(parity=True)
    return ds


def _plan_with(event_start, days, today):
    from backend.engine.planning import Weight
    plan = Plan()
    plan.thresholds.append(Threshold("2026-08-01", lthr=165, cp=250.0))
    plan.events.append(Event(id="e1", name="嘉明湖", date=event_start, kind="baiyue", days=days))
    plan.weights.append(Weight("2026-08-01", 62.0))
    return plan


def _week(event_start="2026-12-05", days=2, today=TODAY, last_week="recovery", prefs=None):
    ds = _history(today, last_week)
    plan = _plan_with(event_start, days, today)
    ds.plan = plan
    st = Status(ds, plan, today, prefs=PP.Prefs()).compute()
    return ds, plan, st, O.week_plan(ds, st, today, prefs=prefs)


def _phases(plan, today):
    from backend.engine import planning
    return [{"kind": p.kind, "start": p.start, "end": p.end}
            for p in planning.phases(plan, today - dt.timedelta(days=400), today + dt.timedelta(days=400))]


def test_week_plan_schedules_b2b_after_the_recovery_week_volume_unchanged():
    _, _, _, wp = _week()
    assert wp["phase"] == "specific" and wp["b2b"]["due"] and wp["b2b"]["days"] == 2
    by = {s["id"]: s for s in wp["sessions"]}
    assert (by["long"]["day"], by["long2"]["day"]) == ("2026-10-03", "2026-10-04")       # Sat + Sun
    assert by["long2"]["minutes"] == pytest.approx(by["long"]["minutes"] * 0.67, abs=5)
    assert by["long"]["title"].startswith("B2B 第 1 天") and "30–60 g" in by["long"]["detail"]
    assert by["long"]["target"].startswith("心率 ≤ AeT")
    assert wp["b2b"]["weeks_out"] == 10 and "3.1 kg" in by["long"]["detail"]         # week 10: 5 % of 62 kg
    # Koop: the week's total is the same as without B2B (day 2 came out of the easy runs)
    main = sum(s["minutes"] for s in wp["sessions"] if s["kind"] not in ("strength",))
    assert main == pytest.approx(wp["target"]["hours"] * 60, abs=30)
    # no B2B for a short single-day race
    _, _, _, short = _week(days=1)
    assert not short["b2b"]["candidate"] and not any(s["id"] == "long2" for s in short["sessions"])


def test_week_plan_three_day_block_and_weekday_cap_falls_back_to_two_days():
    _, _, _, wp = _week(event_start="2026-11-07", days=3)
    assert wp["b2b"]["days"] == 3
    days = sorted(s["day"] for s in wp["sessions"] if s["id"] in ("long", "long2", "long3"))
    assert days == ["2026-10-02", "2026-10-03", "2026-10-04"]                         # Fri–Sun
    # 課表偏好 平日上限 50: the Friday can't hold a B2B day → 2 days + 「請一天假」
    _, _, _, wp = _week(event_start="2026-11-07", days=3, prefs=PP.Prefs(cap_weekday=50, cap_long=300))
    ids = {s["id"]: s["day"] for s in wp["sessions"] if s["id"] in ("long", "long2", "long3")}
    assert set(ids) == {"long", "long2"} and ids["long"] == "2026-10-03"
    assert wp["b2b"]["days"] == 2 and not wp["b2b"].get("three_done")
    assert any("請一天假" in n["text"] for n in wp["notes"])


def test_week_plan_respects_preferred_weekdays():
    # only Tue / Thu / Sun allowed: no two consecutive days → an ordinary long day, with a note
    _, _, _, wp = _week(prefs=PP.Prefs(days=(False, True, False, True, False, False, True)))
    assert not any(s["id"] == "long2" for s in wp["sessions"]) and not wp["b2b"]["due"]
    assert any(n.get("src") == "b2b" for n in wp["notes"])


def test_week_after_b2b_easy_days_no_recovery_week():
    today = date(2026, 10, 7)
    _, _, _, wp = _week(today=today, last_week="b2b")
    assert wp["b2b"]["post"]["until"] == "2026-10-08"
    assert wp["load"]["tsb_today"] < -20                           # the planned drop
    assert wp["mode"] == "specific"                                # not converted to a recovery week
    assert any("B2B" in w and "不改成恢復週" in w for w in wp["why"])
    assert not any(s["kind"] in ("quality", "test") for s in wp["sessions"])
    assert any(n.get("src") == "b2b" for n in wp["notes"])


def test_projection_sample_plan_for_a_three_day_trip():
    """The specific block before a 3-day 嘉明湖 on 12/06: B2B on the first build week after
    the recovery week, the 3-day once 4–6 weeks out, none in the last 3 weeks, easy days after."""
    today = TODAY
    ds, plan, st, cur = _week(event_start="2026-12-06", days=3, today=today)
    weeks = P.project_weeks(cur, _phases(plan, today), date(2026, 12, 7))
    rows = [(cur["week"]["start"], cur["mode"], cur.get("b2b") or {})] + \
        [(w["start"], w["mode"], w.get("b2b") or {}) for w in weeks]
    b2b_weeks = [(s, b["days"]) for s, _, b in rows if b.get("due")]
    assert b2b_weeks[0] == ("2026-09-28", 2)
    assert any(n == 3 for _, n in b2b_weeks)                                    # the 3-day block
    ev_start = date(2026, 12, 6)
    for s, n in b2b_weeks:
        sunday = date.fromisoformat(s) + dt.timedelta(days=6)
        assert (ev_start - sunday).days >= 21
        if n == 3:
            assert 28 <= (ev_start - sunday).days <= 42
    for i, (s, mode, b) in enumerate(rows[:-1]):
        if b.get("due"):
            nxt = rows[i + 1]
            assert nxt[1] != "recovery_week" and nxt[2].get("post")              # 3:1 continues
            w = next(w for w in weeks if w["start"] == nxt[0])
            assert not any(x["kind"] in ("quality", "test") for x in w["sessions"])
    # each B2B week: the B2B days consecutive, day 2 ≈ 2/3 of day 1
    for w in weeks:
        if (w.get("b2b") or {}).get("due"):
            ss = sorted((x for x in w["sessions"] if x["id"] in B2B.FOLLOWERS + ("long",)), key=lambda x: x["day"])
            ds_ = [date.fromisoformat(x["day"]) for x in ss]
            assert all((b - a).days == 1 for a, b in zip(ds_, ds_[1:])) and ss[0]["id"] == "long"


# ---------------------------------------------------------------------------
# evaluation: day 2 vs day 1 (no RPE)
# ---------------------------------------------------------------------------

AET = 142.0


def _rows(hr_shift=0.0, vams=None, grade=0.15, hr_fn=None):
    """100 m climbing windows; HR = 90 + 0.08 × VAM (+ shift) unless hr_fn."""
    vams = list(vams if vams is not None else np.linspace(300, 900, 40))
    out = []
    for k, vam in enumerate(vams):
        v = vam / (grade * 3600.0)
        hr = hr_fn(vam) if hr_fn else 90.0 + 0.08 * vam + hr_shift
        out.append({"k": k, "g": grade, "v": v, "hr": hr, "z": 1000 + 15.0 * k})
    return out


def test_classify_the_2x2():
    assert B2B.classify(-5.0, 0.90) == "muscular"        # low HR + slow
    assert B2B.classify(+5.0, 0.90) == "cardio"          # high HR + slow
    assert B2B.classify(0.0, 0.90) == "cardio"           # similar HR + slow (doc: same column)
    assert B2B.classify(+1.0, 0.98) == "durable"         # similar
    assert B2B.classify(-5.0, 1.00) == "uncommon"
    assert B2B.classify(None, 0.9) == B2B.classify(-5.0, None) == "insufficient"
    assert B2B.CELLS["durable"][0] == "耐久性好" and B2B.CELLS["muscular"][0] == "肌肉疲勞"


def test_evaluate_durable_cardio_and_too_few_climbs():
    r = B2B.evaluate(_rows(), _rows(), AET)
    assert r["cell"] == "durable" and r["hr_shift_bpm"] == pytest.approx(0.0, abs=0.1)
    assert r["vam_ratio"] == pytest.approx(1.0, abs=0.01) and r["thresholds"]["label"] == "推估"
    r = B2B.evaluate(_rows(), _rows(hr_shift=6.0), AET)       # same VAM needs 6 bpm more
    assert r["cell"] == "cardio" and r["hr_shift_bpm"] == pytest.approx(6.0, abs=0.1) and r["vam_ratio"] < 0.95
    # muscular: most of day 2 is slow and low (HR held down), the few AeT-band climbs are slower
    slow = _rows(vams=np.linspace(300, 400, 20), hr_fn=lambda v: 84.0 + 0.08 * v) + \
        [dict(x, k=x["k"] + 30) for x in _rows(vams=np.linspace(450, 500, 8), hr_fn=lambda v: 135.0)]
    r = B2B.evaluate(_rows(), slow, AET)
    assert r["cell"] == "muscular" and r["hr_shift_bpm"] <= -3 and r["vam_ratio"] < 0.95
    r = B2B.evaluate(_rows(vams=[500, 600]), _rows(), AET)                    # < 5 day-1 climbs
    assert r["cell"] == "insufficient" and r["level"] == "na"


def test_climb_windows_filter_and_day_stats():
    rows = _rows(vams=[500] * 6)
    rows[2]["g"] = 0.05                                       # breaks the run: 2 + 3 windows left
    w = B2B.climb_windows(rows)
    assert [x["k"] for x in w] == [3, 4, 5]
    st = B2B.day_stats(_rows(hr_shift=0.0), AET)
    assert 0 < st["over_aet_share"] < 1 and st["climb_m"] > 0


def test_trend_across_b2b_weekends():
    t = B2B.trend([{"cell": "cardio", "vam_ratio": 0.88}, {"cell": "durable", "vam_ratio": 0.97}])
    assert t["direction"] == "better" and t["durable_share"] == 0.5 and "進步" in t["text"]
    assert B2B.trend([{"cell": "durable", "vam_ratio": 0.97}])["direction"] is None
    assert B2B.trend([{"cell": "durable", "vam_ratio": 0.97}, {"cell": "muscular", "vam_ratio": 0.90}])["direction"] == "worse"


def _climb_day(day, minutes, hr_shift=0.0):
    """A synthetic mountain day: a 20 % climb at 2.5–3.5 km/h (20-min waves), HR from VAM."""
    n = minutes * 60
    t = np.arange(n, dtype=float)
    kmh = 3.0 + 0.5 * np.sin(2 * np.pi * t / 1200.0)
    d = np.cumsum(kmh / 3.6)
    z = 500.0 + 0.20 * d
    vam = kmh / 3.6 * 0.20 * 3600.0
    hr = 90.0 + 0.08 * vam + hr_shift
    ch = {"elapsedtime": list(t), "elapseddistance": list(d / 1000.0), "speed": list(kmh),
          "elevation": list(z), "heartrate": list(hr)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running", "runningtrail"],
                       sport_type="trail running", channels=ch,
                       metrics={"duration": n, "movingduration": n, "distance": d[-1] / 1000.0, "tss": minutes * 1.0,
                                "climbing": float(z[-1] - z[0])})


def test_card_reads_every_done_b2b_weekend_and_the_trend():
    from backend.tests.test_workout_review import SETTINGS
    from backend.tests.wko5_fakes import FakeDataset
    ws = [_climb_day(date(2026, 9, 12), 150), _climb_day(date(2026, 9, 13), 100, hr_shift=6.0),
          _climb_day(date(2026, 9, 26), 150), _climb_day(date(2026, 9, 27), 100, hr_shift=0.5)]
    ds = FakeDataset(ws, TODAY, settings=SETTINGS)
    phase = type("Ph", (), {"kind": "specific", "start": "2026-09-01", "end": "2026-10-30"})()
    c = B2B.card(ds, TODAY, [ev()], phase, None, [], aet_of=lambda d: AET)
    assert c["active"] and c["event"]["qualifies"] and len(c["done"]) == 2
    assert [d["eval"]["cell"] for d in c["done"]] == ["cardio", "durable"]
    assert c["trend"]["direction"] == "better"
    assert c["cells"]["durable"]["label"] == "耐久性好"


def test_adapt_red_streak_still_acts_during_a_b2b():
    red = [{"uid": f"r{i}", "state": "done", "kind": "easy", "day": f"2026-09-2{i}", "minutes": 60, "tss": 40.0,
            "done_by": {"index": i, "moving_s": 600, "tss": 5.0, "category": "road"}} for i in (8, 9)]
    out, adj, _ = A.adapt(_gw(), red, _actx(b2b={"due": True}))
    q = next(s for s in out[0]["sessions"] if s["id"] == "quality")
    assert q["title"] != "爬坡間歇 5×4 分"                                   # downgraded to the recovery fartlek
