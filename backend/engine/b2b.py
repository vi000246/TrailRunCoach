"""
連續兩天長天（Back-to-Back，B2B） — docs/research/back-to-back-and-long-day.md.

A SUGGESTION, not an auto-scheduled block (the user, 2026-10-02: not every
weekend has both days free). When a B2B week is due under the rules below,
the planner emits a suggestion (why, a day pair, the two durations); the
plan only contains a B2B once the user accepts it. Accepting stores the two
days as the user's own sessions (api/plan_sessions suggestions/accept) and
an entry in `plan.b2b.accepted` ({week, days, minutes, uids}); the generator
then plans the rest of that week around them (day 2 out of the easy minutes,
quality ≥ 48 h away, the 4 easy days after) but never emits the two days
into the stored plan itself (plan_store.gen_weeks drops them), so the
auto-replan treats them like any user edit. Declining / dismissing is
recorded per week (engine/suggestions.py). 課表偏好 `b2b` off = no
suggestions at all. Always 2 days (the 3-day version was dropped).

Hooks (small, so the planner files stay readable):

  * overview.week_plan  — week_context() at the volume step (the TSB exception),
    finalize() (→ the suggestion) or the accepted entry + followers() in the
    session template, decorate() after the 課表偏好 shaping, done_follow()
    when marking what is done, place(fixed=the accepted days) after the
    placement.
  * projection.project_weeks — the same per projected week (no TSB there:
    re-checked when the week comes).
  * adapt._fatigue — fatigue_exempt(): TSB < −30 alone after an accepted B2B
    does not cut the week.

States of one week's info: `candidate` (the rules allow it, before the base
check), `suggest` (+ base built and the minutes fit → a suggestion), `due`
(accepted: the B2B is in the plan this week), `post` (an accepted B2B ended
just before this week: the 4 easy days).

When (doc §2.4):
  * only in the 專項期 (`specific`) before the next A event that is multi-day
    (days > 1) or a single day ≥ 6 h (planning.Event.is_long) — UA「longer
    races」, CTS, Koop;
  * the last B2B ends ≥ 3 weeks before the event (搜尋摘要「3 weeks」未驗證;
    Koop: nothing hard in the last 2–3 weeks);
  * at most one per 3:1 cycle, on the first build week after the recovery week
    (Johnston「slightly below-average」week before, Koop「after rest」); the
    number of weekends (≈ 2 in an 8-week block) is 推估;
  * prerequisites: base built (longest of the 28 days before ≥ day 1 × 0.87,
    the app's +15 % step reversed), not a re-entry / recovery week, TSB ≥ −20,
    CTL ramp < 8 and the gate's guardrails pass (combination 推估).

Structure (doc §2.5):
  * day 1 = the week's long day (longer, more climbing); day 2 ≈ 2/3 of day 1
    (CTS 30:20 ≈ 0.67; 0.6–0.7 推估); both ≤ AeT HR, HR-only target
    (vo2max-gate-and-trail-metric.md: trail pace / power unreliable);
  * the week's total does not grow (Koop「Do not increase the total」): day 2
    comes out of the easy runs; the pair ≤ 70 % of the week (推估);
  * no pack (the load is simulated by grade: engine/steep_hill.py); day 1
    practises race fuelling 30–60 g/h (Burke 2011).

The exception (doc §2.5, user-approved): an accepted B2B pushes TSB below −30 /
−20. week_plan would make the next week a recovery week (3:1 → 2:2); instead
the B2B week and the week after keep their volume, the 4 days after the B2B are
easy only (no quality / test; UA「three or four light days」, 4 推估), and the
3:1 cycle continues. Not when something beyond the expected drop shows up:
a CTL ramp ≥ 8 (status.RAMP["short"]) or — in adapt — two red-compliance
sessions in a row.

Evaluation without RPE (doc §2.6): day 2 vs day 1 on the climbs (grade ≥ 10 %,
≥ 3 consecutive 100 m windows, moving > 1.5 km/h — hikehr.HIKE_FILTER):
ΔHR@VAM (hikehr.fatigue: day-1 HR ~ VAM line, day-2 median residual) and
ΔVAM@AeT (windows at AeT − 10 … AeT: each day's VAM ~ HR line read at
AeT − 5, day 2 ÷ day 1 — the line is 推估; one-speed days fall back to the
band medians), read as a
2 × 2: lower HR + slower = muscular fatigue; similar / higher HR + slower =
cardiovascular drift or under-fuelling; similar = good durability. Thresholds
±3 bpm (= workout_review.AET_MARGIN) and ±5 % (UA's 5 %) are 推估.
"""
from __future__ import annotations

import datetime as dt
from statistics import median
from typing import Callable, Iterable, Optional

import re

from backend.i18n import _, N_, fmt

FOLLOWERS = ("long2",)         # always 2 days (the 3-day version was dropped, 2026-10-02)
ACCEPTED_KEY = "plan.b2b.accepted"   # user_settings: [{week, days: [d1, d2], minutes: [m1, m2], uids, at}]
SUGGEST_AHEAD_DAYS = 13        # suggestions for this week and the next (推估: enough notice to free a weekend)

# ---- when ------------------------------------------------------------------
LAST_BEFORE_DAYS = 21          # last B2B day ≥ 3 weeks before the event (未驗證 summary; Koop 2–3 wk)
SPACING_DAYS = 14              # 推估: never two B2B weekends < 2 weeks apart
RECOVERY_SHARE = 0.85          # 推估: last week ≤ 85 % of the 3 before = the 3:1 recovery week (0.65)
LONGEST_FRAC = 0.87            # base built: longest 28 d ≥ day 1 × 0.87 (1 / 1.15, the app's +15 % step)
TSB_MIN = -20.0                # week_plan's 維持量 line
RAMP_MAX = 8.0                 # status.RAMP["short"] (Friel 5–8)
# ---- structure ----------------------------------------------------------------
DAY2_RATIO = 0.67              # CTS 30:20 (Jones-Wilkins); 0.6–0.7 推估
SINGLE_DAY2 = (90, 150)        # single-day ≥ 6 h event: day 2 1.5–2.5 h easy (推估, doc §2.5)
PAIR_SHARE = 0.70              # 推估: the B2B days ≤ 70 % of the week's minutes
MIN_DAY2 = 60                  # 推估: a "long" day 2 is ≥ 60 min, else no B2B this week
FUEL_MIN_H = 4.0               # Koop: a fuelling long run is ≥ 4 h
POST_EASY_DAYS = 4             # UA / Johnston「three or four light days」; 4 推估
# ---- detection of a done B2B --------------------------------------------------
DETECT_MIN = 90.0              # 推估: a day counts as a long day from 90 moving minutes
# ---- evaluation ---------------------------------------------------------------
HR_SAME_BPM = 3.0              # 推估 (= workout_review.AET_MARGIN)
VAM_SAME = 0.05                # 推估 (UA 5 %)
AET_BAND_BPM = 10.0            # ΔVAM@AeT: windows at AeT − 10 … AeT (vo2max-gate-and-trail-metric.md §2.4 (a) ③)
TREND_STEP = 0.02              # 推估: the day-2 VAM ratio moved ≥ 2 points = 變好 / 變差

