"""
SP-366: the charts at the bottom of the race calculator (坡度 RE 曲線 + 跑走切換速度,
爬坡步頻分布) on a road race (a marathon) showed 「Failed to fetch」.

GET /grade-model and GET /cadence-check are not about the race: they fit the athlete's
whole year. On a cold cache that is the slowest thing the page asks for (77 s on the
synthetic demo athlete right after a start), and nothing stopped a second caller — /plan,
the other chart, a second tab — from computing the same thing from scratch at the same
time. A request that long is dropped before it answers (the page then shows the browser's
「Failed to fetch」). Fixed by:
  * one computation per key (backend/singleflight.py) for both, so concurrent callers wait
    for the first one instead of multiplying the work;
  * the page: a road race does not load them (they are about climbing: a short note
    instead), and a dropped connection reads as a short message with 重試, not the raw error.

Synthetic only: a fake Dataset of flat road runs, fake inputs (no WKO5 folder, no DB).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from backend.engine.racepower import athlete as A
from backend.engine.racepower import backtest as BT
from backend.engine.racepower import grade_model as GM
from backend.tests.test_racepower_export import client  # noqa: F401  (fixture)
from backend.tests.test_racepower_runwalk import _ds, track
from backend.tests.test_racepower_v2 import RE0

from backend.api.racepower import _grade_models as REAL_GRADE_MODELS  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "racepower.html"


def flat_road_arrays():
    """Two flat road runs' samples: 10 km at 1 %, 172 spm (no climbing to speak of)."""
    return track([(10000, 0.0, 86, 3.3)])


@pytest.fixture()
def flat_road_athlete(monkeypatch):
    from backend.api import racepower as RP
    from backend.engine.wko5expr import dataset as D
    monkeypatch.setattr(D, "date_to_day", lambda d: 10)
    ds = _ds(flat_road_arrays())
    monkeypatch.setattr(RP, "_dataset", lambda: ds)
    # a road runner's grade model: no climbing windows, the curve is the prior only
    monkeypatch.setattr(A, "grade_models", lambda *a, **k: {"grade_re": GM.GradeRE(RE0),
                                                             "hike_speed": GM.fit_hike_speed([])})
    monkeypatch.setattr(A, "solo_hikes", lambda path=None: set())
    monkeypatch.setattr(BT, "class_model_flag", lambda path=None: False)
    monkeypatch.setattr(BT, "surface_split_flag", lambda path=None: False)
    monkeypatch.setattr(BT, "load", lambda path=None: None)
    for k in ("grade", "climb_cadence"):
        RP._cache.pop(k, None)
    yield RP
    for k in ("grade", "climb_cadence"):
        RP._cache.pop(k, None)


def test_flat_road_athlete_gets_both_answers(client, flat_road_athlete, monkeypatch):  # noqa: F811
    """The API side on a road runner: both answer 200 with something the page can draw."""
    monkeypatch.setattr(flat_road_athlete, "_grade_models", REAL_GRADE_MODELS)   # client's fake → the real one
    g = client.get("/api/v1/racepower/grade-model")
    assert g.status_code == 200, g.text
    gre = g.json()["grade_re"]
    assert gre["bins"] and all(b["re"] is not None for b in gre["bins"])
    c = client.get("/api/v1/racepower/cadence-check")
    assert c.status_code == 200, c.text
    cj = c.json()
    assert cj["verdict"] == "few" and cj["n_runs"] == 0 and len(cj["bins"]) == len(cj["seconds"])


def _together(fn, n=3):
    """Call fn() from n threads at once; their results."""
    out, errs = [None] * n, []
    go = threading.Barrier(n)

    def run(i):
        try:
            go.wait()
            out[i] = fn()
        except Exception as e:          # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs, errs
    return out


def test_concurrent_grade_model_callers_share_one_fit(flat_road_athlete, monkeypatch):
    """/plan, /grade-model and a second tab on a cold cache: one fit, not one each."""
    RP = flat_road_athlete
    calls = []

    def slow_fit(*a, **k):
        calls.append(1)
        time.sleep(0.4)
        return {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([])}
    monkeypatch.setattr(A, "grade_models", slow_fit)
    from backend.tests.test_racepower_v2 import fake_inputs
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    got = _together(RP._grade_models)
    assert len(calls) == 1, f"{len(calls)} fits for 3 concurrent callers"
    assert got[0] is got[1] is got[2]


def test_concurrent_cadence_checks_share_one_scan(client, flat_road_athlete, monkeypatch):  # noqa: F811
    calls = []

    def slow_scan(ds, today=None):
        calls.append(1)
        time.sleep(0.4)
        return list(np.zeros(50)), 0
    monkeypatch.setattr(A, "climb_cadence_seconds", slow_scan)
    got = _together(lambda: client.get("/api/v1/racepower/cadence-check").status_code)
    assert got == [200, 200, 200]
    assert len(calls) == 1, f"{len(calls)} scans for 3 concurrent requests"


# ---- the page --------------------------------------------------------------------------

def _fn(src: str, name: str) -> str:
    m = re.search(r"async function " + name + r"\(\) \{\n(.*?)\n\}\n", src, re.S)
    assert m, name
    return m.group(1)


def test_page_road_race_shows_a_note_instead_of_loading():
    src = PAGE.read_text("utf-8")
    for name, url in (("renderGM", "/grade-model"), ("renderCad", "/cadence-check")):
        body = _fn(src, name)
        # the road guard comes before the request: a road race never asks for it
        guard = body.find("if (chartRoad(")
        assert 0 <= guard < body.find(url), name
    assert re.search(r'function chartRoad\(sec\) \{ const road = type === "road";', src)
    for sec in ("gm", "cad"):
        assert f'id="{sec}-road"' in src and f'id="{sec}-body"' in src
    # switching road ↔ trail updates them (an open one loads)
    st = re.search(r"\nfunction setType\(t\) \{\n(.*?)\n\}\n", src, re.S)
    assert st and 'chartRoad("gm")' in st.group(1) and 'chartRoad("cad")' in st.group(1)


def test_page_a_dropped_connection_reads_as_a_short_message_with_retry():
    src = PAGE.read_text("utf-8")
    for name in ("renderGM", "renderCad"):
        body = _fn(src, name)
        assert "chartErr(" in body, name                    # not the raw e.message
    assert re.search(r"function chartErr\(", src)
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        for k in ("charts.road", "charts.neterr", "charts.retry"):
            assert cat.get(k), (loc, k)
