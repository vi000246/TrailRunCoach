"""SP-837: the 長爬坡反覆's detail said 「緩和 10 分」 while the structure (and the COROS push) had the
rest of the 5′-rounded session — 11′ for 2 × 20, 9′ for 3 × 20. The text now says the structure's
minutes (specific_phase.climb_cool)."""
from __future__ import annotations

import copy
import re

import pytest

from backend.engine import specific_phase as SPP
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0}


def _session(n: int, rep: int, *, minutes=None, speed: bool = False) -> dict:
    race = {"name": "測試 50K", "features": {}}
    if speed:
        race["features"] = {"climb": {"km": 5.0, "dz": 800.0, "grade": 16.0, "minutes": 60.0}}
    sh = {"n": n, "rep": rep, "grade": 10.0, "climb_dz": 800, "climb_min": 60.0 if speed else None}
    m = SPP.climb_minutes(sh, n) if minutes is None else minutes
    title, detail, hr = SPP.climb_text(race, sh, n, 150.0)
    return {"id": "climb", "kind": "easy", "terrain": "trail", "minutes": m, "title": title, "detail": detail,
            "target": hr, "tss": round(55.0 * 1.1 * m / 60.0, 1), "source": SPP.SRC_CLIMB}


def _text_cool(s: dict) -> int:
    return int(re.search(r"緩和\s*(\d+)\s*分", s["detail"]).group(1))


@pytest.mark.parametrize("n,rep,cool", [(2, 20, 11), (3, 20, 9), (3, 15, 8), (4, 10, 11), (2, 15, 12)])
@pytest.mark.parametrize("speed", [False, True])
def test_text_cool_down_is_the_structures(n, rep, cool, speed):
    s = _session(n, rep, speed=speed)
    items = WS.derive(s, FULL)["items"]
    assert items[-1]["kind"] == "cool"
    assert items[-1]["dur"]["value"] == cool * 60
    assert _text_cool(s) == cool == SPP.climb_cool({"n": n, "rep": rep}, n)
    assert "暖身 15 分" in s["detail"]


@pytest.mark.parametrize("n,rep", [(2, 20), (3, 20), (3, 15)])
def test_pushed_cool_down_is_the_texts(n, rep):
    s = _session(n, rep)
    pushed = CW.session_steps(copy.deepcopy(s), CW.Thresholds.of(FULL))
    assert pushed[-1].kind == CW.EX_COOLDOWN
    assert pushed[-1].seconds == _text_cool(s) * 60


def test_english_text_says_the_same_minutes():
    from backend import i18n
    with i18n.use_locale("en"):
        s = _session(3, 20)
    assert "cool down 9 min" in s["detail"] and "Warm up 15 min" in s["detail"]
