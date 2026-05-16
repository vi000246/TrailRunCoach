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
    workout_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    sport: Mapped[Optional[str]] = mapped_column(String(50))
    duration_s: Mapped[Optional[float]]
    total_distance_m: Mapped[Optional[float]]
    source: Mapped[str] = mapped_column(String(20), default="local")
    tp_workout_id: Mapped[Optional[int]]
    coros_activity_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    coros_sport_type: Mapped[Optional[int]]
    elevation_gain_m: Mapped[Optional[float]] = mapped_column(nullable=True)
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
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_sync_cursor: Mapped[Optional[str]] = mapped_column(String(50))
    coros_access_token: Mapped[Optional[str]] = mapped_column(Text)
    coros_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime)
    coros_last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    coros_email: Mapped[Optional[str]] = mapped_column(String(200))
    coros_base_url: Mapped[Optional[str]] = mapped_column(String(100))
    coros_user_id: Mapped[Optional[str]] = mapped_column(String(50))


class DashboardConfig(Base):
    __tablename__ = "dashboard_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
    name: Mapped[str] = mapped_column(String(100))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    layout_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
