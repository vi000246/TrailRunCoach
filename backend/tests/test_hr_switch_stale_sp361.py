"""
SP-361 (Q1 = B, owner 2026-10-13): after switching 設定 → 課表心率區間 the 課表 shows the previous
plan at once, marked 「心率區間更新中」, and the plan with the new model's HR targets is computed in
the background — the stale view of SP-362 B4, now also for a change of the HR model alone.

Before: the plan inputs' key holds HRP.stamp() among the user's own settings, so a model switch
missed the cache and GET /calendar recomputed the whole Status + week plan in the request
(2.8–2.9 s on the demo data, ≈ 24 s on the NAS). Switching back to a model used before hit the
inputs' cache but recomputed the Status for the calendar's phases (_range_extras, 1.9 s), though
the phases and the goals it reads do not depend on the HR model.

Rules kept: any other setting changed with it (課表偏好, 不排課日期 …) is computed in the request as
before; writers never get a stale view (test_stale_calendar_sp362); demo tenants are never served stale.
"""
import threading
import time
from datetime import date
from types import SimpleNamespace

import pytest

from backend import tenancy
from backend.api import overview as OV
from backend.api import plan_sessions as PSA
# the SP-362 B4 scaffolding: a scripted key / computation, the module state reset per test
from backend.tests.test_stale_calendar_sp362 import Keys, _fresh_state, _view, _wait  # noqa: F401


def _keys():
    """Keys whose layout has the HR stamp where _inputs_key puts it (_K_HR)."""
    k = Keys()
    n = PSA._K_HR - PSA._K_SETTINGS
    k.settings = tuple(f"s{i}" for i in range(n)) + ("hr:lthr",) + ("tech", True, "z3", "exp")
    return k


def _with_hr(k, model):
    s = list(k.settings)
    s[PSA._K_HR - PSA._K_SETTINGS] = f"hr:{model}"
    k.settings = tuple(s)


def test_a_model_switch_answers_the_previous_view_marked_hr_and_recomputes_once(monkeypatch):
    k = _keys()
    monkeypatch.setattr(PSA, "_inputs_key", k)
    assert "stale" not in PSA._compute_inputs() and len(k.builds) == 1
    _with_hr(k, "hrr")
    k.gate = threading.Event()                                  # the new model's plan is slow
    t0 = time.perf_counter()
    v = _view()
    assert time.perf_counter() - t0 < 0.5                       # no wait for the computation
    assert v["stale"]["reason"] == "hr" and v["stale"]["at"] > 0
    assert _wait(lambda: len(k.builds) == 2)                    # computed in the background
    assert _view()["stale"]["reason"] == "hr" and len(k.builds) == 2      # asked again: not twice
    k.gate.set()
    assert _wait(lambda: not PSA._refreshing)
    v2 = _view()
    assert "stale" not in v2 and len(k.builds) == 2 and v2 is PSA._cache.get(k()[0])


def test_the_model_with_another_setting_is_computed_in_the_request(monkeypatch):
    k = _keys()
    monkeypatch.setattr(PSA, "_inputs_key", k)
    PSA._compute_inputs()
    _with_hr(k, "hrmax")
    k.settings = ("prefs-2",) + k.settings[1:]                  # 課表偏好 saved too
    v = _view()
    assert "stale" not in v and len(k.builds) == 2


def test_a_writer_waits_for_the_new_models_plan(monkeypatch):
    k = _keys()
    monkeypatch.setattr(PSA, "_inputs_key", k)
    PSA._compute_inputs()
    _with_hr(k, "hrr")
    k.gate = threading.Event()
    assert _view()["stale"]
    got = []
    t = threading.Thread(target=lambda: got.append(PSA._compute_inputs()))   # an edit / reconcile
    t.start()
    time.sleep(0.15)
    assert not got
    k.gate.set()
    t.join(5)
    assert "stale" not in got[0] and len(k.builds) == 2


def test_the_hr_stamp_sits_at_K_HR_of_the_real_key(monkeypatch):
    """The stale rule reads key[_K_HR]: HRP.stamp() must land exactly there (a reordered key would
    serve some other setting stale, or never the model)."""
    from backend.engine import hr_profile as HRP
    from backend.engine import plan_prefs as PP
    ds = SimpleNamespace(today=20000.0)
    monkeypatch.setattr(OV, "_dataset", lambda: ds)
    monkeypatch.setattr(PP, "load", lambda: SimpleNamespace(stamp=lambda: "p1"))
    model = {"v": "lthr"}
    monkeypatch.setattr(HRP, "stamp", lambda *a: f"stamp-{model['v']}")
    k1, _b = PSA._inputs_key()
    model["v"] = "hrr"
    k2, _b = PSA._inputs_key()
    assert k1[PSA._K_HR] == "stamp-lthr" and k2[PSA._K_HR] == "stamp-hrr"
    assert k1[:PSA._K_HR] == k2[:PSA._K_HR] and k1[PSA._K_HR + 1:] == k2[PSA._K_HR + 1:]


def test_the_calendars_phases_take_the_status_of_another_model(monkeypatch):
    """Switching back to a model used before: the inputs are cached, the Status (one per tenant)
    is the other model's — its plan / goals (all _range_extras reads) are the same: no recompute."""
    me = tenancy.current().id
    day = date(2026, 9, 30)
    head = (me, 1, day, 100.0, "prefs", ())
    other = object()
    monkeypatch.setattr(OV, "_status_cache", {(*head, "hr:hrr", None, "z3", "exp"): other})
    monkeypatch.setattr(OV, "_status_key", lambda ds, today: ((*head, "hr:lthr", None, "z3", "exp"), None))
    monkeypatch.setattr(OV, "_status", lambda ds, today: pytest.fail("computed the status"))
    assert PSA._status_for(None, day) is other
    # anything else different (課表偏好 here): the Status is computed as before
    monkeypatch.setattr(OV, "_status_key", lambda ds, today: ((me, 1, day, 100.0, "prefs-2", (), "hr:lthr",
                                                                None, "z3", "exp"), None))
    monkeypatch.setattr(OV, "_status", lambda ds, today: "fresh")
    assert PSA._status_for(None, day) == "fresh"


def test_the_hr_stamp_sits_at_SK_HR_of_the_real_status_key(monkeypatch):
    from backend.engine import hr_profile as HRP
    from backend.engine import plan_prefs as PP
    from backend.engine import plan_store as PS
    monkeypatch.setattr(PP, "load", lambda: SimpleNamespace(stamp=lambda: "p1"))
    monkeypatch.setattr(PS, "test_sessions", lambda: [])
    model = {"v": "lthr"}
    monkeypatch.setattr(HRP, "stamp", lambda *a: f"stamp-{model['v']}")
    ds = SimpleNamespace(today=20000.0)
    k1, _p = OV._status_key(ds, date(2026, 9, 30))
    model["v"] = "hrr"
    k2, _p = OV._status_key(ds, date(2026, 9, 30))
    assert k1[OV._SK_HR] == "stamp-lthr" and k2[OV._SK_HR] == "stamp-hrr"
    assert k1[:OV._SK_HR] == k2[:OV._SK_HR] and k1[OV._SK_HR + 1:] == k2[OV._SK_HR + 1:]
