"""
Activity metadata — activity type and effort — auto-filled and user-editable,
like WKO5's workout metadata (user request 2026-10-01).

    activity_type  race 比賽 / training 練跑 / hike 爬山 / baiyue_group 百岳跟團 /
                   test 測試 / other 其他
    effort         max 全力 / hard_with_rests 有拼但有休息 / moderate 一般 / easy 輕鬆
    note           free text

Why effort, not "race": what a race-time prediction can learn from an
activity is whether it was MAXIMAL. The athlete often races by feel (not
all-out), trains hard without racing, and on trail power is a poor effort
signal (downhill power is low; fatigue makes them walk even on flats), so on
trail / hike the effort is read from HR.

Storage: only the USER values live in the DB (`activity_tags`, db/models.py,
with `*_overridden` flags); auto values are computed here at read time and
merged (`merge`): a user mark always wins, and re-running the auto rules can
never clobber it. Tags are keyed by the activity's local start minute
(Dataset entry.start), with the dataset file name matched first — the race-
power engine reads the WKO5 / COROS / TP datasets, and most WKO5 activities
have no workout_files row.

Auto rules (each 自組 unless a source is named; numbers in AUTO_EFFORT):

activity_type, first match:
  1. a season-plan road / 越野賽 event matched to the run (maximal.match_events:
     date + kind + distance) → race;
  2. a test the plan / title says (workout_review.classify test_cp / test_aet,
     not the power pattern alone) → test;
  3. a race word in the title (賽 / 馬拉松 / race / marathon) → race;
  4. hiking / mountaineering sport with a plan 百岳 event covering that day →
     baiyue_group (百岳 trips are group-paced: athlete.GROUP_HIKE_NOTE);
  5. hiking / mountaineering sport, or a hike word in the title of a trail
     run (爬山 / 登山 / 健行 / 郊山 / 百岳 / 縱走 / hike / trek) → hike;
  6. any run → training; anything else → other.

effort, trail / hike (`effort_hr`, HR on MOVING time only):
  * moving HR = time-weighted HR over moving samples (speed > 1 km/h for runs,
    > 0.3 m/s for hikes — athlete.RUN_MOVING_KMH / HIKE_REST_MS);
  * hr_frac = moving HR ÷ LTHR of the activity's own date
    (athlete.thresholds_as_of: plan tests dated on or before the day, else the
    estimate from earlier runs — no later threshold);
  * above_aet = share of the HR-moving time ≥ AeT of that date;
  * rest_share = LONG rests ÷ elapsed time (`rest_spells`: contiguous stops
    of ≥ 5 min, a watch auto-pause / recording gap counts as stopped;
    elapsed = first to last sample). Short stops are not rests: on this
    athlete's races 22–48 % of the elapsed time is "not moving" by the
    speed rule (aid stations, queues, GPS speed dropouts on steep climbs),
    but only 0–5 % is in stops ≥ 5 min;
  1. hr_frac ≥ 0.90 (Friel Z3 lower bound, as maximal.trail_avg_frac) and
     above_aet ≥ 2/3 (Seiler AeT boundary; 2/3 自組):
       rest_share ≤ 0.10 → max; rest_share > 0.10 → hard_with_rests
       (a hard mountain day with long stops is not a maximal effort; 0.10
       自組, see AUTO_EFFORT);
  2. ≥ half the HR time below AeT and average < AeT + 3 bpm (intensity.py's
     easy rule) → easy;
  3. otherwise → moderate.

effort, road (`effort_road`): the existing road maximal rules decide max
(maximal.road_maximal: distance bucket, last-quarter HR vs LTHR, peak HR,
even split, the 1.5 × power monotonicity); otherwise the HR easy rule above,
else moderate.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

TYPES = {"race": "比賽", "training": "練跑", "hike": "爬山", "baiyue_group": "百岳跟團",
         "test": "測試", "other": "其他"}
EFFORTS = {"max": "全力", "hard_with_rests": "有拼但有休息", "moderate": "一般", "easy": "輕鬆"}

AUTO_EFFORT = {
    "max_hr_frac": 0.90,        # Friel HR Z3 lower bound (zones.FRIEL_HR), as maximal.MAXIMAL["trail_avg_frac"]
    "above_aet": 2.0 / 3.0,     # 自組 (Seiler three-zone AeT boundary; the 2/3 share is ours)
    "rest_min_s": 300.0,        # 自組: a LONG rest = a stop of ≥ 5 min (shorter: aid stations, gates, GPS dropouts)
    "rest_max": 0.10,           # 自組: long rests ≤ 10 % of the elapsed time = a continuous effort. This
                                # athlete's 7 diary trail races: 0.00–0.05; hard mountain days with real
                                # breaks (2026-07-27, 2025-11-02, 2024-07-27/-08-18): 0.12–0.17
    "easy_low_share": 0.50,     # intensity.INTENSITY["majority"] (自組)
    "easy_tol_bpm": 3.0,        # intensity.INTENSITY["easy_tol_bpm"] (workout_review.AET_MARGIN)
}
MATCH_TOL_MIN = 3               # 自組: the same activity in two sources starts within 3 min
RACE_WORDS = re.compile(r"賽|馬拉松|race|marathon", re.I)
HIKE_WORDS = re.compile(r"爬山|登山|健行|郊山|百岳|縱走|hike|hiking|trek", re.I)
HIKE_SPORTS = ("hiking", "mountaineering")


# ---------------------------------------------------------------------------
# keys and the store
# ---------------------------------------------------------------------------

def key_of(start: Optional[dt.datetime]) -> Optional[str]:
    """The tag key: local start to the minute, 'YYYY-MM-DDTHH:MM'."""
    if start is None:
        return None
    return start.strftime("%Y-%m-%dT%H:%M")


TAGS_DB_ENV = "WKO5COACH_TAGS_DB"   # read the tags from another DB (a scratch copy: back-test what-ifs)


def _default_db() -> Optional[Path]:
    """The app DB, or the WKO5COACH_TAGS_DB override. Tests patch this."""
    if os.environ.get(TAGS_DB_ENV):
        return Path(os.environ[TAGS_DB_ENV])
    try:
        from backend.db.database import DB_PATH
        return Path(DB_PATH)
    except Exception:                       # noqa: BLE001
        return None


def _db_path(db_path=None) -> Optional[Path]:
    return Path(db_path) if db_path is not None else _default_db()


_memo: dict = {}
COLS = ("id", "athlete_id", "start_local", "source", "file", "workout_id", "distance_km", "label",
        "activity_type", "activity_type_overridden", "effort", "effort_overridden", "note", "exclusion")
EXCLUSIONS = ("keep", "exclude")    # bad_activity.KEEP / EXCLUDE; None = the auto rule


def load(db_path=None, athlete_id: int = 1) -> list[dict]:
    """Every stored user tag (sync, read-only, memoised on the DB file's
    mtime). [] when the DB or the table is missing. A table from before a
    column was added (e.g. `exclusion`, until init_db migrates it) still
    loads: the missing columns read as None."""
    p = _db_path(db_path)
    if p is None or not p.exists():
        return []
    try:
        stt = os.stat(p)
        stamp = (str(p), stt.st_mtime_ns, stt.st_size, athlete_id)
    except OSError:
        return []
    if _memo.get("stamp") == stamp:
        return _memo["rows"]
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            have = {r[1] for r in con.execute("PRAGMA table_info(activity_tags)").fetchall()}
            cols = [c for c in COLS if c in have]
            cur = con.execute(f"SELECT {', '.join(cols)} FROM activity_tags WHERE athlete_id=?", (athlete_id,)) \
                if cols else None
            rows = [{**dict.fromkeys(COLS), **dict(zip(cols, r))} for r in cur.fetchall()] if cur else []
        finally:
            con.close()
    except sqlite3.Error:
        rows = []
    for r in rows:
        r["activity_type_overridden"] = bool(r["activity_type_overridden"])
        r["effort_overridden"] = bool(r["effort_overridden"])
    _memo.update(stamp=stamp, rows=rows)
    return rows


def find(rows: list[dict], start: Optional[dt.datetime], file: Optional[str] = None,
         tol_min: int = MATCH_TOL_MIN) -> Optional[dict]:
    """The stored tag of an activity: same dataset file, else the same start
    minute, else the nearest start within ±tol_min minutes."""
    if not rows:
        return None
    if file:
        for r in rows:
            if r.get("file") and r["file"] == file:
                return r
    k = key_of(start)
    if k is None:
        return None
    for r in rows:
        if r.get("start_local") == k:
            return r
    best, bd = None, None
    for r in rows:
        try:
            t = dt.datetime.fromisoformat(r["start_local"])
        except (TypeError, ValueError):
            continue
        d = abs((t - start.replace(second=0, microsecond=0)).total_seconds()) / 60.0
        if d <= tol_min and (bd is None or d < bd):
            best, bd = r, d
    return best


def user_of(w, rows: Optional[list] = None) -> Optional[dict]:
    """The stored tag of a dataset workout (None when the user never set one)."""
    rows = load() if rows is None else rows
    return find(rows, w.entry.start, getattr(w.entry, "file", None))


def user_effort(u: Optional[dict]) -> Optional[str]:
    return u["effort"] if u and u.get("effort_overridden") and u.get("effort") in EFFORTS else None


def user_type(u: Optional[dict]) -> Optional[str]:
    return u["activity_type"] if u and u.get("activity_type_overridden") and u.get("activity_type") in TYPES \
        else None


def user_exclusion(u: Optional[dict]) -> Optional[str]:
    """The user's bad-file override: "keep" / "exclude" / None (auto rule)."""
    return u["exclusion"] if u and u.get("exclusion") in EXCLUSIONS else None


