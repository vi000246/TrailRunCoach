"""
有杖 vs 沒杖 (views kind "polecompare", 我的訓練 → 能力, after 下坡腳程; SP-243).

Compares what was MEASURED on the activities the athlete marked 有杖 / 沒杖
(activity_tags.POLES, SP-242; with no choice of theirs, a race marked 「會用登山杖」
makes its activities 有杖, SP-300 — activity_tags.race_poles / pole_state),
never a model output: the weekly downhill
impact load is grade × speed and poles can't change it
(docs/research/trekking-poles.md §5.1, downhill-knee-load-display.md §5).
Read-only and for this chart only: the mark feeds no prediction model.

Shown only to someone who uses poles: ≥ 5 activities marked 有杖 AND ≥ 5
marked 沒杖 in the last 365 days, counting only the activities this chart
uses (trail runs and hikes; `counts` / `chart_rows` → activity_tags.pole_counts;
the chart list says so per chart: `"needs": "poles"`, wko5views.list_views →
needs_met).

Per activity (trail runs and hikes, panels/climb_vam.kind_of) and per grade
bin (rgrade, %): ≤ −15, −15…−8, −8…−3 (downhill) and ≥ +15 (steep climb),
on every moving step — the knee-load card's §3.1 rule (SP-235): no cadence
filter, a walked step counts, since poles are used almost only walking; the
moving floor is the hike rest speed (athlete.HIKE_REST_MS, 0.3 m/s), so a
slow steep step is not dropped:
  * downhill: vertical speed (m/h), cadence (spm), impact G and Stryd ILR
    when the device wrote them (a watch / Stryd may not, walking: then "–");
  * steep climb: VAM ÷ HR (m/h per bpm) on the steps with HR — "同心率":
    comparing per heartbeat takes out part of "that day I pushed harder"
    (§5.1; the same measure as the 穩定爬坡 VAM:HR chart).
An activity counts in a bin with ≥ MIN_BIN_S there. Each group's point is
the median of its activities' values; n = the number of activities; n <
MIN_N is not drawn (the n is still shown). No verdict: the days with poles
are often the days with a heavier pack, steeper ground and longer hours.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Optional

import numpy as np

from backend.i18n import N_, _

SERIES_KEY = "pole_compare_v1"
# grade bins, % (trekking-poles.md §5.1; downhill-knee-load-display.md §5): lo < g ≤ hi downhill, g ≥ lo up
BINS = (
    {"id": "d15", "dir": "down", "lo": None, "hi": -15.0, "label": "≤ −15%"},
    {"id": "d8", "dir": "down", "lo": -15.0, "hi": -8.0, "label": "−15 ~ −8%"},
    {"id": "d3", "dir": "down", "lo": -8.0, "hi": -3.0, "label": "−8 ~ −3%"},
    {"id": "u15", "dir": "up", "lo": 15.0, "hi": None, "label": "≥ +15%"},
)
# (id, bins, label, unit, decimals)
METRICS = (
    ("vspeed", "down", N_("下坡垂直速度"), "m/h", 0),
    ("cadence", "down", N_("下坡步頻"), "spm", 0),
    ("impact_g", "down", N_("下坡衝擊 G"), "G", 2),
    ("ilr", "down", N_("下坡衝擊負荷率 ILR"), "BW/s", 1),
    ("vam_hr", "up", N_("陡上坡同心率 VAM（VAM ÷ 心率）"), "m/h/bpm", 2),
)
MIN_N = 3              # SP-243 / trekking-poles.md §5 #2 ④: a point with n < 3 is not drawn
MIN_BIN_S = 120.0      # 推估: ≥ 2 min in a bin for an activity to count there (a few steep seconds are noise)
GLITCH_RATIO = 1.0     # 推估 (as climb_vam's glitch rule): |Δheight| > Δdistance in a bin = an altitude glitch
CAVEAT = N_("有杖的日子常常也是背包較重、坡較陡、走得較久的日子，差異不一定是杖造成的。")
SOURCES_OVERRIDE: Optional[list] = None   # tests: the tag rows (activity_tags.load())


def _moving(t: np.ndarray, speed) -> np.ndarray:
    from backend.engine.racepower.athlete import HIKE_REST_MS
    from backend.engine.workout_review import MAX_DT, _arr
    d = np.diff(t, prepend=t[0] if len(t) else 0.0)
    ok = np.isfinite(d) & (d > 0) & (d <= MAX_DT)
    if speed is not None:
        s = _arr(speed, len(t))
        ok &= ~(np.isfinite(s) & (s <= HIKE_REST_MS * 3.6))
    return ok


def extract(ds, w) -> dict:
    """{bin id: {time_s, dz_m, dist_m, cadence, impact_g, ilr, hr_s, hr, hr_dz_m}} of one
    activity (bins with no moving step left out); {} without time, distance or altitude."""
    from backend.engine import workout_review as WR
    s = WR._samples(ds, w)
    if s is None or s["elev"] is None or s["dist"] is None:
        return {}
    t = np.asarray(s["t"], dtype=float)
    n = len(t)
    g = WR._rgrade(ds, w, s)
    if g is None or not np.isfinite(g).any():
        return {}
    gp = WR._arr(g, n) * 100.0
    d = WR._dt(t)
    mov = _moving(t, s["speed"])
    z = WR._arr(s["elev"], n)
    dz = np.nan_to_num(np.diff(z, prepend=z[0] if n else 0.0), nan=0.0)
    km = WR._arr(s["dist"], n)
    dd = np.nan_to_num(np.diff(km, prepend=km[0] if n else 0.0), nan=0.0) * 1000.0
    cad = None if s["cadence"] is None else WR._arr(s["cadence"], n) * 2.0     # strides/min → spm
    ilr = None if s["ilr"] is None else WR._arr(s["ilr"], n)
    ig = None
    if s["gct"] is not None:
        ig = WR._eval(ds, w, "fmax/(metric(weight)*g)")
        ig = None if ig is None or not WR._has(ig) else WR._arr(ig, n)
    h = WR._arr(s["hr"], n)
    out = {}
    for b in BINS:
        m = mov & np.isfinite(gp)
        if b["dir"] == "down":
            m &= gp <= b["hi"]
            if b["lo"] is not None:
                m &= gp > b["lo"]
        else:
            m &= gp >= b["lo"]
        secs = float(d[m].sum())
        if secs <= 0:
            continue
        hm = m & np.isfinite(h) & (h > 0)
        out[b["id"]] = {
            "time_s": secs, "dz_m": float(dz[m].sum()), "dist_m": float(dd[m].sum()),
            "cadence": WR._wmean(cad, d, m) if cad is not None else None,
            "impact_g": WR._wmean(ig, d, m) if ig is not None else None,
            "ilr": WR._wmean(ilr, d, m) if ilr is not None else None,
            "hr_s": float(d[hm].sum()), "hr": WR._wmean(h, d, hm), "hr_dz_m": float(dz[hm].sum())}
    return out


def _cached(ds, w) -> dict:
    f = getattr(ds, "cached_series", None)
    if f is None:
        return extract(ds, w)
    return f(SERIES_KEY, w, lambda: extract(ds, w)) or {}


def values(bins: dict) -> dict:
    """{bin id: {metric: value}} of one activity: only bins with ≥ MIN_BIN_S (for VAM ÷ HR:
    with HR) and no altitude glitch."""
    out = {}
    for b in BINS:
        r = bins.get(b["id"])
        if not r or r["time_s"] < MIN_BIN_S:
            continue
        if r.get("dist_m") and abs(r["dz_m"]) > GLITCH_RATIO * r["dist_m"]:
            continue
        v = {}
        if b["dir"] == "down":
            v["vspeed"] = -r["dz_m"] / r["time_s"] * 3600.0
            for k in ("cadence", "impact_g", "ilr"):
                if r.get(k) is not None:
                    v[k] = r[k]
        elif r.get("hr") and r.get("hr_s", 0) >= MIN_BIN_S:
            vam = r["hr_dz_m"] / r["hr_s"] * 3600.0
            v.update(vam_hr=vam / r["hr"], vam=vam, hr=r["hr"])
        out[b["id"]] = v
    return out


def _stat(vals: list[float]) -> dict:
    n = len(vals)
    if n < MIN_N:                       # too few: only the count, no value to read a difference into
        return {"n": n, "value": None, "q1": None, "q3": None}
    q = np.percentile(vals, [25, 50, 75]) if n >= 4 else (None, statistics.median(vals), None)
    return {"n": n, "value": float(q[1]), "q1": None if q[0] is None else float(q[0]),
            "q3": None if q[2] is None else float(q[2])}


def _rows() -> list:
    if SOURCES_OVERRIDE is not None:
        return SOURCES_OVERRIDE
    from backend.engine import activity_tags as AT
    return AT.load()


def used(w) -> bool:
    """Whether this chart uses the activity: a trail run or a hike (climb_vam.kind_of)."""
    from backend.engine.panels.climb_vam import kind_of
    return kind_of(w) is not None


def chart_rows(ds, rows: list, today: dt.date, days: Optional[int] = None) -> list:
    """The pole marks of the activities this chart uses (`used`: trail runs and hikes) that
    started in about the last `days` days (AT.pole_counts makes the exact cut on the row's own
    start), as tag rows {start_local, file, tags: [the effective mark]}. Only their marks count
    toward the 5 + 5 (user decision 2026-10-06), so 「再標有杖 N 次、沒杖 M 次」 is what the chart
    really needs: a mark on a road run, a ride or an excluded file does not count. The mark is
    the user's choice, else 有杖 「依賽事設定」 when a race marked 「會用登山杖」 covers the activity
    (SP-300: AT.race_poles / pole_state)."""
    from backend.engine import activity_tags as AT
    from backend.engine.wko5expr.dataset import date_to_day
    race = AT.race_poles(ds)
    if not race and (not rows or not AT.pole_marks_stamp(rows)):   # no mark at all: nothing to look up
        return []
    days = AT.POLE_COMPARE_DAYS if days is None else days
    first = date_to_day(today) - days                       # a day of slack before the window
    out, seen = [], set()
    for w in ds.workouts:
        if w.day < first or not used(w):
            continue
        r = AT.find(rows, w.entry.start, getattr(w.entry, "file", None)) if rows else None
        if r is not None and id(r) in seen:
            continue
        p = _mark(r, race.get(w.idx))
        if r is not None:
            seen.add(id(r))
        if p is not None:
            out.append({"start_local": (r or {}).get("start_local") or AT.key_of(w.entry.start),
                        "file": (r or {}).get("file") or getattr(w.entry, "file", None), "tags": [AT.POLES[p]]})
    return out


def _mark(row: Optional[dict], race: Optional[str]) -> Optional[str]:
    """"with" / "without" / None of one activity: its tag row's choice, else the race default."""
    from backend.engine import activity_tags as AT
    tags = (row or {}).get("tags") if (row or {}).get("tags") is not None else AT.tags_of(row)
    return AT.pole_state(tags, race)["poles"]


