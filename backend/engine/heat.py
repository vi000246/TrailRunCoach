"""
Heat acclimation — docs/research/heat-acclimation.md §2.3, §2.4, §4.2–4.4, §5.1.
Pure functions, no I/O (the per-activity weather is read by route_weather).

    S (0–1)   a state variable updated once per day from the day's heat dose:
              exposed:  S_d = S_{d−1} + k_in · dose_d · (1 − S_{d−1})
              no dose:  S_d = S_{d−1} · (1 − r)
    H_eff     = H · (1 − a · S)   (the Hadley penalty that remains; both the
              From and the To side of env.multiplier, each with its own S)
    HRC       ΔHR per 10 Hadley units at a steady flat power, against the
              athlete's cool-day HR ~ power line (an observation that checks
              the S model; never drives the race maths)

Every constant names its source and status (the env.py convention):
[已驗證] read in the paper, [二手] quoted by another paper, [自組] our own
composition, [待驗證] still to be checked. The S model's structure is 自組 —
the page labels S 推估 until two summers cross-validate it (§6.2 point 8).
"""
from __future__ import annotations

import datetime as dt
import math
from statistics import median
from typing import Iterable, Optional, Sequence

# Hadley sum (air °F + dew °F) at and above which a minute counts as a full
# heat minute — route_weather.HOT_HADLEY (Hadley's 151–160 band, ≥ ~4.5 %
# slower), shared so the overview and the routes page flag the same days.
HOT_HADLEY = 150.0
# 130–150 counts linearly 0 → 1 (Taipei summer evenings; Brown 2022 shows
# everyday outdoor activity acclimatises seasonally) [自組]
PARTIAL_HADLEY = 130.0
# ≥ 60 min/day of heat exposure (Racinais et al. 2015 consensus, BJSM
# 49:1164–73, [已驗證·全文] PMC4602249): a full day's dose
MIN_DOSE_MIN = 60.0
# k_in calibrated so 5 consecutive full doses give S = 0.70: Pandolf 1998
# (Int J Sports Med 19 S2:S157–60) 2/3–75 % of the adaptation in 4–6 days
# [已驗證 摘要]; the calibration itself is [自組]
K_IN = 1.0 - 0.3 ** (1.0 / 5.0)
# decay per day without exposure: Daanen, Racinais & Périard 2018 (Sports Med
# 48:409–30, [已驗證·全文] PMC5775394) HR 2.3 %/day, Tc 2.6 %/day; 2.5 % is
# Benjamin 2019's rounding [二手]. Estimated over 7–16 days; beyond that the
# decay is an extrapolation.
DECAY = 0.025
DECAY_RANGE = (0.023, 0.026)
# share of the heat penalty an acclimatised athlete wins back: Racinais,
# Périard, Karlsen & Nybo 2015 (MSSE 47:601–6, [已驗證·全文] PMC4342312) power
# deficit −48 → −11 W after 2 weeks = 77 %; range 0.35 (Lorenzo 2010 on the
# pre-acclimation cool baseline) – 1.0 (Racinais by time). Using it as a
# constant multiplier on Hadley's penalty is [自組].
A_RECOVER = 0.75
A_RANGE = (0.35, 1.0)
MAINTAIN_EVERY_D = 4          # every 3–5 days (Pryor 2019; Benjamin 2022; Sekiguchi 2022) → 4 [自組, in range]
# level cut-offs [自組]: 0.75 ≈ Pandolf's 75 %
LEVELS = (("acclimatised", 0.75, "已適應"), ("partial", 0.35, "部分"), ("none", 0.0, "未適應"))
# fixed choices of the race-calculator selector (heat-acclimation.md §5.5) [自組]
PRESET_S = {"none": 0.0, "partial": 0.5, "acclimatised": 0.9}
# the three parameter sets of §4.4 (optimistic / centre / conservative)
SCENARIOS = {"high": {"a": A_RANGE[1], "rule": "exp", "decay": DECAY_RANGE[0]},
             "center": {"a": A_RECOVER, "rule": "exp", "decay": DECAY},
             "low": {"a": A_RANGE[0], "rule": "day_loss", "decay": None}}
EVIDENCE = "Pandolf 1998；Racinais 2015 共識；Daanen 2018；Racinais 2015 MSSE（a）；模型結構 [自組]"
# The athlete's own heat coefficient of HR at a given power: the heat
# back-test (backend/scripts/heat_backtest.py, 271 running route efforts,
# route FE + power + moving min + time of day + β·(Hadley − 120), OLS) gave
# β = 0.224 ± 0.036 bpm per Hadley unit (docs/spec/racepower.spec.md,
# 2026-10-01; docs/research/unsourced-rules.md §A5). Linear and centred on
# Hadley 120, as it was fitted. The fit is the athlete's own [本人資料]; using
# it to move one run's HR to Hadley 120 is [自組].
HR_BETA = 0.224
HR_BETA_SE = 0.036
HR_BETA_REF = 120.0
HR_BETA_SRC = "本人 HEAT 回測 β 0.224 ± 0.036 bpm／Hadley（271 段路線 effort，docs/spec/racepower.spec.md）"


