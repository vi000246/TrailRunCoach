from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import date
from typing import Optional
from pydantic import BaseModel

import numpy as np

from backend.db.database import get_db
from backend.db.models import WorkoutFile, WorkoutMetric, MmpCache, AthleteSettings, ActivityTag
from backend.engine.algorithms.mmp import compute_mmp
from backend.engine.algorithms.trail import (
    compute_grade, compute_gap, segment_climbs, compute_vam, compute_hr_drift,
)
from backend.files.fit_reader import parse_fit

router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])

# Palladino's running power zones (% CP; engine/zones.py) — not Coggan's cycling 7 (owner 2026-10-02)
from backend.engine.zones import palladino_rows  # noqa: E402

POWER_ZONES_DEF = palladino_rows()

HR_ZONES_DEF = [
    (1, "Recovery",  0.00, 0.85),
    (2, "Aerobic",   0.85, 0.90),
    (3, "Tempo",     0.90, 0.95),
    (4, "Threshold", 0.95, 1.00),
    (5, "VO2max",    1.00, 99.0),
]


async def _get_workout_or_404(workout_id: int, db: AsyncSession) -> WorkoutFile:
    result = await db.execute(
        select(WorkoutFile).where(WorkoutFile.id == workout_id).options(selectinload(WorkoutFile.metrics))
    )
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    return w


VALID_CLASSIFICATIONS = ("road", "trail", "unknown")
AUTO_CLASSIFICATION = "auto"       # clears the override: back to the classify_trail rule


class ClassificationUpdate(BaseModel):
    trail_classification: str


