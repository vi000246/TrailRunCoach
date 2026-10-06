"""
The athlete-derived inputs of the race-power page, read from the Dataset:
weight, CP / W′ / TTE sources, the mean-max envelope (with the activity that
set each point), personal Riegel k, road / trail RE, hiking EP/h, training
conditions and AeT. Everything numeric is delegated to the pure modules.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import math
import re
from statistics import median
from typing import Optional

import numpy as np

from backend.engine.algorithms.effort import SIMPLE_FORMULAS, divisor_of
from backend.engine.racepower import cp as CP
from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import re as RE
from backend.engine.racepower import riegel as R
from backend.engine.racepower import weather as WX
from backend.i18n import _

CP_WINDOW_DAYS = 90
RIEGEL_WINDOW_DAYS = 365
RE_WINDOW_DAYS = 365
HIKE_WINDOW_DAYS = 3 * 365
PLAN_CP_MAX_AGE_DAYS = 90
DEFAULT_TTE_S = 3000.0
TRAIL_MIN_CLIMB_M, TRAIL_MIN_MOVING_S = 150.0, 45 * 60
ROAD_MAX_CVI, ROAD_MIN_MOVING_S = 25.0, 20 * 60
ROAD_MAX_CVI_ADJ = 51.0         # 推估: CVI-adjust road runs up to 小丘 (category ≤ 3), no further extrapolation
DEFAULT_K = -0.07               # ≈ Stryd's race-power table (k −0.069, V-F1)
HIKE_MIN_MOVING_S = 3600.0
HIKE_HEAVY_GAIN_M, HIKE_HEAVY_WEIGHT = 600.0, 3.0
FALLBACK_TRAINING = {"altitude_m": 100.0, "temp_c": 25.0, "rh_pct": 75.0}
METRICS_KEY = "racepower_v1"
TRAINING_ENV_CACHE = None      # fixed file (tests); None = <tenant shared>/racepower_training_env.json


def _training_env_cache() -> Path:
    return Path(TRAINING_ENV_CACHE) if TRAINING_ENV_CACHE is not None else WX.home() / "racepower_training_env.json"

SPORT_ZH = {"running": "路跑", "trail running": "越野跑", "indoor running": "跑步機",
            "hiking": "健行", "mountaineering": "登山"}


def _grid() -> list[float]:
    g = set(float(x) for x in range(1, 11))
    x = 10.0
    while x < 40 * 3600:
        x *= 1.05
        g.add(float(round(x)))
    g.update(float(t) for t in CP.CP_DURATIONS)
    g.update(float(t) for t in (1800, 2400, 3600, 5400, 7200, 10800, 14400, 18000, 21600))
    return sorted(g)


GRID = _grid()


def label(w) -> str:
    km = w.metrics.get("distance")
    return f"{w.entry.start:%Y-%m-%d} {SPORT_ZH.get(w.sport_type, w.sport_type)}" + \
        (f" {km:.1f} km" if km else "")


def act_ref(w) -> dict:
    return {"idx": w.idx, "date": w.entry.start.date().isoformat(), "label": label(w),
            "file": w.entry.file}


def power_ok(ds, w) -> bool:
    """The run's power may feed the power-based models (Stryd power, or watch
    power with power.accept_watch_power; backend/engine/power_source.py).
    Datasets without the method (test doubles) accept every power."""
    f = getattr(ds, "power_ok", None)
    return True if f is None else bool(f(w))


def power_source(ds, w) -> Optional[str]:
    f = getattr(ds, "power_source", None)
    return f(w) if f is not None else None


def power_runs(ds, runs) -> list:
    return [w for w in runs if power_ok(ds, w)]


def watch_unused(ds, runs) -> list[dict]:
    """The runs whose watch-estimated power the models skip (「手錶推估功率（未採用）」)."""
    from backend.engine import power_source as PS
    if getattr(ds, "accept_watch_power", True):
        return []
    return [{**act_ref(w), "power_source": PS.WATCH, "label_power": PS.UNUSED_LABEL}
            for w in runs if power_source(ds, w) == PS.WATCH]


def model_stats(ds, w) -> Optional[dict]:
    """intensity_stats with the power numbers (p_avg, Pw:HR drift) removed
    when the run's power is not used (watch power); HR / pace untouched."""
    st = intensity_stats(ds, w)
    if st and not power_ok(ds, w):
        st = {**st, "p_avg": None, "drift": None}
    return st


def _curve(ds, w, any_power: bool = False) -> Optional[tuple[list, list]]:
    """The run's power mean-max. `any_power`: also watch-estimated power
    (only the LTHR estimate's cp_as_of reads it; see pd_model)."""
    if not any_power and not power_ok(ds, w):
        return None                      # watch-estimated power: no mean-max for the models
    hit = ds.curve_cache("meanmax(power)").get(w.entry.file)
    if hit is None:
        hit = ds.workout_curve(w.idx, "power")
    return hit


def implausible(w, xs, ys, ref_cp: Optional[float]) -> bool:
    """Power that no run can have: NP ≥ 1.5 × CP over the whole run, or a
    5-minute best above 2 × CP (a dropped / spiking sensor)."""
    if not ref_cp:
        return False
    np_ = w.metrics.get("np")
    if np_ and np_ > 1.5 * ref_cp:
        return True
    if xs[-1] >= 300 and np.interp(300.0, xs, ys) > 2.0 * ref_cp:
        return True
    return False


def altitude_norm(elev_m: Optional[float], ref: Optional[dict]) -> float:
    """D2: power measured at `elev_m`, expressed in the reference (training)
    conditions — P_ref = P_act · M(activity → reference). Only the altitude
    term: per-activity temperature / humidity are not stored, so both sides
    use the reference T / RH and the heat term cancels."""
    if elev_m is None or not ref or ref.get("altitude_m") is None:
        return 1.0
    from backend.engine.racepower import env as ENV
    t, rh = ref.get("temp_c"), ref.get("rh_pct")
    return ENV.multiplier({"altitude_m": elev_m, "temp_c": t, "rh_pct": rh},
                          {"altitude_m": ref["altitude_m"], "temp_c": t, "rh_pct": rh})["M"]


def envelope(ds, runs, ref_cp: Optional[float] = None, dropped: Optional[list] = None,
             ref_env: Optional[dict] = None, metrics: Optional[dict] = None) -> dict:
    """Mean-max envelope on GRID with the activity that set each point:
    {"xs", "ys", "who": [workout idx]}. Only durations each curve reaches.
    Runs with implausible power (see `implausible`) are skipped and listed
    in `dropped`. With `ref_env` + `metrics` each run's power is first
    normalised to the reference altitude (`altitude_norm`, decision D2)."""
    grid = np.array(GRID)
    best = np.full(len(grid), np.nan)
    who = np.full(len(grid), -1)
    for w in runs:
        c = _curve(ds, w)
        if not c or not c[0]:
            continue
        xs = np.array(c[0], float)
        ys = np.array([np.nan if v is None else v for v in c[1]], float)
        ok = np.isfinite(ys)
        if ok.sum() < 2:
            continue
        xs, ys = xs[ok], ys[ok]
        if implausible(w, xs, ys, ref_cp):
            if dropped is not None:
                dropped.append(act_ref(w))
            continue
        if ref_env is not None and metrics is not None:
            ys = ys * altitude_norm((metrics.get(w.idx) or {}).get("elev_median"), ref_env)
        m = grid <= xs[-1]
        vals = np.interp(grid[m], xs, ys)
        cur = best[m]
        better = ~(vals <= cur)          # NaN-safe: true when cur is NaN
        idx = np.where(m)[0][better]
        best[idx] = vals[better]
        who[idx] = w.idx
    keep = np.isfinite(best)
    return {"xs": grid[keep].tolist(), "ys": best[keep].tolist(), "who": who[keep].astype(int).tolist()}


def _per_run(ds, w, weight) -> Optional[dict]:
    t = ds.channel(w.idx, "elapsedtime")
    p = ds.channel(w.idx, "power")
    s = ds.channel(w.idx, "speed")
    if t is None or p is None or s is None or not np.any(np.nan_to_num(p) > 0):
        return None
    m = RE.activity_metrics(t, p, s, weight, ds.channel(w.idx, "@air_power"),
                            ds.channel(w.idx, "@form_power"), ds.channel(w.idx, "@leg_spring_stiffness"),
                            min_speed_kmh=1.0)
    if m is None:
        return None
    e = ds.channel(w.idx, "_elevation")
    if e is None:
        e = ds.channel(w.idx, "elevation")
    lat, lon = ds.channel(w.idx, "latitude"), ds.channel(w.idx, "longitude")

    def first(a):
        if a is None:
            return None
        ok = a[np.isfinite(a) & (a != 0)]
        return float(ok[0]) if len(ok) else None
    m["elev_median"] = float(np.nanmedian(e)) if e is not None and np.isfinite(e).any() else None
    m["lat"], m["lon"] = first(lat), first(lon)
    m["air_any"] = bool(ds.channel(w.idx, "@air_power") is not None and
                        np.nanmax(np.nan_to_num(ds.channel(w.idx, "@air_power"))) > 0)
    return m


def run_metrics(ds, runs, weight) -> dict[int, dict]:
    """Per-run power metrics (RE, moving power, …) of the runs whose power
    the models use (power_ok); watch-estimated power is left out."""
    out = {}
    for w in power_runs(ds, runs):
        v = ds.cached_series(METRICS_KEY, w, lambda w=w: _per_run(ds, w, weight))
        if v:
            out[w.idx] = v
    ds.flush_series()
    return out


def _plan_cp(ds, today: dt.date) -> Optional[dict]:
    rows = sorted((t for t in ds.plan.thresholds if t.cp is not None and t.date[:10] <= today.isoformat()),
                  key=lambda t: t.date)
    if not rows:
        return None
    t = rows[-1]
    age = (today - dt.date.fromisoformat(t.date[:10])).days
    return {"cp": float(t.cp), "date": t.date[:10], "age_days": age, "fresh": age <= PLAN_CP_MAX_AGE_DAYS}


SOLO_HIKES = None      # fixed file (tests); None = the tenant's (private) racepower_solo_hikes.json


def _solo_hikes_path() -> Path:
    from backend import tenancy
    return Path(SOLO_HIKES) if SOLO_HIKES is not None else tenancy.private_path("racepower_solo_hikes.json")
GROUP_HIKE_NOTE = "百岳多為跟團，速度不代表個人能力，不列入目標時間推算"
HIKE_SPORTS = ("hiking", "mountaineering")
HIKE_TAGS = {"hiking", "mountaineering"}


def is_hike(w) -> bool:
    return w.sport_type in HIKE_SPORTS or bool(HIKE_TAGS & set(w.tags))


def solo_hikes(path=None) -> set[str]:
    """Hikes the user opted in as solo (paced by the athlete): their file
    names (+ "starts": {file: local start}). Everything else tagged hiking /
    mountaineering is treated as group-paced and kept out of every
    target-time calibration. A ByStartSet (engine/activity_key.py): `file in
    solo` also matches the same trip under another source's file name."""
    from backend.engine.activity_key import ByStartSet
    try:
        d = json.loads((path or _solo_hikes_path()).read_text("utf-8"))
        return ByStartSet(d.get("files") or [], d.get("starts") or {})
    except (OSError, ValueError):
        return ByStartSet()


def set_solo_hikes(files, path=None) -> set[str]:
    from backend.engine import activity_key as AK
    p = path or _solo_hikes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    s = sorted({str(f) for f in files if f})
    old = solo_hikes(p)
    starts = {f: old.starts.get(f) or AK.key_of(AK.start_of_file(f)) for f in s}
    starts = {f: v for f, v in starts.items() if v}
    p.write_text(json.dumps({"files": s, "starts": starts}, ensure_ascii=False), "utf-8")
    return AK.ByStartSet(s, starts)


def hiking_days(ds, today: dt.date, solo: Optional[set] = None) -> list[dict]:
    """One row per hiking / mountaineering day (multi-day trips split by
    calendar day, as achievements.py does); `solo` marks the opted-in ones."""
    from backend.engine.achievements import KIND_HIKE, build_achievements
    lo = (today - dt.timedelta(days=HIKE_WINDOW_DAYS)).isoformat()
    solo = solo_hikes() if solo is None else solo
    rows = []
    for a in build_achievements(ds):
        if a.kind != KIND_HIKE or a.start[:10] < lo or a.start[:10] > today.isoformat():
            continue
        name = a.auto_name
        parts = a.days or [{"date": a.start[:10], "distance_km": a.distance_km,
                            "climbing_m": a.climbing_m, "moving_s": a.moving_s}]
        for i, d in enumerate(parts):
            km, gain, mv = d.get("distance_km"), d.get("climbing_m"), d.get("moving_s")
            if not km or gain is None or not mv or mv < HIKE_MIN_MOVING_S:
                continue
            ep = km + gain / 100.0
            rows.append({"date": d["date"], "trip": a.start[:10], "name": name,
                         "day": i + 1 if a.days else None, "days": len(a.days) or 1,
                         "km": km, "gain_m": gain, "moving_h": mv / 3600.0, "ep": ep,
                         "ep_per_h": ep / (mv / 3600.0), "top_m": d.get("top_m", a.top_m),
                         "peaks": [p["name"] for p in a.peaks], "file": a.id, "solo": a.id in solo})
    return sorted(rows, key=lambda r: r["date"])


