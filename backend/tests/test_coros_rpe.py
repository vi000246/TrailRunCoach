"""
SP-231: COROS's post-run self-rating (engine/coros_rpe.py, sync/coros_client read_feel /
fill_feel / backfill_feel). All COROS HTTP is a MockTransport: nothing logs in anywhere,
nothing is written to COROS (every request is recorded and checked).
"""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import SyncState, WorkoutFile
from backend.engine import activity_tags as AT
from backend.engine import coros_rpe as CR
from backend.settings.repository import SettingsRepository
from backend.sync import coros_client, http
from backend.tests.test_sync_e2e import FakeCoros, _coros_act, collect, make_session, run


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(coros_client, "DETAIL_PAUSE_S", 0)


def detail_body(feel, **extra):
    """A detail answer as COROS sends it (sportNote / voice fields present: never read)."""
    return {"result": "0000", "data": {"summary": {"trainingProgram": {"x": 1}} if feel else {},
                                       "sportFeelInfo": {"feelType": feel, "sportNote": "secret note",
                                                         "voiceNoteFileUuid": "v", "voiceNoteWavUrl": "https://x/v.wav",
                                                         "voiceNoteStatus": 1}, **extra}}


class FakeDetail(FakeCoros):
    """FakeCoros + POST /activity/detail/query: `feels` labelId -> feelType, or an
    httpx.Response / Exception to answer with. Every request is recorded."""

    def __init__(self, activities, feels=None, default=None):
        super().__init__(activities)
        self.feels = feels or {}
        self.default = default
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/activity/detail/query":
            if request.method != "POST":
                return httpx.Response(200, json={"result": "1001", "message": "GET not allowed"})
            label = request.url.params.get("labelId")
            v = self.feels.get(label, self.default)
            if isinstance(v, Exception):
                raise v
            if isinstance(v, httpx.Response):
                return httpx.Response(v.status_code, content=v.content, headers=v.headers)
            if v is None:
                return httpx.Response(500)
            return httpx.Response(200, json=detail_body(v))
        return super().__call__(request)

    def details(self):
        return [r for r in self.requests if r[1] == "/activity/detail/query"]

    def writes(self):
        """Anything but the login, the reads and the FIT downloads."""
        reads = {"/account/login", "/activity/query", "/activity/detail/query", "/account/query"}
        return [r for r in self.requests if r[1] not in reads and not r[1].startswith("/fit/")]


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------

def test_mapping_and_labels():
    assert CR.TO_RPE == {1: 2.0, 2: 4.0, 3: 5.0, 4: 7.0, 5: 10.0}
    assert CR.FEEL_LABEL[4] == "Hard" and CR.HARD_RPE == 7.0
    # the five levels are rpe_load's own (輕鬆 2、稍累 4、累 5、很累 7、極限 10)
    from backend.engine import rpe_load as RL
    assert sorted(CR.TO_RPE.values()) == sorted(float(v) for v in RL.CR10.values())


def test_parse_feel_reads_only_feel_type():
    assert CR.parse_feel(detail_body(4)) == 4
    assert CR.parse_feel(detail_body(0)) == 0
    assert CR.parse_feel({"result": "0000", "data": {"sportFeelInfo": {"feelType": "5"}}}) == 5
    for bad in (None, {}, {"result": "1001"}, {"result": "0000", "data": {}},
                {"result": "0000", "data": {"sportFeelInfo": {"feelType": 7}}},
                {"result": "0000", "data": {"sportFeelInfo": {"feelType": 2.5}}},
                {"result": "0000", "data": {"sportFeelInfo": {"feelType": True}}},
                {"result": "0000", "data": {"sportFeelInfo": {"sportNote": "x"}}}):
        assert CR.parse_feel(bad) is None, bad


class Row:
    def __init__(self, rpe=None, rpe_source=None, coros_feel=None):
        self.rpe, self.rpe_source, self.coros_feel = rpe, rpe_source, coros_feel


def test_apply_maps_and_fit_wins():
    r = Row()
    assert CR.apply(r, 4) is True and (r.rpe, r.rpe_source, r.coros_feel) == (7.0, "coros", 4)
    assert CR.apply(r, 4) is False                       # the same again: no change
    assert CR.apply(r, 3) is True and r.rpe == 5.0       # re-rated on COROS: follows
    assert CR.apply(r, 0) is True and (r.rpe, r.rpe_source, r.coros_feel) == (None, None, 0)
    fit = Row(rpe=8.0)                                   # a Garmin FIT's own RPE
    assert CR.apply(fit, 2) is False and (fit.rpe, fit.rpe_source, fit.coros_feel) == (8.0, None, 2)