@router.patch("/{workout_id}/classification")
async def update_classification(
    workout_id: int,
    body: ClassificationUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Set the trail / road class of a workout_files row (a user override);
    "auto" clears the override and re-applies the rule (classify_trail)."""
    if body.trail_classification not in VALID_CLASSIFICATIONS + (AUTO_CLASSIFICATION,):
        raise HTTPException(400, "INVALID_CLASSIFICATION")
    wf = (await db.execute(
        select(WorkoutFile).where(WorkoutFile.id == workout_id)
    )).scalar_one_or_none()
    if not wf:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    if body.trail_classification == AUTO_CLASSIFICATION:
        from backend.engine.algorithms.classify import classify_trail
        wf.classification_overridden = False
        wf.trail_classification = classify_trail(wf.sport, wf.total_distance_m, wf.elevation_gain_m)
    else:
        wf.trail_classification = body.trail_classification
        wf.classification_overridden = True
    await db.commit()
    return {
        "id": wf.id,
        "trail_classification": wf.trail_classification,
        "classification_overridden": bool(wf.classification_overridden),
    }


class ActivityUpdate(BaseModel):
    """PATCH body of the activity tags (engine/activity_tags.py). A field that
    is present sets the user value (and its *_overridden flag); present with
    null clears it (back to the auto value); absent = unchanged."""
    activity_type: Optional[str] = None
    effort: Optional[str] = None
    note: Optional[str] = None
    # bad activity files (engine/bad_activity.py): "keep" 這筆是正常的，不要排除 /
    # "exclude" 手動排除 / null = the auto rule
    exclusion: Optional[str] = None
    # 活動編輯 page: the user's title (null / "" = the original) and the
    # free-form tag list (replaces the stored list)
    name: Optional[str] = None
    tags: Optional[list[str]] = None
    # 疼痛 (engine/injuries.py): null 沒填 / 0 沒痛 / 1 痠 / 2 痛 / 3 中斷; the area (a fixed
    # key or the user's own label) and the side (only carried to a new draft event)
    pain: Optional[int] = None
    pain_area: Optional[str] = None
    pain_side: Optional[str] = None


TAG_FIELDS = ("activity_type", "effort", "note", "exclusion", "name", "tags", "pain", "pain_area", "pain_side")
STORED_FIELDS = ("activity_type", "effort", "note", "exclusion", "name", "tags", "pain", "pain_area")


async def save_activity_tag(db: AsyncSession, body: ActivityUpdate, *, start_local: str, athlete_id: int = 1,
                            source=None, file=None, workout_id=None, distance_km=None, label=None) -> ActivityTag:
    """Upsert the user tag of one activity (key: local start minute)."""
    from backend.engine import activity_tags as AT
    sent = body.model_fields_set
    err = AT.validate(body.activity_type if "activity_type" in sent else None,
                      body.effort if "effort" in sent else None,
                      body.exclusion if "exclusion" in sent else None,
                      body.name if "name" in sent else None,
                      body.tags if "tags" in sent else None,
                      body.pain if "pain" in sent else None,
                      body.pain_area if "pain_area" in sent else None)
    painish = bool({"pain", "pain_area", "pain_side"} & sent)
    if painish:
        from backend.engine import injuries as INJ
        if INJ.demo_mode():
            raise HTTPException(404, "NOT_FOUND")
        if not err and body.pain_side is not None and body.pain_side not in INJ.SIDES:
            err = "INVALID_SIDE"
    if err:
        raise HTTPException(400, err)
    row = (await db.execute(select(ActivityTag).where(ActivityTag.athlete_id == athlete_id,
                                                      ActivityTag.start_local == start_local))).scalar_one_or_none()
    if row is None:
        row = ActivityTag(athlete_id=athlete_id, start_local=start_local,
                          activity_type_overridden=False, effort_overridden=False)
        db.add(row)
    kw = {k: getattr(body, k) for k in STORED_FIELDS if k in sent}
    if kw.get("pain_area") is not None:
        from backend.engine import injuries as INJ
        kw["pain_area"] = INJ.norm_area(kw["pain_area"])
    AT.apply_update(row, **kw)
    for k, v in (("source", source), ("file", file), ("workout_id", workout_id),
                 ("distance_km", distance_km), ("label", label)):
        if v is not None:
            setattr(row, k, v)
    if "pain" in sent or "pain_area" in sent:
        await _attach_injury(db, row, body.pain_side if "pain_side" in sent else None)
        from backend.engine import injuries as INJ
        if INJ.is_custom(row.pain_area):
            from backend.api.injuries import remember_area
            await remember_area(db, row.pain_area)
    await db.commit()
    AT._memo.clear()
    return row


async def _attach_injury(db: AsyncSession, row: ActivityTag, side: Optional[str]) -> None:
    """The pain mark → the injury events (engine/injuries.attach): join an open
    event of the same area, open a draft, or drop this activity's orphan draft."""
    import datetime as _dt
    from backend.db.models import InjuryEvent
    from backend.engine import injuries as INJ
    await db.flush()
    evs = (await db.execute(select(InjuryEvent).where(InjuryEvent.athlete_id == row.athlete_id))).scalars().all()
    cnt = dict((await db.execute(select(ActivityTag.injury_id, func.count()).where(
        ActivityTag.injury_id.is_not(None)).group_by(ActivityTag.injury_id))).all())
    dicts = [{c: getattr(e, c) for c in INJ.EVENT_COLS} for e in evs]
    try:
        day = _dt.date.fromisoformat(row.start_local[:10])
    except (TypeError, ValueError):
        return
    res = INJ.attach(row.pain, row.pain_area, side, day, row.start_local, row.file, row.injury_id, dicts, cnt)
    by = {e.id: e for e in evs}
    now = _dt.datetime.utcnow()
    if res["update"]:
        e = by[res["update"]["id"]]
        for k, v in res["update"]["fields"].items():
            setattr(e, k, v)
        e.updated_at = now
    new_id = res["injury_id"]
    if res["create"]:
        e = InjuryEvent(athlete_id=row.athlete_id, created_at=now, updated_at=now, pause_quality=False,
                        **res["create"])
        db.add(e)
        await db.flush()
        new_id = e.id
    row.injury_id = new_id
    if res["delete"] is not None and res["delete"] != new_id:
        await db.delete(by[res["delete"]])
    INJ._memo.clear()


def _tag_json(t: Optional[ActivityTag]) -> dict:
    """The stored user tag fields of a workout_files row (auto values need the
    dataset: GET /api/v1/wko5/workouts/{idx}/activity)."""
    from backend.engine import activity_tags as AT
    ty = t.activity_type if t and t.activity_type_overridden else None
    ef = t.effort if t and t.effort_overridden else None
    return {"activity_type": ty, "activity_type_label": AT.TYPES.get(ty), "activity_type_overridden": ty is not None,
            "effort": ef, "effort_label": AT.EFFORTS.get(ef), "effort_overridden": ef is not None,
            "note": t.note if t else None, "key": t.start_local if t else None,
            "exclusion": AT.user_exclusion({"exclusion": t.exclusion}) if t else None,
            "name": AT.name_of({"name": t.name}) if t else None,
            "tags": AT.tags_of({"tags_json": t.tags_json}) if t else [],
            "pain": t.pain if t else None, "pain_area": t.pain_area if t else None,
            "injury_id": t.injury_id if t else None}


def _local_start(wf: WorkoutFile) -> Optional[str]:
    from backend.engine import activity_tags as AT
    if wf.start_time_utc is None:
        return None
    import datetime as _dt
    from backend.engine.wko5expr.datasource import athlete_tz
    loc = wf.start_time_utc.replace(tzinfo=_dt.timezone.utc).astimezone(athlete_tz()).replace(tzinfo=None)
    return AT.key_of(loc)


@router.patch("/{workout_id}/activity")
async def update_activity(workout_id: int, body: ActivityUpdate, db: AsyncSession = Depends(get_db)):
    """Set the activity type / effort / note of a workout_files row — the
    same pattern as update_classification: user values with *_overridden
    flags that auto re-classification never touches."""
    wf = (await db.execute(select(WorkoutFile).where(WorkoutFile.id == workout_id))).scalar_one_or_none()
    if not wf:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    key = _local_start(wf)
    if key is None:
        raise HTTPException(422, "NO_START_TIME")
    row = await save_activity_tag(db, body, start_local=key, athlete_id=wf.athlete_id, source=wf.source,
                                  workout_id=wf.id,
                                  distance_km=(wf.total_distance_m or 0) / 1000.0 if wf.total_distance_m else None)
    return {"id": wf.id, **_tag_json(row)}


async def _tags_by_start(db: AsyncSession, workouts) -> dict:
    keys = {w.id: _local_start(w) for w in workouts}
    want = {k for k in keys.values() if k}
    if not want:
        return {}
    rows = (await db.execute(select(ActivityTag).where(ActivityTag.start_local.in_(want)))).scalars().all()
    by = {r.start_local: r for r in rows}
    return {i: by.get(k) for i, k in keys.items()}


@router.get("")
async def list_workouts(
    athlete_id: int = 1,
    page: int = 1,
    per_page: int = 20,
    sport: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id)
    if sport:
        q = q.where(WorkoutFile.sport == sport)
    if date_from:
        q = q.where(WorkoutFile.workout_date >= date_from)
    if date_to:
        q = q.where(WorkoutFile.workout_date <= date_to)

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar()

    q = q.order_by(WorkoutFile.workout_date.desc()).offset((page - 1) * per_page).limit(per_page)
    q = q.options(selectinload(WorkoutFile.metrics))
    result = await db.execute(q)
    workouts = result.scalars().all()
    tags = await _tags_by_start(db, workouts)

    return {
        "total": total, "page": page, "per_page": per_page,
        "items": [_workout_summary(w, tags.get(w.id)) for w in workouts],
    }


@router.get("/{workout_id}")
async def get_workout(workout_id: int, db: AsyncSession = Depends(get_db)):
    q = select(WorkoutFile).where(WorkoutFile.id == workout_id).options(selectinload(WorkoutFile.metrics))
    result = await db.execute(q)
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    tags = await _tags_by_start(db, [w])
    return _workout_detail(w, tags.get(w.id))


@router.get("/{workout_id}/mmp")
async def get_workout_mmp(workout_id: int, channel: str = "power", db: AsyncSession = Depends(get_db)):
    q = select(MmpCache).where(MmpCache.workout_id == workout_id, MmpCache.channel == channel)
    result = await db.execute(q)
    cached = result.scalars().all()
    if cached:
        curve = {str(c.duration_s): c.value for c in cached}
        return {"workout_id": workout_id, "channel": channel, "curve": curve, "cached": True}

    w_result = await db.execute(select(WorkoutFile).where(WorkoutFile.id == workout_id))
    w = w_result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    if w.file_format != "fit":
        raise HTTPException(422, "NO_POWER_DATA")

    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"FILE_PARSE_ERROR: {e}")

    if not raw.has_power:
        raise HTTPException(422, "NO_POWER_DATA")

    mmp = compute_mmp(raw.power_w, raw.time_s)
    for dur, val in mmp.items():
        if val > 0:
            db.add(MmpCache(workout_id=workout_id, channel=channel, duration_s=dur, value=val))
    await db.commit()

    curve = {str(d): v for d, v in mmp.items() if v > 0}
    return {"workout_id": workout_id, "channel": channel, "curve": curve, "cached": False}


