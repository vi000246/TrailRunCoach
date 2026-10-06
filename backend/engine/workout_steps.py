"""
A session's structure as steps (docs/plans/workout-editor.plan.md §3.1): warm-up,
work, rest, cool-down and other steps, ×N repeat blocks (one level of nesting),
each step with a duration and a target. The 課表 page's editor shows and edits
it; sync/coros_workouts pushes it.

    {"v": 1, "origin": "derived" | "template:<variant key>" | "user", ["tpl": <user template id>,
     "route": {"km", "z", "route_km", "gain_m", "name"} (that template's route profile, the
     session's own copy: user_templates.route_copy),]
     "items": [
       {"id": "a1", "kind": "warm", "dur": {"type": "time", "value": 600},
        "target": {"type": "auto", "intent": "easy"}, "note": "輕鬆跑暖身（跑到間歇地點）"},
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
dur       time (s) | distance (m) | open (ends with the lap button) | load (TSS, SP-38: main-set
          work steps only; COROS gets its TL end condition — engine/coros_tl.py, 推估 — and every
          other provider an estimated time = TSS ÷ (IF² × 100) h at the step's intensity);
          entered by feel (SP-57): {"type": "load", "value": TSS, "rpe": easy | moderate | hard |
          very_hard | max, "min": minutes} — normalize sets `value` from the RPE level × minutes
          (engine/rpe_load.py, Foster session RPE × the athlete's factor, 推估); the step is then
          timed by its minutes and converted at the IF those imply. Planning only: the load the
          PMC uses stays the watch's record
target    auto — what 「目標用：自動／心率／功率」 (engine/target_policy.py) gives the step:
             intent easy   HR ≤ AeT (with plo/phi: the power band when the session runs by power)
             intent band   lo/hi × CP on power; on HR the class's % LTHR (or the text's bpm)
             intent open   no target (drills, walk rests, all-out bouts)
          power | hr | pace — the user's override of one step:
             mode pct (× CP / × LTHR / × threshold pace), zone (Palladino / Friel id,
             hr also "aet"), abs (W / bpm / s per km — never rescaled by a threshold
             change, except watts on a CP change: engine/plan_auto.rescale_sessions)
          rpe — 技術地形／下坡 (SP-62): lo–hi on Borg CR-10 (1–10), optional `up` / `down`
             (m of climb / descent to cover). No HR / power target: COROS gets the step
             with no intensity and the RPE + climb in its name; the editor shows a
             reference HR only as text (rpe_hint, 推估)
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
from backend.engine.hr_profile import EASY_CAP
from backend.engine.hr_profile import ZONE_IDS as HR_MODEL_ZONES
from backend.i18n import N_, _
from backend.engine.zones import FRIEL_HR, FRIEL_PACE, PALLADINO_POWER_ZONES

V = 1
KINDS = ("warm", "work", "rest", "cool", "other")
KIND_LABEL = {"warm": "暖身", "work": "主課", "rest": "休息", "cool": "緩和", "other": "其他", "repeat": "重複"}
TYPES = ("auto", "power", "hr", "pace", "rpe", "none")
TYPE_LABEL = {"auto": "自動", "power": "功率", "hr": "心率", "pace": "配速", "rpe": "RPE", "none": "無"}
MODES = ("pct", "zone", "abs")
INTENTS = ("easy", "band", "open")
DUR_TYPES = ("time", "distance", "open", "load")
OPEN_LABEL = N_("直到按下計圈")      # the "open" end condition (lap button; renamed in SP-38)
LOAD_LABEL = N_("負荷")            # the "load" end condition (TSS here; COROS TL on the watch)
LOAD_RANGE = (1, 500)            # TSS of one load step
LOAD_KINDS = ("work",)           # 「負荷」 only on main-set steps (SP-38, the user 2026-10-04)
TL_RESEND_MIN = 3                # 推估: a refit moving a load step's TL by less keeps the TL sent (no 需更新)
MAX_TIMES = 99
MAX_DEPTH = 2                    # a repeat may hold one more level of repeats
MAX_ITEMS = 120                  # steps in the model (the editor's limit; COROS is checked apart)
COROS_MAX_STEPS = 50             # Garmin's documented limit; COROS: 未驗證 (plan §3.4)
MAX_NOTE = 60
OPEN_CHART_S = 90                # the chart width of a lap-button step
ROUTE_MAX = 400                  # points of a session's route profile copy (user_templates.PROFILE_OUT)
DIST_PACE_DEFAULT = 360.0        # s/km for a distance step with no pace at all (推估)

# Zone tables for the editor's 區間 choice: (id, lo, hi) fractions; open ends closed (推估)
POWER_ZONES = [(z, lo, hi if hi is not None else 1.8) for z, _n, lo, hi in PALLADINO_POWER_ZONES]
HR_ZONES = [("aet", None, None)] + [(z, lo if lo else 0.70, hi if hi is not None else 1.10)
                                    for z, _n, lo, hi in FRIEL_HR]
PACE_ZONES = [(z, lo if lo is not None else 0.85, hi if hi is not None else 1.45)
              for z, _n, lo, hi in FRIEL_PACE]         # × threshold pace; bigger = slower
ZONES = {"power": POWER_ZONES, "hr": HR_ZONES, "pace": PACE_ZONES}
# The HR 區間 choice follows 設定 → 課表心率區間 (SP-30): ids Z1–Z6 of that model, in bpm
# from Ctx.hrz (hr_profile.plan_hr_zones). The Friel ids above stay valid for steps saved
# before (resolved as % LTHR, as then) and are the only choice without a 課表心率區間.
HR_Z1_SPAN = 20                  # bpm under Z1's top: zone 1's open lower end (推估)
HR_Z6_OPEN = 1.10                # × LTHR: zone 6's open top without a max HR (as Friel 5c)

# Z5 / Z3 rules (interval_library §C2): 台灣教練 ≥ 2 min; Buchheit rest; Haugen ≥ 3 min
Z5_MIN_REP_S, Z3_MIN_REP_S, Z5_MAX_REST_S = IL.Z5_MIN_REP_S, IL.Z3_MIN_REP_S, IL.Z5_MAX_REST_S
Z5_FRAC = IL.CLASS_RANGE["Z5"][0]
HR_WORK = {"Z3sub": ("aet", 1.00), "Z3near": (0.95, 1.00), "Z4": (1.00, 1.03), "Z5": (1.00, 1.05)}    # = coros_workouts.HR_WORK

WARM_NAME = {"city": "輕鬆跑暖身", "river": "輕鬆跑→漸進", "drills": "動態伸展／drill"}
REST_NAME = {"walk": "走路或極慢跑", "jog": "慢跑恢復", "jog_down": "慢跑／走下坡", "none": "恢復"}

# RPE targets (技術地形／下坡, SP-62): Borg CR-10 as Foster's session RPE uses it (Foster et al.
# 2001, J Strength Cond Res 15:109–115) — 3 中等, 5 吃力, 7 很累, 10 極限. The ≈ % CP of each value
# only sizes the chart bar and the TSS estimate (推估: the load itself is always the watch's
# record, never corrected by RPE — the user, 2026-10-04).
RPE_MIN, RPE_MAX = 1, 10
RPE_WORD = {1: "很輕鬆", 2: "輕鬆", 3: "中等", 4: "有點吃力", 5: "吃力", 6: "吃力", 7: "很累", 8: "很累", 9: "很累",
            10: "極限"}
RPE_FRAC = {1: 0.55, 2: 0.62, 3: 0.70, 4: 0.76, 5: 0.82, 6: 0.88, 7: 0.94, 8: 1.00, 9: 1.05, 10: 1.10}   # 推估
RPE_EASY_MAX = 4                 # ≤ 4: below LT1 (Seiler's zone 1 by session RPE, 推估)
RPE_HARD_MIN = 7                 # ≥ 7 很累: a hard session (workout_templates.technical_role)
MAX_CLIMB_M = 5000
RPE_LIMIT = "RPE、爬升／下降手錶沒有這種目標：這些段不設目標，RPE 和爬升寫在步驟名稱"


NO_TPACE = N_("沒有閾值配速：這段推到手錶不會有配速目標")


def no_tpace_text() -> str:
    return _(NO_TPACE)


def needs_tpace(items) -> bool:
    """A step whose pace target is % / zone of threshold pace (Daniels / Canova / Billat
    templates); absolute s/km steps don't need one. `items`: a steps doc or its item list."""
    items = items.get("items") if isinstance(items, dict) else items
    for it in items or []:
        if it.get("kind") == "repeat":
            if needs_tpace(it.get("items")):
                return True
            continue
        tg = it.get("target") or {}
        if tg.get("type") == "pace" and tg.get("mode", "pct") in ("pct", "zone"):
            return True
    return False


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
    # 課表心率區間 (engine/hr_profile.plan_hr_zones; the plan thresholds' "hr_model"): the
    # automatic easy / interval HR targets and the editor's HR 區間 choice (hr_model_zones)
    hrz: Optional[dict] = None
    # a walking session (target_policy.is_walk, SP-115): its uphill cap (hr_profile.walk_cap, the
    # plan thresholds' "walk_cap") replaces the easy-run cap of its 「輕鬆」 steps
    walk: Optional[dict] = None
    # the push target (sync/workout_targets, setting plan.push.provider): its end conditions
    # (empty = not checked) and name, for the 「負荷」 issue on providers without one
    end_conditions: tuple = ()
    provider_label: str = ""
    tl: Optional[object] = None             # engine/coros_tl.Model; None = the stored one
    # the TL each 「負荷」 step was last pushed with {(tss, basis, if): tl} (sync/coros_workouts
    # from engine/coros_tl's closed-loop record): sent_tl keeps it while a refit moves it < TL_RESEND_MIN
    sent_tl: Optional[dict] = None

    rpe: Optional[object] = None            # engine/rpe_load.Model; None = the stored one (SP-57)

    def tl_model(self):
        from backend.engine import coros_tl
        return self.tl if self.tl is not None else coros_tl.current()

    def rpe_model(self):
        from backend.engine import rpe_load
        return self.rpe if self.rpe is not None else rpe_load.current()

    @classmethod
    def of(cls, th: Optional[dict], basis: Optional[str] = None, hr_cap: bool = False,
           speeds: Optional[dict] = None, walk: bool = False) -> "Ctx":
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
                   terrain=ter, climb_per_km=max(0.0, f("climb_per_km", sp) or 0.0) if ter == "trail" else 0.0,
                   hrz=th.get("hr_model") if isinstance(th.get("hr_model"), dict) else None,
                   walk=th.get("walk_cap") if walk and isinstance(th.get("walk_cap"), dict) else None)


