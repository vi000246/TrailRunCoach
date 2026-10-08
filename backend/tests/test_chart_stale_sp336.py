"""SP-336: when a chart's cache entry is gone (new data, a new day, a deploy) and the same chart
was drawn before, GET …/charts/{c}?stale=1 answers that drawing marked `stale` at once and
computes the new one in the background; GET /render/ready says when it is there.

Never stale: without ?stale=1 (the static demo export, scripts), for a demo tenant, or when the
background computation of that key failed. Synthetic data only (FakeDataset)."""
import datetime as dt
import threading
import time

import pytest

from backend import tenancy
from backend.api import wko5views as WV
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.render_cache import RenderCache
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)
VIEW = {"source": "custom", "name": "v", "dashboards": [{"title": "d", "charts": [
    {"kind": "athlete", "id": "load", "title": "TSS", "series": [{"name": "tss", "expression": "tss"}]},
    {"kind": "athlete", "id": "dist", "title": "km", "series": [{"name": "km", "expression": "distance"}]}]}]}


def _ds(n_days, today=TODAY, extra=()):
    ws = [FakeWorkout(dt.datetime.combine(TODAY - dt.timedelta(days=k), dt.time(7)),
                      metrics={"tss": 50.0, "distance": 10.0, "duration": 3600.0}) for k in range(1, n_days)]
    ws += [FakeWorkout(dt.datetime.combine(d, dt.time(18)), metrics={"tss": 99.0, "distance": 5.0,
                                                                       "duration": 1800.0}) for d in extra]
    d = FakeDataset(ws, today)
    d.config = EngineConfig()
    d.dir = None
    for w in d.workouts:
        w.entry.file = f"fake/{w.entry.start:%Y%m%d%H%M}.fit"
    return d


@pytest.fixture
def api(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    holder = {"ds": _ds(200)}
    gate = threading.Event()
    gate.set()
    st = {"calls": 0, "fail": False}
    real = WV._render

    def render(*a, **k):
        st["calls"] += 1
        assert gate.wait(10)
        if st["fail"]:
            st["fail"] = False
            raise RuntimeError("boom")
        return real(*a, **k)
    monkeypatch.setattr(WV, "_render", render)
    monkeypatch.setattr(WV, "_dataset", lambda parity=None, source=None: holder["ds"])
    monkeypatch.setattr(WV, "_view", lambda name, parity=None: VIEW)
    monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render"))
    app = FastAPI()
    app.include_router(WV.router)
    yield TestClient(app), holder, gate, st
    gate.set()


def _get(client, c=0, stale=True, begin=TODAY - dt.timedelta(days=89), end=TODAY):
    q = f"begin={begin}&end={end}" + ("&stale=1" if stale else "")
    r = client.get(f"/api/v1/wko5/views/v/dashboards/0/charts/{c}?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def _points(j):
    return j["series"][0]["data"]["points"]


def _wait_ready(client, key, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = client.get(f"/api/v1/wko5/render/ready?keys={key}").json()
        if key in r["ready"] or key in r["failed"]:
            return r
        time.sleep(0.05)
    raise AssertionError("background render never finished")


def test_new_data_shows_the_old_chart_then_the_new_one(api):
    client, holder, gate, st = api
    first = _get(client)
    assert "stale" not in first
    holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=3)])          # a sync brought one activity
    gate.clear()                                                            # the new render is slow
    t0 = time.monotonic()
    old = _get(client)
    assert time.monotonic() - t0 < 5
    assert old["stale"]["key"] and _points(old) == _points(first)
    assert client.get(f"/api/v1/wko5/render/ready?keys={old['stale']['key']}").json()["pending"] == [old["stale"]["key"]]
    gate.set()
    assert old["stale"]["key"] in _wait_ready(client, old["stale"]["key"])["ready"]
    new = _get(client)
    assert "stale" not in new and len(_points(new)) == len(_points(first)) + 1


