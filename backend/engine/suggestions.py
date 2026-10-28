"""
The floating suggestion box (static/suggestions.js, loaded by shell.js on
every page): one list of what the planner SUGGESTS but never schedules by
itself. The athlete picks a day (or a day pair), then 「排入」; 「不要」 / ✕
records the choice server-side so the same suggestion doesn't come back.

Kinds (the `type` of a row):
  b2b          連續兩天長天 (engine/b2b.py suggestion): a day pair this week or
               next; accepting stores the two days as the user's sessions.
  race_sim     賽事模擬 (engine/specific_phase.py) 6 and 4 weeks before the A race (路跑 4 and 3): a
               day (a day pair for a multi-day trip); accepting stores it as the
               user's session(s) in place of that week's long day.
  test         a due CP / AeT test of this week (overview.week_plan
               test_suggestions; the interval-library merge) — a day.
  baseline     基線測試 (engine/baseline_test.py, SP-71): the fixed 12′ + 3′ CP test and the
               AeT test the plan is judged by, now and again every 6–8 weeks — a day;
               it stands in for the `test` row of the same kind.
  zone_test    a zone-update retest (engine/zone_events.py suggestions:
               HR shift at the same power, a ≥ 4-week break, the first cool
               spell, easy runs above the easy zone = the CP may be low (SP-848);
               engine/threshold_confidence.py: LTHR / max HR not
               believable, SP-64) — a test (AeT / CP) and a day; the 30-min TT
               and the max-HR test are not scheduled here but carry `links`
               (「安排課表」 → the 課表 page's new-session dialog).
  zone_update  a test applied in the last 14 days: the zones were recomputed
               (zone_events.applied_events) — information, ✕ only.
  injury_rest  an open 重（停跑） injury: the next 7 days as 不排課日期 (confirm).
  injury_hold  a 痛 mark inside a re-entry block: hold the volume (information).
  injury_done  SP-273: an open injury that looks healed (injuries.done_check) — 「好了」 resolves it
               (confirm; never automatic), 「還沒」 hides it for 7 days (ids `injury_done:<id>`).
  injury_pattern 「跟受傷前很像」 (engine/injury_exposure.py; off by default,
               only with ≥ 5 analysed injuries) — information, ✕ only.
  altitude     高山症預防提醒 (engine/altitude.py, SP-546): a 百岳 trip or an event whose GPX
               reaches ≥ 3,000 m, 1–7 days before its start, unless the athlete's activities reached
               3,000 m in the last 3 months — information, ✕ / 稍後提醒 only (SP-546 R1: 稍後提醒 brings it
               back the day before the trip, not SNOOZE_DAYS later).
  hr_zone_switch SP-493: the 自動 課表心率區間 switched the zones in effect by itself (plan_auto
               .check_hr_model, plan.hr_zone_auto) — told once: information, ✕ only, for HR_SWITCH_DAYS
               (id `hr_zone_switch:<since>`).

Ids (the dismissal key): `b2b:<week>`, `test:<kind>:<week>`, `baseline:<kind>:<week>` (all per
week: ✕ / 排入 hold for that week, 「不要」 on a B2B too — 「不要」 on a test holds that test for good, below),
`zone:<detector id>`, `zone_update:<field>:<date>`,
`altitude:<event id>:<start>` (SP-546: one per trip — closed once, it does not show again for that trip;
the same trip moved to another date is a new one).
A dismissal is dropped once its suggestion is no longer computed (prune), so
a zone suggestion that fires again later is new and shows again.

稍後提醒 (SP-507, every row but injury_done, whose 「還沒」 already is one): the row is hidden for
SNOOZE_DAYS (an altitude row: until the day before its trip, SP-546 R1), then shows again if it is still computed — unless a test it suggests was done since
(`tested`: a done test session, a test the review found, a measured threshold). The record is kept
under the row's id with `key` = snooze_key(id): a weekly id's next week (`test:cp:<next monday>`) is
the same suggestion, and a 基線測試 and the weekly test of the same kind are one test.
Only the box hides it: the 課表's 排入測試 ▸ still offers a test while it is held (SP-507 R1,
`visible(..., snoozes=False)`).

「不要」 on a test (SP-515, `test:` / `baseline:` ids): held by the test's kind, not the week —
decline_key(id) = snooze_key(id) (`test:cp`; a 基線測試 and the weekly test of the same kind are one
test), so next week's row of that test does not show either; prune keeps it. It is undone only by
restore(key) (the settings page's 「不再建議的測試」, declined_tests).

Stored as user_settings `plan.suggestions.dismissed` = {id: {action, at, week}}
with action accepted | declined | dismissed; snoozed = {action, at, until, key, tests}.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _, fmt

KEY = "plan.suggestions.dismissed"
SNOOZED = "snoozed"                   # 稍後提醒 (SP-507)
ACTIONS = ("accepted", "declined", "dismissed", SNOOZED)
SNOOZE_DAYS = 7                       # 「一週後再提醒」 (the owner, SP-507)
ZONE_TESTS = ("aet", "cp")            # schedulable from the box (tt30: described only)
TEST_TYPES = ("test", "baseline", "zone_test")     # the rows that suggest a test (排入測試 ▸, the overview's 測試)
ALT = "altitude:"                           # 高山症預防提醒 (SP-546): ends in the trip's start; held until then
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
    """The race simulation (engine/specific_phase.sim_suggestion: 越野 賽前第 6、4 週, 路跑 4、3), unless a
    「賽事模擬」 session is already in the plan from its first week up to the race (`until`; SP-523: one
    accepted under the old 4–3 window still counts). `opts(sg)`: the day (or day-pair) options."""
    sg = (inp.get("cur") or {}).get("race_sim_suggestion")
    if not sg:
        return []
    lo = sg["weeks"][0]
    hi = max(sg.get("until") or "", (dt.date.fromisoformat(sg["weeks"][-1]) + dt.timedelta(days=6)).isoformat())
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
                    # (SP-471: translated — the overview's easy-power block shows it too)
                    "title": _("建議做一次 {test} 測試：{name}", test=name,
                               name=_("{title}（{minutes} 分）", title=sg["title"], minutes=sg["minutes"])),
                    "reason": sg.get("reason") or "", "minutes": sg.get("minutes"),
                    "help": (_("測試是建議，不會自動排進課表：挑一天按「排入」。")
                             + (_("徐國峰 90 分鐘測試就是那週的長跑，會取代那天的長跑。") if sg.get("replaces_long") else
                                _("建議的日子避開長跑、強度課的前後一天。"))),
                    "pick": "day", "options": day_options(sg.get("days") or []),
                    # SP-471: the easy-power conflict's reason ({summary, why, lthr_doubt}) and the
                    # 30-min LTHR test's 安排課表 link when the LTHR is the doubt
                    **({"easy_power": sg["easy_power"]} if sg.get("easy_power") else {}),
                    **({"links": sg["links"]} if sg.get("links") else {})})
    return out


def carry_easy_power(rows: list[dict]) -> list[dict]:
    """SP-471: a 基線測試 row stands in for the weekly `test` row of its kind — it takes that row's
    easy-power reason and links along, so the overview's block still shows it. In place; returned."""
    ep = {r["kind"]: r for r in rows if r.get("type") == "test" and r.get("easy_power")}
    for r in rows:
        t = ep.get(r.get("kind")) if r.get("type") == "baseline" else None
        if t is None or r.get("easy_power"):
            continue
        r["easy_power"] = t["easy_power"]
        r["reason"] = " · ".join(x for x in (r.get("reason"), t["easy_power"]["why"]) if x)
        if t.get("links"):
            r["links"] = list(r.get("links") or []) + t["links"]
    return rows


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
            # SP-954: a suggestion's own `labels` (zone_events.without_cp_tests: 「30 分鐘獨跑測試（LTHR）」 without a Stryd)
            tests.append({"key": t, "label": (sg.get("labels") or {}).get(t) or ZE.TEST_LABEL.get(t, t),
                          "options": day_options(days_for(t, sg.get("earliest")))})
        manual = [(sg.get("labels") or {}).get(t) or ZE.TEST_LABEL[t] for t in sg.get("tests") or []
                  if t not in ZONE_TESTS]
        if not tests and not manual:
            continue
        help_ = _("；").join(sg.get("conditions") or [])
        if manual:
            help_ += _("。{tests}請按「安排課表」自己挑一天（這裡不排）", tests=_("、").join(_(m) for m in manual))
        # the tests not scheduled here (30-min TT, max-HR test): a 「安排課表」 deep link to the
        # 課表 page's new-session dialog with the template preselected (SP-39 / SP-64)
        links = sg.get("links") or [_manual_link(t, sg.get("earliest")) for t in sg.get("tests") or []
                                    if t not in ZONE_TESTS]
        out.append({"id": f"zone:{sg['id']}", "type": "zone_test", "title": sg["title"], "reason": sg.get("text") or "",
                    "help": _("{text}。觸發規則為推估。", text=(help_ + "。" + (sg.get("caveat") or "")).strip("。")),
                    "src": sg.get("source"), "pick": "test_day" if tests else None, "tests": tests,
                    "links": [x for x in links if x], "wait_cool": bool(sg.get("wait_cool")),
                    "detected": sg.get("detected"), "earliest": sg.get("earliest")})
    for ev in zone.get("events") or []:
        out.append({"id": f"zone_update:{ev.get('field')}:{ev.get('date')}", "type": "zone_update",
                    "title": _("區間已更新"), "reason": ev.get("text") or "", "pick": None,
                    "help": _("套用新的測試後，從那天起的區間、訓練負荷（Training load）都用新門檻重算（不會改到之前的日子）。")})
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
        why = _("{label}：重（停跑），傷病紀錄 #{id} 進行中。", label=lab, id=e["id"]) if e.get("severity") == "severe" else \
            _("{label}：疼痛紅燈（{reason}），傷病紀錄 #{id} 進行中。", label=lab, reason=light.get("reason") or "",
              id=e["id"])
        out.append({"id": f"injury_rest:{e['id']}", "type": "injury_rest", "pick": "confirm",
                    "accept_label": _("設成不排課"), "injury_id": e["id"], "start": days[0], "end": days[-1],
                    "title": _("要不要把 {d} 起 {n} 天設成不排課日期？", d=_md(days[0]), n=REST_DAYS),
                    "reason": why,
                    "help": _("按「設成不排課」才會寫入不排課日期（課表頁可以再改）；不會自動改。"
                              "好了以後回來跑，恢復期會照停跑天數排。")})
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
                        "title": _("恢復期內又痛了：先維持這週的量，不要往上加"),
                        "reason": _("{d} 記了「{pain}」{area}。", d=m["date"], pain=_(INJ.PAIN.get(m["pain"], N_("痛"))),
                                    area=_("・{area}", area=_(INJ.area_label(m["area"]))) if m.get("area") else ""),
                        "help": mon["text"] + "\n" + mon["disclaimer"]})
    return out


