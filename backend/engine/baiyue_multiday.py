"""
多日百岳的賽事評估 (SP-114, SP-545): a multi-day 百岳 is mountaineering, not a trail race — the race check
(engine/race_feasibility.py) does not judge it on the weekly volume. docs/research/baiyue-mountaineering-training.md
§1.2, §4.1, §4.2, §7 (SP-545); the owner's decisions 2026-10-05 and 2026-10-14.

Only a 百岳 of ≥ 2 days (applies). A 單攻百岳 is entered as a trail race and stays one; a multi-day
trail race keeps the trail rules (B2B included).

  攻頂日      the trip's day with the most climb (a 3-day 大小霸 ↑3500 m simulates its hardest DAY, ~1000 m,
              not the trip) — from the event's day plan / the GPX's day ends (race_refs.course_of).
  攻頂日模擬  one day climbing ≥ the summit day's climb with a pack (the activity's pack,
              racepower_hike_meta.json via athlete.activity_pack) ≥ SIM_PACK_SHARE of the trip's pack, OR
              without one on a slope steep enough (SP-545): the day's uphill average grade (100 m steps
              climbing ≥ UPHILL_MIN, uphill_grade) ≥ STEEP_GRADE. UA〈Training for Mountaineering〉: from
              8 weeks out, at least one a week, the summit day's whole climb, a pack of about the trip's
              weight. SIM_NEED = 3 is the owner's number and only a suggestion (owner 2026-10-14: 「UA 的
              三次也只是建議」); the 80 % pack, the 20 % grade and the last one ≥ 10 days out are 推估.
  一天        a multi-day activity (achievements' per-day split, > 20 h) counts day by day.

  可行性 (feasibility, SP-545 owner 2026-10-14: no 來得及／來不及 verdict) — how hard the trip will feel
              with today's ability, on the race calculator's effort words (racepower/difficulty.LABELS:
              輕鬆 < 80 %, 穩定 80–90 %, 吃力 90–97 %, 極限 97–100 %, 超出 > 100 %): the summit day's
              demand ÷ the athlete's own, item by item, the hardest item wins (effort()):
                climb   the summit day's climb ÷ the biggest single-day climb of the ability window
                hours   the summit day's hours ÷ the longest single day of the ability window
                vam     the climb rate the turnaround (撤退時間) needs ÷ 「走得辛苦」 (race_feasibility's
                        vam check) — only with a turnaround: without one the rate is the predicted
                        hours read back, a reference, not a demand
              The cut-points are the calculator's; using them for these ratios is 推估. 輕鬆／穩定 green,
              吃力 and up yellow — red stays for the real danger only: the turnaround missed (cutoff
              check, kept) and 「時間不夠」 (late, < 3 weeks, kept). The weekly-volume checks (DROP_FEAS)
              are dropped; 跨級 / climb rate / climb power (SP-112) stay as details (跨級 at most yellow).
  能力窗口 (SP-545 R1, owner 2026-10-14) 「你一次能爬多少」 looks at the last BASE_MONTHS (6) months; only when
              those were normal training — no 停練, no 減量, no 傷停 — it reaches further back, up to the
              day after the latest interruption, at most LONG_MONTHS (24, 跨級's window). An interruption
              (all 推估, ability_window / interruptions):
                停練   ≥ BREAK_DAYS (14) days in a row without a run or hike (reentry.py's 14–28-day block:
                       Daniels / VDOT O2 FVDOT starts to cost fitness there; Mujika & Padilla 2000)
                減量   ≥ LOW_WEEKS (4) weeks in a row each under LOW_SHARE (50 %) of 平常 — the median
                       weekly time on feet of the NORMAL_WEEKS (12) weeks before (a recovery week is 70 %,
                       SP-512, a taper 1–3 weeks; 50 % is reentry's first stage)
                傷停   a 傷病紀錄 injury (not illness) of 中／重 severity (its own words: 減量或改練／停跑)
  攻頂日模擬（建議） how many simulations fit before the trip: with the ability window's biggest
              single-day climb already ≥ the summit day's, every non-recovery weekend inside [trip − 8
              weeks, trip − SIM_LAST_DAYS] is one (this week's too when its Saturday is still ahead);
              below it, from that biggest day ≤ +10 % a week (SP-89's step) as before. Plus the ones
              done. ≥ SIM_NEED ok, else a yellow suggestion 「建議行程前做 3 次…，目前排得下 N 次」 —
              never red, and it does not set the card's level.
  完備程度 (readiness)  the コース定数 long day and the biggest week are replaced by the simulations
              done since trip − 8 weeks: ok ≥ SIM_NEED, else a suggestion (yellow); climbs high enough
              but neither with a pack nor steep enough are named, so the user can record the pack.

The late (< 3 weeks), turnaround (撤退時間) and B2B checks stay as race_feasibility makes them.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.engine import hints as H
from backend.i18n import N_, _

SIM_NEED = 3                 # owner 2026-10-05: 3 summit-day simulations are enough (a suggestion, SP-545)
SIM_WEEKS = 8                # UA: from 8 weeks before the trip
SIM_LAST_DAYS = 10           # 推估 (research §3.2): the last simulation ≥ 10 days before the trip (taper 7–10)
SIM_PACK_SHARE = 0.8         # 推估 (research §4.2): a pack ≥ 80 % of the trip's counts
SIM_STEP = 1.10              # SP-89: ≤ +10 % a week
LONG_MONTHS = 24             # SP-545: 「一次能爬多少」 reaches back at most 24 months — the same window as 跨級
                             # (race_feasibility.STEP_MONTHS: the UTMB Index's validity)
BASE_MONTHS = 6              # SP-545 R1 (owner 2026-10-14): the default window; further back only when normal
BREAK_DAYS = 14              # 推估: 停練 = ≥ 14 days in a row without a run / hike (reentry.py's 14–28-day block,
                             # where FVDOT starts to cost ~3 %; the owner's 「停練超過 2 週」)
LOW_SHARE = 0.5              # 推估: 減量 = a week under 50 % of 平常 (reentry's first stage is 50 %; a recovery
LOW_WEEKS = 4                # week 70 %, SP-512) for ≥ 4 weeks in a row (longer than a recovery week or a 1–3
                             # week taper; Mujika & Padilla 2000: > 4 weeks loses the recent gains)
NORMAL_WEEKS = 12            # 推估: 平常 = the median weekly time on feet of the 12 weeks before
INJ_SEVERITIES = ("moderate", "severe")   # 傷病紀錄 中／重 (injuries.SEVERITY_HELP: 減量或改練／停跑)
STEEP_GRADE = 0.20           # SP-545 推估 (research §7.2): UA's 「steep」 is > 20 % (Scott Johnston; gravity is the
                             # loading, like weight on a bar; a UA forum reply: 14–15 % is not steep); Pandolf
                             # 1977: no pack at 20 % ≈ a pack of 18 % of body weight at ~17 % (steep_hill.simulated)
UPHILL_MIN = 0.03            # 推估: a 100 m step climbing < 3 % is flat (downhill_recovery's ±3 %)
GRADE_STEP_M = 100.0         # the hikehr / grade_model window
# the race calculator's effort words (racepower/difficulty.LABELS, owner 2026-10-14: the same words both places)
EFFORT_LABEL = {"easy": N_("輕鬆"), "steady": N_("穩定"), "hard": N_("吃力"), "max": N_("極限"), "over": N_("超出")}
EFFORT_LEVEL = {"easy": "ok", "steady": "ok", "hard": "tight", "max": "tight", "over": "tight"}
SIM_LABEL = {"ok": N_("排得下"), "tight": N_("建議")}
OK_LABEL = N_("沒問題")          # the details' ok (跨級 / climb rate / climb power) instead of 「來得及」
DETAIL_OK = ("step", "vam", "power")
# The feasibility checks the 攻頂日模擬 replaces (SP-112 × SP-114, 2026-10-05 integration): the weekly
# EP volume (UA Big Vert), its climb sub-check (the peak week's climb ÷ the hardest day's — a weekly
# volume too, and the simulation counts the one-day climb instead) and Koop's ultra hours: trail-race
# sources. Kept as details: 「step」 (跨級: the biggest single day done vs the hardest day — its
# trail-race suggestion is swapped for a route one in feasibility(), at most yellow since SP-545), 「vam」
# (the climb rate the route needs vs the athlete's own) and 「power」 (W/kg for a pack uphill); the cutoff /
# turnaround and late checks stay too and still set the level.
DROP_FEAS = ("weekly", "climb", "hours")
LEVEL_FROM = ("effort", "cutoff", "late")       # SP-545: the checks that set the card's level
# 完備程度: the long day, the biggest week and Koop's hours go the same way (SP-112 adds no readiness
# check of its own; B2B stays).
DROP_READY = ("long", "weekly", "hours")
SRC_SUMMIT = N_("攻頂日模擬：Uphill Athlete〈Training for Mountaineering〉——行程前 8 週起每週一次，一天爬完攻頂日（爬升最多的那天）"
                "的爬升，背和行程差不多重的背包；3 次是建議，不是門檻。背包 ≥ 行程背包 80%、最後一次在行程前 10 天以前是推估。"
                "沒背包但爬坡段平均坡度 ≥ 20% 也算（推估：UA 的「陡」是 20% 以上，教練 Scott Johnston 說重力就是負荷；"
                "Pandolf 1977：不背包走 20% 約等於背體重 18% 的包走 17%）。一天能爬多少看近 6 個月最多的一天；"
                "近 6 個月訓練照常（沒停練、沒減量、沒傷停）才往前延伸到上一次中斷之後，最多 24 個月"
                "（停練＝連續 14 天以上沒跑步或健行，減量＝連續 4 週每週時數不到前 12 週中位數的一半，傷停＝傷病紀錄中、重度；都是推估）。"
                "到得了攻頂日的爬升就每個週末都能排，不到才每週最多 +10% 慢慢加。多日百岳不看每週的距離和爬升（那是越野跑的規則）")
SRC_EFFORT = N_("吃力程度：攻頂日的爬升、時數（有撤退時間時加上要的爬升速度）÷ 你自己做過的（近 6 個月最多的一天，"
                "訓練照常時往前延伸、最多 24 個月；走得辛苦的爬升速度），取最吃力的一項，用賽事計算機的努力度字眼：80% 以下輕鬆、80–90% 穩定、"
                "90–97% 吃力、97–100% 極限、超過 100% 超出。這個對應是推估")
EFFORT_SCALE = N_("百分比對到賽事計算機的努力度：80% 以下輕鬆、80–90% 穩定、90–97% 吃力、97–100% 極限、超過 100% 超出（推估）")
ITEM_NAME = {"climb": N_("攻頂日爬升"), "hours": N_("攻頂日時數"), "vam": N_("撤退時間要的爬升速度")}
EASIER = {"climb": N_("多排一天把攻頂日拆短、換攻頂日爬升少一點的路線，或先做幾次攻頂日模擬"),
          "hours": N_("多排一天把攻頂日拆短，或先走一次差不多時數的長天"),
          "vam": N_("多排一天把攻頂日拆短、背輕一點，或先做幾次攻頂日模擬")}


def applies(e) -> bool:
    """A 百岳 of ≥ 2 days (a 單攻百岳 is entered as a trail race)."""
    return getattr(e, "kind", None) == "baiyue" and int(getattr(e, "days", 1) or 1) >= 2


def summit_day(line: dict) -> Optional[dict]:
    """The race line's day with the most climb: {day, km, climb_m, hours}."""
    days = line.get("per_day") or []
    if not days:
        return None
    d = max(days, key=lambda x: x["climb_m"])
    return {"day": d["day"], "km": float(d["km"]), "climb_m": float(d["climb_m"]), "hours": float(d["hours"]),
            "cc": float(d["cc"])}


