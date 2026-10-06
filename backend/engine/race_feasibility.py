"""
賽事可行性 (SP-105): is the race too hard for the time left? With 賽事完備程度 (readiness(), below) in one card per race. docs/research/race-feasibility.md §3.

Only advice — nothing here changes the plan or the event. Each check gives a level
(ok < tight < over < late); the race's level is the worst one.

  weekly   the projected peak week's EP (km + climb ÷ the personal divisor, planning.event_ep's —
           SP-112) ÷ the race's hardest day's EP (UA Big Vert: 「weekly distance and vertical start
           at about 50 % of the event's largest single day and progress to about 90–100 % for
           longer events, > 100 % for shorter ones」; ITRA / UTMB grade races by the same EP). A road
           race (a day climbing < CLIMB_MIN_M) by km. ok ≥ 90 % (a long event: multi-day or ≥ 6 h
           in all) / ≥ 100 % (shorter), else tight — never over (SP-112: the weekly volume does not
           tell finishers from non-finishers — Hoffman & Fogard 2011 134 vs 127 km, Belinchón 2019,
           Corrion 2018, Maleka 2026; a low volume slows the predicted time, which the cutoff check
           reads). The peak week = the last 4 full weeks' mean (or the last week if
           higher) growing +10 % a week (SP-89 decision 1) with every 4th week a recovery week
           (3:1, no growth), up to the week holding race − 21 days (Koop: no fitness gained in
           the last 2–3 weeks).
  climb    trail / 百岳 (a day climbing ≥ CLIMB_MIN_M): the peak week's climb ÷ the hardest day's;
           < CLIMB_TIGHT → tight 「爬升練得比距離少」, never over (推估).
  step     跨級: the biggest single-day EP of the last STEP_MONTHS months (activity records) as an
           ITRA class (XXS–XXL) against the hardest day's class: same or one up ok, STEP_OVER classes
           up or more → over (UTMB entry ≈ one class up at a time; Corrion 2018, Maleka 2026:
           experience predicts finishing; 「two classes」 and 24 months are 推估).
  hours    ultras only (a trail race of 超馬級 or bigger, planning.event_size — SP-111; the 100 km
           row from EP 100): Koop's minimum — 50 km / 50 mi: 6 h a week for
           ≥ 3 weeks in a row from 6 weeks out; 100 km / 100 mi: 9 h for ≥ 6 weeks from 9 weeks
           out (coach experience). Never worse than tight.
  cutoff   races: the predicted finish ÷ the cutoff (ok ≤ 90 %, tight ≤ 100 %, over above).
           A trail race in one piece (SP-220, docs/research/trail-pacing-strategy.md §1.2 item 3,
           §4.3): the cutoff is clock time, so the finish = the race calculator's own moving time
           (its HR pace model — the main screen's number, not the old CP + Riegel /predict, which
           ran fast on trail) + the stops: the calculator's aid stations for the event, else the
           athlete's own past races (nonmoving.py); neither → no stops, and the text says so (owner
           2026-10-05: no population default — SP-221 cancelled). No HR model → the old moving
           time, said in the text. Road races and stage races keep the old finish. The same moving
           time feeds every other check of such a race (owner 2026-10-06): the 6 h long-event line,
           Koop's ultra size, the hardest day's コース定数 (readiness' long day).
           百岳: the predicted time to the summit vs the turnaround (撤退時間, hours from that
           day's start): later = over (「不適合這座百岳」, the owner's rule 2026-10-05), < 30 min
           to spare = tight (推估).
  late     < 21 days to the race: late (the fitness window has closed — Koop).
「over」 comes only from the cutoff / turnaround and 跨級 (SP-112); < 3 weeks is its own 「late」.

百岳 only (SP-112 items 6–7), never over:
  vam      the climb rate the hardest day needs (its climb ÷ the uphill share of its time — the
           turnaround hours when set, else the predicted hours; the uphill share = the climb's part of
           the day's EP, 推估) against the athlete's own rates from hikehr's windows (100 m, > 1.5
           km/h, ≥ 10 %, HR ≥ AeT): 「走得順」 = the median VAM at AeT … 0.95 LTHR, 「走得辛苦」 = at ≥ 0.95
           LTHR; each shrunk towards 山本 2015's 430 m/h (無雪期登山, pack ~10 % of body weight; 510 m/h
           for the hard rate, 推估) with k = CLIMB_K windows (terrain_calib's rule). Moved to the
           route's top altitude by the athlete's own altitude factor (hikehr.altitude_factor) when it
           is known, else Wehrlin & Hallén 2006 (−6.3 % per 1000 m); by the pack (山本: 0 → 10 → 20 %
           of body weight = 475 → 430 → 395 m/h, 推估) when the event has one. ≤ steady ok, else tight.
  power    with a running power meter (plan profile power_meter + a CP): CP ÷ body weight ≥ 1.5 W/kg
           ok, else tight (Burtscher 2004: a pack at 300 m/h up to 3,500 m needs ~1.2–1.5 W/kg below
           the anaerobic threshold). Not shown without a power meter.

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
WEEK_OVER = 0.50             # UA's starting point: below it a shorter course is suggested (never over, SP-112)
CLIMB_TIGHT = 0.50           # SP-112 推估: the peak week's climb < half the hardest day's → tight
STEP_MONTHS = 24             # SP-112 推估 (UTMB Running Stones: two years)
STEP_OVER = 2                # classes up → over (推估)
# ITRA's race classes by EP (km-effort; race-feasibility.md §2.4, run-motion's copy of ITRA)
ITRA_CLASSES = (("XXS", 0.0), ("XS", 25.0), ("S", 45.0), ("M", 75.0), ("L", 115.0), ("XL", 155.0), ("XXL", 210.0))
LONG_DAY_H = 6.0             # planning.LONG_EVENT_HOURS: a longer / shorter event for UA's rule
CLIMB_MIN_M = 200.0          # 推估: a race day climbing less is judged on km only
CUTOFF_TIGHT = 0.90          # 推估: a finish within 10 % of the cutoff
TRAIL_KINDS = ("race", "other")   # the events the race calculator runs as 越野 (race_refs.CALC_TYPE)
SUMMIT_SPARE_H = 0.5         # 推估: < 30 min to spare at the summit
# Koop〈How Much Do You Need To Train〉: (race km ≥, hours a week, weeks in a row, from weeks out)
KOOP = ((100.0, 9.0, 6, 9), (50.0, 6.0, 3, 6))
SRC_UA = N_("週量：Uphill Athlete〈Big Vert Ultra Marathon〉——每週的距離和爬升，從比賽最難那天的一半開始，長的比賽練到 90–100 %")
SRC_WEEK = N_("週量最多判到「有點趕」：完賽和沒完賽的人最高週量沒有差別（Hoffman & Fogard 2011：134 對 127 km；"
               "沒完賽主要是腸胃 23 %、趕不上關門 18.7 %，練不夠只有 0.7 %），和完賽有關的是經驗（Corrion 2018、Maleka 2026）")
SRC_STEP = N_("跨級：ITRA 依 EP 分級（XXS–XXL）；UTMB 報名等於一次只能往上跳一級；完賽過越多場越不容易 DNF"
              "（Corrion 2018、Maleka 2026）；「高兩級」和「24 個月」是推估")
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


CLIMB_K = 10                 # terrain_calib's shrinkage strength
YAMAMOTO_STEADY = 430.0      # m/h, 無雪期登山 with ~10 % of body weight (山本 2015)
YAMAMOTO_HARD = 510.0        # m/h, the table's fastest row (雪山岩場) as the hard default — 推估
YAMAMOTO_PACK = ((0.0, 475.0), (0.10, 430.0), (0.20, 395.0))   # pack share of body weight → m/h
CLIMB_POWER_OK = 1.5         # W/kg (Burtscher 2004)
CLIMB_POWER_LOW = 1.2
SRC_VAM = N_("爬升速度：山本正嘉 2015（健行 350、無雪期登山 430、雪山岩場 510 m/h，背包約體重 10 %）只當資料不夠時的預設；"
             "你的速度用心率分區的 100 m 爬坡段（AeT 到 0.95 LTHR = 走得順，≥ 0.95 LTHR = 走得辛苦）；"
             "海拔：你的海拔因子，沒有時用 Wehrlin & Hallén 2006（每 1000 m −6.3 %）")
SRC_POWER = N_("爬坡功率：Burtscher 2004——背包每小時爬 300 m（到 3,500 m）約需 1.2–1.5 W/kg，而且要在無氧閾值以下")


def _shrink(v: Optional[float], n: int, prior: float) -> float:
    return prior if not v or n <= 0 else (n * v + CLIMB_K * prior) / (n + CLIMB_K)


def pack_factor(share: float) -> float:
    """山本's m/h at a pack share of body weight ÷ the 10 % row (linear between the rows)."""
    pts = YAMAMOTO_PACK
    share = min(max(share, pts[0][0]), pts[-1][0])
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if a <= share <= b:
            return (va + (vb - va) * (share - a) / (b - a)) / pts[1][1]
    return 1.0