SRC_WHEN = N_("UA（專項期、longer races）；Koop（最後 2–3 週不硬塞）；"
              "賽前 ≥ 3 週為搜尋摘要（未驗證）；每個 3:1 週期一次、次數為推估")
SRC_DAYS = N_("Koop／CTS〈Block Training〉：第 1 天較硬、總量不加；Jones-Wilkins（CTS）30:20；"
              "UA：兩天都 ≤ AeT；Burke 2011：補給 30–60 g/h；第 2 天比例 0.67、背負進度對應次數為推估")
SRC_POST = N_("Johnston（UA）「three or four light days」；4 天、不改恢復週為推估")
SRC_EVAL = N_("hikehr.fatigue（同 VAM 的心率差，無外部來源 F17）；ΔVAM@AeT（vo2max-gate-and-trail-metric.md）；"
              "2×2 判讀依 UA、Le Meur 2013、Coyle 2001；±3 bpm、±5 % 為推估")


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _r5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def wd(day) -> str:
    return fmt.weekday(_d(day))                 # 週一 / Mon (backend/i18n/fmt.py)


def md(day) -> str:
    d = _d(day)
    return f"{d.month}/{d.day}"


# ---------------------------------------------------------------------------
# the target event
# ---------------------------------------------------------------------------

def target_event(events: Iterable, today: dt.date):
    """The next A event after `today` (the one the 專項期 builds towards)."""
    ahead = sorted((e for e in events or () if getattr(e, "priority", "A") == "A" and e.start > today),
                   key=lambda e: e.start)
    return ahead[0] if ahead else None


def qualifies(ev) -> bool:
    """Multi-day, or a single day ≥ 6 h (planning.Event.is_long)."""
    if ev is None:
        return False
    days = int(ev.get("days") if isinstance(ev, dict) else (ev.days or 1)) or 1
    is_long = ev.get("is_long") if isinstance(ev, dict) else ev.is_long
    return days > 1 or bool(is_long)


def event_json(ev) -> Optional[dict]:
    if ev is None:
        return None
    return {"id": ev.id, "name": ev.name, "start": ev.start.isoformat(), "days": int(ev.days or 1),
            "kind": ev.kind, "is_long": bool(ev.is_long), "est_hours": ev.est_hours,
            "pack_kg": getattr(ev, "pack_kg", None), "qualifies": qualifies(ev)}


# ---------------------------------------------------------------------------
# B2B weekends already done (from the activities)
# ---------------------------------------------------------------------------

def detect(rows: Iterable[tuple]) -> list[dict]:
    """Done B2B blocks from (date, moving minutes, activity idx) rows of the
    endurance activities: ≥ 2 consecutive days of ≥ DETECT_MIN minutes each
    (推估). Per day the minutes add up; the day's longest activity is kept."""
    per: dict = {}
    for day, minutes, idx in rows:
        d = _d(day)
        tot, best = per.get(d, (0.0, None))
        bm = best[1] if best else -1
        per[d] = (tot + float(minutes or 0), (idx, minutes) if (minutes or 0) > bm else best)
    long_days = sorted(d for d, (m, _) in per.items() if m >= DETECT_MIN)
    out, run = [], []
    for d in long_days + [None]:
        if run and (d is None or (d - run[-1]).days != 1):
            if len(run) >= 2:
                out.append({"start": run[0].isoformat(), "end": run[-1].isoformat(), "days": len(run),
                            "minutes": [round(per[x][0]) for x in run], "idx": [per[x][1][0] for x in run]})
            run = []
        if d is not None:
            run.append(d)
    return out


def history(done: list[dict], since: Optional[dt.date], before: dt.date) -> dict:
    """The block's B2B record before `before`: last end, count. `done`: detect()
    rows and accepted entries (as {start, end}); the same weekend counts once."""
    blk, seen = [], set()
    for b in sorted(done, key=lambda b: str(b["start"])):
        if (since is not None and _d(b["start"]) < since) or _d(b["end"]) >= before:
            continue
        if any(abs((_d(b["start"]) - s).days) <= 1 for s in seen):
            continue
        seen.add(_d(b["start"]))
        blk.append(b)
    return {"last": max((b["end"] for b in blk), default=None), "count": len(blk), "done": blk}


# ---------------------------------------------------------------------------
# accepted B2B weekends (the user's own sessions; api/plan_sessions)
# ---------------------------------------------------------------------------

def load_accepted(user_id: int = 1) -> list[dict]:
    """The stored accepted entries (read-only sqlite, like plan_prefs.load)."""
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(ACCEPTED_KEY, [], user_id)
    return [e for e in v if isinstance(e, dict) and e.get("week") and len(e.get("days") or []) == 2] \
        if isinstance(v, list) else []


def accepted_stamp(acc: Optional[list]) -> str:
    import json
    return json.dumps([{k: e.get(k) for k in ("week", "days", "minutes")} for e in acc or []], sort_keys=True)


def accepted_for(acc: Optional[list], monday: dt.date) -> Optional[dict]:
    """The accepted entry of the week starting `monday` (or None)."""
    m = _d(monday).isoformat()
    return next((e for e in acc or () if e.get("week") == m), None)


def accepted_spans(acc: Optional[list]) -> list[dict]:
    """Accepted entries as history() / post rows {start, end}."""
    return [{"start": min(e["days"]), "end": max(e["days"]), "accepted": True} for e in acc or ()]


def accepted_post(acc: Optional[list], monday: dt.date) -> Optional[dict]:
    """The accepted B2B that ended in the 3 days before `monday` (→ the 4 easy days)."""
    rows = [r for r in accepted_spans(acc) if monday - dt.timedelta(days=3) <= _d(r["end"]) < monday]
    return max(rows, key=lambda r: r["end"]) if rows else None


def apply_accepted(info: dict, entry: Optional[dict]) -> dict:
    """An accepted entry for this week: the B2B is in the plan (due), with the
    user's days and minutes, whatever the rules say now (it is the user's)."""
    if not entry:
        return info
    info.update(due=True, accepted=True, suggest=False, days=2, pair=list(entry["days"]),
                minutes=[int(m) for m in entry.get("minutes") or []] or info.get("minutes"))
    return info


