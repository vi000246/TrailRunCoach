"""
One-off, idempotent seed of activity tags (activity_tags table,
engine/activity_tags.py) from a JSON file of corrections you write yourself,
e.g. runs a back-test wrongly took as maximal, or your races. The app ships
no rows; a (fictional) example file:

    [{"date": "2025-08-09", "km": 5.0, "trail": false,
      "activity_type": "training", "effort": "moderate", "why": "weekday run"},
     {"date": "2025-06-14", "file": "Example_2025_06_14_07_30.wko4",
      "activity_type": "race", "why": "trail race (effort left auto)"}]

A row with `file` (a WKO5 file name) is found by that name, or on a COROS /
TP source by the start time the name encodes (<anything>_YYYY_MM_DD_HH_MM.wko4,
±3 min, activity_tags.MATCH_TOL_MIN); a row with `km` by date + distance
(±10 %) and terrain. Leave `effort` out to keep it AUTO (the HR rule).

Activities are looked up in the dataset (default: the charts.data_source
setting). Tags go to the app DB's activity_tags table (the same DB
activity_tags.load() and the back-test read; the table is created on the
first write), keyed by the local start minute, so a tag applies to the same
activity in every source. Dry run by default: it prints what it would
change; --apply writes.

    python -m backend.scripts.seed_activity_tags [--seed my_tags.json] [--source coros|tp|wko5] [--db PATH] [--apply]

The seed file is --seed, else $WKO5COACH_TAG_SEED, else
~/.wko5coach/activity_tag_seed.json (DEFAULT_SEED).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from backend.engine import activity_tags as AT

SEED_ENV = "WKO5COACH_TAG_SEED"
DEFAULT_SEED = Path.home() / ".wko5coach" / "activity_tag_seed.json"

# the shape of a seed file (fictional rows, see the module docstring); the tests use it,
# nothing reads it by default
EXAMPLE_SEED = [
    {"date": "2025-08-09", "km": 5.0, "trail": False, "activity_type": "training", "effort": "moderate",
     "why": "平日練跑，不是全力"},
    {"date": "2025-04-12", "km": 14.4, "trail": True, "activity_type": "hike", "effort": "moderate",
     "why": "一般爬山"},
    {"date": "2025-05-17", "km": 11.7, "trail": True, "activity_type": "hike", "effort": "hard_with_rests",
     "why": "有拼但休息很久"},
] + [{"date": d, "file": f, "activity_type": "race", "why": "越野賽（努力度保留自動）"} for d, f in (
    ("2025-06-14", "Example_2025_06_14_07_30.wko4"),
    ("2024-05-11", "Example_2024_05_11_05_55.wko4"),
)]


def seed_path(arg: Optional[str] = None) -> Path:
    """The seed file: the --seed argument, else $WKO5COACH_TAG_SEED, else DEFAULT_SEED."""
    return Path(arg or os.environ.get(SEED_ENV) or DEFAULT_SEED)


def load_seed(path) -> list[dict]:
    """A seed file: a JSON list of rows, each with `date`, `activity_type` and
    either `file` or `km` (see the module docstring)."""
    rows = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(rows, list) or not all(
            isinstance(r, dict) and r.get("date") and r.get("activity_type") and (r.get("file") or r.get("km"))
            for r in rows):
        raise ValueError(f"{path}: expected a list of {{date, activity_type, file | km}} rows")
    return rows


KM_TOL = 0.10        # 推估: the watch distance within ±10 % of the stated one


def _trail(w) -> bool:
    return "runningtrail" in w.tags or w.sport_type == "trail running"


def start_of_file(name: str) -> Optional[dt.datetime]:
    """The local start a WKO5 file name encodes: <athlete>_YYYY_MM_DD_HH_MM.wko4."""
    m = re.search(r"(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.wko4$", name or "")
    if not m:
        return None
    try:
        return dt.datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None


def match(spec: dict, workouts) -> tuple[Optional[object], str]:
    """The dataset workout a seed row means: the file name (ending) on that
    date; on a COROS / TP source (no .wko4 names) the activity starting
    within ±activity_tags.MATCH_TOL_MIN of the start the WKO5 file name
    encodes (the same rule activity_tags.find uses across sources); else the
    run that date whose distance is within ±10 % (and the right terrain),
    the nearest if several."""
    day = [w for w in workouts if w.entry.start.date().isoformat() == spec["date"]]
    if spec.get("file"):
        hit = [w for w in day if str(w.entry.file).replace("\\", "/").endswith(spec["file"])]
        if hit:
            return hit[0], "file"
        st = start_of_file(spec["file"])
        near = [(abs((w.entry.start.replace(second=0, microsecond=0) - st).total_seconds()) / 60.0, w)
                for w in day if st is not None]
        near = [x for x in near if x[0] <= AT.MATCH_TOL_MIN]
        if near:
            return min(near, key=lambda x: x[0])[1], "start"
        return None, "找不到這個檔名或起始時間"
    cand = [w for w in day if w.sport == "run" and w.metrics.get("distance")
            and abs(w.metrics["distance"] / spec["km"] - 1.0) <= KM_TOL
            and ("trail" not in spec or _trail(w) == spec["trail"])]
    if not cand:
        return None, "這天沒有距離相符的跑步"
    w = min(cand, key=lambda w: abs(w.metrics["distance"] - spec["km"]))
    return w, "date+distance"


def plan(workouts, rows: list[dict], source: str = "wko5", seed: Optional[list[dict]] = None) -> list[dict]:
    """What the seed would do: per row the matched workout, the current
    stored tag and the change (empty when already applied: idempotent)."""
    out = []
    for spec in seed if seed is not None else []:
        w, how = match(spec, workouts)
        item = {"spec": spec, "found": w is not None, "how": how}
        if w is None:
            out.append(item)
            continue
        cur = AT.find(rows, w.entry.start, w.entry.file) or {}
        want = {"activity_type": spec["activity_type"]}
        if spec.get("effort"):
            want["effort"] = spec["effort"]
        change = {}
        if AT.user_type(cur) != want["activity_type"]:
            change["activity_type"] = (AT.user_type(cur), want["activity_type"])
        if "effort" in want and AT.user_effort(cur) != want["effort"]:
            change["effort"] = (AT.user_effort(cur), want["effort"])
        km = w.metrics.get("distance")
        item.update(start_local=cur.get("start_local") or AT.key_of(w.entry.start), file=w.entry.file,
                    source=source, km=km, label=f"{w.entry.start:%Y-%m-%d} {w.sport_type} {km or 0:.1f} km",
                    want=want, change=change)
        out.append(item)
    return out


def apply(db_path, items: list[dict]) -> int:
    n = 0
    for it in items:
        if not it.get("found") or not it.get("change"):
            continue
        AT.upsert(db_path, start_local=it["start_local"], source=it["source"], file=it["file"],
                  distance_km=it["km"], label=it["label"], **it["want"])
        n += 1
    return n


def _fmt(item: dict) -> str:
    s = item["spec"]
    if not item["found"]:
        return f"  ?? {s['date']} {s.get('file') or str(s.get('km')) + ' km'}: {item['how']}"
    if not item["change"]:
        return f"  == {item['label']} ({item['file']}): 已是 {item['want']}"
    ch = "、".join(f"{k} {AT.TYPES.get(a) or AT.EFFORTS.get(a) or '自動'} → {AT.TYPES.get(b) or AT.EFFORTS.get(b)}"
                  for k, (a, b) in item["change"].items())
    return f"  -> {item['label']} ({item['file']}, {item['how']}): {ch}（{s['why']}）"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", help=f"JSON file of the tags to set (see the module docstring; "
                                   f"default: ${SEED_ENV}, else {DEFAULT_SEED})")
    ap.add_argument("--source", choices=("wko5", "coros", "tp"),
                    help="dataset to match against (default: the charts.data_source setting)")
    ap.add_argument("--db", help="the DB to write (default: the app DB ~/.wko5coach/wko5coach.db)")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    seed = seed_path(a.seed)
    if not seed.is_file():
        print(f"no seed file: {seed} (pass --seed or set ${SEED_ENV})", file=sys.stderr)
        return 2
    from backend.api.wko5views import _dataset
    from backend.engine.wko5expr.datasource import current_source
    a.source = a.source or current_source()
    db = a.db or str(AT._db_path())
    ds = _dataset(source=a.source)
    items = plan(ds.workouts, AT.load(db), a.source, load_seed(seed))
    print(f"source {a.source}, DB {db}")
    for it in items:
        print(_fmt(it))
    if a.apply:
        print(f"wrote {apply(db, items)} tag(s)")
    else:
        print("dry run — add --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