def climb_rates(wins: list[dict]) -> dict:
    """The athlete's climb rates from hikehr windows ({"vam", "hr", "z", "lthr"}): {"steady" / "hard":
    {"vam" (median), "n", "z" (median altitude)}, "alt_pct" (% per 1000 m, None = not enough)}."""
    from statistics import median
    from backend.engine.racepower import hikehr as HH
    out = {}
    for band, keep in (("steady", lambda w: w["hr"] < 0.95 * w["lthr"]), ("hard", lambda w: w["hr"] >= 0.95 * w["lthr"])):
        ws = [w for w in wins or () if w.get("lthr") and keep(w)]
        zs = [w["z"] for w in ws if w.get("z") is not None]
        out[band] = {"vam": float(median(w["vam"] for w in ws)) if ws else None, "n": len(ws),
                     "z": float(median(zs)) if zs else None}
    alt = HH.altitude_factor(wins or [], 0.0, 0.0) if wins else {"enough": False}
    out["alt_pct"] = alt.get("pct_per_km") if alt.get("enough") else None
    return out


def vam_check(e, hd: dict, rates: dict, top_m: Optional[float], weight: Optional[float], div: float) -> dict:
    """The 百岳 climb-rate reference (SP-112 item 6): {"level", "text", "need", "steady", "hard", ...}."""
    hours = float(e.cutoff_hours) if getattr(e, "cutoff_hours", None) else float(hd["hours"] or 0.0)
    eff = ep(hd["km"], hd["climb_m"], div)
    share = (hd["climb_m"] / div) / eff if eff else 0.0
    need = hd["climb_m"] / (hours * share) if hours > 0 and share > 0 else None
    notes = []
    vals = {}
    for band, prior in (("steady", YAMAMOTO_STEADY), ("hard", YAMAMOTO_HARD)):
        r = (rates or {}).get(band) or {}
        v = _shrink(r.get("vam"), int(r.get("n") or 0), prior)
        if top_m is not None and r.get("z") is not None and top_m > r["z"]:
            dz = (top_m - r["z"]) / 1000.0
            pct = rates.get("alt_pct")
            v *= (1 + pct / 100.0) ** dz if pct is not None else max(0.0, 1 - 0.063 * dz)
        vals[band] = v
    n = int(((rates or {}).get("steady") or {}).get("n") or 0)
    notes.append(_("你的資料 {n} 段（往山本的預設收縮）", n=n) if n >= CLIMB_K else
                 _("資料不足（{n} 段），主要用山本的預設 430 m/h", n=n))
    if top_m is not None:
        notes.append(_("換算到路線最高點 {z:.0f} m（{how}）", z=top_m,
                       how=_("你的海拔因子") if (rates or {}).get("alt_pct") is not None else _("Wehrlin & Hallén")))
    if getattr(e, "pack_kg", None) is not None and weight:
        f = pack_factor(float(e.pack_kg) / float(weight))
        vals = {k: v * f for k, v in vals.items()}
        notes.append(_("背包 {kg:g} kg（體重的 {p:.0%}）", kg=e.pack_kg, p=float(e.pack_kg) / float(weight)))
    else:
        notes.append(_("沒有背包資料"))
    if need is None:
        return {"level": "unknown", "text": _("算不出需要的爬升速度（沒有爬升或時間）"), "notes": notes}
    lv = "ok" if need <= vals["steady"] else "tight"
    txt = _("最難那天要每小時爬約 {need:.0f} m；你走得順約 {s:.0f} m/h、走得辛苦約 {h:.0f} m/h",
            need=need, s=vals["steady"], h=vals["hard"])
    if need > vals["hard"]:
        txt += _("：要比你目前最用力的爬升還快，撤不撤退看撤退時間那項")
    elif lv == "tight":
        txt += _("：要靠接近閾值的強度撐")
    return {"level": lv, "text": txt + "（" + "，".join(notes) + "）", "need": round(need), "steady": round(vals["steady"]),
            "hard": round(vals["hard"]), "n": n}


