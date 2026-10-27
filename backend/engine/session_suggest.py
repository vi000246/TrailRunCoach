"""
新增課表 (SP-628): the session a new one is filled with when you pick its type in the 課表 dialog —
the one the automatic plan would give you, not a fixed title read back into steps.

  * 強度課 › 有氧間歇: the Zone 3 ladder's next step, the way the generator picks it
    (quality_gate.rung_now → interval_library.fit for the day's cap → session_for). Under
    個人化區間 session_for re-cuts the reps by the power–duration model (SP-590); the 課表功率區間
    and the 心率 rules come with it. The session keeps its variant_key, so the structure, the push and
    the judging take the same path as the generator's.
  * 強度課 › VO2max 間歇: the Zone 5 ladder's step when the track is open; still closed
    (quality_gate.z5_track) → its first step V1 (5×2′, re-cut the same way), marked
    「還沒解鎖：手動加的，不算進階」: equiv False and swap LOCKED, so it never moves the Zone 5 ladder
    and never counts as the track being under way (quality_gate.row_track). It is still a 強度課:
    the week's intensity budget and the 48-h spacing count it like any other.
  * 強度課 › 速度: there is no speed ladder; the best full-session 速度 template for the day
    (template_recs' own rules: phase, the day's cap, terrain), applied as a structure of your own.
    Before the Zone 5 track opens it is marked like VO2max (SP-628 R2, Q2-1: locked_info), saved with
    swap LOCKED.
  * Only what the dialog fills in by itself is marked (SP-628 R2, Q2-3): a Zone 5 template inserted by
    hand (插入範本) is saved and counted as before — 「不需要擋，擋的是自動產生的邏輯」.
  * 輕鬆跑 / LSD: the minutes, title, target and detail of that kind in the generated week of the
    day (the projected one past this week), else this week's.

Nothing here changes the generator or the gates: they are read, not written.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.engine import interval_library as IL
from backend.engine import target_policy as TP
from backend.i18n import N_, _

LOCKED = "locked"                      # plan_sessions.swap of a Zone 5 session added before the track opened
LOCKED_NOTE = N_("還沒解鎖：手動加的，不算進階")
SPEED_DEFAULT = "lib:daniels_r"


def z5_state(gate: Optional[dict]) -> dict:
    """quality_gate.z5_track of the gate; no gate (no state) = closed, with no reason."""
    from backend.engine import quality_gate as QG
    if not gate or not gate.get("state"):
        return {"open": False, "reason": "", "text": ""}
    return QG.z5_track(gate)


def locked_z5(gate: Optional[dict]) -> bool:
    """A Zone 5 session added now would be one before the track opened. Only with a gate that has
    a state (an old / test plan without one keeps the old behaviour)."""
    return bool(gate and gate.get("state")) and not z5_state(gate)["open"]


def locked_info(gate: Optional[dict]) -> Optional[dict]:
    """{"text", "why"} of the 「還沒解鎖」 mark of a Zone 5 session (VO2max, 速度) the dialog fills in
    while the Zone 5 track is closed; None when it is open (or the plan has no gate state)."""
    if not locked_z5(gate):
        return None
    return {"text": _(LOCKED_NOTE), "why": z5_state(gate).get("reason") or ""}


def _dose(gate: dict, track: str) -> dict:
    d = (gate or {}).get("dose") or {}
    return d.get(track) if isinstance(d.get(track), dict) else {}


def ladder(family: str, gate: Optional[dict], th: Optional[dict], *, cap: Optional[float] = None,
           history=(), prefs=None, mountain: bool = False) -> dict:
    """{"session": interval_library.session_for dict, "locked": None | {"text", "why"}, "rung"} for
    有氧間歇 (Zone 3 track) or VO2max 間歇 (Zone 5 track)."""
    from backend.engine import quality_gate as QG
    gate = gate or {}
    th = th or {}
    track = "z5" if family == "vo2max" else "z3"
    first = IL.Z5_TRACK[0] if track == "z5" else IL.Z3_TRACK[0]
    locked = track == "z5" and not z5_state(gate)["open"]
    rung = first if locked else (QG.rung_now(gate, track) or first)
    adj = None if locked else (_dose(gate, track).get("adjust") or None)
    f = IL.fit(rung, cap, history or (), prefs, mountain, None, adj)
    if locked and f.get("rung") != rung:
        # the day's cap can't take V1: fit falls back to a Zone 3 step — the user asked for VO2max,
        # so V1 as it is (the cap is a preference; the dialog shows the minutes)
        f = IL.fit(rung, None, history or (), prefs, mountain)
    pre = ""
    if gate.get("state") and not locked:
        pre = QG.prefix(gate)
        dose = _dose(gate, track)
        if dose.get("faded"):
            pre = (dose.get("note") or "") + pre
    tth = TP.text_th(th)               # SP-951: + tpace / power_state (target_policy.flat_pace)
    lthr_default = bool((gate.get("lthr") or {}).get("default"))
    if locked:
        f = {**f, "equiv": False, "progress": False, "rung": rung, "reason": _(LOCKED_NOTE)}
    s = IL.session_for(f, tth, pre, lthr_default, prefs, swap=LOCKED if locked else "user")
    lk = {"text": _(LOCKED_NOTE), "why": z5_state(gate).get("reason") or ""} if locked else None
    return {"session": s, "locked": lk, "rung": f.get("rung") or rung,
            "reason": f.get("reason") or ""}


def week_of(inp: dict, day: Optional[str]) -> dict:
    """The generated week `day` falls in: {"monday" date, "kind" (phase), "mode", "hours", "sessions",
    "first" (this week: today's guardrails apply), "maintain"} — this week's when `day` is in no
    projected week (or is None)."""
    cur = (inp or {}).get("cur") or {}
    this = {"monday": _date(((cur.get("week") or {}).get("start"))), "kind": cur.get("phase") or "base",
            "mode": cur.get("mode") or cur.get("phase") or "base",
            "hours": (cur.get("target") or {}).get("hours"), "sessions": cur.get("sessions") or [], "first": True,
            "maintain": cur.get("maintain")}
    d = _date(day)
    if d is None or this["monday"] is None or this["monday"] <= d < this["monday"] + dt.timedelta(days=7):
        return this
    for w in (inp or {}).get("weeks") or []:
        ws = _date(w.get("start"))
        if ws is not None and ws <= d < ws + dt.timedelta(days=7):
            return {"monday": ws, "kind": w.get("phase") or this["kind"], "mode": w.get("mode") or w.get("phase"),
                    "hours": w.get("hours"), "sessions": w.get("sessions") or [], "first": False,
                    "maintain": w.get("maintain")}
    return this


def _date(s) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def z3_item(gate: dict, wk: dict, prefs=None) -> dict:
    """The Zone 3 item of the week's week_decision (quality_gate, the generator's own call: the week's phase,
    mode, Monday, sessions a week; this week with today's guardrails — a CTL ramp at the watch line gives
    the threshold-only SUB —, a projected week at the step its generated Zone 3 session reached). A week
    without one (a Zone 5 week, a recovery week, a guardrail, Zone 3 not unlocked yet): the track's rung as
    it stands, as SP-628 gave it — you asked for an 有氧間歇 that day."""
    from backend.engine import overview as O
    from backend.engine import quality_gate as QG
    kind, mode = wk["kind"], wk["mode"] or wk["kind"]
    if kind in ("base", "specific"):
        step = None
        if not wk["first"]:
            rk = next((s.get("rung_key") for s in wk["sessions"] or ()
                       if s.get("kind") == "quality" and s.get("rung_key") in IL.Z3_TRACK), None)
            if rk:
                step = {"z3": IL.Z3_TRACK.index(rk)}
        dec = QG.week_decision(gate, kind, mode, wk["monday"], step, first=wk["first"],
                               n=O.quality_per_week(prefs, kind, mode, gate),
                               prefer="z5" if wk.get("maintain") else None)
        it = next((x for x in dec.get("items") or () if x.get("track") == "z3" and not x.get("cruise")), None)
        if it is not None:
            return it
    d3 = QG._track_doses(gate)[0] or {}
    s3 = int(d3.get("step") or 0)
    adj = (d3.get("adjust") or None) if wk["first"] else None
    spec = QG.track_spec("z3", s3)
    return {"track": "z3", "spec": QG.adjusted_spec(spec, adj) if adj else spec, "advance": True, "adjust": adj,
            "first": s3 == 0 and int(d3.get("met") or 0) == 0}


def aerobic(inp: dict, day: Optional[str], gate: Optional[dict], th: Optional[dict], *, cap: Optional[float] = None,
            prefs=None) -> Optional[dict]:
    """有氧間歇 as the generator would schedule it on `day` (SP-733): the week's Zone 3 item (z3_item) through
    overview.ladder_session — the same function quality_sessions calls, so the three re-picks come with it
    (the week's Zone 3 cap → 巡航版, the day's cap → 巡航版, a CTL ramp week → threshold only) and the 越野
    專項期's uphill version — then the week's ≤ 20 % (overview._shorten), Zone 5 first as the generator
    counts it. `cap`: the day's (課表偏好). Same shape as ladder(); None without a gate state (an old / test
    plan: ladder() then) or when the rung isn't a library one."""
    from backend.engine import overview as O
    from backend.engine import quality_gate as QG
    gate = gate or {}
    if not gate.get("state"):
        return None
    wk = week_of(inp, day)
    it = z3_item(gate, wk, prefs)
    kind = wk["kind"] if wk["kind"] in ("base", "specific") else "base"
    cur = (inp or {}).get("cur") or {}
    road = (cur.get("primary_sport") or "trail") == "road"
    mountain = mountain_of(inp) if kind == "base" else not road
    mw = wk.get("maintain")
    hill = (mw.get("focus") == "hill") if isinstance(mw, dict) else None
    hours = wk["hours"]
    total = QG.QUALITY_SHARE_MAX * hours * 60.0 if hours else None
    used = sum(O.session_tiz_min(s) for s in wk["sessions"] or ()
               if s.get("kind") == "quality" and _is_z5(s))
    left = None if total is None else max(0.0, total - used)
    th = th or {}
    tth = TP.text_th(th)               # SP-951: + tpace / power_state (target_policy.flat_pace)
    s = O.ladder_session(gate, it, kind, tth, hours, prefs, O.variant_history(wk["monday"] or _date(day)
                                                                                or dt.date.today(), gate),
                         mountain, road, cap, None, None, left=left, hill=hill)
    if not s.get("variant_key"):
        return None
    if left is not None and O.session_tiz_min(s) > left + 1e-6:
        s = O._shorten(s, left, total, hours, tth, gate, prefs, None)
    # added by you, from the dialog: the user's (the generator's own swap "auto" / "cap" is its own choice)
    s = {**s, "swap": "user"}
    return {"session": s, "locked": None, "rung": s.get("rung_key"), "reason": s.get("swap_reason") or ""}


