"""
SP-474: the walking sessions' warm-up has no HR floor on the watch.

ME 負重爬坡 (SP-443), 陡坡健走 (SP-409) and 攻頂日模擬 (SP-114; its warm-up is 每堂課前加熱身's
「含暖身 N 分」, SP-364) are walking sessions (target_policy walk, Ctx.walk): every auto-easy step got
hr_profile.walk_band — the easy-run cap as the floor up to the 75 % HRmax uphill cap. During the warm-up
the HR isn't up yet, below that floor, and the watch kept alerting. The warm-up now has no target
(like the other no-target steps: NONE_IF["warm"] for its TSS, not the work step's 1.05); the uphill
keeps the walk band. Synthetic sessions; nothing reads the athlete's data.
"""
import pytest

from backend.engine import hr_profile as HP
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW
from backend.tests.test_me_climb_structure_sp443 import TH_WALK, _me
from backend.tests.test_special_easy_structure_sp409 import STEEP

SUMMIT = {"id": "long", "kind": "long", "terrain": "trail", "title": "攻頂日模擬｜嘉明湖", "minutes": 240,
          "detail": "一天爬升約 1200 m，背 9 kg 的背包（行程背包），爬上去再下來；含暖身 15 分",
          "target": "上坡心率 ≤ 147 bpm", "day": "2026-10-10", "tss": 200.0}


def _sessions():
    return {"me": _me(), "steep": STEEP, "summit": SUMMIT}


@pytest.mark.parametrize("name", ["me", "steep", "summit"])
def test_walking_warm_up_pushes_no_hr_floor(name):
    """The bug (SP-474): the warm-up step went to the watch as ("hr", easy cap, walk cap)."""
    s = _sessions()[name]
    st = CW.session_steps({**s, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert st[0].kind == CW.EX_WARMUP
    assert st[0].intensity is None                      # no HR target: no floor to fall under
    # the uphill keeps the walking session's band (easy cap → 75 % HRmax)
    up = next(x for x in st[1:] if x.kind == CW.EX_TRAIN)
    assert up.intensity == ("hr", 124, 147)
    # the push and the editor's structure are the same steps
    c = WS.session_ctx(s, TH_WALK)
    assert WS.steps_to_coros(WS.normalize(WS.derive(s, TH_WALK)), c) == st


@pytest.mark.parametrize("name", ["me", "steep", "summit"])
def test_walking_warm_up_resolves_to_no_target_and_a_warm_up_if(name):
    """The editor / preview: 「不設目標」, and the TSS counts it at the no-target warm-up IF (0.65), never
    the no-target work step's 1.05."""
    s = _sessions()[name]
    d = WS.normalize(WS.derive(s, TH_WALK))
    c = WS.session_ctx(s, TH_WALK)
    warm = d["items"][0]
    assert warm["kind"] == "warm"
    r = WS.resolve(warm, c)
    assert r.type == "none" and r.intensity is None and r.frac is None
    one = {"items": [warm]}
    secs = warm["dur"]["value"]
    assert WS.totals(one, c)["tss"] == pytest.approx(secs * WS.NONE_IF["warm"] ** 2 * 100 / 3600, abs=0.05)


def test_running_warm_ups_and_an_explicit_warm_up_target_unchanged():
    """Only the walking warm-up: a running session's warm-up (no walk cap) keeps the easy band; an
    explicit HR target on a walking warm-up is the user's. (The 陡坡健走's flat cool-down kept the walk
    band here until the user's 2026-10-13 follow-up: test_steep_walk_cool_down_pushes_no_hr_floor.)"""
    run = {"kind": "quality", "title": "VO2max 間歇 5×4 分上坡", "minutes": 60, "detail": "暖身 15 分；休 3 分"}
    rs = CW.session_steps({**run, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert rs[0].kind == CW.EX_WARMUP and rs[0].intensity is not None and rs[0].intensity[0] == "hr"
    c = WS.session_ctx(STEEP, TH_WALK)
    own = {"kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "hr", "mode": "abs", "lo": 100, "hi": 130}}
    assert WS.resolve(own, c).intensity == ("hr", 100, 130)


def test_without_a_walk_cap_the_walking_session_is_the_easy_band_as_before():
    """No walk cap (no max HR / age): no walk band anywhere (hr_profile.walk_band → the easy band), the
    warm-up included — unchanged (SP-474 is the walk band's floor)."""
    th = {k: v for k, v in TH_WALK.items() if k != "walk_cap"}
    st = CW.session_steps({**STEEP, "basis": "hr"}, CW.Thresholds.of(th))
    assert st[0].intensity == st[1].intensity and st[1].intensity is not None
    assert HP.walk_band(None, ("hr", 120, 145)) == ("hr", 120, 145)


# ---- SP-474 follow-up (the user 2026-10-13): the 陡坡健走's 「平路緩和」 5′ has no HR floor either --------

def test_steep_walk_cool_down_pushes_no_hr_floor():
    """The bug: the 平路緩和 went to the watch as ("hr", easy cap, walk cap) — the HR is coming down after
    the steep part, under that floor, and the watch kept alerting. Now no target, as the warm-up."""
    st = CW.session_steps({**STEEP, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert [x.kind for x in st] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert st[-1].seconds == 300 and st[-1].intensity is None
    assert st[1].intensity == ("hr", 124, 147)                      # the steep part keeps the walk band
    c = WS.session_ctx(STEEP, TH_WALK)
    assert WS.steps_to_coros(WS.normalize(WS.derive(STEEP, TH_WALK)), c) == st     # the editor = the push


def test_steep_walk_cool_down_resolves_to_no_target_and_a_cool_down_if():
    """The editor / preview: 「不設目標」, and its TSS at the no-target cool-down IF (0.65), never the
    no-target work step's 1.05."""
    d = WS.normalize(WS.derive(STEEP, TH_WALK))
    c = WS.session_ctx(STEEP, TH_WALK)
    cool = d["items"][-1]
    assert cool["kind"] == "cool" and cool["dur"]["value"] == 300
    r = WS.resolve(cool, c)
    assert r.type == "none" and r.intensity is None and r.frac is None
    assert WS.totals({"items": [cool]}, c)["tss"] == pytest.approx(300 * WS.NONE_IF["cool"] ** 2 * 100 / 3600,
                                                                    abs=0.05)


def test_running_cool_downs_and_an_explicit_cool_down_target_unchanged():
    """Only the walking sessions' cool-down: a running session's cool-down keeps the easy band; an explicit
    HR target the user set on the 陡坡健走's cool-down stays."""
    run = {"kind": "quality", "title": "VO2max 間歇 5×4 分上坡", "minutes": 60, "detail": "暖身 15 分；休 3 分；緩和 10 分"}
    rs = CW.session_steps({**run, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert rs[-1].kind == CW.EX_COOLDOWN and rs[-1].intensity is not None and rs[-1].intensity[0] == "hr"
    c = WS.session_ctx(STEEP, TH_WALK)
    own = {"kind": "cool", "dur": {"type": "time", "value": 300}, "target": {"type": "hr", "mode": "abs", "lo": 100, "hi": 130}}
    assert WS.resolve(own, c).intensity == ("hr", 100, 130)