def session_ctx(s: dict, th: Optional[dict], prefs=None) -> Ctx:
    """The Ctx of a session: its basis from engine/target_policy (the session's own
    目標用, 課表偏好, else 自動 by session type; falls back without CP / LTHR)."""
    from backend.engine import target_policy as TP
    pol = TP.target_policy(s, prefs, th or {})
    b = s.get("basis") if s.get("basis") in ("hr", "power", "none") else pol["basis"]
    if b == "power" and th and not (th or {}).get("cp"):
        b = pol["basis"]
    return Ctx.of(th, b, bool(pol.get("hr_cap")), walk=pol["type"] == "walk")


def easy_hr(c: Ctx) -> Optional[tuple]:
    """HR ≤ the easy-run cap (coros_workouts.easy_hr; with a 課表心率區間 its Z2 band); a
    walking session's cap is its uphill cap (hr_profile.walk_band)."""
    from backend.engine.hr_profile import walk_band
    if c.hrz and c.hrz.get("easy"):
        lo, hi = c.hrz["easy"]
        return walk_band(c.walk, ("hr", round(lo), round(hi)))
    hi = c.aet or (0.89 * c.lthr if c.lthr else None)
    if not hi:
        return walk_band(c.walk, None)
    lo = 0.75 * c.lthr if c.lthr else hi - 25
    lo = min(lo, hi - 10)
    return walk_band(c.walk, ("hr", round(lo), round(hi)))


def hr_model_zones(c: Optional[Ctx]) -> Optional[list]:
    """The 課表心率區間's zones as [(id, name, lo, hi)] bpm with the open ends closed
    (Z1 from HR_Z1_SPAN under its top; Z6 up to the max HR, else HR_Z6_OPEN × LTHR), or
    None without one. Same edges as the HR-zone charts of that model (zones.zone_table)."""
    rows = ((c.hrz or {}).get("rows") if c else None) or []
    if len(rows) != len(HR_MODEL_ZONES):
        return None
    out = []
    for r in rows:
        lo, hi = r.get("lo"), r.get("hi")
        if hi is not None and not lo:
            lo = hi - HR_Z1_SPAN
        if hi is None:
            top = c.hrz.get("mhr") or (HR_Z6_OPEN * c.lthr if c.lthr else None)
            hi = top if top and lo and top > lo else (lo + 10 if lo else None)
        if lo is None or hi is None:
            return None
        out.append((r["id"], r.get("name") or "", round(lo), round(hi)))
    return out


def _hr_model_zone(c: Optional[Ctx], zid: str) -> Optional[tuple]:
    return next(((lo, hi) for z, _n, lo, hi in hr_model_zones(c) or [] if z == zid), None)


def _power(c: Ctx, lo: float, hi: float) -> Optional[tuple]:
    return ("power", round(lo * c.cp), round(hi * c.cp)) if c.cp else None


def _work_hr(c: Ctx, tg: dict) -> Optional[tuple]:
    """HR of a band step (coros_workouts._work_hr): the text's bpm, else the class's % LTHR."""
    if tg.get("hr"):
        return ("hr", int(tg["hr"][0]), int(tg["hr"][1]))
    if tg.get("hrp") and c.lthr:                 # a template's own % LTHR (workout_templates)
        return ("hr", round(tg["hrp"][0] * c.lthr), round(tg["hrp"][1] * c.lthr))
    from backend.engine.hr_profile import work_band
    wb = work_band(c.hrz, tg.get("cls") or "Z3near")      # 課表心率區間: the class's COROS zone
    if wb:
        return ("hr", *wb)
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
CAP_NAME = "心率 ≤ " + EASY_CAP              # an easy step capped by HR (hr_profile: not 「AeT」)
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
    if proto == "xu90":
        # SP-274: pace (E pace ± 3 %) or power (75–80 % of a tested CP) from the stored target,
        # else no target (the talk test) — no HR cap on the main block; the warm-up stays easy
        from backend.engine.aet_test import xu_main_name, xu_main_target
        tg = xu_main_target(s.get("target") or "")
        main_t = {"type": tg[0], "mode": "abs", "lo": tg[1], "hi": tg[2]} if tg else OPEN
        out = [step(ids, "warm", warm * 60, EASY),
               step(ids, "work", main * 60, main_t, xu_main_name(s.get("target") or ""))]
    elif proto == "friel":
        out = [step(ids, "warm", warm * 60, EASY), step(ids, "work", main * 60, EASY, "AeT 心率附近穩定跑")]
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