def divisor() -> float:
    """The EP climb divisor: the personal one when installed (planning.DIVISOR_OF), else ITRA's 100."""
    from backend.engine import planning as P
    try:
        d = float(P.DIVISOR_OF()) if P.DIVISOR_OF is not None else P.EP_DIVISOR
    except Exception:                       # noqa: BLE001
        d = P.EP_DIVISOR
    return d if d > 0 else P.EP_DIVISOR


def ep(km: float, climb_m: float, div: Optional[float] = None) -> float:
    return float(km or 0.0) + float(climb_m or 0.0) / (div or divisor())


def itra_class(e_p: float) -> int:
    """ITRA_CLASSES index of an EP."""
    return max(i for i, (_n, lo) in enumerate(ITRA_CLASSES) if e_p >= lo)


def best_day_ep(acts: list[dict], div: Optional[float] = None) -> Optional[dict]:
    """The biggest single-day EP of foot activities `acts` (activity_rows; a day's activities summed):
    {"ep", "date"}; None without any."""
    by: dict = {}
    for a in acts or ():
        if a.get("foot"):
            by[a["date"]] = by.get(a["date"], 0.0) + ep(a["km"], a["climb_m"], div)
    if not by:
        return None
    d = max(by, key=by.get)
    return {"ep": by[d], "date": d}


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


