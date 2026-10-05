"""
賽事可行性 (SP-105): is the race too hard for the time left? docs/research/race-feasibility.md §3.

Only advice — nothing here changes the plan or the event. Each check gives a level
(ok < tight < over < late); the race's level is the worst one.

  weekly   the projected peak week's foot km and climb ÷ the race's hardest day (UA Big Vert:
           「weekly distance and vertical start at about 50 % of the event's largest single day
           and progress to about 90–100 % for longer events, > 100 % for shorter ones」).
           ok ≥ 90 % (hardest day ≥ 6 h) / ≥ 100 % (shorter), over < 50 % (UA's starting point;
           a red line is 推估). The peak week = the last 4 full weeks' mean (or the last week if
           higher) growing +10 % a week (SP-89 decision 1) with every 4th week a recovery week
           (3:1, no growth), up to the week holding race − 21 days (Koop: no fitness gained in
           the last 2–3 weeks).
  hours    ultras only (trail race ≥ 50 km): Koop's minimum — 50 km / 50 mi: 6 h a week for
           ≥ 3 weeks in a row from 6 weeks out; 100 km / 100 mi: 9 h for ≥ 6 weeks from 9 weeks
           out (coach experience). Never worse than tight.
  cutoff   races: the predicted finish ÷ the cutoff (ok ≤ 90 %, tight ≤ 100 %, over above).
           百岳: the predicted time to the summit vs the turnaround (撤退時間, hours from that
           day's start): later = over (「不適合這座百岳」, the owner's rule 2026-10-05), < 30 min
           to spare = tight (推估).
  late     < 21 days to the race: late (the fitness window has closed — Koop).

The long day is shown, not graded (Koop: 20–80 % of the race; past ~6 h coaches stop the
long run — §1). C races are training days: not assessed.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Optional

from backend.i18n import N_, _

LEVELS = ("unknown", "ok", "tight", "over", "late")       # worst last
LEVEL_LABEL = {"unknown": N_("資料不足"), "ok": N_("可行"), "tight": N_("吃力"), "over": N_("超出"),
               "late": N_("來不及")}

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
SRC_UA = N_("Uphill Athlete〈Big Vert Ultra Marathon〉：每週距離和爬升從賽事最大單日的 50 % 開始，長的賽事練到 90–100 %")
SRC_KOOP = N_("Koop〈How Much Do You Need To Train〉：50 km 賽前 6 週起每週 6 小時、連續 3 週；100 km 賽前 9 週起每週 9 小時、連續 6 週")


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


def koop_need(line: dict, kind: str) -> Optional[tuple]:
    if kind not in ("race", "other"):
        return None
    km = float(line.get("km") or 0.0)
    return next((k for k in KOOP if km >= k[0]), None)


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
           "days_to": (e.start - today).days, "checks": [], "suggestions": [], "src": [_(SRC_UA)]}
    if e.priority == "C":
        out.update(level="ok", label=_(LEVEL_LABEL["ok"]), skipped=_("C 賽當訓練，不判定"))
        return out
    level = "unknown"
    days_to = out["days_to"]

    def check(cid: str, lv: str, text: str, **kw) -> None:
        nonlocal level
        out["checks"].append({"id": cid, "level": lv, "label": _(LEVEL_LABEL[lv]), "text": text, **kw})
        if lv != "unknown":
            level = lv if level == "unknown" else _worse(level, lv)

    if days_to < WINDOW_DAYS:
        check("late", "late", _("剩 {n} 天：賽前 3 週內練不出體能，只剩減量（Koop）", n=days_to))
        out["suggestions"].append(_("建議改成 B 或 C 賽，照現有體能跑；不要為它硬加量") if e.priority == "A"
                                  else _("照現有體能跑，不要為它硬加量"))
    if line is None:
        out["checks"].append({"id": "weekly", "level": "unknown", "label": _(LEVEL_LABEL["unknown"]),
                              "text": _("賽事沒有距離或預估時間，算不出比賽的需求")})
    else:
        hd = hardest_day(line)
        base = base_week(hist)
        weeks = weeks_ahead(today, e.start)
        pk = peak_week(base, weeks)
        out["race_day"] = hd
        out["base_week"] = {k: round(v, 1) for k, v in base.items()}
        out["peak_week"] = {k: round(v, 1) for k, v in pk.items()}
        if base["km"] <= 0:
            check("weekly", "unknown", _("近 {n} 週沒有跑步或健行紀錄，算不出週量", n=BASE_WEEKS))
        else:
            r_km = pk["km"] / hd["km"] if hd["km"] else None
            r_cl = pk["climb_m"] / hd["climb_m"] if hd["climb_m"] >= CLIMB_MIN_M else None
            ratio = min(x for x in (r_km, r_cl) if x is not None) if (r_km or r_cl) else None
            ok_at = WEEK_OK_LONG if hd["hours"] >= LONG_DAY_H else WEEK_OK_SHORT
            if ratio is not None:
                lv = "ok" if ratio >= ok_at else "over" if ratio < WEEK_OVER else "tight"
                txt = _("高峰週預估 {km:.0f} km、爬升 {cl:.0f} m（+10 %／週推到賽前第 3 週）＝比賽最難那天的 {p:.0f} %",
                        km=pk["km"], cl=pk["climb_m"], p=ratio * 100)
                txt += _("；UA：這個長度要練到 {a:.0f} %，起點 {b:.0f} %", a=ok_at * 100, b=WEEK_OVER * 100)
                check("weekly", lv, txt, ratio=round(ratio, 3), ok_at=ok_at, over_below=WEEK_OVER,
                      ratio_km=None if r_km is None else round(r_km, 3),
                      ratio_climb=None if r_cl is None else round(r_cl, 3))
                if lv == "over":
                    s = ratio / ok_at
                    out["downgrade"] = {"km": round(hd["km"] * s), "climb_m": round(hd["climb_m"] * s / 10) * 10}
                    out["suggestions"].append(_("降組別：以預估的高峰週，大約撐得起單日 {km} km、爬升 {cl} m 的賽事（同樣的爬升密度，推估）",
                                                km=out["downgrade"]["km"], cl=out["downgrade"]["climb_m"]))
                    out["suggestions"].append(_("或換一場更晚的比賽，或放棄這場"))
                elif lv == "tight" and days_to >= WINDOW_DAYS:     # late already says 「照現有體能跑」
                    out["suggestions"].append(_("照現有體能設定目標、保守配速"))
            need = koop_need(line, e.kind)
            if need:
                kr = koop_run(base["hours"], weeks, e.start, need)
                out["koop"] = {k: round(v, 1) if isinstance(v, float) else v for k, v in kr.items()}
                out["src"].append(_(SRC_KOOP))
                txt = _("Koop 最低量：賽前 {w} 週起每週 {h:g} 小時、連續 {n} 週；預估最多連續 {b} 週達到（高峰約 {p:.1f} 小時）",
                        w=need[3], h=need[1], n=need[2], b=kr["best_run"], p=kr["peak_h"])
                check("hours", "ok" if kr["best_run"] >= need[2] else "tight", txt)
        # the long day: shown, not graded
        out["long_day_note"] = _("長天只當參考：Koop 帶過的完賽者最長長跑佔比賽 20–80 %；比賽超過約 6 小時，長天停在 4–6 小時")
        cut = getattr(e, "cutoff_hours", None)
        if cut and e.kind == "baiyue":
            if summit is None:
                check("cutoff", "unknown", _("有撤退時間，但不知道山頂在哪：上傳 GPX，或填「山頂在第幾公里」"))
            else:
                eta = summit["hours"]
                spare = cut - eta
                lv = "ok" if spare >= SUMMIT_SPARE_H else "tight" if spare >= 0 else "over"
                txt = _("預估第 {d} 天出發後 {eta:.1f} 小時到山頂，撤退時間是出發後 {c:g} 小時", d=summit["day"], eta=eta, c=cut)
                if summit.get("assumed_climb"):
                    txt += _("（沒有 GPX：假設當天的爬升都在山頂之前，推估）")
                check("cutoff", lv, txt, eta_h=round(eta, 2), cutoff_h=cut)
                if lv == "over":
                    out["suggestions"].insert(0, _("預估到不了山頂就得撤退：不適合這座百岳。換短一點的路線、多排一天，或放棄"))
        elif cut:
            fin = float(line["hours"])
            r = fin / cut
            lv = "ok" if r <= CUTOFF_TIGHT else "tight" if r <= 1.0 else "over"
            check("cutoff", lv, _("預測完賽 {f:.1f} 小時（{src}），關門 {c:g} 小時＝{p:.0f} %",
                                  f=fin, src=line.get("time_source") or "", c=cut, p=r * 100),
                  finish_h=round(fin, 2), cutoff_h=cut)
            if lv == "over":
                out["suggestions"].insert(0, _("預測完賽超過關門時間：建議降組別或放棄"))
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
    hist = weekly_history(ds, today)
    out = []
    for e in sorted(evs, key=lambda x: x.start):
        line = summit = None
        if e.distance_km:
            course = RR.course_of(e, gpx)
            hs = predict(e, course)
            line = RR.race_line(e, hs, _("賽事計算器預測的完賽時間"), course)
            if line is not None:
                summit = event_summit(e, course, [d["hours"] for d in line["per_day"]])
        out.append(assess(e, line, today, hist, summit))
    return out
