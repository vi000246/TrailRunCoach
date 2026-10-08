"""The order of the work after a sync and at start-up (SP-362 B2): the Dataset → the overview
status → the plan inputs first (what a page load waits for), then the automatic plan run, then
the calibration / automatic classification at the lowest priority. Everything heavy is faked."""
import asyncio
from types import SimpleNamespace

from backend.tests.test_sync_e2e import make_session, run


def test_runner_starts_the_warm_up_before_the_plan_and_the_calibration(tmp_path, monkeypatch):
    from backend.api import wko5views
    from backend.engine import calibrate, localtime, plan_auto
    from backend.sync import runner
    order = []

    async def tz(db, athlete_id):
        order.append("timezone")
    monkeypatch.setattr(localtime, "refresh_from_fits", tz)
    monkeypatch.setattr(wko5views, "warm_up", lambda reason: order.append(("warm", reason)))
    monkeypatch.setattr(plan_auto, "after_sync", lambda src, res: order.append("plan"))
    monkeypatch.setattr(calibrate, "after_sync", lambda src, res, aid: order.append("calib"))

    async def fake_stream(db, source, athlete_id, since):
        yield {"status": "complete", "total_downloaded": 2, "total_checked": 2, "errors": []}
    monkeypatch.setattr(runner, "_client_stream", fake_stream)

    async def go():
        s = await make_session(tmp_path)
        _ = [e async for e in runner.stream(s, "coros", 1)]
    run(go())
    assert order == ["timezone", ("warm", "sync-coros"), "plan", "calib"]


def test_the_calibration_after_a_sync_waits_for_the_plan_run(monkeypatch):
    from backend.engine import calibrate, plan_auto
    order = []

    async def plan_run(trigger):
        await asyncio.sleep(0.2)
        order.append("plan")

    async def calib_run(athlete_id=1):
        order.append("calib")
    monkeypatch.setattr(plan_auto, "run_safe", plan_run)
    monkeypatch.setattr(calibrate, "run_safe", calib_run)

    async def go():
        t1 = plan_auto._after_sync("coros", {"status": "ok", "downloaded": 1})
        t2 = calibrate._after_sync("coros", {"status": "ok", "downloaded": 1})
        await asyncio.gather(t1, t2)
    asyncio.run(go())
    assert order == ["plan", "calib"]


def test_wait_idle_returns_at_once_without_a_plan_run():
    from backend.engine import plan_auto

    async def go():
        return await plan_auto.wait_idle(0.01)
    assert asyncio.run(go()) is True and plan_auto.busy() is False


def test_warm_up_order_dataset_status_inputs_then_low_priority(monkeypatch):
    from backend.api import activity_auto as AA
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import wko5views
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    order = []
    ds = SimpleNamespace(today=20000.0)
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.setattr(wko5views, "_dataset", lambda: order.append("dataset") or ds)
    monkeypatch.setattr(OV, "_status", lambda d, today: order.append("status"))
    monkeypatch.setattr(PSA, "_compute_inputs", lambda: order.append("inputs"))
    polls = iter([True, False])               # a plan run is going at the first look

    def busy():
        b = next(polls, False)
        order.append("plan busy" if b else "plan idle")
        return b
    monkeypatch.setattr(plan_auto, "busy", busy)
    monkeypatch.setattr(CAL, "_registry", lambda: {})          # nothing never-fitted
    monkeypatch.setattr(AA, "job_for", lambda d: order.append("classify"))
    from backend.api import racepower as RP
    monkeypatch.setattr(RP, "warm_charts", lambda job=None: order.append("charts"))      # SP-366, last
    t = wko5views.warm_up("test")
    t.join(10)
    wko5views._WARM["low"] and wko5views._WARM["low"].join(10)
    # the low-priority part re-reads the Dataset after its wait (review SP-362 #4)
    assert order == ["dataset", "status", "inputs", "plan busy", "plan idle", "dataset", "classify", "charts"]


def test_a_second_warm_up_runs_while_the_first_waits_for_the_plan(monkeypatch):
    """Review SP-362 #4: the wait for the plan run happens in its own thread, so a warm-up
    asked for meanwhile (a second sync) still warms the dataset / status / inputs; the
    classification runs once the plan is idle, on the Dataset read after the wait."""
    import threading
    from backend.api import activity_auto as AA
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import wko5views
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    order, made, classified = [], [], []
    release = threading.Event()

    def dataset():
        ds = SimpleNamespace(today=20000.0, n=len(made))
        made.append(ds)
        order.append("dataset")
        return ds
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.setattr(wko5views, "_dataset", dataset)
    monkeypatch.setattr(OV, "_status", lambda d, today: order.append("status"))
    monkeypatch.setattr(PSA, "_compute_inputs", lambda: order.append("inputs"))
    monkeypatch.setattr(plan_auto, "busy", lambda: not release.is_set())
    monkeypatch.setattr(wko5views, "_PLAN_POLL_S", 0.01)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(AA, "job_for", lambda d: classified.append(d.n))
    from backend.api import racepower as RP
    monkeypatch.setattr(RP, "warm_charts", lambda job=None: None)
    t1 = wko5views.warm_up("first")
    t1.join(10)
    assert not t1.is_alive()                         # the warm part is done; the wait is elsewhere
    t2 = wko5views.warm_up("second")
    assert t2 is not None
    t2.join(10)
    assert order == ["dataset", "status", "inputs"] * 2 and classified == []
    release.set()
    low = wko5views._WARM["low"]
    low and low.join(10)
    assert classified and classified[-1] == made[-1].n and made[-1].n >= 2    # read after the wait
