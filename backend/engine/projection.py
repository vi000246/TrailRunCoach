"""
Project the weekly plan past this week, up to a horizon (the phase end,
capped at MAX_WEEKS), so a whole training phase can be scheduled.

Structural companion to overview.week_plan(): it does not recompute anything
from samples. It starts from this week's week_plan() output (target hours,
TSS per hour, CTL, the 8-week history, long-session weekday, thresholds) and
rolls the same rules forward week by week:

  * base / specific: CTL ramp goal (load_guard.ramp_goal) capped at +10 % (at least
    +0.5 h) of max(4-week mean, last week) over normal weeks (no taper / race / 恢復期 /
    轉換期 week, SP-73); after 3 build weeks a recovery
    week at 65 % of those 3 (3:1)
  * taper 40–50 % of the 6-week mean, event week 30 %, recovery 50 %,
    transition 50 % of the 4 weeks before the race's taper (SP-73;
    overview.transition_hours), each easy run <= 60 min
  * the same session template: long, one quality session, strength, easy
    runs filling the rest

Everything beyond next week is provisional: reconcile recalculates it from
the real state as the weeks arrive.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Optional

from backend.engine import aet_test as AT
from backend.engine import b2b as B2B
from backend.engine import load_guard as LG
from backend.engine import specific_phase as SP
from backend.engine import steep_hill as SH
from backend.engine import overview as O
from backend.engine import quality_gate as QG
from backend.engine.hr_profile import below, easy_cap_label, easy_cap_measured
from backend.engine.zones import WORKOUT_TARGETS

MAX_WEEKS = 8                 # never schedule further ahead than this
MODE_LABELS = {"base": "基礎期", "specific": "專項期", "taper": "減量期", "event": "比賽週",
               "recovery": "恢復期", "transition": "轉換期", "recovery_week": "恢復週", "reentry": "停訓後恢復期"}


def _d(s) -> dt.date:
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def target_texts(th: dict) -> dict[str, str]:
    """Same strings as overview._targets(), from the thresholds in week_plan()['thresholds']."""
    cp, lthr, aet = th.get("cp"), th.get("lthr"), th.get("aet")
    rows = []
    for tid, name, plo, phi, hlo, hhi, primary, example, src in WORKOUT_TARGETS:
        def hr(x):
            if x is None or lthr is None:
                return None
            return aet if x == "aet" else x * lthr
        rows.append({"id": tid, "primary": primary,
                     "power": [None if plo is None or not cp else plo * cp, None if phi is None or not cp else phi * cp],
                     "hr": [hr(hlo), hr(hhi)]})
    return O._targets({"rows": rows})


def phase_kind(phases: list, day: dt.date) -> str:
    for p in phases:
        s, e = _d(p["start"] if isinstance(p, dict) else p.start), _d(p["end"] if isinstance(p, dict) else p.end)
        if s <= day <= e:
            return p["kind"] if isinstance(p, dict) else p.kind
    return "base"


def _phase_notes(phases: list, monday: dt.date) -> list[tuple[str, str]]:
    """planning.week_phase_notes: a shortened / skipped 轉換期 (SP-73), two A races close
    together (SP-90) — the phase holding Monday and the ones starting later that week."""
    from backend.engine import planning as PL
    return PL.week_phase_notes(phases, monday)


def _skip_week(phases: list, monday: dt.date) -> bool:
    """The week touches a 減量期 / race week / 恢復期 / 轉換期 (load_guard.STEP_SKIP_KINDS)."""
    return any(phase_kind(phases, monday + dt.timedelta(days=k)) in LG.STEP_SKIP_KINDS for k in range(7))


def _next_event_start(phases: list, day: dt.date) -> Optional[dt.date]:
    for p in phases:
        kind = p["kind"] if isinstance(p, dict) else p.kind
        s = _d(p["start"] if isinstance(p, dict) else p.start)
        if kind == "event" and s >= day:
            return s
    return None


def week_hours(kind: str, hist: list[float], build: list[bool], ctl0: float, r: float,
               cc: float, days_to_a: Optional[int], tr_ref: Optional[float] = None,
               cap_ref: Optional[float] = None) -> tuple[float, str, list[str]]:
    """(hours, mode, why) for one projected week; `hist` = weekly hours, oldest first.
    `tr_ref`: a 轉換期 week's pre-race level (overview.transition_ref; None = the old 65 % rule).
    `cap_ref`: the +10 % cap's reference on normal weeks (load_guard.step_base of the normal
    weeks, SP-73); None = max(4-week mean, last week) of `hist`."""
    base4 = statistics.mean(hist[-4:]) if hist else 0.0
    last = hist[-1] if hist else 0.0
    ref = cap_ref if cap_ref else max(base4, last)
    why: list[str] = []
    if kind in ("base", "specific"):
        if len(build) >= 3 and all(build[-3:]):
            h = 0.65 * statistics.mean(hist[-3:])
            return h, "recovery_week", ["連續 3 週加量後的恢復週（前 3 週平均的 65%）"]
        f7 = 1.0 - (1.0 - 1.0 / cc) ** 7
        goal = LG.ramp_goal(kind, ctl0)
        need_h = 7.0 * (ctl0 + goal / f7) / max(r, 1.0)
        cap = max(1.10 * ref, ref + 0.5)
        h = min(max(need_h, base4), cap)
        why.append(f"CTL {ctl0:.0f} 每週 +{goal:.1f}，上限 +10%（至少 +0.5 h）→ {h:.1f} h")
        return h, kind, why
    if kind == "taper":
        base6 = statistics.mean(hist[-6:]) if hist else 0.0
        share = 0.4 if days_to_a is not None and days_to_a <= 7 else 0.5
        return base6 * share, kind, [f"減量期：平常 {base6:.1f} h × {share:.0%}"]
    if kind == "event":
        return 0.3 * base4, kind, ["比賽週：短、輕鬆"]
    if kind == "transition":
        h, w = O.transition_hours(tr_ref, base4)
        return h, kind, [w]
    share = 0.5 if kind == "recovery" else 0.65
    return share * base4, kind, [f"{MODE_LABELS.get(kind, kind)}：近 4 週的 {share:.0%}"]


def week_sessions(monday: dt.date, kind: str, mode: str, hours: float, tph: float,
                  tgt: dict, long_wd: int, longest: float, mountain: bool,
                  allow_quality: bool, strength_tss: float, aet: Optional[float],
                  base_quality: Optional[dict] = None, prefs=None, rates: Optional[dict] = None,
                  notes: Optional[list] = None, blocked=frozenset(), quality_cap: Optional[int] = None,
                  aet_test_days: Optional[str] = None, xu_test: Optional[dict] = None,
                  b2b: Optional[dict] = None, long_min: Optional[float] = None,
                  sport: str = "trail", goal_pace: Optional[float] = None,
                  aet_measured: bool = False, taper: Optional[dict] = None) -> list[dict]:
    """The week_plan() session template for a projected week, placed on days.
    `taper` (SP-96, a 減量期 week): {"runs": the pre-taper runs a week, "long": whether a last long
    run ≤ 90 min fits, "sore"} — the run count is kept (overview.taper_easy_count).
    `aet` = the easy-run cap (hr_profile; `aet_measured`: a measured AeT).
    `long_min`: the 專項期 long day (engine/specific_phase.long_minutes); None = the base rule.
    `b2b` (engine/b2b.py): {"event", "state", "prev_mode", "weight"} — the
    week's B2B is decided here and written back as b2b["info"].
    `base_quality`: the session(s) the gate picked for this week — a list of the
    two-track intervals (overview.quality_sessions: base / 專項期 / 減量期), or one dict
    (the recovery-week fartlek, the AeT test, kind "test"); None in base with
    `allow_quality` = 有氧間歇（巡航）3×10, in 專項期 the old fixed session, in 減量期 TAPER_Q 4×3′.
    `quality_cap`: 1 = at most one interval (the gate's guardrail mode).
    `prefs` (課表偏好, engine/plan_prefs.py): shaped and placed like week_plan();
    `rates` = TSS / h per category for it, `notes` collects its notes.
    `blocked`: ISO days of 不排課日期 (engine/blackouts.py) — never a candidate day.
    `sport` (主要訓練項目, engine/primary_sport.py): road = the week_plan() road template (flat long
    run with a marathon-pace segment in the 專項期, flat threshold interval, flat strides)."""
    road = sport == "road"
    total = hours * 60.0
    ss: list[dict] = []
    # one session (older callers, the AeT test) or the week's interval list (two tracks, SP-31)
    bqs = [b for b in (base_quality if isinstance(base_quality, list) else [base_quality]) if b]
    cap_txt = easy_cap_label(None, aet, aet_measured)

    def add(**kw):
        ss.append({"target": "", "detail": "", "source": "", "tss": 0.0, "day": None,
                   "done": False, "done_by": None, **kw})

    if long_min is None:
        long_min = max(60.0, min(0.30 * total, max(longest, 60.0) * 1.15))
    long_min = min(long_min, 0.5 * total) if total >= 120 else long_min
    info = None
    if b2b is not None:
        info = b2b["info"] = B2B.projected(kind, mode, monday, b2b.get("event"), b2b.get("prev_mode"),
                                           b2b.get("state") or {}, round(long_min / 5) * 5, longest, total,
                                           accepted=b2b.get("accepted"))
        if info.get("post"):
            allow_quality = False                   # the easy days after a B2B (engine/b2b.py)
    if kind in ("base", "specific") and mode != "recovery_week":
        if xu_test and kind == "base":
            add(**_bq(xu_test))                     # 徐國峰's 90-min test = this week's LSD
        elif road:
            add(**O.road_long_session(long_min, kind, aet, tph, goal_pace, aet_measured), target=tgt.get("long", ""))
        else:
            add(id="long", kind="long", title="LSD" + ("（山路）" if mountain else ""),
                minutes=int(round(long_min / 5) * 5), target=tgt.get("long", ""),
                detail=("有山路就走山路，陡坡用走的" if mountain else "平路或緩坡")
                + f"；全程心率壓在{below(cap_txt)}，爬坡可以走",
                source=O.SRC_KOOP if kind == "specific" else O.SRC_UA, tss=long_min / 60.0 * tph)
            if info is not None and info.get("due"):
                ss.extend(B2B.followers(ss[-1], info))     # out of the easy minutes (Koop)
        if allow_quality and kind == "specific" and bqs:
            for b in bqs:                           # the two-track pick (SP-31; overview.quality_sessions)
                add(**_bq(b))
        elif allow_quality and kind == "specific" and road:
            add(**O.ROAD_SPECIFIC_Q, target=tgt.get("threshold", ""))
        elif allow_quality and kind == "specific":
            add(id="quality", kind="quality", title="VO2max 間歇 5×4 分上坡", minutes=60, target=tgt.get("supra", ""),
                detail="上坡 4 分鐘（6–10% 坡），慢跑或走下來恢復；暖身 15 分、緩和 10 分",
                source=O.SRC_PALLADINO + "（Supra-threshold）", tss=75.0)
        elif allow_quality and kind == "base" and bqs:
            for b in bqs:
                add(**_bq(b))
        elif allow_quality:
            add(id="quality", kind="quality", title="有氧間歇（巡航）3×10 分", minutes=60, target=tgt.get("threshold", ""),
                detail="休 2–3 分鐘；暖身 15 分、緩和 10 分", source=O.SRC_PALLADINO + "（3B）", tss=70.0)
    elif kind == "base" and mode == "recovery_week" and allow_quality and bqs \
            and bqs[0].get("kind", "quality") == "quality":
        add(**_bq(bqs[0]))                         # 3:1 recovery week: the short fartlek (Palladino)
    elif kind == "taper" and bqs:
        for b in bqs:                               # 減量期's two-track pick (overview.quality_sessions)
            add(**_bq(b))
    elif kind == "taper":
        add(**O.TAPER_Q, target=tgt.get("threshold", ""))
    if kind == "taper" and (taper or {}).get("long"):
        lm = O.taper_long_minutes(total)          # the last long run (SP-96), as week_plan
        add(id="long", kind="long", title=f"長跑（減量期，≤ {O.TAPER_LONG_MAX_MIN} 分）", minutes=int(round(lm / 5) * 5),
            target=tgt.get("long", ""), detail=f"心率不超過{cap_txt}；"
            + ("平路或緩坡，不跑長下坡" if taper.get("sore") else "輕鬆跑"), source=O.SRC_TAPER_WEEK, tss=lm / 60.0 * tph)
    n_strength = 2 if kind in ("base", "transition", "recovery") else 1
    for i in range(n_strength):
        add(id=f"strength{i + 1}", kind="strength", title="肌力（下肢單腳＋核心）", minutes=35,
            detail="膝主導＋臀中肌；安排在輕鬆日或跑完後", source=O.SRC_UA, tss=strength_tss)
    used = sum(s["minutes"] for s in ss if s["kind"] != "strength")
    left = max(0.0, total - used)
    n_easy = O.easy_count(left, kind)
    if kind == "taper" and taper:
        n_easy = O.taper_easy_count(left, taper.get("runs"), sum(1 for s in ss if s["kind"] in O.RUN_KINDS))
    for i in range(n_easy):
        m = min(left / n_easy, O.TRANSITION_RUN_MAX) if kind == "transition" else left / n_easy
        strides = kind == "base" and i == 0 and mode not in ("recovery_week", "reentry")
        st_t, st_d, _st_s = O.ROAD_STRIDES if road else O.HILL_STRIDES
        add(id=f"easy{i + 1}", kind="easy", title="輕鬆跑" + (st_t if strides else ""),
            minutes=int(round(m / 5) * 5), target=tgt.get("z2", ""),
            detail=f"心率不超過{cap_txt}" + (st_d if strides else ""),
            source=O.SRC_UA, tss=m / 60.0 * tph)
    if prefs is not None and prefs.active:
        from backend.engine import plan_prefs as PP
        r = {"road": tph, "trail": tph, "hike": tph, "strength": strength_tss / 35 * 60, **(rates or {})}
        days = [d for d in (monday + dt.timedelta(days=i) for i in range(7)) if d.isoformat() not in blocked]
        n_lost = sum(1 for i in range(7) if prefs.days[i] and (monday + dt.timedelta(days=i)).isoformat() in blocked)
        ctx = PP.Ctx(kind=kind, mode=mode, allow_quality=allow_quality, rates=r, aet=aet, aet_measured=aet_measured,
                     slots=max(1, sum(bool(x) for x in prefs.days) - n_lost), notes=notes if notes is not None else [],
                     quality_cap=quality_cap)
        ss = PP.shape(ss, total, prefs, ctx)
        if kind == "transition":
            O.cap_transition_runs(ss, ctx.notes)     # 轉換期: each run ≤ 60 min (Canova), as week_plan
        ctx.notes.extend(PP.blocked_pref_notes(prefs, monday, blocked))
        PP.place(ss, days, PP.long_weekday(prefs, long_wd), prefs, notes=ctx.notes)
        return _b2b_finish(ss, info, b2b, monday, aet, blocked, prefs, ctx.notes, aet_measured)
    # the raw 課表偏好 value: aet_test_days isn't part of `active`, so `prefs` may be None here
    aet_days = AT.TEST_DAYS.get(aet_test_days or getattr(prefs, "aet_test_days", None) or "weekday")
    _place(ss, monday, long_wd, blocked, aet_days, notes)
    return _b2b_finish(ss, info, b2b, monday, aet, blocked, None, notes, aet_measured)


def _b2b_finish(ss: list[dict], info: Optional[dict], b2b: Optional[dict], monday: dt.date, aet, blocked,
                prefs, notes, aet_measured: bool = False) -> list[dict]:
    """engine/b2b.py after the shaping and the placement: texts / caps, then
    the B2B days on consecutive days (課表偏好: allowed days, the caps)."""
    if not info or not info.get("due"):
        return ss
    B2B.decorate(ss, info, aet, prefs.long_cap if prefs is not None else None, (b2b or {}).get("weight"),
                 aet_measured)
    kept = B2B.place(ss, monday, monday, set(blocked or ()), prefs.allowed if prefs is not None else None, notes,
                     prefs.cap_weekday if prefs is not None else None, fixed=info.get("pair"))
    B2B.placed(info, kept)
    return kept


VARIANT_FIELDS = ("variant_key", "rung_key", "equiv", "swap", "swap_reason", "variant_reps", "variant_blocks",
                  "variant_adj", "progress", "prefer_days", "terrain")


def PP_long(prefs, auto_wd: int) -> int:
    from backend.engine import plan_prefs as PP
    return PP.long_weekday(prefs, auto_wd) if prefs is not None else auto_wd


def _bq(b: dict) -> dict:
    """add() kwargs for a gate-picked base session (quality or the AeT test)."""
    kind = b.get("kind", "quality")
    return {"id": b.get("id") or ("test_aet" if kind == "test" else "quality"), "kind": kind, "title": b["title"],
            "minutes": b["minutes"], "target": b.get("target", ""), "detail": b.get("detail", ""),
            "source": b.get("source", ""), "tss": float(b.get("tss") or 65.0),
            **({"protocol": b["protocol"]} if b.get("protocol") else {}),   # the AeT test: "aet"
            **({"terrain": b["terrain"]} if b.get("terrain") else {}),
            # the interval library variant (engine/interval_library.py)
            **{k: b[k] for k in VARIANT_FIELDS if b.get(k) is not None}}


def _place(ss: list[dict], monday: dt.date, long_wd: int, blocked=frozenset(),
           aet_days: Optional[tuple] = AT.TEST_DAYS["weekday"], notes: Optional[list] = None) -> None:
    """`aet_days`: where the AeT test goes (aet_test.test_days / pick_day:
    Mon–Fri by default; None = like an interval)."""
    days = [monday + dt.timedelta(days=i) for i in range(7)]
    free = [d for d in days if d.isoformat() not in blocked]
    main = [s for s in ss if s["kind"] != "strength"]
    long_day = None
    for s in sorted(main, key=lambda s: -1 if AT.is_xu(s) else {"long": 0, "quality": 1, "test": 1}.get(s["kind"], 2)):
        if not free:
            break
        if s["kind"] == "test" and AT.is_xu(s):
            pick = AT.pick_day_xu(free, long_wd)
            if pick is None:
                continue
            s["day"] = pick.isoformat()
            free.remove(pick)
            long_day = pick
            continue
        if s["kind"] == "long":
            pick = days[long_wd] if days[long_wd] in free else free[-1]
            long_day = pick
        elif s["kind"] == "test" and aet_days is not None and AT.is_aet_session(s):
            r = AT.pick_day(free, long_day, [], aet_days, weekend_ok=not AT.is_short(s))
            if r["day"] is None:
                if notes is not None:
                    notes.append({"level": "info", "text": r["note"]})
                continue
            s["day"] = r["day"].isoformat()
            free.remove(r["day"])
            continue
        elif s["kind"] in ("quality", "test"):
            order = [days[i] for i in (1, 2, 3, 0, 4, 5, 6)]
            pick = next((d for d in order if d in free and (long_day is None or abs((d - long_day).days) >= 2)),
                        free[0])
        else:
            pick = free[0]
        s["day"] = pick.isoformat()
        free.remove(pick)
    easy_days = [_d(s["day"]) for s in main if s["kind"] == "easy" and s["day"]]
    taken: set[dt.date] = set()
    for s in [s for s in ss if s["kind"] == "strength"]:
        cands = sorted(d for d in free + easy_days
                       if (long_day is None or d != long_day - dt.timedelta(days=1)) and d not in taken)
        if cands:
            s["day"] = cands[0].isoformat()
            taken.add(cands[0])
            if cands[0] in free:
                free.remove(cands[0])


def _gate_inputs(cur: dict) -> dict:
    """week_plan()'s gate (engine/quality_gate.py): method state, guardrails,
    dose step, base-phase start. An older `cur` without it — or with the old
    {levels, streak_ok} shape — becomes a no-method gate (guardrails only; the
    unsourced drift streak is gone): intensity bad blocks, otherwise allowed.
    A CP-test session says nothing (week_plan puts the test in place of the
    quality session)."""
    from backend.engine import quality_gate as QG
    g = cur.get("quality_gate")
    if g is not None and not QG.legacy(g):
        return g
    levels = dict((g or {}).get("levels") or {})
    if g is None:
        ok = (any(s["kind"] == "quality" for s in cur.get("sessions") or []) or cur.get("phase") != "base"
              or cur.get("mode") not in ("base", "specific"))
        levels = {"intensity": "good" if ok else "na", "drift": "na"}
    blocked = levels.get("intensity") == "bad"
    return {"state": "none", "mode": "auto", "resolved": "none", "levels": levels,
            "guard": {"block": blocked, "rule": "intensity" if blocked else "", "verdict": ""},
            "dose": {"done": 0, "step": 0, "faded": False}}


def allow_quality(kind: str, gate: dict, monday: Optional[dt.date] = None, step=None,
                  mode: Optional[str] = None, n: int = 1) -> dict:
    """week_plan()'s rule for one projected week: quality_gate.week_decision
    with the week's phase, mode (a recovery week gets the fartlek) and Monday
    (weeks mode, the Zone 3 gate's time path, the 1-a-week turn) and the track steps
    reached by then ({"z3", "z5", "met"}; an int = the Zone 3 step); this week's ramp /
    volume / TSB guardrails are not carried forward (re-checked when the week comes)."""
    from backend.engine import quality_gate as QG
    return QG.week_decision(gate, kind, mode or kind, monday, step, first=False, n=n)


def IL_track(q: dict) -> Optional[str]:
    """The track of a planned quality session (its rung, else its variant's class): z3 / z5 / None."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    t = IL.track_of(q.get("rung_key"))
    if t is None and q.get("variant_key"):
        t = "z5" if QG.is_z5_variant(q["variant_key"]) else "z3" if QG.is_z3_variant(q["variant_key"]) else None
    return t


def _paired(qs: list, items: list) -> list:
    """(session, week_decision item) pairs. quality_sessions returns one per item unless one was
    left out (the second Zone 3 that didn't fit, the user's RPE ≥ 7 sessions took the budget):
    then each session takes the first remaining item of its own track."""
    if len(qs) == len(items):
        return list(zip(qs, items))
    rest, out = list(items), []
    for q in qs:
        tr = IL_track(q)
        it = next((x for x in rest if x.get("track") == tr), rest[0] if rest else {})
        if it in rest:
            rest.remove(it)
        out.append((q, it))
    return out


def _advance(steps: dict, rung: Optional[str]) -> None:
    """One step of the track `rung` serves (a projected session assumed 達標)."""
    from backend.engine import interval_library as IL
    t = IL.track_of(rung)
    if t == "z3":
        steps["z3"] += 1
        steps["met"] += 1
    elif t == "z5":
        steps["z5"] += 1


def project_weeks(cur: dict, phases: list, until: dt.date, ctlconstant: float = 42.0,
                  atlconstant: float = 7.0, prefs=None, blackouts=None, events=None,
                  heat_acts: Optional[list] = None, b2b_accepted: Optional[list] = None) -> list[dict]:
    """Weeks after cur['week'] (a week_plan() result) up to `until` (≤ MAX_WEEKS).
    `b2b_accepted`: the accepted B2B entries (engine/b2b.py); a due B2B in a
    week without one is only a suggestion (the week's `b2b_suggestion`).
    `ctlconstant` / `atlconstant`: the athlete's (ds.athlete), as for the PMC.
    `prefs`: the 課表偏好 week_plan() used (None / defaults = the original rules).
    `blackouts`: the 不排課日期 ranges week_plan() used (engine/blackouts.py).
    `events` (season-plan events) + `heat_acts` (per-activity heat exposure):
    熱適應課 before a hot A/B race (engine/heat_plan.py); None = none. The S
    carried into each week counts the heat sessions planned before it."""
    from backend.engine import blackouts as BL
    planned_heat: dict = {d: 1.0 for d in ((cur.get("heat") or {}).get("days") or [])}
    PR = prefs if prefs is not None and prefs.active else None
    bmap = BL.blocked(blackouts or ())
    allowed_fn = PR.allowed if PR is not None else None
    prev_lost = [_d(x) for x in cur.get("blackout_days") or []]
    monday = _d(cur["week"]["start"])
    cap = monday + dt.timedelta(weeks=MAX_WEEKS + 1) - dt.timedelta(days=1)
    until = min(until, cap)
    tph = float(cur["target"].get("tss_per_hour") or 50.0)
    th = cur.get("thresholds") or {}
    th_meas = easy_cap_measured(th)          # the easy cap is a measured AeT (「（實測 AeT）」)
    tgt = target_texts(th)
    long_wd = O.WEEKDAYS.index(cur.get("long_weekday") or "六")
    cur_s = cur.get("sessions") or []
    long_s = next((s for s in cur_s if s.get("id", s.get("gen_key")) == "long" or s["kind"] == "long"), None)
    longest = float(long_s["minutes"]) if long_s else 60.0
    mountain = bool(long_s and "山路" in long_s["title"])
    # 主要訓練項目 (engine/primary_sport.py): the sport week_plan() used; road = no B2B, no steep walk
    sport = cur.get("primary_sport") or "trail"
    road = sport == "road"
    rates = cur.get("tss_per_category") if PR is not None else None
    gate = _gate_inputs(cur)
    # each track's step (SP-31): the gate's, plus this week's intervals — a 縮量版 / the step before
    # under a tight cap is maintenance and moves nothing (§C5.3)
    d3, d5 = QG._track_doses(gate)
    steps = {"z3": int(d3.get("step") or 0), "z5": int(d5.get("step") or 0), "met": int(d3.get("met") or 0),
             # the Zone 3 session dates Zone 5's soft 「3 區先」 counts per projected week (SP-39, z5_track)
             "z3_dates": list((gate.get("z3_recent") or {}).get("dates") or [])}
    if cur.get("phase") in ("base", "specific"):
        for q in cur_s:
            if q.get("kind") == "quality" and IL_track(q) == "z3":
                steps["z3_dates"].append(monday.isoformat())
            if q.get("kind") == "quality" and q.get("rung_key") in QG.ladder_keys() and q.get("progress") is not False:
                _advance(steps, q.get("rung_key"))
    last_aet = (gate.get("aet_test") or {}).get("last")
    # the interval library's rotation (engine/interval_library.fit): the stored variants done
    # before this week, then this week's pick and each projected week's
    vhist = O.variant_history(monday, gate)
    this_q = next((s for s in cur_s if s.get("kind") == "quality" and s.get("variant_key")), None)
    if this_q is not None:
        vhist.append({"day": monday.isoformat(), "rung_key": this_q.get("rung_key"),
                      "variant_key": this_q["variant_key"], "state": "done", "outcome": None})
    st = next((s for s in cur_s if s["kind"] == "strength"), None)
    strength_tss = float(st["tss"]) if st else 35 / 60 * 30
    hist = [float(h["hours"]) for h in cur.get("history") or []] + [float(cur["target"]["hours"])]
    # 轉換期 (SP-73): weekly hours by Monday — the past weeks, this week, then each projected week
    hours_at = {str(h.get("start")): float(h["hours"]) for h in cur.get("history") or [] if h.get("start")}
    hours_at[monday.isoformat()] = float(cur["target"]["hours"])
    cur_tr = cur.get("transition_ref") or {}
    # the +10 % cap's normal weeks (SP-73): week_plan's, this week's, then each projected week
    # that isn't a 減量期 / race week / 恢復期 / 轉換期 one (None = an older `cur`: the raw history)
    ref_wk = cur["target"].get("ref_weeks")
    norm = None if ref_wk is None else [float(h) for h in ref_wk]
    if norm is not None and not _skip_week(phases, monday):
        norm.append(float(cur["target"]["hours"]))
    build = [False] * (len(hist) - 1) + [cur.get("mode") in ("base", "specific")]
    for i in range(1, len(hist) - 1):
        build[i] = hist[i] >= 0.95 * hist[i - 1] and hist[i] > 0.5
    ctl = float((cur.get("load") or {}).get("ctl_end") or 0.0)
    atl = float((cur.get("load") or {}).get("atl_end") or 0.0)
    out = []
    from backend.engine import reentry as RE
    blocks = ([cur["reentry"]] if cur.get("reentry") else []) + RE.planned_ahead(
        blackouts or (), monday + dt.timedelta(days=6),
        (sum(hist[-5:-1]) / 4.0) if len(hist) >= 5 else (hist[-1] if hist else None), longest)
    cb = cur.get("b2b") or {}
    b2b_state = B2B.next_state(cb, monday, cur_s)          # 連續兩天長天 (engine/b2b.py)
    lc_cur = cur.get("steep_hill") or {}                    # 陡坡健走（模擬負重） (engine/steep_hill.py)
    sp_cur = cur.get("specific") or {}                      # 專項期 (engine/specific_phase.py)
    recent_long = [longest, float(sp_cur.get("longest28") or 0.0)]   # the long days of the last 4 weeks
    prev_mode = cur.get("mode")
    s_stops = cur.get("strength_stop") or []                 # 賽前停肌力 (SP-86): the A events' windows
    from backend.engine import technical as TECH
    user_rows = TECH.load_user()                             # the user's own RPE sessions (SP-74)
    # 減量期 (SP-96): this week's taper context and pre-taper level (week_plan), the run count of the
    # last projected week before a taper, its hours (the share the climb follows)
    cur_t = cur.get("taper") or {}
    last_runs = cur_t.get("pre_runs") or sum(1 for s in cur_s if s.get("kind") in O.RUN_KINDS) or None
    pre_h = cur_t.get("pre_hours")
    week = monday + dt.timedelta(weeks=1)
    while week <= until:
        kind = phase_kind(phases, week)
        tc = O.taper_context(phases, events, week) if events is not None else None
        same = bool(tc and cur_t.get("id") == tc["id"])
        if kind == "taper" and not same and pre_h is None:
            pre_h = statistics.mean(hist[-4:]) if hist else None
        ev = _next_event_start(phases, week)
        days_to = (ev - week).days if ev else None
        tr_h = None
        if kind == "transition":
            ref = O.transition_ref(phases, week, lambda m: hours_at.get(m.isoformat()))
            tr_h = cur_tr.get("hours") if cur_tr.get("hours") and cur_tr.get("mondays") == ref["mondays"] \
                else ref["hours"]
        hours, mode, why = week_hours(kind, hist, build, ctl, tph, ctlconstant, days_to, tr_h,
                                      LG.step_base(norm) if norm else None)
        notes: list = []
        if PR is not None and PR.weekly_hours is not None and hours > PR.weekly_hours:
            hours = PR.weekly_hours
            why = why + [f"你的每週時數上限 {PR.weekly_hours:g} h"]
        lost: list[dt.date] = []
        # 停訓後的恢復期 (engine/reentry.py) replaces the old step_cap after 不排課日期
        rp = RE.block_on(blocks, week) if kind in ("base", "specific") else None
        if rp is not None and rp.get("prev_hours"):
            f = RE.week_factor(rp, week)
            hours, mode = rp["prev_hours"] * f, "reentry"
            why = why + [f"{rp['text']}：停訓前 {rp['prev_hours']:.1f} h × {f:.0%} → {hours:.1f} h"]
        elif kind in ("base", "specific") and any(p.get("prev_hours") and p["end"] <= week.isoformat()
                                                   < (_d(p["end"]) + dt.timedelta(days=7)).isoformat() for p in blocks):
            p = next(p for p in blocks if p["end"] <= week.isoformat() < (_d(p["end"]) + dt.timedelta(days=7)).isoformat())
            hours = max(hours, p["prev_hours"])
            why = why + [f"恢復期結束：回到停訓前的量 {p['prev_hours']:.1f} h（Daniels 表 9.2）"]
        full_h = hours                       # before this week's 不排課 days are taken off
        if bmap:
            lost = BL.lost_days(bmap, week, allowed_fn)
            if lost and mode == "reentry":
                lost = []
            if lost:
                f = BL.factor(week, lost, allowed_fn)
                lost_h = hours * (1.0 - f)
                hours *= f
                why = why + [f"不排課 {BL.range_text(lost)}：少 {len(lost)} 個可練日，週量 × {f:.0%}"]
                notes.append(BL.week_note(bmap, lost, lost_h))
        q_n = O.quality_per_week(PR, kind, mode, gate)
        dec = allow_quality(kind, gate, week, dict(steps), mode, q_n)
        # the user's own RPE ≥ 7 技術地形 sessions this week: in the 20 % first (technical.py, SP-74)
        user_q = TECH.user_quality(user_rows, week) \
            if kind in ("base", "specific") and mode != "recovery_week" else []
        user_min = sum(u["work"] for u in user_q)
        if user_q:
            notes.append(TECH.user_note(user_q, hours, kind))
        no_q = mode == "reentry" and rp is not None and not RE.quality_ok(rp, week)
        if no_q:
            dec = {**dec, "allow": False, "spec": None, "advance": False}
        base_q, xu_q = None, None
        proto = AT.resolve_protocol(getattr(prefs, "aet_test_protocol", None) or "auto",
                                    getattr(prefs, "cap_weekday", None), getattr(prefs, "long_cap", None))
        due = kind == "base" and mode not in ("recovery_week", "reentry") and \
            AT.due(week, kind, gate.get("base_start"), gate.get("aet_test_reason"), last_aet)
        if due:
            # tests are suggested for the current week only (overview.week_plan test_suggestions),
            # never put into the plan — projected weeks keep their long run and interval
            last_aet = week.isoformat()
        if base_q is None and dec["allow"] and dec.get("items") and kind in ("base", "specific", "taper"):
            # the two-track pick (SP-31): the same sessions as week_plan (overview.quality_sessions)
            q_cap, q_alt = O.quality_caps(PR, PP_long(PR, long_wd))
            base_q = O.quality_sessions(gate, dec, kind, th, tgt, hours, prefs, vhist,
                                        mountain if kind == "base" else not road, road, q_cap, q_alt,
                                        reserved=user_min)
            for q, it in _paired(base_q, dec["items"]):
                if it.get("track") == "z3":
                    steps["z3_dates"].append(week.isoformat())
                if q.get("variant_key"):
                    # this week's pick joins the rotation history of the weeks after it
                    vhist.append({"day": week.isoformat(), "rung_key": q.get("rung_key"),
                                  "variant_key": q["variant_key"], "state": "done", "outcome": None})
                if it.get("advance") and q.get("rung_key") in QG.ladder_keys() and q.get("progress", True) is not False:
                    _advance(steps, q.get("rung_key"))
        elif base_q is None and kind == "taper":
            base_q = O.quality_sessions(gate, dec, kind, th, tgt, hours)
        b2b = None if road else {"event": cb.get("event"), "state": b2b_state, "prev_mode": prev_mode,
                                 "weight": cb.get("weight"), "accepted": b2b_accepted}
        sp_info = SP.projected_context(kind, mode, week, sp_cur) if sp_cur.get("race") else None
        sp_long = SP.long_minutes(sp_info, max(recent_long[-4:])) if sp_info else None
        # nothing left of the 20 % after the user's own sessions: no fallback interval either
        q_ok = (dec["allow"] or base_q is not None) and not (user_q and base_q == [])
        ss = week_sessions(week, kind, mode, hours, tph, tgt, long_wd, longest, mountain,
                           q_ok, strength_tss, th.get("aet"), base_q,
                           prefs=PR, rates=rates, notes=notes, blocked=set(bmap),
                           quality_cap=1 if kind == "base" and QG.guardrail_mode(gate) else None,
                           aet_test_days=getattr(prefs, "aet_test_days", None), xu_test=xu_q, b2b=b2b,
                           long_min=sp_long, sport=sport, goal_pace=cur.get("mp_goal_pace_s"),
                           aet_measured=th_meas,
                           taper={"runs": cur_t.get("pre_runs") if same else last_runs, "sore": tc.get("sore"),
                                  "long": (_d(tc["start"]) - week).days > tc["long_days"]}
                           if tc and kind == "taper" else None)
        if kind == "transition":
            notes.append({"level": "info", "src": "transition", "text": O.TRANSITION_NOTE})
        ph_notes = _phase_notes(phases, week)
        ph_note = bool(ph_notes)
        for pk, t in ph_notes:
            notes.append({"level": "info", "src": "transition" if pk in ("recovery", "transition") else "phase",
                          "text": t})
        if sp_info and sp_info.get("active"):
            try:
                SP.decorate(ss, sp_info)
                SP.apply_climb(ss, sp_info, aet=th.get("aet"), prefs=prefs, b2b=(b2b or {}).get("info"), notes=notes,
                               rates=cur.get("tss_per_category"), aet_measured=th_meas)
            except Exception:              # noqa: BLE001 — never breaks the projection
                pass
        b2b_info = (b2b or {}).get("info") or {}
        b2b_sug = B2B.suggestion(b2b_info, week, next((s["day"] for s in ss if s.get("id") == "long"), None),
                                 enabled=getattr(prefs, "b2b", True) is not False)
        if b2b_info.get("post"):
            notes.append(B2B.post_note(b2b_info))
        b2b_state, prev_mode = B2B.next_state(b2b_info, week, ss), mode
        lc_info = None
        if lc_cur.get("active"):
            # 陡坡健走（模擬負重） (engine/steep_hill.py): one weekday steep walk in the 專項期
            try:
                lc_info = SH.projected_context(kind, mode, week, lc_cur)
                SH.apply(ss, lc_info, aet=th.get("aet"), prefs=prefs, b2b=b2b_info, notes=notes,
                         rates=cur.get("tss_per_category"), aet_measured=th_meas, walk=th.get("walk_cap"))
            except Exception:              # noqa: BLE001 — never breaks the projection
                lc_info = None
        heat_w = None
        if events is not None:
            try:
                from backend.engine import heat as HT
                from backend.engine import heat_plan as HP
                acts_w = list(heat_acts or []) + [{"date": d, "hot_min": 60.0} for d in planned_heat]
                heat_w = HP.apply(ss, events=events, today=week, prefs=prefs, aet=th.get("aet"), mode=mode, kind=kind,
                                  notes=notes, acts=acts_w, s_now=HT.current(acts_w, week - dt.timedelta(days=1))["s"],
                                  aet_measured=th_meas)
                for d in heat_w.get("days") or []:
                    planned_heat[d] = 1.0
            except Exception:              # noqa: BLE001 — never breaks the projection
                heat_w = None
        # 技術地形課 (engine/technical.py, SP-74): the same rule as week_plan, per projected week
        tech = TECH.week_context(kind=kind, mode=mode, monday=week, road=road, b2b=b2b_info)
        if tech.get("active"):
            try:
                TECH.apply(ss, tech, hours=hours, rates=cur.get("tss_per_category"), prefs=prefs, notes=notes,
                           user=user_q)
            except Exception:              # noqa: BLE001 — never breaks the projection
                tech = {"active": False}
        # 賽前停肌力 (SP-86): week_plan's A-event windows, the same rule (overview.drop_strength_before_a)
        n_notes = len(notes)
        ss = O.drop_strength_before_a(ss, s_stops, week, notes)
        # 減量期規則 (SP-96): the same rule as week_plan (overview.taper_rules)
        ss = O.taper_rules(ss, tc, week, notes, road)
        if tc and kind == "taper":
            n = O.taper_climb_note(tc, cur_t.get("pre_climb") if same else cur.get("climb4"),
                                   hours / (cur_t.get("pre_hours") if same else pre_h)
                                   if (cur_t.get("pre_hours") if same else pre_h) else None)
            if n:
                notes.append(n)
        elif kind in ("base", "specific"):
            last_runs = sum(1 for s in ss if s["kind"] in O.RUN_KINDS and s["day"]) or last_runs
            pre_h = None
        s_note = len(notes) > n_notes
        prev_lost = lost
        drop = [s for s in ss if not s["day"] and s["kind"] != "strength"] if lost else []
        if drop:
            notes.append({"level": "info", "src": "blackout", "text": f"剩下的日子排不下 {len(drop)} 堂課（約 {sum(s['minutes'] for s in drop)} 分鐘）——不用補"})
        # a session _place() found no day for has day None: keep it out of the date test
        ss = [s for s in ss if not s["day"] or _d(s["day"]) <= until] if ss else ss
        by_day = {}
        for s in ss:
            if s["day"]:
                by_day[s["day"]] = by_day.get(s["day"], 0.0) + s["tss"]
        planned = [by_day.get((week + dt.timedelta(days=i)).isoformat(), 0.0) for i in range(7)]
        proj = O.project(ctl, atl, planned, ctlconstant, atlconstant)
        ctl0 = ctl
        ctl, atl = proj[-1]["ctl"], proj[-1]["atl"]
        out.append({"start": week.isoformat(), "phase": kind, "mode": mode,
                    "mode_label": MODE_LABELS.get(mode, mode), "hours": hours,
                    "tss": sum(planned), "ctl_start": ctl0, "ctl_end": ctl,
                    "provisional": week > monday + dt.timedelta(weeks=1), "why": why,
                    "sessions": [s for s in ss if s["day"]],
                    **({"notes": notes} if PR is not None or bmap or (heat_w or {}).get("active")
                       or kind == "transition" or ph_note or tech.get("planned") is not None or s_note or user_q
                       or b2b_info.get("post") or b2b_info.get("due") or (lc_info or {}).get("planned") else {}),
                    **({"b2b": B2B.public(b2b_info)} if b2b_info.get("due") or b2b_info.get("post") else {}),
                    **({"b2b_suggestion": b2b_sug} if b2b_sug else {}),
                    **({"steep_hill": SH.public(lc_info)} if lc_info and lc_info.get("active") else {}),
                    **({"specific": SP.public(sp_info)} if sp_info and sp_info.get("active") else {}),
                    **({"heat": heat_w} if (heat_w or {}).get("active") else {}),
                    **({"technical": TECH.public(tech)} if tech.get("active") else {}),
                    **({"blackout_days": [d.isoformat() for d in lost]} if lost else {})})
        long_n = next((s for s in ss if s["id"] == "long"), None) if kind != "taper" else None
        recent_long.append(float(long_n["minutes"]) if long_n else 0.0)
        if long_n:
            # a B2B day 1 shortened to fit the pair (engine/b2b.py) doesn't lower the long-run base
            longest = max(longest, float(long_n["minutes"])) if b2b_info.get("due") else \
                float(long_n["minutes"])
        if PR is not None or lost:
            # what the preferences / 不排課日期 actually let through (a hard cap or
            # too few days can leave less)
            hours = sum(s["minutes"] for s in ss if s["kind"] != "strength" and s["day"]) / 60.0
        # a short break (< 6 days, Daniels cat. 1: back to 100 %) doesn't lower the base the next
        # weeks ramp from — the re-entry block handles the longer ones
        h_hist = full_h if lost and mode != "reentry" else hours
        build.append(mode in ("base", "specific") and h_hist >= 0.95 * hist[-1] and h_hist > 0.5)
        hist.append(h_hist)
        if norm is not None and not _skip_week(phases, week):
            norm.append(h_hist)
        hours_at[week.isoformat()] = h_hist
        week += dt.timedelta(weeks=1)
    return out
