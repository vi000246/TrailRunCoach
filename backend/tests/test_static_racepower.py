"""
The static demo's race calculator (backend/demo/static_racepower.py: engine/racepower/calc.py
on an exported athlete context, run in the browser with Pyodide) against the live API on
the same synthetic demo athlete: road 10K / half / full, goal pace / power, the events'
stored GPX (trail + multi-day 百岳), the CSV. The context goes through the same JSON the
browser reads; the computations run with the browser's limits checked separately
(no FastAPI / DB / network: test_trace_needs_no_server_modules).

Synthetic only: a small demo build in tmp_path, network refused.
"""
from __future__ import annotations

import datetime as dt
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from backend.tests import demo_fixtures as F

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-root")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return root


@pytest.fixture
def demo(built, monkeypatch):
    F.demo_env(monkeypatch, built)
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        yield c, built
    wko5views._dataset_cfg.cache_clear()


def _static(monkeypatch, raw: bytes):
    """static_racepower.load() as the worker runs it; its patches undone after the test."""
    from backend.demo import static_racepower as SR
    from backend.engine import calibrate as CAL
    from backend.engine import heat_calib as HC
    from backend.engine import localtime as LT
    for mod, name in ((CAL, "entry"), (CAL, "stored_entry"), (HC, "current"), (LT, "today_local")):
        monkeypatch.setattr(mod, name, getattr(mod, name))
    monkeypatch.setattr(SR, "_CTX", None)
    return SR.load(raw.decode("utf-8"))


def _diff(a, b, path="$", out=None):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'api' if k in a else 'static'}")
            else:
                _diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: api {str(a)[:80]!r} != static {str(b)[:80]!r}")
    return out


def _csv_rows(text: str) -> list[str]:
    # the header's 計算時間 is the clock when each one ran
    return [r for r in text.lstrip("﻿").splitlines() if "計算時間" not in r and "計算於" not in r]


def test_static_calc_equals_the_api(demo, monkeypatch):
    c, _root = demo
    from backend.api import racepower as RP
    from backend.demo import static_racepower as SR
    raw = SR.export_json(RP.LIVE)
    doc = SR.decode(json.loads(raw))
    reqs = SR.sample_requests(doc)
    assert any(r[1].startswith("course/event/") for r in reqs), "the demo has no event with a GPX"
    assert any(r[2].get("type") == "baiyue" and (r[2].get("days") or 1) > 1 for r in reqs)
    h = {"X-TRC-CSRF": F.csrf(c)}
    # the live answers first (the static run below patches the calibration reads)
    live = []
    for label, sub, body in reqs:
        r = c.post(f"/api/v1/racepower/{sub}", json=body, headers=h)
        live.append((label, sub, body, r.status_code, r.text if sub == "export/csv" else r.json()))
    _static(monkeypatch, raw)
    bad = []
    for label, sub, body, status, want in live:
        got = json.loads(SR.handle("POST", f"/api/v1/racepower/{sub}", json.dumps(body)))
        assert got["status"] < 500, (label, got.get("trace"))
        if got["status"] != status:
            bad.append(f"{label}: status api {status} != static {got['status']} ({want} / {got.get('body')})")
            continue
        if sub == "export/csv":
            if _csv_rows(want) != _csv_rows(got["csv"]):
                bad.append(f"{label}: CSV differs")
            continue
        d = _diff(want, got["body"])
        if d:
            bad.append(f"{label}: " + "; ".join(d[:5]))
    assert not bad, "\n".join(bad)
    assert sum(1 for x in live if x[3] == 200) >= len(live) - 1      # the inputs really computed


def test_trace_needs_no_server_modules(demo, tmp_path):
    """The bundle's import trace (a fresh interpreter that refuses FastAPI / SQLAlchemy /
    sqlite / httpx, like Pyodide): every sample computes, only numpy / pydantic outside backend."""
    from backend.api import racepower as RP
    from backend.demo import static_racepower as SR
    p = tmp_path / "ctx.json"
    p.write_bytes(SR.export_json(RP.LIVE))
    r = subprocess.run([sys.executable, "-m", "backend.demo.static_racepower", "--trace", str(p)],
                       cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout)
    assert not out["failures"], out["failures"]
    assert set(out["third_party"]) <= set(SR.ALLOWED_THIRD_PARTY), out["third_party"]
    assert "backend.engine.racepower.calc" in out["modules"]
    assert not any(m.startswith(("backend.api", "backend.db", "backend.sync")) for m in out["modules"])


def test_codec_roundtrip():
    import numpy as np

    from backend.demo import static_racepower as SR
    from backend.engine.racepower import grade_model as GM
    g = GM.GradeRE(1.01, bins={-2: {"n": 3, "re": 0.98}, 4: {"n": 1, "re": np.float64(0.9)}}, n_samples=4)
    o = {"a": (1, 2.5, None), "b": {3: "x"}, "c": np.array([1.0, float("nan")]), "d": dt.date(2026, 1, 2),
         "e": [float("inf"), np.int64(7), np.bool_(True)], "g": g, "__k": 1}
    back = SR.decode(json.loads(json.dumps(SR.encode(o), allow_nan=False)))
    assert back["a"] == (1, 2.5, None) and back["b"] == {3: "x"} and back["d"] == dt.date(2026, 1, 2)
    assert back["c"].dtype == np.float64 and back["c"][0] == 1.0 and np.isnan(back["c"][1])
    assert back["e"][0] == float("inf") and type(back["e"][1]) is np.int64 and type(back["e"][2]) is np.bool_
    assert isinstance(back["g"], GM.GradeRE) and back["g"].bins[-2]["re"] == 0.98 and back["__k"] == 1
    assert type(back["g"].bins[4]["re"]) is np.float64
