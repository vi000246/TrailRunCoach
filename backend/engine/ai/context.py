"""Build AI coach context from athlete DB state."""
from __future__ import annotations
from datetime import date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import AthleteSettings, PmcCache, WorkoutFile, WorkoutMetric


SYSTEM_PROMPT = """你是一位專業的越野跑教練AI助理。
請一律用繁體中文回覆。
根據以下運動員的訓練數據分析訓練狀況，提供具體、實用的建議。
分析時請考慮：訓練壓力、疲勞、狀態、訓練量趨勢、強度分布。
回覆請簡潔清晰，使用條列式格式。"""


async def build_context(db: AsyncSession, athlete_id: int) -> str:
    """Assemble recent training context as a text block for the AI prompt."""
    lines: list[str] = ["=== 運動員訓練數據 ==="]

    # Settings
    s_result = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = s_result.scalars().first()
    if settings:
        lines.append(f"FTP (跑步功率): {settings.run_ftp_w or settings.ftp_w} W")
        if settings.lthr:
            lines.append(f"LTHR: {settings.lthr} bpm")
        if settings.threshold_pace_s_per_km:
            pace = settings.threshold_pace_s_per_km
            mins, secs = divmod(int(pace), 60)
            lines.append(f"閾值配速: {mins}:{secs:02d} /km")

    # PMC last 42 days
    today = date.today()
    start = today - timedelta(days=42)
    pmc_result = await db.execute(
        select(PmcCache)
        .where(PmcCache.athlete_id == athlete_id, PmcCache.date >= start)
        .order_by(PmcCache.date.desc())
    )
    pmc_rows = pmc_result.scalars().all()
    if pmc_rows:
        latest = pmc_rows[0]
        lines.append(f"\n=== 訓練狀態 (最新: {latest.date}) ===")
        if latest.ctl is not None:
            lines.append(f"CTL (體能): {latest.ctl:.1f}")
        if latest.atl is not None:
            lines.append(f"ATL (疲勞): {latest.atl:.1f}")
        if latest.tsb is not None:
            lines.append(f"TSB (狀態): {latest.tsb:.1f}")

    # Last 7 days workouts
    week_start = today - timedelta(days=7)
    wo_result = await db.execute(
        select(WorkoutFile)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.workout_date >= week_start,
        )
        .order_by(WorkoutFile.workout_date.desc())
    )
    workouts = wo_result.scalars().all()
    if workouts:
        lines.append(f"\n=== 近7天訓練 ({len(workouts)} 次) ===")
        total_tss = 0.0
        total_hours = 0.0
        for wf in workouts:
            dur_h = (wf.duration_s or 0) / 3600
            total_hours += dur_h
            sport = wf.sport or "unknown"
            lines.append(f"- {wf.workout_date} [{sport}] {dur_h:.1f}h")
            # fetch TSS
            m_result = await db.execute(
                select(WorkoutMetric).where(
                    WorkoutMetric.workout_id == wf.id,
                    WorkoutMetric.metric_key == "tss",
                )
            )
            m = m_result.scalars().first()
            if m and m.value:
                total_tss += m.value
        lines.append(f"週TSS合計: {total_tss:.0f}, 訓練時數: {total_hours:.1f}h")

    return "\n".join(lines)
