"""
The floating suggestion box (static/suggestions.js, loaded by shell.js on
every page): one list of what the planner SUGGESTS but never schedules by
itself. The athlete picks a day (or a day pair), then 「排入」; 「不要」 / ✕
records the choice server-side so the same suggestion doesn't come back.

Kinds (the `type` of a row):
  b2b          連續兩天長天 (engine/b2b.py suggestion): a day pair this week or
               next; accepting stores the two days as the user's sessions.
  test         a due CP / AeT test of this week (overview.week_plan
               test_suggestions; the interval-library merge) — a day.
  zone_test    a zone-update retest (engine/zone_events.py suggestions:
               HR shift at the same power, a ≥ 4-week break, the first cool
               spell) — a test (AeT / CP; the 30-min TT is described, not
               scheduled here) and a day.
  zone_update  a test applied in the last 14 days: the zones were recomputed
               (zone_events.applied_events) — information, ✕ only.

Ids (the dismissal key): `b2b:<week>`, `test:<kind>:<week>` (both per week:
「不要」 holds for that week), `zone:<detector id>`, `zone_update:<field>:<date>`.
A dismissal is dropped once its suggestion is no longer computed (prune), so
a zone suggestion that fires again later is new and shows again.

Stored as user_settings `plan.suggestions.dismissed` = {id: {action, at, week}}
with action accepted | declined | dismissed.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

KEY = "plan.suggestions.dismissed"
ACTIONS = ("accepted", "declined", "dismissed")
ZONE_TESTS = ("aet", "cp")            # schedulable from the box (tt30: described only)


def _monday(day: str) -> str:
    d = dt.date.fromisoformat(day[:10])
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def _md(day: str) -> str:
    d = dt.date.fromisoformat(day[:10])
    return f"{d.month}/{d.day}（週{'一二三四五六日'[d.weekday()]}）"


def day_options(days: list[dict]) -> list[dict]:
    """plan_sessions.suggestion_days rows → the box's options."""
    return [{"day": d["day"], "label": _md(d["day"]), "note": d.get("note") or ""} for d in days]


def b2b_rows(inp: dict, today: str, pair_opts) -> list[dict]:
    """The B2B suggestions of this week and the next (engine/b2b.SUGGEST_AHEAD_DAYS).
    `pair_opts(sg)`: the day pairs (api: from the stored plan)."""
    from backend.engine import b2b as B2B
    cur = inp.get("cur") or {}
    cands = [cur.get("b2b_suggestion")] + [w.get("b2b_suggestion") for w in inp.get("weeks") or []]
    lim = (dt.date.fromisoformat(today) + dt.timedelta(days=B2B.SUGGEST_AHEAD_DAYS)).isoformat()
    out = []
    for sg in cands:
        if not sg or sg["week"] > lim:
            continue
        out.append({**sg, "pick": "pair", "options": pair_opts(sg)})
    return out


def test_rows(tests: list[dict], monday: str) -> list[dict]:
    """plan_sessions._suggestions rows (with their `days`) → box rows."""
    out = []
    for sg in tests:
        name = "AeT" if sg["kind"] == "aet" else "CP"
        out.append({"id": f"test:{sg['kind']}:{monday}", "type": "test", "week": monday, "kind": sg["kind"],
                    "title": f"建議做一次 {name} 測試：{sg['title']}（{sg['minutes']} 分）",
                    "reason": sg.get("reason") or "", "minutes": sg.get("minutes"),
                    "help": ("測試是建議，不會自動排進課表：挑一天按「排入」。"
                             + ("徐國峰 90 分鐘測試就是那週的長跑，會取代那天的長跑。" if sg.get("replaces_long") else
                                "建議的日子避開長跑、強度課的前後一天。")),
                    "pick": "day", "options": day_options(sg.get("days") or [])})
    return out


def zone_rows(zone: dict, covered_kinds: set, scheduled: callable, days_for) -> list[dict]:
    """engine/zone_events.py suggestions + applied events → box rows.
    `covered_kinds`: test kinds this week's test suggestions already offer
    (not offered twice); `scheduled(kind, since)`: a test of that kind is in
    the plan since that day; `days_for(kind, earliest)`: the day options."""
    from backend.engine import zone_events as ZE
    out = []
    for sg in zone.get("suggestions") or []:
        tests = []
        for t in sg.get("tests") or []:
            if t not in ZONE_TESTS or t in covered_kinds or scheduled(t, sg.get("detected") or ""):
                continue
            tests.append({"key": t, "label": ZE.TEST_LABEL.get(t, t),
                          "options": day_options(days_for(t, sg.get("earliest")))})
        manual = [ZE.TEST_LABEL[t] for t in sg.get("tests") or [] if t not in ZONE_TESTS]
        if not tests and not manual:
            continue
        help_ = "；".join(sg.get("conditions") or [])
        if manual:
            help_ += f"。{'、'.join(manual)}請自己在課表新增（這裡不排）"
        out.append({"id": f"zone:{sg['id']}", "type": "zone_test", "title": sg["title"], "reason": sg.get("text") or "",
                    "help": (help_ + "。" + (sg.get("caveat") or "")).strip("。") + "。觸發規則為推估。",
                    "src": sg.get("source"), "pick": "test_day" if tests else None, "tests": tests,
                    "detected": sg.get("detected"), "earliest": sg.get("earliest")})
    for ev in zone.get("events") or []:
        out.append({"id": f"zone_update:{ev.get('field')}:{ev.get('date')}", "type": "zone_update",
                    "title": "區間已更新", "reason": ev.get("text") or "", "pick": None,
                    "help": "套用新的測試後，從那天起的區間、TSS 都用新門檻重算（不會改到之前的日子）。"})
    return out


def visible(rows: list[dict], dismissed: dict) -> list[dict]:
    return [r for r in rows if r["id"] not in (dismissed or {})]


def prune(dismissed: dict, rows: list[dict], today: str) -> dict:
    """Dismissals whose suggestion is still computed (or a B2B / test of this
    week or later) stay; the rest go, so a later, new suggestion shows again."""
    ids = {r["id"] for r in rows}
    mon = _monday(today)
    keep = {}
    for k, v in (dismissed or {}).items():
        if k in ids or (v.get("week") and v["week"] >= mon):
            keep[k] = v
    return keep


def record(dismissed: dict, sid: str, action: str, now: dt.datetime, week: Optional[str] = None) -> dict:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    return {**(dismissed or {}), sid: {"action": action, "at": now.isoformat(timespec="seconds"),
                                       **({"week": week} if week else {})}}
