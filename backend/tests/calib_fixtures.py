"""Self-consistency fixture template for per-athlete calibration items
(engine/calibrate.py; generalize-athlete plan §0.1 「作者的回歸保證」).

Every item gets one test like test_calibrate.test_aet_heat_beta_self_consistent:

  1. build synthetic data that mimics the author's distribution (n, spread,
     noise) — never the WKO5 folder;
  2. feed it to the item's fit (monkeypatch the item's row collector, or a
     FakeDataset from wko5_fakes.py when the fit reads workouts);
  3. `assert_self_consistent(item, ds, current, se)`: the stored value
     (after shrinkage) lies within ±1 SE of today's constant.

The generators here are deterministic (seeded) so a failure is reproducible.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL


def linear_rows(n: int, slope: float, *, x_name: str = "temp_c", y_name: str = "hr", x_mean: float = 24.0,
                x_sd: float = 4.0, y0: float = 140.0, noise_sd: float = 3.0, seed: int = 1,
                extra: Optional[dict] = None) -> list[dict]:
    """n rows {y_name, x_name, **extra} with y = y0 + slope·(x − x_mean) + noise."""
    rng = np.random.default_rng(seed)
    x = rng.normal(x_mean, x_sd, n)
    y = y0 + slope * (x - x_mean) + rng.normal(0.0, noise_sd, n)
    return [{y_name: float(b), x_name: float(a), **(extra or {})} for a, b in zip(x, y)]


def stored_value(item: CAL.Item, ds, today: Optional[dt.date] = None) -> Optional[dict]:
    """The entry calibrate.run would store for `item` on `ds` (None = not enough data)."""
    return CAL.shrink(item, item.fit(ds, today or dt.date(2026, 10, 1)))


def assert_self_consistent(item: CAL.Item, ds, current: float, se: float,
                           today: Optional[dt.date] = None) -> dict:
    e = stored_value(item, ds, today)
    assert e is not None, f"{item.name}: not enough data in the fixture (min_n {item.min_n})"
    assert abs(e["value"] - current) <= se, \
        f"{item.name}: fitted {e['value']:.3f} vs today's {current:.3f} (±{se:.3f})"
    return e
