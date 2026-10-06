"""
Walk or run on a climb: one answer from grade × speed — docs/research/
run-walk-threshold.md (SP-197) §3.1, §5.1, §5.3; SP-226.

Every uphill grade has a transition speed: slower than it, walking costs less;
faster, running does; the steeper the grade, the lower that speed (Brill &
Kram 2021; Finiel 2026; Ortiz 2017). Two curves give three answers (the owner's
decision, 2026-10-05):

    speed < PTS(g)            "walk"     走
    PTS(g) ≤ speed < EOTS(g)  "either"   走跑皆可 (people prefer running, walking is a bit cheaper)
    speed ≥ EOTS(g)           "run"      跑

    PTS   preferred (self-selected) transition speed
    EOTS  energetically optimal transition speed (walk and run cost the same)

The curves (§5.1), as treadmill belt speed (m/s along the slope):

    0–15°    PTS = 1.945 − 0.032 × angle (°): the straight line through Brill &
             Kram 2021's four measured PTS (1.95 / 1.78 / 1.62 / 1.47 m/s at
             0 / 5 / 10 / 15°), ≤ 0.005 m/s off at each (推估, the fit).
             EOTS: their measured 2.14 / 1.99 / 1.78 / 1.51 m/s, linearly
             interpolated (推估 between the points).
    15–30°   both straight to Ortiz 2017's 0.80 m/s at 30° (walking and running
             cost the same there); nothing measured in between (推估).
    > 30°    a constant vertical speed of 0.40 m/s (1,440 m/h, = 0.80 m/s at
             30°): Ortiz's single point carried on (推估).

Brill & Kram measured ten high-level male trail runners, fresh, on a treadmill,
no poles: applying it to everyone is 推估 (the owner's decision: the default
curve is used for everyone and the page says 預設值; SP-228 shifts it with the
athlete's own windows). `shift` (m/s) moves both curves by the same amount.

Only climbs: grade ≥ 3 % (seg_targets' runnable-climb lower bound, 推估);
flats and descents get None. The app's speeds are horizontal (map distance ÷
time); the belt speed is that × √(1 + g²).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from backend.i18n import N_, _

MIN_GRADE = 0.03                       # 推估: only climbs (seg_targets.RUN_CLIMB[0])
BK_DEG = (0.0, 5.0, 10.0, 15.0)        # Brill & Kram 2021 (J Exp Biol 224:jeb233056), 已驗證 full text
BK_PTS = (1.95, 1.78, 1.62, 1.47)      # preferred transition speed, m/s (belt)
BK_EOTS = (2.14, 1.99, 1.78, 1.51)     # energetically optimal transition speed, m/s (belt)
PTS_FLAT, PTS_PER_DEG = 1.945, 0.032   # 推估: the straight line through BK_PTS (§3.1)
ORTIZ_DEG, ORTIZ_V = 30.0, 0.80        # Ortiz, Giovanelli & Kram 2017 (Eur J Appl Physiol): 30°, costs equal at 0.8 m/s (摘要)
STEEP_VERT_MS = ORTIZ_V * math.sin(math.radians(ORTIZ_DEG))   # 0.40 m/s vertical above 30° (推估)
MIN_SPEED = 0.10                       # 推估: a shifted curve never goes below this (m/s)

GAITS = ("walk", "either", "run")
LABEL = {"walk": N_("走"), "either": N_("走跑皆可"), "run": N_("跑")}


def angle_deg(grade: float) -> float:
    return math.degrees(math.atan(grade))


def _steep(deg: float, at15: float) -> float:
    """15–30°: straight from the 15° value to Ortiz's 0.8 m/s; beyond, 0.4 m/s vertical."""
    if deg <= ORTIZ_DEG:
        return at15 + (ORTIZ_V - at15) * (deg - BK_DEG[-1]) / (ORTIZ_DEG - BK_DEG[-1])
    return STEEP_VERT_MS / math.sin(math.radians(deg))


def pts(grade: float, shift: float = 0.0) -> float:
    """Preferred walk–run transition speed at `grade` (m/s along the slope)."""
    deg = max(0.0, angle_deg(grade))
    if deg <= BK_DEG[-1]:
        v = PTS_FLAT - PTS_PER_DEG * deg
    else:
        v = _steep(deg, PTS_FLAT - PTS_PER_DEG * BK_DEG[-1])
    return max(MIN_SPEED, v + shift)


def eots(grade: float, shift: float = 0.0) -> float:
    """Energetically optimal walk–run transition speed at `grade` (m/s along the slope)."""
    deg = max(0.0, angle_deg(grade))
    if deg <= BK_DEG[-1]:
        v = float(np.interp(deg, BK_DEG, BK_EOTS))
    else:
        v = _steep(deg, BK_EOTS[-1])
    return max(MIN_SPEED, v + shift)


