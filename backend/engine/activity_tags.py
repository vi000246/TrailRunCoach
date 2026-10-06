"""
Activity metadata — activity type and effort — auto-filled and user-editable,
like WKO5's workout metadata (user request 2026-10-01).

    activity_type  race 比賽 / training 練跑 / hike 爬山 / baiyue_group 百岳跟團 /
                   test 測試 / other 其他
    effort         max 全力 / hard_with_rests 有拼但有休息 / moderate 一般 / easy 輕鬆
    note           free text
    poles          登山杖 有杖 / 沒杖 / 未標 — user only, stored as a free-form tag,
                   read by no model (POLES, SP-242)

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

Auto rules (each 推估 unless a source is named; numbers in AUTO_EFFORT):

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
     above_aet ≥ 2/3 (Seiler AeT boundary; 2/3 推估):
       rest_share ≤ 0.10 → max; rest_share > 0.10 → hard_with_rests
       (a hard mountain day with long stops is not a maximal effort; 0.10
       推估, see AUTO_EFFORT);
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
    "above_aet": 2.0 / 3.0,     # 推估 (Seiler three-zone AeT boundary; the 2/3 share is ours)
    "rest_min_s": 300.0,        # 推估: a LONG rest = a stop of ≥ 5 min (shorter: aid stations, gates, GPS dropouts)
    "rest_max": 0.10,           # 推估: long rests ≤ 10 % of the elapsed time = a continuous effort. One
                                # runner's 7 diary trail races: 0.00–0.05; hard mountain days with real
                                # breaks (4 of them): 0.12–0.17
    "easy_low_share": 0.50,     # intensity.INTENSITY["majority"] (推估)
    "easy_tol_bpm": 3.0,        # intensity.INTENSITY["easy_tol_bpm"] (workout_review.AET_MARGIN)
}
MATCH_TOL_MIN = 3               # 推估: the same activity in two sources starts within 3 min
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
        from backend.db.database import db_path
        return db_path()
    except Exception:                       # noqa: BLE001
        return None


def _db_path(db_path=None) -> Optional[Path]:
    return Path(db_path) if db_path is not None else _default_db()


_memo: dict = {}
COLS = ("id", "athlete_id", "start_local", "source", "file", "workout_id", "distance_km", "label",
        "activity_type", "activity_type_overridden", "effort", "effort_overridden", "note", "exclusion",
        "name", "tags_json", "pain", "pain_area", "injury_id")
EXCLUSIONS = ("keep", "exclude")    # bad_activity.KEEP / EXCLUDE; None = the auto rule
# columns added after the table first shipped (database._migrate_schema; upsert adds them too)
LATE_COLS = {"exclusion": "TEXT", "name": "TEXT", "tags_json": "TEXT",
             "pain": "INTEGER", "pain_area": "TEXT", "injury_id": "INTEGER"}   # pain*: engine/injuries.py
NAME_MAX = 200
TAG_MAX_LEN = 30
TAGS_MAX = 20


def clean_tags(tags) -> list[str]:
    """Free-form tags as stored: stripped, empty ones dropped, duplicates
    (case-insensitive) dropped keeping the first spelling, order kept."""
    out, seen = [], set()
    for t in tags or []:
        s = str(t).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


# 登山杖 (SP-242, docs/research/trekking-poles.md §5 #1): the user's own mark, stored as one of
# two free-form tags (no schema change). 有杖 / 沒杖 / 未標 (neither tag); the two are mutually
# exclusive. Nothing detects it (a watch cannot tell), and NO model reads it — the calculator,
# the HR model and the downhill bump stay as they are (§4); it is kept only for the 有杖 vs 沒杖
# comparison (SP-243). The tag strings are storage values, the same in every UI language.
POLES = {"with": "有杖", "without": "沒杖"}
_POLE_OF_TAG = {v: k for k, v in POLES.items()}


def poles_of(tags) -> Optional[str]:
    """"with" / "without" / None (未標) from a tag list; the last pole tag
    wins should both ever be there."""
    out = None
    for t in tags or []:
        out = _POLE_OF_TAG.get(str(t).strip(), out)
    return out


def with_poles(tags, poles: Optional[str]) -> list[str]:
    """The tag list with the pole mark set to `poles` ("with" / "without",
    None = 未標): both pole tags removed, the chosen one appended."""
    out = [t for t in clean_tags(tags) if t not in _POLE_OF_TAG]
    return out + [POLES[poles]] if poles in POLES else out


def exclusive_poles(tags) -> list[str]:
    """A cleaned tag list with at most one pole tag (the last one kept)."""
    ct = clean_tags(tags)
    p = poles_of(ct)
    return ct if sum(t in _POLE_OF_TAG for t in ct) <= 1 else with_poles(ct, p)


def tags_of(row: Optional[dict]) -> list[str]:
    """The stored free-form tags of a tag row ([] when none / unreadable)."""
    import json
    raw = (row or {}).get("tags_json")
    if not raw:
        t = (row or {}).get("tags")
        return clean_tags(t) if isinstance(t, list) else []
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return clean_tags(v) if isinstance(v, list) else []


def name_of(row: Optional[dict]) -> Optional[str]:
    """The user's title of an activity (None = keep the original)."""
    n = (row or {}).get("name")
    return n.strip() if isinstance(n, str) and n.strip() else None


