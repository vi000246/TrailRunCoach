"""
技術地形課 in the generated plan (SP-74; the session type is SP-62's: time + climb + RPE, no
HR / power target — footing, not the heart, limits the pace on technical trail).

Only for 主要訓練項目 = 越野跑 (a road athlete gets none). Applied after the week is placed,
like engine/steep_hill.py, in week_plan and in every projected week (the same rule, so reconcile
never flips a week back and forth):

  * 基礎期 — none (SP-87, owner 2026-10-05; docs/research/trail-terrain-and-climb-sessions.md §4 #1,
    §5): the LSD stays on steady terrain — no study asks for technical terrain far from the race,
    and the long run's HR drift reads the aerobic base only on steady ground. The 基礎期's climbing
    is the weekly 長爬坡有氧 (engine/base_climb.py), which also carries the small downhill dose (SP-442).
    (SP-74 turned every other week's LSD into a low-RPE technical run.)
  * 專項期 — one 技術地形 session a week close to the race's terrain, out of a placed easy run, when
    the next A race's 土徑比 (planning.Event.trail_pct, SP-87 owner 2026-10-12) is ≥ TRAIL_PCT_MIN or
    not set; a mostly paved course (< 50 %) gets none (Koop: train on the race's terrain — the reverse
    holds too, 推估).
    RPE 6–7 (the template 「技術地形 90′」) counts as a quality session (workout_steps.rpe_role:
    RPE ≥ 7), so it needs a day ≥ 2 days from the long run and every hard day (台灣教練) and room
    in SP-31's budget: its RPE 6–7 work + the intervals' time in zone ≤ 20 % of the week (the
    Zone 3 ≤ 10 % bucket stays the intervals': technical terrain keeps HR low, SP-62 — 推估). Its work is
    min(90′, the room, the weekday cap − warm-up − cool-down); under SPEC_WORK_MIN, or no spaced
    day → the same session at RPE 4–5 (an easy session, no spacing / budget) on the easy run's
    time. A week note says which and why.

The load stays the watch's (no RPE correction, SP-62); the planned TSS uses the trail rate.

HR (owner 2026-10-05): the detail names SP-115's walking cap as a reference only — 「心率參考上限約 N
bpm（75% 最大心率）或 RPE ≤ 13；技術路段以安全為主，不用硬壓心率」 (hr_profile.walk_cap_hint; omitted
without a max HR or an age); the target policy, the watch steps and the push are not changed.

A 技術地形 session the user added (or an auto one they edited) whose RPE makes it a quality session
(workout_templates.session_role) counts like the generated one (SP-74 follow-up): its RPE ≥ 7 work
(`user_work_min`) comes off the week's 20 % before the intervals are fitted
(overview.quality_sessions `reserved`: they shrink, or go when nothing is left), and the 專項期
gets no second one. Read from the stored plan (plan_store.user_rpe_rows), per week, in week_plan
and in the projection alike.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _

SPEC_RPE = (6, 7)            # 技術地形 90′ — RPE 7 = a quality session (workout_steps.RPE_HARD_MIN)
SPEC_EASY_RPE = (4, 5)       # 專項期 without room / a spaced day: kept easy (推估)
CLIMB_PER_MIN = 600 / 90     # m per work minute: the template's 600 m / 90′
SPEC_WORK_MAX = 90           # the template's main block
SPEC_WORK_MIN = 30           # 推估: a shorter RPE 6–7 block is not worth a hard day
WARM, COOL = 15, 10          # the template's
EASY_MIN = 20                # the other easy runs give the difference, never below this
TRAIL_PCT_MIN = 50.0         # SP-87 (owner 2026-10-12): a race with ≥ 50 % dirt / trail gets the 專項期 session
HARD_IDS = ("long", "long2", "long3", "climb")   # B2B days and the race-climb repeats (steep_hill: its hard list)
SRC_PARTS = (N_("Koop（賽道專項：練和比賽相同的地形）"), "Uphill Athlete", N_("RPE 對強度預算的換算是這個 app 的建議（推估）"))
SRC = "；".join(SRC_PARTS)          # (SP-905: a session's 出處 is read part by part — the parts are msgids)


def _d(x) -> Optional[dt.date]:
    if not x:
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def week_context(*, kind: str, mode: str, monday: dt.date, road: bool, b2b: Optional[dict] = None,
                 injury: Optional[dict] = None, behind: Optional[str] = None, events=None) -> dict:
    """Whether this week gets a 技術地形 session and which rule: {"active", "phase", "why"}.
    `injury`: injuries.condition_week's rule (SP-270) — 膝前痛／髂脛束進行中 = no 技術地形. `behind`:
    the day of this week's stored one when it is already behind (plan_store.hook_days, SP-87) — no
    second one. `events`: the season plan's events — the next A race's 土徑比 (SP-87). `b2b`: this
    week's B2B info — its easy days after the pair (`post.until`) take no RPE 6–7 session."""
    info = {"active": False, "phase": kind, "monday": monday.isoformat()}
    if behind:
        b = _d(behind)
        return {**info, "why": _("本週的技術地形課已經排在 {day}，不另外排", day=f"{b.month}/{b.day}")}
    if road:
        return {**info, "why": _("主要訓練項目：路跑")}
    if kind not in ("base", "specific") or mode in ("recovery_week", "reentry"):
        return {**info, "why": _("只在基礎期、專項期（恢復週、停訓後恢復期不排）")}
    from backend.engine import injuries as INJ
    if INJ.avoids(injury, "technical"):
        return {**info, "why": "；".join(injury["notes"])}
    if kind == "base":
        # SP-87: the LSD stays on steady terrain (the module docstring)
        return {**info, "why": _("基礎期不排技術地形課：長跑留在穩定地形，爬升改排長爬坡有氧")}
    # SP-87: the easy days after an accepted B2B take no RPE 6–7 session (a hard day), only the easy one
    until = ((b2b or {}).get("post") or {}).get("until")
    if until:
        info["b2b_until"] = str(until)[:10]
    e = min((x for x in events or () if getattr(x, "priority", None) == "A" and x.start > monday),
            key=lambda x: x.start, default=None)
    pct = getattr(e, "trail_pct", None) if e is not None else None
    if pct is not None and float(pct) < TRAIL_PCT_MIN:
        return {**info, "trail_pct": float(pct),
                "why": _("{name} 土徑比 {pct:.0f}%（< {min:.0f}%）：路線多半是柏油或水泥，專項期不排技術地形課",
                         name=e.name, pct=float(pct), min=TRAIL_PCT_MIN)}
    return {**info, "active": True, **({"trail_pct": float(pct)} if pct is not None else {})}


