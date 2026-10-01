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
2 time (s), 5 distance (cm). intensityType 0 none, 2 heart rate, 3 pace
(ms/km), 6 power (W). HR: isIntensityPercent false = absolute bpm in
intensityValue/intensityValueExtend; hrType 3 = the LTHR zone scheme
(intensityPercent = % of LTHR × 1000).

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import CorosPlanPush
from backend.engine.zones import WORKOUT_TARGETS
from backend.sync import http
from backend.sync.coros_client import _get_token_and_base, _headers

log = logging.getLogger(__name__)

SPORT_RUN = 1
EX_GROUP, EX_WARMUP, EX_TRAIN, EX_COOLDOWN, EX_REST = 0, 1, 2, 3, 4
TARGET_OPEN, TARGET_TIME = 1, 2
INT_NONE, INT_HR, INT_POWER = 0, 2, 6
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


class CorosError(Exception):
    """COROS answered with an error (HTTP or result != "0000")."""


class CorosAuthError(CorosError):
    """No usable COROS token: log in again."""


class Unsupported(Exception):
    """This session is not pushed (reason in the message)."""


# ---------------------------------------------------------------------------
# session -> steps
# ---------------------------------------------------------------------------

@dataclass
class Step:
    kind: int                     # EX_WARMUP / EX_TRAIN / EX_COOLDOWN / EX_REST
    seconds: int                  # 0 = open (ends with the lap button)
    intensity: Optional[tuple] = None   # ("hr", lo, hi) | ("power", lo, hi)
    name: str = ""


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

    @classmethod
    def of(cls, t: Optional[dict]) -> "Thresholds":
        t = t or {}
        f = lambda k: float(t[k]) if t.get(k) else None
        return cls(cp=f("cp"), lthr=f("lthr"), aet=f("aet"))


def easy_hr(th: Thresholds) -> Optional[tuple]:
    """Easy / long / hike: heart rate capped at AeT."""
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


def _quality_steps(s: dict, th: Thresholds) -> list[StepLike]:
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
    if hm and "功率" not in target:
        work_int = ("hr", int(hm.group(1)), int(hm.group(2)))
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


def session_steps(s: dict, th: Thresholds) -> list[StepLike]:
    """Structured steps for one week-plan session, or Unsupported."""
    kind = s.get("kind")
    secs = int(s.get("minutes") or 0) * 60
    if kind in ("race", "rest"):
        raise Unsupported("比賽 / 休息不推")
    if kind == "strength":
        raise Unsupported("COROS 肌力課要從動作庫挑動作，先不推")
    if kind == "heat_passive":
        raise Unsupported("被動熱適應不推")
    if kind in ("quality",):
        return _quality_steps(s, th)
    if kind == "test":
        from backend.engine.aet_test import is_aet_session
        return _aet_test_steps(s, th) if is_aet_session(s) else _test_steps(s, th)
    if secs <= 0:
        raise Unsupported("沒有時間長度")
    if kind in ("long", "mountain", "hike"):
        return [Step(EX_TRAIN, secs, easy_hr(th), "心率 ≤ AeT")]
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
                return [Step(EX_TRAIN, base, easy_hr(th), "心率 ≤ AeT"),
                        Repeat(n, [Step(EX_TRAIN, sprint, None, f"{sprint} 秒上坡衝刺"),
                                   Step(EX_REST, recover, None, "走下來")], f"衝刺 {n}×{sprint} 秒")]
        return [Step(EX_TRAIN, secs, easy_hr(th), "心率 ≤ AeT")]
    raise Unsupported(f"不支援的課表類型 {kind}")


# ---------------------------------------------------------------------------
# steps -> COROS program payload
# ---------------------------------------------------------------------------