def load(db_path=None, athlete_id: int = 1) -> list[dict]:
    """Every stored user tag (sync, read-only, memoised on the DB file's
    stamp, its WAL included: db/filestamp.py). [] when the DB or the table is
    missing. A table from before a column was added (e.g. `exclusion`, until
    init_db migrates it) still loads: the missing columns read as None."""
    from backend.db.filestamp import db_stamp
    p = _db_path(db_path)
    if p is None or not p.exists():
        return []
    fs = db_stamp(p)
    if fs is None:
        return []
    stamp = (str(p), *fs, athlete_id)
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
        r["tags"] = tags_of(r)
        r["name"] = name_of(r)
    _memo.update(stamp=stamp, rows=rows)
    return rows


def find(rows: list[dict], start: Optional[dt.datetime], file: Optional[str] = None,
         tol_min: int = MATCH_TOL_MIN) -> Optional[dict]:
    """The stored tag of an activity: same dataset file (also without the
    同步資料 source's "coros/" / "tp/" prefix: engine/activity_key.py), else the
    same start minute, else the nearest start within ±tol_min minutes — so a
    tag set on one source (incl. the 「當作間歇」 tag) holds on every other."""
    if not rows:
        return None
    if file:
        from backend.engine.activity_key import same_file
        for r in rows:
            if r.get("file") and same_file(r["file"], file):
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


def validate(activity_type=None, effort=None, exclusion=None, name=None, tags=None,
             pain=None, pain_area=None, poles=None) -> Optional[str]:
    if activity_type is not None and activity_type not in TYPES:
        return "INVALID_ACTIVITY_TYPE"
    if effort is not None and effort not in EFFORTS:
        return "INVALID_EFFORT"
    if exclusion is not None and exclusion not in EXCLUSIONS:
        return "INVALID_EXCLUSION"
    if name is not None and (not isinstance(name, str) or len(name.strip()) > NAME_MAX):
        return "INVALID_NAME"
    if tags is not None:
        if not isinstance(tags, (list, tuple)) or not all(isinstance(t, str) for t in tags):
            return "INVALID_TAGS"
        ct = clean_tags(tags)
        if len(ct) > TAGS_MAX or any(len(t) > TAG_MAX_LEN for t in ct):
            return "INVALID_TAGS"
    if poles is not None and poles not in POLES:
        return "INVALID_POLES"
    if pain is not None or pain_area is not None:
        from backend.engine import injuries as INJ
        err = INJ.validate_pain(pain, pain_area)
        if err:
            return err
    return None


_UNSET = object()


