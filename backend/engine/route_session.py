"""
A session made from a whole route (SP-641; 課表 › 路線 →「從這條路線產生課」, static/routelib.html;
POST /steps/from-route in api/route_library.py): one step per climb, the race calculator's
「匯出至課表」 way (racepower/watch_export.steps_for: legs that end with the lap button at the place
the step names) on a training route — an uploaded GPX or a 「我的路段」 (engine/route_library.py).

Legs (legs_of):
  * each climb of SP-639's rule (user_templates.CLIMB_*: ≥ 5 %, ≥ 30 m, a drop of max(10 m, 5 %)
    allowed) is one leg; between the climbs the grade segments (the race calculator's 坡段) make
    descents (≤ −3 %) and flat / rolling legs, neighbours of one kind together;
  * a leg shorter than MIN_LEG_M goes into the neighbour whose grade is nearer (its own, net);
  * at most watch_export.LAP_MAX steps: the shortest neighbours merged first (two climbs last).
Targets (SP-632's rule, by the kind of ground with the most time in the leg, at the easy intensity):
  * runnable (−3 … +15 %, watch_export.ep_ok): power in the easy band (power_zone_plan.easy_band;
    no CP: HR ≤ the easy cap) with `ep` = the Effort Pace band (the GAP at that intensity ±
    watch_export.EP_BAND) — COROS gets Effort Pace instead of HR, and instead of power with
    課表偏好「可跑坡目標」 = Effort Pace (workout_steps.ep_intensity);
  * steep (> +15 %): HR ≤ the easy cap; without LTHR / AeT RPE 3–4 with the climb;
  * descents: no target, 「控制、安全」 in the name.
The run is easy (Z2): a route session is a long trail run, not a race. Each step is 「直到按下計圈」 with
its estimated time (`est`, the step chart's speed: user_templates._flat_kmh over Minetti's factor,
walked along the profile as route_elevation does, so the chart puts each step on its stretch),
named 「爬 120 m · 約 6′」 (user_templates.CLIMB_LABEL, as SP-640's hill repeats). The session gets the
route's profile copy (the chart); the watch gets the steps and targets only (never the route). 推估.
"""
from __future__ import annotations

import math
from typing import Optional

from backend.i18n import N_, _

MIN_LEG_M = 200.0                 # SP-633 B: shorter legs go into the neighbour with the nearer grade
DOWN_GRADE = -0.03                # watch_export._group / seg_targets.DESCENT
RPE_STEEP = (3, 4)                # 推估: an easy run's steep climb without HR (RPE_EASY_MAX = 4)
CLIMB, DOWN, FLAT = "climb", "down", "flat"
RUN, STEEP = "run", "steep"       # the ground's class for the target (DOWN as above)

DOWN_LABEL = N_("下坡 {m} m · 約 {t}")
FLAT_LABEL = N_("平路 {km} km · 約 {t}")
ROLL_LABEL = N_("起伏 {km} km · 約 {t}")
ROLL_M = 10.0                     # a flat leg climbing or dropping this much is 「起伏」 (rolling), not 「平路」
SAFE = N_("控制、安全")
TITLE = N_("路線：{name}")
EASY_NOTE = N_("強度是輕鬆跑（Z2）：可跑的坡看功率（COROS 可改 Effort Pace），陡坡看心率上限，下坡不設目標")
MERGED_NOTE = N_("路線有 {n} 段，超過手錶建議的 {limit} 步：相鄰的段合併成 {m} 步")


# ---------------------------------------------------------------------------
# the legs
# ---------------------------------------------------------------------------

class _Prof:
    def __init__(self, prof: dict):
        self.km = [float(k) for k in prof.get("km") or []]
        self.z = [float(v) for v in prof.get("z") or []]

    def z_at(self, k: float) -> float:
        from backend.engine.user_templates import _interp
        return _interp(k, self.km, self.z)

    def stats(self, a: float, b: float) -> dict:
        """dist_m, gain_m, loss_m, net grade and mean elevation of [a, b] km along the profile."""
        import bisect
        pts = [a] + self.km[bisect.bisect_right(self.km, a):bisect.bisect_left(self.km, b)] + [b]
        zs = [self.z_at(k) for k in pts]
        gain = sum(max(0.0, y - x) for x, y in zip(zs, zs[1:]))
        loss = sum(max(0.0, x - y) for x, y in zip(zs, zs[1:]))
        d = max(0.0, (b - a) * 1000.0)
        return {"dist_m": d, "gain_m": gain, "loss_m": loss, "grade": (zs[-1] - zs[0]) / d if d > 0 else 0.0,
                "z_mean": sum(zs) / len(zs)}