def validate(activity_type=None, effort=None, exclusion=None) -> Optional[str]:
    if activity_type is not None and activity_type not in TYPES:
        return "INVALID_ACTIVITY_TYPE"
    if effort is not None and effort not in EFFORTS:
        return "INVALID_EFFORT"
    if exclusion is not None and exclusion not in EXCLUSIONS:
        return "INVALID_EXCLUSION"
    return None


_UNSET = object()


def upsert(db_path, *, start_local: str, athlete_id: int = 1, source=None, file=None, workout_id=None,
           distance_km=None, label=None, activity_type=_UNSET, effort=_UNSET, note=_UNSET,
           exclusion=_UNSET) -> dict:
    """Write one user tag (sync; the seed script and tests). For each of
    activity_type / effort: a value sets it and its *_overridden flag; None
    clears it (back to auto); left out = unchanged. `exclusion`: "keep" /
    "exclude" / None (auto). Creates the table when missing. Returns the
    stored row."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from backend.db.models import ActivityTag
    err = validate(None if activity_type is _UNSET else activity_type, None if effort is _UNSET else effort,
                   None if exclusion is _UNSET else exclusion)
    if err:
        raise ValueError(err)
    eng = create_engine(f"sqlite:///{Path(db_path)}")
    ActivityTag.__table__.create(eng, checkfirst=True)
    from sqlalchemy import text
    with eng.begin() as c:                     # a table from before `exclusion` (database._migrate_schema)
        if "exclusion" not in {r[1] for r in c.execute(text("PRAGMA table_info(activity_tags)"))}:
            c.execute(text("ALTER TABLE activity_tags ADD COLUMN exclusion TEXT"))
    with Session(eng) as s:
        row = s.execute(select(ActivityTag).where(ActivityTag.athlete_id == athlete_id,
                                                  ActivityTag.start_local == start_local)).scalar_one_or_none()
        if row is None:
            row = ActivityTag(athlete_id=athlete_id, start_local=start_local,
                              activity_type_overridden=False, effort_overridden=False)
            s.add(row)
        apply_update(row, activity_type=activity_type, effort=effort, note=note, exclusion=exclusion)
        for k, v in (("source", source), ("file", file), ("workout_id", workout_id),
                     ("distance_km", distance_km), ("label", label)):
            if v is not None:
                setattr(row, k, v)
        s.commit()
        out = {c: getattr(row, c) for c in COLS}
    eng.dispose()
    _memo.clear()
    return out


def apply_update(row, *, activity_type=_UNSET, effort=_UNSET, note=_UNSET, exclusion=_UNSET) -> None:
    """Set the user fields of an ActivityTag row (shared by upsert and the
    async API): value → set + overridden; None → cleared, back to auto.
    `exclusion` (bad_activity.py): "keep" / "exclude", None = the auto rule."""
    if exclusion is not _UNSET:
        row.exclusion = exclusion
    if activity_type is not _UNSET:
        row.activity_type = activity_type
        row.activity_type_overridden = activity_type is not None
    if effort is not _UNSET:
        row.effort = effort
        row.effort_overridden = effort is not None
    if note is not _UNSET:
        row.note = (note or "").strip() or None
    row.updated_at = dt.datetime.utcnow()


# ---------------------------------------------------------------------------
# auto rules (pure)
# ---------------------------------------------------------------------------

def auto_type(*, plan_race: Optional[dict] = None, test: Optional[str] = None, sport: str = "",
              sport_type: str = "", title: str = "", trail: bool = False,
              baiyue_event: Optional[str] = None) -> tuple[str, str]:
    """(activity_type, reason) — the module docstring's order."""
    title = title or ""
    if plan_race:
        return "race", f"賽季計畫的比賽「{plan_race.get('name') or ''}」"
    if test:
        return "test", test
    if RACE_WORDS.search(title):
        return "race", f"標題有比賽字樣（{title}）"
    hike_sport = (sport_type or "").lower() in HIKE_SPORTS
    if hike_sport and baiyue_event:
        return "baiyue_group", f"賽季計畫的百岳行程「{baiyue_event}」（跟團）"
    if hike_sport:
        return "hike", "運動類型是登山／健行"
    if trail and HIKE_WORDS.search(title):
        return "hike", f"越野＋標題有登山字樣（{title}）"
    if (sport or "").lower() == "run":
        return "training", "跑步（沒有比賽／測試／登山的跡象）"
    return "other", "非跑步、非登山"