# ---------------------------------------------------------------------------
# is it due this week?
# ---------------------------------------------------------------------------

def last_was_recovery(hours: list[float]) -> bool:
    """`hours` = complete weeks, oldest first. The last one is clearly lighter
    than the 3 before it (the 3:1 recovery week is 0.65 of them; 0.85 推估)."""
    if len(hours) < 4:
        return False
    prev = sum(hours[-4:-1]) / 3.0
    return prev > 0.5 and hours[-1] <= RECOVERY_SHARE * prev


def week_context(*, kind: str, mode: str, monday: dt.date, event: Optional[dict], last_recovery: bool,
                 state: dict, tsb: Optional[float] = None, ramp: Optional[float] = None,
                 guard_ok: bool = True, post_from: Optional[dict] = None) -> dict:
    """Everything about B2B for one week except the base-built check (needs
    the long-run minutes: finalize()). `event`: event_json(); `state`:
    history(); `tsb`: TSB at the start of the week (Monday's, so the answer
    doesn't change mid-week after day 1); `post_from`: the B2B that ended just
    before this week (an accepted one, or a projected one)."""
    sunday = monday + dt.timedelta(days=6)
    info = {"event": event, "candidate": False, "suggest": False, "due": False, "accepted": False, "days": 2,
            "index": int(state.get("count") or 0) + 1, "why": [], "blocked": [], "last": state.get("last"),
            "count": int(state.get("count") or 0), "post": None, "src": _(SRC_WHEN), "week": monday.isoformat()}
    if post_from and kind == "specific" and qualifies(event):
        end = _d(post_from["end"])
        if monday - dt.timedelta(days=3) <= end < monday:
            until = end + dt.timedelta(days=POST_EASY_DAYS)
            info["post"] = {"start": post_from["start"], "end": post_from["end"], "until": until.isoformat(),
                            "src": _(SRC_POST)}
    if kind != "specific":
        info["blocked"].append(_("只在專項期排"))
        return info
    if not qualifies(event):
        info["blocked"].append(_("下一場 A 賽事不是多日、也不到 6 小時") if event else _("沒有下一場 A 賽事"))
        return info
    if mode in ("recovery_week", "reentry"):
        info["blocked"].append(_("恢復週／停訓後恢復期不排"))
        return info
    days_before = (_d(event["start"]) - sunday).days
    if days_before < LAST_BEFORE_DAYS:
        info["blocked"].append(_("離賽事不到 3 週（{n} 天）：最後一次 B2B 要在賽前 ≥ 3 週", n=days_before))
        return info
    multi = int(event.get("days") or 1) > 1
    last = _d(state.get("last"))
    if last is not None and (monday - last).days < SPACING_DAYS:
        info["blocked"].append(_("上一次 B2B（{day}）不到 2 週", day=md(last)))
        return info
    if last_recovery:
        info["why"].append(_("上週是恢復週：3:1 的第一個加量週"))
    else:
        info["blocked"].append(_("每個 3:1 週期最多一次，排在恢復週後的第一個加量週"))
        return info
    if tsb is not None and tsb < TSB_MIN:
        info["blocked"].append(_("週初 TSB {tsb:+.0f} < {min:.0f}：先恢復", tsb=tsb, min=TSB_MIN))
        return info
    if ramp is not None and ramp >= RAMP_MAX:
        info["blocked"].append(_("CTL 每週 +{ramp:.1f}（≥ {max:.0f}）", ramp=ramp, max=RAMP_MAX))
        return info
    if not guard_ok:
        info["blocked"].append(_("間歇門檻的護欄沒過（低強度比例或飄移）"))
        return info
    info["candidate"] = True
    info["weeks_out"] = -(-(days_before + 6) // 7)          # 賽前第 n 週 (the week's Monday)
    info["why"].append(_("賽前 {n} 天（{name}，{kind}）", n=days_before, name=event.get("name") or _("A 賽事"),
                         kind=_("多日") if multi else _("單日 ≥ 6 小時")))
    return info


def finalize(info: dict, long_min: float, longest_before: float, total_min: float) -> dict:
    """The base-built check and the day minutes. Sets info["suggest"] /
    ["minutes"] (a suggestion: nothing is scheduled until it is accepted)."""
    if not info.get("candidate") or info.get("accepted"):
        return info
    if longest_before < LONGEST_FRAC * long_min:
        info["blocked"].append(_("基礎還不夠：近 28 天最長 {longest:.0f} 分 < 第 1 天 {day1:.0f} 分 × {frac}",
                                 longest=longest_before, day1=long_min, frac=LONGEST_FRAC))
        return info
    mins = minutes(long_min, total_min, 2, info.get("event") or {})
    if mins is None:
        info["blocked"].append(_("本週的量排不下兩天長天（第 2 天會少於 60 分）"))
        return info
    info["suggest"] = True
    info["minutes"] = mins
    return info


def minutes(long_min: float, total_min: float, days: int, event: dict) -> Optional[list[int]]:
    """[day 1, day 2] minutes; None when day 2 would be < MIN_DAY2. `days`: 2
    (kept as a parameter for the callers; the 3-day version was dropped)."""
    n = 1
    d1 = float(long_min)
    single = int(event.get("days") or 1) <= 1
    d2 = DAY2_RATIO * d1
    if single:
        d2 = min(max(d2, SINGLE_DAY2[0]), SINGLE_DAY2[1], d1)
    cap = PAIR_SHARE * total_min
    if d1 + n * d2 > cap > 0:
        f = cap / (d1 + n * d2)
        d1, d2 = d1 * f, d2 * f
    if d2 < MIN_DAY2:
        return None
    return [_r5(d1)] + [_r5(d2)] * n


# ---------------------------------------------------------------------------
# week_plan / projection glue
# ---------------------------------------------------------------------------

def plan_context(ds, status, today: dt.date, monday: dt.date, hist_hours: list[float], ctl_s, atl_s,
                 d_prev_sun: int, by: dict, mode: str, accepted: Optional[list] = None) -> dict:
    """week_context() from week_plan()'s data: the next A event, the B2B
    weekends done / accepted in this 專項期, last week's hours (3:1 position),
    TSB at the start of the week, the status ramp and the gate's guardrails;
    then this week's accepted entry (`accepted`: load_accepted()). Never
    raises: a failure is just "no B2B"."""
    try:
        from backend.engine import overview as O
        ev = event_json(target_event(status.plan.events, today))
        phase = getattr(status, "phase", None)
        since = _d(phase.start) if phase is not None and phase.kind == "specific" else monday - dt.timedelta(weeks=12)
        rows = [(O.wdate(w), O.moving_s(w) / 60.0, w.idx)
                for w in O.workouts_between(ds, min(since, monday - dt.timedelta(days=28)), monday)
                if O.category(w) in O.ENDURANCE]
        done = [b for b in detect(rows) if _d(b["start"]) >= since]
        state = history(done + accepted_spans(accepted), since, monday)
        lo = monday - dt.timedelta(days=28)
        longest = max((m for d, m, _ in rows if d >= lo), default=0.0)
        tsb = O._n(ctl_s.at(d_prev_sun) - atl_s.at(d_prev_sun))
        ramp = ((getattr(by.get("fitness"), "extra", None) or {}).get("ramp_week")) if by else None
        guard = ((getattr(by.get("gate"), "extra", None) or {}).get("guard") or {}) if by else {}
        info = week_context(kind=status.kind or "base", mode=mode, monday=monday, event=ev,
                            last_recovery=last_was_recovery(hist_hours), state=state, tsb=tsb, ramp=ramp,
                            guard_ok=not guard.get("block"), post_from=accepted_post(accepted, monday))
        info.update(longest_before=longest, ramp=ramp, tsb_week_start=tsb,
                    weight=status.plan.weight_on(today) if getattr(status, "plan", None) else None)
        return apply_accepted(info, accepted_for(accepted, monday))
    except Exception as e:                  # noqa: BLE001 — the plan must still build
        return apply_accepted({"due": False, "candidate": False, "suggest": False, "post": None,
                               "error": type(e).__name__}, accepted_for(accepted, monday))


def placed(info: dict, kept: list[dict]) -> None:
    """After place(): the B2B days' real minutes (the caps may have cut day 2)."""
    if info.get("due"):
        days = sorted((s for s in kept if s.get("id") in ("long",) + FOLLOWERS), key=lambda s: s["id"])
        if days:
            info["minutes"] = [s["minutes"] for s in days]


PUBLIC = ("event", "candidate", "suggest", "due", "accepted", "pair", "days", "index", "why", "blocked", "last",
          "count", "post", "minutes", "src", "error", "weight", "weeks_out", "week")


def public(info: Optional[dict]) -> Optional[dict]:
    """The JSON part of the week's B2B state (week_plan()["b2b"])."""
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}


