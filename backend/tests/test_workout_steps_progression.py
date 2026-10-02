"""
Progression reads the structure the user edited (quality_gate.steps_spec /
planned_variant_spec), not the title: an equivalent edit moves the ladder, a
non-equivalent one is judged but doesn't.
"""
import copy

from backend.engine import quality_gate as QG
from backend.engine import workout_steps as WS


def _user(key):
    d = WS.normalize(WS.template_steps(key))
    d["origin"] = "user"
    return d


def _row(steps, n, lo=0.92, cp=250.0, rung="z3a", title="自己改的"):
    return {"title": title, "rung_key": rung, "steps": steps, "cp": cp,
            "bouts": [{"power": lo * cp} for _ in range(n)], "date": "2026-10-01"}


def test_dose_spec_starts_at_z3a():
    assert QG.dose_spec(0, True)[0] == "z3a"


def test_equivalent_user_structure_moves_the_ladder():
    st = _user("t1b")                    # 6×3′ at 92–97 %: an equivalent of T1 (3×6′)
    h = [_row(st, 6, lo=0.93)]
    out = QG.dose_step(h)
    assert out["step"] == 1 and h[0]["outcome"] == "met" and h[0]["steps_equiv"] is True


def test_non_equivalent_user_structure_is_judged_but_does_not_move():
    st = _user("t1a")
    for it in st["items"]:
        if it["kind"] == "repeat" and it["times"] == 3:
            it["times"] = 2              # 2×6′: 67 % of the time in zone
    h = [_row(st, 2)]
    out = QG.dose_step(h)
    assert out["step"] == 0 and h[0].get("counted") is False and h[0]["steps_equiv"] is False


def test_title_alone_no_longer_decides_once_steps_are_edited():
    # the title says 3×6 分 (T1) but the edited structure is 2×6′: not counted
    st = _user("t1a")
    st2 = copy.deepcopy(st)
    for it in st2["items"]:
        if it["kind"] == "repeat" and it["times"] == 3:
            it["times"] = 2
    h = [_row(st2, 2, title="閾值 3×6 分")]
    assert QG.dose_step(h)["step"] == 0
    # untouched (origin template:…): the variant / title path, as before
    tpl = WS.normalize(WS.template_steps("t1a"))
    assert QG.steps_spec({"steps": tpl}, 0) is None


def test_planned_spec_for_rep_matching_uses_the_structure():
    st = _user("v2b")
    v = QG.planned_variant_spec({"steps": st, "rung_key": "z5b", "variant_key": "v2a"})
    assert v.key == "user" and v.n == 6 and v.work_s == 120


def test_hr_only_structure_is_estimated():
    st = WS.normalize({"origin": "user", "items": [{"kind": "repeat", "times": 3, "items": [
        {"kind": "work", "dur": {"type": "time", "value": 360}, "target": {"type": "hr", "mode": "pct", "lo": 0.9, "hi": 0.95}},
        {"kind": "rest", "dur": {"type": "time", "value": 90}, "target": {"type": "none"}}]}]})
    h = [_row(st, 3, lo=0.90)]
    QG.dose_step(h)
    assert h[0]["steps_estimated"] is True