def _easy(hr_avg, low_share, aet) -> bool:
    k = AUTO_EFFORT
    return bool(hr_avg and aet and low_share is not None and low_share >= k["easy_low_share"]
                and hr_avg < aet + k["easy_tol_bpm"])


def rest_spells(t, moving, min_rest_s: float = None, max_dt: float = 30.0) -> dict:
    """Stopped time of one activity from its time array and a moving mask:
    every not-moving sample interval (≤ max_dt) plus every recording gap
    (> max_dt: a watch auto-pause or a stop with the watch off) is stopped;
    contiguous stopped intervals form a spell, and spells ≥ min_rest_s are
    LONG rests. Short stops (an aid station, a gate, GPS speed dropouts on a
    steep climb) are not rests. Returns {elapsed_s, stopped_s, rest_s,
    stopped_share, rest_share}."""
    import numpy as np
    min_rest_s = AUTO_EFFORT["rest_min_s"] if min_rest_s is None else min_rest_s
    t = np.asarray(t, float)
    mv = np.asarray(moving, bool)
    n = min(len(t), len(mv))
    t, mv = t[:n], mv[:n]
    ok = np.isfinite(t)
    t, mv = t[ok], mv[ok]
    if len(t) < 2:
        return {"elapsed_s": None, "stopped_s": None, "rest_s": None, "stopped_share": None, "rest_share": None}
    d = np.diff(t)
    d[d < 0] = 0.0
    gap = d > max_dt
    stop = gap | ~mv[1:]
    elapsed = float(d.sum())
    stopped = float(d[stop].sum())
    rest, run = 0.0, 0.0
    for x, s in zip(d, stop):
        if s:
            run += x
        else:
            if run >= min_rest_s:
                rest += run
            run = 0.0
    if run >= min_rest_s:
        rest += run
    return {"elapsed_s": elapsed, "stopped_s": stopped, "rest_s": rest,
            "stopped_share": stopped / elapsed if elapsed > 0 else None,
            "rest_share": rest / elapsed if elapsed > 0 else None}