def _gap_pieces(segs: list, a: float, b: float) -> list[dict]:
    """[a, b] (between two climbs) cut by the grade segments into descents and flat / rolling stretches,
    neighbours of one kind together."""
    out: list[dict] = []
    for s in segs:
        x0, x1 = max(a, float(s["start_km"])), min(b, float(s["end_km"]))
        if x1 - x0 < 1e-4:
            continue
        t = DOWN if float(s["grade"]) <= DOWN_GRADE else FLAT
        if out and out[-1]["type"] == t and abs(out[-1]["b"] - x0) < 1e-6:
            out[-1]["b"] = x1
        else:
            if out and x0 > out[-1]["b"]:
                out[-1]["b"] = x0             # a gap between segments: the stretch before runs on
            out.append({"a": x0, "b": x1, "type": t, "no": None})
    if not out and b - a > 1e-4:
        out.append({"a": a, "b": b, "type": FLAT, "no": None})
    if out:
        out[0]["a"], out[-1]["b"] = a, b
    return out


def _merge2(p: dict, q: dict) -> dict:
    """Two neighbouring pieces as one: the longer one's kind (a climb stays a climb over a short piece)."""
    lp, lq = p["b"] - p["a"], q["b"] - q["a"]
    big = p if lp >= lq else q
    return {"a": min(p["a"], q["a"]), "b": max(p["b"], q["b"]), "type": big["type"], "no": big["no"], "cg": big.get("cg")}


def _coalesce(ps: list[dict]) -> list[dict]:
    out: list[dict] = []
    for p in ps:
        if out and out[-1]["type"] == p["type"] and p["type"] != CLIMB:
            out[-1] = _merge2(out[-1], p)
        else:
            out.append(p)
    return out


def legs_of(course: dict, prof: dict, limit: Optional[int] = None) -> list[dict]:
    """The route's legs [{start_km, end_km, type climb | down | flat, climb_no, dist_m, gain_m, loss_m,
    grade, z_mean}], contiguous from the route's start to its end (the module docstring)."""
    from backend.engine.racepower import watch_export as WE
    limit = limit or WE.LAP_MAX
    P = _Prof(prof)
    if len(P.km) < 2:
        return []
    k0, k1 = P.km[0], P.km[-1]
    segs = sorted(course.get("segments") or [], key=lambda s: float(s["start_km"]))
    climbs = sorted(course.get("climbs") or [], key=lambda c: float(c["start_km"]))
    ps: list[dict] = []
    pos = k0
    for c in climbs:
        a, b = max(pos, float(c["start_km"])), min(k1, float(c["end_km"]))
        if b - a < 1e-4:
            continue
        ps += _gap_pieces(segs, pos, a) if a - pos > 1e-4 else []
        ps.append({"a": a, "b": b, "type": CLIMB, "no": c.get("no"), "cg": float(c.get("gain_m") or 0.0)})
        pos = b
    if k1 - pos > 1e-4:
        ps += _gap_pieces(segs, pos, k1)
    if not ps:
        ps = [{"a": k0, "b": k1, "type": FLAT, "no": None}]
    ps = _coalesce(ps)

    def grade(p):
        return P.stats(p["a"], p["b"])["grade"]

    # legs under MIN_LEG_M → the neighbour with the nearer grade (the shortest first)
    while len(ps) > 1:
        short = [i for i, p in enumerate(ps) if (p["b"] - p["a"]) * 1000.0 < MIN_LEG_M]
        if not short:
            break
        i = min(short, key=lambda j: ps[j]["b"] - ps[j]["a"])
        g = grade(ps[i])
        nb = [j for j in (i - 1, i + 1) if 0 <= j < len(ps)]
        j = min(nb, key=lambda k: (abs(grade(ps[k]) - g), ps[k]["b"] - ps[k]["a"]))
        lo, hi = min(i, j), max(i, j)
        ps[lo:hi + 1] = [_merge2(ps[lo], ps[hi])]
        ps = _coalesce(ps)
    # ≤ limit steps: the shortest neighbours first, two climbs last
    while len(ps) > limit:
        def cost(j):
            a, b = ps[j], ps[j + 1]
            w = 4.0 if a["type"] == CLIMB and b["type"] == CLIMB else 1.0 if a["type"] == b["type"] else 1.5
            return (b["b"] - a["a"]) * w
        j = min(range(len(ps) - 1), key=cost)
        ps[j:j + 2] = [_merge2(ps[j], ps[j + 1])]
    out = []
    for p in ps:
        st = P.stats(p["a"], p["b"])
        out.append({"start_km": round(p["a"], 4), "end_km": round(p["b"], 4), "type": p["type"],
                    "climb_no": p["no"] if p["type"] == CLIMB else None,
                    # the climb's own gain (SP-639's 坡段表 and SP-640's 「爬 120 m」: the detector's, top − foot)
                    "climb_gain_m": round(p["cg"], 1) if p["type"] == CLIMB and p.get("cg") is not None else None,
                    **{k: round(v, 4 if k == "grade" else 1) for k, v in st.items()}})
    return out


