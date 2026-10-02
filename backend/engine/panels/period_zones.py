"""
Time in zone over a period (圖表分析 → 強度; views kind "periodzones").

    「這段時間總共在哪個區間做了多少訓練」: x = zone, y = hours, for a chosen
    period (view "total"), and the same per week / month as stacked bars
    (view "weekly").

Reuse, not a copy:
  * the zone models are activity_charts.MODELS (zones.py's tables + WKO5
    iLevels), minus the ones the user rejected (%HRmax) or the app can't
    compute (RQ %HRR: no resting HR). Default HR Friel, power Palladino.
  * an activity's zone boundaries are activity_charts._bounds on that
    activity: the thresholds in effect on ITS day (workout_review._thresholds
    -> Dataset.sport_setting / cp / aethr, i.e. the plan's dated tests, the
    dataset's dated settings or as-of estimates). A week in March is cut at
    March's CP / LTHR, not today's.
  * the samples are activity_charts.grid (the single-activity cards' 1-s
    grid) and only its moving seconds count (recorded, and > 1.6 km/h when
    there is speed — workout_review's / WKO5's moving rule).

Speed: per activity the moving seconds are stored once as a histogram of
whole bpm / W (Dataset.cached_series, disk-memoised by file stamp, so a year
is read from disk after the first time); the zone seconds are then sums over
the histogram at that day's boundaries. Values are rounded to the nearest
whole bpm / W, so a sample within 0.5 of a boundary may land on its other
side (HR and Stryd power are recorded in whole units).

Power: an activity whose power the app doesn't use (watch-estimated, Dataset
.power_ok) is left out of the power models and counted. Bad / excluded files
are not in ds.workouts at all.

Phase targets (the 3-zone summary low / moderate / high, the zones the
sources define their numbers in): see PHASE_TARGETS.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.engine.panels import activity_charts as A

HIST_KEY = "period_zone_hist_v1"

HR_IDS = ("frielhr", "classichr", "seiler3")
POWER_IDS = ("palladino", "ilevels", "stryd", "palladino3", "coggan")
MODEL_IDS = {"hr": HR_IDS, "power": POWER_IDS}
DEFAULT_MODEL = {"hr": "frielhr", "power": "palladino"}
SUMMARY_MODEL = {"hr": "seiler3", "power": "palladino3"}      # the 3 zones the targets are written in

SPORTS = (("road", "路跑"), ("trail", "越野跑"), ("hike", "登山健行"))
DEFAULT_SPORTS = ("road",)
HIKE_TAGS = {"hiking", "mountaineering"}

PERIODS = (("range", "日期範圍"), ("week", "本週"), ("lastweek", "上週"), ("4w", "近 4 週"),
           ("phase", "本期"), ("custom", "自訂"))
GROUPS = ("auto", "week", "month")
AUTO_WEEK_MAX_DAYS = 182          # a longer range is grouped by month (26 weekly bars is the most that reads)
MIN_VERDICT_S = 3600              # < 1 h of zoned time: no verdict (the PI chart's 1-h floor)

# ---------------------------------------------------------------------------
# targets per phase, on the 3-zone summary (time shares)
# ---------------------------------------------------------------------------
from backend.engine.status import LOW_SHARE_GOOD, LOW_SHARE_WATCH   # noqa: E402  0.75 / 0.65

SRC_FLOOR = ("低強度 ≥ 75%：app 的強度護欄（status.LOW_SHARE_GOOD）。Seiler & Kjerland 2006（Scand J Med Sci Sports "
             "16:49–56）：約 75–80% 的『課』是低強度；Seiler & Tønnessen 2009（Sportscience 13）：同一批選手依時間算 "
             "91% 在第一閾值以下、約 6% 在兩閾值之間、2.6% 在第二閾值以上。依時間算 75% 已經偏寬。< 65% = 太多中強度"
             "（LOW_SHARE_WATCH）")
SRC_UA_BASE = ("基礎期 ≥ 90%：Uphill Athlete「90 percent or more in Zones 1 and 2」（UA 的 1–2 區在 AeT 以下；"
               "教練經驗，非同儕審查）")
SRC_TAPER = "減量期：Bosquet 2007（MSSE 39:1358–65）量減 41–60%、強度維持——高強度不要砍光"
SRC_SPECIFIC = ("專項期：Filipas 2022（Scand J Med Sci Sports 32:498–511）先金字塔、後極化進步最多，也就是後段高強度比例"
                "可以提高；Rosenblat 2025 極化和金字塔沒有差別。沒有專項期的低強度百分比，所以只用 75% 護欄")
TAPER_HIGH_MIN = 0.01             # 推估: below 1 % of the time above LTHR / 95 % CP counts as 「沒有高強度」

PHASE_TARGETS = {
    # kind: (low target, sources, note)
    "base": (0.90, [SRC_UA_BASE, SRC_FLOOR], None),
    "specific": (None, [SRC_SPECIFIC, SRC_FLOOR], None),
    "taper": (None, [SRC_TAPER, SRC_FLOOR], "taper"),
    "transition": (None, [SRC_FLOOR], None),
    "recovery": (None, [SRC_FLOOR], None),
    "event": (None, [SRC_FLOOR], None),
}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def day_to_date(n: float) -> dt.date:
    from backend.engine.wko5expr.dataset import day_to_date as d2d
    return d2d(n)


def date_to_day(d: dt.date) -> float:
    from backend.engine.wko5expr.dataset import date_to_day as d2n
    return d2n(d)


# ---------------------------------------------------------------------------
# which activities
# ---------------------------------------------------------------------------

def sport_of(w) -> Optional[str]:
    """road / trail / hike, or None (not in these charts)."""
    st = (w.sport_type or "").lower()
    tags = set(w.tags or [])
    if w.sport == "run":
        return "trail" if ("runningtrail" in tags or "trail" in st) else "road"
    if w.sport == "walk" and (tags & HIKE_TAGS or st in HIKE_TAGS):
        return "hike"
    return None


def parse_sports(raw: Optional[str]) -> tuple[str, ...]:
    ok = [s for s in (raw or "").split(",") if s in dict(SPORTS)]
    return tuple(s for s, _ in SPORTS if s in ok) or DEFAULT_SPORTS


def _as_run(w):
    """Hikes use the run thresholds (the app keeps HR / CP thresholds for
    running only; the athlete's heart and Stryd are the same on a hike)."""
    if w.sport == "run" or not dataclasses.is_dataclass(w):
        return w
    return dataclasses.replace(w, sport="run")


# ---------------------------------------------------------------------------
# per-activity histogram (disk-memoised)
# ---------------------------------------------------------------------------

def _hist_compute(ds, w) -> dict:
    G = A.grid(ds, w)
    if G is None:
        return {"moving_s": 0.0, "hr": [], "power": []}
    mov = G["moving"]
    out = {"moving_s": float(mov.sum())}
    for k in ("hr", "power"):
        v = G[k]
        if v is None:
            out[k] = []
            continue
        x = v[mov & np.isfinite(v) & (v > 0)]
        u, c = np.unique(np.rint(x).astype(int), return_counts=True)
        out[k] = [[int(a), int(n)] for a, n in zip(u, c)]
    return out


def activity_hist(ds, w) -> dict:
    """{"moving_s", "hr": [[bpm, s]], "power": [[W, s]]} of the moving
    seconds (1-s grid). Disk-memoised where the dataset can (cached_series),
    else per process."""
    key = ("period_zone_hist", w.entry.file)
    hit = ds.memo.get(key)
    if hit is not None:
        return hit
    v = None
    if hasattr(ds, "cached_series"):
        v = ds.cached_series(HIST_KEY, w, lambda: _hist_compute(ds, w))
    if v is None:
        v = _hist_compute(ds, w)
    ds.memo[key] = v
    return v


def _secs(h: list, rows: list[tuple]) -> list[float]:
    """Seconds of histogram h ([[value, s]]) per zone row (id, name, lo, hi), zone = [lo, hi)."""
    if not h:
        return [0.0] * len(rows)
    a = np.asarray(h, dtype=float)
    v, s = a[:, 0], a[:, 1]
    out = []
    for _, _, lo, hi in rows:
        m = v >= (lo or 0.0)
        if hi is not None:
            m &= v < hi
        out.append(float(s[m].sum()))
    return out


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

def models(kind: str) -> list[dict]:
    by_id = {m["id"]: m for m in A.MODELS[kind]}
    return [by_id[i] for i in MODEL_IDS[kind]]


def model(kind: str, mid: Optional[str]) -> dict:
    ms = {m["id"]: m for m in models(kind)}
    return ms.get(mid) or ms[DEFAULT_MODEL[kind]]


def _pct_text(m: dict) -> list[Optional[str]]:
    """「85–90% LTHR」 per zone of a fraction-based model; None otherwise."""
    if "zones" not in m or m["basis"] not in ("lthr", "cp"):
        if m["id"] == "seiler3":
            return ["< AeT", "AeT–LTHR", "≥ LTHR"]
        return [None] * len(A.ILEVEL_NAMES)
    b = "LTHR" if m["basis"] == "lthr" else "CP"
    out = []
    zs = m["zones"]()
    for i, (_, _, lo, hi) in enumerate(zs):
        lo = 0.0 if i == 0 else lo
        p = lambda x: f"{x * 100:.0f}"
        out.append(f"< {p(hi)}% {b}" if not lo else f"≥ {p(lo)}% {b}" if hi is None else f"{p(lo)}–{p(hi)}% {b}")
    return out


# ---------------------------------------------------------------------------
# periods and phases
# ---------------------------------------------------------------------------

def _plan(ds):
    return getattr(ds, "plan", None)


def phase_on(ds, day: dt.date) -> Optional[dict]:
    plan = _plan(ds)
    if plan is None:
        return None
    try:
        from backend.engine.planning import phase_on as P
        p = P(plan, day)
    except Exception:            # noqa: BLE001 — no plan file / a broken one: no phase
        return None
    if p is None:
        return None
    return {"kind": p.kind, "label": p.label, "start": p.start, "end": p.end}


def resolve_period(ds, b: float, e: float, key: Optional[str], zbegin: Optional[str] = None,
                   zend: Optional[str] = None) -> dict:
    """[begin, end] (dates, inclusive) of the main chart: the viewer's range
    or a quick pick relative to the dataset's today."""
    today = day_to_date(ds.today)
    monday = today - dt.timedelta(days=today.weekday())
    key = key if key in dict(PERIODS) else "range"
    s, en, label, note = day_to_date(b), day_to_date(e), dict(PERIODS)[key], None
    if key == "week":
        s, en = monday, today
    elif key == "lastweek":
        s, en = monday - dt.timedelta(days=7), monday - dt.timedelta(days=1)
    elif key == "4w":
        s, en = monday - dt.timedelta(days=21), today
    elif key == "phase":
        p = phase_on(ds, today)
        if p is None:
            key, label, note = "range", dict(PERIODS)["range"], "沒有賽事周期，改用日期範圍"
        else:
            s, en = dt.date.fromisoformat(p["start"]), min(dt.date.fromisoformat(p["end"]), today)
            label = f"本期（{p['label']}）"
    elif key == "custom":
        try:
            s, en = dt.date.fromisoformat(zbegin), dt.date.fromisoformat(zend)
        except (TypeError, ValueError):
            key, label, note = "range", dict(PERIODS)["range"], "自訂日期不完整，改用日期範圍"
    if en < s:
        s, en = en, s
    return {"key": key, "label": label, "begin": s.isoformat(), "end": en.isoformat(),
            "days": (en - s).days + 1, "note": note}


def target_for(phase: Optional[dict]) -> dict:
    kind = (phase or {}).get("kind")
    low, sources, special = PHASE_TARGETS.get(kind, (None, [SRC_FLOOR], None))
    return {"phase": phase, "low_target": low, "low_floor": LOW_SHARE_GOOD, "low_watch": LOW_SHARE_WATCH,
            "taper": special == "taper", "sources": sources}


def verdict(summary: Optional[dict], target: dict, kind: str) -> dict:
    """✓ / ⚠ on the 3-zone shares against the phase's target."""
    if not summary or summary["total_s"] < MIN_VERDICT_S:
        return {"level": "none", "icon": "–", "text": "資料不足（有區間的時間 < 1 小時）"}
    low, mod, high = (r["share"] or 0.0 for r in summary["rows"])
    lbl = (target.get("phase") or {}).get("label")
    if low < target["low_floor"]:
        why = "太多時間在中強度" if mod >= high else "高強度太多"
        lvl = "serious" if low < target["low_watch"] else "warning"
        return {"level": lvl, "icon": "⚠", "text": f"低強度不足：{why}（{low * 100:.0f}% < 75%）"}
    if target.get("low_target") and low < target["low_target"]:
        return {"level": "watch", "icon": "⚠",
                "text": f"低強度略少：{lbl}目標 ≥ {target['low_target'] * 100:.0f}%（現在 {low * 100:.0f}%）"}
    if target.get("taper") and high < TAPER_HIGH_MIN:
        return {"level": "watch", "icon": "⚠", "text": "減量期幾乎沒有高強度：強度要保留"}
    return {"level": "good", "icon": "✓", "text": "分配得當" + (f"（{lbl}）" if lbl else "")}


# ---------------------------------------------------------------------------
# the aggregation
# ---------------------------------------------------------------------------

def _activities(ds, begin: dt.date, end: dt.date, sports: tuple[str, ...]):
    lo, hi = date_to_day(begin), date_to_day(end) + 1
    return [w for w in ds.workouts if lo <= w.day < hi and sport_of(w) in sports]


def _per_activity(ds, ws, kind: str, m: dict, sm: dict) -> list[dict]:
    """Zone seconds of each activity under model m and the summary model sm,
    each at the thresholds in effect on the activity's own day."""
    from backend.engine.workout_review import _thresholds
    out = []
    for w in ws:
        item = {"w": w, "day": day_to_date(w.day), "skip": None}
        if kind == "power" and hasattr(ds, "power_ok") and not ds.power_ok(w):
            item["skip"] = "手錶推估功率不採用"
            out.append(item)
            continue
        h = activity_hist(ds, w)[kind]
        if not h:
            item["skip"] = "沒有心率" if kind == "hr" else "沒有功率"
            out.append(item)
            continue
        wt = _as_run(w)
        ctx = {"thr": _thresholds(ds, wt)}
        if m["id"] == "ilevels":
            # the PD fit of the 90 days before the activity: in-process memo (the render
            # cache keeps the chart; a past file added later changes the data fingerprint)
            k = ("period_zone_ilevels", w.entry.file)
            if k not in ds.memo:
                ds.memo[k] = A.ilevels_for(ds, wt)
            ctx["ilevels"] = ds.memo[k]
        for name, mm in (("main", m), ("sum", sm)):
            if name == "sum" and mm is m:
                item["sum"] = item.get("main")
                continue
            b = A._bounds(ds, wt, kind, mm, ctx)
            if "reason" in b:
                item[name] = None
                if name == "main":
                    item["skip"] = b["reason"]
                continue
            item[name] = {"secs": _secs(h, b["rows"]), "rows": b["rows"], "basis_text": b.get("basis_text")}
        out.append(item)
    if hasattr(ds, "flush_series"):
        ds.flush_series()
    return out


def _summary(items: list[dict], sm: dict) -> Optional[dict]:
    names = ["低強度", "中強度", "高強度"]
    secs = [0.0, 0.0, 0.0]
    n = 0
    for it in items:
        s = it.get("sum")
        if not s or it.get("skip"):
            continue
        n += 1
        for i in range(3):
            secs[i] += s["secs"][i]
    tot = sum(secs)
    pct = _pct_text(sm)
    return {"model": sm["id"], "title": sm["title"], "total_s": tot, "n": n,
            "rows": [{"id": str(i + 1), "name": names[i], "pct": pct[i], "seconds": secs[i],
                      "share": secs[i] / tot if tot else None} for i in range(3)]}


def _thresholds_used(items: list[dict]) -> list[dict]:
    seen: dict = {}
    for it in items:
        s = it.get("main")
        if not s or it.get("skip"):
            continue
        t = s.get("basis_text") or "—"
        # value = the numbers without the source in brackets (「LTHR 155 bpm」), for the short on-chart line
        r = seen.setdefault(t, {"text": t, "value": t.split("（")[0].strip(), "n": 0,
                                "first": it["day"].isoformat(), "last": it["day"].isoformat()})
        r["n"] += 1
        r["last"] = it["day"].isoformat()
    return sorted(seen.values(), key=lambda r: r["first"])


def _skipped(items: list[dict]) -> list[dict]:
    c: dict = {}
    for it in items:
        if it.get("skip"):
            c[it["skip"]] = c.get(it["skip"], 0) + 1
    return [{"reason": k, "n": v} for k, v in sorted(c.items(), key=lambda kv: -kv[1])]


def _zone_rows(items: list[dict], m: dict, kind: str) -> tuple[list[dict], float]:
    used = [it for it in items if it.get("main") and not it.get("skip")]
    if used:
        zs = used[0]["main"]["rows"]
    elif "zones" in m:
        zs = m["zones"]()
    elif m["id"] == "ilevels":
        zs = [(i, n, None, None) for i, n in A.ILEVEL_NAMES]
    else:                                   # seiler3: the names _bounds gives its rows
        zs = [("1", "低強度（< AeT）", None, None), ("2", "中強度（AeT–LTHR）", None, None),
              ("3", "高強度（≥ LTHR）", None, None)]
    secs = [0.0] * len(zs)
    for it in used:
        for i, s in enumerate(it["main"]["secs"]):
            secs[i] += s
    tot = sum(secs)
    one = len({it["main"]["basis_text"] for it in used}) == 1
    pct = _pct_text(m)
    unit = "bpm" if kind == "hr" else "W"
    rows = []
    for i, (zid, nm, lo, hi) in enumerate(zs):
        rng = None
        if one and used:
            lo_, hi_ = (0 if i == 0 else lo), hi
            rng = (f"< {hi_:.0f}" if not lo_ else f"≥ {lo_:.0f}" if hi_ is None else f"{lo_:.0f}–{hi_:.0f}") + f" {unit}"
        rows.append({"id": str(zid), "name": nm, "pct": pct[i] if i < len(pct) else None, "range": rng,
                     "seconds": secs[i], "share": secs[i] / tot if tot else None})
    return rows, tot


def _group_key(d: dt.date, by: str) -> dt.date:
    return d - dt.timedelta(days=d.weekday()) if by == "week" else d.replace(day=1)


def _groups(begin: dt.date, end: dt.date, by: str) -> list[tuple[dt.date, dt.date]]:
    out, cur = [], _group_key(begin, by)
    while cur <= end:
        if by == "week":
            nxt = cur + dt.timedelta(days=7)
        else:
            nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        out.append((cur, nxt - dt.timedelta(days=1)))
        cur = nxt
    return out


def compute(ds, b: float, e: float, params: dict, view: str = "total") -> dict:
    kind = params.get("zkind") if params.get("zkind") in ("hr", "power") else "hr"
    m = model(kind, params.get("zmodel"))
    sm = model(kind, SUMMARY_MODEL[kind])
    sports = parse_sports(params.get("zsports"))
    today = day_to_date(ds.today)
    base = {"zone_kind": kind,
            "model": {"id": m["id"], "title": m["title"], "source": m["source"], "estimate": bool(m.get("estimate"))},
            "models": {k: [{"id": x["id"], "title": x["title"], "estimate": bool(x.get("estimate"))} for x in models(k)]
                       for k in ("hr", "power")},
            "default_model": DEFAULT_MODEL,
            "sports": list(sports), "sport_choices": [{"id": s, "label": lb} for s, lb in SPORTS],
            "summary_model": {"id": sm["id"], "title": sm["title"]}}
    if view == "weekly":
        begin, end = day_to_date(b), min(day_to_date(e), today)
        if end < begin:
            end = begin
        g = params.get("zgroup") if params.get("zgroup") in GROUPS else "auto"
        by = g if g != "auto" else ("week" if (end - begin).days + 1 <= AUTO_WEEK_MAX_DAYS else "month")
        spans = _groups(begin, end, by)
        items = _per_activity(ds, _activities(ds, spans[0][0], end, sports), kind, m, sm)
        rows, tot = _zone_rows(items, m, kind)
        groups = []
        for gs, ge in spans:
            gi = [it for it in items if gs <= it["day"] <= ge]
            zr, zt = _zone_rows(gi, m, kind) if gi else ([{**r, "seconds": 0.0, "share": None} for r in rows], 0.0)
            ph = phase_on(ds, min(ge, today))
            tg = target_for(ph)
            sumr = _summary(gi, sm)
            groups.append({"start": gs.isoformat(), "end": ge.isoformat(), "partial": ge > today,
                           "seconds": [r["seconds"] for r in zr], "total_s": zt, "n": len(gi),
                           "summary": sumr, "low_share": (sumr["rows"][0]["share"] if sumr and sumr["total_s"] else None),
                           "phase": ph, "low_target": tg["low_target"], "verdict": verdict(sumr, tg, kind)})
        return {**base, "view": "weekly", "group_by": by, "group_choice": g,
                "range": {"begin": begin.isoformat(), "end": end.isoformat()},
                "zones": [{k: r[k] for k in ("id", "name", "pct", "range")} for r in rows],
                "groups": groups, "total_s": tot, "n_activities": len(items),
                "low_floor": LOW_SHARE_GOOD, "thresholds": _thresholds_used(items), "skipped": _skipped(items),
                "target_sources": sorted({s for gr in groups for s in target_for(gr["phase"])["sources"]}),
                "empty": None if tot else _empty(items, kind)}
    per = resolve_period(ds, b, e, params.get("zperiod"), params.get("zbegin"), params.get("zend"))
    begin, end = dt.date.fromisoformat(per["begin"]), dt.date.fromisoformat(per["end"])
    items = _per_activity(ds, _activities(ds, begin, end, sports), kind, m, sm)
    rows, tot = _zone_rows(items, m, kind)
    ph = phase_on(ds, min(end, today))
    tg = target_for(ph)
    sumr = _summary(items, sm)
    return {**base, "view": "total", "period": {**per, "phase": ph},
            "period_choices": [{"id": k, "label": lb} for k, lb in PERIODS],
            "rows": rows, "total_s": tot, "n_activities": len(items),
            "n_used": sum(1 for it in items if it.get("main") and not it.get("skip")),
            "thresholds": _thresholds_used(items), "skipped": _skipped(items),
            "summary": sumr, "target": tg, "verdict": verdict(sumr, tg, kind),
            "empty": None if tot else _empty(items, kind)}


def _empty(items: list[dict], kind: str) -> str:
    if not items:
        return "這段時間沒有選到的活動"
    sk = _skipped(items)
    return "沒有可用的" + ("心率" if kind == "hr" else "功率") + (f"（{sk[0]['reason']}）" if sk else "")


def render(ds, ch: dict, b: float, e: float, params: dict) -> dict:
    """The JSON of one kind "periodzones" chart (wko5views._render)."""
    view = ch.get("view") or "total"
    return {"title": ch.get("title"), "description": ch.get("description"), "kind": "periodzones",
            **compute(ds, b, e, params or {}, view)}
