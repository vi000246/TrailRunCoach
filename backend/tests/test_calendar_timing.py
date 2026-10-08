"""GET /overview/plan/calendar writes one app-log line with where its time went (SP-362):
inputs, lock_wait, reconcile, view, extras, suggestions (applog.phases). Hand-built inputs
(test_plan_store.Env), no dataset."""
import logging
import time
from datetime import date

import pytest

from backend import applog
from backend.api import plan_sessions
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def test_calendar_logs_its_phases(monkeypatch, caplog):
    def slow_extras(start, end):
        time.sleep(0.12)
        return {"activities": [], "phases": []}
    monkeypatch.setattr(plan_sessions, "_range_extras", slow_extras)
    with Env(monkeypatch) as e, caplog.at_level(logging.INFO, logger="backend"):
        assert e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").status_code == 200
    lines = [r.getMessage() for r in caplog.records if "calendar took" in r.getMessage()]
    assert len(lines) == 1, lines
    line = lines[0]
    assert "days=7" in line and "| " in line and "extras 0.1 s" in line
    assert plan_sessions._TIMING.get() is None                 # nothing left behind


def test_phases_line_levels_and_rest(caplog):
    with caplog.at_level(logging.INFO, logger="backend"):
        t0 = time.perf_counter() - 5.0                          # 5 s ago: slow
        total = applog.phases("calendar", t0, {"inputs": 3.0, "extras": 0.01}, days=7)
    rec = caplog.records[-1]
    assert total >= 5.0 and rec.levelno == logging.WARNING
    msg = rec.getMessage()
    assert msg.startswith("slow: calendar took 5.0 s days=7 | inputs 3.0 s, other 2.0 s")
    assert "extras" not in msg                                  # under 0.05 s: left out
    with caplog.at_level(logging.INFO, logger="backend"):
        applog.phases("calendar", time.perf_counter(), {})
    assert caplog.records[-1].levelno == logging.INFO
