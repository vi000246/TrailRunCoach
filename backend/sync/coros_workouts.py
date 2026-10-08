"""
Push stored plan sessions (engine/plan_store, via api/plan_sessions) to
COROS Training Hub as structured workouts, scheduled on their day so they
sync to the watch.

Unofficial Training Hub API (same host + token as coros_client). Endpoints
and field codes cross-checked in dholliday3/coros-training-mcp,
jgretz/coros-run-plan-mcp and wtcollote/coros-workout-mcp:

  POST /training/program/add      body = program            -> data: "<program id>"
  GET  /training/program/detail   ?id=&supportRestExercise=1 -> program
  POST /training/program/query    {name, startNo, limitSize, sportType, supportRestExercise}
  POST /training/program/delete   body = ["<program id>", ...]
  GET  /training/schedule/query   ?startDate=YYYYMMDD&endDate=&supportRestExercise=1
                                  -> {id (plan), maxIdInPlan, entities[], programs[]}
  POST /training/schedule/update  add:    {entities:[{happenDay, idInPlan, sortNoInSchedule}],
                                           programs:[{...program, idInPlan}],
                                           versionObjects:[{id: idInPlan, status: 1}], pbVersion: 2}
                                  delete: {versionObjects:[{id: idInPlan, planProgramId, planId,
                                           status: 3}], pbVersion: 2}

Program codes: sportType 1 run, 2 bike, 4 strength. exerciseType 0 group,
1 warm-up, 2 training, 3 cool-down, 4 rest. targetType 1 open (lap button),
2 time (s), 5 distance (cm), 6 training load (targetValue = COROS TL, an integer — read back
2026-10-04 from a Training Hub workout with a 「TL 100」 end condition: targetType 6,
targetValue 100, the step's HR target kept as usual). intensityType 0 none, 2 heart rate, 3 pace
(seconds per km in intensityValue = the faster bound / intensityValueExtend = the slower,
intensityDisplayUnit 1 — verified on a COROS watch 2026-10-02: 270 / 285 showed
4'30"–4'45"/km; 270000 showed 4500'00"), 6 power (W). HR: isIntensityPercent false = absolute bpm in
intensityValue/intensityValueExtend; hrType 3 = the LTHR zone scheme
(intensityPercent = % of LTHR × 1000). intensityPercent / intensityPercentExtend are left 0
(SP-67): COROS keeps the bpm and fills the percent itself from the account's own LTHR — a
workout pushed with 82–94 % (÷ the app's 160) read back as 86–99 % (÷ COROS's 152), bpm
unchanged (docs/research/coros-threshold-unification.md §2.3). Nothing here reads the field.

Every pushed session is recorded in `coros_plan_push` (session key = the
stored session's uid, plan_store.push_dict -> COROS
program / schedule ids + a fingerprint of what was sent), so pushing again
leaves unchanged sessions alone, replaces changed ones and removes sessions
that dropped out of the plan. Only workouts recorded there are ever deleted.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Union

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import CorosPlanPush
from backend.engine.zones import WORKOUT_TARGETS
from backend.sync import http
from backend.sync.coros_client import _get_token_and_base, _headers
# the provider interface's errors (sync/workout_targets/base.py); Unsupported is shared
from backend.sync.workout_targets.base import SyncAuthError, SyncError, Unsupported  # noqa: F401

log = logging.getLogger(__name__)

SPORT_RUN = 1
EX_GROUP, EX_WARMUP, EX_TRAIN, EX_COOLDOWN, EX_REST = 0, 1, 2, 3, 4
TARGET_OPEN, TARGET_TIME, TARGET_DIST = 1, 2, 5
# 「負荷」 steps (SP-38): COROS's training-load end condition. Set to None to push them as an
# estimated-time step instead (the name then carries 「負荷 X TSS（約 Y TL）」)
COROS_TARGET_TYPE_LOAD: Optional[int] = 6
INT_NONE, INT_HR, INT_PACE, INT_POWER = 0, 2, 3, 6
PACE_DISPLAY_UNIT = 1            # min/km on the watch (verified 2026-10-02)
HR_TYPE_LTHR = 3
REST_NONE = 3
SORT_TOP, SORT_CHILD = 16777216, 65536
PB_VERSION = 2
NAME_PREFIX = "TRC"
MAX_NAME = 30

OVERVIEW = {EX_WARMUP: "sid_run_warm_up", EX_TRAIN: "sid_run_training",
            EX_COOLDOWN: "sid_run_cool_down", EX_REST: "sid_run_rest"}
STEP_NAME = {EX_WARMUP: "暖身", EX_TRAIN: "主課", EX_COOLDOWN: "緩和", EX_REST: "恢復"}

# power / HR fractions per target id, straight from zones.WORKOUT_TARGETS
_FRAC = {tid: (plo, phi, hlo, hhi) for tid, _n, plo, phi, hlo, hhi, *_ in WORKOUT_TARGETS}

_push_lock = asyncio.Lock()


PROVIDER = "coros"                # coros_plan_push.provider (sync/workout_targets)


class CorosError(SyncError):
    """COROS answered with an error (HTTP or result != "0000")."""


class CorosAuthError(CorosError, SyncAuthError):
    """No usable COROS token: log in again."""


# ---------------------------------------------------------------------------
# session -> steps
# ---------------------------------------------------------------------------

@dataclass
class Step:
    kind: int                     # EX_WARMUP / EX_TRAIN / EX_COOLDOWN / EX_REST
    seconds: int                  # 0 = open (ends with the lap button)
    intensity: Optional[tuple] = None   # ("hr", lo, hi) | ("power", lo, hi)
    name: str = ""
    meters: int = 0               # > 0: a distance step (targetType 5, cm) — engine/workout_steps.py
    # a 「負荷」 step (engine/workout_steps.py, SP-38): the planned TSS and its COROS TL
    # (engine/coros_tl.py, 推估); `seconds` is then the estimated time, which every provider
    # without a load end condition gets instead
    load_tss: float = 0.0
    load_tl: float = 0.0


@dataclass
class Repeat:
    sets: int
    steps: list = field(default_factory=list)
    name: str = "間歇"


StepLike = Union[Step, Repeat]


@dataclass
class Thresholds:
    cp: Optional[float] = None
    lthr: Optional[float] = None
    aet: Optional[float] = None
    tpace: Optional[float] = None      # threshold pace, s/km (pace targets in % / zones)
    # 課表心率區間 (engine/hr_profile.plan_hr_zones, via the plan's thresholds "hr_model"):
    # easy = its Z2 band (a measured AeT caps it), interval classes = its zones
    hrz: Optional[dict] = None
    aet_measured: bool = False         # the easy cap is a measured AeT (week_plan thresholds)
    walk: Optional[dict] = None        # the walking sessions' uphill cap (hr_profile.walk_cap, SP-115)

    @classmethod
    def of(cls, t: Optional[dict]) -> "Thresholds":
        t = t or {}
        f = lambda k: float(t[k]) if t.get(k) else None
        hrz = t.get("hr_model") if isinstance(t.get("hr_model"), dict) else None
        from backend.engine.hr_profile import easy_cap_measured
        return cls(cp=f("cp"), lthr=f("lthr"), aet=f("aet"), tpace=f("tpace"), hrz=hrz,
                   aet_measured=easy_cap_measured(t),
                   walk=t.get("walk_cap") if isinstance(t.get("walk_cap"), dict) else None)

    def walk_of(self, s: dict) -> Optional[dict]:
        """The uphill cap when `s` is a walking session (target_policy.is_walk), else None."""
        from backend.engine.target_policy import is_walk
        return self.walk if self.walk and is_walk(s) else None


def cap_name(th: Thresholds) -> str:
    """The step name of an easy step capped by HR: 「心率 ≤ 輕鬆跑上限」 (+「（實測 AeT）」 when
    measured; the bpm is the step's target) — hr_profile.easy_cap_label."""
    from backend.engine.hr_profile import easy_cap_label
    return "心率 ≤ " + easy_cap_label(None, None, th.aet_measured or bool((th.hrz or {}).get("aet_measured")))


def easy_hr(th: Thresholds) -> Optional[tuple]:
    """Easy / long / hike: heart rate capped at the easy-run cap — with a 課表心率區間, its Z2
    band (the cap a measured AeT, else Z2's top; hr_profile.plan_hr_zones)."""
    if th.hrz and th.hrz.get("easy"):
        lo, hi = th.hrz["easy"]
        return ("hr", round(lo), round(hi))
    hi = th.aet or (0.89 * th.lthr if th.lthr else None)
    if not hi:
        return None
    lo = 0.75 * th.lthr if th.lthr else hi - 25
    lo = min(lo, hi - 10)
    return ("hr", round(lo), round(hi))


def power(th: Thresholds, lo_frac: float, hi_frac: float) -> Optional[tuple]:
    if not th.cp:
        return None
    return ("power", round(lo_frac * th.cp), round(hi_frac * th.cp))


def _num(pat: str, text: str, default: Optional[int] = None, group: int = 1) -> Optional[int]:
    m = re.search(pat, text or "")
    return int(m.group(group)) if m and m.group(group) else default


def _basis(s: dict) -> Optional[str]:
    """hr / power / none from engine/target_policy (push_dict resolves it with the 課表偏好;
    a session without it is resolved here with the defaults)."""
    b = s.get("basis")
    if b in ("hr", "power", "none"):
        return b
    from backend.engine import target_policy as TP
    return TP.target_policy(s)["basis"]


HR_WORK = {"Z3sub": ("aet", 1.00), "Z3near": (0.95, 1.00), "Z4": (1.00, 1.03), "Z5": (1.00, 1.05)}   # × LTHR (aet = the AeT)


def _work_hr(s: dict, th: Thresholds, cls: Optional[str]) -> Optional[tuple]:
    hm = re.search(r"心率\s*(\d+)\s*[–-]\s*(\d+)\s*bpm", s.get("target", ""))
    if hm:
        return ("hr", int(hm.group(1)), int(hm.group(2)))
    from backend.engine.hr_profile import work_band
    wb = work_band(th.hrz, cls or "Z3near")          # 課表心率區間: the class's COROS zone
    if wb:
        return ("hr", *wb)
    a, b = HR_WORK.get(cls or "", (0.95, 1.00))
    if not th.lthr:
        return None
    lo = th.aet if a == "aet" else a * th.lthr
    return ("hr", round(lo or 0.89 * th.lthr), round(b * th.lthr))


def easy_target(s: dict, th: Thresholds, frac: tuple = (0.75, 0.80)) -> Optional[tuple]:
    """Easy / long / hike by the session's basis: HR ≤ AeT (auto), a power band (課表偏好 or the
    session's own 功率: Palladino Z2), or nothing. A walking session's HR cap is its uphill
    cap (hr_profile.walk_band, SP-115)."""
    from backend.engine.hr_profile import walk_band
    b = _basis(s)
    if b == "power":
        return power(th, *frac) or walk_band(th.walk_of(s), easy_hr(th))
    if b == "none":
        return None
    return walk_band(th.walk_of(s), easy_hr(th))


def easy_name(s: dict, th: Thresholds, it: Optional[tuple]) -> str:
    """The step name of an easy / long / walking main step by its target."""
    if it and it[0] == "hr":
        if th.walk_of(s):
            from backend.engine.hr_profile import walk_step_name
            return walk_step_name()
        return cap_name(th)
    return "功率區間" if it else "照感覺"


WARM_NAME = {"city": "輕鬆跑暖身", "river": "輕鬆跑→漸進", "drills": "動態伸展／drill"}
REST_NAME = {"walk": "走路或極慢跑", "jog": "慢跑恢復", "jog_down": "慢跑／走下坡", "none": "恢復"}


def _variant_steps(s: dict, th: Thresholds) -> Optional[list[StepLike]]:
    """A library variant (engine/interval_library.py) from its own timed steps:
    the warm-up blocks (city run, riverside, drills, strides), every rep and rest
    as its own lap (walk rests have no target), the cool-down. None when the
    session carries no known variant."""
    from backend.engine import interval_library as IL
    v = IL.resolve(s.get("variant_key"), s.get("variant_reps"), s.get("variant_adj"))
    if v is None:
        return None
    work_int = power(th, v.lo, v.hi)
    if _basis(s) == "hr":
        work_int = _work_hr(s, th, v.cls) or work_int
    elif _basis(s) == "none":
        work_int = None
    out: list[StepLike] = []
    for st in IL.steps(v, s.get("variant_blocks") or "std"):
        k = st["kind"]
        if k == "warm" and st["code"] == "strides":
            n = max(1, st["s"] // 60)
            out.append(Repeat(n, [Step(EX_TRAIN, 20, None, "快步跑 20 秒"), Step(EX_REST, 40, None, "慢跑")],
                              f"快步跑 {n}×20 秒"))
        elif k == "warm":
            out.append(Step(EX_WARMUP, st["s"], None if st["code"] == "drills" else easy_hr(th),
                            WARM_NAME.get(st["code"], "暖身")))
        elif k == "work":
            out.append(Step(EX_TRAIN, st["s"], work_int, st["text"]))
        elif k == "rest":
            out.append(Step(EX_REST, st["s"], easy_hr(th) if st.get("mode") == "jog" else None,
                            REST_NAME.get(st.get("mode"), "恢復")))
        else:
            out.append(Step(EX_COOLDOWN, st["s"], easy_hr(th), "緩和"))
    return out


def _quality_steps(s: dict, th: Thresholds) -> list[StepLike]:
    vs = _variant_steps(s, th) if s.get("variant_key") else None
    if vs is not None:
        return vs
    title, detail, target = s.get("title", ""), s.get("detail", ""), s.get("target", "")
    m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*分", title)
    if not m:
        raise Unsupported(f"看不懂間歇結構：{title}")
    reps, work = int(m.group(1)), int(m.group(2))
    rest = _num(r"休\s*\d+\s*[–-]\s*(\d+)\s*分", detail) or _num(r"休\s*(\d+)\s*分", detail) or work
    warm = _num(r"暖身\s*(\d+)\s*分", detail, 15)
    cool = _num(r"緩和\s*(\d+)\s*分", detail, 10)
    cool = max(cool, int(s.get("minutes") or 0) - warm - reps * (work + rest))
    tid = "supra" if "爬坡" in title or "Supra" in s.get("source", "") else "threshold"
    plo, phi = _FRAC[tid][:2]
    pm = re.search(r"(\d+)\s*[–-]\s*(\d+)\s*%\s*CP", detail + " " + target)
    if pm:
        plo, phi = int(pm.group(1)) / 100.0, int(pm.group(2)) / 100.0
    work_int = power(th, plo, phi)
    # 課表偏好 間歇目標 = 心率 (engine/plan_prefs.py): the target carries only a heart-rate range
    hm = re.search(r"心率\s*(\d+)\s*[–-]\s*(\d+)\s*bpm", target)
    if (hm and "功率" not in target) or s.get("basis") == "hr":
        work_int = _work_hr(s, th, None) or work_int
    elif s.get("basis") == "none":
        work_int = None
    return [Step(EX_WARMUP, warm * 60, easy_hr(th)),
            Repeat(reps, [Step(EX_TRAIN, work * 60, work_int, f"{work} 分"),
                          Step(EX_REST, rest * 60, None)], f"{reps}×{work} 分"),
            Step(EX_COOLDOWN, cool * 60, easy_hr(th))]


def _test_steps(s: dict, th: Thresholds) -> list[StepLike]:
    """CP test by protocol (engine/cp_protocols.py; legacy rows from the title).
    The all-out bouts have an open target (no power range: a range reads as
    the goal, and the old 12′ range topped out below the athlete's real
    12′ power); warm-up / rest / cool-down are HR ≤ AeT, lengths from detail."""
    from backend.engine import cp_protocols as CPP
    proto = CPP.protocol_of(s) or "standard"
    if proto == "race":
        raise Unsupported("用比賽代替 CP 測試：比賽不推")
    t = CPP.TABLE[proto]
    title, detail = s.get("title", ""), s.get("detail", "")
    warm = _num(r"暖身\s*(\d+)\s*分", detail, t["warm"])
    cool = _num(r"緩和\s*(\d+)\s*分", detail, t["cool"])
    if proto == "quick":
        work = _num(r"(\d+)\s*分全力", title, 20)
        return [Step(EX_WARMUP, warm * 60, easy_hr(th)),
                Step(EX_TRAIN, work * 60, None, f"{work} 分全力"),
                Step(EX_COOLDOWN, cool * 60, easy_hr(th))]
    # "CP 測試 12 分 + 3 分" (long first); a legacy "3 分 + 12 分" keeps its order
    a = _num(r"(\d+)\s*分\s*\+", title, 12)
    b = _num(r"\+\s*(\d+)\s*分", title, 3)
    gap = _num(r"休\s*(\d+)\s*分", s.get("target", "") + detail, t["rest"])
    return [Step(EX_WARMUP, warm * 60, easy_hr(th)),
            Step(EX_TRAIN, a * 60, None, f"{a} 分全力"),
            Step(EX_REST, gap * 60, easy_hr(th), "恢復"),
            Step(EX_TRAIN, b * 60, None, f"{b} 分全力"),
            Step(EX_COOLDOWN, cool * 60, easy_hr(th))]


def _aet_test_steps(s: dict, th: Thresholds) -> list[StepLike]:
    # 「AeT 飄移測試」 (engine/aet_test.py, UA's minimum): 10′ warm-up ≤ the start HR,
    # 40′ at a fixed power ±3 %; the cool-down is optional (「緩和可省略」 → no step, 50 min
    # in all; a stored 「緩和 N 分」 → an N′ lap); each its own lap so the analysis can cut them.
    # Rows stored with the old 15 / 60 / 5 text keep their own numbers.
    text = f"{s.get('target', '')} {s.get('detail', '')}"
    warm = _num(r"暖身\s*(\d+)\s*分", text, 10)
    main = _num(r"測試\s*(\d+)\s*分", text, 40)
    from backend.engine.aet_test import protocol_of_title, xu_main_name, xu_main_target
    proto = protocol_of_title(s.get("title"))
    if proto in ("xu90", "friel"):
        # 徐國峰 90 分 (SP-274): the main block holds a pace (the E pace ± 3 %) or a power (75–80 %
        # of a tested CP) read back from the stored target (aet_test.xu_target), else no target
        # (the talk test) — never an HR cap: it would hold the drift down. The warm-up keeps the
        # easy-run cap. Friel (steady at AeT): an HR-capped main block.
        if proto == "xu90":
            tg = xu_main_target(s.get("target", ""))
            main_step = Step(EX_TRAIN, main * 60, tg, xu_main_name(s.get("target", "")))
        else:
            main_step = Step(EX_TRAIN, main * 60, easy_hr(th), "AeT 心率附近穩定跑")
        steps = [Step(EX_WARMUP, warm * 60, easy_hr(th)), main_step]
        cool = _num(r"緩和\s*(?:\d+\s*[–-]\s*)?(\d+)\s*分", text, 0)
        if cool:
            steps.append(Step(EX_COOLDOWN, cool * 60, easy_hr(th)))
        return steps
    cool = _num(r"緩和\s*(?:\d+\s*[–-]\s*)?(\d+)\s*分", text, 0)
    p = _num(r"固定功率\s*(\d+)\s*W", text) or (round(0.75 * th.cp) if th.cp else None)
    hr0 = _num(r"心率從\s*(\d+)", text)
    warm_int = easy_hr(th)
    if hr0 and warm_int:
        warm_int = ("hr", min(warm_int[1], hr0 - 10), hr0)
    main_int = ("power", round(p * 0.97), round(p * 1.03)) if p else None
    steps = [Step(EX_WARMUP, warm * 60, warm_int),
             Step(EX_TRAIN, main * 60, main_int, "固定功率，不要調")]
    if cool:
        steps.append(Step(EX_COOLDOWN, cool * 60, easy_hr(th)))
    return steps


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _int_text(it: Optional[tuple]) -> str:
    if not it:
        return "不設目標"
    typ, lo, hi = it
    if typ == "hr":
        return f"心率 {lo:.0f}–{hi:.0f}" if lo else f"心率 ≤ {hi:.0f}"
    if typ == "pace":
        return f"配速 {_mmss(lo)}–{_mmss(hi)} /km"
    return f"功率 {lo:.0f}–{hi:.0f} W"


def _mmss(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


def step_lines(steps: list[StepLike]) -> list[str]:
    """「① 暖身 15 分 心率 ≤ 138」「② 跑 8 分 ×3 功率 184–194 W，休息 2 分 走路」 — what 自動
    (or the chosen basis) gives each step of this session, as the editor lists it."""
    def mins(sec):
        return f"{sec / 60:g} 分" if sec >= 60 else f"{sec} 秒"
    out = []
    i = 0
    flat = []
    for st in steps:
        flat.append(st)
    while i < len(flat):
        st = flat[i]
        n = len(out)
        mark = CIRCLED[n] if n < len(CIRCLED) else f"{n + 1}."
        if isinstance(st, Repeat):
            w, r = st.steps[0], (st.steps[1] if len(st.steps) > 1 else None)
            rest = f"，休息 {mins(r.seconds)} {r.name or ''}".rstrip() if r else ""
            head = w.name if w.name and re.search(r"\d", w.name) else f"{w.name or '跑'} {mins(w.seconds)}"
            out.append(f"{mark} {head} ×{st.sets} {_int_text(w.intensity)}{rest}")
            i += 1
            continue
        if st.kind == EX_TRAIN and i + 1 < len(flat) and isinstance(flat[i + 1], Step) and flat[i + 1].kind == EX_REST:
            # a run of work + rest steps with the same lengths → one 「×n」 line
            k, j = 0, i
            while j + 1 < len(flat) and isinstance(flat[j], Step) and flat[j].kind == EX_TRAIN and \
                    flat[j].seconds == st.seconds and isinstance(flat[j + 1], Step) and flat[j + 1].kind == EX_REST:
                k += 1
                j += 2
            last_work = j < len(flat) and isinstance(flat[j], Step) and flat[j].kind == EX_TRAIN and flat[j].seconds == st.seconds
            reps = k + (1 if last_work else 0)
            if reps >= 2:
                r = flat[i + 1]
                out.append(f"{mark} 跑 {mins(st.seconds)} ×{reps} {_int_text(st.intensity)}，休息 {mins(r.seconds)} {REST_NAME.get('walk') if not r.intensity else '慢跑'}")
                i = j + (1 if last_work else 0)
                continue
        label = {EX_WARMUP: "暖身", EX_COOLDOWN: "緩和", EX_REST: "休息"}.get(st.kind, st.name or "跑")
        name = f"{label}（{st.name}）" if st.name and st.kind in (EX_WARMUP,) and st.name not in ("暖身",) else label
        out.append(f"{mark} {name} {mins(st.seconds)} {_int_text(st.intensity)}")
        i += 1
    return out


def session_steps(s: dict, th: Thresholds, sent_tl: Optional[dict] = None) -> list[StepLike]:
    """Structured steps for one week-plan session, or Unsupported. `sent_tl`: the TL its
    「負荷」 steps were last pushed with (session_workout / _sent_tl)."""
    kind = s.get("kind")
    secs = int(s.get("minutes") or 0) * 60
    if kind == "rest" or (kind == "race" and not s.get("steps")):
        # a race goes to the watch only with steps: the 賽事計算機's 「匯出至課表」 (its legs);
        # the generator's own 比賽 row has none
        raise Unsupported("比賽 / 休息不推")
    if kind == "strength":
        raise Unsupported("COROS 肌力課要從動作庫挑動作，先不推")
    if kind == "heat_passive":
        raise Unsupported("被動熱適應不推")
    if s.get("steps") and kind != "notice":
        # the structure the user saved in the editor (engine/workout_steps.py) wins over the text
        from backend.engine import workout_steps as WS
        try:
            st = WS.normalize(s["steps"])
        except WS.StepsError as e:
            raise Unsupported(f"課表結構有誤：{e}")
        c = WS.Ctx(cp=th.cp, lthr=th.lthr, aet=th.aet, tpace=th.tpace, basis=_basis(s), hrz=th.hrz,
                   sent_tl=sent_tl, walk=th.walk_of(s))
        return WS.steps_to_coros(st, c)
    if kind == "notice":
        # 課表待確認 (engine/plan_auto.py): one 1-minute open warm-up step, so it is
        # obviously not a real session; the summary goes in the overview (detail)
        return [Step(EX_WARMUP, 60, None, "課表待確認：到總覽頁同意／拒絕")]
    if kind in ("quality",):
        return _quality_steps(s, th)
    if kind == "test":
        from backend.engine.aet_test import is_aet_session
        return _aet_test_steps(s, th) if is_aet_session(s) else _test_steps(s, th)
    if secs <= 0:
        raise Unsupported("沒有時間長度")
    from backend.engine import workout_steps as WS
    mp = WS.mp_minutes(s) if kind == "long" else None
    if mp and secs - mp * 60 - WS.MP_TAIL_S >= 10 * 60:
        # 主要訓練項目 = 路跑: easy, marathon pace, easy (workout_steps.derive / mp_target): the goal pace,
        # else threshold pace × 1.04–1.08 (intensityType 3, s/km), else an HR band
        tg, g = WS.mp_target(s), WS.mp_goal_pace(s)
        if g:
            mp_t = ("pace", tg["lo"], tg["hi"])
        elif th.tpace:
            mp_t = ("pace", round(WS.MP_PACE[0] * th.tpace), round(WS.MP_PACE[1] * th.tpace))
        else:
            mp_t = ("hr", round(WS.MP_HR[0] * th.lthr), round(WS.MP_HR[1] * th.lthr)) if th.lthr else None
        return [Step(EX_TRAIN, secs - mp * 60 - WS.MP_TAIL_S, easy_target(s, th, (0.80, 0.88)), "輕鬆"),
                Step(EX_TRAIN, mp * 60, mp_t, "馬拉松配速"),
                Step(EX_COOLDOWN, WS.MP_TAIL_S, easy_target(s, th), "輕鬆收操")]
    if kind in ("long", "mountain", "hike"):
        it = easy_target(s, th, (0.80, 0.88) if kind == "long" else (0.75, 0.88))
        return [Step(EX_TRAIN, secs, it, easy_name(s, th, it))]
    if kind == "easy" and (s.get("heat") or "熱適應" in (s.get("title") or "")) and secs >= 20 * 60:
        # heat-acclimation.md §5.4: warm-up 10 / main / cool-down 5 (walk), HR ≤ AeT
        return [Step(EX_WARMUP, 10 * 60, easy_hr(th), "熱適應：慢慢進入"),
                Step(EX_TRAIN, secs - 15 * 60, easy_hr(th), "熱適應：照心率、配速放慢"),
                Step(EX_COOLDOWN, 5 * 60, None, "走路降溫")]
    if kind == "easy":
        m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*秒", s.get("title", ""))
        if m:
            n, sprint = int(m.group(1)), int(m.group(2))
            recover = 60
            base = secs - n * (sprint + recover)
            if base >= 10 * 60:
                w_name, r_name, rep_name = WS.strides_names(s.get("title") or "", sprint, n)
                return [Step(EX_TRAIN, base, easy_target(s, th), cap_name(th)),
                        Repeat(n, [Step(EX_TRAIN, sprint, None, w_name),
                                   Step(EX_REST, recover, None, r_name)], rep_name)]
        it = easy_target(s, th)
        return [Step(EX_TRAIN, secs, it, easy_name(s, th, it))]
    raise Unsupported(f"不支援的課表類型 {kind}")


# ---------------------------------------------------------------------------
# steps -> COROS program payload
# ---------------------------------------------------------------------------

def load_note(st: Step) -> str:
    """「負荷 80 TSS（約 100 TL）」: what a load step pushed as estimated time says in its name."""
    return f"負荷 {st.load_tss:g} TSS（約 {round(st.load_tl)} TL）"


def native_load(st: Step) -> bool:
    return bool(st.load_tl) and COROS_TARGET_TYPE_LOAD is not None


def _exercise(st: Step, ex_id: int, sort_no: int, group_id: str, th: Thresholds,
              legacy_percent: bool = False) -> dict:
    """One step. `legacy_percent`: also fill an HR step's intensityPercent with bpm ÷ the app's
    LTHR, as every payload did before SP-67 — never sent, only for legacy_fingerprint."""
    name = st.name or STEP_NAME[st.kind]
    if st.load_tl and not native_load(st):
        # COROS_TARGET_TYPE_LOAD unset: the estimated time stands in, the name says the load
        name = f"{st.name} · {load_note(st)}" if st.name else load_note(st)
    if native_load(st):
        ttype, tval = COROS_TARGET_TYPE_LOAD, max(1, int(round(st.load_tl)))
    else:
        ttype = TARGET_DIST if st.meters else TARGET_TIME if st.seconds else TARGET_OPEN
        tval = int(st.meters) * 100 if st.meters else st.seconds
    ex = {
        "id": ex_id, "name": name, "overview": OVERVIEW[st.kind],
        "exerciseType": st.kind, "sportType": SPORT_RUN,
        "targetType": ttype,
        "targetValue": tval,
        "targetDisplayUnit": 0,
        "intensityType": INT_NONE, "intensityValue": 0, "intensityValueExtend": 0,
        "intensityDisplayUnit": 0, "hrType": 0, "isIntensityPercent": False,
        "intensityPercent": 0, "intensityPercentExtend": 0,
        "sets": 1, "sortNo": sort_no, "restType": REST_NONE, "restValue": 0,
        "groupId": group_id, "isGroup": False, "originId": "0",
    }
    if st.intensity:
        typ, lo, hi = st.intensity
        ex["intensityValue"], ex["intensityValueExtend"] = int(lo), int(hi)
        if typ == "hr":
            ex["intensityType"], ex["hrType"] = INT_HR, HR_TYPE_LTHR
            if th.lthr and legacy_percent:
                ex["intensityPercent"] = round(lo / th.lthr * 100000)
                ex["intensityPercentExtend"] = round(hi / th.lthr * 100000)
        elif typ == "power":
            ex["intensityType"] = INT_POWER
        elif typ == "pace":
            # s/km, value = faster (smaller) bound, extend = slower (the verified probe order)
            a, b = sorted((int(round(lo)), int(round(hi))))
            ex["intensityType"], ex["intensityDisplayUnit"] = INT_PACE, PACE_DISPLAY_UNIT
            ex["intensityValue"], ex["intensityValueExtend"] = a, b
    return ex


def build_program(name: str, steps: list[StepLike], th: Thresholds, overview: str = "",
                  legacy_percent: bool = False) -> dict:
    exercises: list[dict] = []
    ex_id = 0
    total = 0
    for top, st in enumerate(steps, start=1):
        base = SORT_TOP * top
        ex_id += 1
        if isinstance(st, Repeat):
            gid = ex_id
            children = []
            for j, c in enumerate(st.steps, start=1):
                ex_id += 1
                children.append(_exercise(c, ex_id, base + SORT_CHILD * j, str(gid), th, legacy_percent))
            one = sum(c.seconds for c in st.steps)
            exercises.append({
                "id": gid, "name": st.name, "overview": OVERVIEW[EX_TRAIN],
                "exerciseType": EX_GROUP, "sportType": SPORT_RUN,
                "targetType": TARGET_TIME, "targetValue": one, "targetDisplayUnit": 0,
                "intensityType": INT_NONE, "intensityValue": 0, "intensityValueExtend": 0,
                "sets": st.sets, "sortNo": base, "restType": REST_NONE, "restValue": 0,
                "groupId": "0", "isGroup": True, "originId": "0",
            })
            exercises.extend(children)
            total += one * st.sets
        else:
            exercises.append(_exercise(st, ex_id, base, "0", th, legacy_percent))
            total += st.seconds
    return {
        "id": "0", "idInPlan": "0", "authorId": "0", "userId": "0", "createTimestamp": 0,
        "name": name[:MAX_NAME], "overview": overview[:200], "sportType": SPORT_RUN,
        "access": 1, "deleted": 0, "status": 1, "version": 0, "pbVersion": PB_VERSION,
        "simple": False, "trainingLoad": 0,
        "estimatedTime": total, "duration": total, "estimatedDistance": 0, "distance": 0,
        "estimatedType": 0, "distanceDisplayUnit": 3,
        "targetType": TARGET_TIME, "targetValue": total,
        "exerciseNum": len(exercises), "totalSets": len(exercises),
        "exercises": exercises,
    }


@dataclass
class WorkoutSpec:
    session_id: str
    day: str                      # ISO date
    name: str
    payload: dict
    fingerprint: str
    # 「負荷」 steps sent with COROS's load target [{i, n, tss, tl}] (engine/coros_tl.record_push)
    load_steps: list = field(default_factory=list)
    # the fingerprint this workout had before SP-67 (HR steps then carried intensityPercent):
    # a row holding it is the same workout on COROS — owner 2026-10-05 「已推送的課不用重推」.
    # Not needed once no workout pushed before 2026-10-05 is still ahead
    legacy_fingerprint: Optional[str] = None

    def pushed_as(self, fp: Optional[str]) -> bool:
        """`fp` (coros_plan_push.fingerprint) is this workout, in today's or the earlier form."""
        return bool(fp) and fp in (self.fingerprint, self.legacy_fingerprint)


def workout_name(s: dict) -> str:
    d = dt.date.fromisoformat(s["day"])
    return f"{NAME_PREFIX} {s['title']} {d.month}/{d.day}"[:MAX_NAME]


def session_workout(s: dict, thresholds: Optional[dict], today: Optional[str] = None) -> WorkoutSpec:
    """COROS workout for one plan session (raises Unsupported when not pushed)."""
    if s.get("done"):
        raise Unsupported("已完成")
    if not s.get("day"):
        raise Unsupported("本週排不進去")
    if today and s["day"] < today:
        raise Unsupported("日期已過")
    th = Thresholds.of(thresholds)
    sent = _sent_tl(s)
    steps = session_steps(s, th, sent)
    name = workout_name(s)
    payload = build_program(name, steps, th, s.get("detail") or "")
    # the payload is the fingerprint: a TSS → TL refit changes it only for sessions with a
    # 「負荷」 step whose sent TL moved by ≥ WS.TL_RESEND_MIN (a smaller move keeps the TL last
    # pushed, _sent_tl; no other field depends on the conversion)
    fp = _fingerprint(s["day"], payload)
    old = _fingerprint(s["day"], build_program(name, steps, th, s.get("detail") or "", legacy_percent=True))
    return WorkoutSpec(s["id"], s["day"], name, payload, fp, _load_records(s, th, sent),
                       legacy_fingerprint=old if old != fp else None)


def _fingerprint(day: Optional[str], payload: dict) -> str:
    return hashlib.sha256(json.dumps({"day": day, "program": payload}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def _sent_tl(s: dict) -> Optional[dict]:
    """{(tss, basis, if): tl} of the session's 「負荷」 steps as last pushed (engine/coros_tl.py's
    closed-loop record, LOAD_KEY → sessions[key].steps), None without one. Never raises."""
    key = s.get("key")
    if not key or not s.get("steps") or COROS_TARGET_TYPE_LOAD is None:
        return None
    try:
        from backend.engine import coros_tl as TL
        from backend.engine import workout_steps as WS
        rec = ((TL._read(TL.LOAD_KEY) or {}).get("sessions") or {}).get(key) or {}
        out = {}
        for x in rec.get("steps") or []:
            if x.get("tss") is not None and x.get("tl") is not None and x.get("if") is not None:
                out[WS.sent_key(x["tss"], x.get("basis") or "hr", x["if"])] = x["tl"]
        return out or None
    except Exception:                        # noqa: BLE001 — no record: the TL as computed
        return None


def _load_records(s: dict, th: Thresholds, sent: Optional[dict] = None) -> list:
    """The session's 「負荷」 steps as sent natively (closed loop, engine/coros_tl.py)."""
    if COROS_TARGET_TYPE_LOAD is None or not s.get("steps") or s.get("kind") == "notice":
        return []
    from backend.engine import workout_steps as WS
    try:
        st = WS.normalize(s["steps"])
    except WS.StepsError:
        return []
    c = WS.Ctx(cp=th.cp, lthr=th.lthr, aet=th.aet, tpace=th.tpace, basis=_basis(s), hrz=th.hrz, sent_tl=sent,
               walk=th.walk_of(s))
    return WS.load_records(st, c)


# ---------------------------------------------------------------------------
# Training Hub client
# ---------------------------------------------------------------------------

def _day(iso: str) -> str:
    return iso.replace("-", "")


class TrainingHub:
    def __init__(self, token: str, base: str, user_id: str):
        self.token, self.base, self.user_id = token, base, user_id
        self._db, self._athlete_id, self._relogged = None, 1, False

    @classmethod
    async def from_db(cls, db: AsyncSession, athlete_id: int = 1) -> "TrainingHub":
        try:
            token, base, user_id = await _get_token_and_base(db, athlete_id)
        except ValueError as e:                  # session_check already marked an expired login
            raise CorosAuthError(str(e)) from None
        hub = cls(token, base, user_id)
        hub._db, hub._athlete_id = db, athlete_id
        return hub

    async def _call(self, method: str, path: str, *, params=None, body=None):
        """One API call. "Access token is invalid" with a remembered password
        (coros_client.relogin): one automatic login, then this call once more."""
        import time
        t0 = time.monotonic()
        try:
            return await self._call_once(method, path, params=params, body=body)
        except CorosAuthError:
            from backend.sync import session_check
            if self._db is None or self._relogged:
                session_check.mark_expired("coros", self._athlete_id)
                raise
            from backend.sync.coros_client import relogin
            if not await relogin(self._db, self._athlete_id, since=t0):
                session_check.mark_expired("coros", self._athlete_id)
                raise
            self._relogged = True
            self.token, self.base, self.user_id = await _get_token_and_base(self._db, self._athlete_id,
                                                                            auto_relogin=False)
            try:
                return await self._call_once(method, path, params=params, body=body)
            except CorosAuthError:
                session_check.mark_expired("coros", self._athlete_id)
                raise

    async def _call_once(self, method: str, path: str, *, params=None, body=None):
        try:
            async with http.client(timeout=30) as c:
                r = await c.request(method, self.base + path, params=params, json=body,
                                    headers=_headers(self.token, self.user_id))
        except Exception as e:
            raise CorosError(f"{path}: {type(e).__name__}: {e}") from None
        if r.status_code in (401, 403):
            raise CorosAuthError(f"COROS_AUTH_REQUIRED: HTTP {r.status_code} at {path}")
        if r.status_code != 200:
            raise CorosError(f"{path}: HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            raise CorosError(f"{path}: response is not JSON") from None
        if data.get("result") != "0000":
            msg = data.get("message") or f"result={data.get('result')}"
            if "token" in str(msg).lower() or data.get("result") in ("1019", "1030"):
                raise CorosAuthError(f"COROS_AUTH_REQUIRED: {msg}")
            raise CorosError(f"{path}: {msg} (result {data.get('result')})")
        return data.get("data")

    async def add_program(self, payload: dict) -> str:
        pid = await self._call("POST", "/training/program/add", body=payload)
        if isinstance(pid, dict):
            pid = pid.get("id") or pid.get("programId")
        if not pid:
            raise CorosError("/training/program/add: no program id returned")
        return str(pid)

    async def program_detail(self, program_id: str) -> dict:
        d = await self._call("GET", "/training/program/detail",
                             params={"id": program_id, "supportRestExercise": 1})
        if not isinstance(d, dict) or not d:
            raise CorosError(f"program {program_id} not found")
        return d

    async def list_programs(self, name: str = "", limit: int = 100) -> list[dict]:
        d = await self._call("POST", "/training/program/query",
                             body={"name": name, "supportRestExercise": 1, "startNo": 0,
                                   "limitSize": limit, "sportType": 0})
        if isinstance(d, list):
            return d
        return (d or {}).get("list") or [] if isinstance(d, dict) else []

    async def delete_program(self, program_id: str) -> None:
        await self._call("POST", "/training/program/delete", body=[str(program_id)])

    async def query_schedule(self, start_iso: str, end_iso: str) -> dict:
        d = await self._call("GET", "/training/schedule/query",
                             params={"startDate": _day(start_iso), "endDate": _day(end_iso),
                                     "supportRestExercise": 1})
        return d or {}

    async def schedule(self, program: dict, day_iso: str) -> dict:
        """Put `program` (a detail response) on `day_iso`; returns the schedule ids."""
        hd = _day(day_iso)
        sched = await self.query_schedule(day_iso, day_iso)
        ents = sched.get("entities") or []
        id_in_plan = max([int(sched.get("maxIdInPlan") or 0)] +
                         [int(e.get("idInPlan") or 0) for e in ents]) + 1
        sort_no = max([0] + [int(e.get("sortNoInSchedule") or 0) for e in ents
                             if str(e.get("happenDay")) == hd]) + 1
        prog = dict(program)
        prog.pop("exerciseBarChart", None)
        prog["idInPlan"] = id_in_plan
        await self._call("POST", "/training/schedule/update", body={
            "entities": [{"happenDay": hd, "idInPlan": id_in_plan, "sortNoInSchedule": sort_no}],
            "programs": [prog],
            "versionObjects": [{"id": id_in_plan, "status": 1}],
            "pbVersion": PB_VERSION,
        })
        ent = await self.find_entry(day_iso, day_iso, str(id_in_plan))
        if ent is None:
            raise CorosError("排上行事曆後找不到這筆（schedule/update 沒生效）")
        return ent

    async def find_entry(self, start_iso: str, end_iso: str, id_in_plan: str) -> Optional[dict]:
        # entity.planId (the plan the entry lives in) is not the calendar's top-level id
        sched = await self.query_schedule(start_iso, end_iso)
        for e in sched.get("entities") or []:
            if str(e.get("idInPlan")) == str(id_in_plan):
                return {"plan_id": str(e.get("planId") or sched.get("id") or ""),
                        "id_in_plan": str(e.get("idInPlan")),
                        "plan_program_id": str(e.get("planProgramId") or e.get("idInPlan")),
                        "execute_status": e.get("executeStatus")}
        return None

    async def unschedule(self, plan_id: str, id_in_plan: str, plan_program_id: str) -> None:
        await self._call("POST", "/training/schedule/update", body={
            "versionObjects": [{"id": str(id_in_plan), "planProgramId": str(plan_program_id or id_in_plan),
                                "planId": str(plan_id), "status": 3}],
            "pbVersion": PB_VERSION,
        })


# ---------------------------------------------------------------------------
# push / remove / status
# ---------------------------------------------------------------------------

def _row_view(r: Optional[CorosPlanPush]) -> dict:
    if r is None:
        return {}
    return {"coros_program_id": r.program_id, "coros_plan_id": r.plan_id,
            "coros_id_in_plan": r.id_in_plan, "pushed_day": r.day,
            "pushed_at": r.pushed_at.isoformat() if r.pushed_at else None, "error": r.error}


class Executed(Exception):
    """The COROS calendar entry was already done: leave it alone."""


def _week_range(r: CorosPlanPush) -> tuple[str, str]:
    try:
        a = dt.date.fromisoformat(r.week_start)
        return a.isoformat(), (a + dt.timedelta(days=6)).isoformat()
    except (TypeError, ValueError):
        return r.day, r.day


RECHECK_DELAY_S = 1.5        # SP-358: the pause before looking for a deleted calendar entry again (tests: 0)


async def _remove_remote(hub: TrainingHub, r: CorosPlanPush) -> Optional[str]:
    """Take a pushed session off the calendar and out of the library. Parts that
    are already gone (deleted in the COROS app) are skipped; an entry the
    athlete already did (executeStatus != 0) is never removed. Returns the day
    when COROS accepted the calendar delete but still lists the entry a moment
    later (SP-358): a reminder to check in the COROS app, not a failure."""
    check = None
    if r.id_in_plan:
        a, b = _week_range(r)            # the whole week: it may have been moved in the app
        if r.day and not a <= r.day <= b:
            a = b = r.day
        ent = await hub.find_entry(a, b, r.id_in_plan)
        if ent is not None:
            if ent.get("execute_status"):
                raise Executed(r.session_id)
            await hub.unschedule(ent["plan_id"], ent["id_in_plan"], ent["plan_program_id"])
            # SP-358: COROS answering 0000 is not proof — look once more after a short pause.
            # Still listed: a reminder (the owner checks real deletes first), logged; no retry
            if RECHECK_DELAY_S:
                await asyncio.sleep(RECHECK_DELAY_S)
            if await hub.find_entry(a, b, r.id_in_plan) is not None:
                check = r.day or a
                log.warning("COROS calendar delete accepted but the entry is still listed: session=%s day=%s",
                            r.session_id, check)
        r.plan_id = r.id_in_plan = r.plan_program_id = None
    if r.program_id:
        # program/detail still answers for deleted programs, flagged deleted=1
        try:
            d = await hub.program_detail(r.program_id)
            exists = str(d.get("id", r.program_id)) == str(r.program_id) and not d.get("deleted")
        except CorosAuthError:
            raise
        except CorosError:
            exists = False
        if exists:
            await hub.delete_program(r.program_id)
        r.program_id = None
    return check


def status_of(s: dict, thresholds: Optional[dict], row: Optional[CorosPlanPush],
              today: Optional[str] = None) -> dict:
    """Offline status for one session: done / skipped / not_pushed / pushed /
    outdated (plan changed since the push, or now not pushable) / failed."""
    out = {"id": s["id"], "title": s.get("title"), "day": s.get("day")}
    try:
        spec = session_workout(s, thresholds, today)
    except Unsupported as e:
        if s.get("done"):
            return {**out, "status": "done", "reason": str(e), **_row_view(row)}
        if (row is not None and (row.program_id or row.id_in_plan) and s.get("day") is None
                and not (today and row.day and row.day < today)):
            return {**out, "status": "outdated", "reason": f"{e}，推送時會從 COROS 移除", **_row_view(row)}
        return {**out, "status": "skipped", "reason": str(e), **_row_view(row)}
    if row is None:
        return {**out, "status": "not_pushed", "name": spec.name}
    if row.status == "failed":
        return {**out, "status": "failed", "name": spec.name, **_row_view(row)}
    st = "pushed" if spec.pushed_as(row.fingerprint) else "outdated"
    return {**out, "status": st, "name": spec.name, **_row_view(row)}


async def _record_load(db: AsyncSession, athlete_id: int, s: dict, spec: WorkoutSpec) -> None:
    """The planned TSS / sent TL of the pushed load steps, for the closed-loop correction once the
    session is run (engine/coros_tl.py LOAD_KEY; one entry per session key). Never fails a push."""
    try:
        from backend.engine import coros_tl as TL
        from backend.settings.repository import SettingsRepository
        repo = SettingsRepository(db, athlete_id)
        old = await repo.get(TL.LOAD_KEY)
        new = TL.record_push(old, s["key"], spec.day, spec.load_steps)
        if new != old:
            await repo.set(TL.LOAD_KEY, new)
            await db.commit()
            TL.forget_reads()
    except Exception as e:                       # noqa: BLE001
        await db.rollback()
        log.warning("load step record failed: %s", type(e).__name__)


def _monday(day: Optional[str], default: Optional[str]) -> Optional[str]:
    if not day:
        return default
    d = dt.date.fromisoformat(day)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def _mine():
    # rows written before the provider column (NULL) are COROS's
    return or_(CorosPlanPush.provider == PROVIDER, CorosPlanPush.provider.is_(None))


async def rows_by_key(db: AsyncSession, athlete_id: int, keys) -> dict[str, CorosPlanPush]:
    keys = list(keys)
    if not keys:
        return {}
    res = await db.execute(select(CorosPlanPush).where(CorosPlanPush.athlete_id == athlete_id, _mine(),
                                                       CorosPlanPush.session_key.in_(keys)))
    return {r.session_key: r for r in res.scalars().all()}


async def all_rows(db: AsyncSession, athlete_id: int = 1) -> dict[str, CorosPlanPush]:
    """The week plan's pushed sessions. Workouts pushed from outside the plan (the race
    calculator's old 「匯出到 COROS」, RACE_KEY_PREFIX) are not the plan's: never listed
    here. Since the calculator exports to the 課表 instead (plan_sessions.ext_key = the same
    racecalc:<event id>), the plan's push removes such a workout when it pushes the
    exported session (api/plan_sessions.push), so the watch never has both."""
    res = await db.execute(select(CorosPlanPush).where(CorosPlanPush.athlete_id == athlete_id, _mine(),
                                                       CorosPlanPush.session_key.notlike(RACE_KEY_PREFIX + "%")))
    return {r.session_key: r for r in res.scalars().all()}


RACE_KEY_PREFIX = "racecalc:"     # a race-calculator export's key (one per event): coros_plan_push.session_key of
                                  # the old direct push; plan_sessions.ext_key of the 「匯出至課表」 row


def library_name(s: dict) -> str:
    return f"{NAME_PREFIX} {s['title']}"[:MAX_NAME]


async def push_workout(db: AsyncSession, s: dict, thresholds: Optional[dict], today: str, *,
                       athlete_id: int = 1, hub: Optional[TrainingHub] = None) -> dict:
    """One workout outside the week plan (the race calculator's race plan): `s` is a
    session dict with "key", "id", "title", "steps" and "day" (None = no date).
    A day from today on: put on that day like a plan session (push_sessions, idempotent
    per key). No day / a past day: only into the COROS library, replacing this key's
    earlier workout. Either way exporting again updates the same workout."""
    if s.get("day") and s["day"] >= today:
        res = await push_sessions(db, [s], thresholds, today, athlete_id=athlete_id, hub=hub)
        return {**res["sessions"][0], "scheduled": True}
    th = Thresholds.of(thresholds)
    steps = session_steps(s, th)
    name = library_name(s)
    payload = build_program(name, steps, th, s.get("detail") or "")
    fp = _fingerprint(None, payload)
    # the same workout as exported before SP-67 (WorkoutSpec.legacy_fingerprint)
    old = _fingerprint(None, build_program(name, steps, th, s.get("detail") or "", legacy_percent=True))
    out = {"id": s["id"], "title": s.get("title"), "day": None, "name": name, "scheduled": False}
    async with _push_lock:
        hub = hub or await TrainingHub.from_db(db, athlete_id)
        row = (await rows_by_key(db, athlete_id, [s["key"]])).get(s["key"])
        if row is not None and row.status == "pushed" and row.fingerprint in (fp, old) and row.program_id:
            return {**out, "status": "pushed", "changed": False, **_row_view(row)}
        replacing = row is not None and (row.program_id or row.id_in_plan)
        if replacing:
            try:
                await _remove_remote(hub, row)
            except Executed:
                pass                          # done on the watch: leave that entry, add the new one
        if row is None:
            row = CorosPlanPush(athlete_id=athlete_id, provider=PROVIDER, session_key=s["key"],
                                week_start=_monday(today, today), session_id=s["id"])
            db.add(row)
        row.title, row.fingerprint, row.status, row.error, row.day = s.get("title"), fp, "failed", None, None
        row.plan_id = row.id_in_plan = row.plan_program_id = None
        try:
            row.program_id = await hub.add_program(payload)
            row.status = "pushed"
            row.pushed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            await db.commit()
        except CorosAuthError:
            row.error = "COROS 登入過期，請重新登入"
            await db.commit()
            raise
        except CorosError as e:
            row.error = str(e)[:500]
            await db.commit()
            return {**out, "status": "failed", "error": row.error, **_row_view(row)}
        return {**out, "status": "updated" if replacing else "pushed", "changed": True, **_row_view(row)}


async def _push_one(db, hub: TrainingHub, athlete_id: int, s: dict,
                    thresholds: Optional[dict], row: Optional[CorosPlanPush],
                    today: Optional[str] = None) -> dict:
    """Push one session; `s["key"]` identifies it in coros_plan_push."""
    out = {"id": s["id"], "title": s.get("title"), "day": s.get("day")}
    try:
        spec = session_workout(s, thresholds, today)
    except Unsupported as e:
        if s.get("done"):
            # done sessions stay on the COROS calendar as they are
            return {**out, "status": "done", "reason": str(e), **_row_view(row)}
        if row is not None and s.get("day") is None:
            # pushed earlier, now dropped from this week's days: take it off COROS
            return {**(await _remove_row(db, hub, row, today)), "reason": str(e)}
        return {**out, "status": "skipped", "reason": str(e), **_row_view(row)}
    if row is not None and row.status == "pushed" and spec.pushed_as(row.fingerprint):
        return {**out, "status": "pushed", "name": spec.name, "changed": False, **_row_view(row)}
    replacing = row is not None and (row.program_id or row.id_in_plan)
    check = None                                  # the old entry's day if COROS may still list it (SP-358)
    if replacing:
        try:
            check = await _remove_remote(hub, row)
        except Executed:
            # done on the watch but not synced here yet: keep it, don't re-create
            return {**out, "status": "done", "reason": "COROS 上已完成", **_row_view(row)}
        except CorosAuthError:
            raise
        except CorosError as e:
            row.status, row.error = "failed", f"更新時刪除舊的失敗：{e}"[:500]
            await db.commit()
            return {**out, "status": "failed", "name": spec.name, "error": row.error, **_row_view(row)}
    if row is None:
        row = CorosPlanPush(athlete_id=athlete_id, provider=PROVIDER, session_key=s["key"],
                            week_start=_monday(spec.day, s.get("week_start")), session_id=s["id"])
        db.add(row)
    row.title, row.fingerprint, row.status, row.error = s.get("title"), spec.fingerprint, "failed", None
    try:
        row.day = spec.day
        row.week_start = _monday(spec.day, row.week_start)
        row.program_id = await hub.add_program(spec.payload)
        await db.commit()                         # the id is safe even if scheduling fails
        detail = await hub.program_detail(row.program_id)
        ent = await hub.schedule(detail, spec.day)
        row.plan_id, row.id_in_plan, row.plan_program_id = ent["plan_id"], ent["id_in_plan"], ent["plan_program_id"]
        row.status = "pushed"
        row.pushed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        await db.commit()
        if spec.load_steps:
            await _record_load(db, athlete_id, s, spec)
        return {**out, "status": "updated" if replacing else "pushed", "name": spec.name,
                "changed": True, **_row_view(row), **({"check_day": check} if check else {})}
    except CorosAuthError:
        row.error = "COROS 登入過期，請重新登入"
        await db.commit()
        raise
    except CorosError as e:
        log.warning("COROS push %s failed: %s", s["id"], e)
        row.status, row.error = "failed", str(e)[:500]
        await db.commit()
        return {**out, "status": "failed", "name": spec.name, "error": row.error, **_row_view(row)}


async def push_sessions(db: AsyncSession, sessions: list[dict], thresholds: Optional[dict], today: str,
                        *, stale_keys=(), missed_keys=(), athlete_id: int = 1,
                        hub: Optional[TrainingHub] = None) -> dict:
    """Push `sessions` (each with a unique "key"). Idempotent per key.
    `stale_keys`: pushed sessions no longer in the plan — removed unless on a
    past day. `missed_keys`: sessions the athlete missed — removed from the
    calendar (still never an entry COROS shows as done)."""
    async with _push_lock:
        hub = hub or await TrainingHub.from_db(db, athlete_id)
        keys = {s["key"] for s in sessions}
        rows = await rows_by_key(db, athlete_id, keys | set(stale_keys) | set(missed_keys))
        results = []
        for s in sessions:
            results.append(await _push_one(db, hub, athlete_id, s, thresholds, rows.get(s["key"]), today))
        removed = []
        for k in stale_keys:
            if k in rows and k not in keys:
                removed.append(await _remove_row(db, hub, rows[k], today))
        for k in missed_keys:
            if k in rows and k not in keys:
                removed.append({**(await _remove_row(db, hub, rows[k])), "missed": True})
        return {"sessions": results, "removed": removed}


async def remove_keys(db: AsyncSession, keys, athlete_id: int = 1,
                      hub: Optional[TrainingHub] = None) -> list[dict]:
    """Remove these pushed sessions from COROS (explicit user action)."""
    async with _push_lock:
        rows = await rows_by_key(db, athlete_id, keys)
        if not rows:
            return []
        hub = hub or await TrainingHub.from_db(db, athlete_id)
        return [await _remove_row(db, hub, r) for r in rows.values()]


def real_today() -> dt.date:
    return dt.date.today()


async def _remove_row(db: AsyncSession, hub: TrainingHub, r: CorosPlanPush,
                      keep_before: Optional[str] = None) -> dict:
    """Remove one pushed session. Automatic removals (the session left the
    plan) pass `keep_before`=today: entries on past days are kept, since the
    athlete may have done them."""
    out = {"id": r.session_id, "title": r.title, "day": r.day}
    if keep_before and r.day and r.day < keep_before:
        return {**out, "status": "kept", "reason": "日期已過，保留在 COROS"}
    try:
        check = await _remove_remote(hub, r)
    except Executed:
        await db.commit()
        return {**out, "status": "kept", "reason": "COROS 上已完成，不移除"}
    except CorosAuthError:
        raise
    except CorosError as e:
        r.status, r.error = "failed", f"刪除失敗：{e}"[:500]
        await db.commit()
        return {**out, "status": "failed", "error": r.error}
    await db.delete(r)
    await db.commit()
    return {**out, "status": "removed", **({"check_day": check} if check else {})}