@router.get("/{workout_id}/timeseries")
async def get_workout_timeseries(workout_id: int, db: AsyncSession = Depends(get_db)):
    w = await _get_workout_or_404(workout_id, db)
    if w.file_format != "fit":
        raise HTTPException(422, "NO_FIT_FILE")
    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"PARSE_ERROR: {e}")

    time_s = raw.time_s if raw.time_s is not None and len(raw.time_s) > 0 else []
    power_w = raw.power_w if raw.power_w is not None else []
    hr_bpm = raw.heart_rate_bpm if raw.heart_rate_bpm is not None else []
    cadence = raw.cadence_rpm if raw.cadence_rpm is not None else []

    n = len(time_s)
    if n == 0:
        return {"workout_id": workout_id, "duration_s": 0, "sample_rate_s": 1, "series": []}

    step = max(1, n // 1800)
    series = []
    for i in range(0, n, step):
        point: dict = {"t": int(time_s[i])}
        if i < len(power_w) and power_w[i] is not None:
            point["power"] = round(float(power_w[i]))
        if i < len(hr_bpm) and hr_bpm[i] is not None:
            point["hr"] = round(float(hr_bpm[i]))
        if i < len(cadence) and cadence[i] is not None:
            point["cadence"] = round(float(cadence[i]))
        series.append(point)

    return {
        "workout_id": workout_id,
        "duration_s": int(time_s[-1]) if len(time_s) > 0 else 0,
        "sample_rate_s": step,
        "series": series,
    }


@router.get("/{workout_id}/zones")
async def get_workout_zones(workout_id: int, db: AsyncSession = Depends(get_db)):
    w = await _get_workout_or_404(workout_id, db)

    settings_result = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == w.athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = settings_result.scalars().first()
    ftp = float(settings.ftp_w) if settings and settings.ftp_w else 200.0
    lthr = int(settings.lthr) if settings and settings.lthr else 165

    if w.file_format != "fit":
        return {"workout_id": workout_id, "ftp": ftp, "lthr": lthr, "power_zones": [], "hr_zones": []}

    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"PARSE_ERROR: {e}")

    power_w = list(raw.power_w) if raw.power_w is not None else []
    hr_bpm = list(raw.heart_rate_bpm) if raw.heart_rate_bpm is not None else []

    def count_zones(values: list, zones: list, threshold: float) -> dict:
        counts: dict[int, int] = {z[0]: 0 for z in zones}
        for v in values:
            if v is None or (isinstance(v, float) and v != v):  # skip None/NaN
                continue
            ratio = float(v) / threshold
            for z_id, _, lo, hi in zones:
                if lo <= ratio < hi:
                    counts[z_id] += 1
                    break
        return counts

    pw_counts = count_zones(power_w, POWER_ZONES_DEF, ftp)
    hr_counts = count_zones(hr_bpm, HR_ZONES_DEF, float(lthr))

    return {
        "workout_id": workout_id,
        "ftp": ftp,
        "lthr": lthr,
        "power_zones": [
            {
                "zone": z, "name": n,
                "min_w": round(lo * ftp),
                "max_w": round(hi * ftp) if hi < 10 else None,
                "time_s": pw_counts[z],
            }
            for z, n, lo, hi in POWER_ZONES_DEF
        ],
        "hr_zones": [
            {
                "zone": z, "name": n,
                "min_bpm": round(lo * lthr),
                "max_bpm": round(hi * lthr) if hi < 10 else None,
                "time_s": hr_counts[z],
            }
            for z, n, lo, hi in HR_ZONES_DEF
        ],
    }


