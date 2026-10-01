import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import numpy as np

from backend.engine.workout_review import form_drift


def _run(power):
    n = len(power)
    t = np.arange(n, dtype=float)
    speed = np.full(n, 3.0)
    cad = np.full(n, 85.0)                      # strides/min: all running
    gct = np.where(np.arange(n) < n // 2, 250.0, 300.0)
    return t, speed, cad, gct


def test_split_by_work_when_power_present():
    # first 2/3 of the time at 100 W, last 1/3 at 200 W: equal work in each part,
    # so the work midpoint sits at 2/3 of the time, not 1/2
    n = 3000
    p = np.where(np.arange(n) < 2000, 100.0, 200.0)
    t, speed, cad, _ = _run(p)
    x = np.where(np.arange(n) < 2000, 1.0, 2.0)
    out = form_drift(t, speed, {"x": x}, cad, p)
    assert out["_split"] == "work"
    assert abs(out["x"]["first"] - 1.0) < 1e-6        # the first half of the work is all at 100 W
    assert abs(out["x"]["last"] - 2.0) < 1e-6


def test_falls_back_to_moving_time_without_power():
    n = 3000
    t, speed, cad, gct = _run(np.zeros(n))
    out = form_drift(t, speed, {"gct": gct}, cad, None)
    assert out["_split"] == "time"
    assert abs(out["gct"]["first"] - 250.0) < 1e-6 and abs(out["gct"]["last"] - 300.0) < 1e-6
