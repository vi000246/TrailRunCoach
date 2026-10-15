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
"""
from __future__ import annotations

import copy
import json
import math
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
PUBLIC_PATHS = frozenset({PREFIX + "/estimate"} | {f"{PREFIX}/example/{k}" for k in KINDS})

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
    global _runner
    with _lock:
        _CACHE.clear()
        _ratios.clear()
        _runner = None
    PER_CLIENT.reset()
    MISS.reset()


def cache_size() -> int:
    with _lock:
        return len(_CACHE)


def page() -> PageResponse:
    from backend.accounts import login as LG
    link = json.dumps(LG.try_link()).replace("<", "\\u003c")
    return render_page(PAGE, fill={"try": f"<script>window.TRC_TRY = {link};</script>"})


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