def gpx_hint() -> str:
    """SP-114: the reminder for an ultra (越野 ≥ 50 km) without a GPX."""
    return _("超馬建議上傳 GPX：專項期的爬坡課和賽事評估要靠它知道爬升在哪幾段、有多陡。在賽季計畫的「路線 GPX」上傳")


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
# the cutoff of a trail race (SP-220)
# ---------------------------------------------------------------------------

def cutoff_finish(finish: dict, line_h: float, cut: float) -> tuple[float, str, dict]:
    """(finish hours, the check's text, its extra fields) from trail_finish()'s {"moving_h" (None = no
    HR pace model), "stop_h", "stop_src" (user / history / None), "stop_user_min", "short_min",
    "n_runs", "effort", "fallback" (power / plan)}: moving + stops, each said in the text; no stop
    data → 「沒有算停留」; no HR model → the race line's hours (`line_h`, the old moving time)."""
    mv = finish.get("moving_h")
    moving = float(mv) if mv else float(line_h)
    stop = max(0.0, float(finish.get("stop_h") or 0.0))
    fin = moving + stop
    src = finish.get("stop_src") if stop > 0 else None
    if src:
        txt = _("照現在的體能，預估移動 {m:.1f} 小時＋停留 {s:.0f} 分鐘＝ {f:.1f} 小時；關門是 {c:g} 小時",
                m=moving, s=stop * 60.0, f=fin, c=cut)
        um, n = float(finish.get("stop_user_min") or 0.0), finish.get("n_runs")
        if src == "user" and n:
            why = _("停留＝賽事計算機的補給站 {u:g} 分鐘＋零碎停頓約 {s:.0f} 分鐘（照你過去 {n} 場比賽的比例）",
                    u=um, s=float(finish.get("short_min") or 0.0), n=n)
        elif src == "user":
            why = _("停留＝賽事計算機的補給站 {u:g} 分鐘", u=um)
        else:
            why = _("停留用你過去 {n} 場比賽的停留推算（賽事計算機沒有填補給站）", n=n or 0)
        txt += _("（{why}）", why=why)
    else:
        txt = _("照現在的體能，預估移動 {m:.1f} 小時；關門是 {c:g} 小時", m=moving, c=cut)
        txt += _("。沒有算停留：在賽事計算機的補給站填停留分鐘，或累積 2 場以上的越野比賽紀錄，就會算進來")
    eff = finish.get("effort")
    if mv and eff and abs(float(eff) - 1.0) > 1e-9:
        txt += _("。用賽事計算機的努力目標 {p:.0%}", p=float(eff))
    if not mv:
        txt += ("。" + (_("沒有越野心率配速模型，移動時間退回舊的功率模型（CP＋Riegel），越野上常算得太快")
                       if finish.get("fallback") == "power" else
                       _("沒有越野心率配速模型，移動時間用賽季計畫填的預估時間")))
    kw = {"moving_h": round(moving, 2), "stop_h": round(stop, 2), "stop_src": src,
          "time_method": "trail_hr" if mv else (finish.get("fallback") or "plan")}
    return fin, txt, kw


# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------

