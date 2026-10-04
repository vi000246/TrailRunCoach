from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Date, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Athlete(Base):
    __tablename__ = "athletes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    tp_athlete_id: Mapped[Optional[int]]
    data_dir: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    workouts: Mapped[list["WorkoutFile"]] = relationship(back_populates="athlete")
    settings: Mapped[list["AthleteSettings"]] = relationship(back_populates="athlete")


class AthleteSettings(Base):
    __tablename__ = "athlete_settings"
    __table_args__ = (UniqueConstraint("athlete_id", "effective_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
    effective_date: Mapped[date] = mapped_column(Date)
    ftp_w: Mapped[Optional[float]]
    weight_kg: Mapped[Optional[float]]
    lthr: Mapped[Optional[int]]
    threshold_pace_s_per_km: Mapped[Optional[float]] = mapped_column(nullable=True)
    run_ftp_w: Mapped[Optional[float]] = mapped_column(nullable=True)
    initial_ctl_run: Mapped[Optional[float]] = mapped_column(nullable=True)
    initial_atl_run: Mapped[Optional[float]] = mapped_column(nullable=True)
    athlete: Mapped["Athlete"] = relationship(back_populates="settings")


class WorkoutFile(Base):
    __tablename__ = "workout_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
    file_path: Mapped[str] = mapped_column(String(500), unique=True)
    file_format: Mapped[str] = mapped_column(String(10))
    workout_date: Mapped[Optional[date]] = mapped_column(Date, index=True)   # athlete-local date
    # activity start in UTC (naive, as SQLite stores it); used to match the
    # same activity across sources
    start_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)
    # set when this row is the same activity as another row from a
    # non-primary source; totals (PMC, analytics) skip such rows
    duplicate_of: Mapped[Optional[int]] = mapped_column(ForeignKey("workout_files.id"), nullable=True, index=True)
    sport: Mapped[Optional[str]] = mapped_column(String(50))
    duration_s: Mapped[Optional[float]]
    total_distance_m: Mapped[Optional[float]]
    source: Mapped[str] = mapped_column(String(20), default="local")
    tp_workout_id: Mapped[Optional[int]]
    coros_activity_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    coros_sport_type: Mapped[Optional[int]]
    elevation_gain_m: Mapped[Optional[float]] = mapped_column(nullable=True)
    trail_classification: Mapped[Optional[str]] = mapped_column(String(20), default="unknown")
    classification_overridden: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    # the watch's post-workout self-rating from the FIT session (engine/
    # activity_tags.recorded_from_session): workout_rpe as RPE 1–10, and
    # workout_feel 0–100 (0 very weak … 100 very strong). NULL = not recorded
    rpe: Mapped[Optional[float]] = mapped_column(nullable=True)
    feel: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    athlete: Mapped["Athlete"] = relationship(back_populates="workouts")
    metrics: Mapped[list["WorkoutMetric"]] = relationship(back_populates="workout", cascade="all, delete-orphan")
    mmp_cache: Mapped[list["MmpCache"]] = relationship(back_populates="workout", cascade="all, delete-orphan")


class WorkoutMetric(Base):
    __tablename__ = "workout_metrics"
    __table_args__ = (UniqueConstraint("workout_id", "metric_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workout_files.id"))
    metric_key: Mapped[str] = mapped_column(String(50))
    value: Mapped[Optional[float]]
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    workout: Mapped["WorkoutFile"] = relationship(back_populates="metrics")


class MmpCache(Base):
    __tablename__ = "mmp_cache"
    __table_args__ = (UniqueConstraint("workout_id", "channel", "duration_s"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workout_files.id"))
    channel: Mapped[str] = mapped_column(String(30), default="power")
    duration_s: Mapped[int]
    value: Mapped[Optional[float]]
    workout: Mapped["WorkoutFile"] = relationship(back_populates="mmp_cache")


class PmcCache(Base):
    __tablename__ = "pmc_cache"
    __table_args__ = (UniqueConstraint("athlete_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    ctl: Mapped[Optional[float]]
    atl: Mapped[Optional[float]]
    tsb: Mapped[Optional[float]]
    ramp_rate: Mapped[Optional[float]]
    tss: Mapped[Optional[float]]


class SyncState(Base):
    __tablename__ = "sync_state"
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), primary_key=True)
    tp_access_token: Mapped[Optional[str]] = mapped_column(Text)
    tp_refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    tp_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # website-login session cookie (sealed); re-issues access tokens
    tp_web_cookie: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_sync_cursor: Mapped[Optional[str]] = mapped_column(String(50))
    coros_access_token: Mapped[Optional[str]] = mapped_column(Text)
    coros_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime)
    coros_last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    coros_email: Mapped[Optional[str]] = mapped_column(String(200))
    coros_base_url: Mapped[Optional[str]] = mapped_column(String(100))
    coros_user_id: Mapped[Optional[str]] = mapped_column(String(50))
    # 「記住密碼」 (opt-in): the password sealed with the local key (settings/secrets.py),
    # for the automatic re-login; deleted on untick / logout; never returned by an API
    coros_password_sealed: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tp_username: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tp_password_sealed: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CorosPlanPush(Base):
    """A week-plan session pushed to COROS Training Hub (sync/coros_workouts.py):
    session key -> COROS program (library) id + schedule (calendar) ids. The push record of
    the workout-sync providers (sync/workout_targets): `provider` = the provider id (added
    2026-10-02, existing rows = "coros"). The unique key is still (athlete, session key):
    only one provider is active; enabling a second one needs (athlete, provider, key)."""
    __tablename__ = "coros_plan_push"
    __table_args__ = (UniqueConstraint("athlete_id", "session_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="coros",
                                                    server_default="coros")
    session_key: Mapped[str] = mapped_column(String(80))        # the stored plan session's uid (plan_store.push_dict)
    week_start: Mapped[str] = mapped_column(String(10), index=True)
    session_id: Mapped[str] = mapped_column(String(40))
    day: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)   # ISO date on the calendar
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    program_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    plan_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    id_in_plan: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    plan_program_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pushed")  # pushed / failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PlanSession(Base):
    """The stored, editable training plan (engine/plan_store.py): one row per
    planned session. Auto rows come from the generator, custom rows from the
    user; `edited` rows are never overwritten by regeneration."""
    __tablename__ = "plan_sessions"
    __table_args__ = (UniqueConstraint("athlete_id", "uid"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    uid: Mapped[str] = mapped_column(String(40))
    week_start: Mapped[str] = mapped_column(String(10), index=True)
    gen_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    day: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tss: Mapped[Optional[float]] = mapped_column(nullable=True)
    origin: Mapped[str] = mapped_column(String(10), default="auto")      # auto / custom
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(12), default="active")    # active/done/missed/deleted/superseded
    done_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON activity row
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # terrain (road / trail / hike) and the designed distance / climb (課表偏好,
    # engine/equivalence.py same-load conversion); None = unspecified
    terrain: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    distance_km: Mapped[Optional[float]] = mapped_column(nullable=True)
    climb_m: Mapped[Optional[float]] = mapped_column(nullable=True)
    # CP-test protocol of a kind "test" session (engine/cp_protocols.py:
    # quick / standard / race); None = legacy row (read from the title)
    protocol: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    # the interval library (engine/interval_library.py; interval-prescription.md §C5.4): the
    # variant, the ladder step it serves, whether it counts for progression (equiv), who
    # chose it (auto / user / cap) and why; fewer reps, the warm-up level, the state
    # machine's tweak (JSON). None = not a library session (legacy rows: the title).
    variant_key: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    rung_key: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    equiv: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    swap: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    swap_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    variant_reps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    variant_blocks: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    variant_adj: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 目標用 (engine/target_policy.py): the user's per-session hr / power; None = 自動
    target_basis: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    # the structure the user saved in the 課表 editor (engine/workout_steps.py, JSON);
    # None = derived from the kind / variant / text when opened or pushed
    steps: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # a session written from outside the generator (賽事計算機「匯出至課表」: racecalc:<event id>):
    # one row per key, re-exporting updates it; ext_sig = the fingerprint of what was exported
    # (plan_store.ext_signature), so a later edit on the 課表 page is noticed before overwriting
    ext_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ext_sig: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlanChangeLog(Base):
    """One automatic plan run (engine/plan_auto.py) or a user decision on one:
    what changed and why, the affected sessions before / after (for 復原), the
    COROS push outcome, and a held proposal waiting for approval. New table:
    created by init_db's create_all (the schema's migration for new tables)."""
    __tablename__ = "plan_change_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    trigger: Mapped[str] = mapped_column(String(40))          # sync:coros / sync:tp / manual / approve / undo …
    # applied / pending / approved / rejected / superseded / undone / restore / failed
    status: Mapped[str] = mapped_column(String(12), index=True)
    summary: Mapped[str] = mapped_column(Text)                # Traditional Chinese, one line
    items_json: Mapped[str] = mapped_column(Text, default="[]")      # [{action, uid, day, title, reason, rule}]
    before_json: Mapped[str] = mapped_column(Text, default="[]")     # affected stored sessions before
    after_json: Mapped[str] = mapped_column(Text, default="[]")      # … and after
    big_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # why it was held
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    push_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # {status, error, sent, removed}
    notice_uid: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the entry an undo / approve acts on


class ActivityTag(Base):
    """The user's activity metadata (engine/activity_tags.py): activity type
    (比賽 / 練跑 / 爬山 / 百岳跟團 / 測試 / 其他), effort (全力 / 有拼但有休息 /
    一般 / 輕鬆) and a note — like WKO5's workout metadata. Only USER values
    are stored; the auto values are computed by the engine at read time, so
    re-classification can never clobber a user mark. `*_overridden` says
    which field the user set (False + NULL = use the auto value).

    Keyed by the activity's local start minute ('YYYY-MM-DDTHH:MM', as
    Dataset entry.start), not by a workout_files row: the race-power engine
    reads the WKO5 / COROS / TP datasets, and most WKO5 activities have no
    workout_files row. `file` (the dataset's entry.file, e.g. a .wko4 name)
    is matched first when present; `workout_id` links a workout_files row
    when the tag was set through /api/v1/workouts/{id}/activity."""
    __tablename__ = "activity_tags"
    __table_args__ = (UniqueConstraint("athlete_id", "start_local"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    start_local: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    file: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    workout_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    distance_km: Mapped[Optional[float]] = mapped_column(nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    activity_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    activity_type_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    effort: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    effort_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # bad activity files (engine/bad_activity.py): "keep" = 這筆是正常的，不要排除,
    # "exclude" = 手動排除, NULL = the auto rule
    exclusion: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # 活動編輯 page (2026-10-02): the user's title (NULL = the original title)
    # and free-form tags (a JSON list of strings, NULL = none)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 疼痛 (engine/injuries.py, docs/plans/injury-tracking.plan.md §1.1): NULL = 沒填,
    # 0 = 沒痛, 1 = 痠, 2 = 痛, 3 = 痛到中斷; the body area (injuries.AREAS key or a
    # user-added area) and the injury_events row the mark is attached to
    pain: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pain_area: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    injury_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InjuryEvent(Base):
    """One injury (傷病紀錄, engine/injuries.py): a period with an onset, an
    area, a severity defined by the training impact (輕 照練 / 中 減量或改練 /
    重 停跑) and an end. Local only: never synced, shared or sent to the AI
    coach. No FK to an activity: the onset activity is the activity_tags key
    (start minute) plus the dataset file, as activity_tags. New table:
    created by init_db's create_all."""
    __tablename__ = "injury_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    area: Mapped[str] = mapped_column(String(40), default="unknown")
    side: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="overuse")
    severity: Mapped[str] = mapped_column(String(10), default="mild")
    pain_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    onset_date: Mapped[str] = mapped_column(String(10), index=True)
    onset_key: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    onset_file: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="draft")
    resolved_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    days_missed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pause_quality: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_of: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventGpx(Base):
    """The GPX stored with a season-plan event (engine/event_gpx.py, which reads
    and writes it with plain sqlite3 and the same DDL). The file itself is
    <HOME>/event_gpx/<event_id>.gz; events live in plan.json, so event_id is
    the plan's event id (no FK). New table: created by init_db's create_all."""
    __tablename__ = "event_gpx"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sha1: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    bytes_raw: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bytes_gz: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gain_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    loss_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    z_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    z_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    day_splits_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # [km, …] the user's day ends
    camp_km_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # camp / hut waypoints' km
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class RaceCalc(Base):
    """The race calculator's saved inputs + last result per season-plan event
    (engine/race_calc_store.py, plain sqlite3 with the same DDL). New table:
    created by init_db's create_all (additive)."""
    __tablename__ = "race_calc"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    inputs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class WorkoutTemplateUser(Base):
    """The user's own 課表範本 (engine/user_templates.py, 範本 page; SP-36): a step structure
    (engine/workout_steps.py, relative targets resolved when applied), several categories
    (built-in ids easy / quality / test / trail, custom ones c<id>), the 目標用 it was made
    for, and an optional training-route GPX (the file: <HOME>/template_gpx/<id>.gz; its
    elevation profile cached here). New table: created by init_db's create_all."""
    __tablename__ = "workout_templates_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    cats_json: Mapped[str] = mapped_column(Text, default="[]")
    target_basis: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)   # hr / power; None = 自動
    steps_json: Mapped[str] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    copied_from: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)   # the built-in row key
    gpx_filename: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    gpx_sha1: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    gpx_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gpx_gain_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gpx_loss_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gpx_profile_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # {"km": [...], "z": [...]}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkoutTemplateCat(Base):
    """A category the user added to the 範本 page (SP-36), id c<id> in a template's cats."""
    __tablename__ = "workout_template_cats"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserSetting(Base):
    """Per-user key/value settings (backend/settings/repository.py)."""
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(100))
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