def belt_speed(grade: float, speed_ms: float) -> float:
    """Horizontal (map) speed → speed along the slope."""
    return speed_ms * math.sqrt(1.0 + grade * grade)


def horizontal(grade: float, belt_ms: float) -> float:
    """Speed along the slope → horizontal (map) speed."""
    return belt_ms / math.sqrt(1.0 + grade * grade)


def gait(grade: Optional[float], speed_ms: Optional[float], shift: float = 0.0) -> Optional[str]:
    """"walk" / "either" / "run" for a climb of `grade` (rise ÷ run) at the
    horizontal `speed_ms`; None below 3 % or without a speed."""
    if grade is None or speed_ms is None or grade < MIN_GRADE or not speed_ms > 0 or not math.isfinite(speed_ms):
        return None
    v = belt_speed(grade, speed_ms)
    if v < pts(grade, shift):
        return "walk"
    if v < eots(grade, shift):
        return "either"
    return "run"


def label(g: Optional[str]) -> Optional[str]:
    """The short label of a gait (走 / 走跑皆可 / 跑), None for None."""
    return _(LABEL[g]) if g in LABEL else None


def walk_label(g: Optional[str]) -> Optional[str]:
    """The segment's `walk` field: the label when the climb is walked or
    either (走 / 走跑皆可), None when it is run or not a climb."""
    return label(g) if g in ("walk", "either") else None


# ---- is 「步頻 < 130 spm 算走」 right for this athlete? (SP-230, §4, §5.2) ----------------
#
# Every walk / run split in the app is cadence < 130 spm (workout_review.RUN_CADENCE, 65
# strides/min; 推估). Outdoors on climbs cadence is bimodal (Sanchez 2024, 摘要) and running
# steps are ~40 % quicker than walking ones on steep ground (Whiting 2020, 摘要), so the line
# should sit in the valley between the two peaks. This only reports; it never moves the line.

CAD_BIN_SPM = 5                 # histogram bin (spm)
CAD_MAX_SPM = 250
THRESHOLD_SPM = 130.0           # = 2 × workout_review.RUN_CADENCE
CHECK_MIN_S = 1800.0            # 推估: under 30 min of climbing with cadence, no verdict
PEAK_GAP_SPM = 20.0             # 推估: two peaks at least this far apart
PEAK_MIN_SHARE = 0.05           # 推估: each side of the valley holds ≥ 5 % of the time
VALLEY_RATIO = 0.5              # 推估: the valley ≤ half the lower peak = two groups
VALLEY_BAND = 0.25              # 推估: 130 counts as 「in the valley」 within the lowest quarter between the two
CAD_SOURCE = N_("步頻雙峰：Sanchez 2024（戶外上坡，摘要）；陡坡跑的步頻比走快約 40 %：Whiting 2020（摘要）；"
                "峰、谷的判斷門檻為推估")


def cad_edges() -> np.ndarray:
    return np.arange(0.0, CAD_MAX_SPM + CAD_BIN_SPM, CAD_BIN_SPM)


def climb_cadence_hist(t, d_m, z, cadence, moving, win_m: float = 100.0,
                       min_grade: float = MIN_GRADE) -> Optional[list]:
    """Seconds of moving time per 5-spm cadence bin on climbs (the 100 m window's grade ≥
    3 %, the same windows as grade_model.windows). `cadence` in strides/min (the FIT
    running cadence; spm = × 2); arrays sample-aligned, `d_m` cumulative metres. None
    without cadence or elevation."""
    t = np.asarray(t, float)
    n = len(t)
    if cadence is None or z is None or n < 10:
        return None
    cad = np.asarray(cadence, float)[:n]
    zz = np.asarray(z, float)[:n]
    ok = np.isfinite(zz)
    if ok.sum() < 10 or not np.any(np.isfinite(cad) & (cad > 0)):
        return None
    dt_ = np.diff(t, prepend=t[0])
    dt_[~np.isfinite(dt_) | (dt_ < 0) | (dt_ > 60)] = 0.0
    d = np.maximum.accumulate(np.nan_to_num(np.asarray(d_m, float)[:n]))
    if d[-1] < 2 * win_m:
        return None
    zf = np.interp(np.arange(n), np.nonzero(ok)[0], zz[ok])
    j = np.clip(np.searchsorted(d, np.arange(0.0, d[-1], win_m)), 0, n - 1)
    g = np.full(n, np.nan)
    for a, b in zip(j[:-1], j[1:]):
        dd = d[b] - d[a]
        if b > a and dd > 0.5 * win_m:
            g[a:b] = (zf[b] - zf[a]) / dd
    sel = np.asarray(moving, bool)[:n] & (dt_ > 0) & np.isfinite(cad) & (cad > 0) & (np.nan_to_num(g, nan=-1.0) >= min_grade)
    if not sel.any():
        return [0.0] * (len(cad_edges()) - 1)
    h, _e = np.histogram(np.clip(2.0 * cad[sel], 0.0, CAD_MAX_SPM - 1e-6), bins=cad_edges(), weights=dt_[sel])
    return [round(float(x), 1) for x in h]