def test_self_rating_text():
    sr = CR.self_rating({"rpe": 7.0, "source": "coros", "coros_feel": 4})
    assert sr["text"] == "自評：Hard（COROS）" and sr["hard"] and sr["level"] == 4 and sr["source"] == "coros"
    assert CR.self_rating({"rpe": 5.0, "source": "coros", "coros_feel": 3})["hard"] is False
    w = CR.self_rating({"rpe": 8.0, "source": "watch"})
    assert w["text"] == "自評：RPE 8（手錶）" and w["hard"] and w["level"] == 4
    assert CR.self_rating({"rpe": None, "coros_feel": 0}) is None
    from backend.i18n import use_locale
    with use_locale("en"):
        assert CR.self_rating({"rpe": 7.0, "source": "coros", "coros_feel": 4})["text"] == "Self-rating: Hard (COROS)"


def test_effort_tag_uses_the_coros_rating():
    rec = {"rpe": 7.0, "source": "coros", "coros_feel": 4}
    e = AT.effort_from_rpe(7.0, None, None, rec=rec)
    assert e["effort"] == "moderate" and e["basis"] == "rpe" and "COROS" in e["reason"] and "推估" in e["reason"]
    assert "手錶記錄" in AT.effort_from_rpe(7.0)["reason"]          # a FIT's own RPE: as before


# ---------------------------------------------------------------------------
# sync: the new activity's rating
# ---------------------------------------------------------------------------

RECENT = datetime.now(timezone.utc).replace(hour=0, minute=30, second=0, microsecond=0) - timedelta(days=1)


async def _rows(s):
    return (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()


def test_new_activity_gets_its_rating_by_post(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeDetail([_coros_act("R4", RECENT), _coros_act("R0", RECENT - timedelta(hours=3))],
                          feels={"R4": 4, "R0": 0})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["status"] == "complete" and ev[-1]["errors"] == []
        rows = {r.coros_activity_id: r for r in await _rows(s)}
        assert (rows["R4"].coros_feel, rows["R4"].rpe, rows["R4"].rpe_source) == (4, 7.0, "coros")
        assert (rows["R0"].coros_feel, rows["R0"].rpe, rows["R0"].rpe_source) == (0, None, None)   # not filled
        d = fake.details()
        assert {m for m, _p, _q in d} == {"POST"}                        # never a GET (result 1001)
        assert sorted(q["labelId"] for _m, _p, q in d) == ["R0", "R4"]   # once each, no second pass
        assert all(q["sportType"] == "200" for _m, _p, q in d)
        assert fake.writes() == []
        assert (await s.execute(select(SyncState))).scalar_one().coros_last_sync_at is not None
    run(go())


@pytest.mark.parametrize("answer", [httpx.Response(500), httpx.Response(200, json={"result": "5001"}),
                                    httpx.Response(200, content=b"not json"),
                                    httpx.ReadTimeout("slow")])
def test_detail_failure_never_breaks_the_sync_nor_holds_the_cursor(tmp_path, answer):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeDetail([_coros_act("F1", RECENT)], default=answer)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["status"] == "complete" and ev[-1]["errors"] == [] and ev[-1]["total_downloaded"] == 1
        assert not [e for e in ev if e.get("status") == "error"]
        row = (await _rows(s))[0]
        assert row.coros_feel is None and row.rpe is None                # unread: retried later
        assert (await s.execute(select(SyncState))).scalar_one().coros_last_sync_at is not None
    run(go())


def test_a_failed_read_is_retried_by_the_next_sync(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeDetail([_coros_act("F1", RECENT)], default=None)        # 500
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            await collect(coros_client.sync_workouts(s, 1))
            fake.default = 5
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["rpe_filled"] == 1
        row = (await _rows(s))[0]
        assert (row.coros_feel, row.rpe) == (5, 10.0)
    run(go())


def test_fit_rpe_wins_over_coros(tmp_path, monkeypatch):
    monkeypatch.setattr(AT, "recorded_from_session", lambda session: (8.0, 75))   # a Garmin FIT's RPE 8

    async def go():
        s = await make_session(tmp_path)
        fake = FakeDetail([_coros_act("G1", RECENT)], feels={"G1": 2})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            await collect(coros_client.sync_workouts(s, 1))
        row = (await _rows(s))[0]
        assert (row.rpe, row.rpe_source, row.coros_feel) == (8.0, None, 2)
    run(go())


# ---------------------------------------------------------------------------
# the 8-week backfill
# ---------------------------------------------------------------------------

async def _seed(s, n_recent=3, n_old=2, feel_of=None):
    """Already-imported COROS rows (before SP-231: coros_feel NULL)."""
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=7 * (i + 1)) for i in range(n_recent)]           # inside 8 weeks
    days += [today - timedelta(days=60 + 7 * i) for i in range(n_old)]             # older: never read
    for i, d in enumerate(days):
        s.add(WorkoutFile(athlete_id=1, file_path=f"/x/B{i}.fit", file_format="fit", source="coros",
                          coros_activity_id=f"B{i}", coros_sport_type=100, sport="running", workout_date=d,
                          rpe=(feel_of or {}).get(f"B{i}")))
    await s.commit()
    return days


def test_backfill_reads_the_last_8_weeks_once(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s)
        fake = FakeDetail([], feels={"B0": 4, "B1": 0, "B2": 3})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
            assert sorted(q["labelId"] for _m, _p, q in fake.details()) == ["B0", "B1", "B2"]   # not B3 / B4
            assert ev[-1]["rpe_filled"] == 2
            mark = await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY)
            assert mark["done"] is True and mark["passes"] == 1 and mark["filled"] == 2
            # idempotent: the next syncs read nothing again
            n = len(fake.details())
            ev2 = await collect(coros_client.sync_workouts(s, 1))
            assert len(fake.details()) == n and ev2[-1]["rpe_filled"] == 0
        rows = {r.coros_activity_id: r for r in await _rows(s)}
        assert rows["B0"].rpe == 7.0 and rows["B1"].coros_feel == 0 and rows["B2"].rpe == 5.0
        assert rows["B3"].coros_feel is None and rows["B4"].coros_feel is None
        assert fake.writes() == []
        assert [r for r in fake.requests if r[1] == "/account/login"] == [fake.requests[0]]   # the test's own login only
    run(go())


