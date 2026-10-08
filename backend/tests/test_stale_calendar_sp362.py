"""
SP-362 B4: stale-while-revalidate for the 課表 page.

After a sync imported files (a new dataset generation) or on a new day, the first calendar
request used to recompute the plan inputs and the status (3–4 s + 9–13 s on the NAS) while the
page waited. Now GET /calendar answers at once with the previous computed plan view of this
tenant, marked `stale` (reason sync / day, its age), and the fresh computation runs in the
background through the existing single flight (never twice); GET /fresh says when it is done
and the page reloads. Owner rule: the stale view is for display only — no reconcile, no done /
missed matching, no 每週課表存檔, no write of any kind; the edit endpoints compute fresh inputs as
before (they wait for the flight). A change of the user's own settings is not served stale.

Hand-built inputs (test_plan_store.Env) behind a scripted key; no dataset, no WKO5 data.
"""
import copy
import dataclasses
import threading
import time
from collections import OrderedDict
from datetime import date
from types import SimpleNamespace

import pytest

from backend import tenancy
from backend.api import overview as OV
from backend.api import plan_sessions as PSA
from backend.engine import plan_history as PH
from backend.engine import plan_store as PS
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import run
from backend.tests.test_plan_store import API, Env

REAL_COMPUTE = PSA._compute_inputs
CAL = f"{API}/calendar?start=2026-09-28&end=2026-10-04"


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    for name in ("_cache", "_refreshing", "_failed"):
        monkeypatch.setattr(PSA, name, {})
    monkeypatch.setattr(PSA, "_last", OrderedDict())
    yield
    for _ in range(200):                 # let a background refresh this test started end
        if not PSA._refreshing:
            break
        time.sleep(0.01)


class Keys:
    """Stands in for _inputs_key: the key layout (tenant, generation, day, data stamp, *settings)
    and a computation that counts its runs and can be held at a gate."""

    def __init__(self, make=None):
        self.gen, self.day, self.settings = 1, date(2026, 9, 30), ("prefs-1",)
        self.builds: list = []
        self.gate = threading.Event()
        self.gate.set()
        self.fail = False
        self.make = make or (lambda key: {"gen": key[1], "today": key[2].isoformat()})

    def __call__(self, blackouts=None):
        key = (tenancy.current().id, self.gen, self.day, (), *self.settings)

        def build():
            self.builds.append(key)
            assert self.gate.wait(10)
            if self.fail:
                raise RuntimeError("status failed")
            out = PSA._cache[key] = self.make(key)            # stored under its key, as _build_inputs does
            return out
        return key, build