def upsert(db_path, *, start_local: str, athlete_id: int = 1, source=None, file=None, workout_id=None,
           distance_km=None, label=None, activity_type=_UNSET, effort=_UNSET, note=_UNSET,
           exclusion=_UNSET, name=_UNSET, tags=_UNSET, pain=_UNSET, pain_area=_UNSET, poles=_UNSET) -> dict:
    """Write one user tag (sync; the seed script and tests). For each of
    activity_type / effort: a value sets it and its *_overridden flag; None
    clears it (back to auto); left out = unchanged. `exclusion`: "keep" /
    "exclude" / None (auto). `name`: the user's title, None / "" = back to
    the original. `tags`: the free-form tag list (replaces the stored one).
    `poles`: "with" / "without" / None (未標), see POLES.
    Creates the table when missing. Returns the stored row."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from backend.db.models import ActivityTag
    un = lambda v: None if v is _UNSET else v       # noqa: E731
    err = validate(un(activity_type), un(effort), un(exclusion), un(name), un(tags), un(pain), un(pain_area),
                   un(poles))
    if err:
        raise ValueError(err)
    eng = create_engine(f"sqlite:///{Path(db_path)}")
    ActivityTag.__table__.create(eng, checkfirst=True)
    from sqlalchemy import text
    with eng.begin() as c:                     # a table from before the late columns (database._migrate_schema)
        have = {r[1] for r in c.execute(text("PRAGMA table_info(activity_tags)"))}
        for col, typ in LATE_COLS.items():
            if col not in have:
                c.execute(text(f"ALTER TABLE activity_tags ADD COLUMN {col} {typ}"))
    with Session(eng) as s:
        row = s.execute(select(ActivityTag).where(ActivityTag.athlete_id == athlete_id,
                                                  ActivityTag.start_local == start_local)).scalar_one_or_none()
        if row is None:
            row = ActivityTag(athlete_id=athlete_id, start_local=start_local,
                              activity_type_overridden=False, effort_overridden=False)
            s.add(row)
        apply_update(row, activity_type=activity_type, effort=effort, note=note, exclusion=exclusion,
                     name=name, tags=tags, pain=pain, pain_area=pain_area, poles=poles)
        for k, v in (("source", source), ("file", file), ("workout_id", workout_id),
                     ("distance_km", distance_km), ("label", label)):
            if v is not None:
                setattr(row, k, v)
        s.commit()
        out = {c: getattr(row, c) for c in COLS}
    eng.dispose()
    _memo.clear()
    out["tags"] = tags_of(out)
    return out


def apply_update(row, *, activity_type=_UNSET, effort=_UNSET, note=_UNSET, exclusion=_UNSET,
                 name=_UNSET, tags=_UNSET, pain=_UNSET, pain_area=_UNSET, poles=_UNSET) -> None:
    """Set the user fields of an ActivityTag row (shared by upsert and the
    async API): value → set + overridden; None → cleared, back to auto.
    `exclusion` (bad_activity.py): "keep" / "exclude", None = the auto rule.
    `name`: None / blank = back to the original title. `tags`: the whole
    list (cleaned; [] / None = no tags; at most one pole tag). `poles`
    (登山杖, POLES): "with" / "without" / None = 未標 — rewrites the pole tag
    of the (new) tag list, after `tags`."""
    import json
    if exclusion is not _UNSET:
        row.exclusion = exclusion
    if name is not _UNSET:
        row.name = (name or "").strip() or None
    if tags is not _UNSET or poles is not _UNSET:
        ct = exclusive_poles(tags) if tags is not _UNSET else tags_of({"tags_json": row.tags_json})
        if poles is not _UNSET:
            ct = with_poles(ct, poles)
        row.tags_json = json.dumps(ct, ensure_ascii=False) if ct else None
    if activity_type is not _UNSET:
        row.activity_type = activity_type
        row.activity_type_overridden = activity_type is not None
    if effort is not _UNSET:
        row.effort = effort
        row.effort_overridden = effort is not None
    if note is not _UNSET:
        row.note = (note or "").strip() or None
    if pain is not _UNSET:                    # 疼痛 (engine/injuries.py); the event link is set by attach()
        row.pain = pain
        if pain is None or pain == 0:
            row.pain_area = None
    if pain_area is not _UNSET:
        row.pain_area = (pain_area or "").strip() or None if (getattr(row, "pain", None) or 0) >= 1 else None
    row.updated_at = dt.datetime.utcnow()


# ---------------------------------------------------------------------------
# the watch's own post-workout rating (FIT session workout_rpe / workout_feel)
# ---------------------------------------------------------------------------
#
# Read-only scan of one runner's ~800 COROS-folder FITs (2026-10-02): the
# COROS APEX 2 Pro files carry NO RPE / feel field (no session field
# 192 / 193, no developer field); Garmin fenix 7 files imported into a COROS
# account carry both, a few with a value (RPE 1–4, feel 25–100). So the RPE is used wherever a FIT has it, whatever the
# watch. Stored per workout_files row at import (file_service) and by
# scripts/backfill_rpe.py; read here by start time.

FEELS = {0: "很差", 25: "差", 50: "普通", 75: "好", 100: "很好"}   # FIT workout_feel (Garmin: Very Weak … Very Strong)
RPE_EFFORT = ((4.0, "easy"), (8.0, "moderate"))   # 推估: RPE ≤ 4 輕鬆, ≤ 8 一般, 9–10 全力 (Borg CR10 words: 4 "somewhat hard", 9–10 "extremely hard")


def recorded_from_session(session: Optional[dict]) -> tuple[Optional[float], Optional[int]]:
    """(RPE 1–10, feel 0–100) from a FIT session message's fields. FIT
    workout_rpe is RPE × 10 (Garmin writes 10 … 100); workout_feel is 0 …
    100 in steps of 25. (None, None) when the watch recorded neither.
    Session fields 193 / 192: fitdecode names them workout_rpe / workout_feel,
    python-fitparse's older profile (fit_reader's primary parser) only
    unknown_193 / unknown_192."""
    s = session or {}

    def num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f else None
    r = num(s.get("workout_rpe", s.get("unknown_193")))
    f = num(s.get("workout_feel", s.get("unknown_192")))
    rpe = round(r / 10.0, 1) if r is not None and 0 < r <= 100 else None
    feel = int(round(f)) if f is not None and 0 <= f <= 100 else None
    return rpe, feel


def feel_label(feel: Optional[int]) -> Optional[str]:
    if feel is None:
        return None
    return FEELS[min(FEELS, key=lambda k: abs(k - feel))]


def effort_from_rpe(rpe: Optional[float], rest_share: Optional[float] = None,
                    base: Optional[dict] = None, rec: Optional[dict] = None) -> Optional[dict]:
    """The auto effort from the watch's RPE (it outranks the HR rule; a user
    mark outranks both). 9–10 with long rests > AUTO_EFFORT["rest_max"] =
    有拼但有休息, as the HR rule. `base` = the HR rule's result (its numbers
    are kept for display). `rec` = the recorded row (load_recorded): an RPE mapped
    from COROS's post-run rating (SP-231) says so. None without an RPE."""
    if rpe is None:
        return None
    eff = next((e for top, e in RPE_EFFORT if rpe <= top), "max")
    rest = rest_share if rest_share is not None else (base or {}).get("rest_share")
    if eff == "max" and rest is not None and rest > _rest_max():
        eff = "hard_with_rests"
    from backend.engine import coros_rpe as CR
    from backend.i18n import _
    if (rec or {}).get("source") == CR.SOURCE and (rec or {}).get("coros_feel") in CR.FEEL_LABEL:
        why = _("COROS 跑後自評 {label}（換算成 RPE {rpe}，推估）→ {effort}",
                label=CR.FEEL_LABEL[rec["coros_feel"]], rpe=f"{rpe:g}", effort=EFFORTS[eff])
    else:
        why = f"手錶記錄的 RPE {rpe:g}（運動後自評）→ {EFFORTS[eff]}"
    if base and base.get("effort") and base["effort"] != eff:
        why += f"；心率規則會判為「{EFFORTS.get(base['effort'], '?')}」"
    return {**(base or {}), "effort": eff, "reason": why, "basis": "rpe", "rpe": rpe,
            "hr_effort": (base or {}).get("effort")}


