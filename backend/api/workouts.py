"""workout_files rows: the user's trail / road override (activity.html) and the
activity tags (save_activity_tag, also called by the wko5views activity routes).
The React SPA's GET list / detail / mmp / timeseries / zones / trail routes were
removed with it (2026-10-04)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from pydantic import BaseModel

from backend.db.database import get_db
from backend.db.models import WorkoutFile, ActivityTag

router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])


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
    # SP-271: the optional 0–10 「跑的時候最痛幾分」 (null = not given; cleared with the mark)
    pain_score: Optional[int] = None
    # 登山杖 (SP-242): "with" 有杖 / "without" 沒杖 / "none" the user's 未標 / null no choice (SP-300:
    # a race's 「會用登山杖」 then applies) — stored as one free-form tag
    poles: Optional[str] = None
    # 路況 (SP-250): "dry" 乾 / "wet" 濕 / null 未標 — stored as one free-form tag
    surface: Optional[str] = None


TAG_FIELDS = ("activity_type", "effort", "note", "exclusion", "name", "tags", "pain", "pain_area", "pain_side",
              "poles", "surface", "pain_score")
STORED_FIELDS = ("activity_type", "effort", "note", "exclusion", "name", "tags", "pain", "pain_area", "poles",
                 "surface", "pain_score")


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
                      body.pain_area if "pain_area" in sent else None,
                      body.poles if "poles" in sent else None,
                      body.surface if "surface" in sent else None,
                      body.pain_score if "pain_score" in sent else None)
    painish = bool({"pain", "pain_area", "pain_side", "pain_score"} & sent)
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
            **AT.pole_state(AT.tags_of({"tags_json": t.tags_json}) if t else [], None),
            "surface": AT.surface_of(AT.tags_of({"tags_json": t.tags_json})) if t else None,
            "pain": t.pain if t else None, "pain_area": t.pain_area if t else None,
            "pain_score": t.pain_score if t else None,
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
