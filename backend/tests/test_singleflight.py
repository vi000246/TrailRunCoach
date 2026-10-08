"""Single flight (backend/singleflight.py, SP-362): concurrent callers of the same key share ONE
computation of the overview status (api/overview._status) and the plan inputs
(api/plan_sessions._compute_inputs); an exception reaches every waiter and is not cached."""
import threading
import time
from types import SimpleNamespace

import pytest

from backend.singleflight import SingleFlight


def _together(n, fn):
    """Run fn() in n threads released at once; [(value | exception)] in thread order."""
    gate, out = threading.Barrier(n), [None] * n

    def one(i):
        gate.wait()
        try:
            out[i] = fn()
        except BaseException as e:          # noqa: BLE001
            out[i] = e
    ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    return out


class Boom(RuntimeError):
    pass


def test_one_computation_per_key_and_the_same_result_for_every_waiter():
    sf, calls = SingleFlight(), []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return object()
    got = _together(6, lambda: sf.do("k", slow))
    assert len(calls) == 1 and all(g is got[0] for g in got)
    assert sf.in_flight() == 0
    sf.do("k", slow)                         # finished flights are forgotten: computes again
    assert len(calls) == 2


def test_an_exception_reaches_every_waiter_and_is_not_remembered():
    sf, calls = SingleFlight(), []

    def bad():
        calls.append(1)
        time.sleep(0.2)
        raise Boom("x")
    got = _together(5, lambda: sf.do("k", bad))
    assert len(calls) == 1 and all(isinstance(g, Boom) for g in got)
    assert sf.do("k", lambda: 42) == 42      # the next call computes afresh


def test_different_keys_do_not_wait_for_each_other_and_reentry_does_not_deadlock():
    sf = SingleFlight()
    assert sf.do("a", lambda: sf.do("a", lambda: 1) + 1) == 2      # same key, same thread: inline
    order = []

    def slow(tag, s):
        def f():
            time.sleep(s)
            order.append(tag)
            return tag
        return f
    t = threading.Thread(target=lambda: sf.do("slow", slow("slow", 0.3)))
    t.start()
    time.sleep(0.05)
    assert sf.do("fast", slow("fast", 0)) == "fast"
    t.join()
    assert order == ["fast", "slow"]


def test_overview_status_is_computed_once_for_concurrent_callers(monkeypatch):
    from backend.api import overview as OV
    calls = []

    class FakeStatus:
        def __init__(self, ds, today, prefs):
            pass

        def compute(self):
            calls.append(1)
            time.sleep(0.2)
            if len(calls) == 1:
                raise Boom("first fails")
            return self
    monkeypatch.setattr(OV, "Status", FakeStatus)
    monkeypatch.setattr(OV, "_status_cache", {})
    ds = SimpleNamespace(today=20000.0)
    import datetime as dt
    day = dt.date(2026, 10, 8)
    got = _together(4, lambda: OV._status(ds, day))
    assert len(calls) == 1 and all(isinstance(g, Boom) for g in got)      # every waiter sees it
    assert OV._status_cache == {}                                           # not poisoned
    got = _together(4, lambda: OV._status(ds, day))
    assert len(calls) == 2 and all(g is got[0] and isinstance(g, FakeStatus) for g in got)
    assert OV._status(ds, day) is got[0] and len(calls) == 2                # cached as before


def test_plan_inputs_are_computed_once_for_concurrent_callers(monkeypatch):
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    calls = []

    def slow_status(ds, today):
        calls.append(1)
        time.sleep(0.2)
        raise Boom("status failed")              # stops _compute_inputs right after the flight starts
    ds = SimpleNamespace(today=20000.0)
    monkeypatch.setattr(OV, "_dataset", lambda: ds)
    monkeypatch.setattr(OV, "_status", slow_status)
    monkeypatch.setattr(PSA, "_cache", {})
    got = _together(4, PSA._compute_inputs)
    assert len(calls) == 1 and all(isinstance(g, Boom) for g in got)
    assert PSA._cache == {}
    with pytest.raises(Boom):
        PSA._compute_inputs()
    assert len(calls) == 2                       # the failure was not remembered
