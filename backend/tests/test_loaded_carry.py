"""負重訓練（engine/loaded_carry.py; docs/research/loaded-carry-training.md):
the trip pack on the event, the pack per activity (racepower_hike_meta.json,
the activity-tags API), the schedule (stages, frequency, caps, B2B, taper,
ME), the projection over a synthetic 3-day 百岳 and the evaluation. Synthetic
data only — never the WKO5 folder, the app DB, the user's plan or COROS."""
import asyncio
import datetime as dt
from datetime import date

import numpy as np
import pytest

from backend.engine import loaded_carry as LC
from backend.engine.planning import Event, Plan
from backend.engine.racepower import athlete as A
from backend.engine.racepower import hikehr as HH


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def test_event_pack_kg_default_and_validation():
    e = Event(id="x", name="嘉明湖", date="2026-12-05", kind="baiyue", days=3)
    assert e.pack_kg is None and e.pack == 9.0
    plan = Plan()
    ev = plan.upsert_event({"name": "嘉明湖", "date": "2026-12-05", "kind": "baiyue", "days": 3, "pack_kg": "11.5"})
    assert ev.pack_kg == 11.5 and ev.pack == 11.5
    assert plan.upsert_event({"name": "b", "date": "2026-12-05", "pack_kg": ""}).pack_kg is None
    with pytest.raises(ValueError):
        plan.upsert_event({"name": "c", "date": "2026-12-05", "pack_kg": 55})


def test_event_pack_kg_round_trips_through_the_plan_file(tmp_path):
    plan = Plan()
    plan.upsert_event({"id": "e1", "name": "嘉明湖", "date": "2026-12-05", "kind": "baiyue", "days": 3, "pack_kg": 10})
    p = tmp_path / "plan.json"
    plan.save(p)
    assert Plan.load(p).events[0].pack_kg == 10.0


def test_trip_and_stage_weights():
    assert LC.trip_kg({"days": 3}) == 9.0 and LC.trip_kg({"days": 3, "pack_kg": 7.0}) == 7.0
    assert LC.stage_kgs(70.0, 9.0) == [3.5, 7.0, 9.0]                   # 5 %, 10 %, the trip pack
    assert LC.stage_kgs(68.0, 9.0) == [3.4, 6.8, 9.0]
    assert LC.stage_kgs(68.0, 3.0) == [3.0, 3.0, 3.0]                   # a trip pack < 5 %: the steps collapse
    assert LC.stage_kgs(None, 9.0) == [None, None, 9.0]
    assert LC.machine_cap(68.0, 9.0) == 10.3                            # 1.15 × 9 = 10.35 → 10.3 (a cap: down) ≤ 13.6
    assert LC.machine_cap(68.0, 10.0) == 11.5                           # 1.15 × 10
    assert LC.machine_cap(45.0, 9.0) == 9.0                             # 20 % of 45 kg = 9.0 wins
    assert LC.machine_cap(40.0, 9.0) == 9.0                             # never below the trip pack
    kgs = [3.5, 7.0, 9.0]
    assert [LC.stage_done(k, kgs) for k in (0, 2.0, 3.0, 5.7, 7.2, 9.0)] == [0, 0, 1, 2, 3, 3]


def test_hikehr_filter_windows_hr_band():
    rows = [{"k": k, "g": 0.2, "v": 0.8, "hr": hr} for k, hr in enumerate([120, 128, 130, 135, 140, 146, 148, 150])]
    assert [w["hr"] for w in HH.filter_windows(rows, 142.0)] == [146, 148, 150]       # the 百岳 filter: ≥ AeT
    band = HH.filter_windows(rows, 142.0, hr_band=LC.TRAIN_HR_BAND)                   # AeT − 15 … AeT + 3
    assert [w["hr"] for w in band] == [128, 130, 135, 140]


class _W:
    def __init__(self, file, start=dt.datetime(2026, 9, 26, 7)):
        self.entry = type("E", (), {"file": file, "start": start})()


def test_activity_pack_reads_and_writes_the_hike_meta_file():
    w = _W("coros/2026/a.fit")
    p = LC.activity_pack(w, planned=7.0)
    assert p["pack_kg"] is None and not p["recorded"] and p["default_kg"] == 7.0
    A.set_hike_meta(w.entry.file, 6.5)
    p = LC.activity_pack(w)
    assert p["pack_kg"] == 6.5 and p["recorded"] and p["range"] == [0, 40]
    assert LC.meta_pack_of()(w) == 6.5
    with pytest.raises(ValueError):
        A.set_hike_meta(w.entry.file, 41)


