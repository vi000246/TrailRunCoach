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
            if not (w["no_run"] < d <= w["short"]):
                continue
            touched = True
            m = int(_get(s, "minutes") or 0)
            nm = min(m, P.REC_SHORT_MIN)
            detail = _("賽後第 1 週：輕鬆跑 ≤ {max} 分", max=P.REC_SHORT_MIN)
            if w["flat"] is not None and d <= w["flat"]:
                detail += _("，平路、不跑下坡")
            if _get(s, "kind") != "easy":
                n_easy += 1
                put(s, id=f"easy{n_easy}", kind="easy", title=_("輕鬆跑"), **{k: None for k in _CLEAR})
            put(s, minutes=nm, tss=round(float(_get(s, "tss") or 0.0) * (nm / m if m else 1.0), 1), detail=detail)
            if w["flat"] is not None and d <= w["flat"]:
                put(s, terrain="road", climb_m=None)
        if touched and notes is not None:
            txt = _("A 賽事「{race}」{date} 後：前 {n} 天不排跑步和肌力，第 1 週每次 ≤ {max} 分輕鬆跑",
                    race=w["race"], date=f"{w['end'].month}/{w['end'].day}", n=P.REC_NO_RUN_DAYS, max=P.REC_SHORT_MIN)
            if w["flat"] is not None:
                txt += _("；下坡比你近 6 週練過的多，賽後 {h} 小時內走平路、不排硬課", h=P.DOWNHILL_FLAT_DAYS * 24)
            if gone:
                txt += _("（{n} 堂排不開，拿掉了，不用補）", n=gone)
            notes.append({"level": "info", "src": "recovery", "text": txt})
    return keep
