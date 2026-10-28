"""介紹頁 (SP-622): what a stranger sees at "/" before signing in (main._home; signed in, "/" stays the
overview). static/intro.html with i18n/<locale>/intro.json; no one's data.

The page's 「先試試看」 button (login.try_link) goes to the public demo when TRC_DEMO_URL is set, else to
the built-in race calculator example (/share/<share.EXAMPLE_ID>, a fictional runner's 50K): the page links
to the example by default and the server says, per request, when the demo is there instead
(window.TRC_TRY). Its screenshots are the manual's (backend/scripts/intro_shots.json, names intro-*): taken
on the demo athlete in both languages, light and dark.

SP-637, the page made interactive — two public routes (login.PUBLIC_PATHS: exactly these paths), the
calculation core staying on the server (SP-500: the page only formats what these answer):

  GET /api/v1/intro/example/<kind>   kind trail | baiyue | road: the built-in example of that kind
        (share.EXAMPLES, repo files, a fictional runner) trimmed for the elevation profile — the profile
        (≤ PROFILE_POINTS points), each leg's distance, pace, heart-rate cap, time and fuel; numbers only
        (the page words them: no source note, no product name, no date); plus the small calculator's
        range for the kind and its answer for the example's own distance and climb.
  GET /api/v1/intro/estimate?kind=&km=&gain=   the small calculator: the finish time of the built-in
        fictional runner (backend/demo/intro_runner.json, frozen by scripts/intro_example.py from the
        demo athlete — never a tenant's; no runner parameter is read): the calculator's total-only
        prediction (calc.predict), scaled per kind so the kind's example reads as its frozen plan (ratio()). km / gain must lie in LIMITS
        (else 422) and are rounded to its grid; each grid point is computed once (an in-process cache).
        Per client (ratelimit.client_ip: the visitor behind a WKO5COACH_TRUSTED_PROXIES proxy; an IPv6
        client by its /64) at most BURST at once, refilled RATE a minute (429 + Retry-After); computations
        (cache misses) site-wide at most MISS_PER_MIN a minute (503). The answer: kind, the rounded
        km / gain, time_s (whole minutes), pace_s_per_km, fictional — nothing of the model.
  Without the core (the public mirror's stub: CoreNotIncluded) or the runner file: 503, nothing cached.

SP-969, what sets the calculator apart:

  GET /api/v1/intro/breakdown   the trail example's finish time factor by factor (backend/demo/
        intro_breakdown.json, frozen with the example by scripts/intro_example.py from the calculator's own
        plan: planner.trail_breakdown — each layer the calculator run again with one factor more); numbers
        only, the page words them and draws the waterfall. 404 when the file is missing or no longer adds
        up to the example.
  「為什麼不一樣」's numbers (why()): counted from the repo (the 關於 page's literature and methods, the
        calibrated items) or, for documents, constants with their source; handed to the page with it
        (window.TRC_WHY).
"""
from __future__ import annotations

import copy
import json
import math
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.i18n.pages import PageResponse, render_page
from backend.security import ratelimit as RL

PAGE = "intro"
PREFIX = "/api/v1/intro"
KINDS = ("trail", "baiyue", "road")
PUBLIC_PATHS = frozenset({PREFIX + "/estimate", PREFIX + "/templates", PREFIX + "/plan", PREFIX + "/breakdown"}
                         | {f"{PREFIX}/example/{k}" for k in KINDS})

RUNNER_FILE = Path(__file__).resolve().parents[1] / "demo" / "intro_runner.json"
# the small calculator's range and grid per kind (km, gain in m)
LIMITS = {
    "trail": {"km": (5.0, 100.0), "km_step": 1.0, "gain": (0.0, 6000.0), "gain_step": 50.0},
    "baiyue": {"km": (5.0, 80.0), "km_step": 1.0, "gain": (200.0, 6000.0), "gain_step": 50.0},
    "road": {"km": (3.0, 42.0), "km_step": 1.0, "gain": (0.0, 600.0), "gain_step": 10.0},
}
BAIYUE_PACK_KG = 10.0          # the 百岳 example's pack (the demo's 示範百岳 3 日縱走)
BURST, RATE = 20, 40           # per client: 20 at once, then 40 a minute
MISS_PER_MIN = 600             # computations (cache misses) site-wide per minute
CACHE_MAX = 4096
PROFILE_POINTS = 240
CACHE_HEADERS = {"Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex"}

