"""
One-off, idempotent seed of the user's activity tags (activity_tags table,
engine/activity_tags.py) — the corrections the user gave on 2026-10-01:

* three runs the race-power back-test wrongly took as maximal:
    2025-10-18 road 5 km     → 練跑 / 一般            (a weekday training run)
    2025-11-02 trail 14.4 km → 爬山 / 一般            (an ordinary mountain trip)
    2026-07-27 trail 11.7 km → 爬山 / 有拼但有休息    (hard, near-max, long rests)
* the trail races from their diary → 比賽, effort left AUTO (they often race
  by feel, so the effort comes from the HR rule, not from "race").

Activities are found in the dataset (default: the charts.data_source
setting, e.g. coros) by date + distance, or for the races by the WKO5 file
name — on a COROS / TP source by the start time that name encodes (±3 min,
activity_tags.MATCH_TOL_MIN). Tags go to the app DB's activity_tags table
(the same DB activity_tags.load() and the back-test read; the table is
created on the first write), keyed by the local start minute, so a tag
applies to the same activity in every source. Dry run by default: it prints
what it would change; --apply writes.

    python -m backend.scripts.seed_activity_tags [--source coros|tp|wko5] [--db PATH] [--apply]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from typing import Optional

from backend.engine import activity_tags as AT

SEED = [
    {"date": "2025-10-18", "km": 5.0, "trail": False, "activity_type": "training", "effort": "moderate",
     "why": "平日練跑，不是全力"},
    {"date": "2025-11-02", "km": 14.4, "trail": True, "activity_type": "hike", "effort": "moderate",
     "why": "一般爬山"},
    {"date": "2026-07-27", "km": 11.7, "trail": True, "activity_type": "hike", "effort": "hard_with_rests",
     "why": "中級山，有拼但休息很久"},
] + [{"date": d, "file": f, "activity_type": "race", "why": "日記中的越野賽（努力度保留自動）"} for d, f in (
    ("2026-04-11", "Athlete_2026_04_11_07_24.wko4"),
    ("2025-09-06", "Athlete_2025_09_06_07_58.wko4"),
    ("2025-07-26", "Athlete_2025_07_26_07_30.wko4"),
    ("2024-09-21", "Athlete_2024_09_21_05_55.wko4"),
    ("2024-06-02", "Athlete_2024_06_02_07_46.wko4"),
    ("2024-04-13", "Athlete_2024_04_13_08_46.wko4"),
    ("2024-01-06", "Athlete_2024_01_06_08_45.wko4"),
)]
KM_TOL = 0.10        # 自組: the watch distance within ±10 % of the stated one


def _trail(w) -> bool:
    return "runningtrail" in w.tags or w.sport_type == "trail running"


def start_of_file(name: str) -> Optional[dt.datetime]:
    """The local start a WKO5 file name encodes: Athlete_YYYY_MM_DD_HH_MM.wko4."""
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


def plan(workouts, rows: list[dict], source: str = "wko5") -> list[dict]:
    """What the seed would do: per row the matched workout, the current
    stored tag and the change (empty when already applied: idempotent)."""
    out = []
    for spec in SEED:
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
    ap.add_argument("--source", choices=("wko5", "coros", "tp"),
                    help="dataset to match against (default: the charts.data_source setting)")
    ap.add_argument("--db", help="the DB to write (default: the app DB ~/.wko5coach/wko5coach.db)")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    from backend.api.wko5views import _dataset
    from backend.engine.wko5expr.datasource import current_source
    a.source = a.source or current_source()
    db = a.db or str(AT._db_path())
    ds = _dataset(source=a.source)
    items = plan(ds.workouts, AT.load(db), a.source)
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
