"""
主要訓練項目 — 越野跑 (trail / mountain, the app's original behaviour) or 路跑／馬拉松 (road).

One setting (`athlete.primary_sport`: auto | trail | road) in the normal settings. "auto"
follows the suggestion below; picking one fixes it (the user chooses, the data only suggests).
What changes per mode: docs/plans/generalize-athlete.plan.md §5.

  * charts (views/*.json): a chart may carry `"sports": ["trail"]` or `["road"]`; the viewer
    hides the charts that don't list the athlete's sport (no tag = both). `"order": {"road": n}`
    moves a chart within its dashboard (lower first; untagged charts keep their file order).
  * planner (engine/overview.week_plan, projection): road = no steep-hill walks, no B2B
    weekend, no mountain long run / uphill interval versions; the 專項期 long run carries a
    marathon-pace segment (Pfitzinger / Daniels).
  * 插入範本 推薦 (engine/template_recs.py) and the race calculator (static/racepower.html).

Suggestion (推估 rule, no published threshold; SP-245 — the user's decision, 2026-10-06):
  1. the future A events decide first: only 越野賽 / 百岳 → trail (the nearest is named), only
     路跑賽 → road (the next is named); both → the HARDER race's sport (`_harder`: planning.
     event_size tier, then predicted hours, then EP); a tie or an unknown size → trail;
  2. no future A event → the future B events, the same way;
  3. else the share of trail + hike time among the foot activities of the last 12 weeks:
     ≥ 25 % → trail, else road;
  4. no data → trail (keeps the original behaviour).
C events, past events and 其他 never count; A / B events have no time window.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from backend.i18n import N_, _

SPORTS = ("trail", "road")
CHOICES = ("auto",) + SPORTS
SETTING_KEY = "athlete.primary_sport"
LABEL = {"trail": N_("越野跑"), "road": N_("路跑／馬拉松")}
WINDOW_DAYS = 84                # 推估: 12 weeks of training
TRAIL_SHARE_MIN = 0.25          # 推估: a quarter of the foot time on trails / mountains = a trail runner
MIN_HOURS = 5.0                 # less than this in the window = no data
EVENT_SPORT = {"road": "road", "race": "trail", "baiyue": "trail"}


def norm(v) -> str:
    return v if v in CHOICES else "auto"


def stored(user_id: int = 1) -> str:
    """The setting as stored (auto | trail | road); a synchronous read like plan_prefs.load."""
    from backend.engine.wko5expr.datasource import read_setting
    return norm(read_setting(SETTING_KEY, "auto", user_id))


def _ahead(events: Iterable, today: dt.date, priorities: tuple) -> list:
    """The future events of these priorities whose kind decides a sport, nearest first."""
    return sorted((e for e in events or () if getattr(e, "priority", "A") in priorities
                   and e.start >= today and getattr(e, "kind", None) in EVENT_SPORT), key=lambda e: e.start)


HARDER_BY = {"size": N_("賽事大小"), "hours": N_("預估時間"), "ep": "EP"}   # what decided _harder (EP: no translation)


def _hardness(ev) -> Optional[tuple]:
    """(size tier, predicted hours | None, EP | None) of an event; None when nothing about its
    size is known (one day, no predicted time, no distance)."""
    from backend.engine import planning as PL
    hours = PL.event_hours(ev)
    if int(getattr(ev, "days", 1) or 1) <= 1 and not hours and not getattr(ev, "distance_km", None):
        return None
    return PL.event_size(ev, hours), hours, PL.event_ep(ev)


def _harder(a, b) -> Optional[str]:
    """Which of two events is harder (SP-245): "a" / "b" and what decided it ("size" |
    "hours" | "ep"), or None for a tie or an unknown size."""
    ha, hb = _hardness(a), _hardness(b)
    if ha is None or hb is None:
        return None
    for i, by in enumerate(("size", "hours", "ep")):
        x, y = ha[i], hb[i]
        if x is not None and y is not None and x != y:
            return ("a" if x > y else "b"), by
    return None


def _hardest(evs: list):
    """The hardest of these (nearest first) events; a tie keeps the nearer one."""
    best = evs[0]
    for e in evs[1:]:
        if (_harder(e, best) or ("", ""))[0] == "a":
            best = e
    return best


def _event_pick(events: Iterable, today: dt.date):
    """The event that decides the sport (SP-245) and, when a trail and a road race were
    compared, (the other race, what decided it or None for tie / unknown → trail); or (None,
    None). A events first, B only when there is no future A. C events never count."""
    for prio in ("A", "B"):
        ahead = _ahead(events, today, (prio,))
        if not ahead:
            continue
        trail = [e for e in ahead if EVENT_SPORT[e.kind] == "trail"]
        road = [e for e in ahead if EVENT_SPORT[e.kind] == "road"]
        if not road:
            return trail[0], None
        if not trail:
            return road[0], None
        t, r = _hardest(trail), _hardest(road)
        win = _harder(t, r)
        if win is not None and win[0] == "b":
            return r, (t, win[1])
        return t, (r, win[1] if win else None)
    return None, None


def suggest(ds=None, events: Iterable = (), today: Optional[dt.date] = None) -> dict:
    """{"sport", "reason", "basis": event | share | default, "trail_share", "hours"}."""
    from backend.engine import overview as O
    if today is None:
        today = O.day_to_date(ds.today) if ds is not None else dt.date.today()
    ev, vs = _event_pick(events, today)
    if ev is not None:
        sp = EVENT_SPORT[ev.kind]
        from backend.engine.planning import KINDS
        kind = _(KINDS.get(ev.kind, ev.kind))
        prio = getattr(ev, "priority", "A")
        if vs is None:
            reason = (_("下一場 {priority} 賽「{name}」是{kind}", priority=prio, name=ev.name, kind=kind)
                      if sp == "road" else _("{priority} 賽「{name}」是{kind}", priority=prio, name=ev.name, kind=kind))
        else:
            other, by = vs
            okind = _(KINDS.get(other.kind, other.kind))
            reason = (_("{priority} 賽「{name}」（{kind}）比「{other}」（{other_kind}）更吃力（依{by}）",
                        priority=prio, name=ev.name, kind=kind, other=other.name, other_kind=okind,
                        by=_(HARDER_BY[by]) if by != "ep" else "EP") if by
                      else _("{priority} 賽「{name}」（{kind}）和「{other}」（{other_kind}）一樣吃力或比不出來，越野優先",
                             priority=prio, name=ev.name, kind=kind, other=other.name, other_kind=okind))
        return {"sport": sp, "basis": "event", "trail_share": None, "hours": None, "reason": reason}
    secs = {"road": 0.0, "trail": 0.0, "hike": 0.0}
    if ds is not None:
        for w in O.workouts_between(ds, today - dt.timedelta(days=WINDOW_DAYS), today + dt.timedelta(days=1)):
            c = O.category(w)
            if c in secs:
                secs[c] += O.moving_s(w)
    hours = sum(secs.values()) / 3600.0
    if hours < MIN_HOURS:
        return {"sport": "trail", "basis": "default", "trail_share": None, "hours": hours,
                "reason": _("近 12 週的跑步資料不夠，先用越野跑（原本的設定）")}
    share = (secs["trail"] + secs["hike"]) / 3600.0 / hours
    sp = "trail" if share >= TRAIL_SHARE_MIN else "road"
    return {"sport": sp, "basis": "share", "trail_share": share, "hours": hours,
            "reason": _("近 12 週越野＋登山佔跑步時間 {share:.0%}（≥ {min:.0%} 算越野跑，推估）",
                        share=share, min=TRAIL_SHARE_MIN)}


def resolve(ds=None, events: Iterable = (), today: Optional[dt.date] = None,
            setting: Optional[str] = None) -> dict:
    """{"setting", "sport" (in effect), "label", "suggested": suggest()}."""
    setting = norm(setting if setting is not None else stored())
    sug = suggest(ds, events, today)
    sport = sug["sport"] if setting == "auto" else setting
    return {"setting": setting, "sport": sport, "label": _(LABEL[sport]), "suggested": sug}


def effective(ds=None, events: Iterable = (), today: Optional[dt.date] = None,
              setting: Optional[str] = None) -> str:
    return resolve(ds, events, today, setting)["sport"]


# ---------------------------------------------------------------------------
# chart tags (views/*.json)
# ---------------------------------------------------------------------------

def chart_sports(raw) -> Optional[list]:
    """A chart's `sports` tag, validated: None (both) or a non-empty subset of SPORTS."""
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw or any(s not in SPORTS for s in raw):
        raise ValueError(f"sports must be a non-empty list of {list(SPORTS)}")
    return list(dict.fromkeys(raw))


def shows(chart: dict, sport: str) -> bool:
    sp = chart.get("sports")
    return not sp or sport in sp
