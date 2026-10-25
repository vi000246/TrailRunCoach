"""Shared helpers of the debug API tests (SP-371): the 課表 test harness (test_plan_store.Env:
in-memory DB, hand-built plan inputs, a fake COROS Training Hub) with the debug routers mounted
and the tenant's folder in tmp_path. Synthetic data only. No PIN since SP-373 (personal tokens)."""
from __future__ import annotations

import contextlib

from backend import debug_auth as DA

ADMIN = "/api/v1/settings/debug-api"
DBG = "/api/v1/debug"
# every read / export scope (the read-only endpoints' tests); write:config has its own 7-day rule (SP-802)
ALL_SCOPES = [s for s in DA.SCOPES if s != DA.WRITE_SCOPE]
ENDPOINTS = ("/activity?date=2026-10-01", "/plan", "/day?date=2026-10-01", "/thresholds?date=2026-10-01",
             "/sync", "/export/config", "/export/plan")


def no_dataset():
    raise RuntimeError("no dataset in this test")


@contextlib.contextmanager
def debug_env(monkeypatch, tmp_path):
    from backend.api import debug as debug_api
    from backend.tests.test_plan_store import Env
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path))
    monkeypatch.delenv("TRC_DEBUG_PIN", raising=False)        # no longer read (SP-373)
    monkeypatch.setattr(debug_api, "_dataset", no_dataset)
    DA.reset_limits()
    with Env(monkeypatch) as e:
        e.c.app.include_router(debug_api.router)
        e.c.app.include_router(debug_api.admin_router)
        yield e
    DA.reset_limits()


def enable(e, on: bool = True):
    return e.c.put(ADMIN, json={"enabled": on})


def make_token(e, scopes=None, days=None, name="agent") -> str:
    body = {"name": name}
    if scopes is not None:
        body["scopes"] = scopes
    if days is not None:
        body["days"] = days
    r = e.c.post(f"{ADMIN}/tokens", json=body)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}