def _steps(work: int, rpe: tuple) -> dict:
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    b = WT.B()
    up = int(round(work * CLIMB_PER_MIN / 50.0) * 50) or None
    items = [b.warm(WARM, "好走的路段暖身"),
             b.t("work", work * 60, WT.rpe(rpe[0], rpe[1], up=up), "接近比賽路況的技術路段"),
             b.t("cool", COOL * 60, WT.OPEN, "收操")]
    return WS.doc(items, origin="template:lib:tech_hard")


def _steps_doc(s: dict) -> Optional[dict]:
    steps = s.get("steps")
    if isinstance(steps, str):
        import json
        try:
            steps = json.loads(steps)
        except ValueError:
            return None
    return steps if isinstance(steps, dict) else None


def user_work_min(s: dict) -> float:
    """Minutes of a stored session's RPE ≥ 7 work (workout_steps.RPE_HARD_MIN): its timed steps,
    an open step by its estimate; a step with no time (distance) → the session's minutes (推估:
    the whole session counts rather than nothing)."""
    from backend.engine import workout_steps as WS
    doc = _steps_doc(s) or {}
    sec, unknown = 0.0, False
    for row in WS.flat(doc.get("items") or []):
        st = row["st"]
        tg = st.get("target") or {}
        if st.get("kind") not in ("work", "other") or tg.get("type") != "rpe" \
                or float(tg.get("hi") or 0) < WS.RPE_HARD_MIN:
            continue
        d = st.get("dur") or {}
        if d.get("type") == "time" and d.get("value"):
            sec += float(d["value"])
        elif d.get("type") == "open" and d.get("est"):
            sec += float(d["est"])
        else:
            unknown = True
    return float(s.get("minutes") or 0) if unknown or sec <= 0 else sec / 60.0