def n_pieces(course: dict, prof: dict) -> int:
    """How many legs the route has before the watch's limit (the 「合併成 m 步」 note)."""
    return len(legs_of(course, prof, limit=10 ** 6))


# ---------------------------------------------------------------------------
# targets and time
# ---------------------------------------------------------------------------

def _cls(g: float, z: float, gap: float) -> tuple[str, bool]:
    """(the ground's class, Effort Pace eligible) of a stretch at grade g (watch_export.ep_ok)."""
    from backend.engine.racepower import watch_export as WE
    ok = WE.ep_ok({"grade": g, "gap_pace_s_per_km": gap, "z_mean": z})
    if g < WE.EP_GRADE[0] - 1e-9:
        return DOWN, False
    if g > WE.EP_GRADE[1] + 1e-9:
        return STEEP, False
    return RUN, ok


def _ground(leg: dict, segs: list, P: _Prof, v: float) -> tuple[str, bool]:
    """The class with the most time in the leg (at flat speed v km/h over Minetti's factor), and whether
    most of the leg's time is Effort Pace ground (watch_export.EP_SHARE_MIN)."""
    from backend.engine import user_templates as UT
    from backend.engine.racepower import watch_export as WE
    a, b = leg["start_km"], leg["end_km"]
    gap = 3600.0 / max(0.1, v)
    t: dict = {}
    ept = tot = 0.0
    parts = [(max(a, float(s["start_km"])), min(b, float(s["end_km"])), float(s["grade"])) for s in segs]
    parts = [p for p in parts if p[1] - p[0] > 1e-4] or [(a, b, leg["grade"])]
    for x0, x1, g in parts:
        z = (P.z_at(x0) + P.z_at(x1)) / 2.0
        sec = (x1 - x0) / (v / UT._gf(g)) * 3600.0
        k, ok = _cls(g, z, gap)
        t[k] = t.get(k, 0.0) + sec
        tot += sec
        ept += sec if ok else 0.0
    k = max(t.items(), key=lambda kv: kv[1])[0]
    return k, k == RUN and ept > WE.EP_SHARE_MIN * max(tot, 1e-9)


def _target(cls: str, ep: Optional[list], gain_m: float, c) -> dict:
    from backend.engine import workout_steps as WS
    from backend.engine.power_zone_plan import easy_band
    from backend.engine.zones import EASY_BAND
    has_hr = bool(c.hrz and c.hrz.get("easy")) or bool(c.aet or c.lthr)
    if cls == DOWN:
        return {"type": "none"}
    if cls == STEEP:
        if has_hr:
            return {"type": "hr", "mode": "zone", "zone": "aet"}
        return {"type": "rpe", "lo": RPE_STEEP[0], "hi": RPE_STEEP[1], **({"up": int(round(gain_m))} if gain_m >= 1 else {})}
    if c.cp:
        lo, hi = easy_band(EASY_BAND, c.pzm)
        tg = {"type": "power", "mode": "pct", "lo": round(float(lo), 3), "hi": round(float(hi), 3)}
    elif has_hr:
        tg = {"type": "hr", "mode": "zone", "zone": "aet"}
    else:
        tg = {"type": "rpe", "lo": WS.RPE_EASY_MAX - 1, "hi": WS.RPE_EASY_MAX}
    if ep:
        tg["ep"] = ep
    return tg