def test_activity_tags_api_patch_pack_kg_only(monkeypatch):
    from fastapi import HTTPException
    from backend.api import wko5views as V
    w = _W("fake/0.wko4")
    ds = type("DS", (), {"workouts": [w]})()
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    monkeypatch.setattr(V, "_activity_json", lambda ds_, w_: {"pack": V._pack_json(w_)})

    async def save(*a, **k):                                    # the tag DB is never touched for a pack edit
        raise AssertionError("no tag write")
    import backend.api.workouts as WK
    monkeypatch.setattr(WK, "save_activity_tag", save)
    run = lambda c: asyncio.new_event_loop().run_until_complete(c)
    r = run(V.patch_activity(0, {"pack_kg": 9}))
    assert r["pack"]["pack_kg"] == 9.0 and A.hike_meta()["fake/0.wko4"]["pack_kg"] == 9.0
    r = run(V.patch_activity(0, {"pack_kg": None}))
    assert r["pack"]["pack_kg"] is None
    with pytest.raises(HTTPException):
        run(V.patch_activity(0, {"pack_kg": 99}))


def test_planned_kg_from_the_title():
    assert LC.planned_kg_of_title("長時間輕鬆（山路） · 背 7 kg") == 7.0
    assert LC.planned_kg_of_title("背包爬坡機 · 背 10.5 kg") == 10.5
    assert LC.planned_kg_of_title("輕鬆跑") is None


# ---------------------------------------------------------------------------
# when: stages, ME, blocks
# ---------------------------------------------------------------------------

MON = date(2026, 9, 28)
W = 70.0                                    # 5 % = 3.5 kg, 10 % = 7 kg


def ev_w(w, days=3, kind="baiyue", pack=None, monday=MON):
    """An event `w` weeks (賽前第 w 週) after `monday`."""
    start = monday + dt.timedelta(days=7 * w - 1)
    return {"id": "e1", "name": "嘉明湖", "start": start.isoformat(), "days": days, "kind": kind, "pack_kg": pack,
            "is_long": days > 1, "est_hours": None}


def row(day, kg, long=True, stage=None):
    return {"day": day, "kg": kg, "long": long, "stage": stage if stage is not None else LC.stage_done(kg, [3.5, 7.0, 9.0])}


def ctx(w=8, kind="specific", mode="specific", loaded=(), me_n=0, **kw):
    base = dict(kind=kind, mode=mode, monday=MON, event=ev_w(w), weight=W, phase=None,
                state={"loaded": list(loaded), "me_n": me_n}, tsb=-5.0, me_ok=True)
    base.update(kw)
    return LC.week_context(**base)


def test_only_before_a_baiyue_or_multi_day_a_event():
    assert not ctx(event=ev_w(8, days=1, kind="race"))["active"]
    assert ctx(event=ev_w(8, days=1, kind="baiyue"))["active"]
    assert ctx(event=ev_w(8, days=2, kind="race"))["active"]
    assert not ctx(event=None)["active"]


def test_stage_by_weeks_out_and_stairstep():
    done = [row("2026-09-12", 3.5), row("2026-09-19", 7.0)]
    assert [ctx(w, loaded=done)["step"] for w in (10, 9, 8, 7, 6, 3)] == [1, 1, 2, 2, 3, 3]
    assert [ctx(w, loaded=done)["stage_kg"] for w in (10, 8, 6)] == [3.5, 7.0, 9.0]
    # stage 1 never done → week 8 stays at 5 % (UA stairstep): no jump
    c = ctx(8)
    assert c["step"] == 1 and any("不跳級" in x for x in c["why"])
    c = ctx(6, loaded=[row("2026-09-19", 3.5)])
    assert c["step"] == 2
    # the trip pack caps every stage
    assert ctx(6, loaded=done, event=ev_w(6, pack=6.0))["stage_kg"] == 6.0


def test_manual_specific_phase_uses_quarters():
    ph = {"kind": "specific", "start": "2026-09-27", "end": "2026-11-21", "auto": False}      # 8 weeks
    done = [row("2026-09-12", 3.5), row("2026-09-19", 7.0)]
    steps = [ctx(12, loaded=done, phase=ph, monday=MON + dt.timedelta(weeks=i),
                 event=ev_w(12 - i, monday=MON + dt.timedelta(weeks=i)))["step"] for i in (0, 1, 2, 3, 4, 7)]
    assert steps == [1, 1, 2, 2, 3, 3]