# ---------------------------------------------------------------------------
# the days done: one row per calendar day of a foot activity
# ---------------------------------------------------------------------------

def _split_days(w, cache: dict) -> Optional[list[dict]]:
    """A multi-day activity's days from the achievements cache (achievements._split_days, > 20 h —
    read only; the 成就 page fills it): [{date, climb_m, km, hours, day_no}], None = one day."""
    hit = cache.get(getattr(w.entry, "file", None)) if cache else None
    days = ((hit or {}).get("summary") or {}).get("days") if isinstance(hit, dict) else None
    if not days or len(days) < 2:
        return None
    out = []
    for i, d in enumerate(days, 1):
        try:
            out.append({"date": dt.date.fromisoformat(str(d["date"])[:10]), "climb_m": float(d.get("climbing_m") or 0.0),
                        "km": float(d.get("distance_km") or 0.0), "hours": float(d.get("moving_s") or 0.0) / 3600.0,
                        "day_no": i})
        except (KeyError, TypeError, ValueError):
            return None
    return out


def foot_days(ds, start: dt.date, end_excl: dt.date) -> list[dict]:
    """The foot activities between the dates, a multi-day one split by day: {date, climb_m, km, hours,
    day_no (None = the whole activity), w}."""
    if ds is None:
        return []
    from backend.engine import overview as O
    try:
        from backend.engine import achievements as ACH
        cache = ACH._load_cache()
    except Exception:                       # noqa: BLE001 — no cache: every activity is one day
        cache = {}
    out = []
    for w in O.workouts_between(ds, start, end_excl):
        if O.category(w) not in O.FOOT:
            continue
        days = _split_days(w, cache)
        if days:
            out += [{**d, "w": w} for d in days if start <= d["date"] < end_excl]
            continue
        out.append({"date": O.wdate(w), "climb_m": O._n(w.metrics.get("climbing")) or 0.0,
                    "km": O._n(w.metrics.get("distance")) or 0.0, "hours": O.moving_s(w) / 3600.0,
                    "day_no": None, "w": w})
    return out


