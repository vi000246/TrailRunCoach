"""SP-454 point 4 (owner 2026-10-20, Q1 A): the easy-kind sessions that are written with a warm-up
of their own — 長爬坡反覆 (暖身 15), 陡坡健走 (暖身 10 分平路), the 基礎期's 長爬坡有氧 (平路暖身 10) — follow
the 「已有暖身的課」 rule of 每堂課前加熱身 (SP-364): the warm-up is topped up to the set minutes, the
main part and the cool-down are not touched, the session gets longer by what was added (TSS too).
The detail says one warm-up time (no 「含暖身 N 分」 behind it) and the structure the watch gets has
that warm-up.

Before: they were treated as easy runs with no warm-up of their own — the detail got 「…暖身 15 分、
緩和 10 分；含暖身 20 分」, the 長爬坡反覆's pushed warm-up stayed 15′ (carve_warm couldn't borrow from
the climbs), and the 陡坡健走 / 長爬坡有氧 lost 10′ of their main part to the warm-up."""
from __future__ import annotations

import copy
import re

import pytest

from backend.engine import base_climb as BC
from backend.engine import plan_prefs as PP
from backend.engine import session_structure as SST
from backend.engine import specific_phase as SPP
from backend.engine import steep_hill as SH
from backend.engine import warmup as WU
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0}
ON = PP.Prefs(warmup_on=True, warmup_min=20)
ON10 = PP.Prefs(warmup_on=True, warmup_min=10)
RATES = {"easy": 60.0, "road": 70.0, "trail": 55.0}


def _secs(ss) -> int:
    return sum(x.sets * sum(y.seconds for y in x.steps) if isinstance(x, CW.Repeat) else x.seconds for x in ss)


def _durs(items) -> list:
    return [("rep", it["times"], [y["dur"]["value"] for y in it["items"]]) if it["kind"] == "repeat"
            else (it["kind"], it["dur"]["value"]) for it in items]


def _warm_times(detail: str) -> list[int]:
    """Every warm-up minute the text states (「暖身 N 分」 and 「含暖身 N 分」)."""
    return [int(x) for x in re.findall(r"暖身\s*(\d+)\s*分", detail or "")]


def climb_session() -> dict:
    """A 長爬坡反覆 as specific_phase.apply_climb makes it (2 × 20′ at 10 %)."""
    race = {"name": "測試 50K", "features": {}}
    sh = {"n": 2, "rep": 20, "grade": 10.0, "climb_dz": 800, "climb_min": None}
    m = SPP.climb_minutes(sh, 2)
    title, detail, hr = SPP.climb_text(race, sh, 2, 150.0)
    return {"id": "climb", "kind": "easy", "terrain": "trail", "minutes": m, "title": title, "detail": detail,
            "target": hr, "tss": round(55.0 * 1.1 * m / 60.0, 1), "source": SPP.SRC_CLIMB}


def steep_session(minutes: int = 45) -> dict:
    info = {"sim": {"grade": 15.0, "kmh": 4.0}, "pct": 0.15, "kg": None}
    title, detail = SH.session_text(info, minutes, 150.0)
    return {"id": "steep", "kind": "easy", "minutes": minutes, "title": title, "detail": detail, "target": "",
            "tss": minutes * 0.8}


def base_climb_session(minutes: int = 50, down=None) -> dict:
    return {"id": BC.ID, "kind": "easy", "minutes": minutes, "tss": minutes * 0.9, "target": "",
            **BC.session_fields(minutes, 150.0, down=down)}


# ---------------------------------------------------------------------------
# 長爬坡反覆 (the reported one)
# ---------------------------------------------------------------------------

def test_climb_repeats_top_their_own_warm_up_up_and_get_longer():
    s0 = climb_session()
    m0, t0 = s0["minutes"], s0["tss"]
    assert _warm_times(s0["detail"]) == [15]
    before = _durs(WS.derive(s0, FULL)["items"])
    assert before[0] == ("warm", 900)
    s = copy.deepcopy(s0)
    assert WU.apply_one(s, ON, RATES)
    # one warm-up time in the text, the cool-down as it was
    assert _warm_times(s["detail"]) == [20] and "含暖身" not in s["detail"]
    # SP-837: the 緩和 is the structure's (2 × 20′: 90 − 15 − 2 × 32 = 11), not a fixed 10
    assert s["detail"].endswith("暖身 20 分、緩和 11 分")
    # the main set and the cool-down untouched, the session 5′ longer, the TSS with it
    assert s["minutes"] == m0 + 5 and s["tss"] == pytest.approx(t0 + 5 / 60 * RATES["easy"])
    after = _durs(WS.derive(s, FULL)["items"])
    assert after[0] == ("warm", 1200) and after[1:] == before[1:]
    assert WS.lead_warm_s(WS.derive(s, FULL)["items"]) == 1200
    # idempotent
    assert not WU.apply_one(s, ON, RATES)


def test_climb_repeats_push_a_20_min_warm_up_and_the_whole_session():
    s = climb_session()
    legacy = CW.session_steps(copy.deepcopy(s), CW.Thresholds.of(FULL))
    WU.apply_one(s, ON, RATES)
    pushed = CW.session_steps(s, CW.Thresholds.of(FULL))
    assert pushed[0].kind == CW.EX_WARMUP and pushed[0].seconds == 1200
    assert _secs(pushed) == s["minutes"] * 60 == _secs(legacy) + 300
    # everything after the warm-up is the old push: the climbs and the cool-down
    assert pushed[1:] == legacy[1:]
    # the editor's structure pushes exactly the text path's steps
    d = WS.derive(s, FULL)
    assert WS.steps_to_coros(WS.normalize(d), WS.Ctx.of(FULL, CW._basis(s))) == pushed