def altitude_rows(events: list, today: str, alt_of, alts_of) -> list[dict]:
    """高山症預防提醒 (engine/altitude.py, SP-546) for the 百岳 / ≥ 3,000 m trips 1–7 days away.
    `events`: planning.Event (or dicts with id / name / date / kind); `alt_of(event)`:
    altitude.event_altitude (None = no GPX; a 百岳 still counts); `alts_of()`: the athlete's
    altitude per day (altitude.day_altitudes, read once, only when such a trip is that close)."""
    from backend.engine import altitude as AL
    d = dt.date.fromisoformat(today)
    alts = None
    out = []
    for e in events:
        get = (lambda k: e.get(k)) if isinstance(e, dict) else (lambda k: getattr(e, k, None))
        start = dt.date.fromisoformat(str(get("date"))[:10])
        if not 0 < (start - d).days <= AL.NOTICE_DAYS:
            continue
        alt = alt_of(e)
        if not AL.is_high_trip(get("kind"), alt):
            continue
        if alts is None:
            alts = alts_of()
        r = AL.reminder({"id": get("id"), "name": get("name"), "start": start, "kind": get("kind")},
                        alt, AL.exposure(alts, d, start), d)
        if r is None:
            continue
        # one id per trip (SP-546): closed = gone for that trip; moved to another date = a new one
        out.append({**r, "id": f"altitude:{get('id')}:{r['start']}", "type": "altitude", "pick": None})
    return out


