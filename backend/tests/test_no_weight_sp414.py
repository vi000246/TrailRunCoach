"""SP-414: a runner who never entered a body weight (plan.json `weights` empty, no weight in the
DB's athlete settings). GET /racepower/inputs answered 500: derive() put the season plan's fresh
CP test beside the W′ prior, and the prior's J/kg band multiplied None.

The W′ prior itself is Ruiz-Alias 2025's amateur mean in kJ — no body weight in it; only the
workbook's J/kg band beside it needs one, so that band is None without a weight (never a default
weight, owner 2026-10-13).

Synthetic only: the demo athlete (backend/demo/build.py, small mode) built into tmp_path with its
weights cleared; network calls refused."""
from __future__ import annotations

import datetime as dt
import json
import socket

import pytest

from backend.engine.racepower import cptest as T
from backend.tests import demo_fixtures as F


def test_w_prime_prior_without_weight_keeps_the_kj_prior():
    with_w = T.w_prime_prior(62.0, "male")
    none = T.w_prime_prior(None, "male")
    assert none["mid"] == with_w["mid"] == 13100.0
    assert (none["lo"], none["hi"]) == (with_w["lo"], with_w["hi"])
    assert none["rwc_band_j"] is None
    assert with_w["rwc_band_j"] and all(v > 0 for v in with_w["rwc_band_j"])


@pytest.fixture(scope="module")
def built_no_weight(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-no-weight")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    base = root / "base" / (root / "base" / "current").read_text("utf-8").strip()
    p = base / "plan.json"
    raw = json.loads(p.read_text("utf-8"))
    assert raw.get("weights"), "the demo athlete should have weights to clear"
    raw["weights"] = []
    p.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
    return root


@pytest.fixture
def client(built_no_weight, monkeypatch):
    F.demo_env(monkeypatch, built_no_weight)
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from backend.api import racepower as RP
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    for k in ("inputs", "grade"):
        RP._cache.pop(k, None)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        yield c
    wko5views._dataset_cfg.cache_clear()
    for k in ("inputs", "grade"):
        RP._cache.pop(k, None)


def test_inputs_answers_without_any_weight(client):
    """The reproduction: the demo athlete has a fresh season-plan CP test and no detected W′."""
    r = client.get("/api/v1/racepower/inputs")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["weight"]["value"] is None
    plan = next(s for s in d["cp"]["sources"] if s["id"] == "plan")
    assert plan["fresh"]
    short = d["cp"]["short"]
    if short and short["label"].startswith("計畫 CP 測試"):
        assert short["w_prime"] == 13100.0          # the kJ prior, weight-free
