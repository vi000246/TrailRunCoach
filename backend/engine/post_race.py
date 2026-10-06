"""
賽後的日子 (SP-98; SP-111 下坡升級): the day rules after a race, shared by overview.week_plan and
projection.project_weeks and applied after the placement (like the 減量期 rules, overview.taper_rules).

A race (its `event` phase, auto or manual):
  * the first planning.REC_NO_RUN_DAYS days no running and no strength: a session there moves to a
    free later day of the week, else it goes (a controlled trial: a 40-min easy run from 48 h on did
    no harm [256]; Higdon: 3 days off [446] — 教練級);
  * up to day planning.REC_SHORT_DAYS every run an easy run ≤ planning.REC_SHORT_MIN minutes;
  * a big downhill for the athlete (planning.downhill: the race ≥ DOWNHILL_BIG × the biggest of the
    last 6 weeks) — the first DOWNHILL_FLAT_DAYS days on the flat, nothing hard (Bontemps 2020:
    strength, CK and soreness take 3–5 days after one downhill run).
The phases carry the volume (planning.recovery_plan / the 回量期); this module only moves, caps and
relabels the sessions on those days. Notes name the race.

B and C races (SP-95; periodization-cross-sport.md §4.8, §4.8.1, §6.1「SP-95」; 教練級, no trial):
  * a B race's week: B_WEEK_SHARE of the volume (b_week_factor, base / 專項期 weeks; 推估), no long
    run; no interval in the B_INTERVAL_DAYS days before it, no tempo or long run in the
    B_TEMPO_LONG_DAYS before it (Pfitzinger, via [437]; Friel rests 2–3 days [431]); the race is the
    week's key session (a `race` session on its day — the other sessions that day go);
  * after it planning.recovery_plan(b=True): 短 3 days, 中 5 days of easy runs only; 馬拉松級 and
    up an A race's recovery days and day rules (no 回量期);
  * a C race replaces one quality session or the long run of its week (中 and longer: the long run
    first; 短: the quality session first), the rest unchanged (TrainerRoad [362]; elites race in
    training [416]);
  * hints (b_hints): more than B_PER_MONTH B race within 30 days (CTS [433]) or the CTL down more
    than B_CTL_DROP since the peak of the weeks holding a B race (Friel's case: −13 % [431]) —
    「B 賽太多，等於一直在減量」; a B race of 中 or longer within B_NEAR_A_DAYS before an A race
    ([438][433]: 2–4 weeks out only a shorter warm-up race on similar terrain); SP-280: a B race of
    any size inside the next A race's 減量期 (planning.taper_start: the planned taper phase, else
    planning.taper_days), or longer than that A race (b_longer: days → predicted time → EP → km,
    SP-111's order) — Runna's rule (no B race in the 7–10 days before the A race, the B race shorter
    than the A race [482]; 廠商規則, no research: 推估).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _

RUN_KINDS = ("easy", "long", "quality", "test", "hike")
_CLEAR = ("variant_key", "rung_key", "equiv", "swap", "swap_reason", "variant_reps", "variant_blocks",
          "variant_adj", "progress", "steps", "distance_km", "climb_m", "protocol")


def _d(s) -> dt.date:
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def _get(p, k):
    return p.get(k) if isinstance(p, dict) else getattr(p, k, None)


def a_windows(phases, events, monday: dt.date) -> list[dict]:
    """The A races whose first REC_SHORT_DAYS after their last day touch the week of `monday`:
    [{"race", "id", "end", "no_run", "short", "flat"}] (dates; `flat` None without a big downhill).
    `phases`: Phase objects or dicts; `events`: planning.Event (for the downhill verdict)."""
    from backend.engine import planning as P
    sunday = monday + dt.timedelta(days=6)
    by_id = {e.id: e for e in events or ()}
    out = []
    for p in phases or ():
        if _get(p, "kind") != "event":
            continue
        end, start = _d(_get(p, "end")), _d(_get(p, "start"))
        if not (monday - dt.timedelta(days=P.REC_SHORT_DAYS) <= end < sunday):
            continue
        ev = by_id.get(_get(p, "event_id")) or next((e for e in events or () if e.start == start), None)
        dh = None
        if ev is not None:
            try:
                dh = P.downhill(ev)
            except Exception:                   # noqa: BLE001 — the plan must still build
                dh = None
        out.append({"race": ev.name if ev is not None else _("A 賽事"), "id": _get(p, "event_id"), "end": end,
                    "no_run": end + dt.timedelta(days=P.REC_NO_RUN_DAYS),
                    "short": end + dt.timedelta(days=P.REC_SHORT_DAYS),
                    "flat": end + dt.timedelta(days=P.DOWNHILL_FLAT_DAYS) if dh and dh.get("big") else None})
    return out


def apply(ss: list, wins: list, monday: dt.date, notes: Optional[list] = None, today: Optional[dt.date] = None,
          blocked=()) -> list:
    """`ss` (Session objects or dicts, placed) with the rules of `wins` (a_windows) applied to the
    not-done sessions: moved off / dropped from the no-run days, capped easy runs in the first week,
    the flat days after a big downhill. Returns the kept sessions; notes say what changed."""
    if not wins or not ss:
        return ss
    from backend.engine import planning as P
    is_d = isinstance(ss[0], dict)

    def put(s, **kw):
        for k, v in kw.items():
            if is_d:
                s[k] = v
            else:
                setattr(s, k, v)

    days = [monday + dt.timedelta(days=i) for i in range(7)]
    lo = today or monday
    blocked = {str(b) for b in blocked or ()}
    keep = list(ss)
    for w in wins:
        touched, gone = False, 0
        # 1. the no-run days: move to a free later day, else drop
        for s in sorted(keep, key=lambda x: _get(x, "day") or "9"):
            if _get(s, "done") or not _get(s, "day"):
                continue
            d = _d(_get(s, "day"))
            if not (w["end"] < d <= w["no_run"]) or _get(s, "kind") not in RUN_KINDS + ("strength",):
                continue
            touched = True
            run = _get(s, "kind") in RUN_KINDS
            used = {_get(x, "day") for x in keep if x is not s and not _get(x, "done")
                    and (_get(x, "kind") in RUN_KINDS) == run}
            free = next((c for c in days if c > w["no_run"] and c >= lo and c.isoformat() not in blocked
                         and c.isoformat() not in used), None)
            if free is not None:
                put(s, day=free.isoformat())
            else:
                keep.remove(s)
                gone += 1
        # 2. the rest of the first week: easy runs ≤ REC_SHORT_MIN; the flat days after a big downhill
        n_easy = sum(1 for x in keep if _get(x, "kind") == "easy")
        for s in keep:
            if _get(s, "done") or not _get(s, "day") or _get(s, "kind") not in RUN_KINDS:
                continue
            d = _d(_get(s, "day"))
            short = w["no_run"] < d <= w["short"]
            flat = w["flat"] is not None and w["end"] < d <= w["flat"]
            if not (short or flat):
                continue
            touched = True
            m = int(_get(s, "minutes") or 0)
            nm = min(m, P.REC_SHORT_MIN) if short else m
            detail = _("賽後第 1 週：輕鬆跑 ≤ {max} 分", max=P.REC_SHORT_MIN) if short else _("輕鬆跑")
            if flat:
                detail += _("，平路、不跑下坡")
            if _get(s, "kind") != "easy":
                n_easy += 1
                put(s, id=f"easy{n_easy}", kind="easy", title=_("輕鬆跑"), **{k: None for k in _CLEAR})
            put(s, minutes=nm, tss=round(float(_get(s, "tss") or 0.0) * (nm / m if m else 1.0), 1), detail=detail)
            if flat:
                put(s, terrain="road", climb_m=None)
        if touched and notes is not None:
            date = f"{w['end'].month}/{w['end'].day}"
            if w["short"] > w["end"]:
                txt = _("{p} 賽事「{race}」{date} 後：前 {n} 天不排跑步和肌力，第 1 週每次 ≤ {max} 分輕鬆跑",
                        p="B" if w.get("b") else "A", race=w["race"], date=date, n=P.REC_NO_RUN_DAYS,
                        max=P.REC_SHORT_MIN)
            else:
                txt = _("{p} 賽事「{race}」{date} 後", p="B" if w.get("b") else "A", race=w["race"], date=date)
            if w["flat"] is not None:
                txt += _("；下坡比你近 6 週練過的多，賽後 {h} 小時內走平路、不排硬課", h=P.DOWNHILL_FLAT_DAYS * 24)
            if gone:
                txt += _("（{n} 堂排不開，拿掉了，不用補）", n=gone)
            notes.append({"level": "info", "src": "recovery", "text": txt})
    return keep


# ---------------------------------------------------------------------------
# B / C races (SP-95)
# ---------------------------------------------------------------------------

B_WEEK_SHARE = 0.75
B_INTERVAL_DAYS = 5          # planning.MINI_TAPER_DAYS
B_TEMPO_LONG_DAYS = 4
B_PER_MONTH = 1
B_CTL_DROP = 0.10
B_CTL_WEEKS = 8              # 推估: the weeks a CTL drop is measured over
B_NEAR_A_DAYS = 28
B_EASY_MAX = 60              # a long run / quality turned easy around a B race: ≤ 60 min (推估)
RACE_TSS_PER_H = 70.0        # 推估: a race hour is harder than an easy one


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def _tempo(s) -> bool:
    """A Zone 3 / tempo quality session (not an interval)."""
    from backend.engine import quality_gate as QG
    title = str(_get(s, "title") or "")
    return (QG.is_z3_variant(_get(s, "variant_key")) or str(_get(s, "rung_key") or "").startswith("z3")
            or any(w in title for w in ("節奏", "巡航", "閾值", "Zone 3", "3 區")))


def b_week(events, monday: dt.date) -> list:
    """The B races starting in the week of `monday`."""
    sunday = monday + dt.timedelta(days=6)
    return sorted((e for e in events or () if getattr(e, "priority", None) == "B" and monday <= e.start <= sunday),
                  key=lambda e: e.start)


def b_week_factor(events, monday: dt.date, kind: str) -> tuple[float, Optional[str]]:
    """(factor, why) of a base / 專項期 week holding a B race: B_WEEK_SHARE; (1, None) otherwise."""
    bs = b_week(events, monday)
    if not bs or kind not in ("base", "specific"):
        return 1.0, None
    e = bs[0]
    return B_WEEK_SHARE, _("B 賽事「{race}」{date}：這週量 {share:.0%}（推估），不排長跑，比賽算這週的重點課",
                           race=e.name, date=_md(e.start), share=B_WEEK_SHARE)


def b_windows(events, monday: dt.date) -> list[dict]:
    """The B races whose recovery (planning.recovery_plan(b=True)) touches the week of `monday`, as
    a_windows rows + "easy" (the last easy-only day) and "b": True; the A-like day rules (no run,
    ≤ 40 min, flat) only for 馬拉松級 and up."""
    from backend.engine import planning as P
    sunday = monday + dt.timedelta(days=6)
    out = []
    for e in events or ():
        if getattr(e, "priority", None) != "B" or e.end >= sunday:
            continue
        rp = P.recovery_plan(e, b=True)
        easy = e.end + dt.timedelta(days=rp["days"])
        if easy < monday:
            continue
        a_like = rp["rec_size"] >= P.MARATHON
        dh = rp.get("downhill") or {}
        out.append({"race": e.name, "id": e.id, "end": e.end, "b": True, "easy": easy, "days": rp["days"],
                    "no_run": e.end + dt.timedelta(days=P.REC_NO_RUN_DAYS) if a_like else e.end,
                    "short": e.end + dt.timedelta(days=P.REC_SHORT_DAYS) if a_like else e.end,
                    "flat": e.end + dt.timedelta(days=P.DOWNHILL_FLAT_DAYS) if dh.get("big") else None})
    return out


def _as_easy(ss, s, cap: float, why: str, put) -> None:
    m = int(_get(s, "minutes") or 0)
    nm = int(min(m, cap) // 5 * 5) or m
    if _get(s, "kind") != "easy":
        n_easy = sum(1 for x in ss if _get(x, "kind") == "easy")
        put(s, id=f"easy{n_easy + 1}", kind="easy", title=_("輕鬆跑"), **{k: None for k in _CLEAR})
    put(s, minutes=nm, tss=round(float(_get(s, "tss") or 0.0) * (nm / m if m else 1.0) * 0.8, 1), detail=why)


def _race_session(e, is_d: bool, rate: float, done: bool, factory=None):
    from backend.engine import planning as P
    h = P.event_hours(e)
    m = int(round(h * 60)) if h else 0
    d = {"id": "race", "kind": "race", "title": _("{p} 賽事：{name}", p=e.priority, name=e.name), "minutes": m,
         "target": "", "detail": _("比賽本身算這週的重點課") if e.priority == "B"
         else _("C 賽事當訓練跑，取代這週一堂強度課或長跑"),
         "source": _("TrainerRoad；Pfitzinger；CTS（教練級）"), "tss": round(m / 60.0 * rate, 1),
         "day": e.start.isoformat(), "done": done, "done_by": None, "terrain": "road" if e.kind == "road" else None}
    return d if is_d or factory is None else factory(**d)


def bc_apply(ss: list, events, monday: dt.date, notes: Optional[list] = None, today: Optional[dt.date] = None,
             blocked=(), rate: float = RACE_TSS_PER_H, done_days=(), factory=None) -> list:
    """`ss` (Session objects or dicts, placed) with the B / C race rules of the week of `monday`
    (b_week / the mini-taper days / b_windows / the C race swap). `factory` makes a Session from a
    dict (week_plan); `done_days`: ISO days with an activity (the race then counts as done)."""
    from backend.engine import planning as P
    if not events:
        return ss
    sunday = monday + dt.timedelta(days=6)
    is_d = bool(ss) and isinstance(ss[0], dict) if ss else factory is None

    def put(s, **kw):
        for k, v in kw.items():
            if isinstance(s, dict):
                s[k] = v
            else:
                setattr(s, k, v)

    keep = list(ss)
    live = [s for s in keep if not _get(s, "done") and _get(s, "day")]
    # 1. B races: the mini-taper days (this week or reaching into it) and the race week
    for e in sorted((e for e in events if getattr(e, "priority", None) == "B"
                     and monday <= e.start + dt.timedelta(days=0) <= sunday + dt.timedelta(days=B_INTERVAL_DAYS)),
                    key=lambda e: e.start):
        said = []
        for s in live:
            if s not in keep:
                continue
            d = _d(_get(s, "day"))
            out = (e.start - d).days
            k = _get(s, "kind")
            if monday <= e.start <= sunday and (k == "long" or _get(s, "id") in ("long", "long2")) and out != 0:
                _as_easy(keep, s, B_EASY_MAX, _("B 賽事「{race}」那週不排長跑：輕鬆跑", race=e.name), put)
                said.append(_("不排長跑"))
            elif 1 <= out <= B_TEMPO_LONG_DAYS and (k in ("quality", "test", "long")):
                _as_easy(keep, s, B_EASY_MAX, _("B 賽事「{race}」前 {n} 天內不排節奏跑、長跑：輕鬆跑",
                                                race=e.name, n=B_TEMPO_LONG_DAYS), put)
                said.append(_("賽前 {n} 天內不排節奏跑、長跑", n=B_TEMPO_LONG_DAYS))
            elif out == B_INTERVAL_DAYS and k in ("quality", "test") and not _tempo(s):
                _as_easy(keep, s, B_EASY_MAX, _("B 賽事「{race}」前 {n} 天內不排間歇：輕鬆跑",
                                                race=e.name, n=B_INTERVAL_DAYS), put)
                said.append(_("賽前 {n} 天內不排間歇", n=B_INTERVAL_DAYS))
        if monday <= e.start <= sunday:
            race_days = {(e.start + dt.timedelta(days=i)).isoformat() for i in range((e.end - e.start).days + 1)}
            gone = [s for s in keep if not _get(s, "done") and _get(s, "day") in race_days and _get(s, "kind") != "race"]
            keep = [s for s in keep if s not in gone and not (_get(s, "kind") == "race" and _get(s, "day") in race_days)]
            keep.append(_race_session(e, is_d, rate, e.start.isoformat() in set(done_days), factory))
        if notes is not None and (said or monday <= e.start <= sunday):
            notes.append({"level": "info", "src": "race",
                          "text": _("B 賽事「{race}」{date}：賽前 {a} 天不排間歇、{b} 天不排節奏跑和長跑，比賽是這週的重點課"
                                    "（Pfitzinger、Friel，教練級）", race=e.name, date=_md(e.start), a=B_INTERVAL_DAYS,
                                    b=B_TEMPO_LONG_DAYS)
                          + ("（" + "、".join(dict.fromkeys(said)) + "）" if said else "")})
    # 2. after a B race: easy runs only; 馬拉松級 and up also the A race's first-week rules
    for w in b_windows(events, monday):
        said = False
        for s in [x for x in keep if not _get(x, "done") and _get(x, "day")]:
            d = _d(_get(s, "day"))
            if w["end"] < d <= w["easy"] and _get(s, "kind") in ("long", "quality", "test"):
                _as_easy(keep, s, B_EASY_MAX, _("B 賽事「{race}」後 {n} 天只排輕鬆跑", race=w["race"], n=w["days"]), put)
                said = True
            elif w["end"] < d <= w["easy"] and _get(s, "kind") == "easy":
                said = True
        if w["short"] > w["end"] or w["flat"] is not None:
            keep = apply(keep, [w], monday, notes, today, blocked)
        if said and notes is not None:
            notes.append({"level": "info", "src": "race",
                          "text": _("B 賽事「{race}」{date} 後 {n} 天只排輕鬆跑（依賽事大小：短 3 天、中 5 天、"
                                    "馬拉松級以上照 A 賽的恢復期；Pfitzinger，推估）",
                                    race=w["race"], date=_md(w["end"]), n=w["days"])})
    # 3. C races: the race replaces one quality session or the long run
    for e in sorted((e for e in events if getattr(e, "priority", None) == "C" and monday <= e.start <= sunday),
                    key=lambda e: e.start):
        long_first = P.event_size(e) >= P.MEDIUM
        cands = [s for s in keep if not _get(s, "done") and _get(s, "day")]
        longs = [s for s in cands if _get(s, "kind") == "long" or _get(s, "id") == "long"]
        qs = [s for s in cands if _get(s, "kind") == "quality"]
        pick = (longs + qs) if long_first else (qs + longs)
        swap = pick[0] if pick else None
        if swap is not None:
            keep.remove(swap)
        on_day = [s for s in keep if not _get(s, "done") and _get(s, "day") == e.start.isoformat()
                  and _get(s, "kind") in RUN_KINDS]
        for s in on_day:                    # the race day's own run moves into the swapped one's day
            if swap is not None:
                put(s, day=_get(swap, "day"))
            else:
                keep.remove(s)
        keep.append(_race_session(e, is_d, rate, e.start.isoformat() in set(done_days), factory))
        if notes is not None:
            what = (_("長跑") if swap is not None and swap in longs else _("一堂強度課") if swap is not None else "")
            notes.append({"level": "info", "src": "race",
                          "text": (_("C 賽事「{race}」{date} 當訓練跑：取代這週的{what}，其他照常", race=e.name,
                                     date=_md(e.start), what=what) if what else
                                   _("C 賽事「{race}」{date} 當訓練跑，其他照常", race=e.name, date=_md(e.start)))})
    return keep


def b_longer(b, a) -> Optional[str]:
    """How B race `b` is longer than A race `a` (SP-280), as 「X 對 Y」 on the first basis both
    have — days (a multi-day trip), predicted time (planning.event_hours), EP, km (SP-111's order);
    None when it is not longer or nothing compares."""
    from backend.engine import planning as P
    db, da = int(getattr(b, "days", 1) or 1), int(getattr(a, "days", 1) or 1)
    if (db > 1 or da > 1) and db != da:
        return _("{b} 天對 {a} 天", b=db, a=da) if db > da else None
    hb, ha = P.event_hours(b), P.event_hours(a)
    if hb and ha:
        return _("預估時間 {b:.1f} h 對 {a:.1f} h", b=hb, a=ha) if hb > ha else None
    eb, ea = P.event_ep(b), P.event_ep(a)
    if eb is not None and ea is not None:
        return _("EP {b:.0f} 對 {a:.0f}", b=eb, a=ea) if eb > ea else None
    kb, ka = float(getattr(b, "distance_km", 0) or 0), float(getattr(a, "distance_km", 0) or 0)
    if kb and ka:
        return _("{b:.0f} 公里對 {a:.0f} 公里", b=kb, a=ka) if kb > ka else None
    return None


def b_hints(events, monday: dt.date, ctl: Optional[list] = None, phases=None,
            taper_pref: Optional[int] = None) -> list[dict]:
    """The week notes about the B races of the week of `monday` (b_week): too many (> B_PER_MONTH in 30
    days, or `ctl` [(date, CTL)] down > B_CTL_DROP from its peak in the last B_CTL_WEEKS weeks with a B
    race in them), a long one (≥ 中) within B_NEAR_A_DAYS before an A race; SP-280 (Runna [482],
    廠商規則): any B race inside the next A race's 減量期 (`phases`: the plan's phases, for the planned
    taper; else planning.taper_days(A, `taper_pref`)) or longer than it (b_longer)."""
    from backend.engine import planning as P
    out = []
    bs = sorted((e for e in events or () if getattr(e, "priority", None) == "B"), key=lambda e: e.start)
    for e in b_week(events, monday):
        near = [x for x in bs if x is not e and abs((x.start - e.start).days) < 30]
        if len(near) + 1 > B_PER_MONTH:
            out.append({"level": "watch", "src": "race",
                        "text": _("B 賽太多，等於一直在減量：「{race}」前後 30 天內還有 {n} 場 B 賽（CTS：訓練用的比賽一個月最多 1 場）",
                                  race=e.name, n=len(near))})
        a = next((x for x in sorted(events, key=lambda x: x.start) if getattr(x, "priority", None) == "A"
                  and 0 < (x.start - e.end).days <= B_NEAR_A_DAYS), None)
        if a is not None and P.event_size(e) >= P.MEDIUM:
            out.append({"level": "watch", "src": "race",
                        "text": _("長距離 B 賽「{race}」在 A 賽事「{a}」前 {n} 天：A 賽前 2–4 週只建議較短、地形相似的熱身賽，"
                                  "或把它改成 C 賽輕鬆跑（CTS）", race=e.name, a=a.name, n=(a.start - e.end).days)})
        # SP-280 (Runna [482], 廠商規則, 推估): the next A race's 減量期, or longer than that A race
        nxt = next((x for x in sorted(events, key=lambda x: x.start) if getattr(x, "priority", None) == "A"
                    and x.start > e.end), None)
        if nxt is None:
            continue
        in_taper = e.end >= P.taper_start(phases or (), nxt, taper_pref)
        longer = b_longer(e, nxt)
        n = (nxt.start - e.end).days
        if in_taper and longer:
            txt = _("B 賽「{race}」落在 A 賽事「{a}」的減量期內（A 賽前 {n} 天），而且比 A 賽還長（{cmp}）：減量期是 A 賽前"
                    "讓身體恢復的時間，B 賽建議比 A 賽短、不排在 A 賽前 7–10 天內；可改成 C 賽輕鬆跑或拿掉"
                    "（Runna 的做法，廠商規則，推估）", race=e.name, a=nxt.name, n=n, cmp=longer)
        elif in_taper:
            txt = _("B 賽「{race}」落在 A 賽事「{a}」的減量期內（A 賽前 {n} 天）：減量期是 A 賽前讓身體恢復的時間，"
                    "再比一場會影響 A 賽；可改成 C 賽輕鬆跑或拿掉（Runna：B 賽不排在 A 賽前 7–10 天內；廠商規則，推估）",
                    race=e.name, a=nxt.name, n=n)
        elif longer:
            txt = _("B 賽「{race}」比 A 賽事「{a}」還長（{cmp}）：B 賽是 A 賽前的練習賽，建議比 A 賽短；"
                    "或把它改成 C 賽輕鬆跑（Runna：B 賽要比 A 賽短；廠商規則，推估）", race=e.name, a=nxt.name, cmp=longer)
        else:
            continue
        out.append({"level": "watch", "src": "race", "text": txt})
    if ctl:
        lo = monday - dt.timedelta(weeks=B_CTL_WEEKS)
        if any(lo <= e.start < monday for e in bs):
            vals = [(d, v) for d, v in ctl if lo <= d <= monday and v]
            if vals:
                peak = max(v for _d0, v in vals)
                now = vals[-1][1]
                if peak > 0 and 1 - now / peak > B_CTL_DROP:
                    out.append({"level": "watch", "src": "race",
                                "text": _("B 賽太多，等於一直在減量：CTL 從近 {w} 週的高點 {p:.0f} 掉到 {n:.0f}（−{d:.0%}；"
                                          "Friel 的案例掉 13 % 就沒力）", w=B_CTL_WEEKS, p=peak, n=now, d=1 - now / peak)})
    return out