def user_quality(rows: list, monday: dt.date) -> list[dict]:
    """The user's own RPE ≥ 7 sessions in the week of `monday` (plan_store.user_rpe_rows rows
    whose workout_templates.session_role is "quality"): [{"uid", "day", "title", "work"}]. A kind
    quality row counts only when it is RPE-set (an RPE ≥ 7 work step, no library variant — the
    generated RPE 6–7 one is kind quality since SP-87); an interval never does."""
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    a, b = monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()
    out = []
    for r in rows or ():
        day = str(r.get("day") or "")[:10]
        if not (a <= day <= b) or r.get("kind") == "test" or r.get("state") not in ("active", "done"):
            continue
        doc = _steps_doc(r)
        if doc is None or WT.session_role({"steps": doc}) != "quality":
            continue
        if r.get("kind") == "quality" and (r.get("variant_key") or WS.rpe_role(doc.get("items") or []) != "quality"):
            continue
        out.append({"uid": r.get("uid"), "day": day, "title": r.get("title") or "",
                    "work": round(user_work_min({**r, "steps": doc}), 1)})
    return sorted(out, key=lambda x: x["day"])


def load_user() -> list[dict]:
    """plan_store.user_rpe_rows(), [] on any error (the plan must still build)."""
    try:
        from backend.engine.plan_store import user_rpe_rows
        return user_rpe_rows()
    except Exception:                       # noqa: BLE001
        return []


def user_stamp(rows: Optional[list] = None) -> tuple:
    """What of the user's RPE sessions changes the generated plan (a cache key part)."""
    rows = load_user() if rows is None else rows
    return tuple(sorted((str(r.get("uid")), str(r.get("day")), str(r.get("state")), round(user_work_min(r), 1))
                        for r in rows))


def user_note(user: list, hours: Optional[float], kind: str = "specific") -> Optional[dict]:
    """The week note for the user's own RPE ≥ 7 sessions in the budget."""
    from backend.engine import quality_gate as QG
    if not user:
        return None
    work = sum(u["work"] for u in user)
    total = QG.QUALITY_SHARE_MAX * hours * 60.0 if hours else None
    left = _("：強度課總量上限 {total:.0f} 分（週量 {share:.0%}）扣掉後剩 {left:.0f} 分給間歇",
             total=total, share=QG.QUALITY_SHARE_MAX, left=max(0.0, total - work)) if total is not None else ""
    names = "、".join(f"{u['day'][5:]} {u['title']}" for u in user)
    return {"level": "info", "src": "technical",
            "text": _("你排的技術地形課（{names}，RPE ≥ 7 算強度課）主課 {work:.0f} 分算進每週強度預算{left}",
                      names=names, work=work, left=left)
                    + (_("；本週不另外排技術地形課") if kind == "specific" else "") + _("（推估）")}


def _rpe_txt(rpe: tuple) -> str:
    return f"RPE {rpe[0]}–{rpe[1]}"


def _rate(rates: Optional[dict]) -> float:
    return float((rates or {}).get("trail") or (rates or {}).get("road") or 60.0)


def _rebalance(ss: list, me: dict, delta: int) -> None:
    """The week's total stays: the other easy runs give / take `delta` minutes (≥ EASY_MIN)."""
    for x in sorted((x for x in ss if x is not me and x.get("kind") == "easy" and not x.get("done")
                     and x.get("id") not in ("climb", "steep") and not x.get("heat")),
                    key=lambda x: -(x.get("minutes") or 0)):
        if delta <= 0:
            break
        m = int(x.get("minutes") or 0)
        new = max(EASY_MIN, m - delta)
        if new < m and m:
            x["tss"] = round(float(x.get("tss") or 0.0) * new / m, 1)
            x["minutes"] = new
            delta -= m - new