def _is_z5(s: dict) -> bool:
    if IL.track_of(s.get("rung_key")) == "z5":
        return True
    v = IL.get(s.get("variant_key"))
    return v is not None and IL.is_z5(v)


def speed(tpl: dict, *, cap: Optional[float] = None, terrain: str = "road", phase: Optional[str] = None,
          sport: str = "trail") -> Optional[dict]:
    """The best full 速度 session among the templates (workout_steps.templates() `tpl`), by
    template_recs' rules with Zone 5 treated as open (the user picked 速度 by hand); uphill
    sprints on a trail day, flat R reps on the road. {"row", "reason"} or None."""
    from backend.engine import template_recs as TR
    s = {"kind": "quality", "cap": cap, "minutes": None, "terrain": terrain or "road",
         "phase": TR.PHASE_OF.get(phase or "", "base"), "z5_open": True, "rung": None,
         "ladder_key": None, "ladder_reason": "", "sport": sport}
    best = None
    i = 0
    for g in tpl.get("groups") or []:
        if g.get("cat") != "quality":
            continue
        for r in g.get("rows") or []:
            fam = (r.get("family") or {}).get("id") or g.get("sub")
            if fam != "speed" or not r.get("full"):
                continue
            sc = TR._score(r, "quality", "speed", s)
            hill = "坡" in str(r.get("title") or r.get("label") or "")
            pts = sc.v + (15.0 if hill == (s["terrain"] == "trail") else -15.0)
            if s["phase"] == "taper" and r.get("key") in TR.TAPER_NO_CLIMB:
                pts -= 80.0                     # 減量期: no short hard climbing (template_recs, SP-96)
            if best is None or pts > best[0]:
                best = (pts, i, r, sc)
            i += 1
    if best is None:
        return None
    r = best[2]
    return {"row": r, "reason": best[3].reason(_("速度課：{m:.0f} 分", m=TR.row_minutes(r)))}