def next_state(info: Optional[dict], monday: dt.date, ss: Optional[list] = None) -> dict:
    """The block state carried into the next week (projection). `ss`: the
    week's sessions — the B2B's real days (else Sat–Sun / Fri–Sun)."""
    info = info or {}
    st = {"last": info.get("last"), "count": int(info.get("count") or 0)}
    if info.get("due"):
        days = sorted(s["day"] for s in ss or [] if s.get("id") in ("long",) + FOLLOWERS and s.get("day")) \
            or sorted(info.get("pair") or [])
        end = _d(days[-1]) if days else monday + dt.timedelta(days=6)
        start = _d(days[0]) if days else end - dt.timedelta(days=1)
        st = {"last": end.isoformat(), "count": st["count"] + 1,
              "post_from": {"start": start.isoformat(), "end": end.isoformat()}}
    return st


def projected(kind: str, mode: str, monday: dt.date, event: Optional[dict], prev_mode: Optional[str],
              state: dict, long_min: float, longest: float, total_min: float,
              accepted: Optional[list] = None) -> dict:
    """week_context() + finalize() for a projected week (TSB / ramp re-checked
    when the week comes), then the week's accepted entry. `state`:
    next_state() of the week before (its post_from: the week before's accepted
    B2B, if any)."""
    info = week_context(kind=kind, mode=mode, monday=monday, event=event, last_recovery=prev_mode == "recovery_week",
                        state=state, post_from=state.get("post_from") or accepted_post(accepted, monday))
    entry = accepted_for(accepted, monday)
    if entry is None:
        finalize(info, long_min, longest, total_min)
    return apply_accepted(info, entry)


def suggestion(info: Optional[dict], monday: dt.date, long_day: Optional[str] = None,
               enabled: bool = True) -> Optional[dict]:
    """The B2B suggestion of a week (None when nothing is suggested or 課表偏好
    b2b is off): why, the durations and the generator's long day (the day pair
    options come from api/plan_sessions, which knows the stored plan)."""
    if not enabled or not info or not info.get("suggest") or info.get("due") or not info.get("minutes"):
        return None
    ev = info.get("event") or {}
    m1, m2 = info["minutes"][:2]
    return {"id": f"b2b:{monday.isoformat()}", "type": "b2b", "week": monday.isoformat(),
            "title": _("建議這週做一次 B2B（連續兩天長天）：第 1 天 {m1} 分、第 2 天 {m2} 分", m1=m1, m2=m2),
            "reason": _("；").join(info.get("why") or []), "minutes": [m1, m2], "long_day": long_day,
            "event": ev.get("name"), "event_days": int(ev.get("days") or 1), "event_kind": ev.get("kind"),
            "weeks_out": info.get("weeks_out"), "index": info.get("index"),
            "help": _("專項期、恢復週後的第一個加量週，下一場 A 賽事（{name}）{kind}。第 1 天是這週的長天，第 2 天約 2/3"
                      "（CTS 30:20），兩天都心率 ≤ AeT；第 2 天從輕鬆跑的時間扣，這週總量不變（Koop）。"
                      "排入後之後 {n} 天只排輕鬆跑（UA），TSB 下降不改成恢復週（推估）。"
                      "你選的兩天會變成你自己的課，自動調整不會動它們。",
                      name=ev.get("name") or _("A 賽事"),
                      kind=_("是多日") if int(ev.get("days") or 1) > 1 else _("≥ 6 小時"), n=POST_EASY_DAYS),
            "src": _(SRC_WHEN) + _("；") + _(SRC_DAYS)}