def test_base_late_me_only_and_the_me_prerequisite():
    c = ctx(12, kind="base", mode="base")
    assert c["step"] == "me" and c["me"] == {"n": 0, "weight": W}
    assert ctx(16, kind="base", mode="base")["step"] is None                    # early base: nothing
    c = ctx(12, kind="base", mode="base", me_ok=False, me_why="沒有實測 AeT")
    assert c["me"] is None and "沒有實測 AeT" in c["why"]
    assert ctx(6)["me"] is not None and ctx(3)["me"] is None                    # ME stops 3 weeks out


def test_recovery_week_reentry_and_low_tsb_carry_nothing():
    assert ctx(mode="recovery_week")["step"] is None and ctx(mode="reentry")["step"] is None
    c = ctx(tsb=-24.0)
    assert c["hold"] and any("TSB" in b for b in c["blocked"])


# ---------------------------------------------------------------------------
# the sessions
# ---------------------------------------------------------------------------

def _ss(long_day="2026-10-03", easy=("2026-09-28", "2026-09-29", "2026-10-01", "2026-10-02"),
        quality="2026-09-30", long_min=180):
    ss = [{"id": "long", "kind": "long", "title": "長時間輕鬆（山路）", "minutes": long_min, "tss": float(long_min),
           "day": long_day, "done": False, "target": "心率 ≤ AeT 142 bpm", "detail": "挑爬升多的路線", "source": "Koop"}]
    if quality:
        ss.append({"id": "quality", "kind": "quality", "title": "爬坡間歇 5×4 分", "minutes": 60, "tss": 75.0,
                   "day": quality, "done": False})
    for i, d in enumerate(easy):
        ss.append({"id": f"easy{i + 1}", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "tss": 40.0, "day": d,
                   "done": False, "target": "", "detail": "", "source": ""})
    ss.append({"id": "strength1", "kind": "strength", "title": "肌力", "minutes": 35, "tss": 15.0,
               "day": "2026-09-29", "done": False, "detail": "", "source": ""})
    return ss


def _main(ss):
    return sum(s["minutes"] for s in ss if s["kind"] != "strength")


def test_loaded_long_day_first_time_at_a_new_weight_is_shorter():
    info = ctx(8, loaded=[row("2026-09-12", 3.5)])
    ss = LC.apply(_ss(), info, aet=142.0)
    L = ss[0]
    assert L["pack_kg"] == 7.0 and L["title"].endswith("· 背 7 kg")
    assert L["pack_from"] == 180 and L["minutes"] == 135 and L["tss"] == pytest.approx(135.0)
    assert "背著下山" in L["detail"] and "UA 一次只加一樣" in L["detail"] and "Orr 2021" in L["source"]
    assert not any(s["id"] == "carry" for s in ss)                            # a long-day week: no machine
    assert [p["kg"] for p in info["planned"]] == [7.0]
    # the second time at 7 kg: the full long day
    info = ctx(8, loaded=[row("2026-09-12", 3.5), row("2026-09-19", 7.0)])
    L = LC.apply(_ss(), info, aet=142.0)[0]
    assert L["minutes"] == 180 and not L.get("pack_from")
    # stage 1: the water can be poured out, the pack isn't carried down
    L = LC.apply(_ss(), ctx(10), aet=142.0)[0]
    assert L["pack_kg"] == 3.5 and "倒掉" in L["detail"] and "背著下山" not in L["detail"]


def test_spacing_gives_the_weekday_machine_session_volume_unchanged():
    info = ctx(8, loaded=[row("2026-09-12", 3.5), row("2026-09-26", 7.0)])      # 7 days before Sat 10/03
    ss0 = _ss()
    total = _main(ss0)
    ss = LC.apply(ss0, info, aet=142.0)
    assert ss[0].get("pack_kg") is None and "不到 10 天" in info["week"]["long_blocked"]
    c = next(s for s in ss if s["id"] == "carry")
    assert c["kind"] == "hike" and c["terrain"] == "hike" and c["pack_kg"] == 7.0 and c["minutes"] == 50
    assert c["day"] == "2026-09-28"                         # ≥ 2 days from the quality (Wed) and the long day (Sat)
    assert c["target"] == "心率 ≤ AeT 142 bpm" and "樓梯機" in c["detail"] and "降速度、不降坡度" in c["detail"]
    assert _main(ss) == total                               # the easy runs gave the 5 minutes back