def assess(e, line: Optional[dict], today: dt.date, hist: list[dict], summit: Optional[dict] = None,
           best: Optional[dict] = None, climb: Optional[dict] = None, power: Optional[dict] = None,
           finish: Optional[dict] = None) -> dict:
    """The verdict for event `e` (planning.Event) with its race line (race_refs.race_line) and
    the last weeks (weekly_history). `summit`: summit_eta()'s result for a 百岳 with a summit.
    `best`: best_day_ep of the last STEP_MONTHS months (the 跨級 check; None = not checked).
    百岳 only: `climb` {"rates" (climb_rates), "top_m", "weight"} — the climb-rate reference; `power`
    {"cp", "kg"} with a running power meter — the climb-power check (None = not shown).
    `finish`: trail_finish()'s moving + stop time for the cutoff of a trail race (SP-220); None =
    the race line's hours, as before."""
    out = {"event_id": e.id, "name": e.name, "date": e.date, "priority": e.priority, "kind": e.kind,
           "days": int(e.days or 1),
           "days_to": (e.start - today).days, "checks": [], "suggestions": [], "src": [_(SRC_UA), _(SRC_WEEK)]}
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
            div = divisor()
            trail = hd["climb_m"] >= CLIMB_MIN_M
            r_km = pk["km"] / hd["km"] if hd["km"] else None
            r_cl = pk["climb_m"] / hd["climb_m"] if trail else None
            hd_ep, pk_ep = ep(hd["km"], hd["climb_m"], div), ep(pk["km"], pk["climb_m"], div)
            # SP-112: one EP ratio (a road race: km), not the lower of km and climb
            ratio = (pk_ep / hd_ep if hd_ep else None) if trail else r_km
            ok_at = week_ok_at(e, line)
            if ratio is not None:
                lv = "ok" if ratio >= ok_at else "tight"          # never over (SP-112)
                # a race day under CLIMB_MIN_M (路跑) is judged on km only: no climb in the text either
                txt = (_("照現在每週慢慢加量，賽前你一週最多大約練到 EP {ep:.0f}（{km:.0f} km、爬升 {cl:.0f} m），"
                         "是比賽最難那天 EP {hd:.0f} 的 {p:.0f} %",
                         ep=pk_ep, km=pk["km"], cl=pk["climb_m"], hd=hd_ep, p=ratio * 100) if trail else
                       _("照現在每週慢慢加量，賽前你一週最多大約跑到 {km:.0f} km，是比賽距離的 {p:.0f} %",
                         km=pk["km"], p=ratio * 100))
                txt += _("（最好到 {a:.0f} %；週量分不出誰跑得完，最多判到有點趕，練得少會讓預估時間變慢，看關門那項）",
                         a=ok_at * 100)
                check("weekly", lv, txt, ratio=round(ratio, 3), ok_at=ok_at, over_below=None,
                      ratio_ep=None if not trail else round(ratio, 3),
                      ratio_km=None if r_km is None else round(r_km, 3),
                      ratio_climb=None if r_cl is None else round(r_cl, 3))
                if ratio < WEEK_OVER:
                    s = ratio / ok_at
                    out["downgrade"] = {"km": round(hd["km"] * s), "climb_m": round(hd["climb_m"] * s / 10) * 10}
                    out["suggestions"].append(_("練量離比賽還遠，可以考慮報短一點的組別：照推算，你大約應付得了一天 {km} km、爬升 {cl} m 的比賽",
                                                km=out["downgrade"]["km"], cl=out["downgrade"]["climb_m"]))
                elif lv == "tight" and days_to >= WINDOW_DAYS:     # late already says 「照現有體能跑」
                    # SP-224: no data supports slowing the first half for runners near the cutoff (SP-198
                    # §2.3: UTMB 2025's slow starters DNF'd more); stop time tracks the finish more
                    # closely than the pacing spread (Martínez-Navarro 2021 r = 0.64; Kerhervé 2015). 推估 wording
                    out["suggestions"].append(_("照分段的心率上限跑，補給站少停"))
            if r_cl is not None:
                # SP-112: the climb as a sub-check — at most tight (推估)
                lv = "ok" if r_cl >= CLIMB_TIGHT else "tight"
                txt = _("賽前一週最多大約爬 {cl:.0f} m，是比賽最難那天爬升 {hd:.0f} m 的 {p:.0f} %",
                        cl=pk["climb_m"], hd=hd["climb_m"], p=r_cl * 100)
                if lv == "tight":
                    txt += _("：爬升練得比距離少，不到一半（推估）")
                check("climb", lv, txt, ratio=round(r_cl, 3), tight_below=CLIMB_TIGHT)
                if lv == "tight" and days_to >= WINDOW_DAYS:
                    out["suggestions"].append(_("每週多排一些爬坡：長跑挑爬升多的路線"))
            need = koop_need(line, e)
            if need:
                kr = koop_run(base["hours"], weeks, e.start, need)
                out["koop"] = {k: round(v, 1) if isinstance(v, float) else v for k, v in kr.items()}
                out["src"].append(_(SRC_KOOP))
                txt = _("超馬建議賽前 {w} 週開始，每週練 {h:g} 小時、連續 {n} 週；照推算最多能連續做到 {b} 週（一週最多約 {p:.1f} 小時）",
                        w=need[3], h=need[1], n=need[2], b=kr["best_run"], p=kr["peak_h"])
                check("hours", "ok" if kr["best_run"] >= need[2] else "tight", txt)
        # 跨級 (SP-112): the biggest single day of the last 24 months against the race's hardest day
        if best is not None:
            out["src"].append(_(SRC_STEP))
            div = divisor()
            hd_ep = ep(hd["km"], hd["climb_m"], div)
            rc, mc = itra_class(hd_ep), itra_class(best["ep"])
            up = rc - mc
            lv = "over" if up >= STEP_OVER else "ok"
            d = best["date"]
            txt = _("過去 {m} 個月單日最大 EP {ep:.0f}（{mine}，{date}）；比賽最難那天 EP {r:.0f}（{race}）",
                    m=STEP_MONTHS, ep=best["ep"], mine=ITRA_CLASSES[mc][0],
                    date=d.isoformat() if hasattr(d, "isoformat") else str(d), r=hd_ep, race=ITRA_CLASSES[rc][0])
            txt += (_("：高 {n} 級，跳太多了", n=up) if lv == "over" else
                    _("：高一級，可以") if up == 1 else _("：同級或更低"))
            check("step", lv, txt, race_class=ITRA_CLASSES[rc][0], best_class=ITRA_CLASSES[mc][0], up=up,
                  best_ep=round(best["ep"], 1))
            if lv == "over":
                out["suggestions"].insert(0, _("先跑一場低一級（{cls}）的比賽，或把這場改成 B／C 賽",
                                               cls=ITRA_CLASSES[max(0, rc - 1)][0]))
        # 百岳: the climb rate and the climb power (SP-112 items 6–7) — never over
        if e.kind == "baiyue" and climb is not None:
            vc = vam_check(e, hd, climb.get("rates") or {}, climb.get("top_m"), climb.get("weight"), divisor())
            check("vam", vc["level"], vc["text"], **{k: vc[k] for k in ("need", "steady", "hard", "n") if k in vc})
            out["src"].append(_(SRC_VAM))
        if e.kind == "baiyue" and power and power.get("cp") and power.get("kg"):
            wkg = float(power["cp"]) / float(power["kg"])
            lv = "ok" if wkg >= CLIMB_POWER_OK else "tight"
            txt = _("CP {cp:.0f} W ÷ 體重 {kg:.1f} kg = {w:.2f} W/kg", cp=power["cp"], kg=power["kg"], w=wkg)
            txt += (_("：夠背包爬坡（≥ {a:g}）", a=CLIMB_POWER_OK) if lv == "ok" else
                    _("：在 {b:g}–{a:g} 之間，爬坡會吃力", a=CLIMB_POWER_OK, b=CLIMB_POWER_LOW) if wkg >= CLIMB_POWER_LOW else
                    _("：低於 {b:g}，背包爬坡會很吃力", b=CLIMB_POWER_LOW))
            check("power", lv, txt, w_per_kg=round(wkg, 2))
            out["src"].append(_(SRC_POWER))
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
        elif cut and finish is not None:
            # SP-220: a trail race — the calculator's moving time + the stops against the clock-time cutoff
            fin, txt, kw = cutoff_finish(finish, float(line["hours"]), cut)
            r = fin / cut
            lv = "ok" if r <= CUTOFF_TIGHT else "tight" if r <= 1.0 else "over"
            check("cutoff", lv, txt, finish_h=round(fin, 2), cutoff_h=cut, **kw)
            if lv == "over":
                out["suggestions"].insert(0, _("預估會超過關門時間：建議報短一點的組別，或這場先不跑"))
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
        div = divisor()
        rk = max(w["km"] for w in weeks) / hd["km"] if hd["km"] else None
        rc = max(w["climb_m"] for w in weeks) / hd["climb_m"] if not road else None
        hd_ep = ep(hd["km"], hd["climb_m"], div)
        # SP-112: the biggest week's EP ÷ the hardest day's (a road race: km); at most 「差一點」
        r = rk if road else (max(ep(w["km"], w["climb_m"], div) for w in weeks) / hd_ep if hd_ep else None)
        if r is not None:
            lv = "ok" if r >= ok_at else "tight"
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