# 主要訓練項目 = 路跑 (engine/overview.road_long_session): 「長跑＋馬拉松配速 N 分」 = easy, then N
# minutes at marathon pace, then MP_TAIL_S easy. The MP segment is a pace target (COROS intensityType 3):
# the race's goal pace ± MP_GOAL_BAND when the detail has 「目標配速 m:ss/km」, else threshold pace ×
# MP_PACE (推估: MP ≈ threshold pace × 1.06). Without a threshold pace the step falls back to an HR band
# (`hrp`, × LTHR: Pfitzinger's MP 79–88 % HRmax ÷ 0.9 → 88–98 % LTHR, top capped at 95 %, 推估) with the
# 沒有閾值配速 warning.
MP_PACE = (1.04, 1.08)
MP_HR = (0.88, 0.95)
MP_GOAL_BAND = 0.015                   # 推估: ± 1.5 % around the goal pace
MP_TAIL_S = 600


def mp_goal_pace(s: dict) -> Optional[float]:
    """The goal pace (s/km) written in a road long run's detail, or None."""
    m = re.search(r"目標配速\s*(\d+):(\d{2})\s*/km", s.get("detail") or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def mp_target(s: dict) -> dict:
    """The MP step's target: the goal pace (absolute s/km), else % threshold pace with the HR fallback."""
    g = mp_goal_pace(s)
    if g:
        return {"type": "pace", "mode": "abs", "lo": round(g * (1 - MP_GOAL_BAND)), "hi": round(g * (1 + MP_GOAL_BAND))}
    return {"type": "pace", "mode": "pct", "lo": MP_PACE[0], "hi": MP_PACE[1], "hrp": list(MP_HR)}


def mp_minutes(s: dict) -> Optional[int]:
    """The marathon-pace minutes of a road long run, from its title; None = an all-easy long run."""
    return _num(r"馬拉松配速\s*(\d+)\s*分", s.get("title") or "")


def strides_names(title: str, sprint: int, n: int) -> tuple[str, str, str]:
    """(work, recovery, repeat) step names: flat strides (路跑, 「加速跑」) or hill sprints."""
    if "加速跑" in (title or ""):
        return f"{sprint} 秒加速跑（平路）", "慢跑回來", f"加速跑 {n}×{sprint} 秒"
    return f"{sprint} 秒上坡衝刺", "走下來", f"衝刺 {n}×{sprint} 秒"


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
    mp = mp_minutes(s) if kind == "long" else None
    if mp and secs - mp * 60 - MP_TAIL_S >= 10 * 60:
        return doc([step(ids, "work", secs - mp * 60 - MP_TAIL_S, _easy(0.80, 0.88), "輕鬆"),
                    step(ids, "work", mp * 60, mp_target(s), "馬拉松配速"),
                    step(ids, "cool", MP_TAIL_S, _easy(0.75, 0.80), "輕鬆收操")])
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
                w_name, r_name, rep_name = strides_names(s.get("title") or "", sprint, n)
                return doc([step(ids, "work", base, _easy(0.75, 0.80), CAP_NAME),
                            rep(ids, n, [step(ids, "work", sprint, OPEN, w_name),
                                         step(ids, "rest", 60, OPEN, r_name)], True, rep_name)])
        return doc([step(ids, "work", secs, _easy(0.75, 0.80))])
    return None


# ---------------------------------------------------------------------------
# validation of the stored shape
# ---------------------------------------------------------------------------

def _f(x, name: str, errs: list, lo: float = None, hi: float = None) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        errs.append(_("{name} 要是數字", name=name))
        return None
    if v != v or (lo is not None and v < lo) or (hi is not None and v > hi):
        errs.append(_("{name} 超出範圍", name=name))
        return None
    return v


def _norm_target(t, errs: list) -> dict:
    if not isinstance(t, dict):
        return dict(OPEN)
    ty = t.get("type", "auto")
    if ty not in TYPES:
        errs.append(_("目標類型不對：{x}", x=repr(ty)))
        return dict(OPEN)
    if ty == "none":
        return {"type": "none"}
    if ty == "rpe":
        lo = _f(t.get("lo"), _("RPE 下限"), errs, RPE_MIN, RPE_MAX)
        hi = _f(t.get("hi", t.get("lo")), _("RPE 上限"), errs, RPE_MIN, RPE_MAX)
        out = {"type": "rpe", "lo": round(lo or RPE_MIN), "hi": round(hi or lo or RPE_MIN)}
        if out["lo"] > out["hi"]:
            errs.append(_("RPE 下限比上限高"))
        for k, name in (("up", _("爬升")), ("down", _("下降"))):
            if t.get(k) not in (None, "", 0):
                v = _f(t.get(k), name, errs, 0, MAX_CLIMB_M)
                if v:
                    out[k] = int(round(v))
        return out
    if ty == "auto":
        it = t.get("intent", "open")
        if it not in INTENTS:
            errs.append(_("自動目標的類型不對：{x}", x=repr(it)))
            return dict(OPEN)
        out = {"type": "auto", "intent": it}
        if it == "easy" and t.get("plo") is not None:
            out["plo"], out["phi"] = _f(t.get("plo"), _("功率下限"), errs, 0.3, 2.5), _f(t.get("phi"), _("功率上限"), errs, 0.3, 2.5)
        if it == "band":
            out["lo"], out["hi"] = _f(t.get("lo"), _("強度下限"), errs, 0.3, 2.5), _f(t.get("hi"), _("強度上限"), errs, 0.3, 2.5)
            out["cls"] = str(t.get("cls") or "")
            if t.get("hr"):
                h = t["hr"]
                if isinstance(h, (list, tuple)) and len(h) == 2:
                    out["hr"] = [int(_f(h[0], _("心率"), errs, 40, 230) or 0), int(_f(h[1], _("心率"), errs, 40, 230) or 0)]
            if t.get("hrp"):
                h = t["hrp"]
                if isinstance(h, (list, tuple)) and len(h) == 2:
                    out["hrp"] = [_f(h[0], _("心率 %"), errs, 0.5, 1.2), _f(h[1], _("心率 %"), errs, 0.5, 1.2)]
        return out
    mode = t.get("mode", "pct")
    if mode not in MODES:
        errs.append(_("目標填法不對：{x}", x=repr(mode)))
        return dict(OPEN)
    out = {"type": ty, "mode": mode}
    if mode == "zone":
        z = str(t.get("zone") or "")
        if z not in {r[0] for r in ZONES[ty]} | (set(HR_MODEL_ZONES) if ty == "hr" else set()):
            errs.append(_("沒有這個區間：{x}", x=repr(z)))
        out["zone"] = z
        return out
    rng = {("power", "pct"): (0.2, 3.0), ("hr", "pct"): (0.3, 1.3), ("pace", "pct"): (0.5, 2.5),
           ("power", "abs"): (20, 1500), ("hr", "abs"): (40, 230), ("pace", "abs"): (120, 1200)}[(ty, mode)]
    out["lo"] = _f(t.get("lo"), _("目標下限"), errs, *rng)
    out["hi"] = _f(t.get("hi"), _("目標上限"), errs, *rng)
    if ty == "pace" and mode == "pct" and isinstance(t.get("hrp"), (list, tuple)) and len(t["hrp"]) == 2:
        # the HR band (× LTHR) used when there is no threshold pace (the MP segment, mp_target)
        out["hrp"] = [_f(t["hrp"][0], _("心率 %"), errs, 0.5, 1.2), _f(t["hrp"][1], _("心率 %"), errs, 0.5, 1.2)]
    return out


def normalize(d, rpe_model=None) -> dict:
    """The stored form of an editor structure (ids filled in, numbers checked), or
    StepsError. Shape only — the training rules are `issues()`. `rpe_model`: the
    engine/rpe_load.Model for 「負荷」 steps entered by RPE (None = the stored one)."""
    if isinstance(d, str):
        import json
        try:
            d = json.loads(d)
        except ValueError:
            raise StepsError([_("結構不是 JSON")])
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise StepsError([_("結構要有 items")])
    errs: list[str] = []
    seen: set = set()
    ids = _Ids("n")
    count = [0]

    def item(x, depth: int) -> Optional[dict]:
        if not isinstance(x, dict):
            errs.append(_("步驟格式不對"))
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
                errs.append(_("重複最多兩層"))
                return None
            try:
                times = int(x.get("times"))
            except (TypeError, ValueError):
                times = 0
            if not 1 <= times <= MAX_TIMES:
                errs.append(_("重複次數要在 1–{n}", n=MAX_TIMES))
                times = max(1, min(MAX_TIMES, times or 1))
            kids = [y for y in (item(c, depth + 1) for c in x.get("items") or []) if y]
            if not kids:
                errs.append(_("重複區塊裡沒有步驟"))
            return {"id": iid, "kind": "repeat", "times": times, "last_rest": x.get("last_rest", True) is not False,
                    "note": note, "items": kids}
        if k not in KINDS:
            errs.append(_("步驟類型不對：{x}", x=repr(k)))
            return None
        dur = x.get("dur") or {}
        dt_ = dur.get("type") if isinstance(dur, dict) else None
        if dt_ not in DUR_TYPES:
            errs.append(_("時長類型要是 時間／距離／{a}／{b}", a=_(OPEN_LABEL), b=_(LOAD_LABEL)))
            dur = {"type": "open"}
        elif dt_ == "load":
            if k not in LOAD_KINDS:
                errs.append(_("「{x}」只能用在主課", x=_(LOAD_LABEL)))
            if dur.get("rpe") is not None:
                dur = _norm_rpe_load(dur, errs, rpe_model)
            else:
                v = _f(dur.get("value"), _("負荷（TSS）"), errs, *LOAD_RANGE)
                dur = {"type": "load", "value": round(v, 1)} if v else {"type": "open"}
        elif dt_ == "time":
            v = _f(dur.get("value"), _("時間"), errs, 5, 6 * 3600)
            dur = {"type": "time", "value": int(round(v))} if v else {"type": "open"}
        elif dt_ == "distance":
            v = _f(dur.get("value"), _("距離"), errs, 50, 100000)
            dur = {"type": "distance", "value": int(round(v))} if v else {"type": "open"}
        else:
            est = dur.get("est") if isinstance(dur, dict) else None
            v = _f(est, _("直到按下計圈的預估時間"), errs, 5, 6 * 3600) if est else None
            dur = {"type": "open", "est": int(round(v))} if v else {"type": "open"}
        return {"id": iid, "kind": k, "dur": dur, "target": _norm_target(x.get("target"), errs), "note": note}

    items = [y for y in (item(x, 0) for x in d["items"]) if y]
    if not items:
        errs.append(_("至少要有一個步驟"))
    if count[0] > MAX_ITEMS:
        errs.append(_("步驟太多（> {n}）", n=MAX_ITEMS))
    if errs:
        raise StepsError(list(dict.fromkeys(errs)))
    origin = str(d.get("origin") or "user")
    if not (origin in ("derived", "user") or origin.startswith("template:")):
        origin = "user"
    out = {"v": V, "origin": origin[:40], "items": items}
    # the user template it was made from (engine/user_templates.py: its route GPX on the chart)
    tpl = d.get("tpl")
    if isinstance(tpl, (int, str)) and not isinstance(tpl, bool) and str(tpl).isdigit() and 0 < int(tpl) < 10 ** 9:
        out["tpl"] = int(tpl)
    # the session's own copy of that template's route profile (user_templates.route_copy): the
    # chart keeps it when the template or its GPX is gone; anything malformed is dropped
    route = _norm_route(d.get("route"))
    if route:
        out["route"] = route
    return out


def _norm_rpe_load(dur: dict, errs: list, model=None) -> dict:
    """A 「負荷」 step entered by feel (SP-57): {"type": "load", "value": TSS, "rpe", "min"}, the
    TSS = the RPE level × minutes by the athlete's factor (engine/rpe_load.py, 推估)."""
    from backend.engine import rpe_load as RL
    lvl = dur.get("rpe")
    if lvl not in RL.CR10:
        errs.append(_("RPE 要是 {levels}", levels="／".join(_(l) for _k, l, _v in RL.LEVELS)))
        return {"type": "open"}
    m = _f(dur.get("min"), _("負荷（RPE）的分鐘"), errs, *RL.MIN_RANGE)
    if not m:
        return {"type": "open"}
    tss = (model or RL.current()).tss(lvl, m)
    if tss is None or tss > LOAD_RANGE[1]:
        errs.append(_("負荷（RPE）換算超過 {max} TSS：分鐘數太多", max=LOAD_RANGE[1]))
        return {"type": "open"}
    return {"type": "load", "value": max(float(LOAD_RANGE[0]), tss), "rpe": lvl, "min": int(round(m))}


def _norm_route(r) -> Optional[dict]:
    """A route profile copy {"km": [...], "z": [...], "route_km", "gain_m", "name"}: 2–ROUTE_MAX
    finite points, km not decreasing; None when it isn't one."""
    import math
    if not isinstance(r, dict):
        return None
    km, z = r.get("km"), r.get("z")
    if not isinstance(km, list) or not isinstance(z, list) or len(km) != len(z) or not 2 <= len(km) <= ROUTE_MAX:
        return None
    try:
        km = [round(float(x), 3) for x in km]
        z = [round(float(x), 1) for x in z]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in km + z) or any(b < a for a, b in zip(km, km[1:])) or km[-1] <= km[0]:
        return None
    out = {"km": km, "z": z}
    for k in ("route_km", "gain_m"):
        try:
            v = float(r.get(k))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and 0 <= v < 1e6:
            out[k] = round(v, 2)
    out["name"] = str(r.get("name") or "")[:200]
    return out


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
    if d.get("type") == "load":
        if d.get("rpe"):
            from backend.engine.rpe_load import LABEL
            return _("{label} {tss:g} TSS（{rpe} {min} 分）", label=_(LOAD_LABEL), tss=d["value"],
                     rpe=_(LABEL.get(d["rpe"], d["rpe"])), min=d.get("min"))
        return f"{_(LOAD_LABEL)} {d['value']:g} TSS"
    return _(OPEN_LABEL)


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
    need: str = ""                       # "tpace": a % / zone pace target with no threshold pace

    def as_dict(self) -> dict:
        return {"type": self.type, "lo": self.lo, "hi": self.hi, "frac": self.frac, "text": self.text,
                "sub": self.sub, "auto": self.auto, "warn": self.warn, "err": self.err, "need": self.need,
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
    sub = ("≤ " + EASY_CAP if c.aet and abs(hi - c.aet) < 1 else
           f"{lo / c.lthr * 100:.0f}–{hi / c.lthr * 100:.0f}% LTHR" if c.lthr else "")
    return Resolved("hr", lo, hi, f, f"{lo:.0f}–{hi:.0f} bpm", sub, auto, warn, intensity=it)


def rpe_frac(lo: float, hi: float) -> float:
    """≈ % CP of an RPE band (the chart and the TSS estimate only; 推估)."""
    a, b = (max(RPE_MIN, min(RPE_MAX, int(round(x)))) for x in (lo, hi))
    return (RPE_FRAC[a] + RPE_FRAC[b]) / 2.0


def rpe_text(lo, hi) -> str:
    return f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"


def climb_text(tg: dict) -> str:
    """「爬升 400 m · 下降 350 m」 of an RPE target (empty without either)."""
    return " · ".join(f"{name} {tg[k]} m" for k, name in (("up", "爬升"), ("down", "下降")) if tg.get(k))


def rpe_hint(lo: float, hi: float, c: Ctx) -> str:
    """The reference HR of an RPE band — text only, never a target (技術地形 HR often stays
    low: the limit is footing, not the heart). ≤ 4 = under the easy cap, 5–6 = between it and
    95 % LTHR, ≥ 7 = from 95 % LTHR up (Seiler's 3 zones by session RPE; 推估)."""
    m = (lo + hi) / 2.0
    if m <= RPE_EASY_MAX:
        cap = c.aet or (0.88 * c.lthr if c.lthr else None)
        txt = f"≤ {cap:.0f} bpm" if cap else ""
    elif m < RPE_HARD_MIN - 0.5:
        a, b = c.aet or (0.88 * c.lthr if c.lthr else None), (0.95 * c.lthr if c.lthr else None)
        txt = f"{a:.0f}–{b:.0f} bpm" if a and b else ""
    else:
        txt = f"≥ {0.95 * c.lthr:.0f} bpm" if c.lthr else ""
    return f"參考心率 {txt}（不當目標）" if txt else ""


def _rpe(tg: dict, c: Ctx) -> Resolved:
    lo, hi = tg.get("lo") or RPE_MIN, tg.get("hi") or tg.get("lo") or RPE_MIN
    a, b = RPE_WORD.get(int(round(lo)), ""), RPE_WORD.get(int(round(hi)), "")
    word = a if a == b else f"{a}～{b}"
    sub = " · ".join(x for x in (climb_text(tg), word, rpe_hint(lo, hi, c)) if x)
    return Resolved("rpe", lo, hi, rpe_frac(lo, hi), rpe_text(lo, hi), sub, auto=False,
                    err="RPE 下限比上限高" if lo > hi else "")


def resolve(st: dict, c: Ctx) -> Resolved:
    tg = st.get("target") or OPEN
    ty = tg.get("type", "auto")
    if ty == "none":
        return Resolved("none", text="不設目標", auto=False)
    if ty == "rpe":
        return _rpe(tg, c)
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
        elif mode == "zone" and tg.get("zone") in HR_MODEL_ZONES:
            b = _hr_model_zone(c, tg["zone"])
            if not b:
                return Resolved("none", text="不設目標", auto=False, err="選了心率區間卻沒有課表心率區間")
            r = _from_int(("hr", *b), c, auto=False)
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
                if tg.get("hrp") and c.lthr:
                    # the step's HR fallback (the MP segment): an HR band, still with the warning
                    r = _from_int(("hr", round(tg["hrp"][0] * c.lthr), round(tg["hrp"][1] * c.lthr)), c, auto=False,
                                  warn=no_tpace_text())
                    r.need = "tpace"
                    return r
                # a warning, not an error: the session can be saved and pushed, that step just
                # has no pace target on the watch (absolute s/km steps never need it)
                return Resolved("none", text=_("不設目標"), auto=False, warn=no_tpace_text(), need="tpace")
            lo, hi = lo * c.tpace, hi * c.tpace
        a, b = min(lo, hi), max(lo, hi)
        f = 1.0 / ((a + b) / 2 / c.tpace) if c.tpace else 0.8
        sub = f"{a / c.tpace * 100:.0f}–{b / c.tpace * 100:.0f}% 閾值配速（推估）" if c.tpace else ""
        # COROS intensityType 3, s/km (verified 2026-10-02): lo = the faster bound
        r = Resolved("pace", a, b, f, f"{mmss(a)}–{mmss(b)} /km", sub, False,
                     intensity=("pace", round(a), round(b)))
    if r.lo is not None and r.hi is not None and r.lo > r.hi and r.type != "pace":
        r.err = "下限比上限高"
    # no plausible-range check on HR or power: this branch is the user's own override
    # (auto targets returned above), so a hand-entered value is sent as entered (SP-33)
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
    if d["type"] == "load":
        if d.get("rpe") and d.get("min"):
            return float(d["min"]) * 60.0, True          # entered by feel: its minutes (SP-57)
        f = load_if(st, r)
        return d["value"] * 3600.0 / (f * f * 100.0), True
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


RPE_IF_RANGE = (0.4, 1.3)        # 推估: the IF an RPE-entered load step may imply


def load_if(st: dict, r: Resolved) -> float:
    """The IF a 「負荷」 step is timed and converted at: its target's ≈ % CP, else the kind's.
    Entered by feel (SP-57): the IF its TSS and minutes imply, √(TSS ÷ (100 × h))."""
    d = st["dur"]
    if d.get("type") == "load" and d.get("rpe") and d.get("min"):
        import math
        f = math.sqrt(float(d["value"]) / (100.0 * float(d["min"]) / 60.0))
        return round(max(RPE_IF_RANGE[0], min(RPE_IF_RANGE[1], f)), 4)
    return r.frac if r.frac else NONE_IF.get(st["kind"], 0.7)


def load_tl(st: dict, r: Resolved, c: Ctx) -> dict:
    """{"tl", "err", "group", "fitted"}: the COROS TL of a 「負荷」 step (engine/coros_tl.py, 推估)."""
    return c.tl_model().tl(st["dur"]["value"], "power" if r.type == "power" else "hr", load_if(st, r))


def sent_key(tss: float, basis: str, f: float) -> tuple:
    return float(tss), basis, round(float(f), 4)


def sent_tl(st: dict, r: Resolved, c: Ctx) -> int:
    """The TL a 「負荷」 step is pushed with: load_tl rounded, or the TL it was last pushed with
    (c.sent_tl, same planned TSS / basis / IF) while the refit moved it by < TL_RESEND_MIN — so a
    small refit doesn't mark the session 需更新 and re-push it (SP-38, owner 2026-10-04)."""
    new = max(1, round(load_tl(st, r, c)["tl"]))
    old = (c.sent_tl or {}).get(sent_key(st["dur"]["value"], "power" if r.type == "power" else "hr",
                                         load_if(st, r)))
    return int(old) if old is not None and abs(new - float(old)) < TL_RESEND_MIN else new


def load_records(steps: dict, c: Ctx) -> list[dict]:
    """[{i (run-order index), n (steps run), tss, tl, basis, if, f (the closed-loop factor in
    effect)}] of the 「負荷」 steps (engine/coros_tl.py closed loop)."""
    rows = flat(steps["items"])
    out = []
    m = c.tl_model()
    for i, row in enumerate(rows):
        st = row["st"]
        if st["dur"]["type"] == "load":
            r = resolve(st, c)
            out.append({"i": i, "n": len(rows), "tss": st["dur"]["value"],
                        "tl": sent_tl(st, r, c),
                        "basis": "power" if r.type == "power" else "hr", "if": round(load_if(st, r), 4),
                        "f": m.factor})
    return out


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
        parts.append("「直到按下計圈」段：用課表原本寫的最短時間")
    if any(s["dur"]["type"] == "load" and not s["dur"].get("rpe") for s in rows):
        parts.append(f"「{_(LOAD_LABEL)}」段：TSS ÷（該段強度 IF² × 100）換成時間")
    if any(s["dur"]["type"] == "load" and s["dur"].get("rpe") for s in rows):
        parts.append(_("「{label}」段用 RPE 填：用你填的分鐘數，TSS 由 RPE × 分鐘換算", label=_(LOAD_LABEL)))
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
        if st["dur"]["type"] == "load":
            f = load_if(st, r)                  # = the step's TSS back (typed or by RPE)
        else:
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
            add("err", _("5 區每趟至少 2 分鐘（台灣教練）：這段只有 {d}", d=mmss(st['dur']['value'])), st["id"])
        if st["kind"] == "work" and st["dur"]["type"] == "time" and _is_z3(st, r) and \
                st["dur"]["value"] < Z3_MIN_REP_S and _has_rest_after(rows, st):
            add("warn", _("3 區每趟至少 3 分鐘（Haugen 2022 的下緣）：這段只有 {d}", d=mmss(st['dur']['value'])), st["id"])
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
                    add("warn", _("5 區休息 {d} 比一趟長或超過 3 分鐘（Buchheit 工休比）", d=mmss(st['dur']['value'])), st["id"])
    t = totals(steps, c)
    if cap:
        mins = t["sec"] / 60.0
        if mins > cap + 0.5:
            hard = cap_mode == "hard"
            add("err" if hard else "warn", _("總時間 {m:.0f} 分超過這天上限 {cap:.0f} 分（課表偏好：{mode}）", m=mins, cap=cap,
                                              mode=_("硬上限") if hard else _("軟上限，只提醒")))
    if t["open"]:
        add("info", _("{n} 段「直到按下計圈」不算進總時間", n=t['open']))
    for row in rows:
        st = row["st"]
        if st["dur"]["type"] == "load" and c.end_conditions and "load" not in c.end_conditions:
            s_, _e = _secs(st, resolve(st, c), c)
            add("warn", _("{p}沒有「{x}」結束條件：推送時換成預估時間 {d}（推估）",
                          p=c.provider_label or _("這個平台"), x=_(LOAD_LABEL), d=mmss(s_)), st["id"])
    role = rpe_role(steps["items"])
    if role:
        add("info", (_("RPE 目標：心率、功率只當參考，負荷照手錶記錄算（不用 RPE 校正）；這堂依 RPE 算")
                     + (_("強度課（RPE ≥ 7：和其他強度課隔 48 小時、算進每週強度預算）") if role == "quality" else _("輕鬆課"))))
    for it in steps["items"]:
        if it.get("kind") == "repeat" and any(x.get("kind") == "repeat" for x in it["items"]):
            add("warn", _("重複裡再放重複：COROS 只確定一層，推送時會攤平"), it["id"])
    n = coros_count(steps, c)
    if n > COROS_MAX_STEPS:
        add("warn", _("推到手錶是 {n} 段，超過 {max} 段：COROS 的上限未驗證", n=n, max=COROS_MAX_STEPS))
    if rung:
        eq = equivalence(steps, rung, c)
        if eq:
            add("info", eq["text"])
    return out


def rpe_role(items: list) -> Optional[str]:
    """A structure whose work is set by RPE (技術地形／下坡, SP-62): "quality" when a work step's
    RPE reaches RPE_HARD_MIN (很累 / 極限 — a hard session: 48 h from the next, counted in the
    week's quality budget), else "easy" (the long-run slot, aerobic). None: no RPE work step."""
    his = [float((row["st"].get("target") or {}).get("hi") or 0) for row in flat(items or [])
           if row["st"].get("kind") in ("work", "other") and (row["st"].get("target") or {}).get("type") == "rpe"]
    if not his:
        return None
    return "quality" if max(his) >= RPE_HARD_MIN else "easy"


# 主課強度類型 (SP-84): the 範本 page's and 插入範本's second filter, after the category. The
# ids in display order; "auto" = a 「自動」 band whose type the session's 目標用 decides (no
# basis on the template); "load" = a 「負荷」 end condition (not a target, listed with them).
TARGET_TYPE_IDS = ("power_pct", "power_abs", "hr_pct", "hr_zone", "hr_abs", "pace", "rpe", "auto", "none", "load")
TARGET_TYPE_LABEL = {"power_pct": N_("% CP 功率區間"), "power_abs": N_("絕對功率"), "hr_pct": N_("% LTHR 心率"),
                     "hr_zone": N_("心率區間"), "hr_abs": N_("絕對心率"), "pace": N_("配速"), "rpe": "RPE",
                     "auto": N_("自動（依課表類型）"), "none": N_("不設目標"), "load": N_("負荷")}


def target_type_list() -> list[dict]:
    """[{id, label}] of every 主課強度類型, in display order (the UIs show the ones in use)."""
    return [{"id": k, "label": _(TARGET_TYPE_LABEL[k])} for k in TARGET_TYPE_IDS]


def _target_type(tg: Optional[dict], basis: Optional[str]) -> str:
    tg = tg or OPEN
    ty = tg.get("type", "auto")
    if ty in ("none", "rpe"):
        return ty
    if ty == "auto":
        it = tg.get("intent", "open")
        if it == "open":
            return "none"
        if it == "easy":
            # easy_hr unless a power band with 目標用 power (resolve)
            if tg.get("plo") is None or basis == "hr":
                return "hr_zone"
            return "power_pct" if basis == "power" else "auto"
        return {"hr": "hr_pct", "power": "power_pct"}.get(basis, "auto")
    if ty == "pace":
        return "pace"
    mode = tg.get("mode", "pct")
    if ty == "power":
        return "power_abs" if mode == "abs" else "power_pct"          # Palladino zones are % CP too
    return {"abs": "hr_abs", "zone": "hr_zone"}.get(mode, "hr_pct")


def target_types(items: list, basis: Optional[str] = None) -> list[str]:
    """The 主課強度類型 of a structure (TARGET_TYPE_IDS order): the targets of its main set —
    the work steps (inside repeats too), else the 「其他」 ones (strides), else every step — plus
    "load" when a main-set step ends on 「負荷」. Mixed main sets list each type. `basis`: the
    template's 目標用 (a library template's / a user template's target_basis), which turns a
    「自動」 band into % CP or % LTHR; without one it stays "auto"."""
    steps: list = []

    def walk(xs):
        for x in xs or []:
            if x.get("kind") == "repeat":
                walk(x.get("items"))
            else:
                steps.append(x)
    walk(items)
    main = [s for s in steps if s.get("kind") == "work"] or [s for s in steps if s.get("kind") == "other"] or steps
    got = {_target_type(s.get("target"), basis) for s in main}
    if any((s.get("dur") or {}).get("type") == "load" for s in main):
        got.add("load")
    return [k for k in TARGET_TYPE_IDS if k in got]


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
        elif tg.get("mode") == "zone" and tg.get("zone") in HR_MODEL_ZONES:
            b = _hr_model_zone(c, tg["zone"])
            if not (b and c.lthr):
                return None
            m = (b[0] + b[1]) / 2 / c.lthr
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


def load_work_s(tss: float, band: tuple) -> int:
    """A 「負荷」 work step's time for the ladder (SP-38, owner 2026-10-04): TSS ÷ (IF² × 100) h
    at IF = the band's middle (≈ % CP; HR-only steps: HR_CLASS_BAND) — the editor's _secs
    formula, but on the band so the callers without a Ctx (dose_history, interval_eval) get the
    same number. 推估."""
    f = (band[0] + band[1]) / 2.0
    return int(round(float(tss) * 3600.0 / (f * f * 100.0))) if f > 0 else 0


def variant_from_steps(steps: dict, rung: Optional[str] = None, c: Optional[Ctx] = None) -> Optional[IL.Variant]:
    """A temporary library Variant of the structure (reps, rep lengths, rests, band),
    so interval_library.equivalent judges a user-edited session like a swap. None
    without timed work steps that have an intensity. An HR-only structure gets the
    class's band (推估: src_kind). A 「負荷」 work step counts by its estimated time at
    the band's middle (load_work_s, SP-38) — 推估 too."""
    rows = flat(steps.get("items") or [])
    works, rests, bands, est = [], [], [], False
    last_work = None
    for i, row in enumerate(rows):
        st = row["st"]
        if st["kind"] == "work":
            b = _work_band(st, c)
            if b is None or st["dur"]["type"] not in ("time", "load"):
                continue
            if st["dur"]["type"] == "load":
                works.append(load_work_s(st["dur"]["value"], b))
                est = True
            else:
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
    load = any(r["st"]["kind"] == "work" and r["st"]["dur"]["type"] == "load" for r in flat(steps.get("items") or []))
    est = ("（「負荷」段用 TSS 換算時間，推估）" if load else "（心率結構換算強度，推估）") if v.src_kind == "推估" else ""
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


def rpe_name(st: dict) -> str:
    """A step with an RPE target on the watch: its name + 「RPE 3–4 · 爬升 400 m」 (no target)."""
    tg = st.get("target") or {}
    extra = " · ".join(x for x in (f"RPE {rpe_text(tg.get('lo'), tg.get('hi'))}", climb_text(tg)) if x)
    return f"{st['note']} · {extra}" if st.get("note") else extra


def _name(st: dict, r: Resolved, em: _Emit, grouped: bool) -> str:
    tg = st.get("target") or {}
    if tg.get("type") == "rpe":
        return rpe_name(st)
    if st.get("note"):
        return st["note"]
    if st["kind"] == "work" and tg.get("type") == "auto" and tg.get("intent") == "easy" and tg.get("plo") is not None:
        if r.type == "hr" and em.c.walk:
            from backend.engine.hr_profile import walk_step_name
            return walk_step_name()
        return CAP_NAME if r.type == "hr" else "功率區間" if r.type == "power" else "照感覺"
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
    if d["type"] == "load":
        # COROS: its TL end condition; `seconds` = the estimated time the others get (SP-38)
        s_, _e = _secs(st, r, em.c)
        return CW.Step(EX[st["kind"]], max(5, int(round(s_))), r.intensity, _name(st, r, em, grouped),
                       load_tss=float(d["value"]), load_tl=float(sent_tl(st, r, em.c)))
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


EX_LABEL = {1: N_("暖身"), 2: N_("訓練"), 3: N_("緩和"), 4: N_("休息")}


def _ex_line(ex: dict) -> dict:
    if ex["targetType"] == 2:
        dur = IL.fmt_s(ex["targetValue"])
    elif ex["targetType"] == 5:
        dur = f"{ex['targetValue'] / 100000:g} km"
    elif ex["targetType"] == _cw().COROS_TARGET_TYPE_LOAD:
        dur = f"{_(LOAD_LABEL)} {ex['targetValue']} TL"
    else:
        dur = _(OPEN_LABEL)
    it = ex.get("intensityType")
    if it == 6:
        tgt = _("功率 {lo}–{hi} W", lo=ex['intensityValue'], hi=ex['intensityValueExtend'])
    elif it == 2:
        tgt = _("心率 {lo}–{hi} bpm", lo=ex['intensityValue'], hi=ex['intensityValueExtend'])
    elif it == 3:
        tgt = _("配速 {lo}–{hi} /km", lo=mmss(ex['intensityValue']), hi=mmss(ex['intensityValueExtend']))
    else:
        tgt = _("不設目標")
    return {"kind": _(EX_LABEL.get(ex["exerciseType"], N_("訓練"))), "dur": dur, "target": tgt, "name": ex.get("name") or ""}


def watch_preview(steps: dict, c: Ctx, name: str = "TRC", overview: str = "") -> dict:
    """What COROS gets: {"lines": [{kind, dur, target, name} | {group, sets, steps}],
    "n", "limits": [{key, text, hit}], "lost": [text]} — limits always listed
    (absolute watts, one target per step, no ramps), `hit` when this session meets one."""
    CW = _cw()
    program = CW.build_program(name, steps_to_coros(steps, c), CW.Thresholds(cp=c.cp, lthr=c.lthr, aet=c.aet, hrz=c.hrz), overview)
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
         "text": _("只收絕對瓦數：跑步沒有 % CP，送的是換算後的 W；CP 更新後這堂會標成「已過期」，要重推")},
        {"key": "one", "hit": bool(c.hr_cap and has_power),
         "text": _("每段只有一個目標：功率段的心率上限只寫在文字，手錶不會提醒")},
        {"key": "ramp", "hit": False, "text": _("沒有漸進（ramp）步驟：漸進只寫在步驟名稱")},
    ]
    if any(r.type == "rpe" for _st, r in res):
        limits.append({"key": "rpe", "hit": True, "text": RPE_LIMIT})
    lost = []
    if any(r.need == "tpace" for _st, r in res):
        lost.append(no_tpace_text())
    if unrolled:
        lost.append(_("「最後一趟不休息」或重複裡的重複：COROS 群組做不到，推送時攤平成一段一段"))
    if dist:
        lost.append(_("距離段：COROS 欄位（公分）依第三方整理，這個 app 還沒實際送過（未驗證）"))
    if any(st["dur"]["type"] == "load" for st, _ in res):
        lost.append(_("「{x}」段：這裡填 TSS，推到 COROS 換算成它的 TL（推估，誤差約 ±20 %；"
                      "每次同步後用你的活動重新校正）", x=_(LOAD_LABEL)))
    n = len(program["exercises"])
    if n > COROS_MAX_STEPS:
        lost.append(_("{n} 段超過 {max} 段：COROS 的上限未驗證", n=n, max=COROS_MAX_STEPS))
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
    rpe_m = c.rpe_model()
    for row in flat(steps["items"]):
        st = row["st"]
        r = resolve(st, c)
        s, e = _secs(st, r, c)
        rd = by_id.setdefault(st["id"], r.as_dict())
        o = {"id": st["id"], "kind": st["kind"], "sec": round(s), "est": e,
             "open": st["dur"]["type"] == "open", "frac": r.frac, "level": level(r.frac),
             "type": r.type, "rep": [{"id": a, "i": i, "n": n} for a, i, n in row["rep"]]}
        if st["dur"]["type"] == "load":
            # the editor: 「≈ 98 TL（推估 ±20）」 next to the TSS, and the estimated time
            lt = load_tl(st, r, c)
            o["load"] = rd["load"] = {"tss": st["dur"]["value"], "tl": round(lt["tl"]), "err": round(lt["err"]),
                                      "fitted": lt["fitted"], "sec": round(s), "if": round(load_if(st, r), 3)}
            if st["dur"].get("rpe"):
                # entered by feel (SP-57): the level, minutes and the factor used (推估)
                o["load"]["rpe"] = {"level": st["dur"]["rpe"], "min": st["dur"].get("min"),
                                    "factor": round(rpe_m.factor, 4), "fitted": rpe_m.fitted,
                                    "err_pct": round(rpe_m.err_frac() * 100)}
        order.append(o)
    eq = equivalence(steps, rung, c) if rung else None
    return {"resolved": by_id, "order": order, "totals": totals(steps, c),
            "issues": issues(steps, c, cap, cap_mode, rung), "watch": watch_preview(steps, c),
            "summary": steps_text(steps, c), "structure": structure_text(steps), "rpe_role": rpe_role(steps["items"]),
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


def templates(prefs=None, user: Optional[dict] = None) -> dict:
    """The editor's 插入範本 (static/workout_editor.js): {"cats": [{id, label, subs?}],
    "groups": [{"group", "cat", "sub", "title", "rows": [{key, label, title, src, url,
    src_kind, items (main set), full, equiv, family, purpose}]}]}. Each category: the
    published library (engine/workout_templates.py) first; 強度課 also the interval ladder's
    variants — both split by workout_templates.family_of (有氧間歇 / VO2max 間歇 / 速度), 速度 also
    strides and short hill sprints (SP-32 follow-up: they are 速度 by family_of, but live in
    other categories); 測試 also the app's CP protocols; 越野跑 split by its kind (結構化爬升 /
    技術地形 / 下坡, SP-62). `user` ({"templates", "cats"}, engine/user_templates.py, SP-36):
    the user's own templates first in every category they are in (「我的範本」), and their
    own categories as extra tabs."""
    from backend.engine import cp_protocols as CPP
    from backend.engine import user_templates as UT
    from backend.engine import workout_templates as WT
    lib = [(t, WT.row(t)) for t in WT.TEMPLATES]
    groups = UT.groups((user or {}).get("templates") or [])

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
    fams = {v.key: WT.family_of_variant(v) for v in IL.ALL.values()}

    def fam_fields(v):
        f = fams[v.key]
        return {"sub": f["id"] if f else None, "family": f, "purpose": WT.variant_purpose(f)}
    for sub in WT.FAMILY_IDS:
        g("quality", "有出處的課表", [r for t, r in lib if t.cat == "quality" and r["sub"] == sub], sub)
        ladder = []
        for rung in IL.RUNG_ORDER + ("tp",):
            for v in IL.LIBRARY[rung]:
                if (fams[v.key] or {}).get("id") != sub:
                    continue
                ok, _why = IL.equivalent(v)
                ladder.append({"key": v.key, "label": f"{IL.RUNG_NAME[rung]} {IL.title(v)} · {IL.rest_text(v)}" + ("（標準）" if v.canonical else ""),
                               "title": IL.plain_title(v), "src": f"間歇庫 {IL.RUNG_NAME[rung]}",
                               "items": main_set(v), "equiv": ok, "src_kind": v.src_kind, "full": from_variant(v, "std")["items"],
                               "variant": True, "rung": v.rung, **fam_fields(v)})
        for v in IL.NON_EQUIV:
            if (fams[v.key] or {}).get("id") == sub:
                ladder.append({"key": v.key, "label": f"{IL.title(v)}（每趟 < 2 分，不算進階）", "title": IL.title(v),
                               "src": "間歇庫（非同等）", "items": main_set(v), "equiv": False, "src_kind": v.src_kind,
                               "full": from_variant(v, "std")["items"], "variant": True, "rung": v.rung, **fam_fields(v)})
        g("quality", "間歇庫（進階階梯）", ladder, sub)
        if sub == "speed":
            # strides / short hill sprints: 速度 by family_of (≤ 2′, rest ≥ 2 ×) but filed under
            # 輕鬆跑 / 越野跑 — listed here too, with the family the tab needs
            fam = WT.label("speed")
            sp = [{**strides, "family": fam, "sub": "speed", "purpose": _(WT.PURPOSE["strides"])},
                  {**hills, "family": fam, "sub": "speed", "purpose": _(WT.PURPOSE["hill_sprint"])}]
            sp += [{**r, "family": fam, "sub": "speed"} for t, r in lib if t.key == "ua_hill_sprints"]
            g("quality", "加速跑與短坡衝刺", sp, sub)
    g("test", "有出處的課表", [r for t, r in lib if t.cat == "test"])
    other = []
    for p in ("quick", "standard"):
        s = CPP.session_for(p)
        d = derive({"kind": "test", "title": s["title"], "detail": s["detail"], "target": s.get("target") or "",
                    "protocol": p}) if s else None
        if d:
            main = [x for x in d["items"] if x["kind"] in ("work", "rest")]
            other.append({"key": f"cp_{p}", "label": f"CP 測試：{s['title']}", "title": s["title"], "items": main,
                          "full": d["items"], "equiv": None, "src_kind": "peer", "src": "這個 app 的 CP 測試"})
    g("test", "這個 app 的 CP 測試", other)
    for sub in WT.TRAIL_IDS:
        g("trail", "有出處的課表", [r for t, r in lib if t.cat == "trail" and r["sub"] == sub], sub)
    g("trail", "附加", [hills], "climb")
    for gr in groups:
        for r in gr["rows"]:
            # the editor badges these when there is no threshold pace (their pace is × it)
            r["needs_tpace"] = needs_tpace(r.get("full") or r.get("items"))
            # the 主課強度類型 filter (SP-84; user rows: user_templates.row, the same helper)
            r["target_types"] = target_types(r.get("full") or r.get("items"), r.get("basis") or r.get("target_basis"))
    return {"cats": WT.cats() + list((user or {}).get("cats") or []), "groups": groups,
            "target_types": target_type_list(), "no_tpace_text": no_tpace_text()}


def zones_table(c: Ctx) -> dict:
    """The 區間 dropdowns with today's numbers: {"power": [{id, label, lo, hi, text}], "hr", "pace"}.
    HR with a 課表心率區間: its Z1–Z6 (lo / hi × LTHR for the editor's 填法 switch, None
    without LTHR), then the Friel rows marked `legacy` (shown only on a step that has one)."""
    hz = hr_model_zones(c)
    # a walking session (SP-115): the easy row is its uphill cap (hr_profile.walk_band)
    cap_label = "≤ " + EASY_CAP
    if c.walk:
        from backend.engine.hr_profile import WALK_CAP
        cap_label = "≤ " + _(WALK_CAP)

    def rows(ty):
        out = []
        if ty == "hr" and hz:
            e = easy_hr(c)
            out.append({"id": "aet", "label": cap_label, "text": f"{e[1]}–{e[2]} bpm" if e else ""})
            for z, name, lo, hi in hz:
                out.append({"id": z, "label": f"{z} {name}".strip(),
                            "lo": lo / c.lthr if c.lthr else None, "hi": hi / c.lthr if c.lthr else None,
                            "text": f"{lo}–{hi} bpm"})
        for z, lo, hi in ZONES[ty]:
            if ty == "hr" and hz:
                if z != "aet":
                    out.append({"id": z, "label": f"Friel Z{z}（舊）", "lo": lo, "hi": hi, "legacy": True,
                                "text": f"{lo * c.lthr:.0f}–{hi * c.lthr:.0f} bpm" if c.lthr else ""})
                continue
            if ty == "hr" and z == "aet":
                e = easy_hr(c)
                out.append({"id": "aet", "label": cap_label, "text": f"{e[1]}–{e[2]} bpm" if e else ""})
                continue
            base = {"power": c.cp, "hr": c.lthr, "pace": c.tpace}[ty]
            if ty == "pace":
                text = f"{mmss(lo * base)}–{mmss(hi * base)} /km" if base else ""
            else:
                text = f"{lo * base:.0f}–{hi * base:.0f} {'W' if ty == 'power' else 'bpm'}" if base else ""
            out.append({"id": z, "label": f"Z{z}", "lo": lo, "hi": hi, "text": text})
        return out
    return {k: rows(k) for k in ("power", "hr", "pace")}
