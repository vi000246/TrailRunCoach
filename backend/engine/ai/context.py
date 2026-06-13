"""Build AI coach context from athlete DB state."""
from __future__ import annotations
from datetime import date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import AthleteSettings, PmcCache, WorkoutFile, WorkoutMetric
from backend.engine.ai.knowledge import build_knowledge
from backend.engine.ai.zones import compute_zones


SYSTEM_PROMPT = f"""你是一位專業的越野跑教練AI助理。請一律用繁體中文回覆。

{build_knowledge()}

根據運動員的訓練數據與上述知識，提供具體、可執行的建議。
回覆結構：
(1) 目前訓練狀態判讀（依 TSB/CTL/ATL）
(2) 建議今天 / 近期該做的 zone（附功率 W 或心率 bpm 範圍）
(3) 若需間歇，給出處方（時間 × 目標瓦數或 zone × 組數 × 恢復）
使用條列式、簡潔清晰。"""


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

    # Training zone boundaries (so the coach can prescribe concrete W / bpm)
    if settings:
        z = compute_zones(settings.run_ftp_w or settings.ftp_w, settings.lthr)
        if z["power"]:
            lines.append("\n=== 功率區間 (W) ===")
            for p in z["power"]:
                hi = f"{p['high_w']:.0f}" if p["high_w"] else "+"
                lines.append(f"Z{p['zone']} {p['name']}: {p['low_w']:.0f}–{hi}")
        if z["hr"]:
            lines.append("\n=== 心率區間 (bpm) ===")
            for h in z["hr"]:
                hi = f"{h['high_bpm']:.0f}" if h["high_bpm"] else "+"
                lines.append(f"Z{h['zone']} {h['name']}: {h['low_bpm']:.0f}–{hi}")

    return "\n".join(lines)