def cadence_check(seconds, threshold_spm: float = THRESHOLD_SPM) -> dict:
    """Where the athlete's climbing cadence splits into walking and running, against the
    130 spm line: {enough, total_s, bimodal, walk_peak_spm, run_peak_spm, valley_spm,
    in_valley, below_share, bins, seconds, threshold_spm, verdict, hint}. Peaks / valley on
    the 1-2-1 smoothed histogram (all limits 推估); verdict ok / off / unimodal / few."""
    s = np.asarray(seconds if seconds is not None else [], float)
    edges = cad_edges()
    mids = (edges[:-1] + edges[1:]) / 2.0
    if len(s) != len(mids):
        s = np.zeros(len(mids))
    total = float(s.sum())
    out = {"bins": [float(x) for x in mids], "seconds": [float(x) for x in s], "total_s": total,
           "threshold_spm": threshold_spm, "bimodal": None, "walk_peak_spm": None, "run_peak_spm": None,
           "valley_spm": None, "in_valley": None, "enough": total >= CHECK_MIN_S, "source": _(CAD_SOURCE),
           "below_share": float(s[mids < threshold_spm].sum() / total) if total > 0 else None}
    if not out["enough"]:
        out.update(verdict="few", hint=_("爬坡（≥ 3 %）有步頻的時間不到 {m:.0f} 分鐘，還不能檢查",
                                         m=CHECK_MIN_S / 60))
        return out
    sm = np.convolve(np.pad(s, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    top = int(np.argmax(sm))
    gap = int(round(PEAK_GAP_SPM / CAD_BIN_SPM))
    peaks = [i for i in range(len(sm)) if sm[i] > 0 and sm[i] >= sm[max(0, i - 1)] and sm[i] >= sm[min(len(sm) - 1, i + 1)]
             and abs(i - top) >= gap]
    best = None
    for i in sorted(peaks, key=lambda i: -sm[i]):
        a, b = sorted((top, i))
        v = a + int(np.argmin(sm[a:b + 1]))
        lo_share, hi_share = s[:v].sum() / total, s[v + 1:].sum() / total
        if min(lo_share, hi_share) >= PEAK_MIN_SHARE and sm[v] <= VALLEY_RATIO * min(sm[a], sm[b]):
            best = (a, b, v)
            break
    if best is None:
        out.update(bimodal=False, walk_peak_spm=float(mids[top]), verdict="unimodal",
                   hint=_("爬坡的步頻只有一群（約 {p:.0f} spm），分不出走和跑兩群：130 spm 這條線沒辦法用你的資料確認",
                          p=mids[top]))
        return out
    a, b, v = best
    lim = sm[v] + VALLEY_BAND * (min(sm[a], sm[b]) - sm[v])
    k = int(np.clip(np.searchsorted(edges, threshold_spm, side="right") - 1, 0, len(mids) - 1))
    # the threshold sits on a bin edge: look at both bins it separates
    near = [x for x in (k - 1, k) if a <= x <= b]
    in_valley = bool(near) and min(sm[x] for x in near) <= lim
    out.update(bimodal=True, walk_peak_spm=float(mids[a]), run_peak_spm=float(mids[b]), valley_spm=float(mids[v]),
               in_valley=in_valley, verdict="ok" if in_valley else "off")
    if in_valley:
        out["hint"] = _("走路一群約 {w:.0f} spm、跑步一群約 {r:.0f} spm，130 spm 落在兩群之間的谷底附近（谷底約 {v:.0f} spm）：門檻合用",
                        w=mids[a], r=mids[b], v=mids[v])
    elif mids[v] > threshold_spm:
        out["hint"] = _("走路一群約 {w:.0f} spm、跑步一群約 {r:.0f} spm，谷底在約 {v:.0f} spm，不在 130："
                        "130–{v:.0f} spm 的快走會被算成跑步。門檻先不改", w=mids[a], r=mids[b], v=mids[v])
    else:
        out["hint"] = _("走路一群約 {w:.0f} spm、跑步一群約 {r:.0f} spm，谷底在約 {v:.0f} spm，不在 130："
                        "{v:.0f}–130 spm 的慢跑會被算成走路。門檻先不改", w=mids[a], r=mids[b], v=mids[v])
    return out
