"""SP-410: times stored in UTC (plan_sessions.updated_at, workout_files.start_time_utc) are shown in
the athlete's zone (athlete_tz: the setting, the env, the detected zone), not sliced from the UTC
string — the calculator's 「會覆蓋上次的匯出（…）」 and the activity editor's 「其他來源的同一次運動」."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from backend.db.models import PlanSession, WorkoutFile
from backend.engine import localtime
from backend.engine import plan_store as PS
from backend.engine.wko5expr import datasource
from backend.sync import dedup
from backend.tests.test_plan_store import KEY, make_db, race_data, run
from backend.tests.test_sync_e2e import make_session

STATIC = Path(__file__).resolve().parents[1] / "static"
UTC0 = dt.datetime(2026, 10, 9, 22, 2, 0)          # naive UTC: 06:02 the next day in Taipei


@pytest.fixture
def zone(monkeypatch):
    """The athlete's zone, whatever it is (never a hard-coded Taiwan)."""
    def use(name):
        monkeypatch.setattr(datasource, "athlete_tz", lambda user_id=1: ZoneInfo(name))
    use("Asia/Taipei")
    return use


def test_local_iso_is_the_athletes_wall_time(zone):
    assert localtime.local_iso(UTC0) == "2026-10-10T06:02:00"
    assert localtime.local_iso(UTC0.replace(tzinfo=dt.timezone.utc)) == "2026-10-10T06:02:00"
    zone("America/Denver")                          # UTC−6 in October
    assert localtime.local_iso(UTC0) == "2026-10-09T16:02:00"
    assert localtime.local_iso(None) is None


def test_other_sources_carry_the_local_start(zone, tmp_path):
    async def go():
        s = await make_session(tmp_path)
        a = WorkoutFile(athlete_id=1, file_path="a.fit", file_format="fit", source="coros", start_time_utc=UTC0,
                        duration_s=3600, total_distance_m=10000)
        b = WorkoutFile(athlete_id=1, file_path="b.json", file_format="json", source="apple_health",
                        start_time_utc=UTC0 + dt.timedelta(seconds=6), duration_s=3580, total_distance_m=9900)
        c = WorkoutFile(athlete_id=1, file_path="c.fit", file_format="fit", source="local",
                        start_time_utc=UTC0 + dt.timedelta(minutes=12), duration_s=3000, total_distance_m=8000)
        s.add_all([a, b, c])
        await s.flush()
        await dedup.resolve(s, b)
        return await dedup.near(s, 1, a.id)
    near = run(go())
    assert [r["start_local"][11:16] for r in near["group"]] == ["06:02", "06:02"]
    assert near["candidates"][0]["start_local"] == "2026-10-10T06:14:00"
    assert near["group"][0]["start"] == "2026-10-09T22:02:00"           # the UTC field stays as it was


def test_overwrite_warning_carries_the_local_time_of_the_last_export(zone):
    db = run(make_db())
    run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))
    row = run(db.execute(select(PlanSession).where(PlanSession.ext_key == KEY))).scalar_one()
    row.updated_at = dt.datetime(2026, 10, 13, 2, 43, 10)               # exported at 10:43 Taipei
    run(db.commit())
    prev = run(PS.upsert_external(db, KEY, race_data(minutes=100), "2026-09-30", write=False))["previous"]
    assert prev["updated_local"] == "2026-10-13T10:43:10"
    assert prev["updated_at"] == "2026-10-13T02:43:10"


def test_the_pages_show_the_local_fields_not_a_slice_of_utc():
    act = (STATIC / "activity.html").read_text("utf-8")
    dup = act[act.index("async function renderDup"):]
    dup = dup[:dup.index("\n}\n")]
    assert "start_local" in dup and not re.search(r"r\.start\.slice", dup)
    rp = (STATIC / "racepower.html").read_text("utf-8")
    assert "pv.updated_local" in rp and "pv.updated_at" not in rp
