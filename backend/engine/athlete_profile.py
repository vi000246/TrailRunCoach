"""
一般設定 (generalize-athlete plan §2, batch B2): the few things a runner knows
and the data cannot tell — body (sex, height, birth year, dated weights) and
the power source — plus what can be pre-filled from the data.

First-run 精靈 (static/setup_wizard.js, SP-211): asks until the weight, the sex and the age
are known — what the calculations are calibrated with and the data cannot tell: weight → W/kg,
RE, the race calculator, the pack default; sex → the W′ prior and energy; age → energy and the
220 − age walk cap without a max HR (docs/research/cold-start.md §1.3: the wizard asked weight /
sex only; §4.3: age is the max-HR fallback). The age is asked, the birth year stored (an age
goes stale; `age()` = this year − birth year, the birthday itself is not asked).

Stored in the season plan's `profile` (engine/planning.py, ~/.wko5coach/plan.json):

    sex          male | female
    height_cm    100–250
    birth_year   1920 … this year − 10
    power_source stryd | watch | none   (S2; the old `power_meter` field —
                 stryd / coros / garmin / other — reads as stryd / watch)

Power source → models (§4 owner decision): only Stryd power feeds the
power models; a runner without Stryd is coached by heart rate. SP-950 (owner
2026-10-27): watch-estimated power never counts — a stored 「手錶推估功率」 reads
as none and 進階 「採用手錶推估功率」 (power.accept_watch_power) is ignored (the
stored values stay). 「使用功率」 (charts.power.enabled, None = auto) follows
from it: on for Stryd. Power in effect also needs a fresh CP: engine/power_state.py.

Detection (pre-fill, `detect_power_source`): the last 90 days of runs by
engine/power_source.py's per-run label — ≥ 5 Stryd runs → stryd, else none
(thresholds 推估, as plan S1; the watch count is still reported).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.i18n import N_, _

POWER_SOURCES = ("stryd", "watch", "none")
# msgids (zh-TW): shown through power_labels() / _() so they follow the UI language (SP-233)
POWER_LABEL = {"stryd": "Stryd", "watch": N_("手錶推估功率"), "none": N_("沒有功率計")}
LEGACY_METER = {"stryd": "stryd", "coros": "watch", "garmin": "watch", "other": "watch"}
DETECT_DAYS = 90
DETECT_MIN_RUNS = 5               # 推估 (plan S1: ≥ 5 Stryd runs in 90 days)
BIRTH_YEAR_MIN = 1920
AGE_MIN, AGE_MAX = 10, 100            # the age the 精靈 / 設定頁 accept (birth year: this year − age)
SETUP_FIELDS = ("weight", "sex", "age")
SETUP_DONE_KEY = "athlete.setup.done"
SETUP_LATER_KEY = "athlete.setup.later_at"
# 「稍後再說」 skips the 精靈 for a week, then it asks again while something is still missing
# (owner 2026-10-06: skippable, reminded periodically — also who dismissed it before; 推估)
REMIND_DAYS = 7


def birth_year_ok(y: Optional[int], today: Optional[dt.date] = None) -> bool:
    today = today or dt.date.today()
    return y is None or (isinstance(y, int) and not isinstance(y, bool) and BIRTH_YEAR_MIN <= y <= today.year - 10)


def age(profile: dict, today: Optional[dt.date] = None) -> Optional[int]:
    """Age this year from the birth year (the birthday itself is not asked)."""
    y = (profile or {}).get("birth_year")
    if not isinstance(y, int):
        return None
    return (today or dt.date.today()).year - y


def birth_year_of_age(age: int, today: Optional[dt.date] = None) -> int:
    """The birth year stored for an age typed in (the age reached this year)."""
    return (today or dt.date.today()).year - int(age)


def age_ok(a: Optional[int]) -> bool:
    return a is None or (isinstance(a, int) and not isinstance(a, bool) and AGE_MIN <= a <= AGE_MAX)


def power_labels() -> dict:
    """POWER_LABEL in the request's language (the 精靈 / 設定頁 「偵測到：…」, SP-233)."""
    return {k: v if k == "stryd" else _(v) for k, v in POWER_LABEL.items()}   # Stryd: a brand name


