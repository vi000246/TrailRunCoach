"""
Plan compliance: how well a completed session matched what was planned.

Colours in the style of TrainingPeaks and intervals.icu (docs/research/
competitor-charts.md, recommendation 9):
  - TrainingPeaks athlete guide, "Compliance colors": green within ±20 % of the
    planned duration / TSS, yellow 20–50 % off, orange / red further off, red
    for a missed workout.
    https://www.trainingpeaks.com/learn/trainingpeaks-athlete-user-guide/
  - intervals.icu "Compliance" field: actual load × 100 / planned load, time
    when there is no load. https://forum.intervals.icu/t/compliance-activity-field/47805
Here: the worse of the duration and TSS deviations decides the colour (green
≤ 20 % off, yellow ≤ 50 %, red beyond, or the wrong kind of activity); the
headline % is TSS when both sides have it, else duration (intervals.icu).
"""
from __future__ import annotations

from typing import Optional

# |actual / planned − 1| upper bounds per level (TrainingPeaks ±20 % / 50 %)
COMPLIANCE = {"green": 0.20, "yellow": 0.50}
LEVEL_LABEL = {"green": "符合計畫", "yellow": "有點偏離", "red": "偏離計畫", "missed": "未完成"}

RUN = {"road", "trail"}
# activity categories (engine/overview.CATEGORIES) that count as the planned kind
KIND_OK = {"easy": RUN, "long": RUN | {"hike"}, "quality": RUN, "test": RUN,
           "hike": {"hike", "trail", "walk"}, "strength": {"strength"}}


def _ratio(actual: Optional[float], planned: Optional[float]) -> Optional[float]:
    if actual is None or not planned or planned <= 0:
        return None
    return float(actual) / float(planned)


def level_of(dev: float) -> str:
    if dev <= COMPLIANCE["green"]:
        return "green"
    if dev <= COMPLIANCE["yellow"]:
        return "yellow"
    return "red"


def session_compliance(s: dict, planned_tss: Optional[float] = None) -> Optional[dict]:
    """{level, pct, duration_pct, tss_pct, wrong_type, label} for a done or
    missed stored session (plan_store dict), None for anything else."""
    if s.get("state") == "missed":
        return {"level": "missed", "pct": 0, "duration_pct": None, "tss_pct": None,
                "wrong_type": False, "label": LEVEL_LABEL["missed"]}
    a = s.get("done_by") if s.get("state") == "done" else None
    if not isinstance(a, dict):
        return None
    dur = _ratio(a.get("moving_s"), (s.get("minutes") or 0) * 60.0)
    tss = _ratio(a.get("tss"), planned_tss if planned_tss is not None else s.get("tss"))
    cat = a.get("category")
    wrong = bool(cat) and cat not in KIND_OK.get(s.get("kind"), {cat})
    devs = [abs(r - 1.0) for r in (dur, tss) if r is not None]
    level = "red" if wrong else (level_of(max(devs)) if devs else "green")
    head = tss if tss is not None else dur
    return {"level": level, "pct": None if head is None else round(head * 100),
            "duration_pct": None if dur is None else round(dur * 100),
            "tss_pct": None if tss is None else round(tss * 100),
            "wrong_type": wrong, "label": "類型不符" if wrong else LEVEL_LABEL[level]}


def week_compliance(planned_tss: float, done_tss: float, planned_hours: float,
                    done_hours: float) -> Optional[dict]:
    """Week 完成度 over the days so far: TSS when planned, else time."""
    r = _ratio(done_tss, planned_tss) if planned_tss > 0 else _ratio(done_hours, planned_hours)
    if r is None:
        return None
    return {"pct": round(r * 100), "level": level_of(abs(r - 1.0))}