def _tiz(s: dict) -> float:
    from backend.engine.overview import session_tiz_min
    return session_tiz_min(s)


def budget_room(ss: list, hours: Optional[float]) -> tuple[Optional[float], str]:
    """Minutes of RPE 6–7 work the week's budget still takes (SP-31): 20 % of the week
    (QUALITY_SHARE_MAX) − the intervals' time in zone, and its text. The 技術地形 work is not put
    in the Zone 3 bucket (≤ 10 %, Z3_SHARE_MAX — Daniels' per-session T cap used per week, coach): on technical trail the footing, not the
    heart, sets the effort and HR stays low (SP-62), so it is hard time but not threshold time
    (推估); the Zone 3 intervals keep their own 10 %."""
    from backend.engine import quality_gate as QG
    if not hours or hours <= 0:
        return None, ""
    qs = [s for s in ss if s.get("kind") == "quality"]
    total = max(0.0, QG.QUALITY_SHARE_MAX * hours * 60.0 - sum(_tiz(s) for s in qs))
    return total, _("強度課總量上限（週量 {share:.0%}）扣掉間歇還剩 {total:.0f} 分",
                     share=QG.QUALITY_SHARE_MAX, total=total)


def _hint(walk: Optional[dict]) -> str:
    """「；心率參考上限約 N bpm（75% 最大心率）或 RPE ≤ 13；…」 (hr_profile.walk_cap_hint), "" without one."""
    from backend.engine.hr_profile import walk_cap_hint
    h = walk_cap_hint(walk)
    return "；" + h if h else ""