def _exercise(st: Step, ex_id: int, sort_no: int, group_id: str, th: Thresholds) -> dict:
    ex = {
        "id": ex_id, "name": st.name or STEP_NAME[st.kind], "overview": OVERVIEW[st.kind],
        "exerciseType": st.kind, "sportType": SPORT_RUN,
        "targetType": TARGET_TIME if st.seconds else TARGET_OPEN, "targetValue": st.seconds,
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
            if th.lthr:
                ex["intensityPercent"] = round(lo / th.lthr * 100000)
                ex["intensityPercentExtend"] = round(hi / th.lthr * 100000)
        elif typ == "power":
            ex["intensityType"] = INT_POWER
    return ex


def build_program(name: str, steps: list[StepLike], th: Thresholds, overview: str = "") -> dict:
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
                children.append(_exercise(c, ex_id, base + SORT_CHILD * j, str(gid), th))
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
            exercises.append(_exercise(st, ex_id, base, "0", th))
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
    steps = session_steps(s, th)
    name = workout_name(s)
    payload = build_program(name, steps, th, s.get("detail") or "")
    fp = hashlib.sha256(json.dumps({"day": s["day"], "program": payload}, sort_keys=True,
                                   ensure_ascii=False).encode()).hexdigest()
    return WorkoutSpec(s["id"], s["day"], name, payload, fp)


# ---------------------------------------------------------------------------
# Training Hub client
# ---------------------------------------------------------------------------

def _day(iso: str) -> str:
    return iso.replace("-", "")


class TrainingHub:
    def __init__(self, token: str, base: str, user_id: str):
        self.token, self.base, self.user_id = token, base, user_id

    @classmethod
    async def from_db(cls, db: AsyncSession, athlete_id: int = 1) -> "TrainingHub":
        try:
            token, base, user_id = await _get_token_and_base(db, athlete_id)
        except ValueError as e:
            raise CorosAuthError(str(e)) from None
        return cls(token, base, user_id)

    async def _call(self, method: str, path: str, *, params=None, body=None):
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


async def _remove_remote(hub: TrainingHub, r: CorosPlanPush) -> None:
    """Take a pushed session off the calendar and out of the library. Parts that
    are already gone (deleted in the COROS app) are skipped; an entry the
    athlete already did (executeStatus != 0) is never removed."""
    if r.id_in_plan:
        a, b = _week_range(r)            # the whole week: it may have been moved in the app
        if r.day and not a <= r.day <= b:
            a = b = r.day
        ent = await hub.find_entry(a, b, r.id_in_plan)
        if ent is not None:
            if ent.get("execute_status"):
                raise Executed(r.session_id)
            await hub.unschedule(ent["plan_id"], ent["id_in_plan"], ent["plan_program_id"])
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
    st = "pushed" if row.fingerprint == spec.fingerprint else "outdated"
    return {**out, "status": st, "name": spec.name, **_row_view(row)}


def _monday(day: Optional[str], default: Optional[str]) -> Optional[str]:
    if not day:
        return default
    d = dt.date.fromisoformat(day)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


async def rows_by_key(db: AsyncSession, athlete_id: int, keys) -> dict[str, CorosPlanPush]:
    keys = list(keys)
    if not keys:
        return {}
    res = await db.execute(select(CorosPlanPush).where(CorosPlanPush.athlete_id == athlete_id,
                                                       CorosPlanPush.session_key.in_(keys)))
    return {r.session_key: r for r in res.scalars().all()}


async def all_rows(db: AsyncSession, athlete_id: int = 1) -> dict[str, CorosPlanPush]:
    res = await db.execute(select(CorosPlanPush).where(CorosPlanPush.athlete_id == athlete_id))
    return {r.session_key: r for r in res.scalars().all()}


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
    if row is not None and row.status == "pushed" and row.fingerprint == spec.fingerprint:
        return {**out, "status": "pushed", "name": spec.name, "changed": False, **_row_view(row)}
    replacing = row is not None and (row.program_id or row.id_in_plan)
    if replacing:
        try:
            await _remove_remote(hub, row)
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
        row = CorosPlanPush(athlete_id=athlete_id, session_key=s["key"],
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
        return {**out, "status": "updated" if replacing else "pushed", "name": spec.name,
                "changed": True, **_row_view(row)}
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
        await _remove_remote(hub, r)
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
    return {**out, "status": "removed"}
