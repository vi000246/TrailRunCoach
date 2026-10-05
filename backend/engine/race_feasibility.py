"""
賽事可行性 (SP-105): is the race too hard for the time left? With 賽事完備程度 (readiness(), below) in one card per race. docs/research/race-feasibility.md §3.

Only advice — nothing here changes the plan or the event. Each check gives a level
(ok < tight < over < late); the race's level is the worst one.

  weekly   the projected peak week's foot km and climb ÷ the race's hardest day (UA Big Vert:
           「weekly distance and vertical start at about 50 % of the event's largest single day
           and progress to about 90–100 % for longer events, > 100 % for shorter ones」).
           ok ≥ 90 % (a long event: multi-day or ≥ 6 h in all) / ≥ 100 % (shorter), over < 50 % (UA's starting point;
           a red line is 推估). The peak week = the last 4 full weeks' mean (or the last week if
           higher) growing +10 % a week (SP-89 decision 1) with every 4th week a recovery week
           (3:1, no growth), up to the week holding race − 21 days (Koop: no fitness gained in
           the last 2–3 weeks).
  hours    ultras only (a trail race of 超馬級 or bigger, planning.event_size — SP-111; the 100 km
           row from EP 100): Koop's minimum — 50 km / 50 mi: 6 h a week for
           ≥ 3 weeks in a row from 6 weeks out; 100 km / 100 mi: 9 h for ≥ 6 weeks from 9 weeks
           out (coach experience). Never worse than tight.
  cutoff   races: the predicted finish ÷ the cutoff (ok ≤ 90 %, tight ≤ 100 %, over above).
           百岳: the predicted time to the summit vs the turnaround (撤退時間, hours from that
           day's start): later = over (「不適合這座百岳」, the owner's rule 2026-10-05), < 30 min
           to spare = tight (推估).
  late     < 21 days to the race: late (the fitness window has closed — Koop).

The long day is shown, not graded (Koop: 20–80 % of the race; past ~6 h coaches stop the
long run — §1). C races are training days: not assessed.

賽事完備程度 — readiness(): what the training has ACTUALLY reached against the race (owner
2026-10-05: feasibility = will the training get there in time; readiness = is what was done
enough, how hard will the race feel). Shown after the feasibility in the same card per race;
the trends are charted in 圖表分析 → 專項期. Levels ok (準備好了) < tight (接近) < short (還差):

  long     the best single foot session of the last 6 weeks against the race's hardest day:
           trail / 百岳 by コース定数 (the target capped at 6 h of the race day — SP-106),
           road by km (the target ≤ 35 km — Pfitzinger's longest). ok ≥ 80 % (race_refs'
           band), tight ≥ 70 % (江晏慶「抓比賽距離爬升的七成」), short below (推估 as lines).
  weekly   the biggest actual week of the last 6 against the hardest day (UA, as above).
  hours    ultras: the longest run of weeks at Koop's hours in the last 9; never worse than tight.
  b2b      multi-day / ≥ 6 h events (b2b.qualifies): B2B weekends done in the last 10 weeks;
           ≥ 2 ok, else tight (推估).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Optional

from backend.i18n import N_, _

LEVELS = ("unknown", "ok", "tight", "over", "late")       # worst last
LEVEL_LABEL = {"unknown": N_("資料不足"), "ok": N_("來得及"), "tight": N_("有點趕"), "over": N_("太難了"),
               "late": N_("時間不夠")}   # plain words (owner 2026-10-05)

STEP = 1.10                  # SP-89 decision 1: ≤ +10 % a week
RECOVERY_EVERY = 4           # 3:1 — every 4th week a recovery week (no growth)
RECOVERY_SHARE = 0.65        # projection.py's recovery week (65 % of the 3 before)
BASE_WEEKS = 4
WINDOW_DAYS = 21             # Koop: the fitness window closes 2–3 weeks out
TAPER_WEEKS = 2              # planning.TAPER_DAYS
WEEK_OK_LONG = 0.90          # UA: 90–100 % for longer events
WEEK_OK_SHORT = 1.00         # UA: > 100 % for shorter events
WEEK_OVER = 0.50             # UA's starting point; as a red line 推估
LONG_DAY_H = 6.0             # planning.LONG_EVENT_HOURS: a longer / shorter event for UA's rule
CLIMB_MIN_M = 200.0          # 推估: a race day climbing less is judged on km only
CUTOFF_TIGHT = 0.90          # 推估: a finish within 10 % of the cutoff
SUMMIT_SPARE_H = 0.5         # 推估: < 30 min to spare at the summit
# Koop〈How Much Do You Need To Train〉: (race km ≥, hours a week, weeks in a row, from weeks out)
KOOP = ((100.0, 9.0, 6, 9), (50.0, 6.0, 3, 6))
SRC_UA = N_("週量：Uphill Athlete〈Big Vert Ultra Marathon〉——每週的距離和爬升，從比賽最難那天的一半開始，長的比賽練到 90–100 %")
SRC_KOOP = N_("超馬週時數：Jason Koop——50 km 賽前 6 週起每週 6 小時、連續 3 週；100 km 賽前 9 週起每週 9 小時、連續 6 週")


def _worse(a: str, b: str) -> str:
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def monday_of(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def weekly_history(ds, today: dt.date, weeks: int = BASE_WEEKS) -> list[dict]:
    """The last `weeks` full Mon–Sun weeks before this one, oldest first: foot km, climb m,
    moving hours (road / trail / hike — overview.FOOT)."""
    from backend.engine import overview as O
    mon = monday_of(today)
    out = []
    for i in range(weeks, 0, -1):
        a = mon - dt.timedelta(weeks=i)
        ws = [w for w in O.workouts_between(ds, a, a + dt.timedelta(days=7)) if O.category(w) in O.FOOT]
        out.append({"monday": a.isoformat(),
                    "km": sum(O._n(w.metrics.get("distance")) or 0.0 for w in ws),
                    "climb_m": sum(O._n(w.metrics.get("climbing")) or 0.0 for w in ws),
                    "hours": sum(O.moving_s(w) for w in ws) / 3600.0})
    return out


def base_week(hist: list[dict]) -> dict:
    """max(the weeks' mean, the last week) per measure — projection.py's base for the +10 %."""
    if not hist:
        return {"km": 0.0, "climb_m": 0.0, "hours": 0.0}
    n = len(hist)
    return {k: max(sum(h[k] for h in hist) / n, hist[-1][k]) for k in ("km", "climb_m", "hours")}


def week_ok_at(e, line: dict) -> float:
    """UA's 「longer event」 (90 %) vs 「shorter」 (> 100 %) is the EVENT's length: a multi-day trip or a
    race of ≥ 6 h in all (a 3-day 百岳 with ~5 h days is a long event, not a short one)."""
    long_event = (e.days or 1) > 1 or float(line.get("hours") or 0.0) >= LONG_DAY_H
    return WEEK_OK_LONG if long_event else WEEK_OK_SHORT


def split_note(line: dict) -> Optional[str]:
    """A multi-day trip split equally (no per-day numbers, no GPX day ends — an event saved before
    SP-114 made them required): its hardest day is likely underestimated."""
    if line.get("multi") and line.get("split_source") == "equal":
        return day_plan_hint(line["days"])
    return None


def day_plan_hint(days: int) -> str:
    """SP-114: the hint for an old multi-day event without its per-day numbers (not blocked)."""
    return _("請補每天的距離和爬升：現在 {n} 天是平均分配，最硬的那天（攻頂日、最難的一站）可能被低估。"
             "在賽季計畫按「編輯」填每一天，或上傳 GPX 並標好每天的終點", n=days)


def hardest_day(line: dict) -> dict:
    """The race day with the highest course constant (UA's 「largest single day」), not the mean."""
    days = line.get("per_day") or []
    d = max(days, key=lambda x: x["cc"]) if days else line
    return {"day": d.get("day", 1), "km": float(d["km"]), "climb_m": float(d["climb_m"]),
            "hours": float(d["hours"]), "cc": float(d["cc"])}


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------

def weeks_ahead(today: dt.date, race: dt.date) -> list[dict]:
    """The weeks from next Monday to the race week: {monday, build (grows), recovery, in_window}."""
    mon, out, builds = monday_of(today), [], 0
    peak = monday_of(race - dt.timedelta(days=WINDOW_DAYS))
    i = 1
    while mon + dt.timedelta(weeks=i) <= monday_of(race):
        m = mon + dt.timedelta(weeks=i)
        rec = i % RECOVERY_EVERY == 0
        if not rec and m <= peak:
            builds += 1
        out.append({"monday": m, "builds": builds, "recovery": rec, "in_window": m <= peak})
        i += 1
    return out


def peak_week(base: dict, weeks: list[dict]) -> dict:
    """The projected peak (last in-window build week): base × 1.1^builds."""
    n = max((w["builds"] for w in weeks if w["in_window"]), default=0)
    return {**{k: base[k] * STEP ** n for k in ("km", "climb_m", "hours")}, "builds": n}


def koop_need(line: dict, e) -> Optional[tuple]:
    """Koop's row for a 超馬級 or bigger trail race (planning.event_size on the line's predicted
    hours — SP-111, not the horizontal km): the 100 km / 100 mi row from EP 100 or the 100 英里級,
    else the 50 km / 50 mi row (推估: Koop names distances). 百岳 is left out (SP-111: decide later)."""
    if e.kind not in ("race", "other"):
        return None
    from backend.engine import planning as P
    size = P.event_size(e, hours=line.get("hours") if int(line.get("days") or 1) == 1 else None)
    if size < P.ULTRA:
        return None
    ep = P.event_ep(e)
    if ep is None:
        ep = float(line.get("km") or 0.0) + float(line.get("climb_m") or 0.0) / P.EP_DIVISOR
    return KOOP[0] if size >= P.HUNDRED or ep >= KOOP[0][0] else KOOP[1]


def koop_run(base_h: float, weeks: list[dict], race: dt.date, need: tuple) -> dict:
    """Weeks in Koop's window (from `from_wk` weeks out to the taper) at ≥ the hours, and the
    longest run of them in a row; recovery weeks at 65 % break a run."""
    _km, hours, in_row, from_wk = need
    lo = monday_of(race) - dt.timedelta(weeks=from_wk)
    hi = monday_of(race) - dt.timedelta(weeks=TAPER_WEEKS)
    best = run = 0
    peak_h = 0.0
    for w in weeks:
        h = base_h * STEP ** w["builds"] * (RECOVERY_SHARE if w["recovery"] else 1.0)
        if w["in_window"]:
            peak_h = max(peak_h, h)
        if lo <= w["monday"] < hi:
            run = run + 1 if h >= hours else 0
            best = max(best, run)
    return {"need_h": hours, "need_weeks": in_row, "from_weeks": from_wk, "best_run": best, "peak_h": peak_h}


# ---------------------------------------------------------------------------
# the summit (百岳)
# ---------------------------------------------------------------------------

def gpx_summit_km(track) -> Optional[float]:
    """km of the highest point of the event's GPX."""
    from backend.engine.racepower import course as CO
    td = CO.track_distance(track)
    z = [x for x in td["z"]]
    if not len(z):
        return None
    i = max(range(len(z)), key=lambda k: -math.inf if z[k] is None or not math.isfinite(z[k]) else z[k])
    return float(td["d"][i]) / 1000.0


def summit_eta(course: dict, hours: list[float], summit_km: float, pieces: Optional[list[dict]] = None) -> Optional[dict]:
    """{day, hours}: the time from that day's start to `summit_km` (km along the whole trip) = the
    day's time × the share of its effort km (km + climb / 100) done by then. `pieces` (the GPX's
    1 km pieces with their day) give the climb before the summit; without them the whole day's
    climb is counted before it (推估: the summit is the day's high point)."""
    days = course["days"]
    if not days or len(hours) != len(days):
        return None
    start = 0.0
    for d, h in zip(days, hours):
        end = start + float(d["km"])
        if summit_km <= end + 1e-6 or d is days[-1]:
            km_in = max(0.0, min(summit_km, end) - start)
            if pieces:
                gain = 0.0
                for p in pieces:
                    if p["day"] != d["day"] or p["start_km"] >= summit_km:
                        continue
                    f = min(1.0, (summit_km - p["start_km"]) / max(1e-9, p["end_km"] - p["start_km"]))
                    gain += p["gain_m"] * f
            else:
                gain = float(d["gain_m"])
            total = float(d["km"]) + float(d["gain_m"]) / 100.0
            share = (km_in + gain / 100.0) / total if total > 0 else 1.0
            return {"day": d["day"], "hours": float(h) * min(1.0, share), "assumed_climb": not pieces}
        start = end
    return None


# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------

def assess(e, line: Optional[dict], today: dt.date, hist: list[dict], summit: Optional[dict] = None) -> dict:
    """The verdict for event `e` (planning.Event) with its race line (race_refs.race_line) and
    the last weeks (weekly_history). `summit`: summit_eta()'s result for a 百岳 with a summit."""
    out = {"event_id": e.id, "name": e.name, "date": e.date, "priority": e.priority, "kind": e.kind,
           "days": int(e.days or 1),
           "days_to": (e.start - today).days, "checks": [], "suggestions": [], "src": [_(SRC_UA)]}
    if e.priority == "C":
        out.update(level="ok", label=_(LEVEL_LABEL["ok"]), skipped=_("C 賽當練習，不評估"))
        return out
    level = "unknown"
    days_to = out["days_to"]

    def check(cid: str, lv: str, text: str, **kw) -> None:
        nonlocal level
        out["checks"].append({"id": cid, "level": lv, "label": _(LEVEL_LABEL[lv]), "text": text, **kw})
        if lv != "unknown":
            level = lv if level == "unknown" else _worse(level, lv)

    if days_to < WINDOW_DAYS:
        check("late", "late", _("只剩 {n} 天：最後 3 週練不出新的體能了，只能減量休息", n=days_to))
        out["suggestions"].append(_("建議改成 B 或 C 賽，用現在的體能去跑就好，不要臨時猛加量") if e.priority == "A"
                                  else _("用現在的體能去跑就好，不要臨時猛加量"))
    if line is None:
        out["checks"].append({"id": "weekly", "level": "unknown", "label": _(LEVEL_LABEL["unknown"]),
                              "text": _("這場比賽沒填距離或預估時間，沒辦法估")})
    else:
        hd = hardest_day(line)
        base = base_week(hist)
        weeks = weeks_ahead(today, e.start)
        pk = peak_week(base, weeks)
        out["race_day"] = hd
        if split_note(line):
            out["split_note"] = split_note(line)
        out["base_week"] = {k: round(v, 1) for k, v in base.items()}
        out["peak_week"] = {k: round(v, 1) for k, v in pk.items()}
        if base["km"] <= 0:
            check("weekly", "unknown", _("最近 {n} 週沒有跑步或健行紀錄，沒辦法推算", n=BASE_WEEKS))
        else:
            r_km = pk["km"] / hd["km"] if hd["km"] else None
            r_cl = pk["climb_m"] / hd["climb_m"] if hd["climb_m"] >= CLIMB_MIN_M else None
            ratio = min(x for x in (r_km, r_cl) if x is not None) if (r_km or r_cl) else None
            ok_at = week_ok_at(e, line)
            if ratio is not None:
                lv = "ok" if ratio >= ok_at else "over" if ratio < WEEK_OVER else "tight"
                # a race day under CLIMB_MIN_M (路跑) is judged on km only: no climb in the text either
                txt = (_("照現在每週慢慢加量，賽前你一週最多大約練到 {km:.0f} km、爬升 {cl:.0f} m，是比賽最難那天的 {p:.0f} %",
                         km=pk["km"], cl=pk["climb_m"], p=ratio * 100) if r_cl is not None else
                       _("照現在每週慢慢加量，賽前你一週最多大約跑到 {km:.0f} km，是比賽距離的 {p:.0f} %",
                         km=pk["km"], p=ratio * 100))
                txt += _("（最好到 {a:.0f} %，不到 {b:.0f} % 就太少）", a=ok_at * 100, b=WEEK_OVER * 100)
                check("weekly", lv, txt, ratio=round(ratio, 3), ok_at=ok_at, over_below=WEEK_OVER,
                      ratio_km=None if r_km is None else round(r_km, 3),
                      ratio_climb=None if r_cl is None else round(r_cl, 3))
                if lv == "over":
                    s = ratio / ok_at
                    out["downgrade"] = {"km": round(hd["km"] * s), "climb_m": round(hd["climb_m"] * s / 10) * 10}
                    out["suggestions"].append(_("建議報短一點的組別：照推算，你大約應付得了一天 {km} km、爬升 {cl} m 的比賽",
                                                km=out["downgrade"]["km"], cl=out["downgrade"]["climb_m"]))
                    out["suggestions"].append(_("或換一場晚一點的比賽，或這場先不跑"))
                elif lv == "tight" and days_to >= WINDOW_DAYS:     # late already says 「照現有體能跑」
                    out["suggestions"].append(_("目標設保守一點，前半段放慢"))
            need = koop_need(line, e)
            if need:
                kr = koop_run(base["hours"], weeks, e.start, need)
                out["koop"] = {k: round(v, 1) if isinstance(v, float) else v for k, v in kr.items()}
                out["src"].append(_(SRC_KOOP))
                txt = _("超馬建議賽前 {w} 週開始，每週練 {h:g} 小時、連續 {n} 週；照推算最多能連續做到 {b} 週（一週最多約 {p:.1f} 小時）",
                        w=need[3], h=need[1], n=need[2], b=kr["best_run"], p=kr["peak_h"])
                check("hours", "ok" if kr["best_run"] >= need[2] else "tight", txt)
        # the long day: shown, not graded
        cut = getattr(e, "cutoff_hours", None)
        if cut and e.kind == "baiyue":
            if summit is None:
                check("cutoff", "unknown", _("有填撤退時間，但不知道山頂在哪：上傳 GPX，或填「山頂在第幾 km」"))
            else:
                eta = summit["hours"]
                spare = cut - eta
                lv = "ok" if spare >= SUMMIT_SPARE_H else "tight" if spare >= 0 else "over"
                txt = _("照現在的體能，預估第 {d} 天出發後 {eta:.1f} 小時到山頂；撤退時間是 {c:g} 小時", d=summit["day"], eta=eta, c=cut)
                if summit.get("assumed_climb"):
                    txt += _("（沒有 GPX，所以假設當天的爬升都在登頂前）")
                check("cutoff", lv, txt, eta_h=round(eta, 2), cutoff_h=cut)
                if lv == "over":
                    out["suggestions"].insert(0, _("預估還沒到山頂就得撤退：這座百岳可能還不適合現在的你。可以換短一點的路線、多排一天，或先不去"))
        elif cut:
            fin = float(line["hours"])
            r = fin / cut
            lv = "ok" if r <= CUTOFF_TIGHT else "tight" if r <= 1.0 else "over"
            check("cutoff", lv, _("照現在的體能，預估 {f:.1f} 小時完賽；關門是 {c:g} 小時",
                                  f=fin, c=cut),
                  finish_h=round(fin, 2), cutoff_h=cut)
            if lv == "over":
                out["suggestions"].insert(0, _("預估會超過關門時間：建議報短一點的組別，或這場先不跑"))
    out["level"] = level
    out["label"] = _(LEVEL_LABEL[level])
    # one suggestion per text, the hard ones first
    seen, sugg = set(), []
    for s in out["suggestions"]:
        if s not in seen:
            seen.add(s)
            sugg.append(s)
    out["suggestions"] = sugg
    return out


# ---------------------------------------------------------------------------
# 賽事完備程度 (readiness): what was actually done
# ---------------------------------------------------------------------------

READY_LEVELS = ("unknown", "ok", "tight", "short")       # worst last
READY_LABEL = {"unknown": N_("資料不足"), "ok": N_("準備好了"), "tight": N_("差一點"), "short": N_("還不夠")}
READY_DAYS = 42              # the long day / the weeks looked at: the last 6 weeks
READY_WEEKS = 6
KOOP_WEEKS = 9               # Koop's longest window (100 km: from 9 weeks out)
B2B_WEEKS = 10               # 推估: a 專項期's worth of B2B weekends
B2B_OK = 2                   # 推估
LONG_OK = 0.80               # race_refs.BAND_LO: the chart's 80–100 % band
LONG_TIGHT = 0.70            # 江晏慶「抓比賽距離爬升的七成」
LONG_CAP_H = 6.0             # specific_phase.TRAIL_LONG_MAX_MIN (SP-106)
ROAD_LONG_KM = 35.0          # specific_phase.ROAD_LONG_MAX_KM (Pfitzinger)
SPECIFIC_DAYS = 70           # 專項期 = 賽前 10–3 週: before it readiness is still growing
SRC_LONG = N_("長天：練到比賽最難那天的 80 % 以上算夠（圖表「每次路線難度」的目標帶）；江晏慶「抓比賽距離和爬升的七成」；比賽超過 6 小時，長天練到 6 小時的量就夠")


def activity_rows(ds, today: dt.date, days: int = READY_DAYS) -> list[dict]:
    """The endurance activities of the last `days`: {date, km, climb_m, descent_m, hours, minutes, idx, foot}."""
    from backend.engine import overview as O
    out = []
    for w in O.workouts_between(ds, today - dt.timedelta(days=days), today + dt.timedelta(days=1)):
        c = O.category(w)
        if c not in O.ENDURANCE:
            continue
        m = w.metrics
        out.append({"date": O.wdate(w), "km": O._n(m.get("distance")) or 0.0, "climb_m": O._n(m.get("climbing")) or 0.0,
                    "descent_m": O._n(m.get("descending")) or 0.0, "hours": O.moving_s(w) / 3600.0,
                    "minutes": O.moving_s(w) / 60.0, "idx": w.idx, "foot": c in O.FOOT})
    return out


def _worse_r(a: str, b: str) -> str:
    return a if READY_LEVELS.index(a) >= READY_LEVELS.index(b) else b


def readiness(e, line: Optional[dict], today: dt.date, hist: list[dict], acts: list[dict]) -> dict:
    """賽事完備程度 of event `e`: `hist` = weekly_history (≥ KOOP_WEEKS weeks, oldest first), `acts` =
    activity_rows of the last READY_DAYS (B2B: the endurance ones of the last B2B_WEEKS)."""
    from backend.engine import b2b as B2B
    from backend.engine.algorithms.chart_metrics import course_constant
    out = {"checks": [], "src": [_(SRC_LONG), _(SRC_UA)]}
    level = "unknown"

    def check(cid: str, lv: str, text: str, **kw) -> None:
        nonlocal level
        out["checks"].append({"id": cid, "level": lv, "label": _(READY_LABEL[lv]), "text": text, **kw})
        if lv != "unknown":
            level = lv if level == "unknown" else _worse_r(level, lv)

    days_to = (e.start - today).days
    if days_to > SPECIFIC_DAYS:
        out["note"] = _("還沒到賽前 10 週的專項期：下面的數字會隨著長天和週量慢慢長上來")
    if line is None:
        check("long", "unknown", _("這場比賽沒填距離或預估時間，沒辦法估"))
        out.update(level=level, label=_(READY_LABEL[level]))
        return out
    hd = hardest_day(line)
    since = today - dt.timedelta(days=READY_DAYS)
    foot = [a for a in acts if a["foot"] and a["date"] >= since]
    road = e.kind == "road" or hd["climb_m"] < CLIMB_MIN_M
    # 1. the long day
    if not foot:
        check("long", "unknown", _("最近 6 週沒有跑步或健行紀錄"))
    elif road:
        want = min(hd["km"], ROAD_LONG_KM)
        best = max(a["km"] for a in foot)
        r = best / want if want else 0.0
        lv = "ok" if r >= LONG_OK else "tight" if r >= LONG_TIGHT else "short"
        check("long", lv, _("最近 6 週最長跑了 {b:.1f} km，是長跑目標 {w:.0f} km 的 {p:.0f} %", b=best, w=want, p=r * 100),
              ratio=round(r, 3))
    else:
        cap = min(1.0, LONG_CAP_H / hd["hours"]) if hd["hours"] else 1.0
        want = hd["cc"] * cap
        best_a = max(foot, key=lambda a: course_constant(a["hours"], a["km"], a["climb_m"], a["descent_m"]))
        best = course_constant(best_a["hours"], best_a["km"], best_a["climb_m"], best_a["descent_m"])
        r = best / want if want else 0.0
        lv = "ok" if r >= LONG_OK else "tight" if r >= LONG_TIGHT else "short"
        txt = _("最近 6 週最硬的一次（{d}，{h:.1f} 小時）難度是比賽最難那天的 {p:.0f} %",
                d=best_a["date"].strftime("%m/%d"), h=best_a["hours"], p=best / hd["cc"] * 100 if hd["cc"] else 0)
        if cap < 1:
            txt += _("；比賽超過 6 小時，長天練到 6 小時的量就夠，等於 {p:.0f} %", p=r * 100)
        check("long", lv, txt, ratio=round(r, 3))
    # 2. the biggest week
    weeks = hist[-READY_WEEKS:]
    if weeks and any(w["km"] > 0 for w in weeks):
        ok_at = week_ok_at(e, line)
        rk = max(w["km"] for w in weeks) / hd["km"] if hd["km"] else None
        rc = max(w["climb_m"] for w in weeks) / hd["climb_m"] if not road else None
        r = min(x for x in (rk, rc) if x is not None) if (rk is not None or rc is not None) else None
        if r is not None:
            lv = "ok" if r >= ok_at else "tight" if r >= WEEK_OVER else "short"
            txt = (_("最近 6 週練最多的一週：{km:.0f} km、爬升 {cl:.0f} m，是比賽最難那天的 {p:.0f} %（最好到 {a:.0f} %）",
                     km=max(w["km"] for w in weeks), cl=max(w["climb_m"] for w in weeks), p=r * 100, a=ok_at * 100)
                   if rc is not None else
                   _("最近 6 週跑最多的一週：{km:.0f} km，是比賽距離的 {p:.0f} %（最好到 {a:.0f} %）",
                     km=max(w["km"] for w in weeks), p=r * 100, a=ok_at * 100))
            check("weekly", lv, txt, ratio=round(r, 3), ok_at=ok_at)
    # 3. Koop's weekly hours (ultras)
    need = koop_need(line, e)
    if need:
        run = best = 0
        for w in hist[-KOOP_WEEKS:]:
            run = run + 1 if w["hours"] >= need[1] else 0
            best = max(best, run)
        check("hours", "ok" if best >= need[2] else "tight",
              _("超馬建議連續 {k} 週、每週練 {h:g} 小時；最近 {n} 週你連續做到 {b} 週", n=KOOP_WEEKS, b=best, h=need[1], k=need[2]))
        out["src"].append(_(SRC_KOOP))
    # 4. B2B (multi-day / ≥ 6 h events)
    if B2B.qualifies(e) and e.kind != "road":
        lo = today - dt.timedelta(weeks=B2B_WEEKS)
        rows = [(a["date"], a["minutes"], a["idx"]) for a in acts if a["date"] >= lo]
        n = len(B2B.detect(rows))
        check("b2b", "ok" if n >= B2B_OK else "tight",
              _("最近 {w} 週做過 {n} 次連續兩天的長距離（B2B）", w=B2B_WEEKS, n=n), count=n)
    out.update(level=level, label=_(READY_LABEL[level]))
    return out


# ---------------------------------------------------------------------------
# the races (API)
# ---------------------------------------------------------------------------

def event_summit(e, course: dict, hours: Optional[list[float]]) -> Optional[dict]:
    """summit_eta() for a 百岳 with a cutoff: the GPX's highest point (or e.summit_km)."""
    if e.kind != "baiyue" or not getattr(e, "cutoff_hours", None) or not hours:
        return None
    from backend.engine import event_gpx as EG
    pieces = skm = None
    try:
        got = EG.track(e.id)
    except EG.EventGpxError:
        got = None
    if got is not None:
        from backend.engine.racepower import course as CO
        tr, row = got
        try:
            pieces = CO.cut_at(EG._course(tr, "km")["segments"], EG.splits_for(row, max(1, int(e.days or 1))))
            skm = gpx_summit_km(tr)
        except Exception:                   # noqa: BLE001 — a broken file: the event's own number
            pieces = skm = None
    if getattr(e, "summit_km", None) is not None:
        skm = float(e.summit_km)
    if skm is None:
        return None
    return summit_eta(course, hours, skm, pieces)


def races(plan, ds, today: dt.date, event_id: Optional[str] = None,
          predict: Optional[Callable] = None, gpx: Optional[Callable] = None) -> list[dict]:
    """The verdicts of the upcoming A / B races (or the one `event_id`, any grade)."""
    from backend.engine.panels import race_refs as RR
    predict = predict or RR.calculator_hours
    gpx = gpx or RR.stored_course
    evs = [e for e in plan.events if e.end >= today]
    evs = [e for e in evs if e.id == event_id] if event_id else [e for e in evs if e.priority in ("A", "B")]
    hist = weekly_history(ds, today, KOOP_WEEKS)
    acts = activity_rows(ds, today, B2B_WEEKS * 7)
    out = []
    for e in sorted(evs, key=lambda x: x.start):
        line = summit = None
        if e.distance_km:
            course = RR.course_of(e, gpx)
            hs = predict(e, course)
            line = RR.race_line(e, hs, _("賽事計算器預測的完賽時間"), course)
            if line is not None:
                summit = event_summit(e, course, [d["hours"] for d in line["per_day"]])
        r = assess(e, line, today, hist[-BASE_WEEKS:], summit)
        if not r.get("skipped"):
            r["readiness"] = readiness(e, line, today, hist, acts)
        out.append(r)
    return out