def fallback_training() -> dict:
    """The training conditions when the last 90 days have no weather (plan P5):
    temperature / RH = the athlete's own medians over every activity with
    weather (engine/heat_calib home_temp_c / home_rh_pct, ≥ 10 activities),
    else racepower/env.py's reference conditions; altitude 200 m (env.py)."""
    from backend.engine import heat_calib as HC
    t, rh = HC.current("home_temp_c"), HC.current("home_rh_pct")
    own = t.get("source") == "fitted" and rh.get("source") == "fitted"
    wx = (f"本人 {t.get('n')} 次活動的中位 {t['value']:.0f} °C／{rh['value']:.0f} %" if own
          else f"預設 {t['value']:.0f} °C／{rh['value']:.0f} %（參考條件）")
    alt = HC.HOME_DEFAULTS["home_alt_m"]
    return {"altitude_m": alt, "temp_c": float(t["value"]), "rh_pct": float(rh["value"]), "label_wx": wx,
            "label": f"{alt:.0f} m（預設）／{wx}"}


def _training_conditions(ds, runs, metrics, today: dt.date, fetch: bool = True) -> dict:
    """Median elevation of the last 90 days of power runs + Open-Meteo archive
    T / RH over those activities at the median start location (cached daily)."""
    ms = [(w, metrics[w.idx]) for w in runs if w.idx in metrics]
    elevs = [m["elev_median"] for _, m in ms if m.get("elev_median") is not None]
    lats = [m["lat"] for _, m in ms if m.get("lat")]
    lons = [m["lon"] for _, m in ms if m.get("lon")]
    fb = fallback_training()
    base = {"altitude_m": float(median(elevs)) if elevs else fb["altitude_m"],
            "temp_c": fb["temp_c"], "rh_pct": fb["rh_pct"],
            "provider": "fallback", "label": fb["label"], "runs": len(ms),
            "lat": float(median(lats)) if lats else None, "lon": float(median(lons)) if lons else None}
    if elevs:
        base["label"] = f"近 {CP_WINDOW_DAYS} 天 {len(elevs)} 次跑步的中位海拔；溫濕度：{fb['label_wx']}"
    if base["lat"] is None or not fetch:
        return base
    stamp = f"{today.isoformat()}|{len(ms)}|{base['lat']:.3f},{base['lon']:.3f}"
    try:
        c = json.loads(_training_env_cache().read_text("utf-8"))
        if c.get("stamp") == stamp:
            return {**base, **c["values"]}
    except (OSError, ValueError, KeyError):
        pass
    windows = []
    for w, m in ms:
        # archive data lags ~5 days
        if w.entry.start.date() > today - dt.timedelta(days=6):
            continue
        dur = w.metrics.get("duration") or m["moving_s"]
        windows.append((w.entry.start, w.entry.start + dt.timedelta(seconds=dur)))
    try:
        r = WX.fetch_activities_conditions(base["lat"], base["lon"], windows)
    except Exception as e:                  # noqa: BLE001
        base["error"] = str(e)[:120]
        return base
    if not r:
        return base
    vals = {"temp_c": r["temp_c"], "rh_pct": r["rh_pct"], "provider": "open_meteo_archive",
            "label": f"Open-Meteo 歷史資料：近 {CP_WINDOW_DAYS} 天 {r['activities']} 次跑步當下的平均溫濕度",
            "fetched_at": dt.datetime.now(WX.TZ).isoformat(timespec="seconds"),
            "attribution": WX.ATTRIBUTION}
    try:
        _training_env_cache().parent.mkdir(parents=True, exist_ok=True)
        _training_env_cache().write_text(json.dumps({"stamp": stamp, "values": vals}, ensure_ascii=False), "utf-8")
    except OSError:
        pass
    return {**base, **vals}


def _aet(ds, today: dt.date) -> dict:
    try:
        from backend.engine.thresholds import estimate
        from backend.engine.zones import training_targets
        from backend.engine.wko5expr.dataset import date_to_day
        est = estimate(ds, today)
        tt = training_targets(ds, int(math.floor(date_to_day(today))),
                              (est.get("lthr") or {}).get("value"), (est.get("aethr") or {}).get("value"))
        return {"aet": tt.get("aet"), "source": tt.get("aet_source"), "lthr": tt.get("lthr")}
    except Exception as e:                  # noqa: BLE001
        return {"aet": None, "source": f"無法估算（{str(e)[:60]}）", "lthr": None}


# ---- thresholds and intensity as of a date (no future values) ------------------

INTENSITY_KEY = "racepower_intensity_v1"
_est_memo: dict = {}
_cp_memo: dict = {}
CP_ASOF_BACK_DAYS = 30          # 推估: an invalid PD fit on a day → the last valid fit up to 30 days earlier


def _pd_mftp(ds, day: dt.date) -> Optional[float]:
    key = (id(ds), "pd", day)
    # a Dataset may keep these refits on disk (FitFolderDataset.pd_memo, keyed
    # on everything the day's fit reads): a restart / a sync then refits only
    # the days whose 90-day window changed
    disk = getattr(ds, "pd_memo", None)
    if key not in _cp_memo and disk is not None:
        hit = disk.get(day)
        if hit is not disk.MISS:
            _cp_memo[key] = hit
    if key not in _cp_memo:
        from backend.engine.wko5expr.dataset import date_to_day
        tday = date_to_day(day)
        runs = [w for w in ds.workouts if w.sport == "run" and tday - CP_WINDOW_DAYS < w.day < tday + 1]
        try:
            # this CP only locates the Friel window of the LTHR estimate
            # (thresholds.estimate, an HR threshold): the power the models use
            # when the window has any (a junk watch file, e.g. a TP car ride
            # read as an 899 W "run", must not break the fit), else every power — before
            # a Stryd the history may have watch power only, measured
            # against a CP from the same watch power (推估)
            pw = [w for w in runs if power_ok(ds, w) and power_source(ds, w) != "none"]
            pdm = pd_model(ds, day, pw, None) if pw else pd_model(ds, day, runs, None, any_power=True)
        except Exception:                   # noqa: BLE001
            pdm = None
        _cp_memo[key] = pdm["mftp"] if pdm else None
        if disk is not None:
            disk.put(day, _cp_memo[key])
    return _cp_memo[key]


def cp_as_of(ds, day: dt.date) -> Optional[float]:
    """The running CP known on `day`, never a later value: a plan CP test
    dated on or before `day`, else WKO5's PD model refitted on the 90-day
    mean-max up to `day` (pd_model, the port that reproduces WKO5's mFTP),
    else the last valid refit of the 30 days before. Used by the back-test's
    thresholds (thresholds.estimate measures each run against it). Falls
    back to watch-estimated power only in a window without usable power
    (the one exception, see _pd_mftp)."""
    key = (id(ds), "cp", day)
    if key not in _cp_memo:
        c = _plan_last(ds, "cp", day)
        v = float(c[1]) if c else None
        for i in range(CP_ASOF_BACK_DAYS + 1):
            if v is not None:
                break
            v = _pd_mftp(ds, day - dt.timedelta(days=i))
        _cp_memo[key] = v
    return _cp_memo[key]


def _estimate(ds, day: dt.date) -> dict:
    key = (id(ds), day)
    if key not in _est_memo:
        from backend.engine.thresholds import estimate
        try:
            _est_memo[key] = estimate(ds, day, cp_of=lambda d: cp_as_of(ds, d))
        except Exception:                   # noqa: BLE001
            _est_memo[key] = {}
    return _est_memo[key]


def _plan_last(ds, name: str, day: dt.date):
    rows = sorted((t.date[:10], getattr(t, name)) for t in ds.plan.thresholds
                  if getattr(t, name, None) is not None and t.date[:10] <= day.isoformat())
    return rows[-1] if rows else None


def thresholds_as_of(ds, day: dt.date) -> dict:
    """LTHR / AeT / CP in effect on `day` using only what existed then: a plan
    test dated on or before `day`, else the estimate from the runs before
    `day` (thresholds.estimate, each run measured against cp_as_of its own
    date — no later CP, no WKO5 snapshot), else WKO5's dated setting; AeT
    falls back to 0.89 × LTHR (Friel Z2 top; then `aet_below`, the highest HR of
    the runs with drift < 5 %, is added to the source text as a reference). CP only from a dated plan test
    (else None: the HR decides the class)."""
    from backend.engine.racepower import intensity as I
    out = {"day": day.isoformat()}
    lt = _plan_last(ds, "lthr", day)
    est = _estimate(ds, day)
    from backend.engine.planning import threshold_row
    if lt:
        # an applied estimate says so (zones-and-thresholds.md §3.4 change 1)
        out.update(lthr=float(lt[1]), lthr_source=(threshold_row(ds.plan, "lthr", day) or {}).get("label")
                   or f"測試 {lt[0]}")
    elif (est.get("lthr") or {}).get("value"):
        out.update(lthr=float(est["lthr"]["value"]), lthr_source="自動估算（當天以前的跑步）")
    else:
        # the dataset's own dated setting: WKO5's (WKO5 source / opt-in), or for
        # a COROS / TP source the as-of estimates made at load (fitdataset.py)
        v = ds.athlete.setting_on("runthr", day)
        hist = ds.athlete.settings.get("runthr") or []
        default = bool(hist) and all(d == dt.date(1980, 1, 1) for d, _ in hist)
        out.update(lthr=v, lthr_source="WKO5 預設值（未設定）" if default
                   else ds.setting_label("runthr", "WKO5 設定") if v is not None else "未設定")
    ae = _plan_last(ds, "aethr", day)
    if ae:
        out.update(aet=float(ae[1]), aet_source=(threshold_row(ds.plan, "aethr", day) or {}).get("label")
                   or f"測試 {ae[0]}")
    elif (est.get("aethr") or {}).get("value"):
        out.update(aet=float(est["aethr"]["value"]), aet_source="自動估算（當天以前的跑步）")
    else:
        out.update(aet=None if not out["lthr"] else I.INTENSITY["aet_frac_lthr"] * out["lthr"],
                   aet_source="0.89 × LTHR（Friel Z2 上限）")
        below = (est.get("aethr") or {}).get("below")
        if below:
            # the estimate failed; the highest HR of the runs with drift < 5 % is shown as a hint
            # (text only — the easy cap stays 0.89 × LTHR)
            out.update(aet_below=float(below),
                       aet_source=out["aet_source"] + f"；參考：飄移 < 5% 的跑步最高心率 {below:.0f} bpm")
    c = _plan_last(ds, "cp", day)
    out.update(cp=float(c[1]) if c else None, cp_source=f"測試 {c[0]}" if c else None)
    return out


def _intensity_stats(ds, w) -> Optional[dict]:
    from backend.engine.racepower import intensity as I
    t = ds.channel(w.idx, "elapsedtime")
    if t is None:
        return None
    return I.stats(t, ds.channel(w.idx, "heartrate"), ds.channel(w.idx, "power"), ds.channel(w.idx, "speed"),
                   ds.channel(w.idx, "cadence"), min_kmh=RUN_MOVING_KMH if w.sport == "run" else HIKE_REST_MS * 3.6)


def intensity_stats(ds, w) -> Optional[dict]:
    return ds.cached_series(INTENSITY_KEY, w, lambda: _intensity_stats(ds, w))


def race_dates(ds) -> set[str]:
    """Season-plan race days (every kind but 百岳 / other, all priorities)."""
    out = set()
    for e in ds.plan.events:
        if e.kind in ("baiyue", "other"):
            continue
        d0 = e.start
        for i in range(max(1, int(e.days or 1))):
            out.add((d0 + dt.timedelta(days=i)).isoformat())
    return out


def is_trail(w) -> bool:
    from backend.engine.algorithms.classify import is_trail as _it
    return _it(w)


def outdoor(w) -> bool:
    return w.sport == "run" and w.sport_type not in ("indoor running", "treadmill running") \
        and "runningtreadmill" not in w.tags and "runningindoor" not in w.tags


def plan_race_runs(ds) -> dict[int, dict]:
    """{idx: matched season-plan event} for every past road / 越野賽 event
    (any priority), matched by date, kind and distance (maximal.match_events)."""
    from backend.engine.racepower import maximal as MX
    runs = [{"idx": w.idx, "date": w.entry.start.date().isoformat(), "km": w.metrics.get("distance"),
             "trail": is_trail(w)} for w in ds.workouts if outdoor(w)]
    return MX.match_events(ds.plan.events, runs)


MAXIMAL_KEY = "racepower_maximal_v3"     # v3 (2026-10-01): + elapsed_s / stopped / long-rest share (activity_tags)


def _maximal_stats(ds, w) -> Optional[dict]:
    """Threshold-independent pacing / HR numbers of one run: last-quarter
    time-weighted HR and second ÷ first half speed (moving time, kmh > 1),
    and the elapsed time (first → last sample, so a watch auto-pause counts
    as stopped) with the stopped share and the LONG-rest share
    (activity_tags.rest_spells: stops ≥ 5 min)."""
    from backend.engine import activity_tags as AT
    a = activity_arrays(ds, w)
    if a is None:
        return None
    t = a["t"]
    d = np.diff(t, prepend=t[0])
    d[~np.isfinite(d) | (d < 0) | (d > 30)] = 0.0
    mv = (np.nan_to_num(a["kmh"]) > RUN_MOVING_KMH) & (d > 0)
    cum = np.cumsum(np.where(mv, d, 0.0))
    tot = float(cum[-1]) if len(cum) else 0.0
    rs = AT.rest_spells(t, np.nan_to_num(a["kmh"]) > RUN_MOVING_KMH)
    elapsed = rs["elapsed_s"]
    if tot <= 0:
        return None
    hr = a["hr"]
    q4 = None
    if hr is not None:
        m = mv & (cum > 0.75 * tot) & np.isfinite(hr) & (hr > 40)
        if d[m].sum() > 0:
            q4 = float((hr[m] * d[m]).sum() / d[m].sum())
    h1, h2 = mv & (cum <= tot / 2), mv & (cum > tot / 2)
    v = np.nan_to_num(a["kmh"])
    s1 = float((v[h1] * d[h1]).sum() / d[h1].sum()) if d[h1].sum() > 0 else None
    s2 = float((v[h2] * d[h2]).sum() / d[h2].sum()) if d[h2].sum() > 0 else None
    return {"q4_hr": q4, "split": (s2 / s1) if s1 and s2 else None, "moving_s": tot, "elapsed_s": elapsed,
            "stopped_share": rs["stopped_share"], "rest_share": rs["rest_share"], "rest_s": rs["rest_s"]}


