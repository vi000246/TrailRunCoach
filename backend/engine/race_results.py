"""
比賽成績 — one list of the runner's race results, shared by every reader (SP-290).

The 跑步經驗問卷 (engine/experience.py, the first-run 精靈 / 設定 → 個人資料) asks 「最近一年有沒有
比賽成績？（距離、時間、日期）」 and stores the answer HERE, not inside the questionnaire, so the
readers that come later — 用近期比賽成績推完賽時間 (SP-293, Riegel) and 從比賽成績算 E 配速
(SP-276, VDOT) — read the same rows instead of each keeping its own copy (SP-290 驗收).

Stored in the settings store `athlete.race_results` (settings/repository.py) as a list, newest
first after `normalise`:

    date         YYYY-MM-DD, not in the future
    distance_km  0.4 – 1000
    time_s       60 s – 14 days (an integer)
    trail        True for a trail / mountain race (climbing distorts the time: SP-276 leaves
                 them out of VDOT; SP-293 treats them apart)
    source       survey (the questionnaire's one entry) | manual | activity (recognised from an
                 activity, threshold_confidence.RACE_WORDS — SP-276)
    confirmed    False for an activity the runner has not confirmed yet (SP-276: unconfirmed
                 races are not used); True otherwise
    name         optional, ≤ 80 characters

Writers: the questionnaire (source survey, at most one; saving it again replaces it) and the
設定 block 「比賽成績（E 配速）」 (engine/e_pace.with_race: source manual, or activity for a run
confirmed with 「用這場」; owner 2026-10-06 — SP-276's own single race, `athlete.race_result`,
is migrated into this list once at start-up and retired). The E pace reads the newest
confirmed road row (e_pace.pick).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

KEY = "athlete.race_results"
SOURCES = ("survey", "manual", "activity")
DIST_KM = (0.4, 1000.0)
TIME_S = (60, 14 * 24 * 3600)
NAME_MAX = 80
MAX_ROWS = 50
RECENT_DAYS = 365           # the questionnaire asks for 「最近一年」


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_entry(e: Any, today: Optional[dt.date] = None) -> None:
    """ValueError when `e` is not a race-result row (see the module docstring)."""
    if not isinstance(e, dict):
        raise ValueError("a race result must be an object")
    extra = set(e) - {"date", "distance_km", "time_s", "trail", "source", "confirmed", "name"}
    if extra:
        raise ValueError(f"unknown race-result fields {sorted(extra)}")
    try:
        d = dt.date.fromisoformat(str(e.get("date")))
    except ValueError:
        raise ValueError("race date must be YYYY-MM-DD") from None
    if d > (today or dt.date.today()) + dt.timedelta(days=1):       # a day of slack for time zones
        raise ValueError("race date is in the future")
    km = e.get("distance_km")
    if not _num(km) or not DIST_KM[0] <= km <= DIST_KM[1]:
        raise ValueError(f"race distance must be {DIST_KM[0]:g}–{DIST_KM[1]:g} km")
    t = e.get("time_s")
    if not isinstance(t, int) or isinstance(t, bool) or not TIME_S[0] <= t <= TIME_S[1]:
        raise ValueError("race time must be a whole number of seconds (1 min – 14 days)")
    if not isinstance(e.get("trail", False), bool) or not isinstance(e.get("confirmed", True), bool):
        raise ValueError("trail / confirmed must be true or false")
    if e.get("source", "manual") not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    n = e.get("name")
    if n is not None and (not isinstance(n, str) or len(n) > NAME_MAX):
        raise ValueError(f"name must be text of at most {NAME_MAX} characters")


def validate(rows: Any) -> None:
    """The settings value: a list of rows, at most one from the questionnaire."""
    if not isinstance(rows, list):
        raise ValueError("race results must be a list")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"at most {MAX_ROWS} race results")
    for r in rows:
        validate_entry(r)
    if sum(1 for r in rows if r.get("source") == "survey") > 1:
        raise ValueError("only one race result can come from the questionnaire")


def make(distance_km: float, time_s: int, date: str, trail: bool = False, source: str = "manual",
         name: Optional[str] = None, confirmed: bool = True) -> dict:
    """A row in the shared shape (validated)."""
    e = {"date": str(date)[:10], "distance_km": round(float(distance_km), 3), "time_s": int(time_s),
         "trail": bool(trail), "source": source, "confirmed": bool(confirmed)}
    if name:
        e["name"] = str(name)
    validate_entry(e)
    return e


def parse_time(s: Any) -> int:
    """「h:mm:ss」 / 「mm:ss」 / seconds → seconds; ValueError otherwise."""
    if _num(s):
        return int(round(float(s)))
    parts = str(s or "").strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
        raise ValueError("time must be h:mm:ss or mm:ss")
    v = [int(p) for p in parts]
    if any(x >= 60 for x in v[1:]):
        raise ValueError("minutes and seconds must be below 60")
    return v[0] * 3600 + v[1] * 60 + v[2] if len(v) == 3 else v[0] * 60 + v[1]


def fmt_time(s: int) -> str:
    h, r = divmod(int(s), 3600)
    return f"{h}:{r // 60:02d}:{r % 60:02d}"


def normalise(rows: Optional[list]) -> list:
    """Valid rows only, newest first (a bad stored row is dropped, never raised)."""
    out = []
    for r in rows or ():
        try:
            validate_entry(r)
        except ValueError:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r["date"], reverse=True)


def survey_entry(rows: Optional[list]) -> Optional[dict]:
    """The questionnaire's row, None without one."""
    return next((r for r in normalise(rows) if r.get("source") == "survey"), None)


def with_survey(rows: Optional[list], entry: Optional[dict]) -> list:
    """`rows` with the questionnaire's row replaced by `entry` (None = removed)."""
    keep = [r for r in normalise(rows) if r.get("source") != "survey"]
    return normalise(keep + ([entry] if entry else []))


def usable(rows: Optional[list], today: dt.date, days: int = RECENT_DAYS, trail: Optional[bool] = None) -> list:
    """The confirmed rows of the last `days` (newest first); `trail` False / True keeps only road /
    trail races, None both. What SP-293 / SP-276 read."""
    lo = (today - dt.timedelta(days=days)).isoformat()
    return [r for r in normalise(rows) if r.get("confirmed", True) and lo <= r["date"] <= today.isoformat()
            and (trail is None or bool(r.get("trail")) == trail)]


def load(user_id: int = 1) -> list:
    """The stored rows (read-only, synchronous; [] without a DB)."""
    from backend.engine.wko5expr.datasource import read_setting
    return normalise(read_setting(KEY, [], user_id))
