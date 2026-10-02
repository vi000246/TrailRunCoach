"""
A session's structure as steps (docs/plans/workout-editor.plan.md §3.1): warm-up,
work, rest, cool-down and other steps, ×N repeat blocks (one level of nesting),
each step with a duration and a target. The 課表 page's editor shows and edits
it; sync/coros_workouts pushes it.

    {"v": 1, "origin": "derived" | "template:<variant key>" | "user",
     "items": [
       {"id": "a1", "kind": "warm", "dur": {"type": "time", "value": 600},
        "target": {"type": "auto", "intent": "easy"}, "note": "市區輕鬆跑到河濱"},
       {"id": "a2", "kind": "repeat", "times": 5, "last_rest": false, "note": "", "items": [
          {"id": "a3", "kind": "work", "dur": {"type": "time", "value": 120},
           "target": {"type": "auto", "intent": "band", "lo": 1.06, "hi": 1.12, "cls": "Z5"}},
          {"id": "a4", "kind": "rest", "dur": {"type": "time", "value": 120},
           "target": {"type": "auto", "intent": "open"}, "note": "走路或極慢跑"}]},
       {"id": "a5", "kind": "cool", "dur": {"type": "open"}, "target": {"type": "power", "mode": "abs",
        "lo": 150, "hi": 170}}]}

kind      warm | work | rest | cool | other, and the container `repeat` (times 1–99;
          `last_rest` false = no rest after the last rep, which a COROS group can't
          express: such a block is pushed unrolled, one lap per step)
dur       time (s) | distance (m) | open (ends with the lap button)
target    auto — what 「目標用：自動／心率／功率」 (engine/target_policy.py) gives the step:
             intent easy   HR ≤ AeT (with plo/phi: the power band when the session runs by power)
             intent band   lo/hi × CP on power; on HR the class's % LTHR (or the text's bpm)
             intent open   no target (drills, walk rests, all-out bouts)
          power | hr | pace — the user's override of one step:
             mode pct (× CP / × LTHR / × threshold pace), zone (Palladino / Friel id,
             hr also "aet"), abs (W / bpm / s per km — never rescaled by a threshold
             change, except watts on a CP change: engine/plan_auto.rescale_sessions)
          none
Relative targets are stored and resolved with today's thresholds, so a CP update
changes the watts (and the COROS fingerprint: the session shows 「已過期」).

`derive(s)` builds the steps of a stored session that has none from its kind,
variant and text, mirroring coros_workouts.session_steps step by step (golden
tests: the payload is identical); nothing is stored until the user saves.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Optional

from backend.engine import interval_library as IL
from backend.engine.zones import FRIEL_HR, FRIEL_PACE, PALLADINO_POWER_ZONES

V = 1
KINDS = ("warm", "work", "rest", "cool", "other")
KIND_LABEL = {"warm": "暖身", "work": "主課", "rest": "休息", "cool": "緩和", "other": "其他", "repeat": "重複"}
TYPES = ("auto", "power", "hr", "pace", "none")
TYPE_LABEL = {"auto": "自動", "power": "功率", "hr": "心率", "pace": "配速", "none": "無"}
MODES = ("pct", "zone", "abs")
INTENTS = ("easy", "band", "open")
DUR_TYPES = ("time", "distance", "open")
MAX_TIMES = 99
MAX_DEPTH = 2                    # a repeat may hold one more level of repeats
MAX_ITEMS = 120                  # steps in the model (the editor's limit; COROS is checked apart)
COROS_MAX_STEPS = 50             # Garmin's documented limit; COROS: 未驗證 (plan §3.4)
MAX_NOTE = 60
OPEN_CHART_S = 90                # the chart width of a lap-button step
DIST_PACE_DEFAULT = 360.0        # s/km for a distance step with no pace at all (推估)

# Zone tables for the editor's 區間 choice: (id, lo, hi) fractions; open ends closed (推估)
POWER_ZONES = [(z, lo, hi if hi is not None else 1.8) for z, _n, lo, hi in PALLADINO_POWER_ZONES]
HR_ZONES = [("aet", None, None)] + [(z, lo if lo else 0.70, hi if hi is not None else 1.10)
                                    for z, _n, lo, hi in FRIEL_HR]
PACE_ZONES = [(z, lo if lo is not None else 0.85, hi if hi is not None else 1.45)
              for z, _n, lo, hi in FRIEL_PACE]         # × threshold pace; bigger = slower
ZONES = {"power": POWER_ZONES, "hr": HR_ZONES, "pace": PACE_ZONES}

# Z5 / Z3 rules (interval_library §C2): 徐國峰 ≥ 2 min; Buchheit rest; Haugen ≥ 3 min
Z5_MIN_REP_S, Z3_MIN_REP_S, Z5_MAX_REST_S = IL.Z5_MIN_REP_S, IL.Z3_MIN_REP_S, IL.Z5_MAX_REST_S
Z5_FRAC = IL.CLASS_RANGE["Z5"][0]
HR_WORK = {"Z3sub": ("aet", 1.00), "Z3near": (0.95, 1.00), "Z4": (1.00, 1.03), "Z5": (1.00, 1.05)}    # = coros_workouts.HR_WORK

WARM_NAME = {"city": "市區輕鬆跑到河濱", "river": "河濱輕鬆→漸進", "drills": "動態伸展／drill"}
REST_NAME = {"walk": "走路或極慢跑", "jog": "慢跑恢復", "jog_down": "慢跑／走下坡", "none": "恢復"}


class StepsError(ValueError):
    """A structure that can't be stored; `errors` lists every problem."""

    def __init__(self, errors: list[str]):
        super().__init__("；".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# thresholds + the session's basis
# ---------------------------------------------------------------------------

@dataclass
class Ctx:
    cp: Optional[float] = None
    lthr: Optional[float] = None
    aet: Optional[float] = None
    tpace: Optional[float] = None          # threshold pace, s/km (thresholds.estimate_tpace: 推估)
    basis: str = "hr"                       # hr | power | none (target_policy)
    hr_cap: bool = False                    # target_policy: an HR cap note on power sessions
    # the athlete's own speeds, for the time of a distance step (estimate_secs)
    v_easy: Optional[float] = None          # km/h, easy flat road running (equivalence.fit v_flat)
    v_easy_src: str = ""
    ep_kmh: Optional[float] = None          # km/h of effort distance on trail (equivalence trail EP)
    terrain: str = "road"                   # road | trail
    climb_per_km: float = 0.0               # m/km of the session (trail: EP = km + climb/100)

    @classmethod
    def of(cls, th: Optional[dict], basis: Optional[str] = None, hr_cap: bool = False,
           speeds: Optional[dict] = None) -> "Ctx":
        th = th or {}

        def f(k, d=None):
            d = th if d is None else d
            try:
                return float(d[k]) if d.get(k) else None
            except (TypeError, ValueError):
                return None
        sp = speeds or {}
        ter = "trail" if sp.get("terrain") in ("trail", "hike") else "road"
        return cls(cp=f("cp"), lthr=f("lthr"), aet=f("aet"), tpace=f("tpace"),
                   basis=basis if basis in ("hr", "power", "none") else "hr", hr_cap=hr_cap,
                   v_easy=f("v_easy", sp), v_easy_src=str(sp.get("v_easy_src") or ""), ep_kmh=f("ep_kmh", sp),
                   terrain=ter, climb_per_km=max(0.0, f("climb_per_km", sp) or 0.0) if ter == "trail" else 0.0)


def session_ctx(s: dict, th: Optional[dict], prefs=None) -> Ctx:
    """The Ctx of a session: its basis from engine/target_policy (the session's own
    目標用, 課表偏好, else 自動 by session type; falls back without CP / LTHR)."""
    from backend.engine import target_policy as TP
    pol = TP.target_policy(s, prefs, th or {})
    b = s.get("basis") if s.get("basis") in ("hr", "power", "none") else pol["basis"]
    if b == "power" and th and not (th or {}).get("cp"):
        b = pol["basis"]
    return Ctx.of(th, b, bool(pol.get("hr_cap")))


def easy_hr(c: Ctx) -> Optional[tuple]:
    """HR ≤ AeT (coros_workouts.easy_hr)."""
    hi = c.aet or (0.89 * c.lthr if c.lthr else None)
    if not hi:
        return None
    lo = 0.75 * c.lthr if c.lthr else hi - 25
    lo = min(lo, hi - 10)
    return ("hr", round(lo), round(hi))


def _power(c: Ctx, lo: float, hi: float) -> Optional[tuple]:
    return ("power", round(lo * c.cp), round(hi * c.cp)) if c.cp else None


def _work_hr(c: Ctx, tg: dict) -> Optional[tuple]:
    """HR of a band step (coros_workouts._work_hr): the text's bpm, else the class's % LTHR."""
    if tg.get("hr"):
        return ("hr", int(tg["hr"][0]), int(tg["hr"][1]))
    if tg.get("hrp") and c.lthr:                 # a template's own % LTHR (workout_templates)
        return ("hr", round(tg["hrp"][0] * c.lthr), round(tg["hrp"][1] * c.lthr))
    a, b = HR_WORK.get(tg.get("cls") or "", (0.95, 1.00))
    if not c.lthr:
        return None
    lo = c.aet if a == "aet" else a * c.lthr
    return ("hr", round(lo or 0.89 * c.lthr), round(b * c.lthr))


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------

class _Ids:
    def __init__(self, prefix: str = "s"):
        self.n, self.p = 0, prefix

    def __call__(self) -> str:
        self.n += 1
        return f"{self.p}{self.n}"


def step(ids, kind: str, dur, target: Optional[dict] = None, note: str = "") -> dict:
    if isinstance(dur, (int, float)):
        dur = {"type": "time", "value": int(dur)} if dur else {"type": "open"}
    return {"id": ids(), "kind": kind, "dur": dur, "target": target or {"type": "auto", "intent": "open"},
            "note": note}


def rep(ids, times: int, items: list, last_rest: bool = True, note: str = "") -> dict:
    return {"id": ids(), "kind": "repeat", "times": int(times), "last_rest": bool(last_rest), "note": note,
            "items": items}


EASY = {"type": "auto", "intent": "easy"}
OPEN = {"type": "auto", "intent": "open"}


def _easy(plo=None, phi=None) -> dict:
    return {**EASY, "plo": plo, "phi": phi} if plo is not None else dict(EASY)


def doc(items: list, origin: str = "derived") -> dict:
    return {"v": V, "origin": origin, "items": items}


# ---------------------------------------------------------------------------
# derive: a stored session's steps from its kind / variant / text (mirrors
# coros_workouts.session_steps — tests/test_workout_steps.py checks the payload)
# ---------------------------------------------------------------------------

def _num(pat: str, text: str, default: Optional[int] = None, group: int = 1) -> Optional[int]:
    m = re.search(pat, text or "")
    return int(m.group(group)) if m and m.group(group) else default


def _text_hr(target: str) -> Optional[list]:
    m = re.search(r"心率\s*(\d+)\s*[–-]\s*(\d+)\s*bpm", target or "")
    return [int(m.group(1)), int(m.group(2))] if m else None


def from_variant(v: IL.Variant, level: str = "std", target_text: str = "", ids=None,
                 origin: Optional[str] = None) -> dict:
    """A library variant (interval_library.steps' blocks and reps) as steps: the
    warm-up blocks, the reps as one ×N block with no rest after the last (pushed
    one lap per step, like coros_workouts._variant_steps), pyramids flat, 30/15 as
    sets × reps, the cool-down."""
    ids = ids or _Ids()
    b = IL.blocks(v, level)
    out = []
    for code, m, _t in b["warm"]:
        if code == "strides":
            n = max(1, int(m))
            out.append(rep(ids, n, [step(ids, "other", 20, OPEN, "快步跑 20 秒"), step(ids, "rest", 40, OPEN, "慢跑")],
                           True, f"快步跑 {n}×20 秒"))
        elif code == "drills":
            out.append(step(ids, "warm", int(m * 60), OPEN, WARM_NAME["drills"]))
        else:
            out.append(step(ids, "warm", int(m * 60), EASY, WARM_NAME.get(code, "暖身")))
    out += main_set(v, target_text, ids)
    out.append(step(ids, "cool", int(b["cool_min"] * 60), EASY, "緩和"))
    return doc(out, origin or f"template:{v.key}")


def _band(v: IL.Variant, target_text: str = "") -> dict:
    tg = {"type": "auto", "intent": "band", "lo": v.lo, "hi": v.hi, "cls": v.cls}
    hr = _text_hr(target_text)
    if hr:
        tg["hr"] = hr
    return tg


def _rest(ids, s: int, mode: str) -> dict:
    return step(ids, "rest", int(s), EASY if mode == "jog" else OPEN, REST_NAME.get(mode, "恢復"))


def main_set(v: IL.Variant, target_text: str = "", ids=None) -> list:
    """The variant's reps (+ rests) only — the editor's 插入範本."""
    ids = ids or _Ids()
    band = _band(v, target_text)
    if v.sets > 1:
        inner = rep(ids, v.reps, [step(ids, "work", v.work_s, dict(band)), _rest(ids, v.rest_s, v.rest_mode)], False)
        return [rep(ids, v.sets, [inner, _rest(ids, v.set_rest_s, "jog")], False)]
    works = v.works
    if v.pattern or not v.rest_s or len(works) == 1:
        out = []
        for i, w in enumerate(works):
            out.append(step(ids, "work", w, dict(band)))
            if i < len(works) - 1 and v.rest_s:
                out.append(_rest(ids, v.rest_s, v.rest_mode))
        return out
    return [rep(ids, len(works), [step(ids, "work", v.work_s, dict(band)), _rest(ids, v.rest_s, v.rest_mode)], False)]


def _quality_text(s: dict, ids) -> Optional[list]:
    """coros_workouts._quality_steps' text path (a 「N×M 分」 title)."""
    from backend.sync.coros_workouts import _FRAC
    title, detail, target = s.get("title", "") or "", s.get("detail", "") or "", s.get("target", "") or ""
    m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*分", title)
    if not m:
        return None
    reps, work = int(m.group(1)), int(m.group(2))
    rest = _num(r"休\s*\d+\s*[–-]\s*(\d+)\s*分", detail) or _num(r"休\s*(\d+)\s*分", detail) or work
    warm = _num(r"暖身\s*(\d+)\s*分", detail, 15)
    cool = _num(r"緩和\s*(\d+)\s*分", detail, 10)
    cool = max(cool, int(s.get("minutes") or 0) - warm - reps * (work + rest))
    tid = "supra" if "爬坡" in title or "Supra" in (s.get("source") or "") else "threshold"
    plo, phi = _FRAC[tid][:2]
    pm = re.search(r"(\d+)\s*[–-]\s*(\d+)\s*%\s*CP", detail + " " + target)
    if pm:
        plo, phi = int(pm.group(1)) / 100.0, int(pm.group(2)) / 100.0
    hr = _text_hr(target)
    if hr and "功率" not in target:
        work_t = {"type": "hr", "mode": "abs", "lo": hr[0], "hi": hr[1]}      # the text holds only an HR range
    else:
        work_t = {"type": "auto", "intent": "band", "lo": plo, "hi": phi, "cls": ""}
        if hr:
            work_t["hr"] = hr
    return [step(ids, "warm", warm * 60, EASY),
            rep(ids, reps, [step(ids, "work", work * 60, work_t, f"{work} 分"), step(ids, "rest", rest * 60, OPEN)],
                True, f"{reps}×{work} 分"),
            step(ids, "cool", cool * 60, EASY)]


def _cp_test(s: dict, ids) -> Optional[list]:
    from backend.engine import cp_protocols as CPP
    proto = CPP.protocol_of(s) or "standard"
    if proto == "race":
        return None
    t = CPP.TABLE[proto]
    title, detail = s.get("title", "") or "", s.get("detail", "") or ""
    warm = _num(r"暖身\s*(\d+)\s*分", detail, t["warm"])
    cool = _num(r"緩和\s*(\d+)\s*分", detail, t["cool"])
    if proto == "quick":
        work = _num(r"(\d+)\s*分全力", title, 20)
        return [step(ids, "warm", warm * 60, EASY), step(ids, "work", work * 60, OPEN, f"{work} 分全力"),
                step(ids, "cool", cool * 60, EASY)]
    a = _num(r"(\d+)\s*分\s*\+", title, 12)
    b = _num(r"\+\s*(\d+)\s*分", title, 3)
    gap = _num(r"休\s*(\d+)\s*分", (s.get("target") or "") + detail, t["rest"])
    return [step(ids, "warm", warm * 60, EASY), step(ids, "work", a * 60, OPEN, f"{a} 分全力"),
            step(ids, "rest", gap * 60, EASY, "恢復"), step(ids, "work", b * 60, OPEN, f"{b} 分全力"),
            step(ids, "cool", cool * 60, EASY)]


def _aet_test(s: dict, c: Ctx, ids) -> list:
    from backend.engine.aet_test import protocol_of_title
    text = f"{s.get('target', '')} {s.get('detail', '')}"
    warm = _num(r"暖身\s*(\d+)\s*分", text, 10)
    main = _num(r"測試\s*(\d+)\s*分", text, 40)
    cool = _num(r"緩和\s*(?:\d+\s*[–-]\s*)?(\d+)\s*分", text, 0)
    proto = protocol_of_title(s.get("title"))
    if proto in ("xu90", "friel"):
        name = "固定 E 配速，不要調（心率 1 區）" if proto == "xu90" else "AeT 心率附近穩定跑"
        out = [step(ids, "warm", warm * 60, EASY), step(ids, "work", main * 60, EASY, name)]
    else:
        p = _num(r"固定功率\s*(\d+)\s*W", text) or (round(0.75 * c.cp) if c.cp else None)
        hr0 = _num(r"心率從\s*(\d+)", text)
        e = easy_hr(c)
        warm_t = {"type": "hr", "mode": "abs", "lo": min(e[1], hr0 - 10), "hi": hr0} if hr0 and e else EASY
        main_t = {"type": "power", "mode": "abs", "lo": round(p * 0.97), "hi": round(p * 1.03)} if p else OPEN
        out = [step(ids, "warm", warm * 60, warm_t), step(ids, "work", main * 60, main_t, "固定功率，不要調")]
    if cool:
        out.append(step(ids, "cool", cool * 60, EASY))
    return out


def derive(s: dict, th: Optional[dict] = None) -> Optional[dict]:
    """The steps of a session that has none (the editor's starting point), or None
    for what isn't pushed (race, rest, strength, passive heat) and unreadable text."""
    ids = _Ids()
    c = Ctx.of(th)
    kind = s.get("kind")
    secs = int(s.get("minutes") or 0) * 60
    if kind in ("race", "rest", "strength", "heat_passive"):
        return None
    if kind == "notice":
        return doc([step(ids, "warm", 60, OPEN, "課表待確認：到總覽頁同意／拒絕")])
    if kind == "quality":
        v = IL.resolve(s.get("variant_key"), s.get("variant_reps"), s.get("variant_adj")) if s.get("variant_key") else None
        if v is not None:
            d = from_variant(v, s.get("variant_blocks") or "std", s.get("target") or "", ids)
            return {**d, "origin": "derived"}
        items = _quality_text(s, ids)
        return doc(items) if items else None
    if kind == "test":
        from backend.engine.aet_test import is_aet_session
        items = _aet_test(s, c, ids) if is_aet_session(s) else _cp_test(s, ids)
        return doc(items) if items else None
    if secs <= 0:
        return None
    if kind in ("long", "mountain", "hike"):
        lo, hi = (0.80, 0.88) if kind == "long" else (0.75, 0.88)
        return doc([step(ids, "work", secs, _easy(lo, hi))])
    if kind == "easy" and (s.get("heat") or "熱適應" in (s.get("title") or "")) and secs >= 20 * 60:
        return doc([step(ids, "warm", 10 * 60, EASY, "熱適應：慢慢進入"),
                    step(ids, "work", secs - 15 * 60, EASY, "熱適應：照心率、配速放慢"),
                    step(ids, "cool", 5 * 60, OPEN, "走路降溫")])
    if kind == "easy":
        m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*秒", s.get("title", "") or "")
        if m:
            n, sprint = int(m.group(1)), int(m.group(2))
            base = secs - n * (sprint + 60)
            if base >= 10 * 60:
                return doc([step(ids, "work", base, _easy(0.75, 0.80), "心率 ≤ AeT"),
                            rep(ids, n, [step(ids, "work", sprint, OPEN, f"{sprint} 秒上坡衝刺"),
                                         step(ids, "rest", 60, OPEN, "走下來")], True, f"衝刺 {n}×{sprint} 秒")])
        return doc([step(ids, "work", secs, _easy(0.75, 0.80))])
    return None


# ---------------------------------------------------------------------------
# validation of the stored shape
# ---------------------------------------------------------------------------

def _f(x, name: str, errs: list, lo: float = None, hi: float = None) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        errs.append(f"{name} 要是數字")
        return None
    if v != v or (lo is not None and v < lo) or (hi is not None and v > hi):
        errs.append(f"{name} 超出範圍")
        return None
    return v


def _norm_target(t, errs: list) -> dict:
    if not isinstance(t, dict):
        return dict(OPEN)
    ty = t.get("type", "auto")
    if ty not in TYPES:
        errs.append(f"目標類型不對：{ty!r}")
        return dict(OPEN)
    if ty == "none":
        return {"type": "none"}
    if ty == "auto":
        it = t.get("intent", "open")
        if it not in INTENTS:
            errs.append(f"自動目標的類型不對：{it!r}")
            return dict(OPEN)
        out = {"type": "auto", "intent": it}
        if it == "easy" and t.get("plo") is not None:
            out["plo"], out["phi"] = _f(t.get("plo"), "功率下限", errs, 0.3, 2.5), _f(t.get("phi"), "功率上限", errs, 0.3, 2.5)
        if it == "band":
            out["lo"], out["hi"] = _f(t.get("lo"), "強度下限", errs, 0.3, 2.5), _f(t.get("hi"), "強度上限", errs, 0.3, 2.5)
            out["cls"] = str(t.get("cls") or "")
            if t.get("hr"):
                h = t["hr"]
                if isinstance(h, (list, tuple)) and len(h) == 2:
                    out["hr"] = [int(_f(h[0], "心率", errs, 40, 230) or 0), int(_f(h[1], "心率", errs, 40, 230) or 0)]
            if t.get("hrp"):
                h = t["hrp"]
                if isinstance(h, (list, tuple)) and len(h) == 2:
                    out["hrp"] = [_f(h[0], "心率 %", errs, 0.5, 1.2), _f(h[1], "心率 %", errs, 0.5, 1.2)]
        return out
    mode = t.get("mode", "pct")
    if mode not in MODES:
        errs.append(f"目標填法不對：{mode!r}")
        return dict(OPEN)
    out = {"type": ty, "mode": mode}
    if mode == "zone":
        z = str(t.get("zone") or "")
        if z not in {r[0] for r in ZONES[ty]}:
            errs.append(f"沒有這個區間：{z!r}")
        out["zone"] = z
        return out
    rng = {("power", "pct"): (0.2, 3.0), ("hr", "pct"): (0.3, 1.3), ("pace", "pct"): (0.5, 2.5),
           ("power", "abs"): (20, 1500), ("hr", "abs"): (40, 230), ("pace", "abs"): (120, 1200)}[(ty, mode)]
    out["lo"] = _f(t.get("lo"), "目標下限", errs, *rng)
    out["hi"] = _f(t.get("hi"), "目標上限", errs, *rng)
    return out


def normalize(d) -> dict:
    """The stored form of an editor structure (ids filled in, numbers checked), or
    StepsError. Shape only — the training rules are `issues()`."""
    if isinstance(d, str):
        import json
        try:
            d = json.loads(d)
        except ValueError:
            raise StepsError(["結構不是 JSON"])
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise StepsError(["結構要有 items"])
    errs: list[str] = []
    seen: set = set()
    ids = _Ids("n")
    count = [0]

    def item(x, depth: int) -> Optional[dict]:
        if not isinstance(x, dict):
            errs.append("步驟格式不對")
            return None
        count[0] += 1
        iid = str(x.get("id") or "")[:16]
        if not iid or iid in seen:
            iid = ids()
            while iid in seen:
                iid = ids()
        seen.add(iid)
        note = str(x.get("note") or "").strip()[:MAX_NOTE]
        k = x.get("kind")
        if k == "repeat":
            if depth >= MAX_DEPTH:
                errs.append("重複最多兩層")
                return None
            try:
                times = int(x.get("times"))
            except (TypeError, ValueError):
                times = 0
            if not 1 <= times <= MAX_TIMES:
                errs.append(f"重複次數要在 1–{MAX_TIMES}")
                times = max(1, min(MAX_TIMES, times or 1))
            kids = [y for y in (item(c, depth + 1) for c in x.get("items") or []) if y]
            if not kids:
                errs.append("重複區塊裡沒有步驟")
            return {"id": iid, "kind": "repeat", "times": times, "last_rest": x.get("last_rest", True) is not False,
                    "note": note, "items": kids}
        if k not in KINDS:
            errs.append(f"步驟類型不對：{k!r}")
            return None
        dur = x.get("dur") or {}
        dt_ = dur.get("type") if isinstance(dur, dict) else None
        if dt_ not in DUR_TYPES:
            errs.append("時長類型要是 時間／距離／按圈")
            dur = {"type": "open"}
        elif dt_ == "time":
            v = _f(dur.get("value"), "時間", errs, 5, 6 * 3600)
            dur = {"type": "time", "value": int(round(v))} if v else {"type": "open"}
        elif dt_ == "distance":
            v = _f(dur.get("value"), "距離", errs, 50, 100000)
            dur = {"type": "distance", "value": int(round(v))} if v else {"type": "open"}
        else:
            est = dur.get("est") if isinstance(dur, dict) else None
            v = _f(est, "按圈的預估時間", errs, 5, 6 * 3600) if est else None
            dur = {"type": "open", "est": int(round(v))} if v else {"type": "open"}
        return {"id": iid, "kind": k, "dur": dur, "target": _norm_target(x.get("target"), errs), "note": note}

    items = [y for y in (item(x, 0) for x in d["items"]) if y]
    if not items:
        errs.append("至少要有一個步驟")
    if count[0] > MAX_ITEMS:
        errs.append(f"步驟太多（> {MAX_ITEMS}）")
    if errs:
        raise StepsError(list(dict.fromkeys(errs)))
    origin = str(d.get("origin") or "user")
    if not (origin in ("derived", "user") or origin.startswith("template:")):
        origin = "user"
    return {"v": V, "origin": origin[:40], "items": items}


# ---------------------------------------------------------------------------
# resolving one step's target
# ---------------------------------------------------------------------------

def mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_dur(d: dict) -> str:
    if d.get("type") == "time":
        return IL.fmt_s(d["value"])
    if d.get("type") == "distance":
        m = d["value"]
        return f"{m / 1000:g} km" if m >= 1000 else f"{m} m"
    return "按圈結束"


def pzone(f: float) -> str:
    for z, lo, hi in POWER_ZONES:
        if lo <= f < hi:
            return f"Z{z}"
    return "Z7" if f >= 1.5 else "Z1A"


def level(f: Optional[float]) -> int:
    """Chart colour step 1–5 (the ramp's steps; the legend lists the bands)."""
    if f is None:
        return 0
    return 1 if f < .75 else 2 if f < .88 else 3 if f < 1.01 else 4 if f < 1.06 else 5


_HR_P = [(.70, .62), (.85, .75), (.90, .82), (.95, .92), (1.00, 1.00), (1.03, 1.06), (1.06, 1.12)]


def hr_to_p(f: float) -> float:
    """Friel % LTHR → about % CP (推估: only the chart height and the TSS estimate)."""
    if f <= _HR_P[0][0]:
        return _HR_P[0][1]
    for (a0, b0), (a1, b1) in zip(_HR_P, _HR_P[1:]):
        if f <= a1:
            return b0 + (f - a0) / (a1 - a0) * (b1 - b0)
    return _HR_P[-1][1]


def _zone_of(ty: str, zid: str) -> tuple:
    r = next((r for r in ZONES[ty] if r[0] == zid), None)
    return (r[1], r[2]) if r else (None, None)


@dataclass
class Resolved:
    type: str                       # power | hr | pace | none
    lo: Optional[float] = None      # absolute (W, bpm, s/km — lo = faster for pace)
    hi: Optional[float] = None
    frac: Optional[float] = None    # ≈ % CP (chart height, TSS); None = no target
    text: str = ""
    sub: str = ""
    auto: bool = True               # the step follows 目標用 (not overridden)
    warn: str = ""
    err: str = ""
    intensity: Optional[tuple] = None    # what COROS gets: ("power" | "hr" | "pace", lo, hi)

    def as_dict(self) -> dict:
        return {"type": self.type, "lo": self.lo, "hi": self.hi, "frac": self.frac, "text": self.text,
                "sub": self.sub, "auto": self.auto, "warn": self.warn, "err": self.err,
                "level": level(self.frac), "label": TYPE_LABEL.get(self.type, self.type)}


def _from_int(it: Optional[tuple], c: Ctx, auto: bool = True, warn: str = "") -> Resolved:
    if not it:
        return Resolved("none", text="不設目標", auto=auto, warn=warn)
    typ, lo, hi = it
    if typ == "power":
        f = (lo + hi) / 2 / c.cp if c.cp else None
        sub = f"{lo / c.cp * 100:.0f}–{hi / c.cp * 100:.0f}% CP · {pzone(f)}" if c.cp else ""
        return Resolved("power", lo, hi, f, f"{lo:.0f}–{hi:.0f} W", sub, auto, warn, intensity=it)
    f = hr_to_p((lo + hi) / 2 / c.lthr) if c.lthr else 0.7
    sub = ("≤ AeT" if c.aet and abs(hi - c.aet) < 1 else
           f"{lo / c.lthr * 100:.0f}–{hi / c.lthr * 100:.0f}% LTHR" if c.lthr else "")
    return Resolved("hr", lo, hi, f, f"{lo:.0f}–{hi:.0f} bpm", sub, auto, warn, intensity=it)


def resolve(st: dict, c: Ctx) -> Resolved:
    tg = st.get("target") or OPEN
    ty = tg.get("type", "auto")
    if ty == "none":
        return Resolved("none", text="不設目標", auto=False)
    if ty == "auto":
        it = tg.get("intent", "open")
        if it == "open":
            return Resolved("none", text="不設目標")
        if it == "easy":
            if tg.get("plo") is not None and c.basis == "power":
                p = _power(c, tg["plo"], tg["phi"])
                if p:
                    return _from_int(p, c)
                return _from_int(easy_hr(c), c, warn="沒有 CP：改用心率")
            if tg.get("plo") is not None and c.basis == "none":
                return Resolved("none", text="不設目標")
            e = easy_hr(c)
            return _from_int(e, c, warn="" if e else "沒有 AeT／LTHR：不設目標")
        # band
        if c.basis == "none":
            return Resolved("none", text="不設目標")
        if c.basis == "hr":
            h = _work_hr(c, tg)
            if h:
                return _from_int(h, c)
            p = _power(c, tg["lo"], tg["hi"])
            return _from_int(p, c, warn="沒有 LTHR：改用功率" if p else "沒有 LTHR／CP：不設目標")
        p = _power(c, tg["lo"], tg["hi"])
        if p:
            return _from_int(p, c)
        h = _work_hr(c, tg)
        return _from_int(h, c, warn="沒有 CP：改用心率" if h else "沒有 CP／LTHR：不設目標")
    mode = tg.get("mode", "pct")
    lo, hi = tg.get("lo"), tg.get("hi")
    if ty == "power":
        if mode == "zone":
            lo, hi = _zone_of("power", tg.get("zone"))
        if mode != "abs":
            if not c.cp:
                return Resolved("none", text="不設目標", auto=False, err="選了功率卻沒有 CP")
            lo, hi = lo * c.cp, hi * c.cp
        r = _from_int(("power", round(lo), round(hi)), c, auto=False)
    elif ty == "hr":
        if mode == "zone" and tg.get("zone") == "aet":
            e = easy_hr(c)
            if not e:
                return Resolved("none", text="不設目標", auto=False, err="選了心率卻沒有 AeT／LTHR")
            r = _from_int(e, c, auto=False)
        else:
            if mode == "zone":
                lo, hi = _zone_of("hr", tg.get("zone"))
            if mode != "abs":
                if not c.lthr:
                    return Resolved("none", text="不設目標", auto=False, err="選了心率卻沒有 LTHR")
                lo, hi = lo * c.lthr, hi * c.lthr
            r = _from_int(("hr", round(lo), round(hi)), c, auto=False)
    else:                                                    # pace
        if mode == "zone":
            lo, hi = _zone_of("pace", tg.get("zone"))
        if mode != "abs":
            if not c.tpace:
                return Resolved("none", text="不設目標", auto=False, err="選了配速卻沒有閾值配速")
            lo, hi = lo * c.tpace, hi * c.tpace
        a, b = min(lo, hi), max(lo, hi)
        f = 1.0 / ((a + b) / 2 / c.tpace) if c.tpace else 0.8
        sub = f"{a / c.tpace * 100:.0f}–{b / c.tpace * 100:.0f}% 閾值配速（推估）" if c.tpace else ""
        # COROS intensityType 3, s/km (verified 2026-10-02): lo = the faster bound
        r = Resolved("pace", a, b, f, f"{mmss(a)}–{mmss(b)} /km", sub, False,
                     intensity=("pace", round(a), round(b)))
    if r.lo is not None and r.hi is not None and r.lo > r.hi and r.type != "pace":
        r.err = "下限比上限高"
    if r.type == "power" and c.cp and (r.lo < 0.4 * c.cp or r.hi > 2.0 * c.cp):
        r.err = r.err or "功率不在 40–200% CP（推估的合理範圍）"
    if r.type == "hr" and c.lthr and r.hi > 1.1 * c.lthr:
        r.err = r.err or "心率超過 110% LTHR（推估的合理範圍）"
    return r


# ---------------------------------------------------------------------------
# flatten, totals, TSS
# ---------------------------------------------------------------------------

def _iter_rep(it: dict) -> list:
    """The items of each pass through a repeat (the last pass without its trailing
    rests when last_rest is false)."""
    passes = []
    for i in range(it["times"]):
        kids = list(it["items"])
        if i == it["times"] - 1 and not it.get("last_rest", True):
            while kids and kids[-1].get("kind") == "rest":
                kids.pop()
        passes.append(kids)
    return passes


def flat(items: list, ctx=None) -> list[dict]:
    """Every step in the order it is run: {"st", "rep": [(repeat id, pass, times), …]}."""
    out = []
    for it in items:
        if it.get("kind") == "repeat":
            for i, kids in enumerate(_iter_rep(it)):
                out += flat(kids, (ctx or []) + [(it["id"], i, it["times"])])
        else:
            out.append({"st": it, "rep": ctx or []})
    return out


EASY_F = 0.78        # ≈ % CP of an easy run (Palladino EZ ≤ 80 % CP; 推估 anchor of v_easy)
WALK_KMH = 5.0       # a walk / rest step with no target (推估)


def speed_kmh(f: Optional[float], c: Ctx) -> tuple[float, str]:
    """(km/h on flat road at ≈ f × CP, how). Two anchors from the athlete's own data:
    easy speed (equivalence: median easy road runs ≤ AeT) at EASY_F, threshold pace
    (thresholds.estimate_tpace) at 100 % CP; linear between them (running power is about
    proportional to flat speed — Stryd), one anchor scales by f, none 6:00/km. 推估."""
    v_e = c.v_easy
    v_t = 3600.0 / c.tpace if c.tpace else None
    if f is None:
        f = EASY_F
    f = max(0.45, min(1.4, f))
    if v_e and v_t and v_t > v_e:
        v = v_e + (f - EASY_F) / (1.0 - EASY_F) * (v_t - v_e)
        how = "easy+tpace"
    elif v_e:
        v = v_e * f / EASY_F
        how = "easy"
    elif v_t:
        v = v_t * f
        how = "tpace"
    else:
        return 3600.0 / DIST_PACE_DEFAULT, "default"
    return max(v, 0.55 * (v_e or v_t)), how


def _secs(st: dict, r: Resolved, c: Ctx) -> tuple[float, bool]:
    """(seconds, estimated) of one step. Time as is; a lap-button step its protocol's
    `est` (else 0); distance by its pace target, else the athlete's speed at the step's
    intensity (speed_kmh); on trail the effort distance km × (1 + climb/100) at the
    athlete's trail EP speed scaled the same way — all 推估."""
    d = st["dur"]
    if d["type"] == "time":
        return float(d["value"]), False
    if d["type"] == "distance":
        km = d["value"] / 1000.0
        if r.type == "pace" and r.lo:
            return km * (r.lo + r.hi) / 2, True
        if r.frac is None and st["kind"] == "rest":
            return km / WALK_KMH * 3600.0, True
        f = r.frac if r.frac is not None else NONE_IF.get(st["kind"], EASY_F)
        v, _how = speed_kmh(f, c)
        if c.terrain == "trail":
            ep = km * (1.0 + c.climb_per_km / 100.0)
            if c.ep_kmh and c.v_easy:
                return ep / (c.ep_kmh * v / c.v_easy) * 3600.0, True
            return ep / v * 3600.0, True
        return km / v * 3600.0, True
    if d.get("est"):
        return float(d["est"]), True
    return 0.0, False


def estimate_note(steps: dict, c: Ctx) -> str:
    """The ? text of an estimated total: how the distance / lap-button steps were timed."""
    rows = [row["st"] for row in flat(steps["items"])]
    dist = any(s["dur"]["type"] == "distance" for s in rows)
    lap = any(s["dur"]["type"] == "open" and s["dur"].get("est") for s in rows)
    parts = []
    if dist:
        _v, how = speed_kmh(EASY_F, c)
        src = {"easy+tpace": f"你的輕鬆路跑速度 {c.v_easy:.1f} km/h（{c.v_easy_src or '近期紀錄'}）和閾值配速 {mmss(c.tpace or 0)}/km 之間，依每段的目標強度內插" if c.v_easy and c.tpace else "",
               "easy": f"你的輕鬆路跑速度 {(c.v_easy or 0):.1f} km/h 依目標強度等比例放大" ,
               "tpace": f"你的閾值配速 {mmss(c.tpace or 0)}/km 依目標強度換算",
               "default": "沒有你的速度資料，先用 6:00/km"}[how]
        parts.append("距離段：" + src)
        if c.terrain == "trail":
            parts.append(f"越野：努力距離 EP = km × (1 + 爬升 {c.climb_per_km:.0f} m/km ÷ 100)" +
                         (f"，用你的越野 EP 速度 {c.ep_kmh:.1f} km/h" if c.ep_kmh else "，你的越野紀錄不夠，先用路跑速度"))
    if lap:
        parts.append("按圈段：用課表原本寫的最短時間")
    return "；".join(parts) + "（推估）" if parts else ""


NONE_IF = {"rest": 0.55, "warm": 0.65, "cool": 0.65, "other": 0.8, "work": 1.05}    # 推估


def totals(steps: dict, c: Ctx) -> dict:
    """{"sec", "open", "est" (distance converted), "tss" (估), "hard_s" (≥ 88 % CP),
    "z5_s"}: Σ s × IF² × 100 / 3600, IF = the target's middle ÷ CP (HR via Friel →
    Palladino, 推估), no target by step kind (推估)."""
    sec = tss = hard = z5 = 0.0
    n_open, est = 0, False
    for row in flat(steps["items"]):
        st = row["st"]
        r = resolve(st, c)
        s, e = _secs(st, r, c)
        est = est or e
        if st["dur"]["type"] == "open" and not s:
            n_open += 1
            continue
        sec += s
        f = r.frac if r.frac is not None else NONE_IF.get(st["kind"], 0.7)
        tss += s * f * f * 100.0 / 3600.0
        if f >= 0.88:
            hard += s
        if r.frac is not None and r.frac >= Z5_FRAC and st["kind"] == "work":
            z5 += s
    return {"sec": round(sec), "open": n_open, "est": est, "tss": round(tss, 1), "hard_s": round(hard),
            "z5_s": round(z5), "est_note": estimate_note(steps, c) if est else ""}


# ---------------------------------------------------------------------------
# validation (the editor shows these live; PATCH refuses errors unless forced)
# ---------------------------------------------------------------------------

def _is_z5(st: dict, r: Resolved) -> bool:
    tg = st.get("target") or {}
    if tg.get("type") == "auto" and tg.get("intent") == "band":
        return (tg["lo"] + tg["hi"]) / 2 >= Z5_FRAC
    return r.type == "power" and r.frac is not None and r.frac >= Z5_FRAC


def _is_z3(st: dict, r: Resolved) -> bool:
    tg = st.get("target") or {}
    if tg.get("type") == "auto" and tg.get("intent") == "band":
        m = (tg["lo"] + tg["hi"]) / 2
    elif r.type == "power" and r.frac is not None:
        m = r.frac
    else:
        return False
    return IL.CLASS_RANGE["Z3sub"][0] <= m < IL.CLASS_RANGE["Z3near"][1]     # Palladino 3A–3B (88–101 %)


def issues(steps: dict, c: Ctx, cap: Optional[float] = None, cap_mode: str = "soft",
           rung: Optional[str] = None) -> list[dict]:
    """[{"level": err | warn | info, "text", "id"}]."""
    out: list[dict] = []
    seen = set()

    def add(lv, text, iid=None):
        k = (lv, text, iid)
        if k not in seen:
            seen.add(k)
            out.append({"level": lv, "text": text, "id": iid})

    rows = flat(steps["items"])
    for row in rows:
        st = row["st"]
        r = resolve(st, c)
        if r.err:
            add("err", r.err, st["id"])
        if r.warn:
            add("warn", r.warn, st["id"])
        if st["kind"] == "work" and st["dur"]["type"] == "time" and _is_z5(st, r) and st["dur"]["value"] < Z5_MIN_REP_S:
            add("err", f"5 區每趟至少 2 分鐘（徐國峰）：這段只有 {mmss(st['dur']['value'])}", st["id"])
        if st["kind"] == "work" and st["dur"]["type"] == "time" and _is_z3(st, r) and \
                st["dur"]["value"] < Z3_MIN_REP_S and _has_rest_after(rows, st):
            add("warn", f"3 區每趟至少 3 分鐘（Haugen 2022 的下緣）：這段只有 {mmss(st['dur']['value'])}", st["id"])
    # Z5 rests: ≤ the shortest rep and ≤ 3 min (Buchheit)
    z5w = [row["st"] for row in rows if row["st"]["kind"] == "work" and row["st"]["dur"]["type"] == "time"
           and _is_z5(row["st"], resolve(row["st"], c))]
    if z5w:
        short = min(x["dur"]["value"] for x in z5w)
        for i, row in enumerate(rows):
            st = row["st"]
            if st["kind"] != "rest" or st["dur"]["type"] != "time" or i == 0:
                continue
            prev = rows[i - 1]["st"]
            if prev in z5w and st["dur"]["value"] > min(short, Z5_MAX_REST_S) and \
                    not (i + 1 < len(rows) and rows[i + 1]["st"]["kind"] == "rest"):
                if i + 1 < len(rows) and rows[i + 1]["st"] in z5w:
                    add("warn", f"5 區休息 {mmss(st['dur']['value'])} 比一趟長或超過 3 分鐘（Buchheit 工休比）", st["id"])
    t = totals(steps, c)
    if cap:
        mins = t["sec"] / 60.0
        if mins > cap + 0.5:
            hard = cap_mode == "hard"
            add("err" if hard else "warn", f"總時間 {mins:.0f} 分超過這天上限 {cap:.0f} 分（課表偏好：{'硬上限' if hard else '軟上限，只提醒'}）")
    if t["open"]:
        add("info", f"{t['open']} 段「按圈結束」不算進總時間")
    for it in steps["items"]:
        if it.get("kind") == "repeat" and any(x.get("kind") == "repeat" for x in it["items"]):
            add("warn", "重複裡再放重複：COROS 只確定一層，推送時會攤平", it["id"])
    n = coros_count(steps, c)
    if n > COROS_MAX_STEPS:
        add("warn", f"推到手錶是 {n} 段，超過 {COROS_MAX_STEPS} 段：COROS 的上限未驗證")
    if rung:
        eq = equivalence(steps, rung, c)
        if eq:
            add("info", eq["text"])
    return out


def _has_rest_after(rows: list, st: dict) -> bool:
    for i, row in enumerate(rows):
        if row["st"] is st:
            return i + 1 < len(rows) and rows[i + 1]["st"]["kind"] == "rest"
    return False


# ---------------------------------------------------------------------------
# progression: the steps as a library Variant (interval_library.equivalent)
# ---------------------------------------------------------------------------

HR_CLASS_BAND = {"Z5": (1.06, 1.12), "Z4": (1.02, 1.05), "Z3near": (0.97, 1.00), "Z3sub": (0.90, 0.95)}   # 推估


def _work_band(st: dict, c: Optional[Ctx]) -> Optional[tuple]:
    """(lo, hi × CP, estimated) of a work step, or None (no target / open)."""
    tg = st.get("target") or {}
    ty = tg.get("type")
    if ty == "auto" and tg.get("intent") == "band":
        return tg["lo"], tg["hi"], False
    if ty == "power":
        if tg.get("mode") == "pct":
            return tg["lo"], tg["hi"], False
        if tg.get("mode") == "zone":
            lo, hi = _zone_of("power", tg.get("zone"))
            return (lo, hi, False) if lo is not None else None
        if c and c.cp:
            return tg["lo"] / c.cp, tg["hi"] / c.cp, False
        return None
    if ty == "hr":
        if tg.get("mode") == "pct":
            m = (tg["lo"] + tg["hi"]) / 2
        elif tg.get("mode") == "zone" and tg.get("zone") != "aet":
            lo, hi = _zone_of("hr", tg.get("zone"))
            m = (lo + hi) / 2
        elif tg.get("mode") == "abs" and c and c.lthr:
            m = (tg["lo"] + tg["hi"]) / 2 / c.lthr
        else:
            return None
        # Friel ↔ Palladino (workout_templates.HRP, 推估): 5b ≥ 103 % LTHR ↔ Zone 5, 5a ≥ 100 % ↔ Zone 4
        cls = "Z5" if m >= 1.03 else "Z4" if m >= 1.0 else "Z3near" if m >= 0.95 else "Z3sub" if m >= 0.88 else None
        if cls is None:
            return None
        return (*HR_CLASS_BAND[cls], True)
    return None


def variant_from_steps(steps: dict, rung: Optional[str] = None, c: Optional[Ctx] = None) -> Optional[IL.Variant]:
    """A temporary library Variant of the structure (reps, rep lengths, rests, band),
    so interval_library.equivalent judges a user-edited session like a swap. None
    without timed work steps that have an intensity. An HR-only structure gets the
    class's band (推估: src_kind)."""
    rows = flat(steps.get("items") or [])
    works, rests, bands, est = [], [], [], False
    last_work = None
    for i, row in enumerate(rows):
        st = row["st"]
        if st["kind"] == "work":
            b = _work_band(st, c)
            if b is None or st["dur"]["type"] != "time":
                continue
            works.append(st["dur"]["value"])
            bands.append(b[:2])
            est = est or b[2]
            last_work = i
        elif st["kind"] == "rest" and last_work is not None and st["dur"]["type"] == "time":
            nxt = next((r["st"] for r in rows[i + 1:] if r["st"]["kind"] in ("work", "cool")), None)
            if nxt is not None and nxt["kind"] == "work":
                rests.append(st["dur"]["value"])
    if not works:
        return None
    lo = sum(b[0] for b in bands) / len(bands)
    hi = sum(b[1] for b in bands) / len(bands)
    probe = IL.Variant("user", rung or "", "Z5", 1, works[0], 0, "none", lo, hi, "flat", False, "")
    cls = IL.class_of(probe)
    if cls is None:
        return None
    same = len(set(works)) == 1
    return IL.Variant("user", rung or "", cls, len(works), works[0], int(max(rests) if rests else 0),
                      "walk" if rests else "none", round(lo, 3), round(hi, 3), "flat", False,
                      "你自己的結構", "推估" if est else "peer", pattern=None if same else tuple(works))


def equivalence(steps: dict, rung: Optional[str], c: Optional[Ctx] = None) -> Optional[dict]:
    """{"ok", "why", "text", "variant"} of the structure against the rung's canonical."""
    if not rung or rung not in IL.LIBRARY:
        return None
    v = variant_from_steps(steps, rung, c)
    canon = IL.canonical(rung)
    if v is None:
        return {"ok": False, "why": ["找不到有強度的主課段"], "variant": None,
                "text": f"{IL.RUNG_NAME.get(rung, rung)}：找不到有功率／心率目標的主課段，這堂不算進階"}
    ok, why = IL.equivalent(v, canon)
    est = "（心率結構換算強度，推估）" if v.src_kind == "推估" else ""
    return {"ok": ok, "why": why, "variant": v,
            "text": f"和 {IL.RUNG_NAME.get(rung, rung)} 標準課表 {IL.structure(canon)} " +
                    ("等效：這堂算進階" if ok else "不等效：這堂不算進階（" + "；".join(why) + "）") + est}


# ---------------------------------------------------------------------------
# steps -> COROS steps (sync/coros_workouts.build_program)
# ---------------------------------------------------------------------------

@dataclass
class _Emit:
    c: Ctx
    n_work: int = 0
    lost: list = field(default_factory=list)


def _cw():
    from backend.sync import coros_workouts as CW
    return CW


EX = {"warm": 1, "work": 2, "other": 2, "rest": 4, "cool": 3}


def _name(st: dict, r: Resolved, em: _Emit, grouped: bool) -> str:
    if st.get("note"):
        return st["note"]
    tg = st.get("target") or {}
    if st["kind"] == "work" and tg.get("type") == "auto" and tg.get("intent") == "easy" and tg.get("plo") is not None:
        return "心率 ≤ AeT" if r.type == "hr" else "功率區間" if r.type == "power" else "照感覺"
    if st["kind"] == "work" and not grouped:
        em.n_work += 1
        return f"第 {em.n_work} 趟 {fmt_dur(st['dur'])}"
    return ""


def _one(st: dict, em: _Emit, grouped: bool):
    CW = _cw()
    r = resolve(st, em.c)
    d = st["dur"]
    secs = d["value"] if d["type"] == "time" else 0
    meters = d["value"] if d["type"] == "distance" else 0
    return CW.Step(EX[st["kind"]], int(secs), r.intensity, _name(st, r, em, grouped), int(meters))


def _plain(items: list) -> bool:
    return all(x.get("kind") != "repeat" for x in items)


def _emit(items: list, em: _Emit, out: list) -> None:
    CW = _cw()
    for it in items:
        if it.get("kind") != "repeat":
            out.append(_one(it, em, False))
        elif _plain(it["items"]) and it.get("last_rest", True):
            out.append(CW.Repeat(it["times"], [_one(x, em, True) for x in it["items"]], it.get("note") or "間歇"))
        else:
            for kids in _iter_rep(it):
                _emit(kids, em, out)


def steps_to_coros(steps: dict, c: Ctx) -> list:
    """coros_workouts Step / Repeat list. A repeat of plain steps that rests after
    every rep is a COROS group; one with no rest after the last rep, or holding a
    repeat, is unrolled (one lap per step)."""
    out: list = []
    _emit(steps["items"], _Emit(c), out)
    return out


def coros_count(steps: dict, c: Ctx) -> int:
    n = 0
    for x in steps_to_coros(steps, c):
        n += 1 + len(getattr(x, "steps", []) or [])
    return n


# ---------------------------------------------------------------------------
# text: the target summary, the 「推到手錶會長這樣」 preview
# ---------------------------------------------------------------------------

def steps_text(steps: dict, c: Ctx) -> str:
    """The calendar chip's target: the work steps' targets (else the first step's)."""
    parts = []
    rows = flat(steps["items"])
    work = [r["st"] for r in rows if r["st"]["kind"] == "work"] or [r["st"] for r in rows][:1]
    for st in work:
        r = resolve(st, c)
        if r.type == "none":
            continue
        t = f"{TYPE_LABEL[r.type]} {r.text}" + (f"（{r.sub.split(' · ')[0]}）" if r.sub else "")
        if t not in parts:
            parts.append(t)
    return " · ".join(parts[:3])[:200]


def structure_text(steps: dict) -> str:
    """「暖身 15 分 · 5×(2 分＋2 分) · 緩和 5 分」."""
    def one(x):
        if x.get("kind") == "repeat":
            return f"{x['times']}×(" + "＋".join(one(y) for y in x["items"]) + ")"
        return fmt_dur(x["dur"]) if x["kind"] in ("work", "rest", "other") else \
            f"{KIND_LABEL[x['kind']]} {fmt_dur(x['dur'])}"
    return " · ".join(one(x) for x in steps["items"])[:300]


EX_LABEL = {1: "暖身", 2: "訓練", 3: "緩和", 4: "休息"}


def _ex_line(ex: dict) -> dict:
    if ex["targetType"] == 2:
        dur = IL.fmt_s(ex["targetValue"])
    elif ex["targetType"] == 5:
        dur = f"{ex['targetValue'] / 100000:g} km"
    else:
        dur = "按圈結束"
    it = ex.get("intensityType")
    if it == 6:
        tgt = f"功率 {ex['intensityValue']}–{ex['intensityValueExtend']} W"
    elif it == 2:
        tgt = f"心率 {ex['intensityValue']}–{ex['intensityValueExtend']} bpm"
    elif it == 3:
        tgt = f"配速 {mmss(ex['intensityValue'])}–{mmss(ex['intensityValueExtend'])} /km"
    else:
        tgt = "不設目標"
    return {"kind": EX_LABEL.get(ex["exerciseType"], "訓練"), "dur": dur, "target": tgt, "name": ex.get("name") or ""}


def watch_preview(steps: dict, c: Ctx, name: str = "TRC", overview: str = "") -> dict:
    """What COROS gets: {"lines": [{kind, dur, target, name} | {group, sets, steps}],
    "n", "limits": [{key, text, hit}], "lost": [text]} — limits always listed
    (absolute watts, one target per step, no ramps), `hit` when this session meets one."""
    CW = _cw()
    program = CW.build_program(name, steps_to_coros(steps, c), CW.Thresholds(cp=c.cp, lthr=c.lthr, aet=c.aet), overview)
    groups = {}
    lines = []
    for ex in program["exercises"]:
        if ex.get("isGroup"):
            g = {"group": ex["name"], "sets": ex["sets"], "steps": []}
            groups[str(ex["id"])] = g
            lines.append(g)
        elif ex.get("groupId") not in (None, "0") and ex["groupId"] in groups:
            groups[ex["groupId"]]["steps"].append(_ex_line(ex))
        else:
            lines.append(_ex_line(ex))
    rows = flat(steps["items"])
    res = [(row["st"], resolve(row["st"], c)) for row in rows]
    has_power = any(r.type == "power" for _, r in res)
    unrolled = any(it.get("kind") == "repeat" and (not it.get("last_rest", True) or not _plain(it["items"]))
                   for it in steps["items"])
    dist = any(st["dur"]["type"] == "distance" for st, _ in res)
    limits = [
        {"key": "watts", "hit": has_power,
         "text": "只收絕對瓦數：跑步沒有 % CP，送的是換算後的 W；CP 更新後這堂會標成「已過期」，要重推"},
        {"key": "one", "hit": bool(c.hr_cap and has_power),
         "text": "每段只有一個目標：功率段的心率上限只寫在文字，手錶不會提醒"},
        {"key": "ramp", "hit": False, "text": "沒有漸進（ramp）步驟：漸進只寫在步驟名稱"},
    ]
    lost = []
    if unrolled:
        lost.append("「最後一趟不休息」或重複裡的重複：COROS 群組做不到，推送時攤平成一段一段")
    if dist:
        lost.append("距離段：COROS 欄位（公分）依第三方整理，這個 app 還沒實際送過（未驗證）")
    n = len(program["exercises"])
    if n > COROS_MAX_STEPS:
        lost.append(f"{n} 段超過 {COROS_MAX_STEPS} 段：COROS 的上限未驗證")
    return {"lines": lines, "n": n, "limits": limits, "lost": lost, "seconds": program["estimatedTime"]}


# ---------------------------------------------------------------------------
# the editor's view of a structure (POST /steps/check)
# ---------------------------------------------------------------------------

def view(steps: dict, c: Ctx, cap: Optional[float] = None, cap_mode: str = "soft",
         rung: Optional[str] = None) -> dict:
    """Everything the editor draws: per-step resolved targets (by id), the run order
    for the chart, totals, issues, the watch preview, the target summary."""
    by_id: dict = {}
    order = []
    for row in flat(steps["items"]):
        st = row["st"]
        r = resolve(st, c)
        s, e = _secs(st, r, c)
        by_id.setdefault(st["id"], r.as_dict())
        order.append({"id": st["id"], "kind": st["kind"], "sec": round(s), "est": e,
                      "open": st["dur"]["type"] == "open", "frac": r.frac, "level": level(r.frac),
                      "type": r.type, "rep": [{"id": a, "i": i, "n": n} for a, i, n in row["rep"]]})
    eq = equivalence(steps, rung, c) if rung else None
    return {"resolved": by_id, "order": order, "totals": totals(steps, c),
            "issues": issues(steps, c, cap, cap_mode, rung), "watch": watch_preview(steps, c),
            "summary": steps_text(steps, c), "structure": structure_text(steps),
            "equiv": {k: eq[k] for k in ("ok", "why", "text")} if eq else None}


def rescale_abs_power(steps: Optional[dict], old: float, new: float) -> Optional[dict]:
    """CP old → new: absolute watt targets × new / old (relative ones follow by
    themselves) — engine/plan_auto.rescale_sessions. None / unchanged → same object."""
    if not steps or not old or not new:
        return steps
    out = copy.deepcopy(steps)
    hit = [False]

    def walk(items):
        for it in items:
            if it.get("kind") == "repeat":
                walk(it["items"])
                continue
            tg = it.get("target") or {}
            if tg.get("type") == "power" and tg.get("mode") == "abs":
                tg["lo"] = round(tg["lo"] * new / old)
                tg["hi"] = round(tg["hi"] * new / old)
                hit[0] = True
    walk(out.get("items") or [])
    return out if hit[0] else steps


def template_steps(key: str, level: str = "std") -> Optional[dict]:
    v = IL.get(key)
    return from_variant(v, level) if v is not None else None


def templates(prefs=None) -> dict:
    """The editor's 插入範本 (static/workout_editor.js): {"cats": [{id, label, subs?}],
    "groups": [{"group", "cat", "sub", "title", "rows": [{key, label, title, src, url,
    src_kind, items (main set), full, equiv}]}]}. Each category: the published library
    (engine/workout_templates.py) first; 強度課 also the interval ladder's variants (by
    their band middle: 三區 / 四區 / 五區); 測試 also the app's CP protocols; strides /
    hill sprints."""
    from backend.engine import cp_protocols as CPP
    from backend.engine import workout_templates as WT
    lib = [(t, WT.row(t)) for t in WT.TEMPLATES]
    groups = []

    def g(cat, title, rows, sub=None):
        if rows:
            groups.append({"group": title, "title": title, "cat": cat, "sub": sub, "rows": rows})

    ids = _Ids("t")
    strides = {"key": "strides", "label": "快步跑 4×20 秒（間隔慢跑 40 秒）", "title": "快步跑 4×20 秒", "equiv": None,
               "src_kind": "coach", "src": "常見的輕鬆跑附加（暖身用）",
               "items": [rep(ids, 4, [step(ids, "other", 20, OPEN, "快步跑 20 秒"), step(ids, "rest", 40, OPEN, "慢跑")],
                             True, "快步跑 4×20 秒")]}
    hills = {"key": "hill_sprints", "label": "上坡衝刺 8×10 秒（走下來 60 秒）", "title": "上坡衝刺 8×10 秒", "equiv": None,
             "src_kind": "coach", "src": "常見的輕鬆跑附加",
             "items": [rep(ids, 8, [step(ids, "work", 10, OPEN, "10 秒上坡衝刺"), step(ids, "rest", 60, OPEN, "走下來")],
                           True, "衝刺 8×10 秒")]}
    g("easy", "有出處的課表", [r for t, r in lib if t.cat == "easy"])
    g("easy", "附加（只換主課時插在中間）", [strides])
    for sub in ("z3", "z4", "z5"):
        g("quality", "有出處的課表", [r for t, r in lib if t.cat == "quality" and r["sub"] == sub], sub)
        ladder = []
        for rung in IL.RUNG_ORDER + ("tp",):
            for v in IL.LIBRARY[rung]:
                if WT.sub_of(v.mid) != sub:
                    continue
                ok, _why = IL.equivalent(v)
                ladder.append({"key": v.key, "label": f"{IL.RUNG_NAME[rung]} {IL.title(v)} · {IL.rest_text(v)}" + ("（標準）" if v.canonical else ""),
                               "title": f"{IL.CLASS_LABEL[v.cls]} {IL.structure(v)}", "src": f"間歇庫 {IL.RUNG_NAME[rung]}（interval-prescription.md）",
                               "items": main_set(v), "equiv": ok, "src_kind": v.src_kind, "full": from_variant(v, "std")["items"],
                               "variant": True, "rung": v.rung})
        for v in IL.NON_EQUIV:
            if WT.sub_of(v.mid) == sub:
                ladder.append({"key": v.key, "label": f"{IL.title(v)}（每趟 < 2 分，不算進階）", "title": IL.title(v),
                               "src": "間歇庫（非同等）", "items": main_set(v), "equiv": False, "src_kind": v.src_kind,
                               "full": from_variant(v, "std")["items"], "variant": True, "rung": v.rung})
        g("quality", "間歇庫（進階階梯）", ladder, sub)
    g("test", "有出處的課表", [r for t, r in lib if t.cat == "test"])
    other = []
    for p in ("quick", "standard"):
        s = CPP.session_for(p)
        d = derive({"kind": "test", "title": s["title"], "detail": s["detail"], "target": s.get("target") or "",
                    "protocol": p}) if s else None
        if d:
            main = [x for x in d["items"] if x["kind"] in ("work", "rest")]
            other.append({"key": f"cp_{p}", "label": f"CP 測試：{s['title']}", "title": s["title"], "items": main,
                          "full": d["items"], "equiv": None, "src_kind": "peer", "src": "這個 app 的 CP 測試（cp-test-protocols.md）"})
    g("test", "這個 app 的 CP 測試", other)
    g("trail", "有出處的課表", [r for t, r in lib if t.cat == "trail"])
    g("trail", "附加", [hills])
    from backend.engine.workout_templates import CATS
    return {"cats": CATS, "groups": groups}


def zones_table(c: Ctx) -> dict:
    """The 區間 dropdowns with today's numbers: {"power": [{id, label, lo, hi, text}], "hr", "pace"}."""
    def rows(ty):
        out = []
        for z, lo, hi in ZONES[ty]:
            if ty == "hr" and z == "aet":
                e = easy_hr(c)
                out.append({"id": "aet", "label": "≤ AeT", "text": f"{e[1]}–{e[2]} bpm" if e else ""})
                continue
            base = {"power": c.cp, "hr": c.lthr, "pace": c.tpace}[ty]
            if ty == "pace":
                text = f"{mmss(lo * base)}–{mmss(hi * base)} /km" if base else ""
            else:
                text = f"{lo * base:.0f}–{hi * base:.0f} {'W' if ty == 'power' else 'bpm'}" if base else ""
            out.append({"id": z, "label": f"Z{z}", "lo": lo, "hi": hi, "text": text})
        return out
    return {k: rows(k) for k in ("power", "hr", "pace")}