def maximal_stats(ds, w) -> Optional[dict]:
    return ds.cached_series(MAXIMAL_KEY, w, lambda: _maximal_stats(ds, w))


def baiyue_on(ds, day: dt.date) -> Optional[str]:
    """The season-plan 百岳 event covering `day` (its name), else None."""
    for e in getattr(ds.plan, "events", None) or []:
        if getattr(e, "kind", None) != "baiyue":
            continue
        d0 = e.start
        if d0 <= day < d0 + dt.timedelta(days=max(1, int(e.days or 1))):
            return getattr(e, "name", "") or "百岳"
    return None


def effort_stats(ds, w, th: dict) -> dict:
    """The numbers activity_tags.effort_hr reads: moving HR (intensity_stats,
    the sport's moving rule), the shares below / above the own-date AeT, and
    the elapsed time (maximal_stats) for the stopped share."""
    from backend.engine.racepower import intensity as I
    st = intensity_stats(ds, w) or {}
    ms = maximal_stats(ds, w) or {}
    sh = I.shares(st, th["aet"], th["lthr"]) if th.get("aet") and th.get("lthr") else None
    return {"hr_avg": st.get("hr_avg"), "moving_s": st.get("moving_s"), "elapsed_s": ms.get("elapsed_s"),
            "rest_share": ms.get("rest_share"), "stopped_share": ms.get("stopped_share"),
            "low_share": sh["low"] if sh else None, "above_aet": (1.0 - sh["low"]) if sh else None}


def capacity_samples(ds, runs, th_of: Optional[dict] = None, tags: Optional[list] = None,
                     tests: Optional[dict] = None, recorded: Optional[list] = None) -> dict[int, dict]:
    """{idx: {"ok", "kind", "reason", "tags", ...}} for every outdoor run: is it
    a capacity sample? (2026-10-01, user: what matters is whether the effort
    was MAXIMAL, not whether it was a race.)

    A run is a sample when its effective effort (activity_tags.merge: the
    user's mark, else the auto rule) is 全力 and its activity type is not
    測試 (tests go through the CP-test path). A user effort mark always wins:
    ≠ 全力 always excludes, 全力 always includes. Auto effort: road =
    maximal.road_maximal; trail = activity_tags.effort_hr (HR on moving time
    with the stopped share), and an auto trail sample also needs the trail
    duration rule (≥ 10 km, ≥ 90 min: maximal.trail_maximal's km / time).
    Plan races are activity type 比賽 but no longer samples by themselves.
    `tags` = activity_tags.load() rows (default: the app DB); `tests` =
    {idx: reason} of runs workout_review marks as tests. `recorded` =
    activity_tags.load_recorded() rows (default: the app DB): a watch-
    recorded RPE outranks the HR rule for the auto effort (a user mark
    outranks both)."""
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import maximal as MX
    from backend.engine.racepower import trailhr as TH
    events = plan_race_runs(ds)
    tags = AT.load() if tags is None else tags
    recorded = AT.load_recorded() if recorded is None else recorded
    tests = tests or {}
    peaks, held = [], []
    for w in ds.workouts:
        if w.sport == "run":
            st = intensity_stats(ds, w)
            pk = MX.peak_hr(st.get("hist"), st.get("hist_lo", 40), MX.MAXIMAL["hrmax_hold_s"]) if st else None
            if pk:
                peaks.append((w.day, pk))
            # the monotonicity check compares only power the models use (no watch power)
            if st and st.get("p_avg") and outdoor(w) and not is_trail(w) and power_ok(ds, w):
                held.append((w.day, st["moving_s"], st["p_avg"]))
    out = {}
    for w in runs:
        if not outdoor(w):
            continue
        ev = events.get(w.idx)
        th = (th_of or {}).get(w.idx) or thresholds_as_of(ds, w.entry.start.date())
        st = model_stats(ds, w) or {}
        ms = maximal_stats(ds, w) or {}
        km = w.metrics.get("distance")
        title = getattr(w.entry, "title", "") or ""
        es = effort_stats(ds, w, th)
        pw_ok = power_ok(ds, w)
        trail = is_trail(w)
        if trail:
            r = MX.trail_maximal({"km": km, "moving_s": st.get("moving_s"), "hr_avg": st.get("hr_avg"),
                                  "above_aet": es["above_aet"]}, th.get("lthr"), th.get("aet"), title, w.tags)
            mvs = es.get("moving_s") or st.get("moving_s")
            eff = AT.effort_hr(es, th.get("lthr"), th.get("aet"),
                               max_frac=TH.auto_max_frac(mvs / 3600.0 if mvs else None))
            rec = AT.recorded_of(recorded, w.entry.start, w.entry.file)
            eff = AT.effort_from_rpe((rec or {}).get("rpe"), es.get("rest_share"), eff, rec=rec) or eff
            long_ok = all(c["ok"] for c in r["checks"] if c["id"] in ("km", "time"))
            auto_ok = eff["effort"] == "max" and long_ok
        else:
            hrmax = MX.hrmax_observed([p for d_, p in peaks if w.day - RIEGEL_WINDOW_DAYS < d_ < w.day + 1])
            mv = st.get("moving_s") or 0.0
            longer = [p for d_, s_, p in held if w.day - RIEGEL_WINDOW_DAYS < d_ < math.floor(w.day)
                      and s_ >= MX.MAXIMAL["longer_ratio"] * mv]
            r = MX.road_maximal({"km": km, "q4_hr": ms.get("q4_hr"), "split": ms.get("split"),
                                 "peak_hr": MX.peak_hr(st.get("hist"), st.get("hist_lo", 40)),
                                 "p_avg": st.get("p_avg"),
                                 # watch-estimated power: the power check is skipped, not failed
                                 "longer_p": max(longer) if longer and mv and pw_ok else None},
                                th.get("lthr"), hrmax)
            r["hrmax"] = hrmax
            eff = AT.effort_road(r, es, th.get("aet"))
            rec = AT.recorded_of(recorded, w.entry.start, w.entry.file)
            eff = AT.effort_from_rpe((rec or {}).get("rpe"), es.get("rest_share"), eff, rec=rec) or eff
            auto_ok = eff["effort"] == "max"
        typ, typ_reason = AT.auto_type(plan_race=ev, test=tests.get(w.idx), sport=w.sport, sport_type=w.sport_type,
                                       title=title, trail=trail)
        user = AT.find(tags, w.entry.start, w.entry.file)
        tg = AT.merge({"activity_type": typ, "activity_type_reason": typ_reason, "effort": eff["effort"],
                       "effort_reason": eff["reason"]}, user)
        ue = AT.user_effort(user)
        if tg["activity_type"] == "test":
            ok, kind, why = False, "test", "活動類型是測試：走 CP 測試路徑"
        elif ue is not None:
            ok, kind = ue == "max", "user"
            why = f"你標記為「{AT.EFFORTS[ue]}」" + ("" if ok else "：不列入能力樣本")
        else:
            ok = auto_ok
            kind = "plan_race" if ev else r["kind"]
            if ok:
                why = f"自動判定全力：{eff['reason']}"
            elif eff["effort"] == "max":
                why = "全力但未達越野樣本長度：" + "；".join(
                    c["text"] for c in r["checks"] if c["id"] in ("km", "time") and not c["ok"])
            else:
                why = f"自動努力度「{AT.EFFORTS.get(eff['effort'], '?')}」：{eff['reason']}"
            if ev:
                why = f"賽季計畫的比賽「{ev['name']}」（{ev['priority'] or '-'} 級）；" + why
        out[w.idx] = {**r, "ok": bool(ok), "kind": kind, "reason": why, "rule": r["kind"], "rule_ok": r["ok"],
                      "category": "trail" if trail else (r.get("category") or "road"), "event": ev,
                      "effort": eff, "tags": tg, "user_marked": user is not None,
                      "user_race": tg["activity_type"] == "race" and tg["activity_type_overridden"],
                      "effort_stats": es, "power_source": power_source(ds, w), "power_used": pw_ok}
    ds.flush_series()
    return out


def auto_tags(ds, w) -> dict:
    """The merged activity tags of ONE dataset workout (the tag card / API):
    the auto activity type and effort with their reasons, the user's stored
    values, and the effort numbers. Runs reuse capacity_samples; hikes and
    other sports use the HR effort rule."""
    from backend.engine import activity_tags as AT
    from backend.engine import workout_review as WR
    test = None
    try:
        if w.sport == "run":
            c = WR.classify(ds, w)
            if c.get("type") in ("test_cp", "test_aet") and c.get("test_match") not in (None, "pattern", "user"):
                test = f"{c.get('type_label')}（{c.get('test_match')}）"
    except Exception:                       # noqa: BLE001
        test = None
    if outdoor(w):
        r = capacity_samples(ds, [w], tests={w.idx: test} if test else None)[w.idx]
        out = dict(r["tags"])
        out.update(effort_detail=r["effort"], capacity={"ok": r["ok"], "kind": r["kind"], "reason": r["reason"]},
                   recorded=AT.recorded_json(AT.recorded_of(AT.load_recorded(), w.entry.start, w.entry.file)))
        return out
    th = thresholds_as_of(ds, w.entry.start.date())
    es = effort_stats(ds, w, th)
    eff = AT.effort_hr(es, th.get("lthr"), th.get("aet"))
    rec = AT.recorded_of(AT.load_recorded(), w.entry.start, w.entry.file)
    eff = AT.effort_from_rpe((rec or {}).get("rpe"), es.get("rest_share"), eff, rec=rec) or eff
    typ, why = AT.auto_type(test=test, sport=w.sport, sport_type=w.sport_type,
                            title=getattr(w.entry, "title", "") or "", trail=is_trail(w),
                            baiyue_event=baiyue_on(ds, w.entry.start.date()))
    out = AT.merge({"activity_type": typ, "activity_type_reason": why, "effort": eff["effort"],
                    "effort_reason": eff["reason"]}, AT.user_of(w))
    out["effort_detail"] = eff
    out["recorded"] = AT.recorded_json(rec)
    return out


def _test_reason(ds, w) -> Optional[str]:
    from backend.engine import workout_review as WR
    try:
        if w.sport == "run":
            c = WR.classify(ds, w)
            if c.get("type") in ("test_cp", "test_aet") and c.get("test_match") not in (None, "pattern", "user"):
                return f"{c.get('type_label')}（{c.get('test_match')}）"
    except Exception:                       # noqa: BLE001
        return None
    return None


def auto_tags_all(ds) -> dict[int, dict]:
    """{idx: {activity_type, activity_type_reason, effort, effort_reason}} —
    the AUTO values only (no user mark) of every dataset workout, the same
    rules as auto_tags in one pass (the 活動編輯 list's filters). Memoised on
    the Dataset (a rebuilt Dataset recomputes)."""
    from backend.engine import activity_tags as AT
    recorded = AT.load_recorded()
    stamp = (len(recorded), sum(r.get("rpe") or 0 for r in recorded), sum(r.get("feel") or 0 for r in recorded))
    memo = getattr(ds, "_activity_auto", None)
    if memo is not None and memo[0] == stamp:        # a backfilled RPE recomputes
        return memo[1]
    runs = [w for w in ds.workouts if outdoor(w)]
    tests = {w.idx: t for w in runs if (t := _test_reason(ds, w))}
    caps = capacity_samples(ds, runs, tags=[], tests=tests, recorded=recorded) if runs else {}
    out: dict[int, dict] = {}
    for w in ds.workouts:
        c = caps.get(w.idx)
        if c is not None:
            tg = c["tags"]
            out[w.idx] = {"activity_type": tg["activity_type_auto"], "activity_type_reason": tg["activity_type_reason"],
                          "effort": tg["effort_auto"], "effort_reason": tg["effort_reason"]}
            continue
        try:
            th = thresholds_as_of(ds, w.entry.start.date())
            es = effort_stats(ds, w, th)
            eff = AT.effort_hr(es, th.get("lthr"), th.get("aet"))
        except Exception:                   # noqa: BLE001 — one bad file never breaks the list
            es, eff = {}, {"effort": None, "reason": "無法計算"}
        rec = AT.recorded_of(recorded, w.entry.start, w.entry.file)
        eff = AT.effort_from_rpe((rec or {}).get("rpe"), es.get("rest_share"), eff, rec=rec) or eff
        typ, why = AT.auto_type(test=_test_reason(ds, w), sport=w.sport, sport_type=w.sport_type,
                                title=getattr(w.entry, "title", "") or "", trail=is_trail(w),
                                baiyue_event=baiyue_on(ds, w.entry.start.date()))
        out[w.idx] = {"activity_type": typ, "activity_type_reason": why, "effort": eff["effort"],
                      "effort_reason": eff["reason"]}
    flush = getattr(ds, "flush_series", None)
    if flush:
        flush()
    try:
        ds._activity_auto = (stamp, out)
    except AttributeError:
        pass
    return out