def counts(ds, rows: list, today: dt.date) -> dict:
    """AT.pole_counts over the marks of the activities this chart uses (chart_rows)."""
    from backend.engine import activity_tags as AT
    return AT.pole_counts(chart_rows(ds, rows, today), today)


def compute(ds, b: float, e: float, params: Optional[dict] = None, rows: Optional[list] = None,
            today: Optional[dt.date] = None) -> dict:
    from backend.engine import activity_tags as AT
    rows = _rows() if rows is None else rows
    today = today or dt.date.today()
    cnt = counts(ds, rows, today)
    out = {"kind": "polecompare", "counts": cnt, "min_n": MIN_N, "min_bin_s": MIN_BIN_S,
           "caveat": _(CAVEAT), "bins": [{k: x[k] for k in ("id", "dir", "label")} for x in BINS],
           "metrics": [], "activities": {"with": 0, "without": 0}, "list": []}
    if not cnt["eligible"]:
        out["empty"] = _("近 {days} 天的越野跑、登山健行標了「有杖」{w} 次、「沒杖」{wo} 次；"
                         "兩邊都要至少 {need} 次才比較。"
                         "到活動列表編輯活動，在「登山杖」選有杖或沒杖。",
                         days=cnt["days"], w=cnt["with"], wo=cnt["without"], need=cnt["need"])
        return out
    per = {"with": [], "without": []}
    race = AT.race_poles(ds)                 # 有杖 「依賽事設定」 (SP-300) where the user didn't choose
    for w in ds.workouts:
        if not (b <= w.day < e + 1):
            continue
        if not used(w):
            continue
        p = _mark(AT.find(rows, w.entry.start, getattr(w.entry, "file", None)), race.get(w.idx))
        if p not in per:
            continue
        v = values(_cached(ds, w))
        per[p].append(v)
        out["list"].append({"date": w.entry.start.date().isoformat(), "workout": w.idx, "poles": p,
                            "bins": sorted(v)})
    out["activities"] = {k: len(v) for k, v in per.items()}
    for mid, direction, label, unit, dec in METRICS:
        rows_m = []
        for bn in BINS:
            if bn["dir"] != direction:
                continue
            st = {g: _stat([a[bn["id"]][mid] for a in per[g] if mid in a.get(bn["id"], {})])
                  for g in ("with", "without")}
            rows_m.append({"bin": bn["id"], "label": bn["label"], **st})
        if not any(r[g]["n"] for r in rows_m for g in ("with", "without")):
            continue                       # e.g. no device wrote an impact value
        out["metrics"].append({"id": mid, "label": _(label), "unit": unit, "decimals": dec, "dir": direction,
                               "rows": rows_m})
    if not out["list"]:
        out["empty"] = _("這段期間沒有標了有杖或沒杖的越野跑、登山健行。")
    elif not any(r[g]["value"] is not None for m in out["metrics"] for r in m["rows"] for g in ("with", "without")):
        out["note"] = _("每個坡度箱的活動都不到 {n} 次，先不畫點；只列出次數。", n=MIN_N)
    return out


def render(ds, ch: dict, b: float, e: float, params: dict, today: Optional[dt.date] = None) -> dict:
    """The JSON of one kind "polecompare" chart (wko5views._render)."""
    return {"title": ch.get("title"), "description": ch.get("description"),
            **compute(ds, b, e, params or {}, today=today)}
