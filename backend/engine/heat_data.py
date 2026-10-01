"""
The athlete's heat-acclimation state from their own history (the I/O side of
engine/heat.py): per-activity heat exposure from route_weather's
activity_weather.json (Open-Meteo archive at each activity's point, filled by
the routes build), completed passive heat sessions, and the projection to a
race day. docs/research/heat-acclimation.md §2.4, §4.3, §5.3.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from statistics import median
from typing import Iterable, Optional

from backend.engine import heat as HT

FROM_WINDOW_DAYS = 90            # the training-conditions window of racepower (athlete.CP_WINDOW_DAYS)
HOT_MONTH_WINDOW_D = 15


def _root() -> Path:
    from backend.engine import routes as R
    return R.HOME


def exposures(root: Optional[Path] = None) -> tuple[list[dict], dict]:
    from backend.engine import route_weather as RW
    doc = RW.load_activity_weather(root or _root())
    acts = [dict(v, file=f) for f, v in (doc.get("activities") or {}).items() if v]
    return acts, {"at": doc.get("at"), "stats": doc.get("stats"), "attribution": doc.get("attribution"),
                  "missing": not doc}


MORNING_H = (5, 6, 7)            # local hours 05:00–07:59: when a dawn test would run [自組]


def morning_weather(dates: Iterable, root: Optional[Path] = None) -> dict:
    """{date iso: {"temp_c", "hadley", "min_c", "cell", "src"}} — each day's
    dawn weather at the athlete's home, independent of when they ran: the
    Open-Meteo archive day that route_weather already cached (one file per
    (0.25° cell, day), the full 24 hours of every point asked for that day).
    Home = the cell with the most cached days; on a day, its lowest point
    (the town, not a hill climbed the same day). morning = mean of the
    05–07 h rows (T, and Hadley from T + Magnus dew point), min_c = the day's
    lowest hourly T. Days without a home-cell file are left out (a trip, or
    the archive not fetched yet). Local disk only — no network."""
    from backend.engine import route_weather as RW
    dates = {str(x)[:10] for x in dates}
    if not dates:
        return {}
    wdir = Path(root or _root()) / "weather"
    try:
        files = [p for p in wdir.glob("*.json")]
    except OSError:
        return {}
    by_cell: dict = {}
    for p in files:
        parts = p.stem.split("_")
        if len(parts) == 3:
            by_cell.setdefault((parts[0], parts[1]), {})[parts[2]] = p
    if not by_cell:
        return {}
    home = max(by_cell, key=lambda c: len(by_cell[c]))
    out = {}
    for d in sorted(dates):
        p = by_cell[home].get(d)
        if p is None:
            continue
        try:
            doc = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        pts = [js for js in (doc.get("points") or {}).values() if isinstance(js, dict)]
        if not pts:
            continue
        js = min(pts, key=lambda j: j.get("elevation") if j.get("elevation") is not None else 1e9)
        rows = RW._hours([js])
        if not rows:
            continue
        morn = [(T, RH) for when, T, RH in rows if when.hour in MORNING_H]
        if not morn:
            continue
        t = sum(T for T, _ in morn) / len(morn)
        h = sum(HT.hadley_sum(T, RH) for T, RH in morn) / len(morn)
        out[d] = {"temp_c": round(t, 1), "hadley": round(h, 1), "min_c": round(min(T for _, T, _ in rows), 1),
                  "cell": f"{home[0]}_{home[1]}", "src": "open_meteo_morning"}
    return out


def completed_passive_dates(athlete_id: int = 1) -> list[str]:
    """Days of heat_passive sessions (hot bath / sauna) the user ticked done
    in the stored plan — a full heat dose each (heat.day_dose). Read-only
    sqlite, like wko5expr.datasource.read_setting; [] without the DB."""
    import sqlite3
    from backend.engine.wko5expr import datasource
    db = datasource._db_path()
    if db is None or not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT day FROM plan_sessions WHERE athlete_id=? AND kind='heat_passive' "
                               "AND state='done' AND day IS NOT NULL", (athlete_id,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return sorted({r[0] for r in rows if r[0]})


def status(today: Optional[dt.date] = None, race_day: Optional[dt.date] = None,
           planned: Optional[dict] = None, passive_dates: Iterable = (), root: Optional[Path] = None,
           acts: Optional[list] = None) -> dict:
    """Today's S, its series, S_from (mean S over the racepower training
    window) and — with `race_day` — the projected race-day S under the
    three parameter sets (heat.project), counting `planned` heat sessions."""
    today = today or dt.date.today()
    meta = {"missing": False}
    if acts is None:
        acts, meta = exposures(root)
    if not passive_dates:
        try:
            passive_dates = completed_passive_dates()
        except Exception:                   # noqa: BLE001
            passive_dates = ()
    cur = HT.current(acts, today, passive_dates)
    s_from = HT.mean_s(cur["series"], today - dt.timedelta(days=FROM_WINDOW_DAYS), today)
    out = {"today": today.isoformat(), "s": cur["s"], "level": cur["level"], "days_14": cur["days_14"],
           "last_exposure": cur["last_exposure"], "since_last_d": cur["since_last_d"],
           "s_from": s_from if s_from is not None else 0.0,
           "series": [(d.isoformat(), round(s, 4)) for d, s in cur["series"][-120:]],
           "doses": {d.isoformat(): round(v, 3) for d, v in cur["doses"].items() if d >= today - dt.timedelta(days=120)},
           "n_activities": len(acts), "data": meta, "evidence": HT.EVIDENCE, "badge": "推估",
           "a": HT.A_RECOVER, "a_range": list(HT.A_RANGE)}
    if race_day:
        pj = HT.project(cur["s"], today, race_day, planned)
        out["s_race"] = {"center": pj["center"], "low": pj["low"], "high": pj["high"], "date": race_day.isoformat()}
        out["source"] = (f"近 14 天 {cur['days_14']} 天熱暴露" +
                         (f"，最後一次 {cur['since_last_d']} 天前" if cur["since_last_d"] is not None else "") +
                         f"，推算到比賽日 {race_day.month}/{race_day.day}")
    else:
        out["source"] = f"近 14 天 {cur['days_14']} 天熱暴露"
    if meta.get("missing"):
        out["warning"] = "還沒有每筆活動的歷史天氣（路線頁重建一次、含天氣）：S 當作 0"
    return out


HRC_DAYS = 84
HRC_FLAT_G, HRC_MIN_S, HRC_SKIP_S, HRC_CV = 0.03, 600.0, 600.0, 0.10


def steady_segments(ds, today: dt.date, acts: list[dict]) -> list[dict]:
    """heat.hr_cost input (§2.3): per run of the last 84 days, stretches of
    consecutive flat (|g| < 3 %) running windows after the first 10 minutes,
    ≥ 10 min moving with power CV < 10 % [自組]; each with the activity's
    Hadley (activity_weather)."""
    import numpy as np
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    had = {a.get("file"): a.get("hadley") for a in acts}
    tday = date_to_day(today)
    runs = [w for w in ds.workouts if w.sport == "run" and tday - HRC_DAYS < w.day <= tday + 1
            and had.get(w.entry.file) is not None]
    out = []
    for w in runs:
        ws = [x for x in A.grade_samples(ds, [w]) if x.get("k") is not None]
        ws.sort(key=lambda x: x["k"])
        cur: list = []

        def flush():
            if not cur:
                return
            t = sum(100.0 / x["v"] for x in cur)
            ps = [x["p"] for x in cur]
            hrs = [x["hr"] for x in cur if x.get("hr")]
            if t >= HRC_MIN_S and hrs and np.std(ps) / max(1e-9, np.mean(ps)) < HRC_CV:
                out.append({"date": w.entry.start.date().isoformat(), "p": float(np.mean(ps)),
                            "hr": float(np.mean(hrs)), "hadley": had[w.entry.file]})
        for x in ws:
            ok = abs(x["g"]) < HRC_FLAT_G and (x.get("run") is None or x["run"] >= 0.5) and (x.get("t") or 0) >= HRC_SKIP_S \
                and x.get("p")
            if ok and cur and x["k"] == cur[-1]["k"] + 1:
                cur.append(x)
            else:
                flush()
                cur = [x] if ok else []
        flush()
    return out


def month_is_hot(acts: list[dict], day: dt.date) -> Optional[bool]:
    """auto rule for Event.heat (自組): the athlete's outdoor activities within
    ±15 calendar days of that date in earlier years — median Hadley > 150 →
    hot. None without data."""
    hs = []
    for a in acts:
        try:
            d = dt.date.fromisoformat(a["date"])
        except (KeyError, ValueError):
            continue
        if d >= day:
            continue
        dd = abs((d.replace(year=2000) - day.replace(year=2000)).days)
        dd = min(dd, 366 - dd)
        if dd <= HOT_MONTH_WINDOW_D and a.get("hadley") is not None:
            hs.append(a["hadley"])
    if len(hs) < 5:
        return None
    return median(hs) > HT.HOT_HADLEY


def event_is_hot(ev, acts: list[dict]) -> dict:
    """Event.heat: hot / cool as the user set it; auto → 百岳 cool (it is at
    altitude), otherwise month_is_hot on the athlete's own history."""
    h = getattr(ev, "heat", "auto") or "auto"
    if h in ("hot", "cool"):
        return {"hot": h == "hot", "source": "你設定的"}
    if getattr(ev, "kind", "") == "baiyue":
        return {"hot": False, "source": "百岳在高海拔：預設不熱"}
    r = month_is_hot(acts, ev.start)
    if r is None:
        return {"hot": False, "source": "同月份的歷史天氣不足：當作不熱"}
    return {"hot": r, "source": "你往年同一時期戶外活動的 Hadley 中位數" + (" > 150" if r else " ≤ 150")}