def _cp_req(ds, w) -> Optional[float]:
    """The CP this run alone proves: max over its mean-max points ≥ 20 min of
    p·(TTE/t)^k (difficulty.cp_lower_bound with k −0.07, TTE 3000 s, no W′)."""
    c = _curve(ds, w)
    if not c or not c[0]:
        return None
    best = None
    for x, y in zip(c[0], c[1]):
        if y is None or x is None or x < DF.SHORT_MAX_S or not y > 0:
            continue
        v = y * (DEFAULT_TTE_S / x) ** DEFAULT_K
        best = v if best is None or v > best else best
    return best


def cp_floor_by_date(ds, runs) -> dict[int, Optional[float]]:
    """{idx: the CP lower bound from the runs of the 365 days BEFORE that
    run} — a CP every later model must meet. Used only to read the power
    side of the intensity class: IF against a lower bound overstates IF, so
    "below 80 % even against the floor" is solid evidence of an easy run."""
    reqs = sorted(((w.day, _cp_req(ds, w)) for w in ds.workouts if w.sport == "run"), key=lambda x: x[0])
    out = {}
    for w in runs:
        vs = [v for d, v in reqs if v and w.day - RIEGEL_WINDOW_DAYS < d < math.floor(w.day)]
        out[w.idx] = max(vs) if vs else None
    return out


def classify_runs(ds, runs, cp_of: Optional[dict] = None, tags: Optional[list] = None) -> dict[int, dict]:
    """{idx: intensity.classify(...)} with each activity's own-date
    thresholds (thresholds_as_of) — never later values. CP: a dated plan
    test, else the lower bound from the earlier runs (cp_floor_by_date).
    A run is a plan race only when it is the activity matched to a past
    season-plan event (plan_race_runs: date + kind + distance), not every run
    that day. Each class carries `capacity`: whether the run is a capacity
    sample (capacity_samples; maximal.py) — the HR class "race" is only the
    terrain stratum."""
    from backend.engine.racepower import intensity as I
    races = plan_race_runs(ds)
    floor = cp_floor_by_date(ds, runs) if cp_of is None else {}
    out, th_of = {}, {}
    for w in runs:
        d = w.entry.start.date()
        th = thresholds_as_of(ds, d)
        th_of[w.idx] = th
        cp = (cp_of or {}).get(w.idx) or th["cp"]
        is_floor = cp is None and floor.get(w.idx) is not None
        cp = cp or floor.get(w.idx)
        c = I.classify(model_stats(ds, w), th["lthr"], th["aet"], cp, w.idx in races, is_floor)
        c.update(lthr_source=th["lthr_source"], aet_source=th["aet_source"],
                 cp_source=th["cp_source"] or ("之前跑步的 CP 下限" if cp else None))
        if w.idx in races:
            c["event"] = races[w.idx]
        out[w.idx] = c
    caps = capacity_samples(ds, runs, th_of, tags=tags)
    for i, c in out.items():
        c["capacity"] = caps.get(i)
    ds.flush_series()
    return out


def capacity_idx(classes: dict) -> set:
    """Workout idx of the capacity samples among classify_runs() results."""
    return {i for i, c in classes.items() if (c.get("capacity") or {}).get("ok")}


def enforce_lower_bound(d: dict, cp: float, w_prime: Optional[float], tte: float, k: float,
                        cp2: Optional[float] = None) -> tuple[float, Optional[dict]]:
    """The CP actually used must cover the envelope for the k actually used
    (derive's bound is for its default k; a steeper table k needs a higher
    anchor). Returns (cp to use, the bound)."""
    pts = (d.get("cp") or {}).get("lb_points") or []
    b = DF.cp_lower_bound(pts, w_prime, tte, k, cp2=cp2,
                          min_s=max(DF.SHORT_MAX_S, tte) if cp2 else DF.SHORT_MAX_S)
    if b and cp < b["cp_min"] - 0.5:
        return b["cp_min"], b
    return cp, b


def cptest_prior(weight: float, sex: str) -> dict:
    from backend.engine.racepower import cptest as T
    return T.w_prime_prior(weight, sex)


def pd_model(ds, today: dt.date, runs_90, ref_cp: Optional[float], any_power: bool = False) -> Optional[dict]:
    """WKO5's default PD model (algorithms/wko5_pdmodel, reproduces WKO5's own
    mFTP 175.7 vs snapshot 175.6 W; docs/research/cp-test-protocols.md §1B.2)
    refitted on the raw 90-day mean-max of the runs (implausible power
    dropped) plus the synced running FIT files not yet in WKO5 (cptest.curves),
    as of `today`. Returns mFTP, TTE, FRC and the curve's source. Watch-
    estimated power (runs and FIT files) is left out unless `any_power` or
    power.accept_watch_power."""
    from backend.engine.algorithms import wko5_pdmodel as PDM
    from backend.engine.racepower import cptest as T
    accept = any_power or getattr(ds, "accept_watch_power", True)
    # WKO5's own duration grid (the PD fit is sensitive to point spacing; on
    # this grid the port reproduces WKO5's mFTP): the longest cached curve's xs
    curves_ = []
    for w in runs_90:
        c = _curve(ds, w, any_power=any_power)
        if c and c[0]:
            xs = np.array(c[0], float)
            ys = np.array([np.nan if v is None else v for v in c[1]], float)
            ok_ = np.isfinite(ys)
            if ok_.sum() >= 2 and not implausible(w, xs[ok_], ys[ok_], ref_cp):
                curves_.append((xs[ok_], ys[ok_]))
    if not curves_:
        return None
    grid = max((c[0] for c in curves_), key=len)
    best = np.full(len(grid), np.nan)
    for xs, ys in curves_:
        m = grid <= xs[-1]
        vals = np.interp(grid[m], xs, ys)
        cur = best[m]
        best[m] = np.where(np.isfinite(cur), np.maximum(cur, vals), vals)
    extra = []
    try:
        extra = T.curves(WX.home(), today - dt.timedelta(days=CP_WINDOW_DAYS - 1), today, accept_watch=accept)
    except Exception:                       # noqa: BLE001
        extra = []
    for c in extra:
        xs, ys = np.array(c["xs"]), np.array(c["ys"])
        if len(xs) < 2:
            continue
        if ref_cp and ((xs[-1] >= 300 and np.interp(300.0, xs, ys) > 2.0 * ref_cp)):
            continue
        m = grid <= xs[-1]
        vals = np.interp(grid[m], xs, ys)
        cur = best[m]
        best[m] = np.where(np.isfinite(cur), np.maximum(cur, vals), vals)
    ok = np.isfinite(best)
    pts = [(float(x), float(y)) for x, y in zip(grid[ok], best[ok])]
    try:
        f = PDM.fit(pts)
    except Exception:                       # noqa: BLE001
        f = None
    if not f or not f.get("valid"):
        return None
    # "tte" = where the model falls to mFTP (tte_solve) — the value WKO5 shows
    # and stores (1895 vs the snapshot's 1897 s on WKO5's data alone)
    return {"mftp": float(f["FTP"]), "tte": float(f["tte"]), "frc": float(f["FRC"]), "pmax": float(f["Pmax"]),
            "d": float(f["D"]), "fit_files": [c["path"] for c in extra], "n_points": len(pts),
            "source": "wko5_pdmodel（WKO5 5.0.587 PD 模型的移植，已對 WKO5 快照驗證）"}


def cp_tests(ds, today: dt.date, weight: float, sex: str) -> list[dict]:
    """3′/12′ tests in the synced FIT files dated within 365 days up to today,
    each with its estimate (cptest.estimate). Suggestions only."""
    from backend.engine.racepower import cptest as T
    try:
        found = T.scan(WX.home(), today - dt.timedelta(days=RIEGEL_WINDOW_DAYS), today,
                       accept_watch=getattr(ds, "accept_watch_power", True))
    except Exception:                       # noqa: BLE001
        return []
    out = []
    for t in found:
        e = T.estimate(t, weight, sex)
        if e.get("cp"):
            age = (today - dt.date.fromisoformat(t["date"])).days
            out.append({**t, "estimate": e, "age_days": age, "fresh": age <= PLAN_CP_MAX_AGE_DAYS})
    return out


def _power_label(prof: dict) -> str:
    """The 一般設定 power source as shown on the race-power page; 未設定 when
    the runner never picked one (the data decides then, engine/power_source.py)."""
    from backend.engine import athlete_profile as AP
    s = AP.profile_power_source(prof)
    return AP.POWER_LABEL[s] if s else "未設定"


def body_profile(ds, today: dt.date) -> dict:
    """Height / sex / age for the 百岳 daily REE (fuel.mifflin_ree): the
    settings-page profile first, then the WKO5 athlete file (height setting,
    3001/3017 sex, 3001/3033 birthday); missing = None (fuel uses labelled
    推估 defaults)."""
    prof = getattr(ds.plan, "profile", None) or {}
    ath = getattr(ds, "athlete", None)
    out = {"height_cm": prof.get("height_cm"), "sex": prof.get("sex"), "age": None,
           "height_cm_src": "設定頁" if prof.get("height_cm") else None, "sex_src": "設定頁" if prof.get("sex") else None,
           "age_src": None}
    from backend.engine import athlete_profile as AP
    if AP.age(prof, today) is not None:
        out["age"], out["age_src"] = AP.age(prof, today), "設定頁（出生年）"
    try:
        root = ath.root.get(3001) if ath is not None and getattr(ath, "root", None) is not None else None
        if out["height_cm"] is None and ath is not None:
            h = (ath.settings.get("height") or [(None, None)])[-1][1]
            if h:
                out["height_cm"], out["height_cm_src"] = round(float(h) * 100.0), "WKO5"
        if root is not None:
            if out["sex"] is None and root.get(3017) in ("male", "female"):
                out["sex"], out["sex_src"] = root.get(3017), "WKO5"
            b = root.get(3033)
            if out["age"] is None and isinstance(b, str) and len(b) >= 10:
                bd = dt.date.fromisoformat(b[:10])
                out["age"] = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                out["age_src"] = "WKO5 生日"
    except (AttributeError, TypeError, ValueError, IndexError):
        pass
    return out