def baiyue_inputs(plan, ds, today: dt.date, e) -> tuple[Optional[dict], Optional[dict]]:
    """(climb, power) for a 百岳's assess(): the hikehr windows' rates, the GPX's top, the body weight;
    CP ÷ weight only with a running power meter in the profile and a CP row. Errors → None."""
    climb = power = None
    kg = plan.weight_on(today) if plan is not None else None
    try:
        from backend.engine.racepower import athlete as A
        wins, _th = A.hike_hr_windows(ds, A.hike_workouts(ds, today))
        top = None
        try:
            from backend.engine import event_gpx as EG
            row = EG.get(e.id)
            top = float(row["z_max"]) if row and row.get("z_max") is not None else None
        except Exception:                   # noqa: BLE001
            top = None
        climb = {"rates": climb_rates(wins), "top_m": top, "weight": kg}
    except Exception:                       # noqa: BLE001 — the other checks still run
        climb = None
    cp = plan.threshold_on("cp", today) if plan is not None else None
    if (getattr(plan, "profile", None) or {}).get("power_meter") and cp and kg:
        power = {"cp": float(cp), "kg": float(kg)}
    return climb, power


def calc_inputs(e) -> dict:
    """The race calculator's saved form state of the event (race_calc_store inputs), {} without one."""
    from backend.engine import race_calc_store as RC
    try:
        saved = RC.get(e.id)
    except Exception:                       # noqa: BLE001 — a bad id / DB: nothing saved
        return {}
    inp = (saved or {}).get("inputs")
    return inp if isinstance(inp, dict) else {}