HR_SWITCH_DAYS = 14                    # SP-493: the switch's row stays this long unless closed (zone_update: 14)


def hr_switch_rows(rec: Optional[dict], today: str) -> list[dict]:
    """SP-493: 「課表心率區間自動換成 X」 once — the switch 自動 made by itself (plan.hr_zone_auto with
    auto: true), from its day for HR_SWITCH_DAYS; ✕ hides it (its id is the day)."""
    if not isinstance(rec, dict) or not rec.get("auto") or not rec.get("from") or not rec.get("since"):
        return []
    try:
        age = (dt.date.fromisoformat(today[:10]) - dt.date.fromisoformat(str(rec["since"])[:10])).days
    except ValueError:
        return []
    if not 0 <= age <= HR_SWITCH_DAYS:
        return []
    from backend.engine.plan_auto import hr_name
    a, b = hr_name(rec["from"]), hr_name(rec["model"])
    return [{"id": f"hr_zone_switch:{rec['since']}", "type": "hr_zone_switch", "pick": None,
             "title": _("課表心率區間自動換成{model}區間", model=b),
             "reason": _("{a} → {b}：{why}。", a=a, b=b, why=rec.get("reason") or ""),
             "help": _("課表的心率目標（週卡、間歇課、推到手錶的心率）從今天起用{model}區間；已推到手錶的課會重推一次"
                       "（自動推送關閉時，課表上會標「要重推」）。訓練負荷（Training load）和活動判讀照舊用 LTHR。"
                       "想固定用某一種區間，到設定 → 心率 → 課表心率區間選。", model=b)}]