def derive(ds, today: Optional[dt.date] = None, fetch_weather: bool = True,
           exclude: Optional[set] = None, strict_as_of: bool = False,
           classes: Optional[dict] = None, hiking: bool = True) -> dict:
    """`exclude` = workout idx never used (racepower v2 back-test,
    leave-one-out). `strict_as_of` (back-test): nothing that only exists
    today — no WKO5 snapshot values (mFTP / TTE; the PD model is refitted on
    the data up to `today` instead), plan tests only up to `today`.
    `classes` = precomputed classify_runs()."""
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.files.wko5_athlete import pd_snapshot
    today = today or dt.date.today()
    tday = date_to_day(today)
    weight = ds.setting("weight", tday)
    weight_src = "賽季計畫體重" if ds.plan.weight_on(today) is not None else ds.setting_label("weight", "WKO5 設定")
    prof = ds.plan.profile or {}
    exclude = exclude or set()
    runs_365 = [w for w in ds.workouts if w.sport == "run" and tday - RIEGEL_WINDOW_DAYS < w.day <= tday + 1
                and w.idx not in exclude]
    runs_90 = [w for w in runs_365 if w.day > tday - CP_WINDOW_DAYS]
    metrics = run_metrics(ds, runs_365, weight)
    by_idx = {w.idx: w for w in ds.workouts}

    # ---- intensity classes (own-date thresholds) -------------------------------
    cls = classes if classes is not None else classify_runs(ds, runs_365)
    # personal k and the table prior only from capacity samples (plan races,
    # self-paced maximal efforts, race-like trail efforts — maximal.py), not
    # the HR race-like class (hard 5 km training runs, 2026-10-01)
    race_idx = capacity_idx(cls)

    # ---- CP sources ----------------------------------------------------------
    pd = {} if strict_as_of else pd_snapshot(ds.athlete.root)
    from backend.engine import cp_protocols as CPP
    sex, sex_src = CPP.sex_or_default(ds)
    plan_cp = _plan_cp(ds, today)
    tests = cp_tests(ds, today, weight, sex)
    test = tests[-1] if tests else None
    ref_cp = (plan_cp or {}).get("cp") or (test or {}).get("estimate", {}).get("cp") or pd.get(("mftp", "Run"))
    training = _training_conditions(ds, runs_90, metrics, today, fetch_weather)
    dropped: list = []
    env90 = envelope(ds, [w for w in runs_90 if w.idx in metrics], ref_cp, dropped, training, metrics)
    table = {x: {"p": y, **act_ref(by_idx[i])} for x, y, i in zip(env90["xs"], env90["ys"], env90["who"])}
    pts = CP.envelope_points(table)
    fit = CP.fit_cp([(p["t"], p["p"]) for p in pts])
    wind_any = any(m.get("air_any") for m in metrics.values())
    acts = None
    if fit:
        checks = CP.validity(pts, envelope=True)
        acts = {**fit, "points": pts, "checks": checks,
                "rating": CP.rwc_rating(fit["w_prime"], weight, sex, wind_any),
                "window_days": CP_WINDOW_DAYS,
                "dates_ok": all(c["ok"] for c in checks if c["id"] == "dates"),
                "errors_ok": all(c["ok"] for c in checks if c["level"] == "error")}
    tte = pd.get(("tte", "Run"))
    tte_src = "WKO5 模型 TTE" if tte else (
        "回測：預設 50 分鐘（不用今天的 WKO5 TTE，沒有當時的 TTE 測試）" if strict_as_of else "預設 50 分鐘（試算表預設）")
    tte_v = tte or DEFAULT_TTE_S

    # ---- Riegel -------------------------------------------------------------
    dropped365: list = []
    env365 = envelope(ds, [w for w in runs_365 if w.idx in metrics], ref_cp, dropped365, training, metrics)
    raw = R.personal_k(env365["xs"], env365["ys"], tte_v, keep_frac=None)
    # personal k only from near-maximal efforts: the race-like class
    env_race = envelope(ds, [w for w in runs_365 if w.idx in metrics and w.idx in race_idx], ref_cp, None,
                        training, metrics)
    pk = R.personal_k(env_race["xs"], env_race["ys"], tte_v)
    riegel = None
    if pk or raw:
        why = []
        span_who = []
        if pk:
            lo, hi = pk["t_min"], pk["t_max"]
            span_who = sorted({i for x, i in zip(env_race["xs"], env_race["who"]) if lo <= x <= hi})
            if not R.is_reasonable_k(pk["k"]):
                why.append("k 不在 −0.25…−0.01 的合理範圍")
            if pk["n"] < 5:
                why.append("擬合點少於 5 個")
            if pk["r2"] < 0.8:
                why.append("R² < 0.8")
        if len(span_who) < 3:
            why.append(f"全力努力（比賽／自配速全力）的長時間努力只有 {len(span_who)} 次，至少要 3 次")
        base = pk or {"k": None, "r2": None, "n": 0, "t_min": None, "t_max": None}
        riegel = {**base, "valid": not why, "invalid_reasons": why, "n_activities": len(span_who),
                  "longest_s": base.get("t_max") or (env365["xs"][-1] if env365["xs"] else None),
                  "envelope_longest_s": env365["xs"][-1] if env365["xs"] else None,
                  "uncut": None if raw is None else {k_: raw[k_] for k_ in ("k", "r2", "n", "t_max")},
                  "activities": [act_ref(by_idx[i]) for i in span_who][:40],
                  "basis": "只用能力樣本（計畫比賽、自配速全力、比賽型越野）", "window_days": RIEGEL_WINDOW_DAYS}
    k0 = riegel["k"] if riegel and riegel["valid"] else DEFAULT_K
    k0_src = "個人擬合" if riegel and riegel["valid"] else "預設 −0.07（≈ Stryd 表的 k −0.069）"

    # ---- WKO5 PD model recomputed on today's data (incl. synced FIT files) ----
    pdm = pd_model(ds, today, runs_90, ref_cp)
    # the short-range (F2) pair: a CP test (plan, else detected) with its W′
    if plan_cp and plan_cp["fresh"]:
        short = {"cp2": plan_cp["cp"], "w_prime": (test or {}).get("estimate", {}).get("w_prime") or
                 cptest_prior(weight, sex)["mid"], "label": f"計畫 CP 測試 {plan_cp['date']}"}
    elif test and test["fresh"]:
        e = test["estimate"]
        short = {"cp2": e["cp"], "w_prime": e["w_prime"], "label": f"偵測到的 CP 測試 {test['date']}（{e['label']}）"}
    elif pdm:
        short = {"cp2": pdm["mftp"], "w_prime": pdm["frc"], "label": "PD 模型 mFTP + FRC"}
    else:
        short = None

    # ---- lower bound: CP must cover what the athlete already held -----------
    env_pts = [(x, y, i) for x, y, i in zip(env365["xs"], env365["ys"], env365["who"])]
    # each run's moving-time average too: the back-test, the effort f and the
    # predictions all work on moving time, and a paused run's elapsed mean-max
    # understates what the athlete held (e.g. ~3 % lower on a long road race)
    bad = {d_["idx"] for d_ in dropped365}
    for w in runs_365:
        m_ = metrics.get(w.idx)
        if m_ and w.idx not in bad and m_.get("moving_s", 0) >= DF.SHORT_MAX_S and m_.get("avg_power"):
            env_pts.append((float(m_["moving_s"]), m_["avg_power"] * altitude_norm(m_.get("elev_median"), training),
                            w.idx))

    def bound(w_, tte_, cp2_=None):
        # two anchors: the bound applies to the F1 anchor only where F1 alone
        # decides (t ≥ TTE); between 20 min and TTE the bridge also depends on
        # the short-range pair, and a low pair would inflate the requirement
        b = DF.cp_lower_bound(env_pts, w_, tte_, k0, cp2=cp2_,
                              min_s=max(DF.SHORT_MAX_S, tte_) if cp2_ else DF.SHORT_MAX_S)
        if b:
            b["who"] = act_ref(by_idx[b["who"]]) if b.get("who") in by_idx else None
            for r_ in b["rows"]:
                r_["who"] = act_ref(by_idx[r_["who"]]) if r_.get("who") in by_idx else None
            b.update(k_source=k0_src)
        return b

    sources = []
    if pdm:
        sources.append({"id": "pdmodel", "label": f"PD 模型重算（近 {CP_WINDOW_DAYS} 天 mean-max，含同步的 FIT）：mFTP "
                        f"{pdm['mftp']:.0f} W、TTE {pdm['tte'] / 60:.0f} 分", "cp": pdm["mftp"], "tte": pdm["tte"],
                        "cp2": (short or {}).get("cp2"), "w_prime": (short or {}).get("w_prime"),
                        "short_label": (short or {}).get("label"), "frc": pdm["frc"], "fit": pdm})
    if plan_cp:
        sources.append({"id": "plan", "label": f"賽季計畫 CP 測試（{plan_cp['date']}）", "cp": plan_cp["cp"],
                        "date": plan_cp["date"], "fresh": plan_cp["fresh"]})
    if test:
        e = test["estimate"]
        sources.append({"id": "cptest", "label": f"偵測到的 CP 測試 {test['date']}（{e['label']}；尚未套用）",
                        "cp": e["cp"], "w_prime": e["w_prime"], "cp_range": e.get("cp_range"),
                        "date": test["date"], "fresh": test["fresh"], "suggestion": True})
    if pd.get(("mftp", "Run")):
        sources.append({"id": "wko5", "label": "WKO5 模型 mFTP（WKO5 存的快照）", "cp": pd[("mftp", "Run")],
                        "tte": pd.get(("tte", "Run")), "frc": pd.get(("frc", "Run")), "w_prime": pd.get(("frc", "Run"))})
    if acts:
        sources.append({"id": "activities", "label": f"近 {CP_WINDOW_DAYS} 天活動（3–20 分鐘最佳功率擬合）",
                        "cp": acts["cp"], "w_prime": acts["w_prime"], "dates_ok": acts["dates_ok"]})
    for s in sources:
        s["lower_bound"] = bound(s.get("w_prime") or (acts or {}).get("w_prime"), s.get("tte") or tte_v, s.get("cp2"))
        s["meets_lower_bound"] = s["lower_bound"] is None or s["cp"] >= s["lower_bound"]["cp_min"] - 0.5
    by_id = {s["id"]: s for s in sources}
    # PD model (mFTP / TTE anchor + test pair; raised to the lower bound when
    # below it). Without a PD fit: plan test → detected test → WKO5 snapshot →
    # activities (its 14-day check passing), each only when it covers the
    # lower bound; else the preferred source raised to its bound
    order = [("pdmodel", lambda s: True), ("plan", lambda s: s["fresh"]), ("cptest", lambda s: s["fresh"]),
             ("wko5", lambda s: True), ("activities", lambda s: s["dates_ok"])]
    pref = next((by_id[sid] for sid, cond in order if sid in by_id and cond(by_id[sid])), sources[0] if sources else None)
    if pref and pref["id"] == "pdmodel":
        # the mFTP / TTE anchor stays the model; below the bound it is raised to it
        default_cp = "pdmodel" if pref["meets_lower_bound"] else None
    else:
        default_cp = next((sid for sid, cond in order if sid in by_id and cond(by_id[sid])
                           and by_id[sid]["meets_lower_bound"]), None)
    lb = (pref or {}).get("lower_bound")
    lb_msg = None
    if pref and not pref["meets_lower_bound"] and lb:
        lb_msg = (f"模型 CP 低於你實際撐過的功率（{lb['t_s'] / 60:.0f} 分鐘 {lb['p']:.0f} W）："
                  f"CP 至少 ≥ {lb['cp_min']:.0f} W，請重測")
    if default_cp is None and pref and lb:
        sources.append({**{k_: v for k_, v in pref.items() if k_ not in ("id", "label", "fit")},
                        "id": "lower_bound", "cp": lb["cp_min"], "meets_lower_bound": True, "base": pref["id"],
                        # a short-range CP that only mirrors mFTP follows the raised anchor
                        **({"cp2": lb["cp_min"]} if pref.get("cp2") and pdm and short and
                           short.get("label", "").startswith("PD 模型") and pref["cp2"] < lb["cp_min"] else {}),
                        "label": f"下限：{pref['label'].split('（')[0]} 提高到你撐過的功率所需的最低值"
                                 f"（{lb['t_s'] / 60:.0f} 分鐘 {lb['p']:.0f} W，k {k0:+.2f}）"})
        default_cp = "lower_bound"
    if default_cp is None and sources:
        default_cp = sources[0]["id"]
    dsrc = next((s for s in sources if s["id"] == default_cp), None)
    if dsrc and dsrc.get("tte"):
        tte_v = dsrc["tte"]
        tte_src = "PD 模型重算的 TTE" if dsrc.get("fit") or dsrc.get("base") == "pdmodel" else "WKO5 模型 TTE"
    lb = (dsrc or {}).get("lower_bound") or lb
    # thin the envelope for charts: log-spaced points only
    chart_env = [{"t": x, "p": y, "idx": i} for x, y, i in zip(env365["xs"], env365["ys"], env365["who"])
                 if x >= 60]

    # ---- RE -------------------------------------------------------------------
    road_rows, trail_rows = [], []
    for w in runs_365:
        m = metrics.get(w.idx)
        if not m:
            continue
        km, climb = w.metrics.get("distance"), w.metrics.get("climbing")
        c = RE.cvi(climb, km)
        row = {**act_ref(w), "km": km, "climb_m": climb, "cvi": c, "moving_s": m["moving_s"],
               "avg_power": m["avg_power"], "re": m["re"], "air_pct": m["air_pct"],
               "form_pct": m["form_pct"], "lss_kg": m["lss_kg"], "trail": "runningtrail" in w.tags,
               "intensity": (cls.get(w.idx) or {}).get("cls")}
        if row["trail"]:
            if (climb or 0) >= TRAIL_MIN_CLIMB_M and m["moving_s"] >= TRAIL_MIN_MOVING_S and km:
                row["re_trail"] = {f: RE.trail_re(km, climb, m["moving_s"], m["avg_power"], weight,
                                                  divisor_of(f)) for f in ("fitted_run", "itra", "scarf")}
                trail_rows.append(row)
        elif c is not None and c < ROAD_MAX_CVI_ADJ and m["moving_s"] >= ROAD_MIN_MOVING_S \
                and w.sport_type != "indoor running" and "runningtreadmill" not in w.tags:
            # the workbook's CVI adjustment (+0.01 RE per category) brings every
            # road run to flat, so the flat RE rests on the whole year instead
            # of the few runs under 25 ft/mi
            row["cvi_adj"] = RE.cvi_adjust(c, 0.0)
            row["re_flat"] = m["re"] + row["cvi_adj"]
            road_rows.append(row)
    road = RE.summary([r["re_flat"] for r in road_rows])
    if road:
        road["basis"] = f"{len(road_rows)} 次路跑（CVI < {ROAD_MAX_CVI_ADJ:g}），各自用 CVI 調整到平路"
    road_cvi = {"median": 0.0, "n": len(road_rows), "note": "RE 已調到平路（CVI 0）"} if road_rows else None
    trail = {f: RE.summary([r["re_trail"][f] for r in trail_rows]) for f in ("fitted_run", "itra", "scarf")}
    train_cvi = RE.summary([RE.cvi(w.metrics.get("climbing"), w.metrics.get("distance"))
                            for w in runs_90 if w.metrics.get("distance")])

    # ---- hiking ---------------------------------------------------------------
    try:
        hdays = hiking_days(ds, today) if hiking else []
    except Exception as e:                  # noqa: BLE001
        hdays, hike_err = [], str(e)[:120]
    else:
        hike_err = None
    # group hikes are paced by the group: only opted-in solo days calibrate EP/h
    solo_days = [d for d in hdays if d.get("solo")]
    eph = RE.summary([d["ep_per_h"] for d in solo_days],
                     [HIKE_HEAVY_WEIGHT if d["gain_m"] >= HIKE_HEAVY_GAIN_M else 1.0 for d in solo_days]) \
        if solo_days else None
    biggest = max(hdays, key=lambda d: d["ep"]) if hdays else None

    # ---- prior-race candidates (standard distances) -----------------------------
    priors = []
    for w in runs_365:
        m, km = metrics.get(w.idx), w.metrics.get("distance")
        if not m or not km or "runningtrail" in w.tags:
            continue
        cat = R.distance_category(km * 1000.0)
        if cat:
            c_ = cls.get(w.idx) or {}
            priors.append({**act_ref(w), "km": km, "category": cat, "time_s": w.metrics.get("movingduration") or m["moving_s"],
                           "avg_power": m["avg_power"], "intensity": c_.get("cls"), "race": w.idx in race_idx})
    priors.sort(key=lambda r: r["date"], reverse=True)
    # the Riegel table encodes "slower racers fade more": only a real race
    # (a capacity sample: plan race or self-paced maximal) may be the table prior —
    # a training run would be circular. None → the API uses k −0.07.
    races_ = [p for p in priors if p["race"]]
    auto_prior = max(races_, key=lambda r: r["km"] * 1000.0 / r["time_s"]) if races_ else None

    events = []
    for e in sorted(ds.plan.events, key=lambda e: e.date):
        if e.end < today:
            continue
        pk_ = WX.find_peak(e.name)
        events.append({"id": e.id, "name": e.name, "date": e.date, "kind": e.kind, "days": e.days,
                       "distance_km": e.distance_km, "climbing_m": e.climbing_m, "est_hours": e.est_hours,
                       "priority": e.priority, "peak": pk_,
                       # SP-114: the per-day numbers fill the calculator's 逐日行程; 連續 races get sleep points
                       "day_plan": getattr(e, "day_plan", None), "race_format": getattr(e, "race_format", None)})

    return {
        "today": today.isoformat(),
        "weight": {"value": weight, "source": weight_src},
        "body": body_profile(ds, today),
        "profile": {"sex": sex, "sex_source": sex_src,
                    "power_meter": _power_label(prof), "wind": wind_any,
                    "wind_source": "資料中有 Stryd air power" if wind_any else "資料中沒有 air power"},
        "cp": {"sources": sources, "default": default_cp, "activities": acts, "plan": plan_cp,
               "dropped": dropped, "dropped_365": dropped365, "ref_cp": ref_cp,
               "lower_bound": lb, "lower_bound_message": lb_msg, "tests": tests, "pd_model": pdm, "short": short,
               "lb_points": [[x, y, i] for x, y, i in env_pts if x >= DF.SHORT_MAX_S],
               "spread": sorted({round(s["cp"], 1) for s in sources if s.get("cp")} |
                                ({round(lb["cp_min"], 1)} if lb else set()))},
        "tte": {"value": tte_v, "source": tte_src},
        "k_default": {"value": k0, "source": k0_src},
        "intensity": {"counts": {c: sum(1 for x in cls.values() if x.get("cls") == c) for c in ("easy", "steady", "race")},
                      "unknown": sum(1 for x in cls.values() if not x.get("cls"))},
        "wko5": {"mftp": pd.get(("mftp", "Run")), "tte": tte, "frc": pd.get(("frc", "Run")),
                 "pmax": pd.get(("pmax", "Run")), "stamina": pd.get(("stamina", "Run"))},
        "riegel": riegel, "envelope": chart_env,
        "re": {"road": road, "road_cvi": road_cvi, "train_cvi": train_cvi, "trail": trail,
               "road_runs": road_rows[-60:], "trail_runs": trail_rows,
               "window_days": RE_WINDOW_DAYS},
        "hiking": {"eph": eph, "days": hdays, "biggest": biggest, "error": hike_err,
                   "window_days": HIKE_WINDOW_DAYS, "solo_n": len(solo_days),
                   "group_n": len(hdays) - len(solo_days), "note": GROUP_HIKE_NOTE},
        "priors": priors[:40], "auto_prior": auto_prior,
        "training_conditions": training,
        "altitude_normalised": True,
        "aet": _aet(ds, today),
        "events": events,
        "counts": {"runs_365": len(runs_365), "runs_with_power": len(metrics), "runs_90": len(runs_90)},
        "power_source": power_summary(ds, runs_365),
    }


