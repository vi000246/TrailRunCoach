"""
SP-479 (the owner 2026-10-14, option B): the warm-up of a running session — and of a walking session without a
walk cap — has no target. The easy band's floor (0.75 × LTHR, the plan zones' Z2 bottom…) was above a HR still
coming up and the watch kept alerting; COROS takes an HR target only as lo–hi, so the warm-up has none at all.
The cool-down keeps the easy band; the walking sessions with a walk cap are SP-474's (warm-up and the 陡坡健走's
cool-down without a target). A warm-up still counts as easy running (chart, TSS). Pushed sessions with a warm-up
change once (their fingerprint) and are re-pushed once. Synthetic sessions; nothing reads the athlete's data.
"""
import pytest

from backend.engine import interval_library as IL
from backend.engine import workout_steps as WS
from backend.i18n import use_locale
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import FakeHub, _push, _status, make_db, run, week1
from backend.tests.test_coros_workouts import TH as PUSH_TH
from backend.tests.test_special_easy_structure_sp409 import STEEP
from backend.tests.test_walk_warmup_hr_floor_sp474 import TH_WALK

TH = {"cp": 250.0, "lthr": 168.0, "aet": 150.0}

QUALITY = {"id": "q", "kind": "quality", "title": "閾值 3×10 分", "minutes": 60, "tss": 70.0, "target": "",
           "detail": "休 2–3 分鐘；暖身 15 分、緩和 10 分", "day": "2026-10-01"}
CP_TEST = {"id": "t", "kind": "test", "title": "CP 測試 12 分 + 3 分", "minutes": 60, "tss": 60.0, "target": "",
           "detail": "", "day": "2026-10-01"}
HEAT = {"id": "h", "kind": "easy", "title": "熱適應輕鬆跑", "minutes": 60, "tss": 55.0, "heat": True,
        "detail": "", "target": "", "day": "2026-10-01"}


@pytest.mark.parametrize("s", [QUALITY, CP_TEST, HEAT], ids=["quality", "cp_test", "heat"])
@pytest.mark.parametrize("basis", ["hr", "power"])
def test_running_warm_up_pushes_no_target_and_the_cool_down_keeps_the_easy_band(s, basis):
    th = CW.Thresholds.of(TH)
    st = CW.session_steps({**s, "basis": basis}, th)
    assert st[0].kind == CW.EX_WARMUP and st[0].intensity is None
    cool = [x for x in st if isinstance(x, CW.Step) and x.kind == CW.EX_COOLDOWN]
    if s is not HEAT:                                   # the heat run's 5′ cool-down is a walk (no target, as before)
        assert cool and all(x.intensity == CW.easy_hr(th) for x in cool)
    # the text push and the editor's structure are the same steps
    d = WS.derive({**s, "basis": basis}, TH)
    c = WS.session_ctx({**s, "basis": basis}, TH)
    assert WS.steps_to_coros(WS.normalize(d), c) == st


@pytest.mark.parametrize("key", sorted(IL.ALL)[:12])
def test_a_library_variant_warm_up_has_no_target(key):
    """The 間歇 library's warm-up blocks (輕鬆跑暖身 / 輕鬆跑→漸進; drills were already open)."""
    s = {"id": "q", "kind": "quality", "title": "間歇", "minutes": 60, "variant_key": key, "detail": "", "target": "",
         "day": "2026-10-01", "tss": 60.0}
    st = CW.session_steps(s, CW.Thresholds.of(TH))
    warm = [x for x in st if isinstance(x, CW.Step) and x.kind == CW.EX_WARMUP]
    assert warm and all(x.intensity is None for x in warm)
    cool = [x for x in st if isinstance(x, CW.Step) and x.kind == CW.EX_COOLDOWN]
    assert cool and all(x.intensity == CW.easy_hr(CW.Thresholds.of(TH)) for x in cool)