def profile_power_source(profile: dict) -> Optional[str]:
    """The power source the runner entered (power_source, else the legacy power_meter).
    SP-950 (owner 2026-10-27): watch power is no power source — a stored 「手錶推估功率」 (and the legacy
    coros / garmin / other meter) reads as none. Read-time only: the stored value is not changed."""
    p = profile or {}
    s = p["power_source"] if p.get("power_source") in POWER_SOURCES else LEGACY_METER.get(p.get("power_meter"))
    return "none" if s == "watch" else s


def detect_power_source(ds, today: Optional[dt.date] = None, days: int = DETECT_DAYS) -> dict:
    """{"source", "counts": {stryd, watch, none}, "days"} over the last `days`
    of runs. Memoised on the Dataset (its workouts don't change)."""
    today = today or dt.date.today()
    memo = getattr(ds, "memo", None)
    mk = ("athlete_profile.power", today.isoformat(), days)
    if isinstance(memo, dict) and mk in memo:
        return memo[mk]
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    counts = {"stryd": 0, "watch": 0, "none": 0}
    label = getattr(ds, "power_source", None)
    for w in getattr(ds, "workouts", []):
        if w.sport != "run" or not (tday - days < math.floor(w.day) <= tday):
            continue
        try:
            s = label(w) if label is not None else "none"
        except Exception:               # noqa: BLE001 — one unreadable file
            s = "none"
        counts[s if s in counts else "none"] += 1
    # SP-950 (owner 2026-10-27): watch power is no power meter — only Stryd counts
    src = "stryd" if counts["stryd"] >= DETECT_MIN_RUNS else "none"
    out = {"source": src, "counts": counts, "days": days}
    if isinstance(memo, dict):
        memo[mk] = out
    return out


def effective_power_source(profile: dict, ds=None, today: Optional[dt.date] = None) -> tuple[str, str]:
    """(source, how): the entered one (「設定」), else detected (「自動偵測」)."""
    s = profile_power_source(profile)
    if s is not None:
        return s, N_("設定")
    if ds is None:
        return "none", N_("預設")
    return detect_power_source(ds, today)["source"], N_("自動偵測")


def use_power(source: str, accept_watch_power: bool = False) -> bool:
    """「使用功率」 in auto: Stryd yes; none no. SP-950 (owner 2026-10-27): watch power never counts —
    `accept_watch_power` is ignored (kept for the callers' signature)."""
    return source == "stryd"


def setup_missing(weight: Optional[float], sex: Optional[str], birth_year: Optional[int]) -> list[str]:
    """Which of weight / sex / age (SETUP_FIELDS) the app does not know yet (any source)."""
    have = {"weight": weight is not None, "sex": sex in ("male", "female"), "age": isinstance(birth_year, int)}
    return [k for k in SETUP_FIELDS if not have[k]]


def setup_needed(weight: Optional[float], sex: Optional[str], birth_year: Optional[int]) -> bool:
    """The first-run 精靈 asks until the weight, the sex and the age are known (SP-211)."""
    return bool(setup_missing(weight, sex, birth_year))


def setup_remind(needed: bool, later_at: Optional[str], now: Optional[dt.datetime] = None) -> bool:
    """Show the 精靈 now: something is missing and 「稍後再說」 was not pressed in the last
    REMIND_DAYS. The old `athlete.setup.done` no longer silences it for good: a runner who
    dismissed it before is asked again (owner 2026-10-06)."""
    if not needed:
        return False
    if not later_at:
        return True
    try:
        t = dt.datetime.fromisoformat(later_at)
    except (TypeError, ValueError):
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return now - t >= dt.timedelta(days=REMIND_DAYS)