def effort_hr(s: dict, lthr: Optional[float], aet: Optional[float]) -> dict:
    """Trail / hike effort from HR on moving time. s = {hr_avg (moving),
    above_aet, low_share (below AeT), moving_s, elapsed_s, rest_share (long
    rests ÷ elapsed, rest_spells)}. Returns {effort, reason, hr_frac,
    above_aet, rest_share, ...}."""
    k = AUTO_EFFORT
    hr, ab, mv, el = s.get("hr_avg"), s.get("above_aet"), s.get("moving_s"), s.get("elapsed_s")
    rest = s.get("rest_share")
    frac = hr / lthr if hr and lthr else None
    out = {"effort": None, "reason": "", "hr_frac": frac, "above_aet": ab, "rest_share": rest,
           "stopped_share": s.get("stopped_share"),
           "hr_avg": hr, "lthr": lthr, "aet": aet, "moving_s": mv, "elapsed_s": el, "basis": "hr"}
    if frac is None or ab is None:
        out.update(effort="moderate", reason="沒有心率或門檻：無法判定，當一般", basis=None)
        return out
    rest_txt = f"長休息 {rest:.0%}" if rest is not None else "長休息 ?"
    if frac >= k["max_hr_frac"] and ab >= k["above_aet"]:
        if rest is not None and rest > k["rest_max"]:
            out.update(effort="hard_with_rests",
                       reason=f"移動心率 {frac:.0%} LTHR、AeT 以上 {ab:.0%}，但{rest_txt}（> {k['rest_max']:.0%}）")
        else:
            out.update(effort="max", reason=f"移動心率 {frac:.0%} LTHR（≥ {k['max_hr_frac']:.0%}）、"
                                            f"AeT 以上 {ab:.0%}（≥ {k['above_aet']:.0%}）、{rest_txt}")
        return out
    if _easy(hr, s.get("low_share"), aet):
        out.update(effort="easy", reason=f"{s['low_share']:.0%} 時間低於 AeT，平均 {hr:.0f} bpm")
        return out
    out.update(effort="moderate", reason=f"移動心率 {frac:.0%} LTHR、AeT 以上 {ab:.0%}、{rest_txt}")
    return out