def test_editor_and_preview_say_no_target_and_the_tss_is_unchanged():
    """The editor (view: resolved + the 「推到手錶會長這樣」 preview) reads 「不設目標」 / "No target" for the
    warm-up; it still counts as easy running — the chart's level and the TSS are the easy band's."""
    d = WS.normalize(WS.derive(QUALITY, TH))
    c = WS.session_ctx(QUALITY, TH)
    warm = d["items"][0]
    r = WS.resolve(warm, c)
    assert r.type == "none" and r.intensity is None and r.text == "不設目標"
    easy = WS.resolve({**warm, "kind": "work"}, c)             # the same step as an easy work step
    assert easy.type == "hr" and r.frac == easy.frac is not None
    # the TSS of the warm-up = the easy band's (not NONE_IF["warm"])
    one = {"items": [warm]}
    secs = warm["dur"]["value"]
    assert WS.totals(one, c)["tss"] == pytest.approx(secs * easy.frac ** 2 * 100 / 3600, abs=0.05)
    v = WS.view(d, c)
    assert v["resolved"][warm["id"]]["text"] == "不設目標"
    assert v["watch"]["lines"][0]["kind"] == "暖身" and v["watch"]["lines"][0]["target"] == "不設目標"
    with use_locale("en"):
        v_en = WS.view(d, c)
    assert v_en["watch"]["lines"][0]["kind"] == "Warm-up" and v_en["watch"]["lines"][0]["target"] == "No target"
    # the cool-down still shows its easy HR band
    cool = v["watch"]["lines"][-1]
    assert cool["kind"] == "緩和" and cool["target"].startswith("心率 ")


def test_walking_without_a_walk_cap_warm_up_has_no_target_cool_down_keeps_the_band():
    th = {k: v for k, v in TH_WALK.items() if k != "walk_cap"}
    st = CW.session_steps({**STEEP, "basis": "hr"}, CW.Thresholds.of(th))
    assert [x.kind for x in st] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert st[0].intensity is None and st[2].intensity is not None and st[2].intensity == st[1].intensity


def test_walking_with_a_walk_cap_unchanged_sp474():
    st = CW.session_steps({**STEEP, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert st[0].intensity is None and st[-1].intensity is None and st[1].intensity == ("hr", 124, 147)
    # SP-474's warm-up TSS stays the no-target one (NONE_IF), not the easy band's
    d = WS.normalize(WS.derive(STEEP, TH_WALK))
    r = WS.resolve(d["items"][0], WS.session_ctx(STEEP, TH_WALK))
    assert r.type == "none" and r.frac is None


def test_a_warm_up_target_set_by_hand_stays():
    c = WS.session_ctx(QUALITY, TH)
    own = {"kind": "warm", "dur": {"type": "time", "value": 600},
           "target": {"type": "hr", "mode": "abs", "lo": 110, "hi": 140}}
    assert WS.resolve(own, c).intensity == ("hr", 110, 140)
    zone = {"kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "hr", "mode": "zone", "zone": "aet"}}
    assert WS.resolve(zone, c).type == "hr"


def test_pushed_sessions_with_a_warm_up_are_re_pushed_once(monkeypatch):
    """A session pushed with the old warm-up (the easy HR band) is 「要更新」 once — its payload changed — and
    is replaced on the next push; the push after that sends nothing. A session with no warm-up step (a plain
    輕鬆跑) is untouched."""
    import datetime as dt
    monkeypatch.setattr(CW, "real_today", lambda: dt.date(2026, 9, 30))     # week1's week (as test_coros_workouts)
    db = run(make_db())
    fake = FakeHub()
    p = week1()
    old = CW.easy_hr(CW.Thresholds.of(PUSH_TH))
    monkeypatch.setattr(CW, "WARM_INT", old)            # pushed before SP-479
    _push(db, p, fake)
    assert set(_status(db, p).values()) <= {"pushed", "skipped", "done"}
    monkeypatch.setattr(CW, "WARM_INT", None)           # now
    st = _status(db, p)
    assert st["quality"] == "outdated"                  # has a warm-up → marked for one re-push
    assert st["easy1"] == "pushed" and st["long"] == "pushed"
    res = _push(db, p, fake)
    assert {r["id"]: r["status"] for r in res["sessions"]}["quality"] == "updated"
    q = [e for e in fake.live().values() if e["exercises"][0]["exerciseType"] == CW.EX_WARMUP]
    assert q and all(x["exercises"][0]["intensityType"] == CW.INT_NONE for x in q)
    n = len(fake.calls)
    res = _push(db, p, fake)                            # once only
    assert len(fake.calls) == n
    assert {r["id"]: r.get("changed") for r in res["sessions"] if r["status"] == "pushed"}["quality"] is False
