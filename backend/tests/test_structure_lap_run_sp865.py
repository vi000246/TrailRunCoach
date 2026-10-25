"""
SP-865: the structure summary (workout_steps.structure_text — the editor's 「結構」 line, the calendar feed)
folds consecutive lap-button steps (「直到按下計圈」, work / rest / other) into 「N × 直到按下計圈」: a session
made from a route (SP-641, a step per climb) and a race export (racepower, watch_export lap mode) are all
of them, and the line listed 16 of them one by one. English the same. Other steps unchanged. Synthetic.
"""
from backend import i18n
from backend.engine import workout_steps as WS
from backend.engine.racepower import watch_export as WE
from backend.tests.test_effort_pace_sp632 import _row
from backend.tests.test_route_session_sp641 import build

LAP = "直到按下計圈"


def _st(kind, dur):
    return {"kind": kind, "dur": dur, "target": {"type": "auto"}, "note": ""}


def _open(kind="work"):
    return _st(kind, {"type": "open"})


def _t(kind, sec):
    return _st(kind, {"type": "time", "value": sec})


def test_sixteen_lap_steps_are_one_part():
    assert WS.structure_text({"items": [_open() for _ in range(16)]}) == f"16 × {LAP}"


def test_climbs_and_descents_fold_together_warm_up_and_cool_down_stay():
    items = [_t("warm", 600)] + [_open("work" if i % 2 == 0 else "other") for i in range(5)] + [_t("cool", 300)]
    txt = WS.structure_text({"items": items})
    parts = txt.split(" · ")
    assert len(parts) == 3 and parts[1] == f"5 × {LAP}" and parts[0].startswith("暖身") and parts[2].startswith("緩和")


def test_one_lap_step_and_timed_steps_unchanged():
    assert WS.structure_text({"items": [_t("warm", 600), _open(), _t("cool", 300)]}).split(" · ")[1] == LAP
    # equal timed steps are not folded (only the lap-button ones)
    assert WS.structure_text({"items": [_t("work", 600), _t("work", 600)]}).count(" · ") == 1
    # lap runs split by a timed step: two runs
    txt = WS.structure_text({"items": [_open(), _open(), _t("rest", 120), _open(), _open(), _open()]})
    assert txt.startswith(f"2 × {LAP} · ") and txt.endswith(f" · 3 × {LAP}")


def test_inside_a_repeat_too():
    txt = WS.structure_text({"items": [{"kind": "repeat", "times": 4, "items": [_open(), _open(), _t("rest", 60)]}]})
    assert txt.startswith(f"4×(2 × {LAP}＋")


def test_english():
    with i18n.use_locale("en"):
        assert WS.structure_text({"items": [_open() for _ in range(16)]}) == "16 × Until lap button"


def test_a_route_session_says_n_times_lap():
    out = build()                                              # the 貓空 GPX, a step per climb
    n = len(out["steps"]["items"])
    assert n >= 3 and all(x["dur"]["type"] == "open" for x in out["steps"]["items"])
    assert WS.structure_text(out["steps"]) == f"{n} × {LAP}"


def test_a_race_export_says_n_times_lap():
    rows = [_row(i, g) for i, g in enumerate([0.05, 0.12, -0.10, 0.0, 0.06, -0.08, 0.02, 0.10])]
    doc = WE.steps_for({"type": "trail"}, rows, mode="lap")["doc"]
    items = WS.normalize(doc)["items"]
    n = len(items)
    assert n >= 2 and all(x["dur"]["type"] == "open" for x in items)
    assert WS.structure_text({"items": items}) == f"{n} × {LAP}"
