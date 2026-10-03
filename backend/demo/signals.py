"""Per-second signals of one demo activity (docs/plans/auth-and-demo.plan.md §3.2).

Running: the athlete holds a target *power* (a fraction of CP per program
segment); speed follows from a Minetti cost model (engine/algorithms/minetti.py:
Stryd-style power = mass x speed x ECOR x Cr(grade)/Cr(0)), with a technical-
descent speed cap on trails and power-hiking on the steepest pitches.
Hiking (百岳): speed from Tobler's hiking function, slowed by the pack, the
altitude and the day; rests at the huts / viewpoints.

Measured channels, as a COROS watch + Stryd pod record them:
  * power: Stryd ±4 % AR(1) noise, or watch-estimated power (noisier, no
    developer fields) on the runs without the pod
  * heart rate: WRIST OPTICAL only (no chest strap): a first-order lag
    (τ ≈ 35 s up / 55 s down), cardiac drift (more in heat), and the optical
    errors — reads low for the first 2–4 min, an occasional cadence lock
    (170–180 bpm following the step rate), short dropouts
  * cadence (strides/min, as FIT stores it), barometric altitude with drift,
    GPS position jitter, watch temperature (air + wrist warmth)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from backend.demo import athlete as A
from backend.demo.course import STEP, Course
from backend.engine.algorithms.minetti import RUN_COEFFS, WALK_COEFFS


def _poly(c, i):
    return ((((c[0] * i + c[1]) * i + c[2]) * i + c[3]) * i + c[4]) * i + c[5]


def run_ratio(grade: np.ndarray) -> np.ndarray:
    """Cr(i)/Cr(0), with the downhill benefit floored (runners cannot bank
    Minetti's cheap steep descent)."""
    g = np.clip(grade, -0.45, 0.45)
    r = _poly(RUN_COEFFS, g) / RUN_COEFFS[-1]
    return np.maximum(r, 0.62)


def walk_ratio(grade: np.ndarray) -> np.ndarray:
    g = np.clip(grade, -0.45, 0.45)
    return np.maximum(_poly(WALK_COEFFS, g) / WALK_COEFFS[-1], 0.55)


def _ar1_fast(rng, n: int, sd: float, phi: float = 0.9) -> np.ndarray:
    """AR(1) via an FFT-free recursive filter in blocks (numpy only)."""
    if n == 0:
        return np.zeros(0)
    e = rng.normal(0.0, sd * math.sqrt(1 - phi * phi), n)
    # y_t = sum_k phi^k e_{t-k}: exact with a truncated kernel (phi^K ~ 0)
    K = int(min(n, max(10, math.ceil(math.log(1e-4) / math.log(phi)))))
    kern = phi ** np.arange(K)
    y = np.convolve(e, kern)[:n]
    return y


def _moving(x: np.ndarray, w: int) -> np.ndarray:
    if w <= 1 or len(x) == 0:
        return x.copy()
    k = np.ones(w) / w
    return np.convolve(np.pad(x, (w // 2, w - 1 - w // 2), mode="edge"), k, mode="valid")


@dataclass
class Segment:
    dur_s: int
    frac: float                      # target power / CP (runs)
    lap: bool = True                 # its own lap
    fade: float = 0.0                # all-out pacing: frac × (1 + fade/2 − fade·progress)
    label: str = ""


@dataclass
class Signals:
    n: int
    lat: np.ndarray
    lon: np.ndarray
    alt: np.ndarray
    dist: np.ndarray
    speed: np.ndarray
    hr: np.ndarray
    cadence: np.ndarray
    power: Optional[np.ndarray]
    temp: np.ndarray
    form_power: Optional[np.ndarray]
    air_power: Optional[np.ndarray]
    lss: Optional[np.ndarray]
    laps: list = field(default_factory=list)
    ascent: float = 0.0
    descent: float = 0.0


def air_temp(day_of_year: int, hour_local: float, ele: np.ndarray, warm: float = 0.0) -> np.ndarray:
    """Invented Taiwan climate: lowland 16 °C (Jan) .. 29 °C (Jul) at dawn,
    + daytime warming, −6.5 °C / km."""
    season = 22.5 - 6.5 * math.cos(2 * math.pi * (day_of_year - 15) / 365.0)
    return season + warm + 3.0 * max(0.0, math.sin(math.pi * (hour_local - 6) / 14)) - 0.0065 * ele


def _course_at(course: Course, d: np.ndarray):
    """Elevation, lat, lon, grade at distances d (wrapping courses repeat)."""
    n = len(course.ele)
    L = course.length
    if course.wraps and L > 0:
        x = np.mod(d, L)
    else:
        x = np.clip(d, 0, L)
    grid = np.arange(n) * STEP
    return (np.interp(x, grid, course.ele), np.interp(x, grid, course.lat),
            np.interp(x, grid, course.lon))


def _measure(rng, *, course: Course, d: np.ndarray, v: np.ndarray, p_true: np.ndarray,
             frac: np.ndarray, walking: np.ndarray, stryd: bool, has_power: bool, drift: float,
             doy: int, hour: float, artifacts: bool, alt_hr: bool, laps: list, temp_warm: float = 0.0,
             fatigue_hr: float = 0.0, lock_ok: bool = True, power_sd: float = 0.04) -> Signals:
    n = len(d)
    ele, lat, lon = _course_at(course, d)
    # ---- heart rate (wrist optical) ----
    fs = _moving(frac, 25)
    air = air_temp(doy, hour, ele, temp_warm)
    heat = 1.0 + max(0.0, float(np.mean(air)) - 18.0) / 12.0
    t = np.arange(n)
    ss = A.hr_steady_vec(fs)
    ss = ss + drift * heat * np.maximum(0.0, t - 900) / 60.0 * np.clip(fs, 0.3, 1.2) + fatigue_hr
    if alt_hr:
        ss = ss + np.maximum(0.0, ele - 1500.0) * 0.008
    ss = np.clip(ss, 88.0 + (np.maximum(0.0, ele - 1500.0) * 0.006 if alt_hr else 0.0), A.HRMAX - 1.0)
    hr = np.empty(n)
    h = 92.0 + rng.uniform(0, 8)
    ssl = ss.tolist()
    for i in range(n):
        s = ssl[i]
        h += (s - h) / (35.0 if s > h else 55.0)
        hr[i] = h
    meas = hr + _ar1_fast(rng, n, 1.2, 0.85)
    cad_spm = None
    # ---- cadence (strides/min) ----
    run_cad = 79.0 + 3.2 * v
    walk_cad = 50.0 + 5.0 * np.minimum(v, 1.6)
    cad = np.where(walking, walk_cad, run_cad) + _ar1_fast(rng, n, 0.8, 0.8)
    cad = np.where(v < 0.15, 0.0, cad)
    cad_spm = cad * 2.0
    if artifacts:
        # reads low while the sensor settles (2–4 min)
        T0 = int(rng.uniform(120, 240))
        k = np.clip(1 - t / T0, 0, 1)
        meas = meas - rng.uniform(10, 20) * k
        # cadence lock: HR follows the step rate for a while
        if lock_ok and rng.random() < 0.18 and n > 900:
            for _ in range(int(rng.integers(1, 3))):
                a = int(rng.integers(300, n - 200))
                w = int(rng.integers(40, 120))
                seg = slice(a, min(n, a + w))
                lock = np.clip(cad_spm[seg] + rng.normal(0, 1.0, len(meas[seg])), 168, 182)
                meas[seg] = np.where(cad_spm[seg] > 150, lock, meas[seg])
        # short dropouts
        if rng.random() < 0.3 and n > 600:
            for _ in range(int(rng.integers(1, 4))):
                a = int(rng.integers(60, n - 30))
                meas[a:a + int(rng.integers(5, 20))] = np.nan
    meas = np.where(np.isfinite(meas), np.clip(meas, 90.0, A.HRMAX + 1.0), np.nan)

    # ---- power ----
    power = form = airp = lss = None
    if has_power:
        if stryd:
            power = p_true * (1.0 + _ar1_fast(rng, n, power_sd, 0.7))
            form = power * 0.27 + rng.normal(0, 1.5, n)
            airp = power * 0.015 + 1.0 + rng.normal(0, 0.4, n)
            lss = 10.0 + _ar1_fast(rng, n, 0.4, 0.95)
            stop = v < 0.15
            for a in (form, airp, lss):
                a[stop] = 0.0
        else:
            power = _moving(p_true * 0.97, 5) * (1.0 + _ar1_fast(rng, n, 0.07, 0.9))
        power = np.maximum(0.0, power)
        power[v < 0.15] = 0.0

    # ---- altitude (barometric), GPS, speed, temperature ----
    baro = ele + np.clip(np.cumsum(rng.normal(0, 0.03, n)), -6, 6) + rng.normal(0, 0.25, n)
    jit = 2.5 / 111_320.0
    glat = lat + _ar1_fast(rng, n, jit, 0.95)
    glon = lon + _ar1_fast(rng, n, jit, 0.95)
    gspd = np.maximum(0.0, v + rng.normal(0, 0.06, n))
    gspd[v < 0.15] = 0.0
    wtemp = _moving(air + 4.0 + 1.5 * np.clip(fs, 0, 1.3), 60) + rng.normal(0, 0.3, n)
    de = np.diff(ele)
    return Signals(n=n, lat=glat, lon=glon, alt=baro, dist=d, speed=gspd, hr=meas, cadence=cad,
                   power=power, temp=wtemp, form_power=form, air_power=airp, lss=lss, laps=laps,
                   ascent=float(de[de > 0].sum()), descent=float(-de[de < 0].sum()))


def _laps(segs: list[Segment], d: np.ndarray, p: np.ndarray) -> list:
    """One lap per segment with lap=True; consecutive lap=False segments merge."""
    out, t = [], 0
    pend = None
    for s in segs:
        if s.lap or pend is None:
            if pend is not None:
                out.append(pend)
            pend = [t, s.dur_s]
        else:
            pend[1] += s.dur_s
        t += s.dur_s
        if s.lap:
            out.append(pend)
            pend = None
    if pend is not None:
        out.append(pend)
    n = len(d)
    laps = []
    for a, dur in out:
        b = min(n, a + dur)
        if b <= a:
            continue
        dd = float(d[b - 1] - d[a] + (d[b - 1] - d[b - 2] if b - 2 >= a else 0.0))
        laps.append({"start_s": a, "duration_s": b - a, "distance_m": max(0.0, dd),
                     "avg_power": float(np.mean(p[a:b])) if p is not None else None})
    return laps


def simulate_run(rng, course: Course, segs: list[Segment], *, cp: float, stryd: bool,
                 doy: int, hour: float, drift: float = 0.06, push: float = 0.0,
                 down_cap: float = 3.2, has_power: bool = True, mass: float = A.WEIGHT_KG,
                 temp_warm: float = 0.0, lock_ok: bool = True, pace_sd: float = 0.025,
                 power_sd: float = 0.04) -> Signals:
    """`pace_sd`: how far the runner wanders off the target power (AR(1)); `power_sd`:
    the Stryd's own noise. A run to the watch's step targets wanders less."""
    durs = [s.dur_s for s in segs]
    n = int(sum(durs))
    target = np.empty(n)
    t = 0
    for s in segs:
        prog = np.linspace(0, 1, s.dur_s, endpoint=False)
        target[t:t + s.dur_s] = cp * s.frac * (1 + s.fade / 2 - s.fade * prog)
        t += s.dur_s
    # start gently, smooth the steps between segments (a few seconds)
    target[:60] *= np.linspace(0.75, 1.0, min(60, n))[: min(60, n)]
    target = _moving(target, 5)
    tn = (target * (1.0 + _ar1_fast(rng, n, pace_sd, 0.95))).tolist()
    g = course.grade()
    rat = run_ratio(g)
    gl, rl = g.tolist(), rat.tolist()
    ng = len(gl)
    L = course.length
    wraps = course.wraps
    trail = course.trail
    d = 0.0
    v = 1.5
    dist = np.empty(n)
    spd = np.empty(n)
    ratio_at = np.empty(n)
    walking = np.zeros(n, dtype=bool)
    for i in range(n):
        x = d % L if (wraps and L > 0) else min(d, L)
        k = int(x / STEP)
        if k >= ng:
            k = ng - 1
        gr, r = gl[k], rl[k]
        p = tn[i]
        if push:
            p *= 1.0 + push * max(-1.0, min(1.0, gr / 0.15))
        vt = p / (mass * A.ECOR * r)
        if trail and gr < -0.06:
            vt = min(vt, down_cap * (1.0 + gr * 1.5))       # technical descent
        if gr > 0.19:
            vt = min(vt, 1.45)                              # power-hiking
            walking[i] = True
        v += (vt - v) * 0.3
        spd[i] = v
        dist[i] = d
        ratio_at[i] = r
        d += v
    p_true = mass * A.ECOR * spd * ratio_at
    frac = p_true / cp
    laps = _laps(segs, dist, p_true)
    return _measure(rng, course=course, d=dist, v=spd, p_true=p_true, frac=frac, walking=walking,
                    stryd=stryd, has_power=has_power, drift=drift, doy=doy, hour=hour, artifacts=True,
                    alt_hr=False, laps=laps, temp_warm=temp_warm, lock_ok=lock_ok, power_sd=power_sd)


def simulate_hike(rng, course: Course, *, cp: float, stryd: bool, doy: int, hour: float,
                  pack: float = A.PACK_KG, day: int = 0, speed_k: float = 0.78,
                  rest_every_s: tuple = (3600, 5400), mass: float = A.WEIGHT_KG,
                  max_s: int = 14 * 3600) -> Signals:
    """Walk the whole course (point to point) with rests."""
    g = course.grade()
    gl = g.tolist()
    el = course.ele.tolist()
    ng = len(gl)
    L = course.length
    k_day = speed_k * (1.0 - 0.03 * day) * (1.0 - 0.012 * pack / 9.0)
    noise = _ar1_fast(rng, max_s, 0.08, 0.98).tolist()
    d = 0.0
    v = 0.8
    ds, vs, rs, wk = [], [], [], []
    t = 0
    next_rest = int(rng.uniform(*rest_every_s))
    rest_left = 0
    lunch_done = False
    while d < L and t < max_s:
        k = min(ng - 1, int(d / STEP))
        gr = gl[k]
        if rest_left > 0:
            vt = 0.0
            rest_left -= 1
        else:
            alt_k = 1.0 - 0.04 * max(0.0, el[k] - 2000.0) / 1000.0
            vt = 6.0 * math.exp(-3.5 * abs(gr + 0.05)) / 3.6 * k_day * alt_k * (1.0 + noise[t])
            vt = max(0.25, vt)
            if t >= next_rest:
                if not lunch_done and d > L * 0.45:
                    rest_left = int(rng.uniform(1200, 1800))
                    lunch_done = True
                else:
                    rest_left = int(rng.uniform(300, 840))
                next_rest = t + rest_left + int(rng.uniform(*rest_every_s))
        v += (vt - v) * 0.2
        if vt == 0.0 and v < 0.1:
            v = 0.0
        ds.append(d)
        vs.append(v)
        rs.append(gr)
        wk.append(True)
        d += v
        t += 1
    dist = np.array(ds)
    spd = np.array(vs)
    gr = np.array(rs)
    p_true = (mass + pack) * spd * walk_ratio(gr)
    frac = p_true / cp
    n = len(dist)
    laps = [{"start_s": 0, "duration_s": n, "distance_m": float(dist[-1]) if n else 0.0,
             "avg_power": float(np.mean(p_true)) if n else None}]
    return _measure(rng, course=course, d=dist, v=spd, p_true=p_true, frac=frac,
                    walking=np.ones(n, dtype=bool), stryd=stryd, has_power=True, drift=0.01,
                    doy=doy, hour=hour, artifacts=True, alt_hr=True, laps=laps,
                    fatigue_hr=2.0 * day + 0.6 * pack)
