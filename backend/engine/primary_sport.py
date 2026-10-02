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

Suggestion (推估 rule, no published threshold):
  1. the next A event: 路跑賽 → road; 越野賽 / 百岳 → trail;
  2. else the share of trail + hike time among the foot activities of the last 12 weeks:
     ≥ 25 % → trail, else road;
  3. no data → trail (keeps the original behaviour).
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


def _a_event(events: Iterable, today: dt.date):
    ahead = sorted((e for e in events or () if getattr(e, "priority", "A") == "A"
                    and e.start >= today and getattr(e, "kind", None) in EVENT_SPORT), key=lambda e: e.start)
    return ahead[0] if ahead else None


def suggest(ds=None, events: Iterable = (), today: Optional[dt.date] = None) -> dict:
    """{"sport", "reason", "basis": event | share | default, "trail_share", "hours"}."""
    from backend.engine import overview as O
    if today is None:
        today = O.day_to_date(ds.today) if ds is not None else dt.date.today()
    ev = _a_event(events, today)
    if ev is not None:
        sp = EVENT_SPORT[ev.kind]
        from backend.engine.planning import KINDS
        return {"sport": sp, "basis": "event", "trail_share": None, "hours": None,
                "reason": _("下一場 A 賽「{name}」是{kind}", name=ev.name, kind=_(KINDS.get(ev.kind, ev.kind)))}
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
