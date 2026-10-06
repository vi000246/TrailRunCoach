"""
COROS's post-run self-rating (SP-231; docs/research/readiness-signals.md §1.3, §4.1).

Where it is (verified read-only on the production account, 2026-10-06): the activity detail,
`POST {base}/activity/detail/query?labelId=&sportType=` (a GET answers result=1001), field
`data.sportFeelInfo.feelType`, 1–5 (1 = lightest … 5 = hardest; a CP test read 5, the user
confirmed), 0 = not filled. Only activities run from a COROS calendar workout carry it; it is
in neither the FIT file nor the activity list. The same object has `sportNote` and voice-note
fields: never read (the user, 2026-10-06 — the app's own activity note is the note).

Stored raw in workout_files.coros_feel and mapped onto the existing 10-point
workout_files.rpe (the effort tag, engine/activity_tags.effort_from_rpe, and the RPE → TSS
factor, engine/rpe_load.refit, read that column):

  COROS  1 Very Light → 2   2 Light → 4   3 Moderate → 5   4 Hard → 7   5 Max Effort → 10

= the app's own five levels (rpe_load.LEVELS: 輕鬆 2、稍累 4、累 5、很累 7、極限 10). 推估:
COROS publishes no conversion. A FIT that has its own RPE (Garmin files) wins: COROS never
overwrites an RPE it did not write (`rpe_source`).

engine/adapt.py uses the rating (rule D's self-rating trigger, `HARD_RPE`): an easy or long run
rated Hard or more pushes a hard session within 48 h back — 推估 (no controlled trial;
research §2.4).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from backend.i18n import _

# COROS's own words for the five levels (its app shows them; kept as they are in every language)
FEEL_LABEL = {1: "Very Light", 2: "Light", 3: "Moderate", 4: "Hard", 5: "Max Effort"}
TO_RPE = {1: 2.0, 2: 4.0, 3: 5.0, 4: 7.0, 5: 10.0}          # 推估 (module doc)
HARD_FEEL = 4                                              # the user's threshold (2026-10-06): Hard
HARD_RPE = TO_RPE[HARD_FEEL]                               # the same on the 10-point scale (a FIT RPE too)
SOURCE = "coros"


def parse_feel(body: Any) -> Optional[int]:
    """feelType 0–5 from a detail response body ({result, data: {sportFeelInfo: {feelType}}});
    None when the answer is not a success or has no readable value. Reads nothing else."""
    if not isinstance(body, dict) or str(body.get("result")) != "0000":
        return None
    data = body.get("data")
    info = data.get("sportFeelInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return None
    v = info.get("feelType")
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not f.is_integer() or not 0 <= f <= 5:
        return None
    return int(f)


def apply(wf, feel: int) -> bool:
    """Store a read feelType on a workout_files row; the RPE follows unless the FIT has its own
    (FIT wins). True when the row's RPE changed (the plan / calibration need a new run)."""
    old = wf.rpe
    wf.coros_feel = int(feel)
    if wf.rpe is not None and wf.rpe_source != SOURCE:
        return False                                   # the FIT's own RPE (Garmin): kept
    new = TO_RPE.get(int(feel))
    wf.rpe = new
    wf.rpe_source = SOURCE if new is not None else None
    return new != old


def level_of(rpe: Optional[float]) -> Optional[int]:
    """A 10-point RPE on COROS's five levels (the nearest mapped value), for a FIT RPE."""
    if rpe is None:
        return None
    return min(TO_RPE, key=lambda k: (abs(TO_RPE[k] - rpe), -k))


def self_rating(rec: Optional[dict]) -> Optional[dict]:
    """{level, label, source, rpe, hard, text} of a recorded row (activity_tags.load_recorded:
    rpe, source, coros_feel); None without an RPE. text: 「自評：Hard（COROS）」, or
    「自評：RPE 8（手錶）」 for a FIT's own RPE."""
    r = rec or {}
    rpe = r.get("rpe")
    if rpe is None:
        return None
    rpe = float(rpe)
    feel = r.get("coros_feel")
    if r.get("source") == SOURCE and feel in FEEL_LABEL:
        label, src = FEEL_LABEL[feel], SOURCE
        text = _("自評：{label}（COROS）", label=label)
    else:
        label, src = f"RPE {rpe:g}", "watch"
        text = _("自評：RPE {rpe}（手錶）", rpe=f"{rpe:g}")
    return {"level": feel if src == SOURCE else level_of(rpe), "label": label, "source": src,
            "rpe": rpe, "hard": rpe >= HARD_RPE, "text": text}


def stamp(rated: dict) -> str:
    """A short hash of {activity index: rating} (the plan's data stamp, engine/plan_auto.stamp)."""
    key = sorted((str(k), (v or {}).get("rpe"), (v or {}).get("source")) for k, v in (rated or {}).items())
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16] if key else ""