def test_machine_session_fits_the_weekday_cap():
    from backend.engine import plan_prefs as PP
    info = ctx(8, loaded=[row("2026-09-12", 3.5), row("2026-09-26", 7.0)])
    c = next(s for s in LC.apply(_ss(), info, aet=142.0, prefs=PP.Prefs(cap_weekday=45)) if s["id"] == "carry")
    assert c["minutes"] == 45
    info = ctx(8, loaded=[row("2026-09-12", 3.5), row("2026-09-26", 7.0)])
    ss = LC.apply(_ss(), info, aet=142.0, prefs=PP.Prefs(cap_weekday=25))
    assert not any(s["id"] == "carry" for s in ss)          # < 30 min on every weekday


def test_at_most_four_loaded_sessions_per_28_days():
    four = [row(d, 7.0, long=False) for d in ("2026-09-08", "2026-09-12", "2026-09-17", "2026-09-22")]
    info = ctx(8, loaded=[row("2026-09-01", 3.5)] + four)
    notes = []
    ss = LC.apply(_ss(), info, aet=142.0, notes=notes)
    assert ss[0].get("pack_kg") is None and not any(s["id"] == "carry" for s in ss)
    assert "28 天" in info["week"]["long_blocked"]
    assert any("28 天" in n["text"] for n in notes)


def test_b2b_day_one_is_the_loaded_long_day_and_no_machine_that_week():
    ss = _ss()
    ss.insert(1, {"id": "long2", "kind": "long", "title": "B2B 第 2 天｜長時間輕鬆", "minutes": 120, "tss": 120.0,
                  "day": "2026-10-04", "done": False, "detail": "B2B 第 2 天：…背包和第 1 天一樣或更輕。", "source": ""})
    ss[0]["detail"] = "B2B 第 1 天（共 2 天）：…百岳背包：賽前第 6 週，第 1 天背 9 kg；UA … （對應週數依 loaded-carry-training.md，推估）。"
    info = ctx(6, loaded=[row("2026-09-05", 3.5), row("2026-09-12", 7.0)])
    ss = LC.apply(ss, info, aet=142.0, b2b={"due": True})
    by = {s["id"]: s for s in ss}
    assert by["long"]["pack_kg"] == by["long2"]["pack_kg"] == 9.0
    assert by["long"]["minutes"] == 180                      # a B2B day 1 is never cut (b2b.py sets its minutes)
    assert "百岳背包：" not in by["long"]["detail"] and "背包和第 1 天一樣：9 kg" in by["long2"]["detail"]
    assert not any(s["id"] == "carry" for s in ss) and len(info["planned"]) == 2


def test_machine_overload_caps_and_the_long_day_never_above_the_trip_pack():
    done = [row("2026-08-29", 3.5), row("2026-09-05", 7.0), row("2026-09-12", 9.0)]
    info = ctx(4, loaded=done + [row("2026-09-26", 9.0)])
    assert info["stage_kg"] == 9.0 and info["machine_kg"] == 10.3            # 1.15 × 9 → 10.3 ≤ 20 % × 70
    c = next(s for s in LC.apply(_ss(), info, aet=142.0) if s["id"] == "carry")
    assert c["pack_kg"] == 10.3 and "1.15 倍" in c["detail"]
    assert ctx(4, loaded=done, weight=48.0)["machine_kg"] == 9.6            # 20 % of 48 kg
    assert ctx(6, loaded=done)["machine_kg"] == 9.0                          # not yet: weeks 5–3 only
    L = LC.apply(_ss(), ctx(4, loaded=done), aet=142.0)[0]
    assert L["pack_kg"] == 9.0


def test_last_loaded_long_day_two_weeks_before_the_trip():
    done = [row("2026-08-29", 3.5), row("2026-09-05", 7.0), row("2026-09-12", 9.0)]
    ev = {**ev_w(3), "start": "2026-10-16"}                                   # Sat 10/03 is 13 days before
    info = ctx(3, loaded=done, event=ev)
    ss = LC.apply(_ss(), info, aet=142.0)
    assert ss[0].get("pack_kg") is None and "14 天" in info["week"]["long_blocked"]