def power_summary(ds, runs) -> dict:
    """Which runs' power the models used: counts per source, the watch-power
    runs left out (「手錶推估功率（未採用）」) and the setting."""
    from backend.engine import power_source as PS
    accept = bool(getattr(ds, "accept_watch_power", True))
    srcs = [power_source(ds, w) for w in runs]
    return {"accept_watch_power": accept, "setting": PS.SETTING_KEY, "counts": PS.counts(s for s in srcs if s),
            "unused": watch_unused(ds, runs)[-40:], "unused_label": PS.UNUSED_LABEL,
            "note": "只用 Stryd 功率（有 Form Power／Air Power／LSS 欄位）；手錶推估功率不進功率模型" if not accept
            else "手錶推估功率也採用"}


# ---- racepower v2: per-activity samples (docs/research/racepower-v2.md §10.1) ----

# v3 (2026-10-01, baiyue-from-running.md §5.1): + window index k, cumulative
# moving seconds t and the HR read HR_LAG_S later (the consecutive-window rule,
# the hour-of-day term and the HR-lag handling need them)
GRADE_KEY = "racepower_v3_grade_hr_kt"   # rows [g, v, p, z, hr, running share, k, t, hr_lag]
HIKE_KEY = "racepower_v3_hike_hr_t"      # rows [g, v, z, hr, window index, day, t, hr_lag]
RUN_MOVING_KMH = 1.0
HIKE_REST_MS = 0.3
HR_LAG_S = 60.0                          # 推估 (baiyue-from-running.md §2.2 finding 1)


def activity_arrays(ds, w) -> Optional[dict]:
    """Sample-aligned numpy arrays of one activity: t (s), d (m, device
    distance or integrated speed), z (smoothed elevation), lat, lon, p
    (power, may be None), kmh (speed)."""
    t = ds.channel(w.idx, "elapsedtime")
    if t is None or len(t) < 10:
        return None
    n = len(t)
    s = ds.channel(w.idx, "speed")
    d = ds.channel(w.idx, "elapseddistance")
    if d is not None and np.isfinite(d).sum() > 10:
        dm = np.asarray(d, float)[:n] * 1000.0
        ok = np.isfinite(dm)
        dm = np.interp(np.arange(n), np.nonzero(ok)[0], dm[ok])
    elif s is not None:
        dt_ = np.diff(t, prepend=t[0])
        dt_[~np.isfinite(dt_) | (dt_ < 0) | (dt_ > 60)] = 0
        dm = np.cumsum(np.nan_to_num(s[:n]) / 3.6 * dt_)
    else:
        return None
    z = ds.channel(w.idx, "_elevation")
    if z is None:
        z = ds.channel(w.idx, "elevation")
    if z is None:
        return None

    def fit(a):
        if a is None:
            return None
        a = np.asarray(a, float)[:n]
        return a if len(a) == n else np.concatenate([a, np.full(n - len(a), np.nan)])
    p = fit(ds.channel(w.idx, "power"))
    kmh = fit(s) if s is not None else np.gradient(dm, t) * 3.6
    return {"t": np.asarray(t, float), "d": dm, "z": fit(z), "lat": fit(ds.channel(w.idx, "latitude")),
            "lon": fit(ds.channel(w.idx, "longitude")), "p": p, "kmh": kmh,
            "hr": fit(ds.channel(w.idx, "heartrate")), "cad": fit(ds.channel(w.idx, "cadence"))}


def _r(x, n):
    return None if x is None else round(x, n)


def _grade_windows(ds, w) -> Optional[list]:
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import intensity as I
    a = activity_arrays(ds, w)
    if a is None or a["p"] is None or not np.any(np.nan_to_num(a["p"]) > 0):
        return None
    mv = (np.nan_to_num(a["p"]) > 0) & (np.nan_to_num(a["kmh"]) > RUN_MOVING_KMH)
    rows = GM.windows(a["t"], a["d"], a["z"], a["p"], mv, hr=a["hr"], cadence=a["cad"],
                      run_cadence=I.INTENSITY["run_cadence"], hr_lag_s=HR_LAG_S)
    return [[round(r["g"], 4), round(r["v"], 3), round(r["p"], 1), round(r["z"], 1), _r(r.get("hr"), 1),
             _r(r.get("run"), 3), r["k"], round(r["t"], 1), _r(r.get("hr_lag"), 1)]
            for r in rows if r.get("p") and r["p"] > 0]


def _day_slices(w, a) -> list[tuple[int, np.ndarray]]:
    """Sample masks per calendar day (multi-day trips), day numbers from 1."""
    start = w.entry.start
    t = a["t"]
    days = np.array([(start + dt.timedelta(seconds=float(x))).date().toordinal() if np.isfinite(x) else -1
                     for x in t])
    uniq = [x for x in sorted(set(days.tolist())) if x > 0]
    return [(n, days == x) for n, x in enumerate(uniq, 1)]


def _hike_windows(ds, w) -> Optional[list]:
    from backend.engine.racepower import grade_model as GM
    a = activity_arrays(ds, w)
    if a is None:
        return None
    out = []
    for day, m in _day_slices(w, a):
        sub = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in a.items()}
        if len(sub["t"]) < 10:
            continue
        mv = np.nan_to_num(sub["kmh"]) > HIKE_REST_MS * 3.6
        rows = GM.windows(sub["t"], sub["d"], sub["z"], None, mv, hr=sub["hr"], hr_lag_s=HR_LAG_S)
        out += [[round(r["g"], 4), round(r["v"], 3), round(r["z"], 1), _r(r.get("hr"), 1), r["k"], day,
                 round(r["t"], 1), _r(r.get("hr_lag"), 1)]
                for r in rows if r["v"] >= HIKE_REST_MS]
    return out


def grade_samples(ds, runs, exclude: Optional[set] = None, power_only: bool = True) -> list[dict]:
    """100 m windows (grade, speed, power, elevation) of every outdoor run
    with power, disk-cached per activity; RE is computed with each run's
    weight. `power_only` (default): only runs whose power the models use
    (no watch-estimated power); the walking-capacity windows (speed / HR
    only) pass False."""
    from backend.engine.wko5expr.dataset import date_to_day  # noqa: F401
    exclude = exclude or set()
    out = []
    for w in runs:
        if w.idx in exclude or w.sport_type == "indoor running" or "runningtreadmill" in w.tags \
                or "runningindoor" in w.tags:
            continue
        if power_only and not power_ok(ds, w):
            continue
        rows = ds.cached_series(GRADE_KEY, w, lambda w=w: _grade_windows(ds, w))
        if not rows:
            continue
        wt = ds.setting("weight", w.day)
        trail = "runningtrail" in w.tags or w.sport_type == "trail running"
        for g, v, p, z, hr, run, k, t, hr_lag in rows:
            out.append({"g": g, "v": v, "p": p, "z": z, "re": v / (p / wt) if wt and p else None, "a": w.idx,
                        "hr": hr, "run": run, "trail": trail, "k": k, "t": t, "hr_lag": hr_lag,
                        "date": w.entry.start.date().isoformat()})
    ds.flush_series()
    return out


CLIMB_CAD_KEY = "racepower_climb_cadence_v1"   # SP-230: seconds per 5-spm bin on climbs ≥ 3 %


def _climb_cadence(ds, w) -> Optional[list]:
    from backend.engine.racepower import runwalk as RW
    a = activity_arrays(ds, w)
    if a is None or a["cad"] is None:
        return None
    mv = np.nan_to_num(a["kmh"]) > RUN_MOVING_KMH
    return RW.climb_cadence_hist(a["t"], a["d"], a["z"], a["cad"], mv)


def climb_cadence_seconds(ds, today: Optional[dt.date] = None) -> tuple[Optional[list], int]:
    """SP-230: the climbing (≥ 3 %) cadence histogram (seconds per runwalk 5-spm bin) of every
    outdoor run of the last 365 days, with or without power (the 130 spm line splits every
    run), disk-cached per activity; and how many runs had climbing with cadence."""
    from backend.engine.wko5expr.dataset import date_to_day
    today = today or dt.date.today()
    tday = date_to_day(today)
    tot = None
    n = 0
    for w in ds.workouts:
        if w.sport != "run" or not (tday - RE_WINDOW_DAYS < w.day <= tday + 1) or w.sport_type == "indoor running" \
                or "runningtreadmill" in w.tags or "runningindoor" in w.tags:
            continue
        h = ds.cached_series(CLIMB_CAD_KEY, w, lambda w=w: _climb_cadence(ds, w))
        if not h or not any(h):
            continue
        tot = np.asarray(h, float) if tot is None else tot + np.asarray(h, float)
        n += 1
    ds.flush_series()
    return (None if tot is None else [float(x) for x in tot]), n


def climb_cadence(ds, today: Optional[dt.date] = None) -> dict:
    """runwalk.cadence_check on climb_cadence_seconds, with `n_runs`."""
    from backend.engine.racepower import runwalk as RW
    secs, n = climb_cadence_seconds(ds, today)
    return {**RW.cadence_check(secs), "n_runs": n}


def hike_workouts(ds, today: dt.date) -> list:
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today)
    return [w for w in ds.workouts if is_hike(w) and w.sport != "run" and tday - HIKE_WINDOW_DAYS < w.day <= tday + 1]


def hike_samples(ds, hikes, exclude: Optional[set] = None) -> list[dict]:
    """Every moving 100 m window of the given hikes: g, v, z, hr, k (window
    index in the day), day (1…), a (workout idx)."""
    exclude = exclude or set()
    out = []
    for w in hikes:
        if w.idx in exclude:
            continue
        rows = ds.cached_series(HIKE_KEY, w, lambda w=w: _hike_windows(ds, w))
        for g, v, z, hr, k, day, t, hr_lag in rows or []:
            out.append({"g": g, "v": v, "z": z, "hr": hr, "k": k, "day": day, "a": w.idx, "t": t, "hr_lag": hr_lag})
    ds.flush_series()
    return out