def _week_sessions(inp: dict, day: Optional[str]) -> list:
    """The generated sessions of the week `day` falls in (the projected weeks, else this week)."""
    cur = (inp or {}).get("cur") or {}
    if day:
        try:
            d = dt.date.fromisoformat(day)
        except ValueError:
            d = None
        if d is not None:
            for w in (inp or {}).get("weeks") or []:
                try:
                    ws = dt.date.fromisoformat(w["start"])
                except (KeyError, TypeError, ValueError):
                    continue
                if ws <= d < ws + dt.timedelta(days=7) and w.get("sessions"):
                    return w["sessions"]
    return cur.get("sessions") or []


def easy(kind: str, inp: dict, day: Optional[str]) -> Optional[dict]:
    """輕鬆跑 / LSD as the plan has it that week: {"title", "minutes", "target", "detail", "terrain"}
    of its first session of `kind` (this week's when that week has none), or None."""
    for ss in (_week_sessions(inp, day), ((inp or {}).get("cur") or {}).get("sessions") or []):
        s = next((x for x in ss if x.get("kind") == kind and int(x.get("minutes") or 0) > 0), None)
        if s is not None:
            return {"title": s.get("title") or "", "minutes": int(s.get("minutes") or 0),
                    "target": s.get("target") or "", "detail": s.get("detail") or "",
                    "terrain": s.get("terrain")}
    return None


def mountain_of(inp: dict) -> bool:
    """The generator's `mountain` (a trail goal: 「LSD（山路）」 this week; never for 路跑)."""
    cur = (inp or {}).get("cur") or {}
    if (cur.get("primary_sport") or "trail") == "road":
        return False
    return any(s.get("kind") == "long" and "山路" in (s.get("title") or "") for s in cur.get("sessions") or [])
