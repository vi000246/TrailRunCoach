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


# ---- personal calibration of the transition speed (SP-228, §5.2, §8) --------------------
#
# The default curve is ten fresh high-level men on a treadmill. Each 2 % grade bin of the
# athlete's own 100 m windows (grade_model.windows: grade, speed, running share by cadence)
# gives the speed where the windows turn from walked to run; the median of (that − the
# default PTS) over the bins is one shift Δ of the whole curve, shrunk n/(n + 30) towards 0
# and held within ±0.4 m/s. Heart-rate crossovers are not used (Brill & Kram 2021: HR does
# not predict the EOTS; Finiel 2026: the HR crossover differs from the preferred one).

CAL_BIN = 0.02                  # = grade_model.BIN
CAL_MIN_EACH = 10               # walked and run windows each in a bin (grade_model.VMAX_MIN_N; 推估)
CAL_SHRINK_N = 30.0             # n/(n + 30), n = the windows of the bins used (grade_model.SHRINK_N; 推估)
CAL_MAX_SHIFT = 0.4             # m/s (推估)
CAL_MAX_GRADE = 0.40            # grade_model.G_MAX
CAL_RUN_SHARE = 0.5             # a window is run when ≥ half its moving time is ≥ 130 spm (grade_model.WALK_MAJORITY)


def split_speed(walk_v, run_v) -> Optional[float]:
    """The speed that best splits walked windows (below) from run ones (above): the cut
    with the fewest windows on the wrong side, the middle of the best cuts (推估: the
    speed where the athlete is as likely to walk as to run)."""
    w = np.sort(np.asarray(walk_v, float))
    r = np.sort(np.asarray(run_v, float))
    if not len(w) or not len(r):
        return None
    cuts = np.unique(np.concatenate([w, r]))
    cuts = np.concatenate([[cuts[0] - 1e-6], (cuts[:-1] + cuts[1:]) / 2.0, [cuts[-1] + 1e-6]])
    wrong = (len(w) - np.searchsorted(w, cuts, side="left")) + np.searchsorted(r, cuts, side="left")
    best = cuts[wrong == wrong.min()]
    return float(np.median(best))


def _bin_of(g: float) -> int:
    return int(round(max(-CAL_MAX_GRADE, min(CAL_MAX_GRADE, g)) / CAL_BIN))


def fit_shift(samples) -> dict:
    """samples = [{"g", "v" (horizontal m/s), "run" (running share or None)}] (athlete.
    grade_samples) → {shift, raw, n, weight, bins, personal}. `bins`: every climbing bin
    with its walked / run windows, and where both reach CAL_MIN_EACH the split speed (belt)
    and its difference from the default PTS. `personal` = False (the default curve) when no
    bin qualifies."""
    by: dict[int, tuple[list, list]] = {}
    for s in samples or []:
        g, v, run = s.get("g"), s.get("v"), s.get("run")
        if g is None or v is None or run is None or g < MIN_GRADE - CAL_BIN / 2 or not v > 0:
            continue
        wr = by.setdefault(_bin_of(g), ([], []))
        (wr[1] if run >= CAL_RUN_SHARE else wr[0]).append(float(v))
    bins, diffs, n = [], [], 0
    for b in sorted(by):
        g = b * CAL_BIN
        if g < MIN_GRADE:
            continue
        wv, rv = by[b]
        row = {"grade": g, "n_walk": len(wv), "n_run": len(rv), "split_ms": None, "diff": None,
               "pts": pts(g), "used": False}
        if len(wv) >= CAL_MIN_EACH and len(rv) >= CAL_MIN_EACH:
            v = split_speed(wv, rv)
            if v is not None:
                belt = belt_speed(g, v)
                row.update(split_ms=v, split_belt=belt, diff=belt - pts(g), used=True)
                diffs.append(belt - pts(g))
                n += len(wv) + len(rv)
        bins.append(row)
    if not diffs:
        return {"shift": 0.0, "raw": None, "n": 0, "weight": 0.0, "bins": bins, "personal": False}
    raw = float(np.median(diffs))
    w = n / (n + CAL_SHRINK_N)
    shift = float(np.clip(w * raw, -CAL_MAX_SHIFT, CAL_MAX_SHIFT))
    return {"shift": shift, "raw": raw, "n": n, "weight": w, "bins": bins, "personal": True,
            "clamped": abs(w * raw) > CAL_MAX_SHIFT}


def curve_json(shift: float = 0.0, step: float = 0.01) -> list[dict]:
    """The default and the shifted curves, horizontal m/s, every 1 % from 3 to 40 % (the page)."""
    out = []
    for i in range(int(round(MIN_GRADE / step)), int(round(CAL_MAX_GRADE / step)) + 1):
        g = i * step
        out.append({"grade": g, "pts": horizontal(g, pts(g)), "eots": horizontal(g, eots(g)),
                    "pts_you": horizontal(g, pts(g, shift)), "eots_you": horizontal(g, eots(g, shift))})
    return out


# ---- 「你爬多快，就在幾 % 改走」 (§3.2; SP-298) ------------------------------------------------
WALK_GRADE_STEP = 0.001                # search step of walk_grade (0.1 %)


def walk_grade(vam_mh: Optional[float], shift: float = 0.0, max_grade: float = 1.0) -> Optional[float]:
    """The grade above which a climb at `vam_mh` (vertical m/h) is walked: the smallest grade
    ≥ MIN_GRADE where gait() at that climbing rate's horizontal speed (vam / 3600 / grade) is
    "walk" — below the PTS line, as on the segment labels. §3.2's table: 500 m/h ≈ 8 %, 700 ≈ 11 %,
    900 ≈ 15 %, 1,400 ≈ 28 % (推估, the default curve). None without a rate, or when the rate is
    still run at `max_grade`."""
    if vam_mh is None or not math.isfinite(vam_mh) or vam_mh <= 0:
        return None
    for i in range(int(round(MIN_GRADE / WALK_GRADE_STEP)), int(round(max_grade / WALK_GRADE_STEP)) + 1):
        g = i * WALK_GRADE_STEP
        if gait(g, vam_mh / 3600.0 / g, shift) == "walk":
            return g
    return None