RECORDED_COLS = ("file_path", "start_time_utc", "rpe", "feel", "coros_feel", "rpe_source")
_rec_memo: dict = {}


def load_recorded(db_path=None) -> list[dict]:
    """The workout_files rows with a recorded RPE / feel, as {start_local,
    file, rpe, feel} (start_local = the athlete-local start minute, the tag
    key). Read-only, memoised on the DB file (WAL included); [] without a
    DB, the table or the columns (a DB from before the migration)."""
    from backend.db.filestamp import db_stamp
    p = _db_path(db_path)
    if p is None or not p.exists():
        return []
    fs = db_stamp(p)
    if fs is None:
        return []
    stamp = (str(p), *fs)
    if _rec_memo.get("stamp") == stamp:
        return _rec_memo["rows"]
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            have = {r[1] for r in con.execute("PRAGMA table_info(workout_files)").fetchall()}
            # + COROS's post-run rating (SP-231) when the DB has its columns
            extra = "coros_feel, rpe_source" if {"coros_feel", "rpe_source"} <= have else "NULL, NULL"
            raw = con.execute(f"SELECT file_path, start_time_utc, rpe, feel, {extra} FROM workout_files "
                              "WHERE rpe IS NOT NULL OR feel IS NOT NULL").fetchall() \
                if {"rpe", "feel", "start_time_utc"} <= have else []
        finally:
            con.close()
    except sqlite3.Error:
        raw = []
    rows = []
    if raw:
        from backend.engine.wko5expr.datasource import athlete_tz
        tz = athlete_tz()
        for fp, st, rpe, feel, cfeel, src in raw:
            try:
                t = dt.datetime.fromisoformat(str(st)).replace(tzinfo=dt.timezone.utc).astimezone(tz)
            except (TypeError, ValueError):
                continue
            rows.append({"start_local": key_of(t.replace(tzinfo=None)), "file": Path(str(fp).replace("\\", "/")).name,
                         "rpe": rpe, "feel": feel, "coros_feel": cfeel,
                         # where the RPE came from: COROS's rating (SP-231) or the FIT
                         "source": src if src else ("watch" if rpe is not None else None)})
    _rec_memo.update(stamp=stamp, rows=rows)
    return rows


