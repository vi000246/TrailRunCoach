"""
穩定地形爬升課 in the generated plan (SP-87; docs/research/trail-terrain-and-climb-sessions.md §4 #2,
§5 decision 2, owner 2026-10-05): a 越野跑 athlete's 基礎期 week gets one 長爬坡有氧 — SP-62's template
(workout_templates long_climb: steady climbing, walk-run mixed, HR ≤ the easy-run cap) on steady
ground (stairs, 階梯步道, a treadmill incline) — out of one placed easy run of the same time.

Why: far from the race the least specific work comes first (Koop; Uphill Athlete: ≥ 8 weeks, 90 %+ in
zones 1–2), and stairs / a treadmill / a steady climb are what Johnston, Uphill Athlete and CTS use
to build climbing before the 專項期's race terrain. The low-intensity long climb (≤ the easy cap)
otherwise never showed up in the base phase: 登山王, Koop 上坡 and 長爬坡 were only in the
推薦 list. It is an easy session (HR-capped, no RPE ≥ 7): no 48 h spacing, not in the week's
quality budget, kept in a 3:1 recovery week (Uphill Athlete: the structure stays, the volume drops —
the run is the easy run's own minutes).

Applied after the week is placed, like engine/downhill.py, in week_plan and in every projected week
(the same rule, so reconcile never flips a week back and forth), after the heat and downhill passes:
the longest easy run that is not done / the heat session / the downhill session (a day before the
long run or a quality session last — a soft preference, 推估). Not for 主要訓練項目 路跑, the 專項期
(the race's own climbs: specific_phase's 長爬坡反覆), the re-entry block or while an injury rule
avoids climbing (SP-270). The planned climb (`climb_m`) is the uphill minutes × CLIMB_M_PER_H (推估:
a walk-run on 8–15 % at easy HR) — the 課表's 坡度走路提示 (engine/walk_hint.py) shows the athlete's
own climbing rate next to it.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _

ID = "climb_base"                   # the session id (gen_key); not 「climb」 = the 專項期's race-climb repeats
WARM, COOL = 10, 10                 # 推估: flat warm-up / walking down (the template's 15 / 15 for 90′)
MIN_WORK = 20                       # 推估: a shorter climb is not worth taking the run's place
MIN_MINUTES = WARM + MIN_WORK + COOL
CLIMB_M_PER_H = 400.0               # 推估: easy-HR walk-run on 8–15 % (stairs / treadmill) — the planned climb only
SKIP_IDS = ("climb", "steep", "tech", "downhill", ID)
SRC = ("Uphill Athlete（基礎期 90% 以上在一、二區；沒有山就用跑步機坡度、樓梯）；Scott Johnston（樓梯機、陡坡健行）；"
       "CTS（跑步機適合上坡課）；每週一堂、取代一次輕鬆跑、爬升速度為推估")


def _d(x) -> Optional[dt.date]:
    if not x:
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def week_context(*, kind: str, mode: str, monday: dt.date, road: bool = False,
                 injury: Optional[dict] = None, behind: Optional[str] = None) -> dict:
    """Whether this week gets the 長爬坡有氧: {"active", "monday", "why"}. `injury`:
    injuries.condition_week's rule (SP-270) — a rule that avoids 「climb」 = none. `behind`: the day
    of this week's stored one when it is already behind (plan_store.hook_days, week_plan) — the
    week has had it, no second one."""
    info = {"active": False, "monday": monday.isoformat()}
    if behind:
        b = _d(behind)
        return {**info, "why": _("本週的長爬坡有氧已經排在 {day}，不另外排", day=f"{b.month}/{b.day}")}
    if road:
        return {**info, "why": _("主要訓練項目：路跑")}
    if kind != "base":
        return {**info, "why": _("只在基礎期排（專項期練比賽的坡）")}
    if mode == "reentry":
        return {**info, "why": _("停訓後恢復期不排")}
    from backend.engine import injuries as INJ
    if INJ.avoids(injury, "climb"):
        return {**info, "why": "；".join(injury["notes"])}
    return {**info, "active": True}


def steps(work: int) -> dict:
    """The 長爬坡有氧 structure (workout_templates long_climb, fitted to `work` minutes)."""
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    b = WT.B()
    items = [b.warm(WARM, "平路暖身"),
             b.t("work", work * 60, WT.AET, "持續上坡（階梯步道、樓梯、跑步機坡度），跑走混合，心率 ≤ 輕鬆跑上限"),
             b.cool(COOL, "走下來、緩和")]
    return WS.doc(items, origin="template:lib:long_climb")


def climb_of(work: int) -> float:
    """The planned climb (m) of `work` uphill minutes (CLIMB_M_PER_H, 推估), to 10 m."""
    return float(round(work * CLIMB_M_PER_H / 60.0, -1))


def session_fields(minutes: int, aet: Optional[float] = None, aet_measured: bool = False) -> Optional[dict]:
    """The fields that make a run of `minutes` the 長爬坡有氧 of the same time (title, detail,
    terrain, steps, climb_m, source); None when it is shorter than MIN_MINUTES. Also used by the
    課表's 「換成長爬坡有氧」 on a long run (api/plan_sessions, SP-87)."""
    from backend.engine.hr_profile import easy_cap_label
    m = int(minutes or 0)
    work = m - WARM - COOL
    if m < MIN_MINUTES:
        return None
    climb = climb_of(work)
    cap = easy_cap_label(None, aet, aet_measured)
    return {"title": _("長爬坡有氧 {m}′（穩定地形）", m=m), "terrain": "trail", "steps": steps(work),
            "climb_m": climb, "source": SRC,
            "detail": _("找穩定、好走的長坡持續往上（階梯步道、樓梯，或跑步機坡度 8–15%），跑走混合，心率不超過{cap}；"
                        "上坡約 {work} 分、爬升約 {climb:.0f} m（推估）；下坡用走的，不衝。暖身、緩和各 {wc} 分",
                        cap=cap, work=work, climb=climb, wc=WARM)}