def pair_options(monday: dt.date, first: dt.date, minutes: list, blocked=frozenset(),
                 allowed: Optional[Callable] = None, weekday_cap: Optional[int] = None,
                 busy: Optional[set] = None, long_day: Optional[str] = None) -> list[dict]:
    """Consecutive day pairs (d, d + 1) inside the week for a suggested B2B:
    both from `first`, not blocked, allowed by 課表偏好, a weekday only when
    its minutes fit the weekday cap, and neither day `busy` (a done session,
    or one of the user's own hard / long sessions). Weekend first, then the
    pairs that keep the generator's long day; the weekend pair is marked."""
    week = [monday + dt.timedelta(days=i) for i in range(7)]
    busy = busy or set()
    out = []
    for d1, d2 in zip(week, week[1:]):
        ok = True
        for d, m in ((d1, minutes[0]), (d2, minutes[1])):
            iso = d.isoformat()
            if d < first or iso in blocked or iso in busy or (allowed is not None and not allowed(d)):
                ok = False
            elif weekday_cap is not None and d.weekday() < 5 and m > weekday_cap:
                ok = False
        if not ok:
            continue
        wknd = d1.weekday() == 5
        keeps = long_day in (d1.isoformat(), d2.isoformat())
        note = _("週末") if wknd else (_("週五＋週六：可能要請一天假") if d1.weekday() == 4 else _("平日"))
        out.append(((not wknd, not keeps, d1.isoformat()),
                    {"day": d1.isoformat(), "end": d2.isoformat(),
                     "label": _("{d1}（{w1}）＋{d2}（{w2}）", d1=md(d1), w1=wd(d1), d2=md(d2), w2=wd(d2)),
                     "note": note}))
    return [o for _, o in sorted(out, key=lambda x: x[0])]


# ---------------------------------------------------------------------------
# the TSB exception
# ---------------------------------------------------------------------------

def tsb_exempt(info: Optional[dict], tsb: Optional[float], ramp: Optional[float] = None) -> Optional[str]:
    """Why a low TSB this week is the planned B2B's expected drop (or None).
    A CTL ramp ≥ 8 is beyond the expected drop: no exception."""
    if not info or tsb is None or tsb >= TSB_MIN:
        return None
    if ramp is not None and ramp >= RAMP_MAX:
        return None
    if info.get("due"):
        return _("TSB {tsb:+.0f}：本週有你排入的 B2B，TSB 下降是預期的，不改成恢復週、量不砍（推估）", tsb=tsb)
    if info.get("post"):
        p = info["post"]
        return _("TSB {tsb:+.0f}：上週末 B2B（{start}–{end}）造成的預期下降，"
                 "只把之後 {n} 天排輕鬆、不改成恢復週，3:1 照常（推估）",
                 tsb=tsb, start=md(p["start"]), end=md(p["end"]), n=POST_EASY_DAYS)
    return None


def fatigue_exempt(info: Optional[dict], today: str) -> Optional[str]:
    """adapt rule E: the TSB < −30 trigger alone is skipped during an accepted
    B2B week and the easy days after one (the red streak and the ramp still act)."""
    if not info:
        return None
    if info.get("due"):
        return _("本週有你排入的 B2B")
    p = info.get("post")
    if p and today <= p["until"]:
        return _("上週末 B2B（{start}–{end}）後的輕鬆日", start=md(p["start"]), end=md(p["end"]))
    return None


def post_note(info: dict) -> Optional[dict]:
    p = info.get("post")
    if not p:
        return None
    return {"level": "info", "src": "b2b",
            "text": _("上週末 B2B（{start}–{end}）：到 {until} 只排輕鬆跑和肌力、不排間歇和測試；"
                      "TSB 下降是預期的，不改成恢復週，3:1 照常（{src}）",
                      start=md(p["start"]), end=md(p["end"]), until=md(p["until"]), src=_(SRC_POST))}


# ---------------------------------------------------------------------------
# the sessions
# ---------------------------------------------------------------------------

def followers(long_s: dict, info: dict) -> list[dict]:
    """long2 for the template, minutes from the accepted entry (or finalize()).
    The long session's own minutes are set to day 1's. Text: decorate()."""
    mins = list(info["minutes"])[:2]
    rate = _rate(long_s)
    long_s["minutes"] = mins[0]
    long_s["tss"] = round(rate * mins[0], 1)
    return [{"id": f"long{i}", "kind": "long", "title": _("B2B 第 {i} 天", i=i), "minutes": m, "target": "",
             "detail": "", "source": _(SRC_DAYS), "tss": round(rate * m, 1), "day": None, "done": False,
             "done_by": None, "terrain": long_s.get("terrain")}
            for i, m in enumerate(mins[1:], 2)]


def _rate(s: dict) -> float:
    m = s.get("minutes") or 0
    return float(s.get("tss") or 0.0) / m if m else 0.0


def decorate(ss: list[dict], info: dict, aet: Optional[float], long_cap: Optional[int] = None,
             weight: Optional[float] = None) -> None:
    """Titles, details (Chinese, with sources) and HR-only targets of the B2B
    days, after the 課表偏好 shaping (which may rename / re-kind the long run or
    cap it): day 2 follows day 1's kind and terrain, ≤ the long-day cap and
    ≤ 2/3 of day 1 (or the single-day clamp)."""
    long_s = next((s for s in ss if s.get("id") == "long"), None)
    fol = [s for s in ss if s.get("id") in FOLLOWERS]
    if long_s is None or not fol:
        return
    n = 1 + len(fol)
    ev = info.get("event") or {}
    aet_t = f" {aet:.0f} bpm" if aet else ""
    hr_t = _("心率 ≤ AeT{bpm}", bpm=aet_t) if aet else _("心率 ≤ AeT")
    rate = _rate(long_s)
    single = int(ev.get("days") or 1) <= 1
    for s in fol:
        lim = DAY2_RATIO * long_s["minutes"]
        if single:
            lim = min(max(lim, SINGLE_DAY2[0]), SINGLE_DAY2[1], long_s["minutes"])
        m = min(s["minutes"], _r5(lim))
        if long_cap is not None:
            m = min(m, long_cap)
        s["minutes"] = int(m)
        s["kind"] = long_s["kind"]
        s["terrain"] = long_s.get("terrain")
        s["tss"] = round(rate * s["minutes"], 1)
        s["target"] = hr_t
    pack = ""                    # no pack in training (engine/steep_hill.py simulates it with grade)
    fuel = _("練比賽補給：每小時 30–60 g 醣，超過 2.5 小時可到 90 g/h；當晚要吃回來，第 2 天才是在練「接續的一天」（Burke 2011）。")
    if long_s["minutes"] < FUEL_MIN_H * 60:
        fuel += _("Koop：練補給的長跑至少 {h:.0f} 小時，本週先練到 {m} 分。", h=FUEL_MIN_H, m=long_s["minutes"])
    base_title = long_s["title"].split("｜", 1)[-1]
    # the generator's / 課表偏好's terrain hint (e.g. 「挑每公里爬升 ≥ 56 m 的路線」) stays a suggestion
    old = long_s.get("detail") or ""
    terrain = "" if old.startswith("B2B") else re.split(r"；|; ", old, maxsplit=1)[0].strip()
    climb = _("建議挑爬升比較多的路線（{terrain}；建議，不強制）", terrain=terrain) if terrain \
        else _("建議挑爬升比較多的路線（建議，不強制）")
    long_s["title"] = _("B2B 第 1 天｜{title}", title=base_title)
    long_s["target"] = hr_t
    long_s["detail"] = (_("B2B 第 1 天（共 {n} 天）：這週最長的一天，{climb}；全程{hr}，爬坡用走的守住上限，不放間歇。",
                          n=n, climb=climb, hr=hr_t) + f"{fuel}{pack}").rstrip()
    long_s["source"] = _(SRC_DAYS)
    for s in fol:
        i = int(s["id"][-1])
        share = _("約第 1 天的 2/3（CTS 30:20）") if not single else _("1.5–2.5 小時輕鬆（推估）")
        s["title"] = _("B2B 第 {i} 天｜{title}", i=i, title=base_title)
        s["detail"] = (_("B2B 第 {i} 天：{share}，{m} 分；{hr}，不加速；建議下坡多一點（不強制），練多日下坡"
                         "（Bontemps 2025 重複負荷效應）。", i=i, share=share, m=s["minutes"], hr=hr_t)
                       + _("跟第 1 天比同樣爬坡速度的心率、同樣心率的爬坡速度，總覽的 B2B 卡會判讀。"))
        s["source"] = _(SRC_DAYS)