def test_the_same_chart_is_recomputed_once(api):
    client, holder, gate, st = api
    _get(client)
    holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=3)])
    gate.clear()
    n = st["calls"]
    keys = {_get(client)["stale"]["key"] for _ in range(4)}
    assert len(keys) == 1
    gate.set()
    _wait_ready(client, keys.pop())
    assert st["calls"] == n + 1


def test_without_stale_the_request_waits_for_the_new_chart(api):
    client, holder, gate, st = api
    first = _get(client, stale=False)
    holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=3)])
    new = _get(client, stale=False)                  # the static demo export, scripts
    assert "stale" not in new and len(_points(new)) == len(_points(first)) + 1


def test_a_chart_drawn_never_before_is_computed(api):
    client, holder, gate, st = api
    _get(client, c=0)
    holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=3)])
    assert "stale" not in _get(client, c=1)          # another chart's drawing is never shown for it


def test_a_new_day_shows_yesterdays_chart_of_the_same_range_length(api):
    client, holder, gate, st = api
    first = _get(client)
    tomorrow = TODAY + dt.timedelta(days=1)
    holder["ds"] = _ds(200, today=tomorrow)
    gate.clear()
    old = _get(client, begin=tomorrow - dt.timedelta(days=89), end=tomorrow)
    assert old["stale"] and _points(old) == _points(first)
    gate.set()
    _wait_ready(client, old["stale"]["key"])
    # a range that does not end today keeps its own drawing (the day is not in its key)
    past = _get(client, begin=TODAY - dt.timedelta(days=120), end=TODAY - dt.timedelta(days=60))
    holder["ds"] = _ds(200, today=tomorrow + dt.timedelta(days=1))
    n = st["calls"]
    again = _get(client, begin=TODAY - dt.timedelta(days=120), end=TODAY - dt.timedelta(days=60))
    assert again == past and st["calls"] == n


def test_unchanged_data_is_a_plain_hit(api):
    client, holder, gate, st = api
    a = _get(client)
    n = st["calls"]
    holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=150)])        # outside the 90 days
    assert _get(client) == a and st["calls"] == n


def test_a_demo_tenant_is_never_served_stale(api, monkeypatch, tmp_path):
    client, holder, gate, st = api
    demo = tenancy.Tenant(id="demo-x", kind=tenancy.DEMO_SANDBOX, root=tmp_path, shared=tmp_path,
                          caps=tenancy.DEMO_CAPS)
    monkeypatch.setattr(tenancy, "current", lambda: demo)
    first = _get(client)
    holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=3)])
    new = _get(client)
    assert "stale" not in new and len(_points(new)) == len(_points(first)) + 1


def test_a_failed_background_render_is_retried_in_the_request(api):
    client, holder, gate, st = api
    _get(client)
    holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=3)])
    st["fail"] = True
    old = _get(client)
    key = old["stale"]["key"]
    assert key in _wait_ready(client, key)["failed"]
    new = _get(client)                                 # computed here, not served stale again
    assert "stale" not in new and new["series"]


def test_viewer_defaults_to_90_days_remembers_the_preset_and_swaps_stale_cards():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "wko5_viewer.html").read_text(encoding="utf-8")
    assert 'DEFAULT_RANGE = "90"' in html and 'RANGE_PRESETS = ["7", "42", "90", "365", "ytd", "all"]' in html
    assert "range: S.range" in html                                   # saved with the other viewer choices
    assert "365 * 864e5" not in html                                  # the old one-year default is gone
    assert '"&stale=1"' in html and "/render/ready?keys=" in html and "markStale(card, res.stale" in html
    assert "TRC_STATIC_CFG" in html                                   # the static demo never asks for stale
    for loc in ("zh-TW", "en"):
        cat = json.loads((root / "i18n" / loc / "viewer.json").read_text(encoding="utf-8"))
        assert all(cat.get(k) for k in ("updating", "updating_tip", "progress", "progress_old")), loc
        assert "{done}" in cat["progress"] and "{total}" in cat["progress"]


def test_stale_is_not_part_of_the_cache_key(api):
    client, holder, gate, st = api
    _get(client, stale=False)
    n = st["calls"]
    _get(client, stale=True)
    assert st["calls"] == n
