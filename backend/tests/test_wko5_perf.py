"""
Performance work: the vectorised mean-max fast path and the disk caches.

The fast path is only safe if it is indistinguishable from the reference
reconstruction of WKO5's loop, so that is what these tests pin. The caches are
checked for invalidation, because a stale cache is a correctness bug.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms.wko5_meanmax import (
    _fast_meanmax, duration_grid, meanmax_curve, meanmax_curve_reference,
)

CHANNELS = ("power", "heartrate", "speed", "cadence", "elevation")


def _dt(t):
    return [t[i] - (t[i - 1] if i else 0.0) for i in range(len(t))]


# --- fast path vs reference, synthetic ---------------------------------------

def test_fast_path_matches_reference_on_regular_samples():
    t = [float(i) for i in range(1, 301)]
    v = [100.0 + (i % 37) * 5 for i in range(300)]
    grid = duration_grid(300)
    assert _fast_meanmax(_dt(t), _dt(t), v, grid) == meanmax_curve_reference(
        _dt(t), _dt(t), v, grid)


def test_fast_path_matches_reference_with_invalid_samples():
    t = [float(i) for i in range(1, 201)]
    v = [None if 40 <= i < 60 else 200.0 + (i % 11) for i in range(200)]
    grid = duration_grid(200)
    fast, ref = (_fast_meanmax(_dt(t), _dt(t), v, grid),
                 meanmax_curve_reference(_dt(t), _dt(t), v, grid))
    for a, b in zip(fast, ref):
        assert (a is None) == (b is None)
        if a is not None:
            assert a == pytest.approx(b, rel=1e-12)


def test_fast_path_matches_reference_on_irregular_samples():
    t, acc = [], 0.0
    for i in range(200):
        acc += 1.0 if i % 5 else 7.5          # gaps, non-integer steps
        t.append(acc)
    v = [150.0 + (i % 23) * 3 for i in range(200)]
    grid = duration_grid(t[-1])
    fast, ref = (_fast_meanmax(_dt(t), _dt(t), v, grid),
                 meanmax_curve_reference(_dt(t), _dt(t), v, grid))
    for a, b in zip(fast, ref):
        assert (a is None) == (b is None)
        if a is not None:
            assert a == pytest.approx(b, rel=1e-12)


def test_fast_path_declines_inputs_it_cannot_prove():
    t = [float(i) for i in range(1, 11)]
    v = [100.0] * 10
    dx = _dt(t)
    assert _fast_meanmax(dx, [2.0] * 10, v, [5]) is None      # weights != increments
    zero = list(dx)
    zero[3] = 0.0
    assert _fast_meanmax(zero, zero, v, [5]) is None          # zero-length sample
    # meanmax_curve still answers, via the reference
    assert meanmax_curve(zero, zero, v, [5])[0] == pytest.approx(100.0)