router = APIRouter(prefix=PREFIX, tags=["intro"], include_in_schema=False)

PER_CLIENT = RL.Buckets(rate=RATE, per=60, burst=BURST)
MISS = RL.Buckets(rate=MISS_PER_MIN, per=60, burst=MISS_PER_MIN)
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_lock = threading.Lock()
_runner: Optional[dict] = None


class Unavailable(Exception):
    """No answer now (no core, no runner, too busy): a 503."""


def reset() -> None:
    """Forget the cache, the runner and the limits (tests)."""
    global _runner, _why
    with _lock:
        _CACHE.clear()
        _ratios.clear()
        _runner = None
        _why = None
    PER_CLIENT.reset()
    MISS.reset()


def cache_size() -> int:
    with _lock:
        return len(_CACHE)


def page() -> PageResponse:
    from backend.accounts import login as LG
    link = json.dumps(LG.try_link()).replace("<", "\\u003c")
    facts = json.dumps(why()).replace("<", "\\u003c")
    cairn = json.dumps({"url": cairn_url()}).replace("<", "\\u003c")
    return render_page(PAGE, fill={"try": f"<script>window.TRC_TRY = {link};</script>",
                                   "why": f"<script>window.TRC_WHY = {facts};</script>",
                                   "cairn": f"<script>window.TRC_CAIRN = {cairn};</script>"})


def cairn_url() -> Optional[str]:
    """SP-969 (2026-10-27 追加): the 介紹頁's 疊石 Cairn block links where the app's own cross-promotion does
    (api/xp.cairn_url: Cairn's front page — TRC_CAIRN_ORIGIN, else its production host — marked from=trc);
    None when that is not an https address (the block then shows no link)."""
    try:
        from backend.api import xp as XP
        u = XP.cairn_url()
    except Exception:                       # noqa: BLE001 — no Cairn module: the block goes without a link
        return None
    return u if isinstance(u, str) and u.startswith("https://") else None


# ---------------------------------------------------------------------------- 為什麼不一樣 (SP-969)
#
# The four numbers of 「為什麼不一樣」 and the back-test line under the waterfall, handed to the page by the
# server (the page words them): what the repo itself counts is counted here, each time the process starts —
# never typed into the page. The research and the back-test are documents, not code: their numbers are
# constants with where they come from.

# docs/research/coach-schools-zones-periodization.md:9 (「把 13 套區間系統換算成 % LT2」) — the zone systems the
# 課表's intensities were aligned with; its bibliography (:361-753) runs [1]…[196]
ZONE_SYSTEMS = 13
ZONE_REFS = 196
# docs/spec/racepower.spec.md:764 (test C, A + B: 50 trail walking segments + 16 百岳 segments, median |err|:
# the full model 7.7 %, the physiological prior 21.9 %, Tobler 51.1 %) and :1029 (the trail HR model on 7 races,
# race level, median |err| 7.6 %); :1034 (durability: 7.6 % with it, 5.5 % without — kept, marked 推估).
# All on one runner's data, the author's (the page says so).
BACKTEST = {"walk_n": 66, "walk_full_pct": 7.7, "walk_prior_pct": 21.9, "walk_formula_pct": 51.1,
            "trail_races": 7, "trail_pct": 7.6, "durability_with_pct": 7.6, "durability_without_pct": 5.5}

_why: Optional[dict] = None


