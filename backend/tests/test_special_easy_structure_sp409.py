"""
SP-409: the planner's special easy runs pushed as what they are, not one block.

長爬坡反覆 (engine/specific_phase.apply_climb, kind easy) used to derive / push as one step of the
whole session with no target (target_policy: long climbs = none), estimated at the no-target IF of a
work step (1.05) — TSS 165 for a 90′ session the card puts at ≈ 87. Owner 2026-10-13 (SP-409 Q1 A):
暖身 15 → N×（上坡 rep＋下坡 rep × DOWN_SHARE）→ 緩和; the uphill keeps no HR / power target on the
watch, its name says the HR suggestion, and it carries an IF so the structure's TSS matches the card.
陡坡健走 (engine/steep_hill.apply, kind easy): 暖身 10 → 陡坡 → 緩和 5 as its detail says.
Synthetic sessions / a tiny synthetic GPX (tmp folder); nothing reads the athlete's data.
"""
import datetime as dt

import pytest

from backend.engine import event_gpx as EG
from backend.engine import hr_profile as HP
from backend.engine import specific_phase as SP
from backend.engine import steep_hill as SH
from backend.engine import workout_steps as WS
from backend.engine.planning import Event, Plan
from backend.sync import coros_workouts as CW
from backend.tests.test_event_gpx import synth_gpx

FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0}
HR = "心率約輕鬆跑上限 150 bpm（實測 AeT）"
CLIMB = {"id": "climb", "kind": "easy", "terrain": "trail", "title": "長爬坡反覆 2×20 分（8% 坡）", "minutes": 90,
         "target": HR, "day": "2026-10-14", "tss": 86.7}
STEEP = {"id": "steep", "kind": "easy", "terrain": "trail", "title": "陡坡健走 14%（模擬負重 9 kg）",
         "minutes": 45, "day": "2026-10-08"}


def _push(s, th=FULL):
    return CW.session_steps(s, CW.Thresholds.of(th))


# ---------------------------------------------------------------------------------- 長爬坡反覆

def test_climb_derives_warm_up_repeats_and_cool_down():
    d = WS.derive(CLIMB, FULL)
    items = d["items"]
    assert [x["kind"] for x in items] == ["warm", "repeat", "cool"]
    assert items[0]["dur"] == {"type": "time", "value": 15 * 60}
    r = items[1]
    assert r["times"] == 2 and [x["dur"]["value"] for x in r["items"]] == [20 * 60, 12 * 60]
    up, down = r["items"]
    assert "上坡" in up["note"] and HR in up["note"] and "下坡" in down["note"]
    # the whole session: 15 + 2 × (20 + 12) = 79, the 緩和 takes the rest of the 90 (climb_minutes rounds to 5)
    assert items[2]["dur"]["value"] == 11 * 60
    assert WS.totals(WS.normalize(d), WS.session_ctx(CLIMB, FULL))["sec"] == 90 * 60


def test_climb_uphill_has_no_watch_target_and_its_name_says_the_hr():
    c = WS.session_ctx(CLIMB, FULL)
    assert c.basis == "none"                                   # target_policy: long climbs = none (unchanged)
    got = WS.steps_to_coros(WS.normalize(WS.derive(CLIMB, FULL)), c)
    assert len(got) == 3 and isinstance(got[1], CW.Repeat) and got[1].sets == 2
    up, down = got[1].steps
    assert up.intensity is None and down.intensity is None
    assert up.seconds == 1200 and HR in up.name
    assert got[0].intensity is None and got[2].intensity is None
    assert got[0].seconds + got[2].seconds + 2 * (up.seconds + down.seconds) == 5400


def test_climb_structure_tss_matches_the_card():
    """The structure's TSS ≈ the card's (rate × 1.1 × h), not the no-target 1.05 of one block (165)."""
    t = WS.totals(WS.normalize(WS.derive(CLIMB, FULL)), WS.session_ctx(CLIMB, FULL))
    assert t["tss"] < 120
    assert t["tss"] == pytest.approx(CLIMB["tss"], rel=0.15)