def _speed(st: dict, c) -> float:
    """The step chart's flat speed for a step (route_elevation's)."""
    from backend.engine import user_templates as UT
    from backend.engine import workout_steps as WS
    r = WS.resolve(st, c)
    return UT._flat_kmh(r.frac if r.frac is not None else WS.NONE_IF.get(st["kind"], c.easy_frac()), c)


def build(course: dict, prof: dict, name: str, src: dict, c) -> dict:
    """{"steps" (workout_steps format, origin user, the route's copy with its source), "legs",
    "minutes", "km", "gain_m", "loss_m", "merged", "notes", "title"} of a route (the module docstring)."""
    from backend.engine import user_templates as UT
    from backend.engine import workout_steps as WS
    from backend.engine.racepower import watch_export as WE
    legs = legs_of(course, prof)
    if not legs:
        raise ValueError(_("這條路線沒有高度剖面"))
    P = _Prof(prof)
    k0 = P.km[0]
    # the session's copy of the profile (≤ ROUTE_MAX points): the chart walks this one, so do the estimates
    r = UT.route_copy({**prof, "name": name, "route_km": round(P.km[-1] - k0, 3),
                       "gain_m": round(sum(x["gain_m"] for x in legs), 1)})
    walk = UT._Walk([k - r["km"][0] for k in r["km"]], r["z"])
    segs = sorted(course.get("segments") or [], key=lambda s: float(s["start_km"]))
    v_easy = UT._flat_kmh(c.easy_frac(), c)
    items, out_legs = [], []
    for i, lg in enumerate(legs, 1):
        mine = [s for s in segs if float(s["end_km"]) > lg["start_km"] and float(s["start_km"]) < lg["end_km"]]
        cls, ep_on = _ground(lg, mine, P, v_easy)
        kind = "other" if cls == DOWN else "work"
        st = {"id": f"g{i}", "kind": kind, "dur": {"type": "open"}, "target": _target(cls, None, lg["gain_m"], c), "note": ""}
        v = _speed(st, c)
        if ep_on:
            g = 3600.0 / v
            st["target"]["ep"] = [int(round(g * (1 - WE.EP_BAND))), int(round(g * (1 + WE.EP_BAND)))]
        _pos, secs, _d, _gn, _ls, _pts = walk.move(lg["start_km"] - k0, 1, v, dist=lg["end_km"] - lg["start_km"])
        secs = float(math.ceil(max(5.0, min(6 * 3600.0, secs)) - 1e-6))     # whole s, never short of the end
        t = UT._mins(secs)
        if lg["type"] == CLIMB:
            note = _(UT.CLIMB_LABEL, m=round(lg["climb_gain_m"] if lg["climb_gain_m"] is not None else lg["gain_m"]), t=t)
        elif lg["type"] == DOWN:
            note = _(DOWN_LABEL, m=round(lg["loss_m"]), t=t)
        else:
            roll = max(lg["gain_m"], lg["loss_m"]) >= ROLL_M
            note = _(ROLL_LABEL if roll else FLAT_LABEL, km=f"{lg['dist_m'] / 1000.0:.1f}", t=t)
        if cls == DOWN:
            note += " · " + _(SAFE)
        st["dur"]["est"] = int(round(secs))
        st["note"] = note[:WS.MAX_NOTE]
        items.append(st)
        out_legs.append({**lg, "cls": cls, "est_s": secs, "name": st["note"], "target": st["target"]})
    r["src"] = {"kind": src["kind"], "id": str(src["id"])}
    r["name"] = name
    steps = {"v": WS.V, "origin": "user", "items": items, "route": r}
    n_all = n_pieces(course, prof)
    notes = [_("每段「直到按下計圈」：到步驟名稱寫的地點時按一下計圈（lap）"), _(EASY_NOTE)]
    if n_all > len(legs):
        notes.insert(0, _(MERGED_NOTE, n=n_all, limit=WE.LAP_MAX, m=len(legs)))
    if any(st["target"].get("ep") for st in items):
        notes.append(_(WE.EP_NOTE))
    tot = sum(x["est_s"] for x in out_legs)
    return {"steps": steps, "legs": out_legs, "minutes": int(round(tot / 60.0)),
            "km": round(P.km[-1] - k0, 2), "gain_m": round(sum(x["gain_m"] for x in legs)),
            "loss_m": round(sum(x["loss_m"] for x in legs)), "merged": max(0, n_all - len(legs)),
            "notes": notes, "title": _(TITLE, name=name)[:60]}
