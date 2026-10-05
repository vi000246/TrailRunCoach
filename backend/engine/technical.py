"""
技術地形課 in the generated plan (SP-74; the session type is SP-62's: time + climb + RPE, no
HR / power target — footing, not the heart, limits the pace on technical trail).

Only for 主要訓練項目 = 越野跑 (a road athlete gets none). Applied after the week is placed,
like engine/steep_hill.py, in week_plan and in every projected week (the same rule, so reconcile
never flips a week back and forth):

  * 基礎期 — every other week (an even ISO week number; 推估) the week's LSD becomes a low-RPE
    技術地形 run of the same time (RPE 3–4: an easy session in the long-run slot — Koop: train on
    the race's terrain; Uphill Athlete: aerobic base). It stays the `long` session (same day,
    same minutes), so the long-run rules (day, 48 h from the intervals, B2B, the projection's
    long-run base) are unchanged. Not in a recovery / re-entry week, not when the long run is
    done, a B2B week, or the 90-min AeT test week.
  * 專項期 — one 技術地形 session a week close to the race's terrain, out of a placed easy run.
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

from backend.i18n import _

BASE_RPE = (3, 4)            # 技術地形 60′ (低 RPE) — the template's
SPEC_RPE = (6, 7)            # 技術地形 90′ — RPE 7 = a quality session (workout_steps.RPE_HARD_MIN)
SPEC_EASY_RPE = (4, 5)       # 專項期 without room / a spaced day: kept easy (推估)
CLIMB_PER_MIN = {"base": 5.0, "specific": 600 / 90}   # m per work minute: the templates' 300 m / 60′, 600 m / 90′
SPEC_WORK_MAX = 90           # the template's main block
SPEC_WORK_MIN = 30           # 推估: a shorter RPE 6–7 block is not worth a hard day
WARM = {"base": 10, "specific": 15}
COOL = {"base": 5, "specific": 10}
EASY_MIN = 20                # the other easy runs give the difference, never below this
HARD_IDS = ("long", "long2", "long3", "climb",   # B2B days and the race-climb repeats (steep_hill: its hard list)
            "downhill")                         # SP-99: ≥ 2 days from the downhill session (eccentric load)
SRC = ("Koop（賽道專項：練和比賽相同的地形）；Uphill Athlete；隔週一次、RPE 對強度預算的換算是這個 app 的"
       "建議（推估）")


def _d(x) -> Optional[dt.date]:
    if not x:
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def base_week(monday: dt.date) -> bool:
    """基礎期: the weeks whose LSD becomes 技術地形 — every other week, by the ISO week number."""
    return monday.isocalendar()[1] % 2 == 0


def week_context(*, kind: str, mode: str, monday: dt.date, road: bool, b2b: Optional[dict] = None) -> dict:
    """Whether this week gets a 技術地形 session and which rule: {"active", "phase", "why"}."""
    info = {"active": False, "phase": kind, "monday": monday.isoformat()}
    if road:
        return {**info, "why": _("主要訓練項目：路跑")}
    if kind not in ("base", "specific") or mode in ("recovery_week", "reentry"):
        return {**info, "why": _("只在基礎期、專項期（恢復週、停訓後恢復期不排）")}
    b = b2b or {}
    if kind == "base" and (b.get("due") or b.get("candidate") or b.get("post")):
        return {**info, "why": _("B2B 週：長跑照 B2B 排")}
    if kind == "base" and not base_week(monday):
        return {**info, "why": _("基礎期隔週一次：這週照常 LSD")}
    return {**info, "active": True}


def _steps(phase: str, work: int, rpe: tuple) -> dict:
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    b = WT.B()
    up = int(round(work * CLIMB_PER_MIN[phase] / 50.0) * 50) or None
    items = [b.warm(WARM[phase], "好走的路段暖身"),
             b.t("work", work * 60, WT.rpe(rpe[0], rpe[1], up=up),
                 "技術路段，跑走混合，練腳步" if phase == "base" else "接近比賽路況的技術路段"),
             b.t("cool", COOL[phase] * 60, WT.OPEN, "收操")]
    return WS.doc(items, origin="template:lib:" + ("tech_easy" if phase == "base" else "tech_hard"))


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
    whose workout_templates.session_role is "quality"): [{"uid", "day", "title", "work"}]."""
    from backend.engine import workout_templates as WT
    a, b = monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()
    out = []
    for r in rows or ():
        day = str(r.get("day") or "")[:10]
        if not (a <= day <= b) or r.get("kind") in ("quality", "test") or r.get("state") not in ("active", "done"):
            continue
        doc = _steps_doc(r)
        if doc is None or WT.session_role({"steps": doc}) != "quality":
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
                     and x.get("id") not in ("climb", "steep", "downhill") and not x.get("heat")),
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
    in the Zone 3 bucket (≤ 10 %, Daniels' T volume): on technical trail the footing, not the
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
    """Turn the week's LSD (基礎期) or one easy run (專項期) into the 技術地形 session, in place;
    sets info["planned"]. `hard_done`: hard days already done this week (dates). `user`: the
    user's own RPE ≥ 7 sessions this week (user_quality) — the 專項期 one already, none added.
    `walk` (hr_profile.walk_cap, SP-115; owner 2026-10-05): the 75 % HRmax figure goes into the
    detail as guidance only — the session keeps no HR target (target_policy: kind hike, HR ≤ AeT on
    the watch), its steps and push are unchanged."""
    if not info or not info.get("active"):
        return ss
    info["planned"] = []
    if any(s.get("id") == "tech" for s in ss):
        return ss                                       # one a week
    if user and info["phase"] == "specific":
        info["user"] = list(user)
        return ss                                       # the user's own is this week's (user_note says so)
    rate = _rate(rates)
    if info["phase"] == "base":
        s = next((x for x in ss if x.get("id") == "long" and x.get("kind") == "long"), None)
        if s is None or s.get("done") or not s.get("day") or s.get("heat"):
            return ss
        m = int(s.get("minutes") or 0)
        work = m - WARM["base"] - COOL["base"]
        if work < 30:
            return ss
        steps = _steps("base", work, BASE_RPE)
        climb = steps["items"][1]["target"].get("up")
        s.update(title=f"技術地形 {m}′（低 {_rpe_txt(BASE_RPE)}）", terrain="trail", target="", steps=steps,
                 detail=(f"技術路段跑走混合，{_rpe_txt(BASE_RPE)}（輕鬆、可以講話）"
                         + (f"，爬升約 {climb} m" if climb else "") + "；心率、功率不當目標（腳步才是瓶頸）"
                         + _hint(walk)),
                 source=SRC, climb_m=float(climb) if climb else None, tss=round(rate * m / 60.0, 1))
        info["planned"].append({"day": s["day"], "minutes": m, "rpe": list(BASE_RPE), "role": "easy",
                                "replaces": "long"})
        if notes is not None:
            notes.append({"level": "info", "src": "technical",
                          "text": _("本週 LSD 換成技術地形 {m}′（{rpe}，算輕鬆課）：基礎期隔週一次，"
                                    "練腳步和路況判斷、不練心肺；時間和 LSD 一樣", m=m, rpe=_rpe_txt(BASE_RPE))})
        return ss
    # 專項期: one session out of a placed easy run
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race")
                                                           or s.get("id") in HARD_IDS)]
    hard += [_d(d) for d in hard_done or ()]
    easy = sorted((s for s in ss if s.get("kind") == "easy" and not s.get("done") and s.get("day")
                   and s.get("id") not in ("climb", "steep", "downhill") and not s.get("heat")),
                  key=lambda s: (-(int(s.get("minutes") or 0)), s["day"]))
    if not easy:
        if notes is not None:
            notes.append({"level": "info", "src": "technical",
                          "text": _("專項期每週 1 堂技術地形課：這週沒有可以換的輕鬆跑")})
        return ss
    cap = getattr(prefs, "cap_weekday", None) if prefs is not None and getattr(prefs, "active", False) else None
    long_cap = getattr(prefs, "long_cap", None) if prefs is not None and getattr(prefs, "active", False) else None
    spaced = [s for s in easy if all(abs((_d(s["day"]) - h).days) >= 2 for h in hard)]
    room, room_txt = budget_room(ss, hours)
    warm_cool = WARM["specific"] + COOL["specific"]

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
            _("沒有離長跑和其他強度課 ≥ 2 天的日子") if not spaced else _("這天的時間上限放不下")
        why = _("專項期每週 1 堂技術地形 {m}′，但{reason}：改成 {rpe}，算輕鬆課（不佔強度預算、不用隔 48 小時）",
                m=m, reason=reason, rpe=_rpe_txt(rpe))
    delta = m - int(pick.get("minutes") or 0)
    steps = _steps("specific", work, rpe)
    climb = steps["items"][1]["target"].get("up")
    pick.update(id="tech", kind="hike", terrain="trail", minutes=m, target="", steps=steps,
                title=f"技術地形 {m}′（{_rpe_txt(rpe)}）",
                detail=(f"找接近比賽路況的技術路段，{_rpe_txt(rpe)}" + (f"，爬升約 {climb} m" if climb else "")
                        + "；心率、功率不當目標" + _hint(walk) + ("；算強度課，前後一天輕鬆" if role == "quality" else "")),
                source=SRC, climb_m=float(climb) if climb else None, tss=round(rate * m / 60.0, 1))
    if delta > 0:
        _rebalance(ss, pick, delta)
    info["planned"].append({"day": pick["day"], "minutes": m, "rpe": list(rpe), "role": role, "work": work})
    if notes is not None:
        notes.append({"level": "info", "src": "technical", "text": why})
    return ss


PUBLIC = ("active", "phase", "why", "planned", "user")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}