def test_climb_push_is_the_derived_structure():
    st = _push(CLIMB)
    assert [type(x).__name__ for x in st] == ["Step", "Repeat", "Step"]
    assert st[0].kind == CW.EX_WARMUP and st[2].kind == CW.EX_COOLDOWN
    assert st[1].sets == 2 and st[1].steps[0].intensity is None and HR in st[1].steps[0].name
    assert st[0].seconds + st[2].seconds + 2 * sum(x.seconds for x in st[1].steps) == 90 * 60
    # the push and the editor's structure are the same steps
    c = WS.session_ctx(CLIMB, FULL)
    assert WS.steps_to_coros(WS.normalize(WS.derive(CLIMB, FULL)), c) == st


def test_climb_three_repeats_120_minutes():
    s = {**CLIMB, "title": "長爬坡反覆 3×20 分（9% 坡）", "minutes": 120}
    items = WS.derive(s, FULL)["items"]
    assert items[1]["times"] == 3
    # 15 + 3 × 32 = 111: the 緩和 is 9′ (the session's 120 = climb_minutes rounded to 5)
    assert items[2]["dur"]["value"] == 9 * 60
    assert WS.totals(WS.normalize(WS.derive(s, FULL)), WS.session_ctx(s, FULL))["sec"] == 120 * 60


def test_climb_too_short_for_its_repeats_stays_one_block():
    """A session the user cut below its repeats keeps the old one block (nothing to guess)."""
    s = {**CLIMB, "minutes": 60}
    items = WS.derive(s, FULL)["items"]
    assert len(items) == 1 and items[0]["dur"]["value"] == 3600


# ---- the generator's real session (apply_climb), not a hand-written one

MON = dt.date(2026, 9, 28)
GPX = synth_gpx([(5, 800), (3, -300), (4, 700), (8, -1200)])


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    EG._memo.clear()
    return tmp_path


def test_the_generated_climb_session_pushes_its_repeats(store):
    EG.save("e1", GPX, "hehuan.gpx")
    ev = Event("e1", "合歡山越野", "2026-11-07", kind="race", priority="A", distance_km=30, climbing_m=2000,
               est_hours=5.0)
    race = SP.race_day(Plan(events=[ev]), MON - dt.timedelta(days=1))
    info = SP.week_context(kind="specific", mode="specific", monday=MON, race=race)
    d = lambda i: (MON + dt.timedelta(days=i)).isoformat()
    ss = [{"id": "long", "kind": "long", "day": d(5), "minutes": 170, "tss": 140.0},
          {"id": "quality", "kind": "quality", "day": d(1), "minutes": 55, "tss": 60.0},
          {"id": "easy1", "kind": "easy", "day": d(3), "minutes": 60, "tss": 40.0, "title": "輕鬆跑"},
          {"id": "easy2", "kind": "easy", "day": d(4), "minutes": 60, "tss": 40.0, "title": "輕鬆跑"},
          {"id": "easy3", "kind": "easy", "day": d(6), "minutes": 70, "tss": 45.0, "title": "輕鬆跑"}]
    SP.apply_climb(ss, info, aet=150.0, rates={"trail": 52.5})
    s = next(x for x in ss if x["id"] == "climb")
    n, rep = info["planned"][0]["n"], info["planned"][0]["rep"]
    items = WS.derive(s, FULL)["items"]
    assert items[1]["times"] == n and items[1]["items"][0]["dur"]["value"] == rep * 60
    assert WS.totals(WS.normalize(WS.derive(s, FULL)), WS.session_ctx(s, FULL))["sec"] == s["minutes"] * 60
    t = WS.totals(WS.normalize(WS.derive(s, FULL)), WS.session_ctx(s, FULL))
    assert t["tss"] == pytest.approx(s["tss"], rel=0.15)
    st = _push(s)
    assert isinstance(st[1], CW.Repeat) and st[1].sets == n and st[1].steps[0].intensity is None