def test_backfill_is_rate_limited_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(coros_client, "BACKFILL_MAX", 2)
    sleeps = []

    async def fake_pause():
        sleeps.append(1)
    monkeypatch.setattr(coros_client, "_pause", fake_pause)

    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=5, n_old=0)
        fake = FakeDetail([], default=3)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            await collect(coros_client.sync_workouts(s, 1))
            assert len(fake.details()) == 2
            assert (await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY))["done"] is False
            await collect(coros_client.sync_workouts(s, 1))
            await collect(coros_client.sync_workouts(s, 1))
            assert len(fake.details()) == 5                               # each row read once
            assert (await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY))["done"] is True
        assert len(sleeps) == 2                                           # between the reads of a pass (2 + 2 + 1)
    run(go())


def test_backfill_gives_up_after_a_few_failing_passes(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=2, n_old=0)
        fake = FakeDetail([], default=None)                                # 500 every time
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            for _ in range(coros_client.BACKFILL_PASSES + 2):
                ev = await collect(coros_client.sync_workouts(s, 1))
                assert ev[-1]["errors"] == []
        mark = await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY)
        assert mark["done"] is True and mark["passes"] == coros_client.BACKFILL_PASSES
        assert len(fake.details()) == 2 * coros_client.BACKFILL_PASSES
    run(go())


def test_backfill_token_refusal_is_not_a_pass_and_never_logs_in(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=2, n_old=0)
        refused = httpx.Response(200, json={"result": "1019", "message": "Access token is invalid"})
        fake = FakeDetail([], default=refused)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["errors"] == []
        assert len(fake.details()) == 1                                  # stopped at the refusal
        assert await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY) is None
        assert len([r for r in fake.requests if r[1] == "/account/login"]) == 1
    run(go())


def test_backfill_keeps_a_fit_rpe(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=1, n_old=0, feel_of={"B0": 5.0})          # B0's FIT says RPE 5
        fake = FakeDetail([], feels={"B0": 4})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        row = (await _rows(s))[0]
        assert (row.rpe, row.rpe_source, row.coros_feel) == (5.0, None, 4) and ev[-1]["rpe_filled"] == 0
    run(go())