def hike_hr_windows(ds, hikes, exclude: Optional[set] = None) -> tuple[list[dict], dict]:
    """hikehr.filter_windows over every hiking day with that day's AeT
    (thresholds_as_of the trip date). Returns (windows, per-trip thresholds)."""
    from backend.engine.racepower import hikehr as HH
    exclude = exclude or set()
    wins, th_of = [], {}
    for w in hikes:
        if w.idx in exclude:
            continue
        th = thresholds_as_of(ds, w.entry.start.date())
        th_of[w.idx] = th
        if not th.get("aet"):
            continue
        rows = hike_samples(ds, [w])
        for day in sorted({r["day"] for r in rows}):
            ds_ = sorted((r for r in rows if r["day"] == day), key=lambda r: r["k"])
            for x in HH.filter_windows(ds_, th["aet"]):
                wins.append({**x, "trip": w.idx, "lthr": th["lthr"], "aet": th["aet"]})
    return wins, th_of


HIKE_META = None      # fixed file (tests); None = the tenant's (private) racepower_hike_meta.json


def _hike_meta_path() -> Path:
    from backend import tenancy
    return Path(HIKE_META) if HIKE_META is not None else tenancy.private_path("racepower_hike_meta.json")


def hike_meta(path=None) -> dict:
    """Per-trip records ({file: {"pack_kg": float, "start": local start}}): the
    pack the athlete carried (baiyue-from-running.md §5.1 point 3). A
    ByStartDict (engine/activity_key.py): `.get(file)` also finds the trip
    stored under another source's file name, by start time."""
    from backend.engine.activity_key import ByStartDict
    try:
        d = json.loads((path or _hike_meta_path()).read_text("utf-8"))
        return ByStartDict({str(k): v for k, v in (d.get("trips") or {}).items() if isinstance(v, dict)})
    except (OSError, ValueError):
        return ByStartDict()


def set_hike_meta(file: str, pack_kg: Optional[float], path=None, start=None) -> dict:
    """Set / clear one trip's pack. The record of the same activity stored
    under another source's file stays the one updated; new records carry the
    local start (`start`, else the registered dataset's) so any source finds
    them."""
    from backend.engine import activity_key as AK
    p = path or _hike_meta_path()
    trips = hike_meta(p)
    key = trips.key_for(str(file), start)
    if pack_kg is None:
        trips.pop(key, None)
    else:
        if not 0 <= float(pack_kg) <= 40:
            raise ValueError(_("背負要在 0–40 kg"))
        rec = {**dict.get(trips, key, {}), "pack_kg": float(pack_kg)}
        st = AK.key_of(start) if start is not None else AK.key_of(AK.start_of_file(str(file)))
        if st and not rec.get("start"):
            rec["start"] = st
        trips[key] = rec
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"trips": dict(trips)}, ensure_ascii=False), "utf-8")
    return trips


def _trip_of(meta, w) -> Optional[dict]:
    """The hike_meta record of a workout: by file, else by its start (activity_key.py)."""
    f = getattr(w.entry, "file", None)
    if hasattr(meta, "find"):
        return meta.find(f, getattr(w.entry, "start", None))
    return meta.get(f)


def activity_pack(w, meta: Optional[dict] = None) -> dict:
    """{pack_kg (recorded), recorded, default_kg, range, file, note} of one dataset
    workout — the activity card's 「這次背多少」 (the 百岳 prediction uses it)."""
    if meta is None:
        meta = hike_meta()
    rec = (_trip_of(meta, w) or {}).get("pack_kg")
    return {"pack_kg": None if rec is None else float(rec), "recorded": rec is not None,
            "default_kg": None if rec is None else float(rec), "range": [0, 40], "file": w.entry.file,
            "note": "這次背多少（kg）：記下來，百岳預測會用；空白 = 沒記錄"}


def walk_capacity_inputs(ds, today: dt.date, exclude: Optional[set] = None, runs: Optional[list] = None,
                         hikes: Optional[list] = None) -> dict:
    """Every window the capacity model (racepower/capacity.py) is fitted on,
    before `today` and without `exclude`: trail walk windows, flat / descent
    walk windows, hike HR windows (with each trip's pack), hike descent
    windows, v_run at AeT, and the per-activity AeT."""
    from backend.engine.racepower import capacity as CAP
    from backend.engine.wko5expr.dataset import date_to_day
    exclude = exclude or set()
    tday = date_to_day(today)
    if runs is None:
        runs = [w for w in ds.workouts if w.sport == "run" and tday - RE_WINDOW_DAYS < w.day <= tday + 1]
    runs = [w for w in runs if w.day < tday + 1]
    gs = grade_samples(ds, runs, exclude, power_only=False)   # walking windows: speed / HR
    aet = {}
    for w in runs:
        aet[w.idx] = thresholds_as_of(ds, w.entry.start.date()).get("aet")
    hikes = hike_workouts(ds, today) if hikes is None else hikes
    hikes = [w for w in hikes if w.idx not in exclude and w.day < tday + 1]
    hr_wins, th_of = hike_hr_windows(ds, hikes, exclude)
    for w in hikes:
        aet[w.idx] = (th_of.get(w.idx) or {}).get("aet")
    meta = hike_meta()
    by_idx = {w.idx: w for w in hikes}
    days_of: dict = {}
    for x in hr_wins:
        days_of[x["trip"]] = max(days_of.get(x["trip"], 1), x.get("day") or 1)
    all_h = hike_samples(ds, hikes, exclude)
    for x in all_h:
        days_of[x["a"]] = max(days_of.get(x["a"], 1), x.get("day") or 1)
    body_w = ds.setting("weight", tday)

    def pack_of(trip):
        w = by_idx.get(trip)
        rec = _trip_of(meta, w) if w is not None else None
        if rec and rec.get("pack_kg") is not None:
            return float(rec["pack_kg"])
        return CAP.pack_default(body_w, days_of.get(trip, 1))
    lo = (today - dt.timedelta(days=CAP_AET_DAYS)).isoformat()
    vr = CAP.run_speed_at_aet(gs, aet.get, lo)
    packs = {w.idx: pack_of(w.idx) for w in hikes}
    recorded = {w.idx for w in hikes if (_trip_of(meta, w) or {}).get("pack_kg") is not None}
    return {"trail": CAP.trail_walk_windows(gs), "flat": CAP.flat_walk_windows(gs),
            "hike": CAP.hike_steep_windows(hr_wins), "hike_all": all_h,
            "hike_down": [x for x in all_h if x["g"] <= CAP.DOWN_CAP_G], "v_run": vr, "aet": aet,
            "pack_of": pack_of, "packs": packs, "packs_recorded": recorded, "hr_wins": hr_wins,
            "weight": ds.setting("weight", tday)}


CAP_AET_DAYS = 90


def walk_capacity(ds, today: Optional[dt.date] = None, exclude: Optional[set] = None, tech: float = 1.0,
                  inputs: Optional[dict] = None, boot_reps: int = 400, sigma_loo=None):
    from backend.engine.racepower import capacity as CAP
    today = today or dt.date.today()
    x = inputs or walk_capacity_inputs(ds, today, exclude)
    cap = CAP.fit_walk_capacity(weight=x["weight"], v_run=x["v_run"], trail=x["trail"], hike=x["hike"],
                                flat=x["flat"], hike_down=x["hike_down"], aet_of=x["aet"].get,
                                pack_of=x["pack_of"], tech=tech, boot_reps=boot_reps, sigma_loo=sigma_loo)
    cap.basis.update(packs_recorded=len(x["packs_recorded"]), packs_default=len(x["packs"]) - len(x["packs_recorded"]),
                     pack_default_rule=f"沒填的趟：{CAP.pack_default_text(x['weight'])}（預設背負）")
    return cap


def grade_models(ds, today: Optional[dt.date] = None, re_flat: Optional[float] = None,
                 exclude: Optional[set] = None, runs: Optional[list] = None,
                 classes: Optional[dict] = None, only_classes: Optional[set] = None,
                 hikes: bool = True) -> dict:
    """Gait-aware RE(g) (GaitRE) from the 365-day runs, and the walking
    model HikeSpeed from solo hikes + the HR-filtered steep windows of every
    hike (group hikes contribute only those), all before `today` and without
    `exclude`. `only_classes` restricts the runs to those intensity classes
    (per-class fit); `classes` = classify_runs()."""
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import hikehr as HH
    from backend.engine.wko5expr.dataset import date_to_day
    today = today or dt.date.today()
    tday = date_to_day(today)
    if runs is None:
        runs = [w for w in ds.workouts if w.sport == "run" and tday - RE_WINDOW_DAYS < w.day <= tday + 1]
    if classes is None:
        classes = classify_runs(ds, runs)
    cmap = {i: c.get("cls") for i, c in classes.items()}
    if only_classes:
        runs = [w for w in runs if cmap.get(w.idx) in only_classes]
    gs = grade_samples(ds, runs, exclude)
    if re_flat is None:
        flat = [s["re"] for s in gs if s["re"] and abs(s["g"]) <= 0.01 and (s["run"] is None or s["run"] >= 0.5)]
        re_flat = float(median(flat)) if flat else 1.0
    if not hikes:
        return {"grade_re": GM.fit_gait_re(gs, re_flat, cmap), "classes": cmap}
    hikes = hike_workouts(ds, today)
    solo = solo_hikes()
    solo_w = [w for w in hikes if w.entry.file in solo]
    hs_solo = hike_samples(ds, solo_w, exclude)
    hr_wins, _ = hike_hr_windows(ds, hikes, exclude)
    solo_idx = {w.idx for w in solo_w}
    steep = [{"g": x["g"], "v": x["v"], "z": x["z"], "a": x["trip"]} for x in hr_wins if x["trip"] not in solo_idx]
    th = thresholds_as_of(ds, today)
    n_days = len({(x["trip"], x["day"]) for x in hr_wins})
    try:
        cap = walk_capacity(ds, today, exclude)
    except Exception:                       # noqa: BLE001 — the planner falls back to HikeSpeed
        import traceback
        traceback.print_exc()
        cap = None
    return {"grade_re": GM.fit_gait_re(gs, re_flat, cmap), "hike_speed": GM.fit_hike_speed(hs_solo + steep),
            "walk_capacity": cap,
            "hike_hr": HH.summary(hr_wins, th.get("aet"), th.get("lthr"), n_days),
            "hike_basis": {"solo_hikes": len(solo_w), "solo_windows": len(hs_solo), "steep_hr_windows": len(steep),
                           "group_hikes": len(hikes) - len(solo_w), "note": GROUP_HIKE_NOTE},
            "classes": cmap}


TRAILHR_DUR_KEY = "racepower_trailhr_dur_v4"   # v3 (2026-10-02): terrain-matched within-run δ ± SE; v4: + fade (SP-222)


def durability_clean_mask(t, kmh, hr, es_rel=None) -> tuple[np.ndarray, dict]:
    """The samples the trail durability may use (docs/research/unsourced-
    rules.md §0.6: "用 DRIFT §8.1 的視窗規則"), from workout_review's drift v2
    rules: the adaptive start (steady_start), the return-leg cool-down
    (steady_end), the trailing idle (trailing_idle), DRIFT_SETTLE_S after
    every stop ≥ 60 s (re-acceleration), and — when `es_rel` (effort-km speed
    per sample) is given — slow stretches: 30-s mean < WALK_FRAC × the
    median for ≥ WALK_SEG_S (queues, photo stops; DRIFT §4.3 on the effort-km
    speed instead of the pace, 推估). Returns (mask, record of the cuts)."""
    from backend.engine import workout_review as WR
    t = np.asarray(t, float)
    n = len(t)
    s = np.asarray(kmh, float)[:n]
    fin = np.isfinite(t)
    rel = t - t[fin][0] if fin.any() else np.zeros(n)
    end, idle = WR.trailing_idle(t, s)
    end2, tail = WR.steady_end(t, s, end)
    start, _rec = WR.steady_start(t, hr, s, end2)
    keep = fin & (rel >= start) & (rel < end2)
    stop = fin & np.isfinite(s) & (s <= WR.STOP_KMH)
    settle = np.zeros(n, bool)
    n_settle = 0
    for a_, b_ in WR._stop_segments(rel, stop):
        if b_ - a_ >= 60.0:
            settle |= (rel > b_) & (rel <= b_ + WR.DRIFT_SETTLE_S)
            n_settle += 1
    keep &= ~settle
    slow_s = 0.0
    if es_rel is not None:
        from backend.engine.panels.workout import rolling_mean
        e = np.asarray(es_rel, float)[:n]
        ok = keep & np.isfinite(e) & (e > 0)
        if ok.sum() > 100:
            med = float(np.median(e[ok]))
            r30 = rolling_mean(t, np.where(ok, e, np.nan), 30.0)
            slow = ok & np.isfinite(r30) & (r30 < WR.WALK_FRAC * med)
            dtv = np.diff(t, prepend=t[0])
            for a_, b_ in WR._runs_of(slow):
                dur = float(rel[b_ - 1] - rel[a_]) if b_ - 1 > a_ else 0.0
                if dur >= WR.WALK_SEG_S:
                    keep[a_:b_] = False
                    slow_s += float(np.clip(dtv[a_:b_], 0, 30).sum())
    return keep, {"start_s": float(start), "end_s": float(end2), "idle_s": float(idle),
                  "tail_s": float((tail or {}).get("excluded_s") or 0.0), "settle_stops": n_settle,
                  "slow_s": slow_s}