SNOOZE = {"injury_done:": 7}            # SP-273 (injuries.DONE_SNOOZE_DAYS): 「還沒」 / ✕ hold this many days


def _snoozed(sid: str, d: dict, now: dt.datetime) -> bool:
    days = next((v for k, v in SNOOZE.items() if sid.startswith(k)), None)
    if days is None or d.get("action") == "accepted":
        return True
    try:
        return (now - dt.datetime.fromisoformat(d["at"])).days < days
    except (KeyError, TypeError, ValueError):
        return True


def snooze_key(sid: str) -> str:
    """The suggestion a 稍後提醒 holds, across weeks: `test:cp:<week>` / `baseline:cp:<week>` → `test:cp`,
    `b2b:<week>` → `b2b`; any other id is its own."""
    if sid.startswith(("test:", "baseline:")):
        parts = sid.split(":")
        return f"test:{parts[1]}" if len(parts) >= 2 else sid
    if sid.startswith("b2b:"):
        return "b2b"
    return sid


def test_kinds(row: dict) -> list[str]:
    """The tests a row suggests (cp / aet / tt30 / hrmax), for 「做完就不再提醒」: its kind (test, baseline), the
    tests to pick and the 安排課表 links' tests (zone_test, the SP-471 LTHR link). [] = not a test."""
    out = [row["kind"]] if row.get("type") in ("test", "baseline") and row.get("kind") else []
    out += [t.get("key") for t in row.get("tests") or [] if isinstance(t, dict)]
    out += [x.get("test") for x in row.get("links") or [] if isinstance(x, dict)]
    return sorted({k for k in out if k})


def tested(kinds, since: str, stored: list[dict], found: Optional[dict], measured: set) -> bool:
    """One of these tests was done on / after `since` (ISO day): a done test session of the plan (an AeT one
    is aet, any other is cp — as plan_sessions' scheduled()), a counted test the review found (`found`:
    plan_sessions._latest_tests, {cp|aet: {date, ok}}), or a measured threshold since (`measured`:
    threshold_confidence.tested_since — cp / tt30 / aet / hrmax)."""
    from backend.engine.aet_test import is_aet_session
    kinds = set(kinds or ())
    got = set(measured or ())
    for s in stored or []:
        if s.get("kind") == "test" and s.get("state") == "done" and (s.get("day") or "") >= since:
            got.add("aet" if is_aet_session(s) else "cp")
    for k, f in (found or {}).items():
        if isinstance(f, dict) and f.get("ok", True) and (f.get("date") or "")[:10] >= since:
            got.add(k)
    return bool(kinds & got)


def _snooze_holds(v: dict, now: dt.datetime, tested_fn) -> bool:
    """A 稍後提醒 still hides its row: before `until`, or after it when a test it suggests was done since."""
    if now.date().isoformat() < str(v.get("until") or ""):
        return True
    return bool(tested_fn and v.get("tests") and tested_fn(v["tests"], str(v.get("at") or "")[:10]))


DECLINE_KIND = ("test:", "baseline:")       # SP-515: 「不要」 on these holds the test (its kind), any week


def decline_key(sid: str) -> Optional[str]:
    """SP-515: the test a 「不要」 holds for good — `test:cp:<week>` / `baseline:cp:<week>` → `test:cp`; None for
    any other id (its 「不要」 holds that id, as before)."""
    return snooze_key(sid) if sid.startswith(DECLINE_KIND) else None