def effort_road(road: dict, s: dict, aet: Optional[float]) -> dict:
    """Road effort: maximal.road_maximal decides max; else easy / moderate by HR."""
    if road.get("ok"):
        return {"effort": "max", "reason": road.get("reason") or "自配速全力", "basis": "road_maximal"}
    if _easy(s.get("hr_avg"), s.get("low_share"), aet):
        return {"effort": "easy", "reason": f"{s['low_share']:.0%} 時間低於 AeT", "basis": "hr"}
    return {"effort": "moderate", "reason": "未達路跑全力條件：" + (road.get("reason") or ""), "basis": "road_maximal"}


def merge(auto: dict, user: Optional[dict]) -> dict:
    """Effective tags: the user's value when it is set, else the auto one.
    auto = {activity_type, activity_type_reason, effort, effort_reason, ...}."""
    ut, ue = user_type(user), user_effort(user)
    at, ae = auto.get("activity_type"), auto.get("effort")
    t = ut or at
    e = ue or ae
    return {"activity_type": t, "activity_type_label": TYPES.get(t, ""),
            "activity_type_overridden": ut is not None, "activity_type_auto": at,
            "activity_type_auto_label": TYPES.get(at, ""), "activity_type_reason": auto.get("activity_type_reason"),
            "effort": e, "effort_label": EFFORTS.get(e, ""), "effort_overridden": ue is not None,
            "effort_auto": ae, "effort_auto_label": EFFORTS.get(ae, ""), "effort_reason": auto.get("effort_reason"),
            "note": (user or {}).get("note"), "stored": user is not None,
            "exclusion": user_exclusion(user),
            "key": (user or {}).get("start_local")}
