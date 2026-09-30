"""
The athlete-derived inputs of the race-power page, read from the Dataset:
weight, CP / W′ / TTE sources, the mean-max envelope (with the activity that
set each point), personal Riegel k, road / trail RE, hiking EP/h, training
conditions and AeT. Everything numeric is delegated to the pure modules.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from statistics import median
from typing import Optional

import numpy as np

from backend.engine.algorithms.effort import SIMPLE_FORMULAS
from backend.engine.racepower import cp as CP
from backend.engine.racepower import re as RE
from backend.engine.racepower import riegel as R
from backend.engine.racepower import weather as WX

CP_WINDOW_DAYS = 90
RIEGEL_WINDOW_DAYS = 365
RE_WINDOW_DAYS = 365
HIKE_WINDOW_DAYS = 3 * 365
PLAN_CP_MAX_AGE_DAYS = 90
DEFAULT_TTE_S = 3000.0
TRAIL_MIN_CLIMB_M, TRAIL_MIN_MOVING_S = 150.0, 45 * 60
ROAD_MAX_CVI, ROAD_MIN_MOVING_S = 25.0, 20 * 60
HIKE_MIN_MOVING_S = 3600.0
HIKE_HEAVY_GAIN_M, HIKE_HEAVY_WEIGHT = 600.0, 3.0
FALLBACK_TRAINING = {"altitude_m": 100.0, "temp_c": 25.0, "rh_pct": 75.0}
METRICS_KEY = "racepower_v1"
TRAINING_ENV_CACHE = WX.HOME / "racepower_training_env.json"

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


def _curve(ds, w) -> Optional[tuple[list, list]]:
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
    out = {}
    for w in runs:
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


def hiking_days(ds, today: dt.date) -> list[dict]:
    """One row per hiking / mountaineering day (multi-day trips split by
    calendar day, as achievements.py does)."""
    from backend.engine.achievements import KIND_HIKE, build_achievements
    lo = (today - dt.timedelta(days=HIKE_WINDOW_DAYS)).isoformat()
    rows = []
    for a in build_achievements(ds):
        if a.kind != KIND_HIKE or a.start[:10] < lo:
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
                         "peaks": [p["name"] for p in a.peaks]})
    return sorted(rows, key=lambda r: r["date"])


def _training_conditions(ds, runs, metrics, today: dt.date, fetch: bool = True) -> dict:
    """Median elevation of the last 90 days of power runs + Open-Meteo archive
    T / RH over those activities at the median start location (cached daily)."""
    ms = [(w, metrics[w.idx]) for w in runs if w.idx in metrics]
    elevs = [m["elev_median"] for _, m in ms if m.get("elev_median") is not None]
    lats = [m["lat"] for _, m in ms if m.get("lat")]
    lons = [m["lon"] for _, m in ms if m.get("lon")]
    base = {"altitude_m": float(median(elevs)) if elevs else FALLBACK_TRAINING["altitude_m"],
            "temp_c": FALLBACK_TRAINING["temp_c"], "rh_pct": FALLBACK_TRAINING["rh_pct"],
            "provider": "fallback", "label": "預設（100 m / 25 °C / 75 %）", "runs": len(ms),
            "lat": float(median(lats)) if lats else None, "lon": float(median(lons)) if lons else None}
    if elevs:
        base["label"] = f"近 {CP_WINDOW_DAYS} 天 {len(elevs)} 次跑步的中位海拔；溫濕度為預設值"
    if base["lat"] is None or not fetch:
        return base
    stamp = f"{today.isoformat()}|{len(ms)}|{base['lat']:.3f},{base['lon']:.3f}"
    try:
        c = json.loads(TRAINING_ENV_CACHE.read_text("utf-8"))
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
        TRAINING_ENV_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TRAINING_ENV_CACHE.write_text(json.dumps({"stamp": stamp, "values": vals}, ensure_ascii=False), "utf-8")
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


def derive(ds, today: Optional[dt.date] = None, fetch_weather: bool = True,
           exclude: Optional[set] = None) -> dict:
    """`exclude` = workout idx never used (racepower v2 back-test, leave-one-out)."""
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.files.wko5_athlete import pd_snapshot
    today = today or dt.date.today()
    tday = date_to_day(today)
    weight = ds.setting("weight", tday)
    weight_src = "賽季計畫體重" if ds.plan.weight_on(today) is not None else "WKO5 設定"
    prof = ds.plan.profile or {}
    exclude = exclude or set()
    runs_365 = [w for w in ds.workouts if w.sport == "run" and tday - RIEGEL_WINDOW_DAYS < w.day <= tday + 1
                and w.idx not in exclude]
    runs_90 = [w for w in runs_365 if w.day > tday - CP_WINDOW_DAYS]
    metrics = run_metrics(ds, runs_365, weight)
    by_idx = {w.idx: w for w in ds.workouts}

    # ---- CP sources ----------------------------------------------------------
    pd = pd_snapshot(ds.athlete.root)
    plan_cp = _plan_cp(ds, today)
    ref_cp = (plan_cp or {}).get("cp") or pd.get(("mftp", "Run"))
    training = _training_conditions(ds, runs_90, metrics, today, fetch_weather)
    dropped: list = []
    env90 = envelope(ds, [w for w in runs_90 if w.idx in metrics], ref_cp, dropped, training, metrics)
    table = {x: {"p": y, **act_ref(by_idx[i])} for x, y, i in zip(env90["xs"], env90["ys"], env90["who"])}
    pts = CP.envelope_points(table)
    fit = CP.fit_cp([(p["t"], p["p"]) for p in pts])
    wind_any = any(m.get("air_any") for m in metrics.values())
    sex = prof.get("sex") or "male"
    acts = None
    if fit:
        acts = {**fit, "points": pts, "checks": CP.validity(pts, envelope=True),
                "rating": CP.rwc_rating(fit["w_prime"], weight, sex, wind_any),
                "window_days": CP_WINDOW_DAYS}
    sources = []
    if plan_cp:
        sources.append({"id": "plan", "label": f"賽季計畫 CP 測試（{plan_cp['date']}）", "cp": plan_cp["cp"],
                        "date": plan_cp["date"], "fresh": plan_cp["fresh"]})
    if acts:
        sources.append({"id": "activities", "label": f"近 {CP_WINDOW_DAYS} 天活動（3–20 分鐘最佳功率擬合）",
                        "cp": acts["cp"], "w_prime": acts["w_prime"]})
    if pd.get(("mftp", "Run")):
        sources.append({"id": "wko5", "label": "WKO5 模型 mFTP", "cp": pd[("mftp", "Run")],
                        "tte": pd.get(("tte", "Run")), "frc": pd.get(("frc", "Run"))})
    default_cp = ("plan" if plan_cp and plan_cp["fresh"] else "activities" if acts else
                  "wko5" if pd.get(("mftp", "Run")) else None)
    tte = pd.get(("tte", "Run"))
    tte_src = "WKO5 模型 TTE" if tte else "預設 50 分鐘（試算表預設）"

    # ---- Riegel -------------------------------------------------------------
    dropped365: list = []
    env365 = envelope(ds, [w for w in runs_365 if w.idx in metrics], ref_cp, dropped365, training, metrics)
    pk = R.personal_k(env365["xs"], env365["ys"], tte or DEFAULT_TTE_S)
    raw = R.personal_k(env365["xs"], env365["ys"], tte or DEFAULT_TTE_S, keep_frac=None)
    riegel = None
    if pk:
        lo, hi = pk["t_min"], pk["t_max"]
        span_who = sorted({i for x, i in zip(env365["xs"], env365["who"]) if lo <= x <= hi})
        why = []
        if not R.is_reasonable_k(pk["k"]):
            why.append("k 不在 −0.25…−0.01 的合理範圍")
        if pk["n"] < 5:
            why.append("擬合點少於 5 個")
        if pk["r2"] < 0.8:
            why.append("R² < 0.8")
        if len(span_who) < 3:
            why.append(f"擬合區間只由 {len(span_who)} 次活動構成（單一次穩定長跑的衰退不等於最大努力曲線）")
        riegel = {**pk, "valid": not why, "invalid_reasons": why, "n_activities": len(span_who),
                  "longest_s": hi, "envelope_longest_s": env365["xs"][-1] if env365["xs"] else None,
                  "uncut": None if raw is None else {k_: raw[k_] for k_ in ("k", "r2", "n", "t_max")},
                  "activities": [act_ref(by_idx[i]) for i in span_who][:40],
                  "window_days": RIEGEL_WINDOW_DAYS}
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
               "form_pct": m["form_pct"], "lss_kg": m["lss_kg"], "trail": "runningtrail" in w.tags}
        if row["trail"]:
            if (climb or 0) >= TRAIL_MIN_CLIMB_M and m["moving_s"] >= TRAIL_MIN_MOVING_S and km:
                row["re_trail"] = {f: RE.trail_re(km, climb, m["moving_s"], m["avg_power"], weight,
                                                  SIMPLE_FORMULAS[f][0]) for f in ("fitted_run", "itra", "scarf")}
                trail_rows.append(row)
        elif c is not None and c < ROAD_MAX_CVI and m["moving_s"] >= ROAD_MIN_MOVING_S \
                and w.sport_type != "indoor running":
            road_rows.append(row)
    road = RE.summary([r["re"] for r in road_rows])
    road_cvi = RE.summary([r["cvi"] for r in road_rows])
    trail = {f: RE.summary([r["re_trail"][f] for r in trail_rows]) for f in ("fitted_run", "itra", "scarf")}
    train_cvi = RE.summary([RE.cvi(w.metrics.get("climbing"), w.metrics.get("distance"))
                            for w in runs_90 if w.metrics.get("distance")])

    # ---- hiking ---------------------------------------------------------------
    try:
        hdays = hiking_days(ds, today)
    except Exception as e:                  # noqa: BLE001
        hdays, hike_err = [], str(e)[:120]
    else:
        hike_err = None
    eph = RE.summary([d["ep_per_h"] for d in hdays],
                     [HIKE_HEAVY_WEIGHT if d["gain_m"] >= HIKE_HEAVY_GAIN_M else 1.0 for d in hdays]) \
        if hdays else None
    biggest = max(hdays, key=lambda d: d["ep"]) if hdays else None

    # ---- prior-race candidates (standard distances) -----------------------------
    priors = []
    for w in runs_365:
        m, km = metrics.get(w.idx), w.metrics.get("distance")
        if not m or not km or "runningtrail" in w.tags:
            continue
        cat = R.distance_category(km * 1000.0)
        if cat:
            priors.append({**act_ref(w), "km": km, "category": cat, "time_s": w.metrics.get("movingduration") or m["moving_s"],
                           "avg_power": m["avg_power"]})
    priors.sort(key=lambda r: r["date"], reverse=True)
    # default prior for the Riegel table: the fastest standard-distance run of the year
    auto_prior = max(priors, key=lambda r: r["km"] * 1000.0 / r["time_s"]) if priors else None

    events = []
    for e in sorted(ds.plan.events, key=lambda e: e.date):
        if e.end < today:
            continue
        pk_ = WX.find_peak(e.name)
        events.append({"id": e.id, "name": e.name, "date": e.date, "kind": e.kind, "days": e.days,
                       "distance_km": e.distance_km, "climbing_m": e.climbing_m, "est_hours": e.est_hours,
                       "priority": e.priority, "peak": pk_})

    return {
        "today": today.isoformat(),
        "weight": {"value": weight, "source": weight_src},
        "profile": {"sex": sex, "sex_source": "賽季計畫" if prof.get("sex") else "預設（男）",
                    "power_meter": prof.get("power_meter") or "stryd", "wind": wind_any,
                    "wind_source": "資料中有 Stryd air power" if wind_any else "資料中沒有 air power"},
        "cp": {"sources": sources, "default": default_cp, "activities": acts, "plan": plan_cp,
               "dropped": dropped, "dropped_365": dropped365, "ref_cp": ref_cp},
        "tte": {"value": tte or DEFAULT_TTE_S, "source": tte_src},
        "wko5": {"mftp": pd.get(("mftp", "Run")), "tte": tte, "frc": pd.get(("frc", "Run")),
                 "pmax": pd.get(("pmax", "Run")), "stamina": pd.get(("stamina", "Run"))},
        "riegel": riegel, "envelope": chart_env,
        "re": {"road": road, "road_cvi": road_cvi, "train_cvi": train_cvi, "trail": trail,
               "road_runs": road_rows[-60:], "trail_runs": trail_rows,
               "window_days": RE_WINDOW_DAYS},
        "hiking": {"eph": eph, "days": hdays, "biggest": biggest, "error": hike_err,
                   "window_days": HIKE_WINDOW_DAYS},
        "priors": priors[:40], "auto_prior": auto_prior,
        "training_conditions": training,
        "altitude_normalised": True,
        "aet": _aet(ds, today),
        "events": events,
        "counts": {"runs_365": len(runs_365), "runs_with_power": len(metrics), "runs_90": len(runs_90)},
    }


# ---- racepower v2: per-activity samples (docs/research/racepower-v2.md §10.1) ----

GRADE_KEY = "racepower_v2_grade"
HIKE_KEY = "racepower_v2_hike"
RUN_MOVING_KMH = 1.0
HIKE_REST_MS = 0.3


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
            "lon": fit(ds.channel(w.idx, "longitude")), "p": p, "kmh": kmh}


def _grade_windows(ds, w) -> Optional[list]:
    from backend.engine.racepower import grade_model as GM
    a = activity_arrays(ds, w)
    if a is None or a["p"] is None or not np.any(np.nan_to_num(a["p"]) > 0):
        return None
    mv = (np.nan_to_num(a["p"]) > 0) & (np.nan_to_num(a["kmh"]) > RUN_MOVING_KMH)
    rows = GM.windows(a["t"], a["d"], a["z"], a["p"], mv)
    return [[round(r["g"], 4), round(r["v"], 3), round(r["p"], 1), round(r["z"], 1)] for r in rows
            if r.get("p") and r["p"] > 0]


def _hike_windows(ds, w) -> Optional[list]:
    from backend.engine.racepower import grade_model as GM
    a = activity_arrays(ds, w)
    if a is None:
        return None
    mv = np.nan_to_num(a["kmh"]) > HIKE_REST_MS * 3.6
    rows = GM.windows(a["t"], a["d"], a["z"], None, mv)
    return [[round(r["g"], 4), round(r["v"], 3), round(r["z"], 1)] for r in rows if r["v"] >= HIKE_REST_MS]


def grade_samples(ds, runs, exclude: Optional[set] = None) -> list[dict]:
    """100 m windows (grade, speed, power, elevation) of every outdoor run
    with power, disk-cached per activity; RE is computed with each run's
    weight."""
    from backend.engine.wko5expr.dataset import date_to_day  # noqa: F401
    exclude = exclude or set()
    out = []
    for w in runs:
        if w.idx in exclude or w.sport_type == "indoor running" or "runningtreadmill" in w.tags \
                or "runningindoor" in w.tags:
            continue
        rows = ds.cached_series(GRADE_KEY, w, lambda w=w: _grade_windows(ds, w))
        if not rows:
            continue
        wt = ds.setting("weight", w.day)
        for g, v, p, z in rows:
            out.append({"g": g, "v": v, "p": p, "z": z, "re": v / (p / wt) if wt and p else None, "a": w.idx})
    ds.flush_series()
    return out


def hike_workouts(ds, today: dt.date) -> list:
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today)
    return [w for w in ds.workouts if w.sport_type in ("hiking", "mountaineering")
            and tday - HIKE_WINDOW_DAYS < w.day <= tday + 1]


def hike_samples(ds, hikes, exclude: Optional[set] = None) -> list[dict]:
    exclude = exclude or set()
    out = []
    for w in hikes:
        if w.idx in exclude:
            continue
        rows = ds.cached_series(HIKE_KEY, w, lambda w=w: _hike_windows(ds, w))
        for g, v, z in rows or []:
            out.append({"g": g, "v": v, "z": z, "a": w.idx})
    ds.flush_series()
    return out


def grade_models(ds, today: Optional[dt.date] = None, re_flat: Optional[float] = None,
                 exclude: Optional[set] = None, runs: Optional[list] = None) -> dict:
    """GradeRE from the 365-day runs and HikeSpeed from the 3-year hikes
    before `today` (both leave `exclude` out)."""
    from backend.engine.racepower import grade_model as GM
    from backend.engine.wko5expr.dataset import date_to_day
    today = today or dt.date.today()
    tday = date_to_day(today)
    if runs is None:
        runs = [w for w in ds.workouts if w.sport == "run" and tday - RE_WINDOW_DAYS < w.day <= tday + 1]
    gs = grade_samples(ds, runs, exclude)
    if re_flat is None:
        flat = [s["re"] for s in gs if s["re"] and abs(s["g"]) <= 0.01]
        re_flat = float(median(flat)) if flat else 1.0
    hs = hike_samples(ds, hike_workouts(ds, today), exclude)
    return {"grade_re": GM.fit_grade_re(gs, re_flat), "hike_speed": GM.fit_hike_speed(hs)}