def _declined_keys(dis: dict) -> dict:
    """{decline_key: (id, record)} of the 「不要」 on tests (the latest one per test)."""
    out: dict = {}
    for k, v in (dis or {}).items():
        key = decline_key(k)
        if key and isinstance(v, dict) and v.get("action") == "declined":
            if key not in out or str(v.get("at") or "") > str(out[key][1].get("at") or ""):
                out[key] = (k, v)
    return out


def _hidden(r: dict, dis: dict, snoozes: list[dict], now: dt.datetime, tested_fn, no_keys=()) -> bool:
    d = dis.get(r["id"])
    if d is not None and d.get("action") != SNOOZED and _snoozed(r["id"], d, now):
        return True
    if no_keys and decline_key(r["id"]) in no_keys:                 # SP-515: 「不要」 on this test, any week
        return True
    k = snooze_key(r["id"])
    return any(v.get("key") == k and _snooze_holds(v, now, tested_fn) for v in snoozes)


def visible(rows: list[dict], dismissed: dict, now: Optional[dt.datetime] = None, tested_fn=None,
            snoozes: bool = True) -> list[dict]:
    """The rows not dismissed; a SNOOZE kind comes back once its days are over; a 稍後提醒 (SP-507) row once
    SNOOZE_DAYS are over, unless `tested_fn(kinds, since_day)` says a test it suggests was done since.
    `snoozes=False`: a 稍後提醒 does not hide its row — the 課表's 排入測試 ▸ (SP-507 R1: 「一樣要能排入測試」)."""
    now = now or dt.datetime.now()
    dis = dismissed or {}
    held = [v for v in dis.values() if isinstance(v, dict) and v.get("action") == SNOOZED] if snoozes else []
    no_keys = _declined_keys(dis)
    return [r for r in rows if not _hidden(r, dis, held, now, tested_fn, no_keys)]


def snoozed(rows: list[dict], vis: list[dict], dismissed: dict) -> list[dict]:
    """SP-507: the rows still computed but hidden by a 稍後提醒 — [{id, type, until}]. They are still the box's: the
    overview's 測試 keeps pointing there (SP-503's testOut does not count them as declined)."""
    shown = {r["id"] for r in vis}
    by_key = {v.get("key"): v for v in (dismissed or {}).values() if isinstance(v, dict) and v.get("action") == SNOOZED}
    out = []
    for r in rows:
        v = by_key.get(snooze_key(r["id"]))
        if r["id"] not in shown and v is not None and (dismissed or {}).get(r["id"], v).get("action") == SNOOZED:
            out.append({"id": r["id"], "type": r.get("type"), "until": v.get("until")})
    return out


def declined(rows: list[dict], vis: list[dict], dismissed: dict) -> list[dict]:
    """SP-503: the rows still computed but hidden by 「不要」 / ✕ — [{id, type, action}]. The overview's
    測試 card and 待辦 point to the box for a test (R15) and take it back when the box no longer has it."""
    shown = {r["id"] for r in vis}
    dis = dismissed or {}
    no_keys = _declined_keys(dis)
    out = []
    for r in rows:
        if r["id"] in shown:
            continue
        a = (dis.get(r["id"]) or {}).get("action")
        if a in ("declined", "dismissed"):
            out.append({"id": r["id"], "type": r.get("type"), "action": a})
        elif decline_key(r["id"]) in no_keys:                         # SP-515: 「不要」 on this test in another week
            out.append({"id": r["id"], "type": r.get("type"), "action": "declined"})
    return out


def declined_tests(dismissed: dict) -> list[dict]:
    """SP-515: the tests 「不要」 holds for good — [{key, kind, at}] (at: the day of the latest 「不要」), for the
    settings page's 「不再建議的測試」 and its 恢復 (restore)."""
    out = [{"key": key, "kind": key.split(":", 1)[1], "at": str(v.get("at") or "")[:10]}
           for key, (_, v) in _declined_keys(dismissed).items()]
    return sorted(out, key=lambda x: x["key"])


def restore(dismissed: dict, key: str) -> dict:
    """SP-515: 恢復 — drop every 「不要」 that holds the test `key` (decline_key); the test is suggested again."""
    return {k: v for k, v in (dismissed or {}).items()
            if not (decline_key(k) == key and isinstance(v, dict) and v.get("action") == "declined")}