def test_runner_passes_rpe_filled_to_the_plan_and_calibration(tmp_path, monkeypatch):
    from backend.engine import calibrate, plan_auto
    from backend.sync import runner
    seen = {}
    monkeypatch.setattr(plan_auto, "after_sync", lambda src, res: seen.setdefault("plan", dict(res)))
    monkeypatch.setattr(calibrate, "after_sync", lambda src, res, aid: seen.setdefault("calib", dict(res)))

    async def fake_stream(db, source, athlete_id, since):
        yield {"status": "started"}
        yield {"status": "complete", "total_downloaded": 0, "total_checked": 3, "errors": [], "rpe_filled": 2}
    monkeypatch.setattr(runner, "_client_stream", fake_stream)

    async def go():
        s = await make_session(tmp_path)
        _ = [e async for e in runner.stream(s, "coros", 1)]
    run(go())
    assert seen["plan"]["rpe_filled"] == 2 and seen["calib"]["rpe_filled"] == 2


def test_after_sync_gates_start_on_rpe_filled(monkeypatch):
    import asyncio
    from backend.engine import calibrate, plan_auto
    started = []

    async def fake_plan(trigger):
        started.append(("plan", trigger))

    async def fake_cal(aid):
        started.append(("calib", aid))
    monkeypatch.setattr(plan_auto, "run_safe", fake_plan)
    monkeypatch.setattr(calibrate, "run_safe", fake_cal)

    async def go():
        assert plan_auto._after_sync("coros", {"status": "ok", "downloaded": 0}) is None
        assert calibrate._after_sync("coros", {"status": "ok", "downloaded": 0}) is None
        t1 = plan_auto._after_sync("coros", {"status": "ok", "downloaded": 0, "rpe_filled": 1})
        t2 = calibrate._after_sync("coros", {"status": "partial", "downloaded": 0, "rpe_filled": 1})
        await asyncio.gather(t1, t2)
    asyncio.run(go())
    assert sorted(x[0] for x in started) == ["calib", "plan"]


def test_load_recorded_carries_the_source(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT, start_time_utc DATETIME, "
                "rpe REAL, feel INTEGER, coros_feel INTEGER, rpe_source TEXT)")
    con.execute("INSERT INTO workout_files VALUES (1, '/a/C1.fit', '2026-09-30 22:00:00', 7.0, NULL, 4, 'coros')")
    con.execute("INSERT INTO workout_files VALUES (2, '/a/G1.fit', '2026-09-29 22:00:00', 8.0, 75, NULL, NULL)")
    con.execute("INSERT INTO workout_files VALUES (3, '/a/Z.fit', '2026-09-28 22:00:00', NULL, NULL, 0, NULL)")
    con.commit()
    con.close()
    monkeypatch.setattr(AT, "_db_path", lambda p=None: db)
    AT._rec_memo.clear()
    rows = {r["file"]: r for r in AT.load_recorded()}
    assert set(rows) == {"C1.fit", "G1.fit"}
    assert rows["C1.fit"]["source"] == "coros" and rows["C1.fit"]["coros_feel"] == 4
    assert rows["G1.fit"]["source"] == "watch"
    j = AT.recorded_json(rows["C1.fit"])
    assert j["self_rating"]["text"] == "自評：Hard（COROS）"
    AT._rec_memo.clear()


# ---------------------------------------------------------------------------
# live-probe follow-ups (2026-10-06): BACKFILL_MAX 25, any COROS row is read, a stored 0
# (not rated) of the last RETRY_DAYS is read again
# ---------------------------------------------------------------------------

async def _seed_rows(s, rows):
    """rows: (label, days ago, coros_sport_type, sport, coros_feel, rpe, rpe_source)."""
    today = datetime.now(timezone.utc).date()
    for label, ago, st, sport, feel, rpe, src in rows:
        s.add(WorkoutFile(athlete_id=1, file_path=f"/x/{label}.fit", file_format="fit", source="coros",
                          coros_activity_id=label, coros_sport_type=st, sport=sport,
                          workout_date=today - timedelta(days=ago), coros_feel=feel, rpe=rpe, rpe_source=src))
    await s.commit()


def test_backfill_reads_at_most_25_per_sync():
    assert coros_client.BACKFILL_MAX == 25 and coros_client.BACKFILL_PASSES == 3