def hr_heat_adjust(hr: float, hadley: float, beta: float = HR_BETA) -> float:
    """HR moved to the reference heat (Hadley 120): HR − β·(Hadley − 120)."""
    return float(hr) - beta * (float(hadley) - HR_BETA_REF)


def minute_weight(hadley: Optional[float]) -> float:
    """Weight of one minute at this Hadley sum: 0 below 130, 1 from 150."""
    if hadley is None or not math.isfinite(hadley):
        return 0.0
    if hadley >= HOT_HADLEY:
        return 1.0
    if hadley <= PARTIAL_HADLEY:
        return 0.0
    return (hadley - PARTIAL_HADLEY) / (HOT_HADLEY - PARTIAL_HADLEY)


def hadley_sum(temp_c: float, rh_pct: float) -> float:
    from backend.engine.racepower.env import dew_point
    return temp_c * 1.8 + 32.0 + dew_point(temp_c, rh_pct)["dew_f"]


def day_dose(hot_minutes: float, passive_done: bool = False) -> float:
    """dose_d = min(1, heat minutes / 60) + 1 for a completed passive session
    (hot bath: Zurawlew 2016; sauna: Scoon 2007), capped at 1."""
    d = min(1.0, max(0.0, float(hot_minutes or 0.0)) / MIN_DOSE_MIN)
    if passive_done:
        d += 1.0
    return min(1.0, d)


def _step(s: float, dose: float, rule: str, decay: Optional[float]) -> float:
    if dose > 0:
        return s + K_IN * min(1.0, dose) * (1.0 - s)
    if rule == "day_loss":
        # "1 day of HA is lost following 2 days of HAD" (Daanen 2018): S as
        # equivalent exposure days n (S = 1 − (1 − k)^n), n − 0.5 per day off
        if s <= 0:
            return 0.0
        n = math.log(1.0 - min(s, 0.999999)) / math.log(1.0 - K_IN)
        n = max(0.0, n - 0.5)
        return 1.0 - (1.0 - K_IN) ** n
    return s * (1.0 - (DECAY if decay is None else decay))


def status_series(doses: dict, start: dt.date, end: dt.date, s0: float = 0.0,
                  decay: float = DECAY, rule: str = "exp") -> list[tuple[dt.date, float]]:
    """S for every day from `start` to `end` inclusive; `doses` = {date: dose}."""
    out, s, d = [], float(s0), start
    while d <= end:
        s = _step(s, float(doses.get(d, 0.0) or 0.0), rule, decay)
        out.append((d, s))
        d += dt.timedelta(days=1)
    return out


def project(s0: float, today: dt.date, race_day: dt.date, planned: Optional[dict] = None) -> dict:
    """S on race day from today's S0 plus the planned heat sessions
    ({date: dose}), under the three parameter sets of §4.4. Returns
    {"center", "low", "high"} (S) and the matching a."""
    planned = planned or {}
    out = {}
    for name, p in SCENARIOS.items():
        if race_day <= today:
            s = s0
        else:
            ser = status_series(planned, today + dt.timedelta(days=1), race_day, s0,
                                p["decay"] or DECAY, p["rule"])
            s = ser[-1][1] if ser else s0
        out[name] = s
    out["a"] = {k: v["a"] for k, v in SCENARIOS.items()}
    return out


def effective_heat(h_pct: float, s: float, a: float = A_RECOVER) -> float:
    """H_eff = H · (1 − a·S), never below 0 (a ≤ 1, S ≤ 1)."""
    s = min(1.0, max(0.0, float(s or 0.0)))
    return max(0.0, float(h_pct) * (1.0 - a * s))


def scale(s: float, a: float = A_RECOVER) -> float:
    """The share of the unacclimatised penalty that remains (the `scale` of
    baiyue-from-running.md §8.1's status object)."""
    return 1.0 - a * min(1.0, max(0.0, float(s or 0.0)))


def level(s: float) -> dict:
    for key, cut, zh in LEVELS:
        if s >= cut:
            return {"id": key, "label": zh}
    return {"id": "none", "label": "未適應"}


def race_is_hot(temp_c: Optional[float], rh_pct: Optional[float]) -> bool:
    """Race-day Hadley sum above HOT_HADLEY."""
    if temp_c is None or rh_pct is None:
        return False
    return hadley_sum(temp_c, rh_pct) > HOT_HADLEY