# ---------------------------------------------------------------------------------- 陡坡健走

TH_WALK = {"cp": 250.0, "lthr": 165.0, "aet": 145.0, "walk_cap": HP.walk_cap(196.0, 145.0)}


def test_steep_walk_derives_warm_up_steep_part_cool_down():
    items = WS.derive(STEEP, TH_WALK)["items"]
    assert [x["kind"] for x in items] == ["warm", "work", "cool"]
    assert [x["dur"]["value"] for x in items] == [600, 30 * 60, 300]        # 暖身 10 → 陡坡 30 → 緩和 5


def test_steep_walk_push_keeps_the_walk_cap_on_the_steep_part():
    st = CW.session_steps({**STEEP, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert [x.kind for x in st] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert [x.seconds for x in st] == [600, 1800, 300]
    main = st[1]
    assert main.intensity == ("hr", 124, 147) and "RPE ≤ 13" in main.name and "下坡" in main.name
    # the same steps as the editor's structure
    c = WS.session_ctx(STEEP, TH_WALK)
    assert WS.steps_to_coros(WS.normalize(WS.derive(STEEP, TH_WALK)), c) == st


def test_steep_walk_generated_session_matches_its_detail():
    ev = {"id": "e1", "name": "嘉明湖", "start": (MON + dt.timedelta(weeks=5, days=4)).isoformat(),
          "days": 3, "kind": "baiyue", "pack_kg": None}
    info = SH.week_context(kind="specific", mode="build", monday=MON, event=ev, weight=68.0)
    d = lambda i: (MON + dt.timedelta(days=i)).isoformat()
    ss = [{"id": "long", "kind": "long", "day": d(5), "minutes": 150, "tss": 120.0, "title": "LSD"},
          {"id": "easy1", "kind": "easy", "day": d(2), "minutes": 45, "tss": 33.0, "title": "輕鬆跑"}]
    SH.apply(ss, info, aet=145.0, walk=HP.walk_cap(196.0, 145.0))
    s = next(x for x in ss if x["id"] == "steep")
    m = s["minutes"]
    assert f"暖身 10 分平路 → 陡坡 {m - 15} 分 → 緩和 5 分" in s["detail"]
    items = WS.derive(s, TH_WALK)["items"]
    assert [x["dur"]["value"] for x in items] == [600, (m - 15) * 60, 300]


def test_steep_walk_too_short_stays_one_block():
    items = WS.derive({**STEEP, "minutes": 20}, TH_WALK)["items"]
    assert len(items) == 1 and items[0]["dur"]["value"] == 1200


# ---------------------------------------------------------------------------------- the rest unchanged

def test_other_easy_runs_derive_as_before():
    """Plain / trail easy runs stay one block; strides and the heat run keep their own structure."""
    for s in ({"kind": "easy", "title": "輕鬆跑", "minutes": 45},
              {"kind": "easy", "title": "輕鬆跑（山路）", "minutes": 45, "terrain": "trail"},
              {"kind": "easy", "title": "長爬坡", "minutes": 60}):           # no 「N×M 分」: not a 長爬坡反覆
        items = WS.derive(s, FULL)["items"]
        assert len(items) == 1 and items[0]["dur"]["value"] == s["minutes"] * 60
    st = WS.derive({"kind": "easy", "title": "輕鬆跑＋6×20 秒衝刺", "minutes": 50}, FULL)["items"]
    assert [x["kind"] for x in st] == ["work", "repeat"] and st[1]["times"] == 6
    ht = WS.derive({"kind": "easy", "title": "輕鬆跑＋熱適應", "minutes": 50}, FULL)["items"]
    assert [x["note"] for x in ht] == ["熱適應：慢慢進入", "熱適應：照心率、配速放慢", "走路降溫"]
    # kind quality / long with the words are not these easy runs
    assert WS.climb_reps({"kind": "long", "title": "長爬坡反覆 2×20 分"}) is None
    assert not WS.steep_walk({"kind": "long", "title": "陡坡健走"})