def calc_stops(inp: dict, km: float) -> list[dict]:
    """The aid stations of the saved calculator inputs as the page sends them (racepower.html stopsOf /
    stopIssue): a row needs 0 < km < the course km and no other row within 50 m; a blank minutes =
    the type's default (fuel.STOP_TYPES, 推估) — the stops its ETA and 含停留總時間 count."""
    from backend.engine.racepower import fuel as FU
    rows = []
    raw = inp.get("stops")
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        try:
            k = float(s["km"])
        except (KeyError, TypeError, ValueError):
            continue
        t = s.get("type") if s.get("type") in FU.STOP_TYPES else FU.STOP_DEFAULT
        try:
            m = float(s["minutes"]) if s.get("minutes") not in (None, "") else FU.STOP_TYPES[t]["minutes"]
        except (TypeError, ValueError):
            m = FU.STOP_TYPES[t]["minutes"]
        if math.isfinite(k) and math.isfinite(m) and 0.0 < k < km and m >= 0.0:
            rows.append({"km": k, "type": t, "minutes": m})
    return [r for r in rows if not any(o is not r and abs(o["km"] - r["km"]) < 0.05 for o in rows)]


def calc_effort(inp: dict) -> float:
    """The calculator's 努力目標 of the saved inputs (auto mode, 0–100 %), else 1.0 (全力, the page's default)."""
    st = inp.get("S") if isinstance(inp.get("S"), dict) else {}
    try:
        f = float(st.get("effort", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return f if (st.get("mode") or "auto") == "auto" and 0.0 < f <= 1.0 else 1.0


def trail_finish(e, course: dict, plan_fn: Optional[Callable] = None) -> dict:
    """SP-220: the moving and stop time the race calculator shows for a trail race (one piece) —
    POST /plan's own computation (calc.make_plan on the live athlete) in auto mode with the course
    the page opens (the event's GPX, else its km / climb), the saved 努力目標 and aid stations. The
    race-day forecast is not fetched here (no network in the card): the calculator adds its heat when
    it has one. Moving = summary.time_s when the total came from the HR pace model (total_method
    "trail_hr"), else None (→ the old moving time). Stops, as the calculator's 含停留總時間: the
    non-moving prediction (nonmoving.py — the stations' minutes, else your past races' rate, plus the
    short stops) when there is a personal profile; else the stations' minutes; else none.
    `plan_fn(body)` = calc.make_plan on the live context (tests)."""
    from backend.engine.racepower import calc as CALC
    t = course["totals"]
    km = float(t["km"] or 0.0)
    inp = calc_inputs(e)
    stops = calc_stops(inp, km)
    user_min = sum(s["minutes"] for s in stops)
    eff = calc_effort(inp)
    out = {"moving_h": None, "stop_h": user_min / 60.0, "stop_src": "user" if user_min > 0 else None,
           "stop_user_min": user_min, "effort": eff, "short_min": None, "n_runs": None}
    if plan_fn is None:
        def plan_fn(body):
            from backend.api import racepower as RP
            return CALC.make_plan(RP.LIVE, body)
    try:
        ref = (CALC.CourseRef(event_id=e.id) if course.get("filename") else
               CALC.CourseRef(manual={"km": km, "gain": float(t["gain_m"] or 0.0),
                                      "loss": None if course.get("descent_assumed") else float(t["loss_m"] or 0.0)}))
        body = CALC.PlanIn(type="trail", mode="auto", effort_target=eff, date=e.date, distance_km=km,
                           gain_m=float(t["gain_m"] or 0.0), course=ref, stops=[CALC.StopIn(**s) for s in stops])
        s = plan_fn(body).get("summary") or {}
    except Exception:                       # noqa: BLE001 — no CP / RE / course: the old moving time
        return out
    if s.get("total_method") == "trail_hr" and s.get("time_s"):
        out["moving_h"] = float(s["time_s"]) / 3600.0
    nm = s.get("nonmoving")
    if nm and nm.get("total_s") is not None:
        out.update(stop_h=float(nm["total_s"]) / 3600.0, stop_src="user" if nm.get("method") == "user" else "history",
                   n_runs=nm.get("n_runs"), short_min=float(nm.get("short_s") or 0.0) / 60.0)
    return out


def races(plan, ds, today: dt.date, event_id: Optional[str] = None,
          predict: Optional[Callable] = None, gpx: Optional[Callable] = None,
          baiyue: Optional[Callable] = None, finish: Optional[Callable] = None) -> list[dict]:
    """The verdicts of the upcoming A / B races (or the one `event_id`, any grade). `baiyue(e)` →
    (climb, power) for a 百岳 (tests); None = baiyue_inputs. `finish(e, course)` → the moving + stop
    time of a trail race in one piece (SP-220: the hours of every check, the stops for the cutoff);
    None = trail_finish."""
    from backend.engine.panels import race_refs as RR
    predict = predict or RR.calculator_hours
    gpx = gpx or RR.stored_course
    finish = finish or trail_finish
    evs = [e for e in plan.events if e.end >= today]
    evs = [e for e in evs if e.id == event_id] if event_id else [e for e in evs if e.priority in ("A", "B")]
    hist = weekly_history(ds, today, KOOP_WEEKS)
    acts = activity_rows(ds, today, B2B_WEEKS * 7)
    try:
        best = best_day_ep(activity_rows(ds, today, STEP_MONTHS * 365 // 12))      # 跨級 (SP-112)
    except Exception:                       # noqa: BLE001 — the other checks still run
        best = None
    out = []
    for e in sorted(evs, key=lambda x: x.start):
        line = summit = sleep_note = fin = None
        if e.distance_km:
            course = RR.course_of(e, gpx)
            hs = predict(e, course)
            src = _("賽事計算器預測的完賽時間")
            if e.kind in TRAIL_KINDS and RR.split_days(e) == 1:
                # SP-220: the calculator's HR pace moving time for every check of a trail race in one
                # piece (owner 2026-10-06: not only the cutoff) + the stops for the cutoff; a stage
                # race keeps the old hours
                fin = finish(e, course) or {"moving_h": None}
                if fin.get("moving_h"):
                    hs, src = [float(fin["moving_h"])], _("賽事計算機的越野心率配速模型（移動時間）")
                else:
                    fin["fallback"] = "power" if hs else "plan"
            course, sleep_note = RR.sleep_course(e, course)    # SP-114: a 連續 race's hardest stretch
            line = RR.race_line(e, hs, src, course)
            if line is not None:
                summit = event_summit(e, course, [d["hours"] for d in line["per_day"]])
        climb = power = None
        if e.kind == "baiyue":
            climb, power = baiyue_inputs(plan, ds, today, e) if baiyue is None else baiyue(e)
        r = assess(e, line, today, hist[-BASE_WEEKS:], summit, best, climb, power, finish=fin)
        if sleep_note:
            r["split_note"] = sleep_note
        elif line is not None and RR.hardest_stretch_note(line):
            r["stretch_note"] = RR.hardest_stretch_note(line)
        if getattr(e, "gpx_recommended", False) and line is not None and not line.get("gpx"):
            r["gpx_note"] = gpx_hint()             # SP-114: an ultra without its GPX (a reminder, not a block)
        if not r.get("skipped"):
            r["readiness"] = readiness(e, line, today, hist, acts)
        # SP-114: a multi-day 百岳 is judged on the 攻頂日模擬, not the weekly volume (its own module)
        from backend.engine import baiyue_multiday as BM
        BM.apply(r, e, line, today, ds)
        out.append(r)
    return out