def done_follow(ss: list, s, day: dt.date) -> bool:
    """Mark-done helper: a long2 activity must be the day after day 1 when
    that one is done (objects or dicts)."""
    get = (lambda x, k: x.get(k)) if isinstance(s, dict) else getattr
    prev = next((x for x in ss if get(x, "id") == "long"), None)
    if prev is None or not get(prev, "done") or not get(prev, "day"):
        return True
    return (day - _d(get(prev, "day"))).days == 1


def place(ss: list[dict], monday: dt.date, first: Optional[dt.date], blocked=frozenset(),
          allowed: Optional[Callable] = None, notes: Optional[list] = None,
          weekday_cap: Optional[int] = None, fixed: Optional[list] = None) -> list[dict]:
    """Put the B2B days on consecutive days around the placed long run:
    (L, L+1) or (L−1, L) — inside the week, from `first`, not blocked,
    allowed by 課表偏好, no done or hard session on them, and a weekday only
    when its B2B minutes fit 課表偏好's weekday cap (weekends: the long-day
    cap, applied by decorate()). `fixed`: the accepted pair (the user's days —
    taken as they are). Easy runs on those days swap to the days the B2B
    left; a quality session closer than 2 days moves or is dropped (48 h
    rule). No 2 days → day 2 dropped (the long run stays: an ordinary long
    day). Returns the kept sessions (a new list; the dicts are edited in place)."""
    long_s = next((s for s in ss if s.get("id") == "long" and (s.get("day") or fixed)), None)
    fol = sorted([s for s in ss if s.get("id") in FOLLOWERS], key=lambda s: s["id"])
    if not fol:
        return ss

    def note(text: str) -> None:
        if notes is not None:
            notes.append({"level": "info", "src": "b2b", "text": text})

    if long_s is None:
        note(_("B2B 第 1 天本週排不進去：第 2 天也取消（推估）"))
        return [s for s in ss if s not in fol or s.get("done")]
    if not any(not s.get("done") for s in fol):
        return ss
    week = [monday + dt.timedelta(days=i) for i in range(7)]
    L = _d(long_s.get("day") or fixed[0])
    block_ids = {id(long_s)} | {id(s) for s in fol}
    others = [s for s in ss if id(s) not in block_ids]

    def day_ok(d: dt.date, s: dict) -> bool:
        if d not in week or d.isoformat() in blocked or (allowed is not None and not allowed(d)):
            return False
        if first is not None and d < first:
            return False
        if weekday_cap is not None and d.weekday() < 5 and s["minutes"] > weekday_cap:
            return False
        for x in others:
            if x.get("day") == d.isoformat() and (x.get("done") or x.get("kind") in ("quality", "test", "race")):
                return False
        return True

    def find(fol_n: list[dict]) -> Optional[list[dt.date]]:
        n = 1 + len(fol_n)
        seq = [long_s] + fol_n
        cands = [[L + dt.timedelta(days=i - k) for i in range(n)] for k in range(n)]
        for c in cands:
            if long_s.get("done") and c[0] != L:
                continue
            if all((s.get("done") and s.get("day") == d.isoformat()) or (not s.get("done") and day_ok(d, s))
                   for s, d in zip(seq, c)):
                return c
        return None

    block = sorted(_d(x) for x in fixed)[:2] if fixed else find(fol)
    if block is None:
        note(_("本週沒有連續 2 天可以練：B2B 第 2 天取消，這週照一般長天（推估）"))
        return [s for s in ss if s not in fol or s.get("done")]
    pending = [s for s in fol if not s.get("done")]
    old = ({long_s.get("day")} | {s["day"] for s in pending if s.get("day")}) - {None}
    if not long_s.get("done"):
        long_s["day"] = block[0].isoformat()
    for s, d in zip(fol, block[1:]):
        if not s.get("done"):
            s["day"] = d.isoformat()
    taken = {d.isoformat() for d in block}
    vacated = sorted(x for x in old if x not in taken)
    # easy runs / strength on the B2B days go to the days the B2B left (else a free allowed day)
    main_days = {x["day"] for x in others if x.get("day") and x.get("kind") not in ("strength", "heat_passive", "notice")}
    free = [d.isoformat() for d in week if d.isoformat() not in taken and d.isoformat() not in main_days
            and d.isoformat() not in blocked and (allowed is None or allowed(d)) and (first is None or d >= first)]
    spare = vacated + [d for d in free if d not in vacated]
    kept = list(ss)
    for x in others:
        if x.get("done") or x.get("day") not in taken:
            continue
        if x.get("kind") == "strength":
            continue                    # strength after the run is fine (UA: on easy days or after a run)
        if x.get("kind") in ("easy", "heat_passive", "notice"):
            nxt = next((d for d in spare if d not in {y.get("day") for y in kept if y is not x
                                                       and y.get("kind") not in ("strength", "heat_passive", "notice")}),
                       None)
            if nxt is None:
                kept.remove(x)
                if notes is not None:
                    notes.append({"level": "info", "src": "b2b", "text": _("B2B 佔了週末：{wd}的輕鬆跑排不下，不用補", wd=wd(x["day"]))})
            else:
                x["day"] = nxt
    # 48 h between hard days and the B2B days
    for q in [x for x in kept if x.get("kind") in ("quality", "test") and not x.get("done") and x.get("day")]:
        if all(abs((_d(q["day"]) - d).days) >= 2 for d in block):
            continue
        busy = {y.get("day") for y in kept if y is not q and y.get("kind") not in ("strength", "heat_passive", "notice")}
        hard = [_d(y["day"]) for y in kept if y is not q and y.get("kind") in ("quality", "test") and y.get("day")]
        ok = [d for d in week if d.isoformat() not in busy and d.isoformat() not in blocked
              and (allowed is None or allowed(d)) and (first is None or d >= first)
              and all(abs((d - b).days) >= 2 for b in block + hard)]
        if ok:
            q["day"] = ok[0].isoformat()
        else:
            kept.remove(q)
            if notes is not None:
                notes.append({"level": "info", "src": "b2b",
                              "text": _("{title}離 B2B 不到 48 小時、本週沒有別的空日：這週先不排", title=q["title"])})
    return kept