def _wait(cond, s=5.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _view():
    tok = PSA._STALE_OK.set(True)        # what GET /calendar sets for its own request
    try:
        return PSA._compute_inputs()
    finally:
        PSA._STALE_OK.reset(tok)


def test_generation_bump_answers_the_previous_view_and_recomputes_once(monkeypatch):
    k = Keys()
    monkeypatch.setattr(PSA, "_inputs_key", k)
    assert PSA._compute_inputs()["gen"] == 1 and len(k.builds) == 1
    k.gen, k.gate = 2, threading.Event()                      # a sync imported files; the status is slow
    t0 = time.perf_counter()
    v = _view()
    assert time.perf_counter() - t0 < 0.5                     # no wait for the computation
    assert v["gen"] == 1 and v["stale"]["reason"] == "sync" and v["stale"]["at"] > 0
    assert _wait(lambda: len(k.builds) == 2)                  # the fresh one started in the background
    v2 = _view()                                              # asked again meanwhile
    time.sleep(0.1)
    assert v2["gen"] == 1 and v2["stale"] and len(k.builds) == 2      # ... no second computation
    assert list(PSA._refreshing) == [tenancy.current().id]
    k.gate.set()
    assert _wait(lambda: not PSA._refreshing)
    v3 = _view()
    assert v3["gen"] == 2 and "stale" not in v3 and len(k.builds) == 2


def test_a_new_day_is_served_stale_too_but_a_settings_change_is_not(monkeypatch):
    k = Keys()
    monkeypatch.setattr(PSA, "_inputs_key", k)
    PSA._compute_inputs()
    k.day, k.gate = date(2026, 10, 1), threading.Event()
    assert _view()["stale"]["reason"] == "day"
    k.gate.set()
    assert _wait(lambda: not PSA._refreshing)
    # the user's own change (課表偏好, 不排課日期, …): they expect its effect, computed in the request
    k.settings = ("prefs-2",)
    v = _view()
    assert "stale" not in v and len(k.builds) == 3


def test_a_writer_never_gets_the_stale_view(monkeypatch):
    # reconcile / done-missed / edits call _compute_inputs without the calendar's flag: they wait
    # for the fresh computation (joining the background one: still computed once)
    k = Keys()
    monkeypatch.setattr(PSA, "_inputs_key", k)
    PSA._compute_inputs()
    k.gen, k.gate = 2, threading.Event()
    assert _view()["stale"]
    assert _wait(lambda: len(k.builds) == 2)
    got = []
    t = threading.Thread(target=lambda: got.append(PSA._compute_inputs()))
    t.start()
    time.sleep(0.15)
    assert not got                                            # waiting, not served stale
    k.gate.set()
    t.join(5)
    assert got[0]["gen"] == 2 and "stale" not in got[0] and len(k.builds) == 2


def test_a_failed_background_computation_is_not_retried_forever(monkeypatch):
    k = Keys()
    monkeypatch.setattr(PSA, "_inputs_key", k)
    PSA._compute_inputs()
    k.gen, k.fail = 2, True
    assert _view()["stale"]
    assert _wait(lambda: not PSA._refreshing)
    # the page reloads when GET /fresh says done: this time the request computes (and reports) it
    with pytest.raises(RuntimeError):
        _view()


def test_the_status_of_a_stale_view_is_the_previous_one(monkeypatch):
    me = tenancy.current().id
    prev = object()
    monkeypatch.setattr(OV, "_status_cache", {(me, 1, date(2026, 9, 30)): prev})
    monkeypatch.setattr(OV, "_status", lambda ds, today: pytest.fail("computed the status"))
    tok = PSA._STALE_OK.set(True)
    try:
        assert PSA._status_for(None, date(2026, 10, 1)) is prev
    finally:
        PSA._STALE_OK.reset(tok)
    monkeypatch.setattr(OV, "_status", lambda ds, today: "fresh")
    assert PSA._status_for(None, date(2026, 10, 1)) == "fresh"            # outside a stale view


def test_calendar_serves_the_stale_view_without_writing_then_the_fresh_one(monkeypatch):
    monkeypatch.setattr(PSA, "_range_extras", lambda a, b: {"activities": [], "phases": []})
    with Env(monkeypatch) as e:
        k = Keys(make=lambda key: copy.deepcopy(e.inp))
        monkeypatch.setattr(PSA, "_compute_inputs", REAL_COMPUTE)
        monkeypatch.setattr(PSA, "_inputs_key", k)
        r = e.c.get(CAL)
        assert r.status_code == 200 and not r.json().get("stale")
        before = run(PS.load(e.db))
        writes = []
        for mod, name in ((PS, "save"), (PS, "plan_reconcile"), (PS, "match_only"), (PH, "record_safe")):
            real = getattr(mod, name)
            monkeypatch.setattr(mod, name, lambda *a, _n=name, _r=real, **kw: writes.append(_n) or _r(*a, **kw))
        k.gen, k.gate = 2, threading.Event()
        t0 = time.perf_counter()
        r = e.c.get(CAL)
        assert r.status_code == 200 and time.perf_counter() - t0 < 3
        body = r.json()
        assert body["stale"]["reason"] == "sync" and body["stale"]["age_s"] >= 0 and body["stale"]["since"]
        assert {s["uid"] for s in body["sessions"]} == {s["uid"] for s in before if s.get("day") and
                                                        "2026-09-28" <= s["day"] <= "2026-10-04"}
        assert writes == [] and run(PS.load(e.db)) == before                # display only
        assert e.c.get(f"{API}/fresh").json() == {"updating": True}
        k.gate.set()
        assert _wait(lambda: not e.c.get(f"{API}/fresh").json()["updating"])
        r = e.c.get(CAL)
        assert r.status_code == 200 and not r.json().get("stale") and len(k.builds) == 2
        assert "plan_reconcile" in writes or "match_only" in writes or "record_safe" in writes   # fresh: as before


def _as(tid, kind=tenancy.USER):
    return tenancy.use(dataclasses.replace(tenancy.current(), id=tid, kind=kind))


def test_two_tenants_never_see_each_others_view(monkeypatch):
    k = Keys(make=lambda key: {"who": key[0], "gen": key[1]})
    monkeypatch.setattr(PSA, "_inputs_key", k)
    with _as("u1"):
        PSA._compute_inputs()
    with _as("u2"):
        PSA._compute_inputs()
    k.gen, k.gate = 2, threading.Event()
    with _as("u2"):
        v = _view()
    assert v["who"] == "u2" and v["stale"]
    with _as("u1"):
        assert _view()["who"] == "u1"
    k.gate.set()


def test_the_previous_views_are_capped_and_demo_tenants_never_served_stale(monkeypatch):
    k = Keys(make=lambda key: {"who": key[0], "gen": key[1]})
    monkeypatch.setattr(PSA, "_inputs_key", k)
    monkeypatch.setattr(PSA, "_LAST_MAX", 3)
    for i in range(5):
        with _as(f"u{i}"):
            PSA._compute_inputs()
    assert list(PSA._last) == ["u2", "u3", "u4"]                     # the oldest went first
    with _as("u2"):
        PSA._compute_inputs()                                         # used again: newest
    assert list(PSA._last) == ["u3", "u4", "u2"]
    # one tenant per demo visitor: never kept, never served stale (computed in the request)
    with _as("demo-abc", tenancy.DEMO_SANDBOX):
        PSA._compute_inputs()
        assert "demo-abc" not in PSA._last
        k.gen = 2
        v = _view()
    assert "stale" not in v and v["gen"] == 2 and not PSA._refreshing
    assert PSA._REFRESH_POOL._max_workers == 2                          # background refreshes: 2 at a time


def test_the_real_key_keeps_settings_apart_from_data(monkeypatch):
    """L4: the stale view compares key[_K_SETTINGS:] — a settings change must land there, a new
    dataset generation / day only before it (a reordered key would serve settings stale)."""
    from backend.engine import plan_prefs as PP
    ds1, ds2 = SimpleNamespace(today=20000.0), SimpleNamespace(today=20000.0)
    prefs = {"v": "p1"}
    monkeypatch.setattr(OV, "_dataset", lambda: cur["ds"])
    monkeypatch.setattr(PP, "load", lambda: SimpleNamespace(stamp=lambda: prefs["v"]))
    cur = {"ds": ds1}
    k1, _b = PSA._inputs_key()
    cur["ds"] = ds2                                                     # a sync: new generation
    k2, _b = PSA._inputs_key()
    assert k1[PSA._K_SETTINGS:] == k2[PSA._K_SETTINGS:] and k1[:PSA._K_SETTINGS] != k2[:PSA._K_SETTINGS]
    assert k1[PSA._K_GEN] != k2[PSA._K_GEN]
    cur["ds"] = SimpleNamespace(today=20001.0)                          # a new day
    k3, _b = PSA._inputs_key()
    assert k3[PSA._K_DAY] > k2[PSA._K_DAY] and k3[PSA._K_SETTINGS:] == k2[PSA._K_SETTINGS:]
    cur["ds"] = ds2
    prefs["v"] = "p2"                                                   # 課表偏好 saved
    k4, _b = PSA._inputs_key()
    assert k4[PSA._K_SETTINGS:] != k2[PSA._K_SETTINGS:] and k4[:PSA._K_SETTINGS] == k2[:PSA._K_SETTINGS]
    prefs["v"] = "p1"
    from backend.engine import blackouts as BL
    k5, _b = PSA._inputs_key(blackouts=BL.normalize([{"start": "2026-10-02", "end": "2026-10-02", "label": "x"}]))
    assert k5[PSA._K_SETTINGS:] != k2[PSA._K_SETTINGS:] and k5[:PSA._K_SETTINGS] == k2[:PSA._K_SETTINGS]