def recorded_of(rows: list[dict], start: Optional[dt.datetime], file: Optional[str] = None) -> Optional[dict]:
    """The recorded RPE / feel of one activity: the same FIT file name, else
    the start minute ±MATCH_TOL_MIN (the WKO5 copy of a synced activity)."""
    if not rows:
        return None
    name = Path(str(file).replace("\\", "/")).name if file else None
    return find(rows, start, name)


def recorded_json(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    from backend.engine import coros_rpe as CR
    return {"rpe": r.get("rpe"), "feel": r.get("feel"), "feel_label": feel_label(r.get("feel")),
            "source": r.get("source"), "self_rating": CR.self_rating(r)}


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


def _rest_max() -> float:
    """AUTO_EFFORT["rest_max"] per athlete (engine/effort_calib.py, plan P9)."""
    try:
        from backend.engine.effort_calib import rest_max
        return rest_max()
    except Exception:                       # noqa: BLE001
        return AUTO_EFFORT["rest_max"]


def effort_hr(s: dict, lthr: Optional[float], aet: Optional[float],
              max_frac: Optional[float] = None) -> dict:
    """Trail / hike effort from HR on moving time. s = {hr_avg (moving),
    above_aet, low_share (below AeT), moving_s, elapsed_s, rest_share (long
    rests ÷ elapsed, rest_spells)}. Returns {effort, reason, hr_frac,
    above_aet, rest_share, ...}. `max_frac` (trail runs, 2026-10-02,
    unsourced-rules.md §A2): the duration-dependent full-effort threshold
    x*(T) − 0.03 (racepower.trailhr.auto_max_frac) replaces the fixed 0.90 ×
    LTHR and the 2/3-above-AeT share — Fornasiero 2018: a full-effort 12 h
    race spends 86 % of its time below VT1, so the share rule cannot hold
    for long races."""
    k = AUTO_EFFORT
    hr, ab, mv, el = s.get("hr_avg"), s.get("above_aet"), s.get("moving_s"), s.get("elapsed_s")
    rest = s.get("rest_share")
    frac = hr / lthr if hr and lthr else None
    out = {"effort": None, "reason": "", "hr_frac": frac, "above_aet": ab, "rest_share": rest,
           "stopped_share": s.get("stopped_share"),
           "hr_avg": hr, "lthr": lthr, "aet": aet, "moving_s": mv, "elapsed_s": el, "basis": "hr"}
    if frac is None or (ab is None and max_frac is None):
        out.update(effort="moderate", reason="沒有心率或門檻：無法判定，當一般", basis=None)
        return out
    rest_txt = f"長休息 {rest:.0%}" if rest is not None else "長休息 ?"
    if max_frac is not None:
        hard, need = frac >= max_frac, f"≥ x*(T) − 0.03 = {max_frac:.0%}，推估"
    else:
        hard, need = frac >= k["max_hr_frac"] and ab >= k["above_aet"], f"≥ {k['max_hr_frac']:.0%}"
    ab_txt = f"、AeT 以上 {ab:.0%}" if ab is not None else ""
    if hard:
        if rest is not None and rest > _rest_max():
            out.update(effort="hard_with_rests",
                       reason=f"移動心率 {frac:.0%} LTHR{ab_txt}，但{rest_txt}（> {_rest_max():.0%}）")
        elif max_frac is not None:
            out.update(effort="max", reason=f"移動心率 {frac:.0%} LTHR（{need}）、{rest_txt}")
        else:
            out.update(effort="max", reason=f"移動心率 {frac:.0%} LTHR（{need}）、"
                                            f"AeT 以上 {ab:.0%}（≥ {k['above_aet']:.0%}）、{rest_txt}")
        return out
    if ab is None:
        out.update(effort="moderate", reason=f"移動心率 {frac:.0%} LTHR（全力需 {need}）、{rest_txt}")
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
            "name": name_of(user), "tags": tags_of(user), "poles": poles_of(tags_of(user)),
            "pain": (user or {}).get("pain"), "pain_area": (user or {}).get("pain_area"),
            "injury_id": (user or {}).get("injury_id"),
            "key": (user or {}).get("start_local")}