def sim_rows(ds, today: dt.date, days: int = SIM_WEEKS * 7) -> list[dict]:
    """The foot days of the last `days` with their climb and the pack recorded for them
    (athlete.activity_pack; None = not recorded): {date, climb_m, km, hours, pack_kg, day_no, w}."""
    if ds is None:
        return []
    from backend.engine.racepower import athlete as A
    try:
        meta = A.hike_meta()
    except Exception:                       # noqa: BLE001 — no file: no packs
        meta = {}
    out = []
    for r in foot_days(ds, today - dt.timedelta(days=days), today + dt.timedelta(days=1)):
        try:
            pack = A.activity_pack(r["w"], meta)["pack_kg"]
        except Exception:                   # noqa: BLE001
            pack = None
        out.append({**r, "pack_kg": pack})
    return out


def months_back(today: dt.date, months: int) -> dt.date:
    return today - dt.timedelta(days=months * 365 // 12)


def long_rows(ds, today: dt.date, months: int = LONG_MONTHS) -> list[dict]:
    """The foot days of the last `months` (SP-545: 「你一次能爬多少」)."""
    return foot_days(ds, months_back(today, months), today + dt.timedelta(days=1))


# ---------------------------------------------------------------------------
# the ability window (SP-545 R1): 6 months, further back only while the training was normal
# ---------------------------------------------------------------------------

def interruptions(rows: list[dict], today: dt.date, injuries=()) -> list[dict]:
    """停練 / 減量 / 傷停 in `rows` (foot days, NORMAL_WEEKS before the span looked at included) and the
    傷病紀錄 `injuries`, oldest first: {kind: "break" | "low" | "injury", start, end, days | weeks | label}."""
    from backend.engine import race_feasibility as F
    out = []
    dates = sorted({r["date"] for r in rows or () if (r.get("hours") or 0.0) > 0 or (r.get("km") or 0.0) > 0})
    for a, b in zip(dates, dates[1:]):
        gap = (b - a).days - 1
        if gap >= BREAK_DAYS:
            out.append({"kind": "break", "start": a + dt.timedelta(days=1), "end": b - dt.timedelta(days=1), "days": gap})
    if dates and (today - dates[-1]).days >= BREAK_DAYS:      # the current gap: off since the last one
        out.append({"kind": "break", "start": dates[-1] + dt.timedelta(days=1), "end": today,
                    "days": (today - dates[-1]).days})
    if dates:
        hours: dict = {}
        for r in rows:
            m = F.monday_of(r["date"])
            hours[m] = hours.get(m, 0.0) + (r.get("hours") or 0.0)
        this = F.monday_of(today)
        mons, m = [], F.monday_of(dates[0])
        while m < this:                                         # full weeks only
            mons.append(m)
            m += dt.timedelta(weeks=1)
        h = [hours.get(m, 0.0) for m in mons]
        i = NORMAL_WEEKS
        while i + LOW_WEEKS <= len(h):
            base = sorted(h[i - NORMAL_WEEKS:i])[NORMAL_WEEKS // 2 - 1:NORMAL_WEEKS // 2 + 1]
            base = sum(base) / len(base)                        # the median of the 12 weeks before
            j = i
            while j < len(h) and base > 0 and h[j] < LOW_SHARE * base:
                j += 1
            if j - i >= LOW_WEEKS:
                # the weeks of a 停練 are said by the break; only ≥ LOW_WEEKS reduced weeks after it are 減量 too
                k = i
                while k < j and any(x["start"] <= mons[k] + dt.timedelta(days=6) and mons[k] <= x["end"]
                                    for x in out if x["kind"] == "break"):
                    k += 1
                if j - k >= LOW_WEEKS:
                    out.append({"kind": "low", "start": mons[k], "end": mons[j - 1] + dt.timedelta(days=6),
                                "weeks": j - k, "normal_h": round(base, 1)})
                i = j
            else:
                i += 1
    for ev in injuries or ():
        if ev.get("category") == "illness" or ev.get("severity") not in INJ_SEVERITIES:
            continue
        try:
            from backend.engine import injuries as INJ
            o = INJ._d(ev.get("onset_date"))
            if o is None or o > today:
                continue
            from backend.i18n import current_locale
            label = (INJ.full_label(ev.get("area"), ev.get("side")) if current_locale() == "zh-TW" else  # zh only
                     " ".join(str(x).replace("_", " / ") for x in (ev.get("side"), ev.get("area")) if x) or "injury")
            out.append({"kind": "injury", "start": o, "end": INJ.end_of(ev, today), "label": label})
        except Exception:                   # noqa: BLE001 — a broken record: not an interruption
            continue
    return sorted(out, key=lambda x: (x["end"], x["start"]))


def ability_window(rows: list[dict], today: dt.date, injuries=()) -> dict:
    """「你一次能爬多少」's window: the last BASE_MONTHS; when nothing interrupted them, back to the day
    after the latest interruption, at most LONG_MONTHS. `rows`: the foot days of the last LONG_MONTHS +
    NORMAL_WEEKS. {"since", "months", "extended", "stop" (the interruption that ends it, None = none
    within LONG_MONTHS), "rows" (the window's)}."""
    cap, base = months_back(today, LONG_MONTHS), months_back(today, BASE_MONTHS)
    ints = [x for x in interruptions(rows, today, injuries) if x["end"] >= cap]
    recent = [x for x in ints if x["end"] >= base]
    if recent:
        since, stop = base, recent[-1]
    elif ints:
        stop = ints[-1]
        since = max(cap, stop["end"] + dt.timedelta(days=1))
    else:
        since, stop = cap, None
    months = max(1, int(round((today - since).days / (365 / 12))))
    return {"since": since, "months": months, "extended": since < base, "stop": stop,
            "rows": [r for r in rows or () if since <= r["date"] <= today]}


def _stop_text(x: dict) -> str:
    a, b = x["start"].isoformat(), x["end"].isoformat()
    if x["kind"] == "break":
        return _("{a}–{b} 停練 {n} 天", a=a, b=b, n=x["days"])
    if x["kind"] == "low":
        return _("{a}–{b} 連續 {n} 週運動量不到平常的一半", a=a, b=b, n=x["weeks"])
    return _("{a}–{b} 傷停（{label}）", a=a, b=b, label=x["label"])


def window_text(win: Optional[dict]) -> str:
    """How far back 「你一次能爬多少」 looked and why (推估)."""
    if not win:
        return ""
    stop = win.get("stop")
    if not win.get("extended"):
        return _("「一天最多」看近 {m} 個月：{why}，不往前延伸（推估）", m=win["months"],
                 why=_stop_text(stop) if stop else _("近 {m} 個月內有中斷", m=BASE_MONTHS))
    if stop:
        return _("「一天最多」看近 {m} 個月（{d} 起）：訓練照常，往前延伸到上一次中斷之後（{why}）（推估）",
                 m=win["months"], d=win["since"].isoformat(), why=_stop_text(stop))
    return _("「一天最多」看近 {m} 個月：這段期間訓練照常，沒有停練、減量或傷停（推估）", m=win["months"])


def window_json(win: Optional[dict]) -> Optional[dict]:
    if not win:
        return None
    st = win.get("stop")
    return {"since": win["since"].isoformat(), "months": win["months"], "extended": bool(win["extended"]),
            "stop": None if not st else {k: _round(v) for k, v in st.items()}}


def long_best(rows: list[dict]) -> Optional[dict]:
    """The biggest single-day climb and the longest single day of `rows`: {climb_m, climb_date, hours,
    hours_date}; None without any climb."""
    rows = [r for r in rows or () if (r.get("climb_m") or 0.0) > 0]
    if not rows:
        return None
    c = max(rows, key=lambda r: r["climb_m"])
    h = max(rows, key=lambda r: r.get("hours") or 0.0)
    return {"climb_m": float(c["climb_m"]), "climb_date": c["date"], "hours": float(h.get("hours") or 0.0),
            "hours_date": h["date"]}


def ability_best(ds, today: dt.date, injuries=None) -> Optional[dict]:
    """long_best of the ability window (ability_window over the foot days of `ds`, the 傷病紀錄 read when
    `injuries` is None), with its "window"; None without any climb in it."""
    if injuries is None:
        try:
            from backend.engine import injuries as INJ
            injuries = INJ.load_events()
        except Exception:                   # noqa: BLE001
            injuries = []
    rows = foot_days(ds, months_back(today, LONG_MONTHS) - dt.timedelta(weeks=NORMAL_WEEKS),
                     today + dt.timedelta(days=1))
    win = ability_window(rows, today, injuries)
    b = long_best(win["rows"])
    return None if b is None else {**b, "window": {k: v for k, v in win.items() if k != "rows"}}


# ---------------------------------------------------------------------------
# how steep: the uphill average grade of a day (SP-545)
# ---------------------------------------------------------------------------

def uphill_grade(d_m, z_m, step: float = GRADE_STEP_M) -> Optional[float]:
    """The average grade of the climbing parts: the elevation resampled every `step` m of distance, the
    steps climbing ≥ UPHILL_MIN kept (flats and descents out), their climb ÷ their distance. None
    without a climbing step."""
    import numpy as np
    d = np.asarray(d_m, float)
    z = np.asarray(z_m, float)
    ok = np.isfinite(d) & np.isfinite(z)
    if ok.sum() < 2:
        return None
    d, z = np.maximum.accumulate(d[ok]), z[ok]
    if d[-1] - d[0] < step:
        return None
    grid = np.arange(d[0], d[-1] + 1e-9, step)
    dz = np.diff(np.interp(grid, d, z))
    up = dz >= UPHILL_MIN * step
    if not up.any():
        return None
    return float(dz[up].sum() / (step * up.sum()))


def row_grade(ds, r: dict) -> Optional[float]:
    """uphill_grade of a foot day (its calendar day's samples for a multi-day activity); None = no
    distance / elevation."""
    from backend.engine.racepower import athlete as A
    w = r.get("w")
    if w is None:
        return None
    try:
        a = A.activity_arrays(ds, w)
        if a is None:
            return None
        if r.get("day_no"):
            m = dict(A._day_slices(w, a)).get(r["day_no"])
            if m is None:
                return None
            return uphill_grade(a["d"][m], a["z"][m])
        return uphill_grade(a["d"], a["z"])
    except Exception:                       # noqa: BLE001 — a broken file: not steep
        return None


def add_grades(ds, rows: list[dict], need_m: float) -> None:
    """Each row climbing ≥ `need_m` gets its "grade" (only those can be a simulation: the streams are
    read for a few days, not all)."""
    for r in rows or ():
        if "grade" not in r and (r.get("climb_m") or 0.0) >= need_m and r.get("w") is not None:
            r["grade"] = row_grade(ds, r)


def is_steep(r: dict) -> bool:
    g = r.get("grade")
    return g is not None and g >= STEEP_GRADE - 1e-9


# ---------------------------------------------------------------------------
# the simulations
# ---------------------------------------------------------------------------

def sims_done(rows: list[dict], e, need_m: float, today: dt.date) -> dict:
    """The simulations done since trip − SIM_WEEKS: {"done": [dates], "steep": [dates] (of done: no pack,
    steep enough), "no_pack": [dates] (climb enough, neither a pack nor steep), "since", "pack_min"}."""
    since = e.start - dt.timedelta(weeks=SIM_WEEKS)
    pack_min = SIM_PACK_SHARE * float(e.pack)
    done, steep, no_pack = [], [], []
    for r in rows:
        if not (since <= r["date"] <= today) or r["climb_m"] < need_m:
            continue
        if (r.get("pack_kg") or 0.0) >= pack_min:
            done.append(r["date"])
        elif is_steep(r):
            done.append(r["date"])
            steep.append(r["date"])
        else:
            no_pack.append(r["date"])
    return {"done": sorted(done), "steep": sorted(steep), "no_pack": sorted(no_pack), "since": since,
            "pack_min": pack_min}


def projected(best_m: float, need_m: float, today: dt.date, start: dt.date, done: tuple = ()) -> dict:
    """The simulations still possible inside [start − SIM_WEEKS, start − SIM_LAST_DAYS] (a week's
    Saturday). `best_m` ≥ `need_m` (SP-545): every weekend not in a recovery week (every 4th from next
    week) is one, this week's too while its Saturday is ahead and nothing in `done` falls in it. Below
    it the climb grows ≤ +10 % a week from next week (a recovery week holds and has none). {"n",
    "first", "peak_m", "every"}."""
    from backend.engine import race_feasibility as F
    lo = start - dt.timedelta(weeks=SIM_WEEKS)
    hi = start - dt.timedelta(days=SIM_LAST_DAYS)
    climb, n, first = float(best_m), 0, None
    every = climb >= need_m
    mon = F.monday_of(today)
    sat = mon + dt.timedelta(days=5)
    if every and today <= sat and lo <= sat <= hi and not any(mon <= d <= mon + dt.timedelta(days=6) for d in done):
        n, first = 1, sat
    for w in F.weeks_ahead(today, start):
        if w["recovery"]:
            continue
        if not every:
            climb *= SIM_STEP
        sat = w["monday"] + dt.timedelta(days=5)
        if lo <= sat <= hi and climb >= need_m:
            n += 1
            first = first or sat
    return {"n": n, "first": first, "peak_m": climb, "every": every}


# ---------------------------------------------------------------------------
# how hard (SP-545)
# ---------------------------------------------------------------------------

def effort(sd: dict, best: Optional[dict], vam: Optional[dict], turnaround: bool) -> dict:
    """How hard the summit day will feel with today's ability: {"key" (difficulty.LABELS' key, None =
    nothing to compare), "pct", "driver", "items": [{id, pct, counted, …}]}. The hardest counted item
    wins; the climb rate counts only with a turnaround (`turnaround`)."""
    from backend.engine.racepower import difficulty as DF
    items = []
    if best and best.get("climb_m", 0.0) > 0:
        items.append({"id": "climb", "pct": sd["climb_m"] / best["climb_m"], "need": sd["climb_m"],
                      "mine": best["climb_m"], "date": best["climb_date"], "counted": True})
    if best and best.get("hours", 0.0) > 0 and sd.get("hours", 0.0) > 0:
        items.append({"id": "hours", "pct": sd["hours"] / best["hours"], "need": sd["hours"],
                      "mine": best["hours"], "date": best["hours_date"], "counted": True})
    if vam and vam.get("need") and vam.get("hard"):
        items.append({"id": "vam", "pct": float(vam["need"]) / float(vam["hard"]), "need": float(vam["need"]),
                      "mine": float(vam["hard"]), "steady": vam.get("steady"), "counted": bool(turnaround)})
    for it in items:
        it["key"] = DF.label_of(it["pct"])["key"]
    counted = [it for it in items if it["counted"]]
    if not counted:
        return {"key": None, "pct": None, "driver": None, "items": items}
    top = max(counted, key=lambda it: it["pct"])
    return {"key": top["key"], "pct": top["pct"], "driver": top["id"], "items": items}


def _months(best: Optional[dict]) -> int:
    """The ability window's months (ability_window), BASE_MONTHS when `best` doesn't say."""
    return int(((best or {}).get("window") or {}).get("months") or BASE_MONTHS)


def _item_text(it: dict, m: int = BASE_MONTHS) -> str:
    lab = _(EFFORT_LABEL[it["key"]])
    p = it["pct"] * 100
    if it["id"] == "climb":
        return _("攻頂日爬升 {need:.0f} m＝你近 {m} 個月一天最多 {best:.0f} m（{date}）的 {p:.0f}%（{lab}）",
                 need=it["need"], m=m, best=it["mine"], date=it["date"].isoformat(), p=p, lab=lab)
    if it["id"] == "hours":
        return _("攻頂日約 {h:.1f} 小時＝你近 {m} 個月最長一天 {best:.1f} 小時的 {p:.0f}%（{lab}）",
                 h=it["need"], m=m, best=it["mine"], p=p, lab=lab)
    if it["counted"]:
        return _("撤退時間要每小時爬 {need:.0f} m＝你走得辛苦 {hard:.0f} m/h 的 {p:.0f}%（{lab}）",
                 need=it["need"], hard=it["mine"], p=p, lab=lab)
    return _("爬升速度只當參考、不算進吃力程度：沒有撤退時間，每小時 {need:.0f} m 是照預估時間反推的",
             need=it["need"])


def _level(checks: list, order: tuple) -> str:
    lv = "unknown"
    for c in checks:
        x = c["level"]
        if x == "unknown":
            continue
        lv = x if lv == "unknown" or order.index(x) > order.index(lv) else lv
    return lv


def _round(v):
    if isinstance(v, dt.date):
        return v.isoformat()
    return round(v, 3) if isinstance(v, float) else v


def feasibility(r: dict, e, line: Optional[dict], today: dt.date, rows: list[dict],
                best: Optional[dict] = None) -> None:
    """The race check `r` (race_feasibility.assess) of a multi-day 百岳, in place: the weekly checks
    out; how hard the summit day will feel (effort) and the summit-day simulations (a suggestion) in;
    the late / turnaround checks and their advice kept. `best`: long_best of the ability window, its
    "window" (ability_window) included (None = of `rows`)."""
    from backend.engine import race_feasibility as F
    late_sugg = {_("建議改成 B 或 C 賽，用現在的體能去跑就好，不要臨時猛加量"), _("用現在的體能去跑就好，不要臨時猛加量")}
    cut_sugg = _("預估還沒到山頂就得撤退：這座百岳可能還不適合現在的你。可以換短一點的路線、多排一天，或先不去")
    keep = late_sugg | {cut_sugg}
    r["checks"] = [c for c in r["checks"] if c["id"] not in DROP_FEAS]
    r["suggestions"] = [s for s in r["suggestions"] if s in keep]
    step_over = False
    for c in r["checks"]:
        if c["id"] == "step" and c["level"] == "over":
            # 跨級 kept (SP-112) as a detail, at most yellow (SP-545: red only for the real danger)
            c["level"], c["label"] = "tight", _(F.LEVEL_LABEL["tight"])
            step_over = True
        elif c["id"] in DETAIL_OK and c["level"] == "ok":
            c["label"] = _(OK_LABEL)        # SP-545: no 「來得及」 on a 百岳's card
    for k in ("downgrade", "koop", "milestone"):     # milestone (SP-284): the weekly volume isn't judged here
        r.pop(k, None)
    r["src"] = [_(SRC_EFFORT), _(SRC_SUMMIT)] + [s for s in r.get("src") or () if s == _(F.SRC_VAM)]
    sd = summit_day(line) if line else None
    advice = []
    if sd is None:
        r["checks"].insert(0, {"id": "effort", "level": "unknown", "label": _(F.LEVEL_LABEL["unknown"]),
                               "text": _("沒有每天的距離和爬升，找不到攻頂日")})
        r["checks"].append({"id": "summit_sim", "level": "unknown", "label": _(F.LEVEL_LABEL["unknown"]),
                            "text": _("沒有每天的距離和爬升，找不到攻頂日")})
        ef = {"key": None}
    else:
        r["race_day"] = sd
        best = best if best is not None else long_best(rows)
        vam = next((c for c in r["checks"] if c["id"] == "vam"), None)
        ef = effort(sd, best, vam, bool(getattr(e, "cutoff_hours", None)))
        m = _months(best)
        if ef["key"] is None:
            c = {"id": "effort", "level": "unknown", "label": _(F.LEVEL_LABEL["unknown"]),
                 "text": _("近 {m} 個月沒有跑步或健行紀錄，沒辦法估吃力程度", m=m)}
        else:
            lab = _(EFFORT_LABEL[ef["key"]])
            txt = _("以現在的能力，攻頂日（第 {d} 天）預估{lab}：", d=sd["day"], lab=lab)
            txt += _("；").join(_item_text(it, m) for it in ef["items"]) + _("。")
            wt = window_text((best or {}).get("window"))
            txt += (wt + _("。") if wt else "") + _(EFFORT_SCALE)
            c = {"id": "effort", "level": EFFORT_LEVEL[ef["key"]], "label": lab, "text": txt, "key": ef["key"],
                 "pct": round(ef["pct"], 3), "driver": ef["driver"], "driver_name": _(ITEM_NAME[ef["driver"]]),
                 "items": [{k: _round(v) for k, v in it.items()} for it in ef["items"]],
                 "window": window_json((best or {}).get("window"))}
            if ef["key"] in ("hard", "max", "over"):
                advice.append(H.tag("PL9", _("預估{lab}，最吃力的是{what}：{how}", lab=lab, what=_(ITEM_NAME[ef["driver"]]),
                                             how=_(EASIER[ef["driver"]])), cls=2))
        r["checks"].insert(0, c)
        r["checks"].append(_sim_check(r, e, sd, today, rows, best, advice))
    if step_over:
        # a 百岳 has no lower 組別 — a lower-grade route first (a reminder since SP-545)
        advice.append(H.tag("PL10", _("最難那天比你走過最大的一天高了兩級以上：先走一條低一級的路線（攻頂日的距離和爬升少一點）"
                                      "累積經驗，再來這一座"), cls=2))
    r["suggestions"] += advice
    r["level"] = _level([c for c in r["checks"] if c["id"] in LEVEL_FROM], F.LEVELS)
    title = _("預估：{x}", x=_(EFFORT_LABEL[ef["key"]])) if ef.get("key") else None
    r["effort"] = None if title is None else {"key": ef["key"], "pct": round(ef["pct"], 3), "title": title,
                                               "level": EFFORT_LEVEL[ef["key"]]}
    r["label"] = title if title and r["level"] not in ("over", "late") else _(F.LEVEL_LABEL[r["level"]])


def _sim_check(r: dict, e, sd: dict, today: dt.date, rows: list[dict], best: Optional[dict], advice: list) -> dict:
    """The summit-day simulations still possible + done: ok ≥ SIM_NEED, else a suggestion (tight)."""
    from backend.engine import race_feasibility as F
    best_m = (best or {}).get("climb_m") or 0.0
    m = _months(best)
    if best_m <= 0:
        return {"id": "summit_sim", "level": "unknown", "label": _(F.LEVEL_LABEL["unknown"]),
                "text": _("近 {m} 個月沒有跑步或健行紀錄，沒辦法推算", m=m)}
    got = sims_done(rows, e, sd["climb_m"], today)
    pj = projected(best_m, sd["climb_m"], today, e.start, tuple(got["done"]))
    total = len(got["done"]) + pj["n"]
    lv = "ok" if total >= SIM_NEED else "tight"
    if pj["every"]:
        txt = _("攻頂日是第 {d} 天，爬升 {need:.0f} m。你近 {m} 個月一天最多爬過 {best:.0f} m（{date}），已經到得了："
                "行程前 8 週到前 {last} 天，每個週末（恢復週除外）都能排一次攻頂日模擬，排得下 {n} 次（推估）",
                d=sd["day"], need=sd["climb_m"], m=m, best=best_m, date=best["climb_date"].isoformat(),
                last=SIM_LAST_DAYS, n=pj["n"])
    else:
        txt = _("攻頂日是第 {d} 天，爬升 {need:.0f} m。你近 {m} 個月一天最多爬過 {best:.0f} m，比攻頂日少：從這裡每週最多 +10% "
                "慢慢加，行程前排得下 {n} 次攻頂日模擬（推估）", d=sd["day"], need=sd["climb_m"], m=m, best=best_m,
                n=pj["n"])
    if got["done"]:
        txt += _("；已經做了 {n} 次", n=len(got["done"]))
    txt += _("。建議做 {k} 次：一天爬完攻頂日的爬升，背行程的背包，或沒背包但爬坡段平均坡度 ≥ {g:.0f}%",
             k=SIM_NEED, g=STEEP_GRADE * 100)
    if lv == "tight" and r.get("days_to", 0) >= SIM_LAST_DAYS:
        how = (_("週末排一天爬完攻頂日的爬升，背行程的背包，或走夠陡的坡") if pj["every"] else
               _("每週排一次，爬升一次比一次多（每週最多 +10%）；也可以多排一天把攻頂日拆短"))
        advice.append(H.tag("PL9", _("建議行程前做 {k} 次攻頂日模擬，目前排得下 {n} 次：{how}", k=SIM_NEED, n=total, how=how),
                            cls=2))
    return {"id": "summit_sim", "level": lv, "label": _(SIM_LABEL[lv]), "text": txt, "projected": pj["n"],
            "done": len(got["done"]), "steep": len(got["steep"]), "need": SIM_NEED, "need_m": round(sd["climb_m"]),
            "best_m": round(best_m), "every": pj["every"]}


def readiness(rd: dict, e, line: Optional[dict], today: dt.date, rows: list[dict]) -> None:
    """賽事完備程度 `rd` (race_feasibility.readiness) of a multi-day 百岳, in place: the long day and
    the weekly checks out, 「攻頂日模擬做了幾次」 in (a suggestion below SIM_NEED); B2B kept."""
    from backend.engine import race_feasibility as F
    rd["checks"] = [c for c in rd["checks"] if c["id"] not in DROP_READY]
    rd["src"] = [_(SRC_SUMMIT)]
    sd = summit_day(line) if line else None
    if sd is None:
        rd["checks"].insert(0, {"id": "summit_sim", "level": "unknown", "label": _(F.READY_LABEL["unknown"]),
                                "text": _("沒有每天的距離和爬升，找不到攻頂日")})
    else:
        got = sims_done(rows, e, sd["climb_m"], today)
        n = len(got["done"])
        lv = "ok" if n >= SIM_NEED else "tight"
        txt = _("行程前 8 週內做了 {n} 次攻頂日模擬（一天爬升 ≥ {need:.0f} m，背包 ≥ {kg:.1f} kg，或沒背包但爬坡段平均坡度 ≥ {g:.0f}%），"
                "建議 {k} 次", n=n, need=sd["climb_m"], kg=got["pack_min"], g=STEEP_GRADE * 100, k=SIM_NEED)
        if got["steep"]:
            txt += _("；其中 {s} 次沒背包，靠坡夠陡算進來", s=len(got["steep"]))
        if got["no_pack"]:
            txt += _("；另外 {m} 次爬升夠，但沒有記背包（或太輕）、坡度也不到 {g:.0f}%：在活動頁記下那次背多少就會算進來",
                     m=len(got["no_pack"]), g=STEEP_GRADE * 100)
        if today < got["since"]:
            txt += _("（從 {d} 開始算）", d=got["since"].isoformat())
        rd["checks"].insert(0, {"id": "summit_sim", "level": lv,
                                "label": _(F.READY_LABEL["ok"]) if lv == "ok" else _(SIM_LABEL["tight"]), "text": txt,
                                "count": n, "need": SIM_NEED, "no_pack": len(got["no_pack"]), "steep": len(got["steep"])})
    rd["level"] = _level(rd["checks"], F.READY_LEVELS)
    rd["label"] = _(F.READY_LABEL[rd["level"]])


def apply(r: dict, e, line: Optional[dict], today: dt.date, ds, rows: Optional[list[dict]] = None,
          best: Optional[dict] = None) -> None:
    """race_feasibility.races' hook: a multi-day 百岳's feasibility and readiness. `rows`: the foot days of
    the last SIM_WEEKS (sim_rows), `best`: long_best of the ability window (ability_best) — both read from
    `ds` when None (without `ds`, `best` comes from `rows`)."""
    if not applies(e) or r.get("skipped"):
        return
    rows = sim_rows(ds, today) if rows is None else rows
    if best is None and ds is not None:
        try:
            best = ability_best(ds, today)
        except Exception:                   # noqa: BLE001 — the window's rows below
            best = None
    if best is None:
        best = long_best(rows)
    sd = summit_day(line) if line else None
    if sd is not None and ds is not None:
        add_grades(ds, rows, sd["climb_m"])
    feasibility(r, e, line, today, rows, best)
    if r.get("readiness"):
        readiness(r["readiness"], e, line, today, rows)