@router.get("/{workout_id}/trail")
async def get_workout_trail(workout_id: int, db: AsyncSession = Depends(get_db)):
    w = await _get_workout_or_404(workout_id, db)
    if w.file_format != "fit":
        raise HTTPException(422, "NO_FIT_FILE")
    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"PARSE_ERROR: {e}")

    has_altitude = len(raw.altitude_m) > 0 and np.any(raw.altitude_m > 0)
    has_distance = len(raw.distance_m) > 0 and np.any(raw.distance_m > 0)
    if not has_altitude or not has_distance:
        raise HTTPException(422, "NO_TRAIL_DATA")

    alt = raw.altitude_m
    dist = raw.distance_m
    time_s = raw.time_s
    n = len(alt)
    step = max(1, n // 1800)

    grade = compute_grade(alt, dist)

    # pace s/m from speed (m/s) or distance diff
    if len(raw.speed_ms) == n and np.any(raw.speed_ms > 0):
        speed = np.where(raw.speed_ms > 0.1, raw.speed_ms, 0.1)
        pace_s_per_m = 1.0 / speed
    else:
        dd = np.diff(dist, prepend=dist[0])
        dt = np.diff(time_s, prepend=time_s[0]) if len(time_s) == n else np.ones(n)
        dd = np.where(dd < 0.1, 0.1, dd)
        pace_s_per_m = dt / dd

    gap_s_per_m = compute_gap(pace_s_per_m, grade)

    # Downsampled timeseries for charts
    series = []
    for i in range(0, n, step):
        point: dict = {
            "t": int(time_s[i]) if len(time_s) > i else i,
            "dist_m": round(float(dist[i]), 1),
            "alt_m": round(float(alt[i]), 1),
            "grade_pct": round(float(grade[i]), 1),
            "pace_s_km": round(float(pace_s_per_m[i]) * 1000, 1),
            "gap_s_km": round(float(gap_s_per_m[i]) * 1000, 1),
        }
        if len(raw.heart_rate_bpm) == n:
            point["hr"] = round(float(raw.heart_rate_bpm[i]))
        if len(raw.cadence_rpm) == n:
            point["cadence"] = round(float(raw.cadence_rpm[i]))
        series.append(point)

    # Climb segments with VAM
    segs = segment_climbs(alt, dist)
    segs_with_vam = compute_vam(segs, time_s) if len(time_s) == n else segs

    # HR drift
    hr_drift = None
    if len(raw.heart_rate_bpm) == n:
        hr_drift = compute_hr_drift(raw.heart_rate_bpm, gap_s_per_m)

    # Grade vs cadence scatter (downsampled further)
    gc_step = max(1, n // 500)
    grade_cadence = []
    if len(raw.cadence_rpm) == n:
        for i in range(0, n, gc_step):
            cad = float(raw.cadence_rpm[i])
            if cad > 0:
                grade_cadence.append({
                    "grade_pct": round(float(grade[i]), 1),
                    "cadence": round(cad),
                })

    return {
        "workout_id": workout_id,
        "is_trail": True,
        "series": series,
        "climb_segments": segs_with_vam,
        "hr_drift": hr_drift,
        "grade_cadence": grade_cadence,
        "total_gain_m": round(float(np.sum(np.diff(alt, prepend=alt[0]).clip(min=0))), 1),
    }


def _workout_summary(w: WorkoutFile, tag: Optional[ActivityTag] = None) -> dict:
    metrics = {m.metric_key: m.value for m in w.metrics}
    return {
        "id": w.id,
        "date": w.workout_date.isoformat() if w.workout_date else None,
        "sport": w.sport,
        "duration_s": w.duration_s,
        "file_format": w.file_format,
        "source": w.source,
        "metrics": metrics,
        "trail_classification": w.trail_classification,
        "classification_overridden": bool(w.classification_overridden),
        "activity": _tag_json(tag),
    }


def _workout_detail(w: WorkoutFile, tag: Optional[ActivityTag] = None) -> dict:
    return {**_workout_summary(w, tag), "file_path": w.file_path}
