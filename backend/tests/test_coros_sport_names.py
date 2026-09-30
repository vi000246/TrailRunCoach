"""COROS sync file names take their sport from the FIT session (sport +
sub_sport), not from COROS's sportType code — code 100 used to map to
"cycling", so every run was saved as *_cycling.fit. Plus the migration that
renames the old files."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import WorkoutFile
from backend.engine.wko5expr import datasource as DSRC
from backend.scripts import migrate_coros_sport_names as MIG
from backend.sync import coros_client, http, storage
from backend.sync.coros_sport import COROS_SPORT_TYPES, sport_token
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import FakeCoros, collect, make_session, run

START = datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("sport,sub,code,want", [
    ("running", None, 100, "run"),
    ("running", "generic", 100, "run"),
    ("running", "trail", 102, "trail_run"),
    ("running", "treadmill", None, "treadmill"),
    ("running", "indoor_running", 100, "indoor_run"),
    ("cycling", "indoor_cycling", 100, "indoor_cycling"),      # the FIT wins over the code
    ("cycling", None, None, "cycling"),
    ("training", "strength_training", 402, "strength"),
    ("hiking", None, None, "hike"),
    (None, None, 100, "run"),                                  # unreadable FIT: the code
    (None, None, 102, "trail_run"),
    (None, None, 12345, "other"),
    ("generic", None, None, "other"),
])
def test_sport_token(sport, sub, code, want):
    assert sport_token(sport, sub, code) == want


def test_code_100_is_run_not_cycling():
    assert COROS_SPORT_TYPES[100] == "run" and coros_client.SPORT_NAMES[100] == "run"


def _act(label, start, code, **kw):
    return {"labelId": label, "date": int(start.strftime("%Y%m%d")), "sportType": code,
            "fitUrl": f"https://files.example/fit/{label}", "_bytes": build_run(start, **kw)}


def test_sync_names_files_from_the_fit_session(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([
            _act("111", START, 100),                                       # COROS "run" code, FIT running
            _act("222", START + timedelta(days=1), 100, sub_sport=3),      # trail run
            _act("333", START + timedelta(days=2), 402, sport=10, sub_sport=20),   # strength
        ])
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        got = {e["activity_id"]: (e["file"], e["sport"]) for e in ev if e.get("status") == "downloaded"}
        # (the name's date is COROS's `date` field, here START's UTC day)
        assert got == {"111": ("111_2026-09-01_run.fit", "run"),
                       "222": ("222_2026-09-02_trail_run.fit", "trail_run"),
                       "333": ("333_2026-09-03_strength.fit", "strength")}
        rows = {r.coros_activity_id: r for r in (await s.execute(select(WorkoutFile))).scalars()}
        assert rows["111"].sport == "running" and rows["111"].coros_sport_type == 100
        assert rows["333"].sport == "training"
        assert rows["111"].file_path.endswith("111_2026-09-01_run.fit")
        files = sorted(p.name for p in (_fit_root_in_tmp / "coros").rglob("*") if p.is_file())
        assert files == ["111_2026-09-01_run.fit", "222_2026-09-02_trail_run.fit",
                         "333_2026-09-03_strength.fit"]              # no leftover download temp
    run(go())


def _old(root, name, **kw):
    d = root / "coros" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(build_run(START, **kw))
    return p


def test_migration_renames_old_files_and_rows_and_is_idempotent(tmp_path, _fit_root_in_tmp):
    run_p = _old(_fit_root_in_tmp, "480707326343414059_2026-09-30_cycling.fit")
    trail_p = _old(_fit_root_in_tmp, "480470392861917592_2026-09-20_other.fit", sub_sport=3)
    ok_p = _old(_fit_root_in_tmp, "480000000000000001_2026-09-01_run.fit")
    stray = _old(_fit_root_in_tmp, "notes.fit")                          # not a sync name
    outside = tmp_path / "elsewhere" / "1_2026-09-01_cycling.fit"      # not in fit/coros
    outside.parent.mkdir()
    outside.write_bytes(build_run(START))

    async def go():
        s = await make_session(tmp_path)
        for p, lid, sp in ((run_p, "480707326343414059", "running"),
                           (trail_p, "480470392861917592", "cycling"),    # a wrong row gets fixed
                           (outside, "1", "running")):
            s.add(WorkoutFile(athlete_id=1, file_path=str(p), file_format="fit", sport=sp,
                              source="coros", coros_activity_id=lid, workout_date=date(2026, 9, 30)))
        await s.commit()

        dry = await MIG.migrate(s, apply=False)
        assert dry["renamed"] == 2 and not dry["applied"] and run_p.exists()
        out = await MIG.migrate(s, apply=True)
        assert out["renamed"] == 2 and out["rows_updated"] == 2 and out["sport_fixed"] == 1
        assert out["already"] == 1 and out["skipped"] == 1
        names = sorted(p.name for p in (_fit_root_in_tmp / "coros" / "2026").iterdir())
        assert names == ["480000000000000001_2026-09-01_run.fit",
                         "480470392861917592_2026-09-20_trail_run.fit",
                         "480707326343414059_2026-09-30_run.fit", "notes.fit"]
        rows = {r.coros_activity_id: r for r in (await s.execute(select(WorkoutFile))).scalars()}
        assert rows["480707326343414059"].file_path.endswith("480707326343414059_2026-09-30_run.fit")
        assert rows["480470392861917592"].sport == "running"
        assert rows["1"].file_path == str(outside) and outside.exists()      # outside fit/coros: untouched
        again = await MIG.migrate(s, apply=True)
        assert again["renamed"] == 0 and again["rows_updated"] == 0 and again["sport_fixed"] == 0
        assert again["already"] == 3
    run(go())
    assert stray.exists()


def test_a_rename_changes_the_source_stamp(_fit_root_in_tmp):
    p = _old(_fit_root_in_tmp, "1_2026-09-01_cycling.fit")
    s0 = DSRC.source_stamp("coros", _fit_root_in_tmp)
    p.rename(p.with_name("1_2026-09-01_run.fit"))
    assert DSRC.source_stamp("coros", _fit_root_in_tmp) != s0
