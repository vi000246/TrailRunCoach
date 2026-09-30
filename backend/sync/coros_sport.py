"""
The sport word in COROS sync file names, `<labelId>_<YYYY-MM-DD>_<sport>.fit`.

The FIT file itself says what the activity was (session `sport` +
`sub_sport`), so that wins; COROS's own `sportType` code is only the fallback
for a FIT that can't be read (a corrupt stub) or says nothing useful.

Before this, the name came from a code map that had 100 = "cycling": every
COROS run was saved as `*_cycling.fit` (17/17 code-100 files in the user's
folder have session sport `running`). The DB `sport` column was never wrong —
file_service reads it from the FIT — only the file name (and the SSE
`"sport"` of the sync) were. migrate_coros_sport_names.py renames the old files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# COROS sportType -> file-name word. 100 is VERIFIED against the user's FIT
# files (session sport running); the others follow the COROS web app's codes
# as used by the community COROS API clients and are not verified here.
COROS_SPORT_TYPES: dict[int, str] = {
    100: "run",
    101: "indoor_run",
    102: "trail_run",
    103: "track_run",
    104: "hike",
    105: "mountaineering",
    200: "cycling",
    201: "indoor_cycling",
    300: "swim",
    301: "open_water_swim",
    400: "training",
    402: "strength",
    900: "walk",
    10000: "triathlon",
}

_RUN_SUB = {"trail": "trail_run", "treadmill": "treadmill", "track": "track_run",
            "indoor_running": "indoor_run", "virtual_activity": "indoor_run"}
_BIKE_SUB = {"indoor_cycling": "indoor_cycling", "virtual_activity": "indoor_cycling",
             "spin": "indoor_cycling"}
_SPORT = {"running": "run", "cycling": "cycling", "swimming": "swim", "hiking": "hike",
          "walking": "walk", "mountaineering": "mountaineering", "training": "training",
          "transition": "transition", "multisport": "triathlon"}
_UNKNOWN = {"", "none", "generic", "unknown", "all", "invalid"}
NAME_RE = re.compile(r"^(?P<label>\d+)_(?P<date>\d{4}-\d{2}-\d{2}|unknown)_(?P<sport>[a-z0-9_]+)\.fit$")


def _word(v) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(v or "").strip().lower()).strip("_")


def sport_token(fit_sport=None, fit_sub_sport=None, coros_code: Optional[int] = None) -> str:
    """File-name word: from the FIT session sport / sub_sport, else the COROS
    code, else "other"."""
    sp, sub = _word(fit_sport), _word(fit_sub_sport)
    if sp and sp not in _UNKNOWN and not sp.isdigit():
        if sp == "running":
            return _RUN_SUB.get(sub, "run")
        if sp == "cycling":
            return _BIKE_SUB.get(sub, "cycling")
        if sp == "swimming":
            return "open_water_swim" if sub == "open_water" else "swim"
        if sp == "training":
            return "strength" if sub == "strength_training" else "training"
        return _SPORT.get(sp, sp)
    try:
        code = int(coros_code) if coros_code is not None else None
    except (TypeError, ValueError):
        code = None
    return COROS_SPORT_TYPES.get(code, "other")


def fit_session_sport(path: Path | str) -> tuple[Optional[str], Optional[str]]:
    """(sport, sub_sport) of the FIT session as FIT names ("running", "trail");
    (None, None) if the file can't be read."""
    from backend.files.fit_reader import parse_fit
    try:
        raw = parse_fit(str(path))
    except Exception:
        return None, None
    s = raw.session or {}
    sport = s.get("sport")
    return (str(sport) if sport is not None else (raw.sport if raw.sport != "unknown" else None),
            str(s["sub_sport"]) if s.get("sub_sport") is not None else None)


def file_name(label_id: str, date_str: str, token: str) -> str:
    return f"{label_id}_{date_str}_{token}.fit"


def renamed(path: Path, coros_code: Optional[int] = None) -> Optional[Path]:
    """Where a COROS sync file should live given its FIT sport, or None when
    the name is not a sync name or already right."""
    m = NAME_RE.match(path.name)
    if not m:
        return None
    sport, sub = fit_session_sport(path)
    if sport is None and coros_code is None:
        return None                       # unreadable and no code: leave it
    token = sport_token(sport, sub, coros_code)
    new = path.with_name(file_name(m["label"], m["date"], token))
    return None if new.name == path.name else new