# ---------------------------------------------------------------------------
# evaluation: day 2 vs day 1 (no RPE)
# ---------------------------------------------------------------------------

def climb_windows(rows: Iterable[dict]) -> list[dict]:
    """Climbing 100 m windows of one day ({"k", "g", "v", "hr", "z"?}):
    grade ≥ 10 %, moving > 1.5 km/h, HR present, in runs of ≥ 3 consecutive
    windows (hikehr.HIKE_FILTER without its HR ≥ AeT floor — the B2B days are
    ≤ AeT by design); VAM ≤ the VK world-record rate."""
    from backend.engine.racepower import hikehr as HH
    f = HH.HIKE_FILTER
    ok = sorted([r for r in rows if r.get("hr") and r.get("v") is not None and r["v"] * 3.6 > f["min_kmh"]
                 and r.get("g") is not None and r["g"] >= f["min_grade"]], key=lambda r: r.get("k", 0))
    out, run = [], []
    for r in ok + [None]:
        if run and (r is None or r.get("k") != run[-1].get("k", -10) + 1):
            if len(run) >= f["min_run"]:
                out.extend(run)
            run = []
        if r is not None:
            run.append(r)
    wins = [dict(r, vam=r["v"] * r["g"] * 3600.0) for r in out]
    return [w for w in wins if w["vam"] <= HH.VAM_MAX]


def day_stats(rows: list[dict], aet: Optional[float]) -> dict:
    """§1.3 ①: share of the moving time with HR > AeT + 3 (time = 100 m / v),
    the climbing (sum of positive window elevation steps) and the minutes."""
    from backend.engine import workout_review as WR
    rows = [r for r in rows if r.get("v") and r["v"] > 0]
    t = [100.0 / r["v"] for r in rows]
    tot = sum(t)
    hr_t = sum(ti for ti, r in zip(t, rows) if r.get("hr"))
    over = sum(ti for ti, r in zip(t, rows) if aet and r.get("hr") and r["hr"] > aet + WR.AET_MARGIN)
    zs = [r["z"] for r in sorted(rows, key=lambda r: r.get("k", 0)) if r.get("z") is not None]
    climb = sum(max(0.0, b - a) for a, b in zip(zs, zs[1:]))
    return {"moving_min": round(tot / 60.0), "climb_m": round(climb), "over_aet_share": (over / hr_t) if hr_t and aet else None,
            "over_ok": None if not (hr_t and aet) else over / hr_t <= WR.OVER_AET_SHARE}


CELLS = {
    "durable": (N_("耐久性好"), "good", N_("第 2 天同樣的爬坡速度心率差不多、同樣心率的爬坡速度也沒掉")),
    "muscular": (N_("肌肉疲勞"), "watch",
                 N_("心率被壓住、腿出不了力（UA；Le Meur 2013、Kerhervé 2015 都看到心率下降）→ 下一次第 2 天縮短")),
    "cardio": (N_("心血管漂移或補給不足"), "watch", N_("同樣的速度要更高的心率（Coyle 2001）→ 檢查第 1 天和當晚的補給、熱、睡眠")),
    "uncommon": (N_("不常見：第 2 天心率較低、速度沒掉"), "info", N_("可能是第 1 天熱或脫水 → 先看第 1 天的 Hadley")),
    "insufficient": (N_("爬坡段不夠，不判讀"), "na", N_("第 1 天和第 2 天都要有 ≥ 5 段 100 m 的爬坡（坡度 ≥ 10%）")),
}


def _cell(key: str) -> tuple[str, str, str]:
    """(label, level, text) of a 2 × 2 cell in the request's language."""
    label, level, text = CELLS[key]
    return _(label), level, _(text)


def _vam_at(wins: list[dict], hr: float) -> Optional[float]:
    """VAM at `hr` from a least-squares VAM ~ HR line; None when HR doesn't
    vary (one speed) or the line runs the wrong way."""
    import numpy as np
    x = np.array([w["hr"] for w in wins], float)
    y = np.array([w["vam"] for w in wins], float)
    if len(x) < 2 or np.ptp(x) < 2.0:
        return None
    b, a = np.polyfit(x, y, 1)
    v = a + b * hr
    return float(v) if b > 0 and v > 0 else None


def classify(hr_shift: Optional[float], vam_ratio: Optional[float]) -> str:
    if hr_shift is None or vam_ratio is None:
        return "insufficient"
    low_hr = hr_shift <= -HR_SAME_BPM
    slow = vam_ratio < 1.0 - VAM_SAME
    if slow:
        return "muscular" if low_hr else "cardio"
    return "uncommon" if low_hr else "durable"


