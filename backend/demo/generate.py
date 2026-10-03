"""Write the demo athlete's files (docs/plans/auth-and-demo.plan.md §3.2):

    manifest = generate(root, seed=20261002, anchor=date, weeks=52, small=False)

  root/fit/coros/<year>/demo_<nnnn>.fit   one FIT per activity (as the COROS sync stores them)
  root/plan.json                          events, phases, dated thresholds, weights, profile
  root/demo_courses/<file>.gpx            3 fictional race courses (路跑半馬 / 越野 50K / 百岳 3 日)

Deterministic: every random number comes from numpy PCG64 seeded with
(seed, ...) and every date is counted back from `anchor`; the FIT bytes do
not depend on the wall clock. `small=True` (tests): 8 weeks, activities cut
to a quarter of their length. The DB rows (workout_files, settings), the
plan sessions and the warm-up are build.py's job.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np

from backend.demo import athlete as A
from backend.demo import course as C
from backend.demo import fitwrite as FW
from backend.demo import season as S
from backend.demo.signals import Segment, simulate_hike, simulate_run

VERSION = 1
COURSES = {"路跑半馬": "half_marathon.gpx", "越野 50K": "trail_50k.gpx", "百岳 3 日": "baiyue_3day.gpx"}
COURSE_EVENTS = {"路跑半馬": "demo-half", "越野 50K": "demo-50k", "百岳 3 日": "demo-baiyue"}


def _rng(seed: int, *key: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64([seed, *key]))


def _segments(kind: str, minutes: float) -> list[Segment]:
    """The program of a run: (duration, power fraction of CP) segments."""
    m = lambda x: int(round(x * 60))          # noqa: E731
    if kind == "interval":
        segs = [Segment(m(15), 0.68)]
        for r in range(6):
            segs += [Segment(m(4), 1.05, label=f"rep {r + 1}"), Segment(m(2), 0.52, label="rest")]
        return segs + [Segment(m(minutes - 15 - 36), 0.62)]
    if kind == "interval_short":
        segs = [Segment(m(15), 0.68)]
        for r in range(8):
            segs += [Segment(m(2), 1.10, label=f"rep {r + 1}"), Segment(m(1.5), 0.52, label="rest")]
        return segs + [Segment(m(max(5, minutes - 15 - 28)), 0.62)]
    if kind == "tempo":
        return [Segment(m(15), 0.68), Segment(m(12), 0.92), Segment(m(3), 0.60), Segment(m(12), 0.92),
                Segment(m(max(5, minutes - 42)), 0.62)]
    if kind == "hill":
        segs = [Segment(m(15), 0.68)]
        for r in range(8):
            segs += [Segment(m(1.5), 1.12), Segment(m(2.0), 0.50)]
        return segs + [Segment(m(max(5, minutes - 15 - 28)), 0.62)]
    if kind == "cp_test":
        # cp_protocols "standard": 12′ all-out, 30′ easy, 3′ all-out (long bout first)
        return [Segment(m(15), 0.66), Segment(m(12), 1.055, fade=0.05, label="12′ all-out"),
                Segment(m(30), 0.60), Segment(m(3), 1.27, fade=0.12, label="3′ all-out"), Segment(m(10), 0.58)]
    if kind == "aet_test":
        # aet_test "standard": 15′ warm-up + 60′ at a fixed power (first-half HR ≈ AeT) + 5′ cool-down
        return [Segment(m(15), 0.70), Segment(m(60), 0.78, label="AeT 60′"), Segment(m(5), 0.58)]
    frac = {"easy": 0.70, "recovery": 0.60, "long": 0.70, "lsd": 0.66, "trail": 0.70,
            "trail_long": 0.70, "race_half": 0.98}.get(kind, 0.68)
    return [Segment(m(minutes), frac, lap=False)]


def _shrink(segs: list[Segment], k: float) -> list[Segment]:
    return [Segment(max(30, int(s.dur_s * k)), s.frac, s.lap, s.fade, s.label) for s in segs]


TRAIL_KINDS = ("trail", "trail_long")
TRAIL_DRIFT = 0.5           # × the drawn cardiac drift: a long easy trail run creeps up slowly
TRAIL_PITCH_M = 40.0        # m of one climb / descent of the rolling trail loop
TRAIL_LEAD_M = 3000.0       # m of runnable approach (and run-out) at the trailhead


def _trail_course(rng, p: S.Planned, length: float, climb: float) -> C.Course:
    """A rolling trail loop of `length` m with `climb` m up (and down): climbs and
    descents of ~TRAIL_PITCH_M take turns (no stretch of the run is one long descent),
    after a runnable approach of TRAIL_LEAD_M (the way back out at the end)."""
    if p.kind == "trail_long":
        up, down = (0.08, 0.25), (0.06, 0.20)
    else:
        up, down = (0.06, 0.18), (0.05, 0.15)
    n_seg = int(min(60, max(4, round(climb / TRAIL_PITCH_M))))
    return C.make(rng, "loop", A.AREAS["trail"], length, climb, climb, up=up, down=down, n_seg=n_seg,
                  trail=True, offset_m=float(rng.uniform(0, 1500)), rough=2.0, alternate=True,
                  lead_m=TRAIL_LEAD_M)


def trail_run(rng, p: S.Planned, segs: list[Segment], small: bool, **sim):
    """(course, Signals) of a trail run (generated trail / trail_long and the linked
    trail session): the loop is sized so the run goes round it once and ends back at
    the trailhead — the whole planned climb, and the last part of the run is the same
    rolling terrain as the rest (not the bottom of one long descent). Deterministic:
    the course and the run each get a seed drawn from `rng`, and the loop length is
    found by a few re-runs (the distance the run covers on a loop of that length)."""
    k = 0.25 if small else 1.0
    climb = float(p.params.get("climb") or rng.uniform(350, 650)) * k
    cseed, sseed = (int(x) for x in rng.integers(0, 2 ** 62, 2))
    length = max(2000.0, sum(s.dur_s for s in segs) * 2.4)
    course = sig = None
    for _ in range(6):
        course = _trail_course(np.random.Generator(np.random.PCG64(cseed)), p, length, climb)
        sig = simulate_run(np.random.Generator(np.random.PCG64(sseed)), course, segs, **sim)
        ran = float(sig.dist[-1] + sig.speed[-1]) if sig.n else length
        if abs(ran - length) <= 0.01 * length:
            break
        length = max(2000.0, ran)
    return course, sig


def _run_course(rng, p: S.Planned, minutes: float, small: bool):
    """A course long enough for the run (wrapping courses repeat). Trail runs: trail_run."""
    k = 0.25 if small else 1.0
    if p.kind == "hill":
        return C.make(rng, "out_back", A.AREAS["hill"], 6000 * k, 160 * k, 40 * k, up=(0.05, 0.10),
                      down=(0.04, 0.08), n_seg=4, offset_m=float(rng.uniform(0, 800)))
    if p.kind == "race_half":
        return C.make(rng, "loop", A.AREAS["road"], 21100 * k, 60 * k, up=(0.01, 0.03), down=(0.01, 0.03),
                      n_seg=6, offset_m=float(rng.uniform(0, 500)), rough=0.4)
    flat = p.kind in ("aet_test", "cp_test", "interval", "interval_short", "tempo")
    gain = float(rng.uniform(5, 15)) if flat else float(rng.uniform(15, 45))
    return C.make(rng, "loop", A.AREAS["road"], float(rng.uniform(4000, 6500)), gain, up=(0.005, 0.03),
                  down=(0.005, 0.03), n_seg=3, offset_m=float(rng.uniform(0, 1200)), rough=0.3)


# 百岳 day legs: (length km, gain m, loss m); chained so day 2 starts at day 1's hut
TRIP_3DAY = [(12.5, 1250, 650), (11.0, 1450, 1450), (14.0, 1050, 1650)]
TRIP_2DAY = [(11.0, 1150, 350), (12.5, 1000, 1800)]


def _trip_legs(seed: int, trip: str, n_days: int, small: bool) -> list[C.Course]:
    rng = _rng(seed, 7, sum(map(ord, trip)))
    spec = TRIP_3DAY if n_days >= 3 else TRIP_2DAY
    k = 0.25 if small else 1.0
    start = 2800.0 if n_days >= 3 else 2450.0
    legs = []
    for km, gain, loss in spec:
        legs.append(C.make(rng, "point", A.AREAS["baiyue"], km * 1000 * k, gain * k, loss * k,
                           start_ele=start, up=(0.10, 0.32), down=(0.08, 0.30), n_seg=6, trail=True, rough=2.5))
    return C.chain(legs)


def _training_hike(rng, small: bool) -> C.Course:
    k = 0.25 if small else 1.0
    gain = float(rng.uniform(900, 1200))
    return C.make(rng, "out_back", A.AREAS["trail"], float(rng.uniform(11000, 14000)) * k, gain * k, 0.1 * gain * k,
                  start_ele=420.0, up=(0.10, 0.30), down=(0.08, 0.25), n_seg=6, trail=True, rough=2.5)


def _local_start(p: S.Planned) -> dt.datetime:
    h = int(p.hour)
    mi = int(round((p.hour - h) * 60))
    local = dt.datetime(p.date.year, p.date.month, p.date.day, h, min(mi, 59))
    return (local - dt.timedelta(hours=A.TZ_OFFSET_H)).replace(tzinfo=dt.timezone.utc)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_courses(root: Path, seed: int, small: bool = False) -> dict:
    """The three fictional race courses as GPX; {name: relpath}."""
    out = {}
    d = root / "demo_courses"
    d.mkdir(parents=True, exist_ok=True)
    rng = _rng(seed, 9)
    k = 0.25 if small else 1.0
    half = C.make(rng, "loop", A.AREAS["road"], 21100, 60, up=(0.01, 0.03), down=(0.01, 0.03), n_seg=6, rough=0.4)
    t50 = C.make(rng, "loop", A.AREAS["trail"], 50000 * (1 if not small else 0.3), 2800 * (1 if not small else 0.3),
                 up=(0.08, 0.25), down=(0.06, 0.22), n_seg=10, trail=True, rough=2.0)
    by = C.concat(_trip_legs(seed, "course-baiyue", 3, small))
    _ = k
    for name, c in (("路跑半馬", half), ("越野 50K", t50), ("百岳 3 日", by)):
        p = d / COURSES[name]
        p.write_bytes(C.gpx(c, f"示範賽道：{name}").encode("utf-8"))
        out[name] = p.relative_to(root).as_posix()
    return out


def write_plan(root: Path, season: S.Season) -> Path:
    from backend.engine.planning import Plan
    prof = dict(A.profile())
    prof["birth_year"] = A.birth_year(season.anchor.year)
    plan = Plan(events=list(season.events), phases=list(season.phases), thresholds=list(season.thresholds),
                weights=list(season.weights), profile=prof)
    p = root / "plan.json"
    plan.save(p)
    return p


def build_activity(seed: int, idx: int, p: S.Planned, season: S.Season, small: bool,
                   trip_legs: dict) -> tuple[bytes, dict]:
    rng = _rng(seed, 3, idx)
    cp = season.cp_on.get(p.date, A.CP_W)
    doy = p.date.timetuple().tm_yday
    start = _local_start(p)
    if p.sport == "hike":
        if p.kind == "baiyue":
            legs = trip_legs[p.trip]
            c = legs[min(p.day, len(legs) - 1)]
        else:
            c = _training_hike(rng, small)
        sig = simulate_hike(rng, c, cp=cp, stryd=p.stryd, doy=doy, hour=p.hour,
                            pack=A.PACK_KG if p.kind == "baiyue" else 7.0, day=p.day,
                            max_s=(4 if small else 14) * 3600)
        sport, sub = FW.SPORT_HIKING, FW.SUB_GENERIC
    else:
        segs = _segments(p.kind, p.minutes)
        if small:
            segs = _shrink(segs, 0.25)
        if p.kind in TRAIL_KINDS:
            drift = float(rng.uniform(0.04, 0.12)) * TRAIL_DRIFT
            c, sig = trail_run(rng, p, segs, small, cp=cp, stryd=p.stryd, doy=doy, hour=p.hour, drift=drift,
                               push=0.12, down_cap=float(rng.uniform(2.8, 3.6)), has_power=True, lock_ok=True)
        else:
            c = _run_course(rng, p, p.minutes, small)
            drift = 0.17 if p.kind == "aet_test" else float(rng.uniform(0.04, 0.12))
            sig = simulate_run(rng, c, segs, cp=cp, stryd=p.stryd, doy=doy, hour=p.hour, drift=drift,
                               push=0.12 if c.trail else 0.0, down_cap=float(rng.uniform(2.8, 3.6)),
                               has_power=True, lock_ok=p.kind not in ("cp_test", "aet_test", "interval"))
        sport, sub = FW.SPORT_RUNNING, (FW.SUB_TRAIL if c.trail else FW.SUB_GENERIC)
    raw = FW.encode_activity(start=start, lat=sig.lat, lon=sig.lon, alt=sig.alt, dist=sig.dist,
                             speed=sig.speed, hr=sig.hr, cadence=sig.cadence, power=sig.power, temp=sig.temp,
                             stryd=p.stryd, laps=sig.laps, sport=sport, sub_sport=sub,
                             total_ascent=sig.ascent, total_descent=sig.descent, form_power=sig.form_power,
                             air_power=sig.air_power, lss=sig.lss)
    hr = sig.hr[np.isfinite(sig.hr)]
    info = {"start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "duration_s": int(sig.n),
            "distance_m": round(float(sig.dist[-1]) if sig.n else 0.0, 1),
            "elevation_gain_m": round(sig.ascent, 1), "sport": p.sport, "kind": p.kind, "name": p.name,
            "showcase": p.showcase, "stryd": p.stryd, "date": p.date.isoformat(),
            "hr_min": float(hr.min()) if len(hr) else None, "hr_max": float(hr.max()) if len(hr) else None,
            "avg_power": round(float(np.mean(sig.power)), 1) if sig.power is not None else None,
            "trip": p.trip, "day": p.day}
    return raw, info


def generate(root: Path, seed: int = 20261002, anchor: dt.date | None = None, weeks: int = 52,
             small: bool = False) -> dict:
    if anchor is None:
        raise ValueError("anchor (the last day of data) is required: the generator never reads the clock")
    root = Path(root)
    if small and weeks == 52:
        weeks = 8
    season = S.schedule(seed, anchor, weeks)
    fit_dir = root / "fit" / "coros"
    trips = {}
    for p in season.activities:
        if p.kind == "baiyue" and p.trip not in trips:
            n_days = sum(1 for q in season.activities if q.trip == p.trip)
            trips[p.trip] = _trip_legs(seed, p.trip, max(n_days, 2), small)
    files, acts, showcase = {}, [], {}
    for i, p in enumerate(season.activities, start=1):
        raw, info = build_activity(seed, i, p, season, small, trips)
        d = fit_dir / str(p.date.year)
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"demo_{i:04d}.fit"
        f.write_bytes(raw)
        rel = f.relative_to(root).as_posix()
        files[rel] = _sha(raw)
        info["file"] = rel
        acts.append(info)
        if p.showcase:
            key = "baiyue" if p.kind == "baiyue" else p.kind
            if key == "baiyue":
                showcase.setdefault("baiyue", rel)               # the trip's first day
                showcase.setdefault("baiyue_days", []).append(rel)
            else:
                showcase[key] = rel
    plan_p = write_plan(root, season)
    files[plan_p.relative_to(root).as_posix()] = _sha(plan_p.read_bytes())
    courses = write_courses(root, seed, small)
    for rel in courses.values():
        files[rel] = _sha((root / rel).read_bytes())
    return {"seed": seed, "anchor": anchor.isoformat(), "version": VERSION, "weeks": weeks, "small": small,
            "n_activities": len(acts), "files": files, "activities": acts, "showcase": showcase,
            "courses": courses, "course_events": dict(COURSE_EVENTS),
            "athlete": {"name": A.NAME, "weight_kg": A.WEIGHT_KG, "cp_w": A.CP_HISTORY[-1], "lthr": A.LTHR,
                        "aethr": A.AETHR, "hrmax": A.HRMAX, "age": A.AGE}}