def apply(ss: list, info: Optional[dict], *, aet: Optional[float] = None, aet_measured: bool = False,
          notes: Optional[list] = None, **_kw) -> list:
    """Turn one placed easy run of a base week into the 長爬坡有氧 of the same time, in place;
    sets info["planned"]. Same day, minutes and TSS (an easy run at the same HR cap)."""
    if not info or not info.get("active"):
        return ss
    info["planned"] = []
    if any(s.get("id") == ID for s in ss):
        return ss                                       # one a week
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race", "long"))]
    cands = [s for s in ss if s.get("kind") == "easy" and not s.get("done") and s.get("day")
             and s.get("id") not in SKIP_IDS and not s.get("heat")]
    if not cands:
        return ss
    pick = min(cands, key=lambda s: (any((h - _d(s["day"])).days == 1 for h in hard),
                                     -int(s.get("minutes") or 0), s["day"]))
    f = session_fields(int(pick.get("minutes") or 0), aet, aet_measured)
    if f is None:
        if notes is not None:
            notes.append({"level": "info", "src": "base_climb",
                          "text": _("基礎期每週 1 堂長爬坡有氧：這週的輕鬆跑都不到 {need} 分，放不下", need=MIN_MINUTES)})
        return ss
    pick.update(id=ID, kind="easy", **f)
    d = _d(pick["day"])
    info["planned"].append({"day": pick["day"], "minutes": int(pick["minutes"]), "climb_m": f["climb_m"]})
    if notes is not None:
        notes.append({"level": "info", "src": "base_climb",
                      "text": _("本週一堂長爬坡有氧（{day}，{m} 分、爬升約 {climb:.0f} m）：基礎期每週在穩定地形練爬升"
                                "（階梯步道、樓梯、跑步機坡度），心率不超過輕鬆跑上限，算輕鬆課；時間和原本的輕鬆跑一樣",
                                day=f"{d.month}/{d.day}", m=int(pick["minutes"]), climb=f["climb_m"])})
    return ss


PUBLIC = ("active", "why", "planned")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}
