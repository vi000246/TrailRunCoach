"""SP-344: the same run from many sources counts once — COROS, Apple Health (through a bridge,
a rebuilt export), a manual import; seconds apart, distances a little different — while two
different runs (morning / evening, a warm-up recorded on its own, a ride at the same minute) stay
apart; the user merges or splits by hand; the chart Dataset counts the duplicate once."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace as N

from sqlalchemy import select

from backend.db.models import ActivityLink, WorkoutFile
from backend.sync import dedup
from backend.tests.test_sync_e2e import make_session, run

T0 = datetime(2026, 10, 10, 6, 0, 0)


def _wf(i, src, fmt="fit", start=T0, dur=3600.0, dist=10000.0, sport="running", path=None):
    return WorkoutFile(id=i, athlete_id=1, file_path=path or f"/f/{src}/{i}.{fmt}", file_format=fmt,
                       source=src, start_time_utc=start, duration_s=dur, total_distance_m=dist, sport=sport)


def _groups(rows, links=()):
    return sorted(sorted(r.id for r in g) for g in dedup.group_rows(rows, links))


def test_one_run_from_coros_apple_health_and_a_manual_import_is_one_activity():
    rows = [_wf(1, "coros"),
            _wf(2, "apple_health", fmt="json", start=T0 + timedelta(seconds=8), dist=9810, dur=3570),
            _wf(3, "local", start=T0 + timedelta(seconds=3), dist=10120)]
    assert _groups(rows) == [[1, 2, 3]]
    # the original FIT counts, not the rebuilt export; with COROS as the 資料來源, COROS's
    assert dedup.choose_canonical(rows, "coros").id == 1
    assert dedup.choose_canonical(rows, None).id == 1                  # FIT before json, then the oldest
    rows2 = [_wf(5, "apple_health", fmt="json"), _wf(6, "local", start=T0 + timedelta(seconds=40))]
    assert dedup.choose_canonical(rows2, None).id == 6                 # the most complete copy wins over the older


def test_two_different_runs_stay_apart():
    morning = _wf(1, "coros", start=T0)
    evening = _wf(2, "coros", start=T0 + timedelta(hours=11))
    warmup = _wf(3, "coros", start=T0 + timedelta(seconds=30), dur=600, dist=1500)       # recorded on its own
    ride = _wf(4, "local", start=T0 + timedelta(seconds=10), sport="cycling", dist=30000)
    other = _wf(5, "local", start=T0 + timedelta(seconds=20), dist=21000)                # a very different distance
    assert _groups([morning, evening, warmup, ride, other]) == [[1], [2], [3], [4], [5]]


def test_far_apart_starts_never_merge_but_the_same_content_does():
    a = _wf(1, "coros", path="/x/fit/objects/ab/" + "ab" * 32 + ".fit.gz")
    b = _wf(2, "local", start=T0 + timedelta(hours=3), path="/y/fit/objects/ab/" + "ab" * 32 + ".fit.gz")
    c = _wf(3, "local", start=T0 + timedelta(minutes=5))
    assert _groups([a, b, c]) == [[1, 2], [3]]                         # same bytes = same activity (SP-308)


def test_the_users_links_win():
    a, b = _wf(1, "coros"), _wf(2, "local", start=T0 + timedelta(seconds=5))
    far = _wf(3, "local", start=T0 + timedelta(minutes=20))
    links = [N(a_id=1, b_id=2, kind="different"), N(a_id=1, b_id=3, kind="same")]
    assert _groups([a, b, far], links) == [[1, 3], [2]]


def test_merge_split_and_back_to_automatic_in_the_db(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        a = WorkoutFile(athlete_id=1, file_path="a.fit", file_format="fit", source="coros", start_time_utc=T0,
                        duration_s=3600, total_distance_m=10000)
        b = WorkoutFile(athlete_id=1, file_path="b.json", file_format="json", source="apple_health",
                        start_time_utc=T0 + timedelta(seconds=6), duration_s=3580, total_distance_m=9900)
        c = WorkoutFile(athlete_id=1, file_path="c.fit", file_format="fit", source="local",
                        start_time_utc=T0 + timedelta(minutes=12), duration_s=3000, total_distance_m=8000)
        s.add_all([a, b, c])
        await s.flush()
        await dedup.resolve(s, b)
        assert b.duplicate_of == a.id and c.duplicate_of is None
        near = await dedup.near(s, 1, a.id)
        assert {r["id"] for r in near["group"]} == {a.id, b.id} and [r["id"] for r in near["candidates"]] == [c.id]
        await dedup.split(s, 1, b.id)                                  # 「不是同一次」
        assert a.duplicate_of is None and b.duplicate_of is None
        await dedup.rebuild(s, 1)
        assert b.duplicate_of is None                                  # stays split
        await dedup.merge(s, 1, [a.id, c.id])                          # 「是同一次」, 12 minutes apart
        assert c.duplicate_of == a.id
        await dedup.resolve(s, c)
        assert c.duplicate_of == a.id
        await dedup.unlink(s, 1, b.id)
        await dedup.unlink(s, 1, c.id)                                 # 改回自動
        assert b.duplicate_of == a.id and c.duplicate_of is None
        assert (await s.execute(select(ActivityLink))).scalars().all() == []
    run(go())


def test_a_duplicate_is_counted_once_in_the_totals(tmp_path):
    async def go():
        s = await make_session(tmp_path, **{"sync__primary_source": "coros"})
        rows = [WorkoutFile(athlete_id=1, file_path=f"r{i}", file_format=f, source=src,
                            start_time_utc=T0 + timedelta(seconds=sec), duration_s=3600, total_distance_m=10000)
                for i, (src, f, sec) in enumerate([("coros", "fit", 0), ("local", "fit", 4), ("apple_health", "json", 9)])]
        s.add_all(rows)
        await s.flush()
        for r in rows:
            await dedup.resolve(s, r)
        counted = (await s.execute(select(WorkoutFile.id).where(await dedup.in_use(s, 1)))).scalars().all()
        assert counted == [rows[0].id]
    run(go())


def test_the_chart_dataset_counts_a_duplicate_once():
    from pathlib import Path

    from backend.engine.wko5expr.fitdataset import drop_duplicates
    rows = {"_by_id": {}, "_by_name": {}, "_dups": {}}

    def add(i, fp, dup=None):
        r = {"id": i, "file_path": fp, "trail_classification": None, "classification_overridden": False,
             "duplicate_of": dup, "coros_sport_type": None}
        rows["_by_id"][i] = r
        rows[str(Path(fp).resolve())] = r
        rows["_by_name"].setdefault(Path(fp).name, []).append(r)
        if dup:
            rows["_dups"].setdefault(dup, []).append(r)
    import os
    add(1, "/d/coros/a.fit")
    add(2, "/d/local/b.fit", dup=1)                 # in this Dataset too: left out
    add(3, "/d/local/c.fit", dup=99)                # its canonical copy is elsewhere: kept
    norm = lambda p: os.path.normcase(os.path.abspath(p))
    for i in (1, 2, 3):
        rows[norm(rows["_by_id"][i]["file_path"])] = rows["_by_id"][i]
    entries = [(T0, Path("/d/coros/a.fit"), {}), (T0, Path("/d/local/b.fit"), {}), (T0, Path("/d/local/c.fit"), {})]
    assert [e[1].name for e in drop_duplicates(entries, rows)] == ["a.fit", "c.fit"]