def _trail_durability(ds, w) -> Optional[dict]:
    """trailhr step 2 for one run: durability() on the moving-time axis with
    effort-km speed as output, only on the drift-v2-cleaned samples
    (durability_clean_mask; the moving-time axis itself keeps every moving
    second, so "hours after T0" still counts from the start); δ per hour
    after T0. SP-222: on the same windows, the speed by moving hour (trailhr.fade_run) for the
    segment ETAs' fade shape."""
    from backend.engine.panels.workout import durability
    from backend.engine.racepower import trailhr as TH
    a = activity_arrays(ds, w)
    if a is None or a["hr"] is None:
        return None
    mv = np.nan_to_num(a["kmh"]) > RUN_MOVING_KMH
    tm, es, m = TH.effort_speed_series(a["t"], a["d"], a["z"], mv)
    if len(tm) < 100 or tm[-1] < TH.TRAILHR["dur_min_s"]:
        return {"delta": None, "moving_s": float(tm[-1]) if len(tm) else 0.0}
    es_all = np.full(len(a["t"]), np.nan)
    es_all[m] = es
    keep, cuts = durability_clean_mask(a["t"], a["kmh"], a["hr"], es_all)
    es_clean = np.where(keep[m], es, np.nan)
    r = durability(tm, es_clean, a["hr"][m])
    # trailhr step 7: terrain-matched δ on the cleaned windows (the ratio above only for comparison)
    wins = TH.terrain_windows(a["t"], a["d"], a["z"], a["hr"], mv, keep)
    wd = TH.within_run_delta(wins)
    return {"delta": (wd or {}).get("delta"), "se": (wd or {}).get("se"), "windows": (wd or {}).get("n"),
            "fade": TH.fade_run(wins) if tm[-1] >= TH.FADE["min_run_s"] else None,
            "bins": (wd or {}).get("bins"), "moving_s": float(tm[-1]), "end_pct": (r or {}).get("end_pct"),
            "delta_uncleaned": TH.durability_delta((r or {}).get("points")),
            "clean": cuts, "kept_share": float(keep[m].mean()) if len(m) else None}


def activity_hadley(ds) -> dict[int, float]:
    """{workout idx: the activity's Hadley sum} from route_weather's
    activity_weather.json (heat_data.exposures): by file name, else (a TP /
    COROS FIT file of an activity the weather file knows under its WKO5
    name) by local start time within MATCH_TOL_MIN (activity_tags). Memoised
    on the dataset; {} without the weather file."""
    memo = getattr(ds, "_hadley_by_idx", None)
    if memo is not None:
        return memo
    out: dict[int, float] = {}
    try:
        from backend.engine import activity_tags as AT
        from backend.engine import heat_data as HD
        acts, _meta = HD.exposures()
        by_file = {a.get("file"): a.get("hadley") for a in acts if a.get("hadley") is not None}
        by_day: dict = {}
        for f, h in by_file.items():
            m = re.search(r"(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})", str(f))
            if m:
                t = dt.datetime(*(int(x) for x in m.groups()))
                by_day.setdefault(t.date(), []).append((t, h))
        tol = dt.timedelta(minutes=AT.MATCH_TOL_MIN + 2)
        for w in ds.workouts:
            h = by_file.get(w.entry.file)
            if h is None:
                st = w.entry.start.replace(tzinfo=None) if w.entry.start.tzinfo else w.entry.start
                cand = [(abs(t - st), hh) for t, hh in by_day.get(st.date(), [])]
                cand = [c for c in cand if c[0] <= tol]
                h = min(cand)[1] if cand else None
            if h is not None:
                out[w.idx] = float(h)
    except Exception:                       # noqa: BLE001 — no weather → no heat shift
        out = {}
    try:
        ds._hadley_by_idx = out
    except AttributeError:
        pass
    return out


def trail_hr_points(ds, runs, exclude: Optional[set] = None, heat: bool = True) -> list[dict]:
    """trailhr run points (own-date LTHR) of the given outdoor trail runs;
    with `heat`, x moved to Hadley 120 with the athlete's β (trailhr.heat_adjust;
    x_raw keeps the measured HR level)."""
    from backend.engine.racepower import trailhr as TH
    exclude = exclude or set()
    had = activity_hadley(ds) if heat else {}
    out = []
    for w in runs:
        if w.idx in exclude or not (outdoor(w) and is_trail(w)):
            continue
        st = intensity_stats(ds, w) or {}
        th = thresholds_as_of(ds, w.entry.start.date())
        p = TH.run_point(w.metrics.get("distance"), w.metrics.get("climbing"), st.get("moving_s"),
                         st.get("hr_avg"), th.get("lthr"))
        if p:
            p.update(idx=w.idx, date=w.entry.start.date().isoformat(), label=label(w))
            if heat:
                TH.heat_adjust(p, had.get(w.idx))
            out.append(p)
    return out


def xstar_points(ds, runs, exclude: Optional[set] = None) -> list[dict]:
    """x*(T) samples (trailhr.fit_xstar): the outdoor road and trail runs
    given (the caller's races / 全力 set), x = moving HR ÷ own-date LTHR (the
    measured level, no heat shift), T = moving hours; trail ≥ 90 min
    (maximal.trail_min_s), road ≥ 15 min (XSTAR road_min_s)."""
    from backend.engine.racepower import trailhr as TH
    exclude = exclude or set()
    out = []
    for w in runs:
        if w.idx in exclude or not outdoor(w):
            continue
        st = intensity_stats(ds, w) or {}
        mv, hr = st.get("moving_s"), st.get("hr_avg")
        trail = is_trail(w)
        need = TH.TRAILHR["race_min_s"] if trail else TH.XSTAR["road_min_s"]
        if not mv or not hr or mv < need:
            continue
        th = thresholds_as_of(ds, w.entry.start.date())
        if not th.get("lthr"):
            continue
        out.append({"T_h": mv / 3600.0, "x": hr / th["lthr"], "idx": w.idx, "category": "trail" if trail else "road",
                    "date": w.entry.start.date().isoformat(), "label": label(w)})
    return out


NONMOVING_KEY = "racepower_nonmoving_v1"


def _nonmoving_row(ds, w) -> Optional[dict]:
    from backend.engine.racepower import nonmoving as NM
    a = activity_arrays(ds, w)
    if a is None:
        return None
    r = NM.run_row(a["t"], np.nan_to_num(a["kmh"]) > RUN_MOVING_KMH)
    return r


def nonmoving_row(ds, w) -> Optional[dict]:
    """nonmoving.run_row of one activity (disk-memoised)."""
    return ds.cached_series(NONMOVING_KEY, w, lambda: _nonmoving_row(ds, w))


def _fuel_tags(tags, w) -> Optional[bool]:
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import trailhr as TH
    if not tags:
        return None
    u = AT.find(tags, w.entry.start, w.entry.file)
    if not u:
        return None
    words = AT.tags_of(u) + [str(u.get("note") or "")]
    return TH.fuel_of(words)


def trail_hr_model(ds, today: Optional[dt.date] = None, exclude: Optional[set] = None,
                   race_idx: Optional[set] = None, tags: Optional[list] = None) -> dict:
    """The trail HR pace model (trailhr.py) as of `today`: the trail runs of
    the RE window before `today` without `exclude` (x heat-shifted to Hadley
    120); durability from the runs ≥ 2 h on their drift-v2-cleaned windows,
    shrunk to the 0.05 /h prior, split by the user's fuelling tags when both
    groups are big enough; the full-effort curve x*(T) from earlier `race_idx`
    runs (activity type 比賽 or 全力, road and trail; default:
    capacity_samples' effective tags); the non-moving profile from the
    earlier race / 全力 trail runs ≥ 90 min. `tags` = activity_tags rows
    (default: activity_tags.load(); only the fuelling covariate reads them)."""
    from backend.engine.racepower import nonmoving as NM
    from backend.engine.racepower import trailhr as TH
    from backend.engine.wko5expr.dataset import date_to_day
    today = today or dt.date.today()
    tday = date_to_day(today)
    exclude = exclude or set()
    if tags is None:
        try:
            from backend.engine import activity_tags as AT
            tags = AT.load()
        except Exception:                   # noqa: BLE001 — no tag store: no fuelling split
            tags = []
    runs = [w for w in ds.workouts if outdoor(w) and is_trail(w) and w.idx not in exclude
            and tday - TH.TRAILHR["window_days"] < w.day < tday + 1]
    pts = trail_hr_points(ds, runs)
    drows, frows, n_long = [], [], 0
    for p in pts:
        if p["T_h"] * 3600.0 >= TH.TRAILHR["dur_min_s"]:
            w = ds.workouts[p["idx"]]
            r = ds.cached_series(TRAILHR_DUR_KEY, w, lambda w=w: _trail_durability(ds, w))
            if p["T_h"] * 3600.0 >= TH.FADE["min_run_s"]:
                # SP-222: the athlete's own fade by moving hour, for the segment ETAs
                n_long += 1
                if r and r.get("fade"):
                    frows.append(r["fade"])
            if r and r.get("delta") is not None and r.get("se"):
                p["delta"], p["delta_se"] = r["delta"], r["se"]
                p["delta_uncleaned"] = r.get("delta_uncleaned")
                p["fuel"] = _fuel_tags(tags, w)
                drows.append({"delta": r["delta"], "se": r["se"], "fuel": p["fuel"], "date": p["date"],
                              "windows": r.get("windows")})
    ds.flush_series()
    dinfo = TH.delta_by_fuel(drows)
    dinfo["runs"] = drows
    dinfo["gate"] = TH.choose_delta(pts, dinfo)
    m = TH.fit(pts, dinfo["gate"]["delta"])
    m["delta_raw"] = dinfo["all"]["raw_median"]
    m["delta_info"] = dinfo
    m["n_durability"] = len(drows)
    m["fade"] = TH.fade_shape(frows, n_long)
    if race_idx is None:
        caps = capacity_samples(ds, [w for w in ds.workouts if outdoor(w) and w.day < tday], tags=tags)
        race_idx = {i for i, c in caps.items() if c["tags"]["activity_type"] == "race" or c.get("ok")}
    earlier = [w for w in ds.workouts if w.idx in race_idx and w.day < tday and w.idx not in exclude]
    allx = trail_hr_points(ds, [w for w in earlier if is_trail(w)], heat=False)
    xs = [p["x"] for p in allx if p["T_h"] * 3600.0 >= TH.TRAILHR["race_min_s"]]
    m["x_race_median"], m["x_race_median_source"] = TH.race_level(xs)
    m["x_race_n"] = len(xs)
    m["xstar"] = TH.fit_xstar(xstar_points(ds, earlier))
    # the planner's x_race: the curve at the old "race" length (shown as the HR level); the
    # prediction itself solves x*(T) at the predicted T (trailhr.predict_race)
    m["x_race"] = TH.xstar_at(m["xstar"], 3.0)
    m["x_race_source"] = (f"全力心率曲線 x*(T)（{m['xstar']['n']} 次比賽／全力跑，收縮到文獻先驗）"
                          if m["xstar"]["n"] else "沒有之前的比賽／全力跑：用文獻先驗 x*(T)")
    nm_rows = []
    for w in earlier:
        if not is_trail(w):
            continue
        r = nonmoving_row(ds, w)
        if r and r["moving_s"] >= TH.TRAILHR["race_min_s"]:
            nm_rows.append(r)
    ds.flush_series()
    m["nonmoving"] = NM.profile(nm_rows)
    m["today"] = today.isoformat()
    m["points"] = pts
    return m


def hr_capacity(ds, today: Optional[dt.date] = None, exclude: Optional[set] = None,
                lthr: Optional[float] = None, distribution: bool = True) -> dict:
    """HR-based capacity as of `today` (hrcap.py): the per-run steady flat
    points of the outdoor road runs of the 90 days up to `today` (runs with
    Pw:HR drift > 5 % dropped), OLS power on HR, P at the as-of LTHR; plus
    each road run's moving power as a % of that P (training-intensity
    distribution). 推估."""
    from backend.engine.racepower import hrcap as HC
    from backend.engine.wko5expr.dataset import date_to_day
    today = today or dt.date.today()
    tday = date_to_day(today)
    exclude = exclude or set()
    runs = [w for w in ds.workouts if outdoor(w) and not is_trail(w) and w.idx not in exclude
            and tday - HC.HRCAP["window_days"] < w.day < tday + 1]
    th = thresholds_as_of(ds, today)
    lthr = lthr or th.get("lthr")
    pts, dropped, dist_rows = [], 0, []
    for w in runs:
        if not power_ok(ds, w):
            continue                     # an HR–power regression: watch-estimated power stays out
        st = intensity_stats(ds, w) or {}
        if st.get("p_avg"):
            dist_rows.append({"idx": w.idx, "date": w.entry.start.date().isoformat(), "p_avg": st["p_avg"],
                              "moving_s": st.get("moving_s"), "hr_avg": st.get("hr_avg")})
        if st.get("drift") is not None and st["drift"] > HC.HRCAP["drift_max"]:
            dropped += 1
            continue
        q = HC.run_point(grade_samples(ds, [w]))
        if q:
            pts.append({**q, "idx": w.idx, "date": w.entry.start.date().isoformat()})
    ds.flush_series()
    cap = HC.capacity(pts, lthr)
    cap.update(points=pts, dropped_drift=dropped, lthr_source=th.get("lthr_source"), today=today.isoformat(),
               window_days=HC.HRCAP["window_days"])
    if distribution:
        cap["distribution"] = HC.intensity_distribution(dist_rows, cap["p_lthr"])
        # the same runs against the CP in effect (plan test / as-of PD refit), for comparison
        cp = cp_as_of(ds, today)
        cap["distribution_cp"] = {**(HC.intensity_distribution(dist_rows, cp) or {}), "cp": cp} if cp else None
    return cap
