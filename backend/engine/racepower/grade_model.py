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
# SP-246: the grades where Stryd power ≈ metabolic load without personal data: flats (±2 %, the
# course's flat, course.FLAT_PCT) up to +8 % — van Rassel et al. 2026 (IJSPP 21:597–603) validated
# 0–8 % uphill only; at −7 % power and VO2 decouple (−7→+7 %: power +90 %, VO2 +74 %,
# Gravina-Cognetti et al. 2025, Sports 13:294), so a descent below −2 % needs its own windows
STRYD_VALID_GRADE = 0.08
STRYD_VALID_DOWN = -0.02


def _bin_of(g: float) -> int:
    return int(round(max(G_MIN, min(G_MAX, g)) / BIN))


def stryd_valid(g: float) -> bool:
    """SP-246: is grade g inside the range Stryd power was validated on (−2 % … +8 %)?"""
    return STRYD_VALID_DOWN <= g <= STRYD_VALID_GRADE


def re_prior(g: float, re_flat: float, walking: bool = False) -> float:
    """F8: RE₀(g) = RE_flat · Cr(0)/Cr(g), with Cr(g)/Cr(0) ≥ 0.9 downhill.

    Source: Minetti et al. 2002 (J Appl Physiol 93:1039–1046) running cost
    Cr(i) = 155.4i⁵ − 30.4i⁴ − 43.3i³ + 46.3i² + 19.5i + 3.6 J/kg/m, 已驗證
    (test_minetti). Using it for Stryd power assumes Stryd ∝ metabolic power:
    已驗證 for 0–8 % uphill (van Rassel et al. 2026, IJSPP 21:597–603; flats
    within ±2 % count with it), 待驗證 above 8 % and on descents below −2 %
    (at −7 % power and VO2 decouple, Gravina-Cognetti et al. 2025) — those
    segments rely on the personal bins (F7) or are labelled 推估 (stryd_valid,
    trusted).
    The 0.9 downhill floor (≈ +11 % speed at most) is our choice (推估).

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
    """F7: personal RE(g) — our own data-driven model (推估, 待驗證 by the §3B
    leave-one-out segment errors). Individual RE is strongly correlated across
    grades (Breiner, Ortiz & Kram 2019), which is why the flat RE is a sound
    prior. v_max is F9 (推估; Townshend et al. 2010 found free-paced descents
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

    def v_max(self, g: float, q: int = 90) -> Optional[float]:
        """Downhill speed cap (m/s): the personal p`q` speed of that bin (and
        its neighbours when thin); None when there is no data (then only the
        0.9 floor of the prior limits the descent). q = 90 (road) or 50
        (trail, GaitRE.v_max: unsourced-rules.md §A4 — p90 is "the best
        descent", not what a race holds; 推估)."""
        if g >= -0.02:
            return None
        key = f"v{q}"
        b = _bin_of(g)
        vs = [self.bins[x].get(key, self.bins[x]["v90"]) for x in (b,)
              if x in self.bins and self.bins[x]["n"] >= VMAX_MIN_N]
        if not vs:
            near = [self.bins[x] for x in (b - 1, b + 1) if x in self.bins and self.bins[x]["n"] >= VMAX_MIN_N]
            if not near:
                return None
            return float(np.mean([s.get(key, s["v90"]) for s in near]))
        return float(vs[0])

    def trusted(self, g: float) -> bool:
        """False when the segment's target rests on an extrapolation: outside
        −2 % … +8 % (stryd_valid; SP-246: a descent too) with fewer than 30
        personal windows in that bin."""
        return stryd_valid(g) or self.data_n(g) >= SHRINK_N

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
        bins[b] = {"n": len(ss), "re": float(np.median(res)), "v90": float(np.percentile(vs, 90)),
                   "v50": float(np.percentile(vs, 50))}
    return GradeRE(re_flat, bins, sum(len(v) for v in by.values()), len(acts), walking)


# ---- gait-aware RE(g) + trail technicality ------------------------------------

WALK_MAJORITY = 0.5            # 推估: a window / bin is walked when ≥ half its moving time is < 130 spm
TECH_MIN_N = 30                # windows for a per-class technicality factor (= SHRINK_N)
TECH_BOUNDS = (0.6, 1.2)       # 推估 sanity range for the factor
# technicality by grade bin (2026-10-02, unsourced-rules.md §A4: the one factor on g ≤ +2 %
# missed the steep descents, ≤ −15 % ran 20–23 % too fast): the back-test's downhill bins,
# each shrunk n/(n + 30) towards 1 (推估)
TECH_BIN_EDGES = (-0.15, -0.08, -0.02, 0.02)
TECH_BIN_LABELS = ("≤ −15%", "−15…−8%", "−8…−2%", "±2%")
TRAIL_VMAX_Q = 50              # 推估: the trail descent cap = the personal median speed of the bin
# 路況 split (SP-250, docs/research/wet-muddy-terrain.md §4 #2, §5 #2): no study gives a wet-trail
# running slowdown, so a wet factor only comes from the athlete's OWN runs marked 濕 (乾 / 濕 /
# 未標, activity_tags.SURFACES). Split only when the marked-dry AND the marked-wet trail windows
# on g ≤ +2 % each number ≥ SURFACE_MIN_N (ticket); then per technicality bin each group's median
# ratio is shrunk n/(n + 30) toward the pooled bin factor (推估: a thin group stays near what all
# the data say, not near 1). Unmarked runs stay in the pooled factor only.
SURFACE_MIN_N = 30             # SP-250 acceptance: ≥ 30 windows in each group (= SHRINK_N)


def tech_bin(g: float) -> Optional[str]:
    """The technicality bin of grade g (None above +2 %)."""
    if g > TECH_BIN_EDGES[-1]:
        return None
    for e, lab in zip(TECH_BIN_EDGES, TECH_BIN_LABELS):
        if g < e or (e == TECH_BIN_EDGES[-1] and g <= e):
            return lab
    return TECH_BIN_LABELS[-1]


@dataclass
class GaitRE:
    """RE(g) per gait. Running windows (cadence ≥ 130 spm for ≥ half the
    window, workout_review.RUN_CADENCE) fit `run` (Minetti running prior);
    walked windows fit `walk` (Minetti walking prior). For each 2 % bin the
    athlete's own majority gait decides which curve predicts that grade
    (推估; Minetti's running cost does not describe walking — Minetti 2002,
    Giovanelli 2016 on the walk/run crossover); with `speed_gait` (SP-229) the
    predicted speed decides on climbs ≥ 3 % instead (re_at / curve_at: below
    the transition speed, walk, held at it until running is as fast). `tech` is the trail
    technicality factor on flats and descents (g ≤ +2 %): median of actual ÷
    predicted RE over the athlete's own trail running windows there (推估,
    per intensity class when ≥ 30 windows), applied only with `trail=True`.
    Duck-compatible with GradeRE for the planner and the back-test."""
    run: GradeRE
    walk: GradeRE
    walk_bins: dict = field(default_factory=dict)      # bin -> [n_walked, n_total]
    tech: dict = field(default_factory=dict)           # class|"all" -> {"f", "n"}
    trail: bool = False
    tech_class: Optional[str] = None
    # hook: a route-specific factor ({"f", "n", "route"}) from the athlete's
    # efforts on a known route that overlaps the course (routes module); when
    # set it replaces the per-class factor. Nothing fills it yet.
    route_tech: Optional[dict] = None
    # per grade bin (TECH_BIN_LABELS) -> {"f" (shrunk to 1), "raw", "n"}; used on trail
    # instead of the single factor when its bin has windows (route_tech still wins)
    tech_bins: dict = field(default_factory=dict)
    # SP-228: the athlete's walk–run transition shift (runwalk.fit_shift on the same windows):
    # {"shift" (m/s, shrunk + clamped), "raw", "n", "weight", "bins", "personal"}; {} = the default curve
    runwalk: dict = field(default_factory=dict)
    # SP-250: the dry / wet technicality per bin ({"split", "n": {"dry", "wet"}, "min_n", "dry": {bin: {"f", "raw",
    # "n"}}, "wet": {…}}; split False → no per-surface factor) and the race-day surface chosen
    # ("dry" / "wet" / None = the pooled factor, as before)
    tech_surface: dict = field(default_factory=dict)
    surface: Optional[str] = None
    # SP-229: the time model picks the curve by the predicted speed (re_at / curve_at) instead of
    # the majority gait (walked). Off unless the athlete's own trail back-test says it is no worse
    # (backtest.speed_gait_flag; the API sets it with with_speed_gait)
    speed_gait: bool = False

    @property
    def surface_split(self) -> bool:
        """True when both marked groups have ≥ SURFACE_MIN_N windows (the 路況 choice is offered)."""
        return bool((self.tech_surface or {}).get("split"))

    @property
    def rw_shift(self) -> float:
        """The shift of the walk–run transition curves (runwalk.gait's `shift`), 0 = default."""
        return float((self.runwalk or {}).get("shift") or 0.0)

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
        if self.route_tech and self.route_tech.get("f"):
            return float(self.route_tech["f"]), "route"
        t = self.tech.get(self.tech_class) if self.tech_class else None
        if t and t["n"] >= TECH_MIN_N:
            return t["f"], self.tech_class
        t = self.tech.get("all")
        if t and t["n"] >= TECH_MIN_N:
            return t["f"], "all"
        return 1.0, "none"

    def tech_at(self, g: float) -> tuple[float, str]:
        """The trail technicality factor at grade g: the route's, else the
        grade bin's (§A4), else the single per-class factor."""
        if self.route_tech and self.route_tech.get("f"):
            return float(self.route_tech["f"]), "route"
        if self.surface and self.surface_split:
            # SP-250: the chosen surface's factor for this bin (a bin the group never ran: pooled)
            sb = (self.tech_surface.get(self.surface) or {}).get(tech_bin(g) or "")
            if sb and sb.get("n"):
                return float(sb["f"]), f"bin_{self.surface}"
        tb = self.tech_bins.get(tech_bin(g) or "")
        if tb and tb.get("n"):
            return float(tb["f"]), "bin"
        return self.tech_factor()

    def re(self, g: float) -> float:
        v = self.walk.re(g) if self.walked(g) else self.run.re(g)
        if self.trail and g <= 0.02:
            v *= self.tech_at(g)[0]
        return v

    def gait_at(self, g: float, p: float, weight: float) -> Optional[str]:
        """SP-229: the gait by the predicted speed — the running curve's speed at power `p`,
        then runwalk.gait with the athlete's shift (rw_shift); None below 3 % or without power."""
        from backend.engine.racepower import runwalk as RW
        if not p or p <= 0 or not weight or g < RW.MIN_GRADE:
            return None
        return RW.gait(g, self.run.re(g) * p / weight, self.rw_shift)

    def _speed_pick(self, g: float, p: float, weight: float) -> Optional[tuple[float, str]]:
        """SP-229 on a climb (g ≥ 3 %): (RE, curve). The horizontal speed at power `p` is

            v = max(v_run, min(v_walk, S))      S = the (shifted) PTS as horizontal speed

        i.e. below the transition speed the walking curve where it is faster per watt (walking
        cheaper, Brill & Kram 2021), held at S until the running curve reaches it; at and above
        S the running curve. v never falls as `p` rises (the plain switch did: walking RE is
        above running RE on climbs, so crossing S dropped the speed). The walking curve only
        where the bin has walked windows (no walking from the prior alone). None on flats and
        descents and without power: those keep the majority gait (re)."""
        from backend.engine.racepower import runwalk as RW
        if not p or p <= 0 or not weight or g < RW.MIN_GRADE:
            return None
        rr = self.run.re(g)
        if self.walk.data_n(g) <= 0:
            return rr, "run"
        v_run = rr * p / weight
        v_walk = min(self.walk.re(g) * p / weight, RW.horizontal(g, RW.pts(g, self.rw_shift)))
        if v_walk > v_run:
            return v_walk * weight / p, "walk"
        return rr, "run"

    def re_at(self, g: float, p: float, weight: float) -> float:
        """SP-229 RE with the curve chosen by the predicted speed instead of the majority gait
        (re) on climbs ≥ 3 % (_speed_pick; 推估: a harder effort runs more of the climbs, an
        easier one walks more). Flats and descents are re (the majority gait, trail
        technicality), so only climbs can differ. The choice follows the segment's power;
        the planner's later scaling / fade moves the shown speed and its label, not the curve.
        Used by the back-test's comparison, and by the planner when speed_gait is on."""
        pick = self._speed_pick(g, p, weight)
        return self.re(g) if pick is None else pick[0]

    def speed_curve(self, g: float, p: float, weight: float) -> str:
        """"walk" / "run": the curve re_at predicts grade g with at power `p`."""
        pick = self._speed_pick(g, p, weight)
        if pick is None:
            return "walk" if self.walked(g) else "run"
        return pick[1]

    def curve_at(self, g: float, p: float, weight: float) -> str:
        """"walk" / "run": the curve that predicts grade g at power `p` — speed_curve when
        speed_gait is on, else the majority gait (walked)."""
        if self.speed_gait:
            return self.speed_curve(g, p, weight)
        return "walk" if self.walked(g) else "run"

    def with_speed_gait(self, on: bool) -> "GaitRE":
        """The same model with the SP-229 switch on / off (a copy)."""
        from dataclasses import replace
        return replace(self, speed_gait=bool(on))

    def v_max(self, g: float) -> Optional[float]:
        return self.run.v_max(g, TRAIL_VMAX_Q if self.trail else 90)

    def trusted(self, g: float) -> bool:
        m = self.walk if self.walked(g) else self.run
        return stryd_valid(g) or m.data_n(g) >= SHRINK_N

    def data_n(self, g: float) -> int:
        return (self.walk if self.walked(g) else self.run).data_n(g)

    def trusted_at(self, g: float, p: float, weight: float) -> bool:
        """trusted() for the curve the segment is predicted with (curve_at)."""
        return stryd_valid(g) or self.data_n_at(g, p, weight) >= SHRINK_N

    def data_n_at(self, g: float, p: float, weight: float) -> int:
        return (self.walk if self.curve_at(g, p, weight) == "walk" else self.run).data_n(g)

    def with_flat(self, re_flat: float) -> "GaitRE":
        from dataclasses import replace
        return replace(self, run=replace(self.run, re_flat=re_flat), walk=replace(self.walk, re_flat=re_flat))

    def for_trail(self, cls: Optional[str] = None) -> "GaitRE":
        from dataclasses import replace
        return replace(self, trail=True, tech_class=cls)

    def for_surface(self, surface: Optional[str]) -> "GaitRE":
        """The same model with the race-day 路況 ("dry" / "wet"); ignored (None) unless the split
        exists, so an unsplit model behaves exactly as before (SP-250)."""
        from dataclasses import replace
        return replace(self, surface=surface if surface in ("dry", "wet") and self.surface_split else None)

    def without_surface_split(self, reason: str) -> "GaitRE":
        """The model with the split switched off (the back-test gate did not keep it): {split:
        False, gate: reason}, the counts kept for the page."""
        from dataclasses import replace
        ts = dict(self.tech_surface or {})
        if ts.get("split"):
            ts.update(split=False, gate=reason)
        return replace(self, tech_surface=ts, surface=None)

    def to_json(self) -> dict:
        j = self.run.to_json()
        wj = self.walk.to_json()
        for r, w in zip(j["bins"], wj["bins"]):
            wb = self.walk_bins.get(_bin_of(r["grade"])) or [0, 0]
            r.update(walk_n=w["n"], walk_re=w["re"], walk_median_re=w["median_re"], walk_prior_re=w["prior_re"],
                     walk_share=(wb[0] / wb[1]) if wb[1] else None, gait="walk" if self.walked(r["grade"]) else "run")
        f, which = self.tech_factor()
        from backend.engine.racepower import runwalk as RW
        j.update(walk_samples=self.walk.n_samples, tech=self.tech, tech_used={"f": f, "class": which},
                 tech_bins=self.tech_bins, trail_vmax_q=TRAIL_VMAX_Q, tech_surface=self.tech_surface,
                 speed_gait=self.speed_gait,
                 runwalk={**(self.runwalk or {"shift": 0.0, "personal": False, "n": 0, "bins": []}),
                          "curve": RW.curve_json(self.rw_shift)})
        return j


def surface_tech(by_surface: dict, pooled: dict) -> dict:
    """SP-250: {"dry": [(bin label, ratio), …], "wet": […]} (the marked trail running windows on
    g ≤ +2 %) and the pooled per-bin factors → GaitRE.tech_surface. split only when both groups
    have ≥ SURFACE_MIN_N windows; each group's bin = its median ratio shrunk n/(n + 30) toward the
    pooled bin factor (1 when the pooled bin is missing), clamped to TECH_BOUNDS."""
    n = {s: len(by_surface.get(s) or []) for s in ("dry", "wet")}
    out = {"split": n["dry"] >= SURFACE_MIN_N and n["wet"] >= SURFACE_MIN_N, "n": n, "min_n": SURFACE_MIN_N}
    if not out["split"]:
        return out
    for s in ("dry", "wet"):
        bins: dict = {}
        for lab, r in by_surface[s]:
            bins.setdefault(lab, []).append(r)
        grp = {}
        for lab, rs in bins.items():
            raw, k = float(np.median(rs)), len(rs)
            base = float((pooled.get(lab) or {}).get("f") or 1.0)
            f = (k * raw + SHRINK_N * base) / (k + SHRINK_N)
            grp[lab] = {"f": min(TECH_BOUNDS[1], max(TECH_BOUNDS[0], f)), "raw": raw, "n": k}
        out[s] = grp
    return out


def fit_gait_re(samples: Sequence[dict], re_flat: float, classes: Optional[dict] = None,
                surfaces: Optional[dict] = None) -> GaitRE:
    """samples = [{"g", "re", "v", "a", "run" (running share of the window),
    "trail"}]; classes = {activity idx: intensity class} for the per-class
    technicality factor; surfaces = {activity idx: "dry" / "wet"} (the user's 路況
    marks, SP-250; unmarked activities absent) for the per-surface factor."""
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
    by_bin: dict = {}
    by_surface: dict = {"dry": [], "wet": []}
    for s in run_s:
        if not s.get("trail") or s["g"] > 0.02 or not s.get("re"):
            continue
        r = s["re"] / run.re(s["g"])
        ratios.setdefault("all", []).append(r)
        by_bin.setdefault(tech_bin(s["g"]), []).append(r)
        c = (classes or {}).get(s.get("a"))
        if c:
            ratios.setdefault(c, []).append(r)
        sf = (surfaces or {}).get(s.get("a"))
        if sf in by_surface:
            by_surface[sf].append((tech_bin(s["g"]), r))
    for c, rs in ratios.items():
        f = float(np.median(rs))
        tech[c] = {"f": min(TECH_BOUNDS[1], max(TECH_BOUNDS[0], f)), "raw": f, "n": len(rs)}
    tbins: dict = {}
    for lab, rs in by_bin.items():
        raw, n = float(np.median(rs)), len(rs)
        f = (n * raw + SHRINK_N * 1.0) / (n + SHRINK_N)
        tbins[lab] = {"f": min(TECH_BOUNDS[1], max(TECH_BOUNDS[0], f)), "raw": raw, "n": n}
    from backend.engine.racepower import runwalk as RW
    return GaitRE(run, walk, wb, tech, tech_bins=tbins, runwalk=RW.fit_shift(samples),
                  tech_surface=surface_tech(by_surface, tbins))


def tobler_kmh(g: float) -> float:
    """Tobler 1993: W = 6·exp(−3.5·|S + 0.05|) km/h (via Wikipedia; 已驗證
    second-hand: S = −0.05 → 6.00, S = 0 → 5.04; V-F13)."""
    return 6.0 * math.exp(-3.5 * abs(g + 0.05))


@dataclass
class HikeSpeed:
    """F13: personal walking speed per grade bin, shrunk towards Tobler
    (Tobler's shape is 經驗法則; the personal fit is 推估, 待驗證 by the §3B
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
            cadence: Optional[np.ndarray] = None, run_cadence: float = 65.0,
            hr_lag_s: float = 0.0) -> list[dict]:
    """100 m windows along the distance, using moving samples only: grade,
    speed (m/s) and time-weighted power; with `hr` the time-weighted HR, with
    `cadence` (strides/min) the running share of the moving time (cadence ≥
    run_cadence). Arrays are sample-aligned; d_m is cumulative metres.
    Every row carries `k` (window index) and `t` (cumulative moving seconds
    at the window start). With `hr_lag_s` > 0 each row also carries `hr_lag`:
    the time-weighted HR of the same window read `hr_lag_s` seconds later on
    the elapsed clock (HR responds 1–2 min after a pace change;
    baiyue-from-running.md §2.2 finding 1 — the 60 s shift is 推估).
    Returns [{"g", "v", "p", "z", "k", "t", "hr"?, "hr_lag"?, "run"?}]."""
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
    ch = chn = cl = None
    if hr is not None:
        hv = np.asarray(hr, float)[:n]
        okh = mv & np.isfinite(hv) & (hv > 40)
        ch = np.cumsum(np.where(okh, hv * dt_, 0.0))
        chn = np.cumsum(np.where(okh, dt_, 0.0))
        if hr_lag_s > 0:
            tt = np.asarray(t, float)
            okt = np.isfinite(tt) & np.isfinite(hv) & (hv > 40)
            if okt.sum() >= 2:
                lag = np.interp(tt + hr_lag_s, tt[okt], hv[okt])
                cl = np.cumsum(np.where(okh, lag * dt_, 0.0))
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
        row = {"g": float((zf[b] - zf[a]) / dd), "v": float(v), "z": float(zf[a]), "k": k_, "t": float(ct[a])}
        if ce is not None:
            row["p"] = float((ce[b] - ce[a]) / tm)
        if ch is not None and chn[b] - chn[a] > 0.5 * tm:
            row["hr"] = float((ch[b] - ch[a]) / (chn[b] - chn[a]))
            if cl is not None:
                row["hr_lag"] = float((cl[b] - cl[a]) / (chn[b] - chn[a]))
        if cr is not None and crn[b] - crn[a] > 0.5 * tm:
            row["run"] = float((cr[b] - cr[a]) / (crn[b] - crn[a]))
        out.append(row)
    return out