def injury_done_rows(events: list[dict], marks: list[dict], today: str) -> list[dict]:
    """SP-273: 「右膝看起來好了？」 for each open injury injuries.done_check passes. `marks`: foot_log rows."""
    from backend.engine import injuries as INJ
    d = dt.date.fromisoformat(today)
    out = []
    for e in INJ.active_on(events, d):
        c = INJ.done_check(e, marks, d)
        if c is None:
            continue
        out.append({"id": f"injury_done:{e['id']}", "type": "injury_done", "pick": "confirm", "injury_id": e["id"],
                    "accept_label": _("好了"), "decline_label": _("還沒"), "date": today,
                    "title": _("{label}看起來好了？最近 {n} 次沒痛、跑量回到傷前 {pct}%", label=c["label"],
                               n=len(c["runs"]), pct=c["pct"]),
                    "reason": _("最近一週跑 {h:.1f} h，傷前 4 週平均每週 {pre:.1f} h；最近 {n} 次跑步（{a}–{b}，跨 {d} 天）"
                                "都標「沒痛」或「痠」。", h=c["recent_h"], pre=c["pre_h"], n=len(c["runs"]),
                                a=_md(c["runs"][0]), b=_md(c["runs"][-1]), d=c["span"]),
                    "help": _("按「好了」才會結案（好了的日期之後可以在傷病紀錄改）；結案後傷別要避開的課和疼痛燈號都解除，"
                              "同部位 {recur} 天內再痛會標成復發。按「還沒」{snooze} 天內不再問。回到傷前 75–80% 的量才恢復正常"
                              "訓練（Ohio State Wexner 回跑指引，臨床機構）；「{n} 次、跨 {span} 天」是推估。",
                              recur=INJ.RECUR_DAYS, snooze=INJ.DONE_SNOOZE_DAYS, n=INJ.DONE_RUNS,
                              span=INJ.DONE_SPAN_DAYS) + "\n" + _(INJ.DISCLAIMER)})
    return out


def prune(dismissed: dict, rows: list[dict], today: str) -> dict:
    """Dismissals whose suggestion is still computed (or a B2B / test of this week or later) stay;
    the rest go, so a later, new suggestion shows again."""
    ids = {r["id"] for r in rows}
    keys = {snooze_key(i) for i in ids}
    mon = _monday(today)
    keep = {}
    for k, v in (dismissed or {}).items():
        if v.get("action") == SNOOZED:
            # SP-507: until its day, and after it while the suggestion (any week of it) is still computed
            if str(v.get("until") or "") > today or v.get("key") in keys:
                keep[k] = v
            continue
        if v.get("action") == "declined" and decline_key(k):            # SP-515: until 恢復 (restore)
            keep[k] = v
            continue
        if k in ids or (v.get("week") and v["week"] >= mon) or _trip_ahead(k, today):
            keep[k] = v
    return keep


def _trip_start(sid: str) -> Optional[str]:
    """The trip's start (ISO day) of an altitude id (`altitude:<event>:<start>`, SP-546); None for any other id."""
    if not sid.startswith(ALT):
        return None
    start = sid.rsplit(":", 1)[-1]
    try:
        dt.date.fromisoformat(start)
    except ValueError:
        return None
    return start if len(start) == 10 else None


def _trip_ahead(sid: str, today: str) -> bool:
    """An altitude close (`altitude:<event>:<start>`, SP-546) is kept until its trip starts, even on a
    day the row is not computed (the activities could not be read), so it never comes back."""
    start = _trip_start(sid)
    return bool(start) and start >= today


def record(dismissed: dict, sid: str, action: str, now: dt.datetime, week: Optional[str] = None,
           tests: Optional[list] = None) -> dict:
    """`tests`: a 稍後提醒's test kinds (test_kinds of its row)."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    at = now.isoformat(timespec="seconds")
    if action == SNOOZED:
        until = (now.date() + dt.timedelta(days=SNOOZE_DAYS)).isoformat()
        start = _trip_start(sid)
        if start:       # SP-546 R1: an altitude reminder comes back the day before the trip (not 7 days later)
            until = max(dt.date.fromisoformat(start) - dt.timedelta(days=1), now.date() + dt.timedelta(days=1)).isoformat()
        return {**(dismissed or {}), sid: {"action": action, "at": at, "until": until, "key": snooze_key(sid),
                                           "tests": list(tests or [])}}
    return {**(dismissed or {}), sid: {"action": action, "at": at, **({"week": week} if week else {})}}