def _calibrated() -> Optional[int]:
    """How many numbers are fitted on the athlete's own records (engine/calibrate.py's registry: the items
    with a fit, not the 進階 C 類 ones that are only set by hand); None without the core."""
    try:
        from backend.engine import calibrate as CAL
        return sum(1 for it in CAL._registry().values() if not it.manual_only)
    except Exception:                       # noqa: BLE001 — the public mirror's stub: the card goes without it
        return None


def why() -> dict:
    """「為什麼不一樣」's numbers: the 關於 page's literature and methods (about.SOURCES / METHODS), the
    calibrated items, the zone systems and their references, the back-test (computed once a process)."""
    global _why
    if _why is None:
        from backend.api import about as AB
        _why = {"sources": len(AB.SOURCES), "methods": len(AB.METHODS), "calibrated": _calibrated(),
                "zone_systems": ZONE_SYSTEMS, "zone_refs": ZONE_REFS, "backtest": dict(BACKTEST)}
    return copy.deepcopy(_why)


# ---------------------------------------------------------------------------- the waterfall (SP-969)

BREAKDOWN_FILE = Path(__file__).resolve().parents[1] / "demo" / "intro_breakdown.json"
BREAKDOWN_KEYS = ("flat", "climb", "terrain", "durability", "heat", "altitude", "memory", "night")


