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
    ai_provider: Mapped[Optional[str]] = mapped_column(nullable=True)
    ai_api_key:  Mapped[Optional[str]] = mapped_column(nullable=True)
    ai_model:    Mapped[Optional[str]] = mapped_column(nullable=True)
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
    session key -> COROS program (library) id + schedule (calendar) ids."""
    __tablename__ = "coros_plan_push"
    __table_args__ = (UniqueConstraint("athlete_id", "session_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserSetting(Base):
    """Per-user key/value settings (backend/settings/repository.py)."""
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(100))
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DashboardConfig(Base):
    __tablename__ = "dashboard_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
    name: Mapped[str] = mapped_column(String(100))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    layout_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
