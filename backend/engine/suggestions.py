"""
The floating suggestion box (static/suggestions.js, loaded by shell.js on
every page): one list of what the planner SUGGESTS but never schedules by
itself. The athlete picks a day (or a day pair), then 「排入」; 「不要」 / ✕
records the choice server-side so the same suggestion doesn't come back.

Kinds (the `type` of a row):
  b2b          連續兩天長天 (engine/b2b.py suggestion): a day pair this week or
               next; accepting stores the two days as the user's sessions.
  race_sim     賽事模擬 (engine/specific_phase.py) 4–3 weeks before the A race: a
               day (a day pair for a multi-day trip); accepting stores it as the
               user's session(s) in place of that week's long day.
  test         a due CP / AeT test of this week (overview.week_plan
               test_suggestions; the interval-library merge) — a day.
  baseline     基線測試 (engine/baseline_test.py, SP-71): the fixed 12′ + 3′ CP test and the
               AeT test the plan is judged by, now and again every 6–8 weeks — a day;
               it stands in for the `test` row of the same kind.
  zone_test    a zone-update retest (engine/zone_events.py suggestions:
               HR shift at the same power, a ≥ 4-week break, the first cool
               spell; engine/threshold_confidence.py: LTHR / max HR not
               believable, SP-64) — a test (AeT / CP) and a day; the 30-min TT
               and the max-HR test are not scheduled here but carry `links`
               (「安排課表」 → the 課表 page's new-session dialog).
  zone_update  a test applied in the last 14 days: the zones were recomputed
               (zone_events.applied_events) — information, ✕ only.
  injury_rest  an open 重（停跑） injury: the next 7 days as 不排課日期 (confirm).
  injury_hold  a 痛 mark inside a re-entry block: hold the volume (information).
  injury_pattern 「跟受傷前很像」 (engine/injury_exposure.py; off by default,
               only with ≥ 5 analysed injuries) — information, ✕ only.
  altitude     高度適應提醒 (engine/altitude.py, SP-100): an event whose GPX
               reaches ≥ 3,000 m, 1–14 days before its start — information, ✕ only.

Ids (the dismissal key): `b2b:<week>`, `test:<kind>:<week>`, `baseline:<kind>:<week>` (all per
week: 「不要」 holds for that week), `zone:<detector id>`, `zone_update:<field>:<date>`,
`altitude:<event id>:<start>:<max m>:<flags>` (the flags say what the reminder found, so a
dismissed one shows again only when that changes).
A dismissal is dropped once its suggestion is no longer computed (prune), so
a zone suggestion that fires again later is new and shows again.

Stored as user_settings `plan.suggestions.dismissed` = {id: {action, at, week}}
with action accepted | declined | dismissed.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _, fmt

KEY = "plan.suggestions.dismissed"
ACTIONS = ("accepted", "declined", "dismissed")
ZONE_TESTS = ("aet", "cp")            # schedulable from the box (tt30: described only)
WEEKLY = ("b2b:", "test:", "baseline:")     # ids that end in their week: a dismissal holds for that week


def _monday(day: str) -> str:
    d = dt.date.fromisoformat(day[:10])
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def _md(day: str) -> str:
    return fmt.date(day, "mdw")                 # 10/2（週四） / Thu 10/2 (backend/i18n/fmt.py)


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


def race_sim_rows(inp: dict, opts, stored: list[dict]) -> list[dict]:
    """The race simulation (engine/specific_phase.sim_suggestion: 賽前第 4–3 週), unless a
    「賽事模擬」 session is already in the plan. `opts(sg)`: the day (or day-pair) options."""
    sg = (inp.get("cur") or {}).get("race_sim_suggestion")
    if not sg:
        return []
    lo, hi = sg["weeks"][0], (dt.date.fromisoformat(sg["weeks"][-1]) + dt.timedelta(days=6)).isoformat()
    if any(str(s.get("title") or "").startswith("賽事模擬") and s.get("state") in ("active", "done")
           and lo <= (s.get("day") or "") <= hi for s in stored):
        return []
    return [{**sg, "pick": "pair" if sg.get("multi") else "day", "options": opts(sg)}]


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


def baseline_rows(due: list[dict], monday: str, session_of, days_for) -> list[dict]:
    """基線測試 (engine/baseline_test.due rows: kind cp | aet, reason first | repeat) → box rows.
    `session_of(kind)`: the test session 「排入」 stores ({title, minutes, …, replaces_long}; None =
    nothing to schedule, no row); `days_for(kind, replaces_long)`: the day options. The numbers in
    the help (± 3 %, 4 %, 3–4 tests) are 推估: plan-backtest-feasibility.md §5.2."""
    from backend.engine.baseline_test import REPEAT_DAYS
    lo, hi = REPEAT_DAYS[0] // 7, REPEAT_DAYS[1] // 7
    out = []
    for d in due:
        kind, s = d["kind"], session_of(d["kind"])
        if not s:
            continue
        name = _("{title}（{minutes} 分）", title=s.get("title") or "", minutes=s.get("minutes") or 0)
        if d["reason"] == "repeat":
            title = _("該重測了：{name}", name=name)
            reason = _("上次是 {weeks} 週前（{day}）。每 {lo}–{hi} 週用同一種方式測一次，才比得出有沒有進步。",
                       weeks=d.get("weeks") or 0, day=fmt.date(d["last"], "md"), lo=lo, hi=hi)
            if d.get("late"):
                reason += _("已經超過 {hi} 週了。", hi=hi)
        else:
            title = _("建議做一次基線測試：{name}", name=name)
            reason = (_("還沒有全力的 CP 測試紀錄。") if kind == "cp" else _("還沒有 AeT（有氧閾值）測試紀錄。")) + \
                _("要知道照課表練有沒有進步，需要先有一個起點。")
        if d.get("rejected"):
            reason += _("{day} 那次沒有算進來（不是全力，或流程不完整）。", day=fmt.date(d["rejected"], "md"))
        help_ = _("測試是建議，不會自動排進課表：挑一天按「排入」。")
        if kind == "cp":
            help_ += _("兩段都要全力，中間休息 30 分鐘不要縮短。")
        else:
            help_ += _("全程維持穩定的輕鬆強度，不是全力。")
        if s.get("replaces_long"):
            help_ += _("徐國峰 90 分鐘測試就是那週的長跑，會取代那天的長跑。")
        help_ += "\n" + _("每次用同一種測法、同一條平路、差不多的天氣，前一天不要練太重，結果才能互相比。")
        if kind == "cp":
            help_ += _("測試本身有誤差（約 ±3%，推估），兩次要差 4% 以上才算有變；一次看不出來，至少要 3–4 次。")
        help_ += "\n" + _("之後每 {lo}–{hi} 週會再提醒一次。", lo=lo, hi=hi)
        out.append({"id": f"baseline:{kind}:{monday}", "type": "baseline", "week": monday, "kind": kind,
                    "title": title, "reason": reason, "help": help_, "minutes": s.get("minutes"),
                    "last": d.get("last"), "repeat": d["reason"] == "repeat",
                    "replaces_long": bool(s.get("replaces_long")),
                    "session": {k: v for k, v in s.items() if k != "replaces_long"},
                    "pick": "day", "options": day_options(days_for(kind, bool(s.get("replaces_long"))))})
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
            help_ += f"。{'、'.join(manual)}請按「安排課表」自己挑一天（這裡不排）"
        # the tests not scheduled here (30-min TT, max-HR test): a 「安排課表」 deep link to the
        # 課表 page's new-session dialog with the template preselected (SP-39 / SP-64)
        links = sg.get("links") or [_manual_link(t, sg.get("earliest")) for t in sg.get("tests") or []
                                    if t not in ZONE_TESTS]
        out.append({"id": f"zone:{sg['id']}", "type": "zone_test", "title": sg["title"], "reason": sg.get("text") or "",
                    "help": (help_ + "。" + (sg.get("caveat") or "")).strip("。") + "。觸發規則為推估。",
                    "src": sg.get("source"), "pick": "test_day" if tests else None, "tests": tests,
                    "links": [x for x in links if x], "wait_cool": bool(sg.get("wait_cool")),
                    "detected": sg.get("detected"), "earliest": sg.get("earliest")})
    for ev in zone.get("events") or []:
        out.append({"id": f"zone_update:{ev.get('field')}:{ev.get('date')}", "type": "zone_update",
                    "title": "區間已更新", "reason": ev.get("text") or "", "pick": None,
                    "help": "套用新的測試後，從那天起的區間、TSS 都用新門檻重算（不會改到之前的日子）。"})
    return out


def _manual_link(test: str, earliest: Optional[str]) -> Optional[dict]:
    from backend.engine import threshold_confidence as TC
    return TC.schedule_link(test, earliest) if test in TC.TEST_TEMPLATE else None


REST_DAYS = 7                         # plan §4.2: 「要不要把今天起 7 天設成不排課日期？」


def injury_rows(events: list[dict], today: str, blocked: set, rp: Optional[dict], marks: list[dict],
                light: Optional[dict] = None) -> list[dict]:
    """傷病紀錄 (engine/injuries.py, plan §4.2–§4.3):
      injury_rest  an open 重（停跑） event — or one whose pain light is red (SP-271, `light`:
                   week_plan's injury_light) — and the next 7 days not all blocked:
                   offer them as 不排課日期 (accepting writes them; never automatic);
      injury_hold  a 痛 / 中斷 mark inside a re-entry block: keep this week's
                   volume (information, ✕ only)."""
    from backend.engine import injuries as INJ
    d = dt.date.fromisoformat(today)
    out = []
    days = [(d + dt.timedelta(days=i)).isoformat() for i in range(REST_DAYS)]
    red = light.get("id") if light and light.get("color") == "red" else None
    for e in INJ.active_on(events, d):
        if (e.get("severity") != "severe" and e.get("id") != red) or all(x in blocked for x in days) \
                or INJ.is_illness(e):
            continue
        lab = INJ.full_label(e.get("area"), e.get("side"))
        why = f"{lab}：重（停跑），傷病紀錄 #{e['id']} 進行中。" if e.get("severity") == "severe" else \
            _("{label}：疼痛紅燈（{reason}），傷病紀錄 #{id} 進行中。", label=lab, reason=light.get("reason") or "",
              id=e["id"])
        out.append({"id": f"injury_rest:{e['id']}", "type": "injury_rest", "pick": "confirm",
                    "accept_label": "設成不排課", "injury_id": e["id"], "start": days[0], "end": days[-1],
                    "title": f"要不要把 {_md(days[0])} 起 {REST_DAYS} 天設成不排課日期？",
                    "reason": why,
                    "help": "按「設成不排課」才會寫入不排課日期（課表頁可以再改）；不會自動改。"
                            "好了以後回來跑，恢復期會照停跑天數排。"})
    if rp and rp.get("return", "9999") <= today < rp.get("end", ""):
        hit = [m for m in marks if m["pain"] >= 2 and rp["return"] <= m["date"] <= today]
        if hit:
            # the pain-monitoring text of the mark's event by its 傷別 (SP-269): its own event, else an
            # event of the same area open that day; none = the general return-to-run rules
            m = hit[-1]
            ev = next((e for e in events if m.get("injury_id") is not None and e.get("id") == m["injury_id"]), None) \
                or next((e for e in INJ.active_on(events, dt.date.fromisoformat(m["date"]))
                         if not INJ.is_illness(e) and m.get("area") and e.get("area") == m["area"]), None)
            mon = INJ.monitor(ev)
            out.append({"id": f"injury_hold:{rp['return']}", "type": "injury_hold", "pick": None,
                        "title": "恢復期內又痛了：先維持這週的量，不要往上加",
                        "reason": f"{m['date']} 記了「{INJ.PAIN.get(m['pain'], '痛')}」"
                                  f"{('・' + INJ.area_label(m['area'])) if m.get('area') else ''}。",
                        "help": mon["text"] + "\n" + mon["disclaimer"]})
    return out


def altitude_rows(events: list, today: str, alt_of, alts_of) -> list[dict]:
    """高度適應提醒 (engine/altitude.py) for the events 1–14 days away. `events`: planning.Event
    (or dicts with id / name / date / days); `alt_of(event)`: altitude.event_altitude (None = no
    GPX); `alts_of()`: the athlete's altitude per day (altitude.day_altitudes, read once, only when
    an event needs it)."""
    from backend.engine import altitude as AL
    d = dt.date.fromisoformat(today)
    alts = None
    out = []
    for e in events:
        get = (lambda k: e.get(k)) if isinstance(e, dict) else (lambda k: getattr(e, k, None))
        start = dt.date.fromisoformat(str(get("date"))[:10])
        if not 0 < (start - d).days <= AL.REMIND_DAYS:
            continue
        alt = alt_of(e)
        if not alt or alt.get("max_m") is None or alt["max_m"] < AL.EVENT_MIN_M:
            continue
        if alts is None:
            alts = alts_of()
        r = AL.reminder({"id": get("id"), "name": get("name"), "start": start, "days": get("days")}, alt,
                        AL.exposure(alts, d, start), d)
        if r is None:
            continue
        out.append({**r, "id": f"altitude:{get('id')}:{r['start']}:{r['max_m']}:{'-'.join(r['flags']) or 'none'}",
                    "type": "altitude", "pick": None, "src": AL.SRC})
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
