"""
Bad activity files — a "run" that was not a run (2026-10-01).

Typical case: the watch was left recording on a bike or in a car, so a run
has an impossible speed (TP 2025-12-14 `tp_2025_12_14_3477204875.fit`: 12.28
km in 17.3 min ≈ 43 km/h, average power 899 W, HR 66 bpm). Such a file breaks
every model that reads it (the PD fit, mean-max, power TSS, PMC), so it is
EXCLUDED: it stays in the DB and the activity list, marked
「已排除：疑似交通工具／騎車（均速 43 km/h）」, and the Dataset leaves it out of
everything else (Dataset._exclusion_policy; the same place as the approved
data corrections and the power-source policy).

Detection (pure functions, foot sports only: sport group run / walk — a hike
can include running, so walk uses the running limits too):

* speed, from the distance channel resampled to 1 s. Seconds faster than
  SPIKE_KMH (fit_to_channels.MAX_SPEED_KMH, WKO5's own GPS-spike rule) count as
  no distance, so a GPS jump never makes a file bad. The limit for a duration
  T is the men's world-record average speed at T (WR_POINTS, interpolated in
  log T) × MARGIN:
    - average moving speed over the moving time > limit(moving time);
    - a sustained window of 60 s / 5 min / 20 min (WINDOWS_S) faster than
      limit(window) — a bike or car segment inside a real run. Windows, never
      single samples, so a fast descent or a GPS spike is not flagged.
* power: the average of the non-zero power samples > POWER_WKG_MAX × weight.

Cadence is NOT used: the car file's cadence (54) reads like a slow hike's
(56 on the same day's trail walk), so it cannot tell them apart.

Trim vs exclude: the whole file is excluded, never trimmed. Trimming would
have to recompute duration, distance, TSS / NP and the moving time — the WKO5
source takes those from WKO5's own index, so a trimmed file would disagree
with itself — and the run's remaining load is small next to a broken PD fit.
The reason names the bad segment, so the user can decide with
「這筆是正常的，不要排除」 (keep: the whole file, segment included).

Overrides (activity_tags.exclusion, the same user-value pattern as the
activity type / effort): "keep" = never excluded, "exclude" = always
excluded (手動排除), None = the auto rule. The setting activities.exclude_bad
(default true) switches the auto rule off; manual exclusions still apply.
Parity mode excludes nothing (WKO5 reads every file), like the corrections.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

SETTING_KEY = "activities.exclude_bad"
FOOT_GROUPS = ("run", "walk")
KEEP, EXCLUDE = "keep", "exclude"
OVERRIDES = (KEEP, EXCLUDE)

# Men's world records (World Athletics), (seconds, metres):
#   400 m 43.03 (W. van Niekerk 2016), 1500 m 3:26.00 (H. El Guerrouj 1998),
#   10 000 m 26:11.00 (J. Cheptegei 2020), marathon 2:00:35 (K. Kiptum 2023)
# = 33.5 / 26.2 / 22.9 / 21.0 km/h. Faster than these for as long is beyond
# any runner; a fast downhill is not 15 % faster than a flat world record
# for a minute.
WR_POINTS = ((43.03, 400.0), (206.00, 1500.0), (1571.00, 10000.0), (7235.0, 42195.0))
# 推估: room for GPS distance error and steep downhills. Read-only scan of this
# athlete's 1889 foot activities (2026-10-01): the fastest genuine run reaches
# 0.65 of the limit (2024-06-14 treadmill); two real vehicle segments sat at
# 1.12 (2024-03-18 run: 7 min at 25–38 km/h; 2021-07-17 hike: 5 min at 31
# km/h), which a 1.25 margin missed.
MARGIN = 1.15
WINDOWS_S = (60, 300, 1200)
MIN_MOVING_S = 60.0    # 推估: the average rule needs at least a minute of moving
MOVING_KMH = 1.609344498   # algorithms.wko5_time.MOVING_SPEED_KMH["run"]
SPIKE_KMH = 144.0      # fit_to_channels.MAX_SPEED_KMH (WKO5 blanks faster GPS speed)
# 推估: Stryd-style running power ≈ speed (m/s) × ~1 W/kg (running
# effectiveness ≈ 1, the app's RE metric), so the 1500 m record pace
# (7.3 m/s) is ~7–8 W/kg; 10 W/kg on AVERAGE over a whole file is no run.
POWER_WKG_MAX = 10.0
DEFAULT_WEIGHT_KG = 70.0   # 推估: when the athlete's weight is unknown
MIN_POWER_SAMPLES = 60


def wr_speed_kmh(duration_s: float) -> float:
    """World-record average speed (km/h) for a duration, log-interpolated
    between WR_POINTS; clamped to the 400 m / marathon speeds outside."""
    pts = [(t, d / t * 3.6) for t, d in WR_POINTS]
    if duration_s <= pts[0][0]:
        return pts[0][1]
    if duration_s >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= duration_s <= t1:
            f = math.log(duration_s / t0) / math.log(t1 / t0)
            return v0 + (v1 - v0) * f
    return pts[-1][1]


def limit_kmh(duration_s: float) -> float:
    return wr_speed_kmh(duration_s) * MARGIN


def _arr(x) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in x], dtype=float)


def features(t, dist_km, power=None) -> dict:
    """What the rules read, from the time (s) and distance (km) channels and
    optionally power (W). JSON-safe (cached per file)."""
    out = {"moving_s": 0.0, "distance_km": None, "avg_kmh": None, "best": {}, "avg_power": None,
           "power_n": 0}
    t = _arr(t) if t is not None else np.array([])
    if power is not None:
        p = _arr(power)
        p = p[np.isfinite(p) & (p > 0)]
        out["power_n"] = int(len(p))
        out["avg_power"] = float(p.mean()) if len(p) else None
    if dist_km is None or not len(t):
        return out
    d = _arr(dist_km)
    n = min(len(t), len(d))
    t, d = t[:n], d[:n]
    ok = np.isfinite(t) & np.isfinite(d)
    if ok.sum() < 2:
        return out
    t, d = t[ok], d[ok]
    order = np.argsort(t, kind="stable")
    t, d = t[order], np.maximum.accumulate(d[order])       # distance never goes back
    t0 = float(t[0])
    grid = np.arange(t0, float(t[-1]) + 1.0, 1.0)
    dd = np.interp(grid, t, d)
    v = np.diff(dd) * 3600.0                                 # km/h of each second
    v[v > SPIKE_KMH] = 0.0                                   # a GPS jump is no distance
    moving = v > MOVING_KMH
    out["moving_s"] = float(moving.sum())
    out["distance_km"] = float(v.sum() / 3600.0)
    if moving.any():
        out["avg_kmh"] = float(v[moving].sum() / moving.sum())
    c = np.concatenate([[0.0], np.cumsum(v)])
    for w in WINDOWS_S:
        if len(v) < w:
            continue
        roll = (c[w:] - c[:-w]) / w
        i = int(np.argmax(roll))
        out["best"][str(w)] = [float(roll[i]), float(i)]     # [km/h, start s from the first sample]
    return out


def _minute(s: float) -> str:
    return f"{int(s // 60)}"


def judge(f: Optional[dict], sport_group: str, weight_kg: Optional[float] = None) -> Optional[dict]:
    """The auto verdict: None = fine, else {rule, reason, avg_kmh, ...}.
    Only foot sports (FOOT_GROUPS) are judged."""
    if not f or (sport_group or "").lower() not in FOOT_GROUPS:
        return None
    avg, mv = f.get("avg_kmh"), f.get("moving_s") or 0.0
    base = {"avg_kmh": avg, "moving_s": mv, "distance_km": f.get("distance_km")}
    if avg is not None and mv >= MIN_MOVING_S and avg > limit_kmh(mv):
        return {**base, "rule": "avg_speed", "limit_kmh": limit_kmh(mv),
                "reason": f"疑似交通工具／騎車（均速 {avg:.0f} km/h）"}
    for w in WINDOWS_S:
        b = (f.get("best") or {}).get(str(w))
        if b and b[0] > limit_kmh(w):
            lab = f"{w // 60} 分鐘" if w >= 60 else f"{w} 秒"
            start = b[1]
            return {**base, "rule": "window_speed", "window_s": w, "window_kmh": b[0], "window_start_s": start,
                    "limit_kmh": limit_kmh(w),
                    "reason": (f"疑似交通工具／騎車（第 {_minute(start)}–{_minute(start + w)} 分鐘連續 {lab} "
                               f"{b[0]:.0f} km/h" + (f"，全程均速 {avg:.0f} km/h" if avg is not None else "") + "）")}
    ap, n = f.get("avg_power"), f.get("power_n") or 0
    wt = weight_kg or DEFAULT_WEIGHT_KG
    if ap is not None and n >= MIN_POWER_SAMPLES and ap > POWER_WKG_MAX * wt:
        return {**base, "rule": "power", "avg_power": ap, "limit_w": POWER_WKG_MAX * wt,
                "reason": f"功率不可能是跑步（平均 {ap:.0f} W = {ap / wt:.1f} W/kg）"}
    return None


def decide(auto: Optional[dict], override: Optional[str], enabled: bool = True) -> Optional[dict]:
    """The effective exclusion: {reason, auto, manual, label} or None.
    override "keep" wins over the rule; "exclude" excludes whatever the rule
    says; the setting off disables only the auto rule."""
    if override == KEEP:
        return None
    if override == EXCLUDE:
        why = auto["reason"] if auto else "手動排除"
        return {"reason": why, "manual": True, "auto": auto, "label": f"已排除：{why}" +
                ("（手動）" if auto else "")}
    if auto and enabled:
        return {"reason": auto["reason"], "manual": False, "auto": auto, "label": f"已排除：{auto['reason']}"}
    return None


def read_setting(default: bool = True) -> bool:
    """activities.exclude_bad from the app DB (read-only); `default` without one."""
    try:
        from backend.engine.wko5expr.datasource import read_setting as rs
        v = rs(SETTING_KEY, default)
    except Exception:                       # noqa: BLE001
        return default
    return bool(v) if isinstance(v, bool) else default


def overrides_stamp(rows: Optional[list] = None) -> str:
    """Signature of the stored overrides (a change must rebuild the cached
    Dataset: datasource.source_stamp)."""
    import hashlib
    if rows is None:
        from backend.engine import activity_tags as AT
        rows = AT.load()
    sel = sorted(f"{r.get('start_local')}|{r.get('file') or ''}|{r.get('exclusion')}"
                 for r in rows if r.get("exclusion") in OVERRIDES)
    return hashlib.sha1("\n".join(sel).encode()).hexdigest()[:12] if sel else ""