def apply(ss: list, info: Optional[dict], *, hours: Optional[float] = None, rates: Optional[dict] = None,
          prefs=None, notes: Optional[list] = None, hard_done=(), user=(), walk: Optional[dict] = None,
          **_kw) -> list:
    """Turn one easy run of a 專項期 week into the 技術地形 session, in place; sets info["planned"].
    `hard_done`: hard days already done this week (dates). `user`: the user's own RPE ≥ 7 sessions
    this week (user_quality) — the 專項期 one already, none added.
    `walk` (hr_profile.walk_cap, SP-115; owner 2026-10-05): the 75 % HRmax figure goes into the
    detail as guidance only — the session keeps no HR target (its RPE structure is what the watch
    gets: the warm-up at HR ≤ AeT, the main part by RPE), its steps and push are unchanged.
    Kind: `quality` for the RPE 6–7 one (SP-87; was hike), `hike` for the RPE 4–5 one."""
    if not info or not info.get("active") or info.get("phase") != "specific":
        return ss                                       # SP-87: never in the 基礎期
    info["planned"] = []
    if any(s.get("id") == "tech" for s in ss):
        return ss                                       # one a week
    if user:
        info["user"] = list(user)
        return ss                                       # the user's own is this week's (user_note says so)
    rate = _rate(rates)
    # 專項期: one session out of a placed easy run
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race")
                                                           or s.get("id") in HARD_IDS)]
    hard += [_d(d) for d in hard_done or ()]
    easy = sorted((s for s in ss if s.get("kind") == "easy" and not s.get("done") and s.get("day")
                   and s.get("id") not in ("climb", "steep") and not s.get("heat")),
                  key=lambda s: (-(int(s.get("minutes") or 0)), s["day"]))
    if not easy:
        if notes is not None:
            notes.append({"level": "info", "src": "technical",
                          "text": _("專項期每週 1 堂技術地形課：這週沒有可以換的輕鬆跑")})
        return ss
    cap = getattr(prefs, "cap_weekday", None) if prefs is not None and getattr(prefs, "active", False) else None
    long_cap = getattr(prefs, "long_cap", None) if prefs is not None and getattr(prefs, "active", False) else None
    until = _d(info.get("b2b_until"))
    spaced = [s for s in easy if all(abs((_d(s["day"]) - h).days) >= 2 for h in hard)
              and (until is None or _d(s["day"]) > until)]
    room, room_txt = budget_room(ss, hours)
    warm_cool = WARM + COOL

    def day_cap(s) -> Optional[float]:
        return long_cap if _d(s["day"]).weekday() >= 5 else cap

    pick, work, why = None, 0, ""
    for s in sorted(spaced, key=lambda s: (_d(s["day"]).weekday() < 5, s["day"])):   # weekend first: terrain
        c = day_cap(s)
        w = min(SPEC_WORK_MAX, room if room is not None else SPEC_WORK_MAX,
                (c - warm_cool) if c is not None else SPEC_WORK_MAX)
        w = int(w // 5 * 5)
        if w >= SPEC_WORK_MIN:
            pick, work = s, w
            break
    if pick is not None:
        rpe, role = SPEC_RPE, "quality"
        m = work + warm_cool
        bits = [_("主課 {work}′", work=work)]
        if room is not None and room < SPEC_WORK_MAX:
            bits.append(room_txt)
        why = _("專項期每週 1 堂技術地形 {m}′（{rpe}，接近比賽路況）：RPE 7 算強度課，"
                "排在離長跑和其他強度課 ≥ 2 天的日子；{bits}（RPE 6–7 的時間算進每週強度預算，推估）",
                m=m, rpe=_rpe_txt(rpe), bits="、".join(bits))
    else:
        pick = easy[0]
        rpe, role = SPEC_EASY_RPE, "easy"
        m = int(pick.get("minutes") or 0)
        work = m - warm_cool
        if work < 20:
            if notes is not None:
                notes.append({"level": "info", "src": "technical",
                              "text": _("專項期每週 1 堂技術地形課：輕鬆跑只有 {m} 分，放不下（需要 ≥ {need} 分）",
                                        m=m, need=warm_cool + 20)})
            return ss
        reason = _("本週強度預算不夠（{room}，需要 ≥ {need} 分）", room=room_txt, need=SPEC_WORK_MIN) \
            if spaced and room is not None and room < SPEC_WORK_MIN else \
            (_("沒有離長跑和其他強度課 ≥ 2 天、又不在 B2B 之後輕鬆日的日子") if until is not None else
             _("沒有離長跑和其他強度課 ≥ 2 天的日子")) if not spaced else _("這天的時間上限放不下")
        why = _("專項期每週 1 堂技術地形 {m}′，但{reason}：改成 {rpe}，算輕鬆課（不佔強度預算、不用隔 48 小時）",
                m=m, reason=reason, rpe=_rpe_txt(rpe))
    delta = m - int(pick.get("minutes") or 0)
    steps = _steps(work, rpe)
    climb = steps["items"][1]["target"].get("up")
    # RPE 6–7 = a quality session, and its kind says so (SP-87 §4 #6: adapt / reconcile key their
    # hard-day checks on the kind); RPE 4–5 stays a 越野跑 (hike) easy session
    pick.update(id="tech", kind="quality" if role == "quality" else "hike", terrain="trail", minutes=m, target="",
                steps=steps,
                title=f"技術地形 {m}′（{_rpe_txt(rpe)}）",
                # SP-905: msgids joined by 「；」 (each part is shown in the page's language, engine/stored_text)
                detail=((_("找接近比賽路況的技術路段，{rpe}，爬升約 {climb} m", rpe=_rpe_txt(rpe), climb=climb) if climb
                         else _("找接近比賽路況的技術路段，{rpe}", rpe=_rpe_txt(rpe)))
                        + "；" + _("心率、功率不當目標") + _hint(walk)
                        + ("；" + _("算強度課，前後一天輕鬆") if role == "quality" else "")),
                source="；".join(_(p) for p in SRC_PARTS), climb_m=float(climb) if climb else None,
                tss=round(rate * m / 60.0, 1))
    if delta > 0:
        _rebalance(ss, pick, delta)
    info["planned"].append({"day": pick["day"], "minutes": m, "rpe": list(rpe), "role": role, "work": work})
    if notes is not None:
        notes.append({"level": "info", "src": "technical", "text": why})
    return ss


PUBLIC = ("active", "phase", "why", "planned", "user", "trail_pct")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}
