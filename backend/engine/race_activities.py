"""
Which activity was a race (SP-674, owner 2026-10-16 option C).

Two ways an activity becomes a race:

  B  a finished season-plan event (it stays in the plan, shown 已完賽 — plan.html) is matched to
     the activity of its day(s) (`results`):
       * candidates: outdoor runs and hikes (a long ultra is sometimes recorded as a hike);
       * a 1-day event / a 連續 multi-day race: any day of its range, one activity for the whole
         course; a 分站 race / a 百岳 trip: each day on its own (day_plan's km / gain, else the total
         split evenly);
       * the target: the distance within ±KM_TOL, OR the climbing within ±GAIN_TOL (a shortened /
         re-routed course keeps its climbing or its length); several candidates → the closest one;
         no distance and no climbing on the event → the longest activity of the day;
       * an activity the user marked as NOT a race (any other activity type) is never picked;
       * no match → `why` says what was there that day.
  A  the user's own mark on the activity page (activity_tags: activity_type race, optionally
     `race_event` = the plan event it was): a link always wins over the automatic pick of that
     event.
  +  a race word in the title (activity_tags.RACE_WORDS — the activity page's auto type 比賽 too),
     unless the user marked it otherwise.

`race_activities(ds, since, until)` is the one query for the readiness / race check / back-test:
every race activity in the window with where it came from (plan / manual / title) and its event.
A 百岳 trip is matched (the plan page shows it done) but is not a race.

Tolerances are 推估 (KM_TOL as maximal.match_events' ±25 % for a shortened course).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _

KM_TOL = 0.25           # 推估: as racepower.maximal.match_events (a shortened course)
GAIN_TOL = 0.30         # 推估: the watch's barometric climbing differs from the organiser's by 10–20 %
GAIN_MIN_M = 100.0      # an event climbing below this is flat: the climbing says nothing
NOT_RACE_KINDS = ("baiyue",)    # matched for the plan page, but a trip is not a race


def _user(tags, w):
    from backend.engine import activity_tags as AT
    return AT.find(tags, w.entry.start, getattr(w.entry, "file", None)) if tags else None


def _is_candidate(w) -> bool:
    if getattr(w, "sport", None) == "run":
        st = (getattr(w, "sport_type", "") or "").lower()
        return st not in ("indoor running", "treadmill running") and "runningtreadmill" not in (w.tags or ())
    st = (getattr(w, "sport_type", "") or "").lower()
    if getattr(w, "sport", None) == "hike" or st in ("hiking", "mountaineering"):
        return True
    try:
        from backend.engine.sport_map import app_type
        return app_type(w) == "hike"
    except Exception:                       # noqa: BLE001 — an odd workout is just not a candidate
        return False


def candidates(ds, tags) -> list[dict]:
    """The runs / hikes of the dataset with the user's mark."""
    from backend.engine import activity_tags as AT
    out = []
    for w in getattr(ds, "workouts", None) or []:
        if not _is_candidate(w) or w.entry.start is None:
            continue
        u = _user(tags, w)
        m = w.metrics or {}
        out.append({"idx": w.idx, "date": w.entry.start.date().isoformat(), "key": AT.key_of(w.entry.start),
                    "file": getattr(w.entry, "file", None),
                    "title": AT.name_of(u) or getattr(w.entry, "title", "") or "",
                    "km": m.get("distance"), "gain_m": m.get("climbing"), "sport": w.sport,
                    "user_type": AT.user_type(u), "race_event": (u or {}).get("race_event")})
    return out


def _err(have, want, floor=0.0) -> Optional[float]:
    if not want or want < floor or have is None:
        return None
    return abs(float(have) / float(want) - 1.0)


def _pick(cands: list[dict], km, gain) -> tuple[Optional[dict], Optional[str]]:
    """(the matched activity, why) among one day's / the range's candidates."""
    def errs(c):
        return _err(c["km"], km), _err(c["gain_m"], gain, GAIN_MIN_M)
    if not km and not (gain and gain >= GAIN_MIN_M):
        best = max(cands, key=lambda c: c["km"] or 0)
        return best, _("賽事沒填距離和爬升，取那天最長的一次")
    def score(c):
        ke, ge = errs(c)
        ok = (ke is not None and ke <= KM_TOL) or (ge is not None and ge <= GAIN_TOL)
        return (not ok, min(x for x in (ke, ge, 9.0) if x is not None))
    best = min(cands, key=score)
    ke, ge = errs(best)
    if score(best)[0]:
        return None, _("最接近的一次是 {km} km、爬升 {gain} m，跟賽事的 {ekm} km、{egain} m 差太多"
                       "（距離要在 ±{kt}% 或爬升 ±{gt}% 內）",
                       km=f"{best['km'] or 0:.1f}", gain=f"{best['gain_m'] or 0:.0f}",
                       ekm=f"{km:.1f}" if km else "—", egain=f"{gain:.0f}" if gain else "—",
                       kt=round(KM_TOL * 100), gt=round(GAIN_TOL * 100))
    if ke is not None and ke <= KM_TOL:
        return best, _("同一天，距離差 {p}%", p=round(ke * 100))
    return best, _("同一天，爬升差 {p}%（距離不合，可能改道）", p=round(ge * 100))