def test_taper_one_short_carry_8_to_14_days_out_and_nothing_in_the_last_7():
    ev = {**ev_w(2), "start": "2026-10-10"}                                   # Mon 9/28 is 12 days before
    info = ctx(2, kind="taper", mode="taper", event=ev, loaded=[row("2026-09-19", 9.0)])
    ss = LC.apply(_ss(), info, aet=142.0)
    c = [s for s in ss if s["id"] == "carry"]
    assert len(c) == 1 and c[0]["minutes"] == 30 and c[0]["pack_kg"] == 9.0 and c[0]["day"] == "2026-09-28"
    assert ss[0].get("pack_kg") is None
    # the previous loaded session 3 days before Monday: the carry waits ≥ 5 days
    info = ctx(2, kind="taper", mode="taper", event=ev, loaded=[row("2026-09-25", 9.0, long=False)])
    c = [s for s in LC.apply(_ss(), info, aet=142.0) if s["id"] == "carry"]
    assert [x["day"] for x in c] == ["2026-10-01"]                          # 6 days after, 9 days before the trip
    # the trip in 6 days: nothing loaded at all
    ev = {**ev_w(1), "start": "2026-10-04"}
    info = ctx(1, kind="taper", mode="taper", event=ev)
    ss = LC.apply(_ss(), info, aet=142.0)
    assert not any(s.get("pack_kg") for s in ss) and any("7 天內" in t and "不背包" in t for t in info["week"]["notes"])


def test_a_done_easy_run_with_a_pack_becomes_the_done_machine_session():
    info = ctx(8, loaded=[row("2026-09-12", 3.5), row("2026-09-26", 7.0)])
    ss = _ss()
    ss[1 + 1].update(done=True, done_by={"index": 7})                          # easy1 (Mon) was done with 7 kg
    ss = LC.apply(ss, info, aet=142.0, week_packs={7: 7.0})
    c = [s for s in ss if s["id"] == "carry"]
    assert len(c) == 1 and c[0]["done"] and c[0]["pack_kg"] == 7.0 and c[0]["minutes"] == 45


def test_me_replaces_strength_with_johnstons_progression():
    for n, words in ((0, "休 60 秒"), (2, "休 45 秒"), (4, "背心 10% 體重（約 7 kg）"), (9, "背心 15% 體重")):
        s = {"id": "strength1", "kind": "strength", "title": "肌力", "detail": ""}
        LC._me_session(s, n, W)
        assert s["id"] == "me" and words in s["detail"] and "腿在燒" in s["detail"] and s["target"] == ""
    ss = LC.apply(_ss(), ctx(12, kind="base", mode="base", me_n=3), aet=142.0)
    me = next(s for s in ss if s["kind"] == "strength")
    assert me["id"] == "me" and me["title"] == "肌耐力（ME）第 4 次" and not any(s.get("pack_kg") for s in ss)


# ---------------------------------------------------------------------------
# the generator end to end (the synthetic athlete of test_b2b, a synthetic 3-day 嘉明湖)
# ---------------------------------------------------------------------------

def _week(event_start="2026-12-06", days=3, today=None, aethr=None, prefs=None, kind="baiyue"):
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.planning import Threshold
    from backend.engine.status import Status
    from backend.tests import test_b2b as TB
    today = today or TB.TODAY
    ds = TB._history(today, "recovery")
    plan = TB._plan_with(event_start, days, today)
    plan.events[0].kind = kind
    if aethr:
        plan.thresholds.append(Threshold("2026-08-02", aethr=aethr, aethr_method="manual"))
    ds.plan = plan
    st = Status(ds, plan, today, prefs=PP.Prefs()).compute()
    return ds, plan, st, O.week_plan(ds, st, today, prefs=prefs)


def test_week_plan_puts_the_stage_1_pack_on_the_b2b_weekend():
    _, _, _, wp = _week()
    lc = wp["loaded_carry"]
    assert lc["step"] == 1 and lc["weeks_out"] == 10 and lc["stage_kg"] == 3.1          # 5 % of 62 kg
    by = {s["id"]: s for s in wp["sessions"]}
    assert by["long"]["pack_kg"] == by["long2"]["pack_kg"] == 3.1 and by["long"]["title"].endswith("· 背 3.1 kg")
    assert "3.1 kg" in by["long"]["detail"] and "百岳背包：" not in by["long"]["detail"]
    assert not any(s["id"] == "carry" for s in wp["sessions"])                          # a B2B week
    assert by["long"]["target"].startswith("心率 ≤ AeT")
    assert any("ME" in x for x in lc["why"])                                            # no measured AeT: strength stays
    assert "strength1" in by


