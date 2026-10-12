"""SP-378 (docs/research/power-zone-width.md; the user's answers 2026-10-12): the road easy run's
power target is the whole of Zone 1 (65–80 % CP), the road long run 65–88 %; an interval keeps its
prescription, but the watch gets the band the app judges the rep by (upper × 1.05, Zone 5 open to
116 %); a user's own power target is pushed as typed."""
import pytest

from backend.engine import interval_eval as IE
from backend.engine import interval_library as IL
from backend.engine import workout_steps as WS
from backend.engine import zones as Z
from backend.sync import coros_workouts as CW

TH = {"cp": 200.0, "lthr": 160.0, "aet": 142.0}


def _row(tid):
    return next(r for r in Z.WORKOUT_TARGETS if r[0] == tid)


def test_easy_and_long_bands():
    assert _row("z2")[2:4] == (0.65, 0.80) == Z.EASY_BAND
    assert _row("long")[2:4] == (0.65, 0.88) == Z.LONG_BAND
    assert _row("trail")[2:4] == (0.75, 0.88) == Z.TRAIL_BAND           # mountain days: unchanged
    assert _row("recovery")[2:4] == (None, 0.75)                        # unchanged
    assert "75–80% CP" in _row("z2")[7] and "75–88% CP" in _row("long")[7]


@pytest.mark.parametrize("lo, hi, want", [
    (0.95, 1.01, (0.95, 1.0605)),     # threshold: × 1.05
    (0.90, 0.95, (0.90, 0.9975)),
    (1.01, 1.06, (1.01, 1.113)),      # supra (middle 1.035 < 1.06): × 1.05
    (1.06, 1.12, (1.06, 1.16)),       # Zone 5: up to its top
    (1.04, 1.08, (1.04, 1.16)),       # middle 1.06: Zone 5
    (1.10, 1.20, (1.10, 1.20)),       # 30/15 above Zone 5's top keeps its own
])
def test_watch_band(lo, hi, want):
    got = Z.watch_band(lo, hi)
    assert got[0] == want[0] and got[1] == pytest.approx(want[1])
    assert IE.TIZ_HI_TOL == Z.WATCH_HI_TOL == 1.05                       # judged by the same band


def test_road_easy_and_long_push_the_wide_band():
    th = CW.Thresholds.of(TH)
    easy = {"kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": "2026-10-14", "basis": "power"}
    assert CW.session_steps(easy, th)[0].intensity == ("power", 130, 160)
    long = {"kind": "long", "title": "長跑", "minutes": 100, "day": "2026-10-14", "basis": "power"}
    assert CW.session_steps(long, th)[0].intensity == ("power", 130, 176)
    # the derived structure says the same
    d = WS.derive(easy, TH)
    work = [st for st in d["items"] if st["kind"] == "work"][0]
    assert (work["target"]["plo"], work["target"]["phi"]) == (0.65, 0.80)


def test_interval_step_shows_the_prescription_and_pushes_the_watch_band():
    v = IL.session_for(IL.fit("z3b", None), {"cp": 200.0, "lthr": 160.0})
    s = {**v, "day": "2026-10-14", "basis": "power"}
    d = WS.derive(s, TH)
    c = WS.Ctx.of(TH, "power")
    reps = [st for blk in d["items"] for st in (blk.get("items") or [blk]) if st["kind"] == "work"]
    r = WS.resolve(reps[0], c)
    lo, hi = reps[0]["target"]["lo"], reps[0]["target"]["hi"]
    assert r.text == f"{round(lo * 200)}–{round(hi * 200)} W"                         # the prescription
    assert r.intensity == ("power", round(lo * 200), round(hi * 1.05 * 200))           # the watch
    assert "手錶上限" in r.sub


def test_a_users_own_power_target_is_pushed_as_typed():
    c = WS.Ctx.of(TH, "power")
    for tg in ({"type": "power", "mode": "pct", "lo": 0.95, "hi": 1.0},
               {"type": "power", "mode": "abs", "lo": 190, "hi": 200}):
        r = WS.resolve({"id": "a1", "kind": "work", "dur": {"type": "time", "value": 300}, "target": tg}, c)
        assert r.intensity == ("power", 190, 200) and "手錶上限" not in r.sub


def test_the_editor_starts_an_easy_power_override_at_zone_1():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "workout_editor.js").read_text("utf-8")
    assert 'return { type, mode: "pct", lo: 0.65, hi: 0.8 };' in js and 'zone: st.kind === "work" ? "3A" : "1C"' not in js