def evaluate(day1: list[dict], day2: list[dict], aet: Optional[float]) -> dict:
    """The 2 × 2 reading of day 2 against day 1 from their 100 m windows."""
    from backend.engine.racepower import hikehr as HH
    w1, w2 = climb_windows(day1), climb_windows(day2)
    fat = HH.fatigue([{**w, "trip": 0, "day": 1} for w in w1] + [{**w, "trip": 0, "day": 2} for w in w2])
    hr_shift = fat["days"][0]["hr_shift_bpm"] if fat["days"] else None
    vam_ratio, method = None, None
    b1 = b2 = []
    if aet:
        b1 = [w for w in w1 if aet - AET_BAND_BPM <= w["hr"] <= aet]
        b2 = [w for w in w2 if aet - AET_BAND_BPM <= w["hr"] <= aet]
        if len(b1) >= HH.FAT_MIN_N and len(b2) >= HH.FAT_MIN_N:
            # VAM at the band's middle HR from each day's own HR ~ VAM line over the band
            # windows (a band median alone follows how the windows spread inside the band:
            # a +6 bpm day 2 puts its faster climbs in the band) — interpolation 推估
            ref = aet - AET_BAND_BPM / 2.0
            v1, v2 = _vam_at(b1, ref), _vam_at(b2, ref)
            if v1 and v2:
                vam_ratio, method = v2 / v1, "line"
            else:
                m1, m2 = median(w["vam"] for w in b1), median(w["vam"] for w in b2)
                if m1 > 0:
                    vam_ratio, method = float(m2 / m1), "median"
    cell = classify(hr_shift, vam_ratio)
    label, level, text = _cell(cell)
    return {"cell": cell, "label": label, "level": level, "text": text,
            "hr_shift_bpm": None if hr_shift is None else round(hr_shift, 1),
            "vam_ratio": None if vam_ratio is None else round(vam_ratio, 3), "vam_method": method,
            "n": {"day1": len(w1), "day2": len(w2), "aet_band_day1": len(b1), "aet_band_day2": len(b2)},
            "day1": day_stats(day1, aet), "day2": day_stats(day2, aet),
            "thresholds": {"hr_bpm": HR_SAME_BPM, "vam": VAM_SAME, "label": _("推估")}, "src": _(SRC_EVAL)}


def trend(evals: list[dict]) -> dict:
    """Across the block's B2B weekends: is day 2's VAM drop getting smaller,
    and is 耐久性好 getting more common (Maunder 2021: onset and magnitude)."""
    pts = [e for e in evals if e.get("vam_ratio") is not None]
    judged = [e for e in evals if e.get("cell") != "insufficient"]
    out = {"n": len(evals), "judged": len(judged),
           "durable_share": (sum(e["cell"] == "durable" for e in judged) / len(judged)) if judged else None,
           "first": pts[0]["vam_ratio"] if pts else None, "last": pts[-1]["vam_ratio"] if pts else None,
           "direction": None, "text": "", "label": _("推估")}
    if len(pts) < 2:
        out["text"] = _("還要至少兩次有判讀的 B2B 才看得出趨勢")
        return out
    ch = pts[-1]["vam_ratio"] - pts[0]["vam_ratio"]
    out["change"] = round(ch, 3)
    out["direction"] = "better" if ch >= TREND_STEP else "worse" if ch <= -TREND_STEP else "flat"
    out["text"] = {"better": _("第 2 天的爬坡速度掉得比較少了：耐久性在進步"),
                   "worse": _("第 2 天掉得比上次多：看睡眠、補給，下一次第 2 天縮短"),
                   "flat": _("第 2 天的衰退差不多")}[out["direction"]] \
        + _("（{a:.0%} → {b:.0%}）", a=pts[0]["vam_ratio"], b=pts[-1]["vam_ratio"])
    return out


def _windows(ds, w) -> list[dict]:
    """100 m windows of one activity (racepower.athlete hike rows: works for
    runs too), every calendar day of it together."""
    from backend.engine.racepower import athlete as A
    if hasattr(ds, "cached_series"):
        rows = ds.cached_series(A.HIKE_KEY, w, lambda: A._hike_windows(ds, w))
    else:
        rows = A._hike_windows(ds, w)
    return [{"g": g, "v": v, "z": z, "hr": hr, "k": k} for g, v, z, hr, k, _day, _t, _lag in rows or []]


def evaluate_pair(ds, w1, w2, aet: Optional[float]) -> dict:
    return evaluate(_windows(ds, w1), _windows(ds, w2), aet)


def card(ds, today: dt.date, events, phase=None, cur: Optional[dict] = None, weeks: Optional[list] = None,
         aet_of: Optional[Callable] = None) -> dict:
    """The overview's B2B card: the event, this week / the planned weekends,
    every done B2B in the block with its 2 × 2 reading, and the trend.
    `phase`: the current planning.Phase; `cur` / `weeks`: week_plan() and the
    projection (their "b2b" entries); `aet_of(date)`: AeT on a day."""
    from backend.engine import overview as O
    ev = target_event(events, today)
    since = _d(phase.start) if phase is not None and phase.kind == "specific" else today - dt.timedelta(days=120)
    rows = [(O.wdate(w), O.moving_s(w) / 60.0, w.idx)
            for w in O.workouts_between(ds, since, today + dt.timedelta(days=1)) if O.category(w) in O.ENDURANCE]
    by_idx = {w.idx: w for w in ds.workouts}
    done = []
    for b in detect(rows):
        w1, w2 = by_idx.get(b["idx"][0]), by_idx.get(b["idx"][1])
        aet = aet_of(_d(b["start"])) if aet_of else None
        try:
            r = evaluate_pair(ds, w1, w2, aet) if w1 is not None and w2 is not None else None
        except Exception as e:             # noqa: BLE001 — one broken file never breaks the card
            r = {"cell": "insufficient", **dict(zip(("label", "level", "text"), _cell("insufficient"))),
                 "error": type(e).__name__}
        done.append({**b, "aet": aet, "eval": r})
    planned = []
    cb = (cur or {}).get("b2b") or {}
    if cb.get("due"):
        planned.append({"week": (cur or {}).get("week", {}).get("start"), "days": cb.get("days"),
                        "minutes": cb.get("minutes"), "this_week": True,
                        "sessions": [{"id": s["id"], "day": s.get("day"), "minutes": s["minutes"], "done": s.get("done")}
                                     for s in (cur or {}).get("sessions") or [] if s.get("id") in ("long",) + FOLLOWERS]})
    for w in weeks or []:
        b = w.get("b2b") or {}
        if b.get("due"):
            planned.append({"week": w["start"], "days": b.get("days"), "minutes": b.get("minutes"), "this_week": False,
                            "sessions": [{"id": s["id"], "day": s.get("day"), "minutes": s["minutes"]}
                                         for s in w.get("sessions") or [] if s.get("id") in ("long",) + FOLLOWERS]})
    evs = [d["eval"] for d in done if d.get("eval")]
    return {"today": today.isoformat(), "event": event_json(ev), "active": qualifies(event_json(ev)) or bool(done),
            "this_week": {k: cb.get(k) for k in ("due", "suggest", "accepted", "pair", "days", "index", "why", "blocked",
                                                 "post", "minutes")} if cb else None,
            "planned": planned, "done": done, "trend": trend(evs),
            "rules": {"when": _(SRC_WHEN), "days": _(SRC_DAYS), "post": _(SRC_POST), "eval": _(SRC_EVAL)},
            "cells": {k: dict(zip(("label", "level", "text"), _cell(k))) for k in CELLS}}