def test_backfill_with_more_work_than_passes_finishes_and_only_failures_count(tmp_path, monkeypatch):
    """7 unread rows, 2 per sync: 4 passes — more than BACKFILL_PASSES — and every row is read,
    because a pass that only ran out of reads is not a failing pass."""
    monkeypatch.setattr(coros_client, "BACKFILL_MAX", 2)

    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=7, n_old=0)
        fake = FakeDetail([], feels={"B1": None}, default=3)               # B1 fails once (500)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            marks = []
            for i in range(6):
                if i == 1:
                    fake.feels = {}                                       # B1 answers from now on
                await collect(coros_client.sync_workouts(s, 1))
                marks.append(await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY))
        last = marks[-1]
        assert last["done"] is True and last["failed_passes"] == 1 and last["passes"] > coros_client.BACKFILL_PASSES
        assert [m["done"] for m in marks].index(True) == last["passes"] - 1
        rows = {r.coros_activity_id: r for r in await _rows(s)}
        assert all(r.coros_feel == 3 and r.rpe == 5.0 for r in rows.values())
        reads = [q["labelId"] for _m, _p, q in fake.details()]
        assert sorted(set(reads)) == [f"B{i}" for i in range(7)] and reads.count("B1") == 2
    run(go())


def test_backfill_still_gives_up_after_three_failing_passes_with_work_left(tmp_path, monkeypatch):
    monkeypatch.setattr(coros_client, "BACKFILL_MAX", 2)

    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=6, n_old=0)
        fake = FakeDetail([], default=None)                                # 500 every time
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            for _ in range(coros_client.BACKFILL_PASSES + 2):
                await collect(coros_client.sync_workouts(s, 1))
        mark = await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY)
        assert mark["done"] is True and mark["failed_passes"] == coros_client.BACKFILL_PASSES
        assert len(fake.details()) == 2 * coros_client.BACKFILL_PASSES
    run(go())


def test_every_coros_row_is_read_whatever_its_sport(tmp_path):
    """COROS ignores a wrong sportType here (live probe): a row without a stored code is read
    with 100, whatever its sport; a stored code is sent as it is."""
    async def go():
        s = await make_session(tmp_path)
        await _seed_rows(s, [("H1", 10, None, "hiking", None, None, None),
                             ("N1", 11, None, None, None, None, None),
                             ("T1", 12, 102, "trail running", None, None, None)])
        fake = FakeDetail([], default=2)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        sent = {q["labelId"]: q["sportType"] for _m, _p, q in fake.details()}
        assert sent == {"H1": "100", "N1": "100", "T1": "102"}
        assert ev[-1]["rpe_filled"] == 3
        assert coros_client._detail_sport(WorkoutFile(sport="hiking", coros_sport_type=None)) == 100
    run(go())


def test_a_later_rating_of_a_recent_unrated_activity_arrives(tmp_path):
    """A stored 0 (not rated) of the last RETRY_DAYS is read again: a rating added later in the
    COROS app becomes the RPE and sets rpe_filled; a FIT's own RPE still wins; an older 0 is
    left alone."""
    async def go():
        s = await make_session(tmp_path)
        await _seed_rows(s, [("Z1", 1, 100, "running", 0, None, None),
                             ("Z2", 2, 100, "running", 0, 8.0, None),          # a FIT RPE 8
                             ("Z3", coros_client.RETRY_DAYS + 3, 100, "running", 0, None, None)])
        await SettingsRepository(s, 1).set(coros_client.BACKFILL_KEY, {"done": True})
        await s.commit()
        fake = FakeDetail([], feels={"Z1": 4, "Z2": 5, "Z3": 5})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert sorted(q["labelId"] for _m, _p, q in fake.details()) == ["Z1", "Z2"]   # not the older Z3
        assert ev[-1]["rpe_filled"] == 1
        rows = {r.coros_activity_id: r for r in await _rows(s)}
        assert (rows["Z1"].coros_feel, rows["Z1"].rpe, rows["Z1"].rpe_source) == (4, 7.0, "coros")
        assert (rows["Z2"].coros_feel, rows["Z2"].rpe, rows["Z2"].rpe_source) == (5, 8.0, None)
        assert (rows["Z3"].coros_feel, rows["Z3"].rpe) == (0, None)
        assert fake.writes() == []
    run(go())


def test_unrated_rereads_are_capped_by_the_retry_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(coros_client, "RETRY_MAX", 3)

    async def go():
        s = await make_session(tmp_path)
        await _seed_rows(s, [(f"U{i}", i % 3, 100, "running", 0, None, None) for i in range(6)])
        await SettingsRepository(s, 1).set(coros_client.BACKFILL_KEY, {"done": True})
        await s.commit()
        fake = FakeDetail([], default=0)                                   # still not rated
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert len(fake.details()) == 3 and ev[-1]["rpe_filled"] == 0
    run(go())
