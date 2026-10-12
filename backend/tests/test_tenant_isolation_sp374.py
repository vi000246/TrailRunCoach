"""SP-374: the locks, the warm-up and the in-memory caches are per tenant — one person's work
never makes another person wait, and a few people using the app at once keep each other's
results in the caches. Everything heavy is faked."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from backend import tenancy


def _t(tmp_path, tid: str) -> tenancy.Tenant:
    d = tmp_path / tid
    d.mkdir(parents=True, exist_ok=True)
    return tenancy.Tenant(id=tid, kind=tenancy.USER, root=d, shared=d)


# ---------------------------------------------------------------- 1. the plan writer / push locks

@pytest.mark.parametrize("which", ["_wlock", "_plock"])
def test_a_tenant_holding_the_plan_lock_does_not_block_another(tmp_path, which):
    from backend.api import plan_sessions as PSA
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    get = getattr(PSA, which)

    async def go():
        with tenancy.use(a):
            la = get()
            await la.acquire()                       # A is writing its plan / pushing to its watch
        try:
            with tenancy.use(b):
                lb = get()
                assert lb is not la and not lb.locked()
                await asyncio.wait_for(lb.acquire(), 0.5)        # B does not wait for A
                lb.release()
            with tenancy.use(a):
                assert get() is la and get().locked()          # A's own second writer still queues
        finally:
            la.release()
    asyncio.run(go())


# ---------------------------------------------------------------- the calculator inputs

def test_b_gets_its_calculator_inputs_while_a_is_still_computing(tmp_path, monkeypatch):
    from backend.api import racepower as RP
    from backend.engine.racepower import athlete as ATH
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    monkeypatch.setattr(RP, "_cache", RP.TenantSlots())
    ds = {"uA": SimpleNamespace(name="A"), "uB": SimpleNamespace(name="B")}
    monkeypatch.setattr(RP, "_dataset", lambda: ds[tenancy.current().id])
    monkeypatch.setattr(RP, "_plan_stamp", lambda: 0.0)
    started, release = threading.Event(), threading.Event()

    def derive(d, today):
        if d.name == "A":
            started.set()
            assert release.wait(10)
        return {"who": d.name}
    monkeypatch.setattr(ATH, "derive", derive)
    got = {}

    def run_a():
        with tenancy.use(a):
            got["A"] = RP.inputs()
    ta = threading.Thread(target=run_a)
    ta.start()
    try:
        assert started.wait(5)                     # A is inside derive()
        done = threading.Event()

        def run_b():
            with tenancy.use(b):
                got["B"] = RP.inputs()
            done.set()
        threading.Thread(target=run_b, daemon=True).start()
        assert done.wait(2), "B waited for A's derive()"
        assert got["B"] == {"who": "B"}
    finally:
        release.set()
        ta.join(5)
    assert got["A"] == {"who": "A"}


# ---------------------------------------------------------------- 2. plan_auto.busy per tenant

def test_a_running_auto_adjustment_is_not_busy_for_b(tmp_path, monkeypatch):
    from backend.engine import plan_auto
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    gate = asyncio.Event()

    async def slow(trigger):
        await gate.wait()
    monkeypatch.setattr(plan_auto, "run_safe", slow)

    async def go():
        with tenancy.use(a):
            t = plan_auto._after_sync("coros", {"status": "ok", "downloaded": 1, "trigger": "schedule"})
        assert t is not None
        await asyncio.sleep(0)
        with tenancy.use(a):
            assert plan_auto.busy() is True
        with tenancy.use(b):
            assert plan_auto.busy() is False
            assert await plan_auto.wait_idle(0.05) is True          # B does not wait for A's run
        with tenancy.use(a):
            assert await plan_auto.wait_idle(0.05) is False
        gate.set()
        await t
        with tenancy.use(a):
            assert plan_auto.busy() is False
    asyncio.run(go())


def test_b_low_priority_warm_up_does_not_wait_for_a_plan_run(tmp_path, monkeypatch):
    from backend.api import wko5views
    from backend.engine import plan_auto
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    monkeypatch.setattr(wko5views, "_PLAN_POLL_S", 0.01)
    class Run:                                     # A's automatic run, still going
        _trc_tenant = "uA"

        def done(self):
            return False
    monkeypatch.setattr(plan_auto, "_TASKS", {Run()})
    with tenancy.use(a):
        assert plan_auto.busy() is True
    with tenancy.use(b):
        import time
        t0 = time.monotonic()
        wko5views._wait_plan_idle(limit_s=2.0)
        assert time.monotonic() - t0 < 0.5


# ---------------------------------------------------------------- 3. the warm-up per tenant

def test_b_warm_up_is_queued_not_dropped_while_a_runs_and_runs_as_b(tmp_path, monkeypatch):
    """SP-374 第 3 條 with SP-338's queue: B's warm-up while A's runs is not dropped (it is queued:
    one whole-Dataset rebuild at a time for the server) and it runs in B's own context, its
    low-priority part too."""
    from backend import jobs
    from backend.api import activity_auto as AA
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import racepower as RP
    from backend.api import wko5views
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.setattr(wko5views, "_WARM", {})
    jobs.reset()
    hold = threading.Event()
    built, classified = [], []

    def dataset():
        tid = tenancy.current().id
        built.append(tid)
        if tid == "uA":
            assert hold.wait(10)                    # A's warm-up is busy building
        return SimpleNamespace(today=20000.0, who=tid)
    monkeypatch.setattr(wko5views, "_dataset", dataset)
    monkeypatch.setattr(wko5views, "_warm_chart_code", lambda ds: None)
    monkeypatch.setattr(OV, "_status", lambda d, today: None)
    monkeypatch.setattr(PSA, "_compute_inputs", lambda: None)
    monkeypatch.setattr(plan_auto, "busy", lambda tenant_id=None: False)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(AA, "job_for", lambda d, **kw: classified.append((tenancy.current().id, d.who)))
    monkeypatch.setattr(RP, "warm_charts", lambda job=None: None)
    try:
        with tenancy.use(a):
            ta = wko5views.warm_up("sync-A")
        assert ta is not None
        import time
        for _ in range(100):
            if built:
                break
            time.sleep(0.01)
        with tenancy.use(b):
            tb = wko5views.warm_up("sync-B")
        assert tb is not None, "B's warm-up was dropped while A's ran"
        assert tb.state == "queued" and jobs.position(tb) == 1           # waits its turn
        assert built == ["uA"]
    finally:
        hold.set()
    ta.join(5)
    tb.join(5)
    for tid in ("uA", "uB"):
        low = wko5views._warm_slot(tid).get("low")
        low and low.join(5)
    assert built[0] == "uA" and "uB" in built                        # B built after A (its low part re-reads A)
    assert ("uB", "uB") in classified and ("uA", "uA") in classified   # each in its own context


# ---------------------------------------------------------------- 4. room for each tenant

def test_four_tenants_in_turn_hit_the_plan_inputs_and_calculator_caches(tmp_path, monkeypatch):
    from backend.api import plan_sessions as PSA
    from backend.api import racepower as RP
    from backend.engine.racepower import athlete as ATH
    ts = [_t(tmp_path, f"u{i}") for i in range(4)]
    monkeypatch.setattr(PSA, "_cache", PSA.TenantLRU(per_tenant=3, tenants=4))
    monkeypatch.setattr(RP, "_cache", RP.TenantSlots(tenants=4))
    builds, derives = [], []

    def inputs_key(blackouts=None):
        key = (tenancy.current().id, 1, "2026-10-12", "", 0.0)

        def build():
            builds.append(key[0])
            out = {"who": key[0]}
            with PSA._lock:
                PSA._cache[key] = out
            return out
        return key, build
    monkeypatch.setattr(PSA, "_inputs_key", inputs_key)
    ds = {t.id: SimpleNamespace(name=t.id) for t in ts}
    monkeypatch.setattr(RP, "_dataset", lambda: ds[tenancy.current().id])
    monkeypatch.setattr(RP, "_plan_stamp", lambda: 0.0)
    monkeypatch.setattr(ATH, "derive", lambda d, today: derives.append(d.name) or {"who": d.name})
    for _round in range(2):
        for t in ts:
            with tenancy.use(t):
                assert PSA._compute_inputs() == {"who": t.id}
                assert RP.inputs() == {"who": t.id}
    assert builds == [t.id for t in ts] and derives == [t.id for t in ts]     # round 2: all hits


def test_tenant_lru_keeps_room_per_tenant_and_drops_the_least_recent_tenant():
    from backend.tenant_cache import TenantLRU
    c = TenantLRU(per_tenant=2, tenants=2)
    for k in [("a", 1), ("a", 2), ("a", 3)]:
        c[k] = k
    assert ("a", 1) not in c and ("a", 3) in c          # within a tenant: the oldest of its own goes
    c[("b", 1)] = 1
    assert c.get(("a", 2)) == ("a", 2)                   # a used again: b is now the least recent
    c[("c", 1)] = 1
    assert ("b", 1) not in c and ("a", 2) in c and ("c", 1) in c


def test_cache_tenants_from_the_environment(monkeypatch):
    from backend import tenant_cache as TC
    monkeypatch.setenv(TC.ENV_TENANTS, "5")
    assert TC.max_tenants() == 5
    monkeypatch.setenv(TC.ENV_TENANTS, "x")
    assert TC.max_tenants() == TC.DEFAULT_TENANTS
