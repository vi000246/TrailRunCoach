"""TP file listing / download against the live-verified endpoints (fake HTTP):
details → workoutDeviceFileInfos / attachmentFileInfos, rawfiledata/{fileId}
(gzip), .fit.gz names, date-range listing in chunks, changed for incremental
syncs, basic (non-premium) accounts still downloading."""
import gzip
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import SyncState, WorkoutFile
from backend.sync import http, tp_client
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import START, FakeTP, _tp_state, dev, fit_payload, make_session, run
from backend.tests.test_tp_web_login import LiveShape


# ---- pure helpers -------------------------------------------------------------

def test_pick_fit_file_prefers_device_then_attachment():
    d = {"workoutDeviceFileInfos": [{"fileId": 1, "fileName": "x.pwx"},
                                    {"fileId": 2, "fileName": "480567774272323786.FIT.GZ"}],
         "attachmentFileInfos": [{"fileId": 3, "fileName": "extra.fit"}]}
    f = tp_client.pick_fit_file(d)
    assert f["fileId"] == 2 and f["_kind"] == "device"
    only_att = {"workoutDeviceFileInfos": [], "attachmentFileInfos": [
        {"fileId": 7, "fileName": "notes.pdf"}, {"fileId": 8, "fileName": "run.fit"}]}
    f = tp_client.pick_fit_file(only_att)
    assert f["fileId"] == 8 and f["_kind"] == "attachment"
    assert tp_client.pick_fit_file({"workoutDeviceFileInfos": [
        {"fileId": 1, "fileName": "auto_merged_1.fit"}]}) is None
    assert tp_client.pick_fit_file({}) is None


def test_fit_bytes_sniffs_gzip_not_the_name():
    fit = build_run(START, seconds=30)
    assert tp_client.fit_bytes(gzip.compress(fit)) == fit
    assert tp_client.fit_bytes(fit) == fit
    assert tp_client.is_fit(fit) and not tp_client.is_fit(b"\x1f\x8bnope")


def test_date_chunks_cover_the_range_without_overlap():
    ch = tp_client.date_chunks("2026-01-01", "2026-12-31", 90)
    assert ch[0] == ("2026-01-01", "2026-03-31") and ch[-1][1] == "2026-12-31"
    assert len(ch) == 5
    for (a1, b1), (a2, _b2) in zip(ch, ch[1:]):
        assert date.fromisoformat(a2) == date.fromisoformat(b1) + timedelta(days=1)
    assert tp_client.date_chunks("2026-09-30", "2026-09-30") == [("2026-09-30", "2026-09-30")]


# ---- sync flows ---------------------------------------------------------------

def _fake(workouts=None, detail=None, files=None):
    fit = build_run(START, seconds=300, power=240)
    return FakeTP(workouts or [{"workoutId": 1, "workoutDay": "2026-09-02"}],
                  detail or {1: (200, dev(fid=11))},
                  files or {1: (200, gzip.compress(fit))})


def test_first_sync_lists_by_date_range_in_chunks_and_downloads_fit_gz(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        fake = _fake()
        with http.use_transport(httpx.MockTransport(fake)):
            ev = [e async for e in tp_client.sync_workouts(s, 1, since="2026-01-01")]
        assert not any("changed" in c for c in fake.calls)
        assert len(fake.ranges) == len(tp_client.date_chunks("2026-01-01", date.today().isoformat()))
        assert any(e.get("status") == "downloaded" and e["workout_id"] == 1 for e in ev)
        assert any("/rawfiledata/11" in c for c in fake.calls)
        assert not any("/detaildata" in c or "/filedata/" in c for c in fake.calls)
        wf = (await s.execute(select(WorkoutFile))).scalar_one()
        assert wf.file_format == "fit" and wf.tp_workout_id == 1
        assert open(wf.file_path, "rb").read()[8:12] == b".FIT"       # stored gunzipped
    run(go())


def test_incremental_sync_uses_changed(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        st = (await s.execute(select(SyncState))).scalar_one()
        st.last_sync_cursor = "2026-09-01"
        await s.commit()
        fake = _fake()
        with http.use_transport(httpx.MockTransport(fake)):
            ev = [e async for e in tp_client.sync_workouts(s, 1)]
        assert any("workouts/changed?date=2026-09-01" in c for c in fake.calls)
        assert fake.ranges == []
        assert ev[0]["mode"] == "changed"
        assert any(e.get("status") == "downloaded" for e in ev)
    run(go())


def test_attachment_only_workout_downloads(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        fake = _fake(detail={1: (200, {"workoutDeviceFileInfos": [],
                                       "attachmentFileInfos": [{"fileId": 5, "fileName": "run.fit"}]})})
        with http.use_transport(httpx.MockTransport(fake)):
            ev = [e async for e in tp_client.sync_workouts(s, 1, since="2026-09-01")]
        assert any(e.get("status") == "downloaded" for e in ev)
        assert any("/rawfiledata/5" in c for c in fake.calls)
    run(go())


def test_file_without_id_falls_back_to_filedata(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        fit = build_run(START, seconds=120)
        fake = _fake(detail={1: (200, {"workoutDeviceFileInfos": [{"fileName": "a.fit"}]})},
                     files={1: (200, fit_payload(fit))})
        with http.use_transport(httpx.MockTransport(fake)):
            ev = [e async for e in tp_client.sync_workouts(s, 1, since="2026-09-01")]
        assert any(e.get("status") == "downloaded" for e in ev)
        assert any("/filedata/a.fit" in c for c in fake.calls)
    run(go())


def test_rawfiledata_error_is_an_error(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        fake = _fake(files={1: (404, b"")})
        with http.use_transport(httpx.MockTransport(fake)):
            ev = [e async for e in tp_client.sync_workouts(s, 1, since="2026-09-01")]
        assert any(e.get("status") == "error" and "rawfiledata HTTP 404" in e["detail"] for e in ev)
        # SP-362: on the failed list (retried by id); the cursor no longer waits for it
        from backend.sync import failures as FL
        assert [r.provider_id for r in await FL.due(s, 1, "tp")] == ["1"]
    run(go())


def test_basic_account_still_downloads(tmp_path):
    """can_download / isPremium are informational: nothing is skipped."""
    async def go():
        s = await make_session(tmp_path)
        web, files = LiveShape(), _fake()

        def route(req):
            if "/workouts/" in req.url.path and req.url.host == "tpapi.trainingpeaks.com":
                return files(req)
            return web(req)

        with http.use_transport(httpx.MockTransport(route)):
            info = await tp_client.login_password("u@example.com", "pw", s, 1)
            assert info["premium"] is False and info["can_download"] is False
            ev = [e async for e in tp_client.sync_workouts(s, 1, since="2026-09-01")]
        assert any(e.get("status") == "downloaded" for e in ev)
    run(go())