def test_week_plan_me_in_late_base_with_a_measured_aet():
    _, _, _, wp = _week(event_start="2026-12-27", aethr=152.0)                         # 賽前第 13 週, base
    lc = wp["loaded_carry"]
    assert wp["phase"] == "base" and lc["step"] == "me"
    me = next(s for s in wp["sessions"] if s["id"] == "me")
    assert me["kind"] == "strength" and "Johnston" in me["source"] and "6 組" in me["detail"]
    assert not any(s.get("pack_kg") for s in wp["sessions"])


def test_week_plan_without_a_qualifying_event_changes_nothing():
    _, _, _, wp = _week(days=1, kind="race")
    plan_ev = wp["loaded_carry"]
    assert not any(s.get("pack_kg") or s["id"] in ("carry", "me") for s in wp["sessions"])
    assert plan_ev is None or not plan_ev.get("step")


def _weeks(event_start="2026-12-06", days=3, aethr=152.0, prefs=None):
    from backend.engine import projection as P
    from backend.tests import test_b2b as TB
    ds, plan, st, cur = _week(event_start, days, aethr=aethr, prefs=prefs)
    weeks = P.project_weeks(cur, TB._phases(plan, TB.TODAY), date.fromisoformat(event_start) + dt.timedelta(days=1),
                            prefs=prefs)
    return cur, [{"start": cur["week"]["start"], "mode": cur["mode"], "sessions": cur["sessions"],
                  "loaded_carry": cur["loaded_carry"], "b2b": cur.get("b2b")}] + weeks


def _loaded(rows):
    return sorted(((date.fromisoformat(s["day"]), s, w) for w in rows for s in w["sessions"]
                   if s.get("pack_kg") and s.get("day")), key=lambda x: x[0])


def test_projection_over_a_three_day_trip_keeps_every_rule():
    """The specific block before a 3-day 嘉明湖 on 12/06 (synthetic): stages up, ≤ 4 loaded
    sessions per 28 days, loaded long days ≥ 10 days apart, no machine session in a B2B week,
    the long day ≤ the trip pack, machine ≤ 1.15 × trip and ≤ 20 % body weight, the last
    loaded long day ≥ 14 days out, nothing loaded in the last 7 days."""
    trip, start = 9.0, date(2026, 12, 6)
    cur, rows = _weeks()
    L = _loaded(rows)
    assert L, "no loaded session planned"
    days = [d for d, _, _ in L]
    for d in days:
        assert sum(1 for x in days if d - dt.timedelta(days=27) <= x <= d) <= LC.MAX_PER_28
    longs = [d for d, s, _ in L if s["id"] == "long"]
    assert all((b - a).days >= LC.LONG_SPACING_DAYS for a, b in zip(longs, longs[1:]))
    assert all((start - d).days >= LC.LAST_LONG_DAYS for d in longs)
    assert all((start - d).days > LC.NO_PACK_DAYS for d in days)
    for d, s, w in L:
        if s["id"] in ("long", "long2", "long3"):
            assert s["pack_kg"] <= trip
        else:
            assert s["pack_kg"] <= min(1.15 * trip, 0.20 * 62.0) + 1e-9
        if (w.get("b2b") or {}).get("due"):
            assert s["id"] != "carry"
    stages = [w["loaded_carry"]["step"] for w in rows if isinstance((w.get("loaded_carry") or {}).get("step"), int)]
    assert stages == sorted(stages) and stages[0] == 1 and stages[-1] == 3
    kgs = [s["pack_kg"] for _, s, _ in L if s["id"] == "long"]
    assert kgs == sorted(kgs) and kgs[-1] == trip
    # the taper's one short carry at the trip pack, 8–14 days out
    taper = [(d, s) for d, s, _ in L if s["title"].startswith("背包短課")]
    assert len(taper) <= 1 and all(8 <= (start - d).days <= 14 and s["minutes"] == 30 for d, s in taper)
    # ME on a strength day through week 4, never in the last 3 weeks (measured AeT 152 / LTHR 165 = 8.6 %)
    me_w = [weeks_out for w in rows for weeks_out in [(w.get("loaded_carry") or {}).get("weeks_out")]
            if any(s["id"] == "me" for s in w["sessions"])]
    assert me_w and min(me_w) >= 4


def test_projection_respects_preferred_weekdays_for_the_machine_session():
    from backend.engine import plan_prefs as PP
    prefs = PP.Prefs(days=(False, True, False, True, False, True, True))          # Tue / Thu / Sat / Sun
    cur, rows = _weeks(prefs=prefs)
    for _, s, _ in _loaded(rows):
        assert date.fromisoformat(s["day"]).weekday() in (1, 3, 5, 6)