def breakdown() -> Optional[dict]:
    """The trail example's finish time factor by factor (backend/demo/intro_breakdown.json, frozen by
    scripts/intro_example.py from the calculator's own plan of the example: planner.trail_breakdown) —
    numbers only, the page words them; None without the file or when it no longer adds up to the example."""
    from backend.engine.racepower import share as SH
    try:
        d = json.loads(BREAKDOWN_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    snap = SH.example(SH.EXAMPLE_ID) or {}
    t = (snap.get("summary") or {}).get("time_s")
    layers = [{"key": x.get("key"), "t_s": _r(x.get("t_s"), 1)} for x in d.get("layers") or []
              if isinstance(x, dict) and x.get("key") in BREAKDOWN_KEYS and _r(x.get("t_s"), 1) is not None]
    if not layers or not isinstance(t, (int, float)) or abs(sum(x["t_s"] for x in layers) - t) > 2.0:
        return None                          # an example frozen again without its breakdown: no waterfall
    terr = d.get("terrain") if isinstance(d.get("terrain"), dict) else {}
    a = d.get("assumed") if isinstance(d.get("assumed"), dict) else None
    # SP-969 Q2: the example's assumed race settings (scripts/intro_example.ASSUMED), for the page to name
    assumed = {"effort": _r(a.get("effort"), 3),
               "terrain": a.get("terrain") if a.get("terrain") in ("normal", "technical", "scramble") else None,
               "delta": _r(a.get("delta"), 4), "stops": _r(a.get("stops")), "stop_min": _r(a.get("stop_min"))} \
        if a else None
    return {"time_s": _r(t, 1), "layers": layers, "assumed": assumed, "km": _r(d.get("km"), 2), "gain_m": _r(d.get("gain_m")),
            "divisor": _r(d.get("divisor")), "terrain": {"level": terr.get("level") if terr.get("level") in
                                                         ("normal", "technical", "scramble") else None,
                                                         "mult": _r(terr.get("mult"), 3)},
            "delta": _r(d.get("delta"), 4), "heat_beta": bool(d.get("heat_beta")),
            "temp_c": _r(d.get("temp_c"), 1), "rh_pct": _r(d.get("rh_pct")), "altitude_m": _r(d.get("altitude_m")),
            "legs": len(snap.get("segments") or [])}


# ---------------------------------------------------------------------------- the limits

def client_key(scope) -> str:
    return RL.limit_key(RL.client_ip(scope))


def allow(scope) -> bool:
    return PER_CLIENT.take(client_key(scope))


def _too_many() -> JSONResponse:
    return JSONResponse(status_code=429, headers={"Retry-After": str(math.ceil(60 / RATE))},
                        content={"detail": {"code": "RATE_LIMITED", "message": "too many requests"}})


def _unavailable() -> JSONResponse:
    return JSONResponse(status_code=503, headers={"Retry-After": "30"},
                        content={"detail": {"code": "UNAVAILABLE", "message": "not available now"}})


# ---------------------------------------------------------------------------- the runner and the computation

def _load_runner() -> dict:
    global _runner
    with _lock:
        if _runner is not None:
            return _runner
    try:
        d = json.loads(RUNNER_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        raise Unavailable("no runner") from None
    with _lock:
        _runner = d
    return d


class FrozenRunner:
    """calc.Context of the built-in fictional runner: its frozen /inputs, nothing else of anyone's."""

    def __init__(self, d: dict):
        self._d = d

    def inputs(self) -> dict:
        return copy.deepcopy(self._d)

    def grade_models(self) -> dict:
        return {}


def grid(kind: str, km: float, gain: float) -> tuple[float, float]:
    """(km, gain) rounded to the kind's grid; ValueError when not finite or outside LIMITS."""
    L = LIMITS[kind]
    if not (math.isfinite(km) and math.isfinite(gain)):
        raise ValueError("not a number")
    kq = round(round(km / L["km_step"]) * L["km_step"], 3)
    gq = round(round(gain / L["gain_step"]) * L["gain_step"], 3)
    if not (L["km"][0] <= kq <= L["km"][1] and L["gain"][0] <= gq <= L["gain"][1]):
        raise ValueError("out of range")
    return kq, gq


def _raw(kind: str, km: float, gain: float) -> float:
    """The fictional runner's total-only prediction (calc.predict, the calculator's v1) in seconds."""
    from backend.engine.core_stub import CoreNotIncluded
    from backend.engine.racepower import calc as CALC
    ctx = FrozenRunner(_load_runner())
    body = CALC.PredictIn(type=kind, distance_km=km, gain_m=gain, loss_m=gain if kind != "baiyue" else None,
                          days=1, pack_kg=BAIYUE_PACK_KG if kind == "baiyue" else None)
    try:
        out = CALC.predict(ctx, body)
    except (CoreNotIncluded, CALC.CalcError, KeyError, TypeError, ZeroDivisionError):
        raise Unavailable("no answer") from None
    t = (out.get("result") or {}).get("time_s")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0:
        raise Unavailable("no answer")
    return float(t)


_ratios: dict = {}


def ratio(kind: str) -> float:
    """The kind's example read the same on both: its frozen plan's finish time (the calculator on the
    course, segment by segment) over the total-only prediction for its own distance and climb — so the
    small calculator agrees with the profile next to it at the example, and scales from there. 1 without
    the example; never below 0.8 or above 1.6."""
    with _lock:
        if kind in _ratios:
            return _ratios[kind]
    from backend.engine.racepower import share as SH
    snap = SH.example(SH.example_id(kind)) or {}
    s = snap.get("summary") or {}
    r = 1.0
    if (s.get("time_s") or 0) > 0 and (s.get("km") or 0) > 0:
        r = min(1.6, max(0.8, float(s["time_s"]) / _raw(kind, float(s["km"]), float(s.get("gain_m") or 0.0))))
    with _lock:
        _ratios[kind] = r
    return r


def _compute(kind: str, km: float, gain: float) -> dict:
    t = int(round(_raw(kind, km, gain) * ratio(kind) / 60.0)) * 60
    return {"kind": kind, "km": km, "gain_m": gain, "time_s": t, "pace_s_per_km": int(round(t / km)),
            "fictional": True}


def estimate(kind: str, km: float, gain: float) -> dict:
    """The fictional runner's finish time at the grid point of (km, gain); cached. ValueError: bad input;
    Unavailable: no answer now."""
    if kind not in LIMITS:
        raise ValueError("kind")
    key = (kind, *grid(kind, km, gain))
    with _lock:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return dict(hit)
    if not MISS.take("site"):
        raise Unavailable("busy")
    res = _compute(*key)
    with _lock:
        _CACHE[key] = res
        while len(_CACHE) > CACHE_MAX:
            _CACHE.popitem(last=False)
    return dict(res)


def _num(v: Optional[str]) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError("not a number") from None


@router.get("/estimate")
def get_estimate(request: Request):
    if not allow(request.scope):
        return _too_many()
    q = request.query_params                      # only these three are read: there is no runner parameter
    try:
        res = estimate(q.get("kind") or "", _num(q.get("km")), _num(q.get("gain")))
    except ValueError:
        raise HTTPException(422, "kind trail | baiyue | road; km and gain within the range")
    except Unavailable:
        return _unavailable()
    return JSONResponse(res, headers=CACHE_HEADERS)


# ---------------------------------------------------------------------------- the examples, trimmed

def _r(v, nd: int = 0):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return round(v, nd) if nd else int(round(v))


def _profile(p: dict) -> list:
    km, z = list(p.get("km") or []), list(p.get("z") or [])
    n = min(len(km), len(z))
    if n == 0:
        return []
    step = max(1, math.ceil(n / PROFILE_POINTS))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [[_r(km[i], 2), _r(z[i])] for i in idx]


# the plan's run / walk call of a leg (planner: 「走」, 「走跑皆可」, none = run) as a code the page words
_MODE = {"走": "walk", "走跑皆可": "mix"}


def _leg(s: dict, hike: bool = False) -> dict:
    t = s.get("target") or {}
    return {"n": s.get("i"), "a": _r(s.get("start_km"), 2), "b": _r(s.get("end_km"), 2),
            "cls": s.get("cls") if isinstance(s.get("cls"), str) else None,
            "mode": "walk" if hike else _MODE.get(s.get("walk"), "run"), "pace_s_per_km": _r(s.get("pace_s_per_km")),
            "t_s": _r(s.get("t")), "cum_s": _r(s.get("cum_s")), "hr_cap": _r(t.get("hr_cap")),
            "cho_g": _r(s.get("cho_g")), "water_ml": _r(s.get("water_ml")), "na_mg": _r(s.get("na_mg")),
            "gain_m": _r(s.get("gain_m")), "loss_m": _r(s.get("loss_m")), "grade": _r(s.get("grade"), 3),
            "day": s.get("day") if isinstance(s.get("day"), int) and not isinstance(s.get("day"), bool) else None}


def trimmed(kind: str) -> Optional[dict]:
    from backend.engine.racepower import share as SH
    snap = SH.example(SH.example_id(kind))
    if not snap:
        return None
    s = snap.get("summary") or {}
    legs = [_leg(x, kind == "baiyue") for x in snap.get("segments") or [] if isinstance(x, dict)]
    days = sorted({x["day"] for x in legs if x["day"]})
    L = LIMITS[kind]
    dk = min(max(round((s.get("km") or L["km"][0]) / L["km_step"]) * L["km_step"], L["km"][0]), L["km"][1])
    dg = min(max(round((s.get("gain_m") or 0) / L["gain_step"]) * L["gain_step"], L["gain"][0]), L["gain"][1])
    calc = {"km": list(L["km"]), "km_step": L["km_step"], "gain": list(L["gain"]), "gain_step": L["gain_step"],
            "default": {"km": dk, "gain": dg}, "result": None}
    return {"kind": kind,
            "summary": {"km": _r(s.get("km"), 1), "gain_m": _r(s.get("gain_m")), "loss_m": _r(s.get("loss_m")),
                        "time_s": _r(s.get("time_s")), "legs": len(legs)},
            "profile": _profile(snap.get("profile") or {}), "legs": legs, "days": days, "calc": calc}


# ---------------------------------------------------------------------------- 課表 (SP-637 R1)
#
# R1 (the user, 2026-10-17): 課表 is the lead, the calculator the support — the hero shows the 課表
# calendar and the 課表 section 「新增一堂課 → 選範本 → 看結構」. Both from public data only: the built-in
# template library (engine/workout_templates.py, code) and the fictional runner's four weeks before the
# 50K (backend/demo/intro_plan.json, frozen by scripts/intro_example.py from the demo athlete). Each
# step goes out as its length and a level 1–5 (how hard, on the step's own target scale) — no target
# number, no threshold, nothing of a model; the page words the level and draws the bars.

PLAN_FILE = Path(__file__).resolve().parents[1] / "demo" / "intro_plan.json"
# the 新增一堂課 demo: three categories × three library templates (names and purposes in intro.json)
TEMPLATE_KEYS = {
    "easy": ("daniels_e", "pal_ez", "pfitz_long"),
    "quality": ("daniels_cruise", "koop_vo2", "billat_3030"),
    "trail": ("dsw_classic", "long_climb", "tech_hard"),
}
# a step with no length of its own (lap button, distance): seconds to draw it with
DIST_S_PER_M = 0.36            # 6:00 /km, the editor's own stand-in (workout_editor.js mini())
OPEN_S = 60


def _mid(t: dict) -> Optional[float]:
    lo, hi = t.get("lo"), t.get("hi")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return None
    return (lo + hi) / 2


def level(target: Optional[dict], kind: str) -> Optional[int]:
    """How hard a step is, 1 (very easy) – 5 (hard), on its own target's scale: power × CP (Palladino's
    zones, the editor's mini chart), heart rate × LTHR (Friel's zones), pace × threshold pace (Daniels:
    slower than E, E, M, T, faster), RPE (Borg CR-10). None: no target (an open bout)."""
    t = target or {}
    ty, m = t.get("type"), _mid(t)
    if ty == "auto":
        intent = t.get("intent")
        if intent == "easy":
            return 1
        if intent == "band" and m is not None:
            ty = "power"
        else:
            return 1 if kind in ("rest", "warm", "cool") else None
    if ty == "hr" and t.get("mode") == "zone":
        return 2
    if m is None:
        return None
    if ty == "power":
        return 1 if m < .75 else 2 if m < .88 else 3 if m < 1.01 else 4 if m < 1.06 else 5
    if ty == "hr":
        return 1 if m < .85 else 2 if m < .90 else 3 if m < .95 else 4 if m < 1.0 else 5
    if ty == "pace":                                       # × threshold pace: bigger = slower
        return 1 if m > 1.12 else 2 if m > 1.04 else 3 if m > 1.01 else 4 if m > .97 else 5
    if ty == "rpe":
        return 1 if m <= 2 else 2 if m <= 4 else 3 if m <= 6 else 4 if m <= 8 else 5
    return None


def _secs(dur: Optional[dict]) -> int:
    d = dur or {}
    v = d.get("value")
    if d.get("type") == "time" and isinstance(v, (int, float)) and v > 0:
        return int(v)
    if d.get("type") == "distance" and isinstance(v, (int, float)) and v > 0:
        return int(round(v * DIST_S_PER_M))
    est = d.get("est")
    return int(est) if isinstance(est, (int, float)) and est > 0 else OPEN_S


_STEP_KINDS = ("warm", "work", "rest", "cool", "other")


def structure(items: list) -> list:
    """A session's steps as the page draws them: [{"k": kind, "s": seconds, "lv": level}], a repeat as
    {"x": times, "items": [...]} (its last rest dropped when the structure says so)."""
    out = []
    for x in items or []:
        if not isinstance(x, dict):
            continue
        if x.get("kind") == "repeat":
            kids = structure(x.get("items") or [])
            times = x.get("times") if isinstance(x.get("times"), int) and x.get("times") > 0 else 1
            if kids:
                out.append({"x": times, "last_rest": x.get("last_rest") is not False, "items": kids})
            continue
        k = x.get("kind") if x.get("kind") in _STEP_KINDS else "other"
        out.append({"k": k, "s": _secs(x.get("dur")), "lv": level(x.get("target"), k), "n": _note(x.get("note"))})
    return out


def _note(v) -> Optional[str]:
    """A step's own words (the app's, 中文), its first part only: 「上坡 · 心率約…」 → 「上坡」."""
    if not isinstance(v, str) or not v.strip():
        return None
    head = v.split(" · ")[0].strip()
    if _TARGET_WORDS.search(head):              # 「EZ ≤ 80% CP」, 「心率 ≤ 150 bpm」: a target in numbers — the level says it
        return None
    return head[:24] or None


_TARGET_WORDS = re.compile(r"%|bpm|\d\s*W\b|\bCP\b|LTHR|HRmax|AeT|Stryd", re.I)


def templates() -> dict:
    from backend.engine import workout_templates as WT
    cats = []
    for cat, keys in TEMPLATE_KEYS.items():
        rows = [{"key": k, "steps": structure(WT.items_of(WT.BY_KEY[k]))} for k in keys if k in WT.BY_KEY]
        cats.append({"id": cat, "templates": rows})
    return {"cats": cats}


PHASES = ("base", "build", "specific", "taper", "event", "recovery", "transition")
_SESSION_KINDS = ("easy", "long", "quality", "test", "hike", "strength", "race", "cross", "rest")


CLIMB_M = 300                  # an easy session with this much climb reads as 爬坡 on the calendar


def _session(s: dict) -> dict:
    k = s.get("kind") if s.get("kind") in _SESSION_KINDS else "easy"
    if k == "easy" and isinstance(s.get("gain_m"), (int, float)) and s["gain_m"] >= CLIMB_M:
        k = "climb"
    return {"kind": k, "title": s.get("title") if isinstance(s.get("title"), str) else None,
            "min": _r(s.get("min")), "km": _r(s.get("km"), 1), "gain_m": _r(s.get("gain_m")),
            "steps": structure(s.get("items") or [])}


def plan() -> Optional[dict]:
    """The fictional runner's calendar, 賽前 N 週 down to the race week: per week its phase and planned
    hours, per day (0 = Monday) its sessions; the race on its day. No date anywhere."""
    try:
        d = json.loads(PLAN_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    planned = d.get("planned_days") if isinstance(d.get("planned_days"), int) else 0
    weeks = []
    for wi, w in enumerate(d.get("weeks") or []):
        days = []
        for i, day in enumerate((w.get("days") or [])[:7]):
            r = day.get("race") if isinstance(day.get("race"), dict) else None
            days.append({"dow": i, "planned": wi * 7 + i < planned,
                         "sessions": [_session(s) for s in day.get("sessions") or [] if isinstance(s, dict)],
                         "race": ({"priority": r.get("priority") if r.get("priority") in ("A", "B", "C") else None,
                                   "main": bool(r.get("a"))} if r else None)})
        full = (wi + 1) * 7 <= planned
        weeks.append({"before": _r(w.get("before")), "phase": w.get("phase") if w.get("phase") in PHASES else None,
                      "hours": _r(w.get("hours"), 1) if full else None, "days": days})
    return {"weeks": weeks}


@router.get("/templates")
def get_templates():
    return JSONResponse(templates(), headers=CACHE_HEADERS)


@router.get("/plan")
def get_plan():
    d = plan()
    if d is None:
        raise HTTPException(404, "no example plan")
    return JSONResponse(d, headers=CACHE_HEADERS)


@router.get("/breakdown")
def get_breakdown():
    d = breakdown()
    if d is None:
        raise HTTPException(404, "no breakdown")
    return JSONResponse(d, headers=CACHE_HEADERS)


@router.get("/example/{kind}")
def get_example(kind: str):
    if kind not in KINDS:
        raise HTTPException(404, "no such example")
    d = trimmed(kind)
    if d is None:
        raise HTTPException(404, "no such example")
    try:                                          # the calculator's first answer, cached like any other
        d["calc"]["result"] = estimate(kind, d["calc"]["default"]["km"], d["calc"]["default"]["gain"])
    except (ValueError, Unavailable):
        d["calc"]["result"] = None
    return JSONResponse(d, headers=CACHE_HEADERS)