def _days(e) -> list[tuple[list[str], Optional[float], Optional[float]]]:
    """[(dates, km, gain)] the event is matched by: one slot for the whole course, or one per day."""
    start = e.start
    n = max(1, int(e.days or 1))
    dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(n)]
    if n == 1 or getattr(e, "continuous", False):
        return [(dates, e.distance_km, e.climbing_m)]
    dp = e.day_plan or []
    out = []
    for i, d in enumerate(dates):
        if i < len(dp) and dp[i]:
            out.append(([d], dp[i].get("km"), dp[i].get("gain_m")))
        else:
            out.append(([d], (e.distance_km or 0) / n or None, (e.climbing_m or 0) / n or None))
    return out


def _act(c: dict, how: str, why: str) -> dict:
    return {"idx": c["idx"], "date": c["date"], "key": c["key"], "file": c["file"], "title": c["title"],
            "km": c["km"], "gain_m": c["gain_m"], "how": how, "why": why}


def match_event(e, cands: list[dict], today: dt.date) -> dict:
    """One plan event → {event_id, status: upcoming | matched | partial | unmatched, activities, why}."""
    base = {"event_id": e.id, "activities": []}
    if e.start > today:
        return {**base, "status": "upcoming", "why": ""}
    linked = [c for c in cands if c["race_event"] == e.id]
    if linked:
        return {**base, "status": "matched", "why": _("你在活動頁指定的"),
                "activities": [_act(c, "manual", _("你在活動頁指定的")) for c in sorted(linked, key=lambda c: c["key"])]}
    acts, missing = [], []
    for dates, km, gain in _days(e):
        if dates[0] > today.isoformat():
            continue
        day = [c for c in cands if c["date"] in dates and not c["race_event"]]
        free = [c for c in day if c["user_type"] in (None, "race")]
        when = dates[0] if len(dates) == 1 else f"{dates[0]}～{dates[-1]}"
        if not free:
            missing.append(_("{when}：你把那天的活動標成不是比賽", when=when) if day
                           else _("{when}：那天沒有跑步或登山紀錄", when=when))
            continue
        best, why = _pick(free, km, gain)
        if best is None:
            missing.append(_("{when}：{why}", when=when, why=why))
        else:
            acts.append(_act(best, "auto", why))
    status = "matched" if acts and not missing else "partial" if acts else "unmatched"
    why = _("；").join(missing) if missing else acts[0]["why"] if len(acts) == 1 else _("每天都對上了")
    return {**base, "status": status, "activities": acts, "why": why}


def results(ds, today: Optional[dt.date] = None, tags: Optional[list] = None, plan=None) -> dict[str, dict]:
    """{event id: match_event} for every plan event. `tags`: activity_tags.load() rows (None = the
    app DB of this tenant)."""
    from backend.engine import activity_tags as AT
    if today is None:
        from backend.engine.localtime import today_local
        today = today_local()
    tags = AT.load() if tags is None else tags
    plan = plan if plan is not None else getattr(ds, "plan", None)
    cands = candidates(ds, tags)
    return {e.id: match_event(e, cands, today) for e in getattr(plan, "events", None) or []}


def _event_json(e) -> dict:
    return {"id": e.id, "name": e.name, "date": str(e.date)[:10], "kind": e.kind,
            "priority": (e.priority or "").upper() or None, "distance_km": e.distance_km,
            "climbing_m": e.climbing_m}


def race_activities(ds, since: dt.date, until: dt.date, tags: Optional[list] = None,
                    today: Optional[dt.date] = None, plan=None) -> list[dict]:
    """Every race activity with since ≤ date ≤ until, oldest first:
    {idx, date, key, file, title, km, gain_m, source: plan | manual | title, how (plan: auto | manual),
    event: {id, name, date, kind, priority, …} | None, priority: the event's A / B / C | None}."""
    from backend.engine import activity_tags as AT
    if today is None:
        from backend.engine.localtime import today_local
        today = today_local()
    tags = AT.load() if tags is None else tags
    plan = plan if plan is not None else getattr(ds, "plan", None)
    events = {e.id: e for e in getattr(plan, "events", None) or []}
    cands = candidates(ds, tags)
    lo, hi = since.isoformat(), until.isoformat()
    out: dict[int, dict] = {}
    for eid, r in ((e.id, match_event(e, cands, today)) for e in events.values()):
        e = events[eid]
        if e.kind in NOT_RACE_KINDS:
            continue
        for a in r["activities"]:
            out[a["idx"]] = {**a, "source": "plan", "event": _event_json(e), "priority": _event_json(e)["priority"]}
    for c in cands:
        if c["idx"] in out or not lo <= c["date"] <= hi:
            continue
        if c["user_type"] == "race":
            ev = events.get(c["race_event"]) if c["race_event"] else None
            if ev is not None and ev.kind in NOT_RACE_KINDS:
                ev = None
            out[c["idx"]] = {**_act(c, "manual", _("你在活動頁標成比賽")), "source": "manual",
                             "event": _event_json(ev) if ev else None,
                             "priority": _event_json(ev)["priority"] if ev else None}
        elif c["user_type"] is None and c["sport"] == "run" and AT.RACE_WORDS.search(c["title"] or ""):
            out[c["idx"]] = {**_act(c, "auto", _("標題有比賽的字")), "source": "title", "event": None,
                             "priority": None}
    return sorted((a for a in out.values() if lo <= a["date"] <= hi), key=lambda a: a["key"] or a["date"])