def doses_from(activities: Iterable[dict], passive_dates: Iterable = ()) -> dict:
    """{date: dose} from per-activity exposure rows ({"date", "hot_min"}) and
    completed passive heat sessions (dates)."""
    mins: dict = {}
    for a in activities:
        d = a.get("date")
        if isinstance(d, str):
            d = dt.date.fromisoformat(d[:10])
        if d is None:
            continue
        mins[d] = mins.get(d, 0.0) + float(a.get("hot_min") or 0.0)
    passive = {dt.date.fromisoformat(p[:10]) if isinstance(p, str) else p for p in passive_dates}
    return {d: day_dose(mins.get(d, 0.0), d in passive) for d in set(mins) | passive}


def current(activities: Sequence[dict], today: dt.date, passive_dates: Iterable = (),
            history_days: int = 365) -> dict:
    """Today's S from the exposure history (S = 0 `history_days` ago), the
    series and a short verdict."""
    doses = doses_from(activities, passive_dates)
    start = today - dt.timedelta(days=history_days)
    ser = status_series(doses, start, today)
    s = ser[-1][1] if ser else 0.0
    last14 = [d for d in doses if today - dt.timedelta(days=13) <= d <= today and doses[d] > 0]
    last = max((d for d in doses if d <= today and doses[d] > 0), default=None)
    return {"s": s, "level": level(s), "series": ser, "doses": doses, "days_14": len(last14),
            "last_exposure": last.isoformat() if last else None,
            "since_last_d": (today - last).days if last else None}


def mean_s(series: Sequence[tuple], lo: dt.date, hi: dt.date) -> Optional[float]:
    v = [s for d, s in series if lo <= d <= hi]
    return sum(v) / len(v) if v else None


# ---- HRC: HR cost of heat at a steady power (§2.3, an observation) ------------

HRC_BASE_DAYS = 42           # [自組] keeps up with fitness
HRC_COOL, HRC_HOT = 120.0, 150.0


def _fit_line(pts):
    n = len(pts)
    mx = sum(p for p, _ in pts) / n
    my = sum(h for _, h in pts) / n
    sxx = sum((p - mx) ** 2 for p, _ in pts)
    if sxx <= 0:
        return None
    b = sum((p - mx) * (h - my) for p, h in pts) / sxx
    return my - b * mx, b


def hr_cost(segments: Sequence[dict], window_days: int = HRC_BASE_DAYS) -> dict:
    """segments = [{"date", "p", "hr", "hadley"}] — steady flat stretches (≥ 10
    min, power CV < 10 %, after the first 10 min of the run). For every hot
    segment (Hadley > 150): the cool-day line HR = b0 + b1·P over the
    previous `window_days` (Hadley < 120, ≥ 5 segments), ΔHR = HR − line, and
    HRC = ΔHR / ((Hadley − 120)/10) bpm per 10 Hadley units. Returns the rows
    and the noise level (SD of the cool-day residuals)."""
    rows, noise = [], []
    segs = sorted((s for s in segments if s.get("hadley") is not None), key=lambda s: str(s["date"]))

    def dd(s):
        return s["date"] if isinstance(s["date"], dt.date) else dt.date.fromisoformat(str(s["date"])[:10])
    for s in segs:
        if s["hadley"] <= HRC_HOT:
            continue
        d = dd(s)
        base = [(c["p"], c["hr"]) for c in segs if c["hadley"] < HRC_COOL and d - dt.timedelta(days=window_days) <= dd(c) < d]
        if len(base) < 5:
            continue
        f = _fit_line(base)
        if not f:
            continue
        b0, b1 = f
        res = [h - (b0 + b1 * p) for p, h in base]
        noise.append(math.sqrt(sum(r * r for r in res) / max(1, len(res) - 2)))
        dhr = s["hr"] - (b0 + b1 * s["p"])
        rows.append({"date": d.isoformat(), "hadley": s["hadley"], "d_hr": dhr,
                     "hrc": dhr / ((s["hadley"] - HRC_COOL) / 10.0)})
    return {"rows": rows, "noise_bpm": median(noise) if noise else None, "window_days": window_days}


def hrc_trend(rows: Sequence[dict], today: dt.date) -> dict:
    """Median HRC of the last 4 weeks against the 4 weeks before."""
    def med(lo, hi):
        v = [r["hrc"] for r in rows if lo <= dt.date.fromisoformat(r["date"]) <= hi]
        return (median(v) if v else None), len(v)
    a, na = med(today - dt.timedelta(days=27), today)
    b, nb = med(today - dt.timedelta(days=55), today - dt.timedelta(days=28))
    return {"recent": a, "n_recent": na, "before": b, "n_before": nb}
