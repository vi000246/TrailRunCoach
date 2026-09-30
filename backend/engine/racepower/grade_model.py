"""
Personal grade models — docs/research/racepower-v2.md §3 F7–F9, F13, §7.1.

    GradeRE    RE(g) = speed ÷ (W/kg) per 2 % grade bin, shrunk towards the
               Minetti prior RE₀(g) = RE_flat · Cr(0)/Cr(g) (downhill Cr ratio
               floored at 0.9); v_max(g) = p90 speed of the downhill bins.
    HikeSpeed  v_h(g) per bin, shrunk towards Tobler 6·e^(−3.5·|g + 0.05|) km/h.

Samples come from 100 m windows of the athlete's own activities
(athlete.grade_samples / hike_samples). Shrinkage weight n/(n + 30).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from backend.engine.algorithms import minetti

BIN = 0.02
G_MIN, G_MAX = -0.40, 0.40
SHRINK_N = 30.0
DOWNHILL_FLOOR = 0.9
VMAX_MIN_N = 10
STRYD_VALID_GRADE = 0.08


def _bin_of(g: float) -> int:
    return int(round(max(G_MIN, min(G_MAX, g)) / BIN))


def re_prior(g: float, re_flat: float, walking: bool = False) -> float:
    """F8: RE₀(g) = RE_flat · Cr(0)/Cr(g), with Cr(g)/Cr(0) ≥ 0.9 downhill.

    Source: Minetti et al. 2002 (J Appl Physiol 93:1039–1046) running cost
    Cr(i) = 155.4i⁵ − 30.4i⁴ − 43.3i³ + 46.3i² + 19.5i + 3.6 J/kg/m, 已驗證
    (test_minetti). Using it for Stryd power assumes Stryd ∝ metabolic power:
    已驗證 for 0–8 % (van Rassel et al. 2026, IJSPP 21:597–603), 待驗證 above
    8 % — those segments rely on the personal bins (F7) or are labelled 推估.
    The 0.9 downhill floor (≈ +11 % speed at most) is our choice (自組).

    walking=True: the walking prior RE_flat · Cr(0)/Cw(g) with Minetti's
    walking cost Cw(i) = 280.5i⁵ − 58.7i⁴ − 76.8i³ + 51.9i² + 19.6i + 2.5 (same
    paper, 已驗證 test_minetti). That Stryd power follows walking metabolic
    cost is 待驗證, so walked bins lean on the personal data."""
    if walking:
        c = minetti.cost_of_transport(g, walking=True)
        if g < 0:
            c = max(c, DOWNHILL_FLOOR * minetti.FLAT_WALK)
        return re_flat * minetti.FLAT_RUN / c
    return re_flat / minetti.grade_factor(g, downhill_floor=DOWNHILL_FLOOR)


@dataclass
class GradeRE:
    """F7: personal RE(g) — our own data-driven model (自組, 待驗證 by the §3B
    leave-one-out segment errors). Individual RE is strongly correlated across
    grades (Breiner, Ortiz & Kram 2019), which is why the flat RE is a sound
    prior. v_max is F9 (自組; Townshend et al. 2010 found free-paced descents
    only 13.8 % faster than level; Vernillo et al. 2017 on eccentric load)."""
    re_flat: float
    bins: dict = field(default_factory=dict)       # bin -> {"n", "re", "v90"}
    n_samples: int = 0
    n_activities: int = 0
    walking: bool = False

    def _shrunk(self, b: int, g: float) -> float:
        prior = re_prior(g, self.re_flat, self.walking)
        s = self.bins.get(b)
        if not s or not s["n"]:
            return prior
        w = s["n"] / (s["n"] + SHRINK_N)
        return w * s["re"] + (1 - w) * prior

    def re(self, g: float) -> float:
        """RE at grade g, linearly interpolated between bin centres."""
        g = max(G_MIN, min(G_MAX, g))
        x = g / BIN
        lo = math.floor(x)
        f = x - lo
        a = self._shrunk(lo, lo * BIN)
        if f < 1e-9:
            return a
        b = self._shrunk(lo + 1, (lo + 1) * BIN)
        return a + (b - a) * f

    def data_n(self, g: float) -> int:
        s = self.bins.get(_bin_of(g))
        return int(s["n"]) if s else 0

    def v_max(self, g: float) -> Optional[float]:
        """Downhill speed cap (m/s): the personal p90 speed of that bin (and
        its neighbours when thin); None when there is no data (then only the
        0.9 floor of the prior limits the descent)."""
        if g >= -0.02:
            return None
        b = _bin_of(g)
        vs = [self.bins[x]["v90"] for x in (b,) if x in self.bins and self.bins[x]["n"] >= VMAX_MIN_N]
        if not vs:
            near = [self.bins[x] for x in (b - 1, b + 1) if x in self.bins and self.bins[x]["n"] >= VMAX_MIN_N]
            if not near:
                return None
            return float(np.mean([s["v90"] for s in near]))
        return float(vs[0])

    def trusted(self, g: float) -> bool:
        """False when the segment's target rests on an extrapolation: steeper
        than 8 % with fewer than 30 personal windows in that bin."""
        return abs(g) <= STRYD_VALID_GRADE or self.data_n(g) >= SHRINK_N

    def to_json(self) -> dict:
        rows = []
        for b in range(int(round(G_MIN / BIN)), int(round(G_MAX / BIN)) + 1):
            g = b * BIN
            s = self.bins.get(b) or {}
            rows.append({"grade": g, "n": int(s.get("n", 0)), "median_re": s.get("re"),
                         "prior_re": re_prior(g, self.re_flat, self.walking), "re": self._shrunk(b, g),
                         "v90": s.get("v90"), "v_max": self.v_max(g)})
        return {"re_flat": self.re_flat, "n_samples": self.n_samples, "n_activities": self.n_activities,
                "bins": rows, "shrink_n": SHRINK_N}


def fit_grade_re(samples: Sequence[dict], re_flat: float, walking: bool = False) -> GradeRE:
    """samples = [{"g", "re", "v", "a"(activity idx)}] → GradeRE."""
    by: dict[int, list] = {}
    acts = set()
    for s in samples:
        if s.get("re") is None or not math.isfinite(s["re"]) or s["re"] <= 0:
            continue
        by.setdefault(_bin_of(s["g"]), []).append(s)
        acts.add(s.get("a"))
    bins = {}
    for b, ss in by.items():
        res = np.array([s["re"] for s in ss])
        vs = np.array([s["v"] for s in ss])
        bins[b] = {"n": len(ss), "re": float(np.median(res)), "v90": float(np.percentile(vs, 90))}
    return GradeRE(re_flat, bins, sum(len(v) for v in by.values()), len(acts), walking)


# ---- gait-aware RE(g) + trail technicality ------------------------------------

WALK_MAJORITY = 0.5            # 自組: a window / bin is walked when ≥ half its moving time is < 130 spm
TECH_MIN_N = 30                # windows for a per-class technicality factor (= SHRINK_N)
TECH_BOUNDS = (0.6, 1.2)       # 自組 sanity range for the factor


@dataclass
class GaitRE:
    """RE(g) per gait. Running windows (cadence ≥ 130 spm for ≥ half the
    window, workout_review.RUN_CADENCE) fit `run` (Minetti running prior);
    walked windows fit `walk` (Minetti walking prior). For each 2 % bin the
    athlete's own majority gait decides which curve predicts that grade
    (自組; Minetti's running cost does not describe walking — Minetti 2002,
    Giovanelli 2016 on the walk/run crossover). `tech` is the trail
    technicality factor on flats and descents (g ≤ +2 %): median of actual ÷
    predicted RE over the athlete's own trail running windows there (自組,
    per intensity class when ≥ 30 windows), applied only with `trail=True`.
    Duck-compatible with GradeRE for the planner and the back-test."""
    run: GradeRE
    walk: GradeRE
    walk_bins: dict = field(default_factory=dict)      # bin -> [n_walked, n_total]
    tech: dict = field(default_factory=dict)           # class|"all" -> {"f", "n"}
    trail: bool = False
    tech_class: Optional[str] = None

    @property
    def re_flat(self) -> float:
        return self.run.re_flat

    @property
    def bins(self) -> dict:
        return self.run.bins

    @property
    def n_samples(self) -> int:
        return self.run.n_samples + self.walk.n_samples

    @property
    def n_activities(self) -> int:
        return self.run.n_activities

    def walked(self, g: float) -> bool:
        wb = self.walk_bins.get(_bin_of(g))
        return bool(wb and wb[1] >= VMAX_MIN_N and wb[0] / wb[1] >= WALK_MAJORITY and self.walk.data_n(g) > 0)

    def tech_factor(self) -> tuple[float, str]:
        t = self.tech.get(self.tech_class) if self.tech_class else None
        if t and t["n"] >= TECH_MIN_N:
            return t["f"], self.tech_class
        t = self.tech.get("all")
        if t and t["n"] >= TECH_MIN_N:
            return t["f"], "all"
        return 1.0, "none"

    def re(self, g: float) -> float:
        v = self.walk.re(g) if self.walked(g) else self.run.re(g)
        if self.trail and g <= 0.02:
            v *= self.tech_factor()[0]
        return v

    def v_max(self, g: float) -> Optional[float]:
        return self.run.v_max(g)

    def trusted(self, g: float) -> bool:
        m = self.walk if self.walked(g) else self.run
        return abs(g) <= STRYD_VALID_GRADE or m.data_n(g) >= SHRINK_N

    def data_n(self, g: float) -> int:
        return (self.walk if self.walked(g) else self.run).data_n(g)

    def with_flat(self, re_flat: float) -> "GaitRE":
        from dataclasses import replace
        return replace(self, run=replace(self.run, re_flat=re_flat), walk=replace(self.walk, re_flat=re_flat))

    def for_trail(self, cls: Optional[str] = None) -> "GaitRE":
        from dataclasses import replace
        return replace(self, trail=True, tech_class=cls)

    def to_json(self) -> dict:
        j = self.run.to_json()
        wj = self.walk.to_json()
        for r, w in zip(j["bins"], wj["bins"]):
            wb = self.walk_bins.get(_bin_of(r["grade"])) or [0, 0]
            r.update(walk_n=w["n"], walk_re=w["re"], walk_median_re=w["median_re"], walk_prior_re=w["prior_re"],
                     walk_share=(wb[0] / wb[1]) if wb[1] else None, gait="walk" if self.walked(r["grade"]) else "run")
        f, which = self.tech_factor()
        j.update(walk_samples=self.walk.n_samples, tech=self.tech, tech_used={"f": f, "class": which})
        return j


def fit_gait_re(samples: Sequence[dict], re_flat: float, classes: Optional[dict] = None) -> GaitRE:
    """samples = [{"g", "re", "v", "a", "run" (running share of the window),
    "trail"}]; classes = {activity idx: intensity class} for the per-class
    technicality factor."""
    run_s = [s for s in samples if s.get("run") is None or s["run"] >= WALK_MAJORITY]
    walk_s = [s for s in samples if s.get("run") is not None and s["run"] < WALK_MAJORITY]
    run = fit_grade_re(run_s, re_flat)
    walk = fit_grade_re(walk_s, re_flat, walking=True)
    wb: dict = {}
    for s in samples:
        if s.get("run") is None:
            continue
        b = _bin_of(s["g"])
        x = wb.setdefault(b, [0, 0])
        x[1] += 1
        if s["run"] < WALK_MAJORITY:
            x[0] += 1
    tech: dict = {}
    ratios: dict = {}
    for s in run_s:
        if not s.get("trail") or s["g"] > 0.02 or not s.get("re"):
            continue
        r = s["re"] / run.re(s["g"])
        ratios.setdefault("all", []).append(r)
        c = (classes or {}).get(s.get("a"))
        if c:
            ratios.setdefault(c, []).append(r)
    for c, rs in ratios.items():
        f = float(np.median(rs))
        tech[c] = {"f": min(TECH_BOUNDS[1], max(TECH_BOUNDS[0], f)), "raw": f, "n": len(rs)}
    return GaitRE(run, walk, wb, tech)


def tobler_kmh(g: float) -> float:
    """Tobler 1993: W = 6·exp(−3.5·|S + 0.05|) km/h (via Wikipedia; 已驗證
    second-hand: S = −0.05 → 6.00, S = 0 → 5.04; V-F13)."""
    return 6.0 * math.exp(-3.5 * abs(g + 0.05))


@dataclass
class HikeSpeed:
    """F13: personal walking speed per grade bin, shrunk towards Tobler
    (Tobler's shape is 經驗法則; the personal fit is 自組, 待驗證 by the §3B
    hiking-day back-test). ref_alt_m = median elevation of the samples, so
    the altitude factor is applied relative to where the speeds were walked."""
    bins: dict = field(default_factory=dict)       # bin -> {"n", "v"} (m/s)
    ref_alt_m: Optional[float] = None
    n_samples: int = 0
    n_days: int = 0

    def _shrunk(self, b: int, g: float) -> float:
        prior = tobler_kmh(g) / 3.6
        s = self.bins.get(b)
        if not s or not s["n"]:
            return prior
        w = s["n"] / (s["n"] + SHRINK_N)
        return w * s["v"] + (1 - w) * prior

    def v(self, g: float) -> float:
        g = max(G_MIN, min(G_MAX, g))
        x = g / BIN
        lo = math.floor(x)
        f = x - lo
        a = self._shrunk(lo, lo * BIN)
        if f < 1e-9:
            return a
        return a + (self._shrunk(lo + 1, (lo + 1) * BIN) - a) * f

    def to_json(self) -> dict:
        rows = []
        for b in range(int(round(G_MIN / BIN)), int(round(G_MAX / BIN)) + 1):
            g = b * BIN
            s = self.bins.get(b) or {}
            rows.append({"grade": g, "n": int(s.get("n", 0)), "median_v": s.get("v"),
                         "tobler_v": tobler_kmh(g) / 3.6, "v": self._shrunk(b, g)})
        return {"ref_alt_m": self.ref_alt_m, "n_samples": self.n_samples, "n_days": self.n_days, "bins": rows}


def fit_hike_speed(samples: Sequence[dict]) -> HikeSpeed:
    """samples = [{"g", "v" (m/s), "z", "a"}]; resting windows (< 0.3 m/s)
    are dropped by the sampler."""
    by: dict[int, list] = {}
    zs, acts = [], set()
    for s in samples:
        by.setdefault(_bin_of(s["g"]), []).append(s["v"])
        if s.get("z") is not None:
            zs.append(s["z"])
        acts.add(s.get("a"))
    bins = {b: {"n": len(v), "v": float(np.median(v))} for b, v in by.items()}
    return HikeSpeed(bins, float(np.median(zs)) if zs else None, sum(len(v) for v in by.values()), len(acts))


# ---- window sampling (shared by athlete.py and the back-test) -----------------

def windows(t: np.ndarray, d_m: np.ndarray, z: np.ndarray, p: Optional[np.ndarray],
            moving: np.ndarray, win_m: float = 100.0, hr: Optional[np.ndarray] = None,
            cadence: Optional[np.ndarray] = None, run_cadence: float = 65.0) -> list[dict]:
    """100 m windows along the distance, using moving samples only: grade,
    speed (m/s) and time-weighted power; with `hr` the time-weighted HR, with
    `cadence` (strides/min) the running share of the moving time (cadence ≥
    run_cadence). Arrays are sample-aligned; d_m is cumulative metres.
    Returns [{"g", "v", "p", "z", "hr"?, "run"?}]."""
    n = len(t)
    if n < 10:
        return []
    dt_ = np.diff(t, prepend=t[0])
    dt_[~np.isfinite(dt_) | (dt_ < 0) | (dt_ > 60)] = 0.0
    mv = moving & (dt_ > 0)
    ct = np.cumsum(np.where(mv, dt_, 0.0))
    ce = None
    if p is not None:
        pp = np.nan_to_num(p)
        ce = np.cumsum(np.where(mv, pp * dt_, 0.0))
    ch = chn = None
    if hr is not None:
        hv = np.asarray(hr, float)[:n]
        okh = mv & np.isfinite(hv) & (hv > 40)
        ch = np.cumsum(np.where(okh, hv * dt_, 0.0))
        chn = np.cumsum(np.where(okh, dt_, 0.0))
    cr = crn = None
    if cadence is not None:
        cv = np.asarray(cadence, float)[:n]
        okc = mv & np.isfinite(cv) & (cv > 0)
        cr = np.cumsum(np.where(okc & (cv >= run_cadence), dt_, 0.0))
        crn = np.cumsum(np.where(okc, dt_, 0.0))
    d = np.maximum.accumulate(np.nan_to_num(d_m))
    ok = np.isfinite(z)
    if ok.sum() < 10 or d[-1] < 2 * win_m:
        return []
    zf = np.interp(np.arange(n), np.nonzero(ok)[0], z[ok])
    edges = np.arange(0.0, d[-1], win_m)
    if len(edges) < 2:
        return []
    # the first sample reaching each edge
    j = np.searchsorted(d, edges)
    j = np.clip(j, 0, n - 1)
    out = []
    for k_, (a, b) in enumerate(zip(j[:-1], j[1:])):
        if b <= a:
            continue
        tm = ct[b] - ct[a]
        dd = d[b] - d[a]
        if tm <= 0 or dd <= 0.5 * win_m:
            continue
        v = dd / tm
        row = {"g": float((zf[b] - zf[a]) / dd), "v": float(v), "z": float(zf[a]), "k": k_}
        if ce is not None:
            row["p"] = float((ce[b] - ce[a]) / tm)
        if ch is not None and chn[b] - chn[a] > 0.5 * tm:
            row["hr"] = float((ch[b] - ch[a]) / (chn[b] - chn[a]))
        if cr is not None and crn[b] - crn[a] > 0.5 * tm:
            row["run"] = float((cr[b] - cr[a]) / (crn[b] - crn[a]))
        out.append(row)
    return out