def test_climb_repeats_card_minutes_follow_the_structure():
    """SP-437: the card's minutes / TSS come from the structure — after the warm-up they agree."""
    s = climb_session()
    WU.apply_one(s, ON, RATES)
    m = s["minutes"]
    SST.apply_one(s, FULL, RATES, ON)
    assert s["minutes"] == m
    assert WS.totals(WS.normalize(WS.derive(s, FULL)), WS.Ctx.of(FULL))["sec"] == pytest.approx(m * 60, abs=1)


def test_climb_repeats_keep_a_longer_own_warm_up():
    s0 = climb_session()
    s = copy.deepcopy(s0)
    assert not WU.apply_one(s, ON10, RATES) and s == s0                      # 15 ≥ 10: as it is
    assert WS.derive(s, FULL)["items"][0]["dur"]["value"] == 900


def test_climb_repeats_drop_the_old_mark():
    """A row decorated before the fix (「…暖身 15 分、緩和 10 分；含暖身 20 分」) loses the mark and gets
    the real top-up."""
    s = climb_session()
    m0 = s["minutes"]
    s["detail"] += "；含暖身 20 分"
    assert WU.apply_one(s, ON, RATES)
    assert _warm_times(s["detail"]) == [20] and "含暖身" not in s["detail"] and s["minutes"] == m0 + 5
    assert WS.derive(s, FULL)["items"][0]["dur"]["value"] == 1200


# ---------------------------------------------------------------------------
# the other easy-kind sessions with a warm-up of their own
# ---------------------------------------------------------------------------

def test_steep_walk_tops_up_its_flat_warm_up_and_keeps_the_climb():
    s0 = steep_session(45)
    assert _warm_times(s0["detail"]) == [10] and "陡坡 30 分" in s0["detail"]
    assert _durs(WS.derive(s0, FULL)["items"]) == [("warm", 600), ("work", 1800), ("cool", 300)]
    s = copy.deepcopy(s0)
    assert WU.apply_one(s, ON, RATES)
    assert _warm_times(s["detail"]) == [20] and "含暖身" not in s["detail"]
    assert "暖身 20 分平路 → 陡坡 30 分 → 緩和 5 分" in s["detail"]
    assert s["minutes"] == 55
    assert _durs(WS.derive(s, FULL)["items"]) == [("warm", 1200), ("work", 1800), ("cool", 300)]
    pushed = CW.session_steps(s, CW.Thresholds.of(FULL))
    assert pushed[0].seconds == 1200 and _secs(pushed) == 55 * 60
    assert not WU.apply_one(s, ON, RATES)
    t = copy.deepcopy(s0)
    assert not WU.apply_one(t, ON10, RATES) and t == s0                       # 10 ≥ 10


@pytest.mark.parametrize("down", [None, 15])
def test_base_climb_tops_up_its_warm_up_and_keeps_the_climb(down):
    s0 = base_climb_session(60 if down else 50, down)
    m0 = s0["minutes"]
    work0 = [x["dur"]["value"] for x in s0["steps"]["items"][1:]]
    s = copy.deepcopy(s0)
    assert WU.apply_one(s, ON, RATES)
    items = s["steps"]["items"]
    assert items[0]["kind"] == "warm" and items[0]["dur"]["value"] == 1200
    assert [x["dur"]["value"] for x in items[1:]] == work0                    # the climb (and run-down) kept
    assert s["minutes"] == m0 + 10
    assert s["tss"] == pytest.approx(s0["tss"] + 10 / 60 * RATES["easy"])
    # the text and the title say the new times
    assert _warm_times(s["detail"]) == [20] and "含暖身" not in s["detail"]
    assert f"長爬坡有氧 {m0 + 10}′" in s["title"]
    if down is None:
        assert s["detail"].endswith("暖身 20 分、緩和 10 分")
    else:
        assert s["detail"].endswith("暖身 20 分")
    assert s0["steps"]["items"][0]["dur"]["value"] == 600                    # the input was not mutated
    pushed = CW.session_steps(s, CW.Thresholds.of(FULL))
    assert pushed[0].seconds == 1200 and _secs(pushed) == s["minutes"] * 60
    assert not WU.apply_one(s, ON, RATES)
    t = copy.deepcopy(s0)
    assert not WU.apply_one(t, ON10, RATES) and t == s0


def test_week_plan_session_objects_get_the_same():
    """overview.week_plan decorates Session objects (apply_objs): the new title, detail, steps and
    minutes are written back; the minutes added are what apply_objs returns."""
    from backend.engine.overview import Session
    rows = [climb_session(), steep_session(45), base_climb_session(50)]
    objs = [Session(**{k: v for k, v in r.items() if k in Session.__dataclass_fields__}) for r in rows]
    m0 = [o.minutes for o in objs]
    assert WU.apply_objs(objs, ON, RATES) == pytest.approx(5 + 10 + 10)
    assert [o.minutes for o in objs] == [m0[0] + 5, 55, 60]
    assert all(_warm_times(o.detail) == [20] for o in objs)
    assert objs[2].title.startswith("長爬坡有氧 60′") and objs[2].steps["items"][0]["dur"]["value"] == 1200
    assert WS.derive(rows[0] | {"detail": objs[0].detail, "minutes": objs[0].minutes}, FULL)["items"][0]["dur"]["value"] == 1200


def test_plain_easy_runs_still_carve_their_warm_up():
    """Unchanged: an easy run with no warm-up of its own keeps its time (含暖身 N 分)."""
    s = {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "target": "", "detail": "", "tss": 45.0}
    assert WU.apply_one(s, ON, RATES) and s["minutes"] == 45 and WU.mark_min(s) == 20
