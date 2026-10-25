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

Applied after the week is placed, like engine/technical.py, in week_plan and in every projected week
(the same rule, so reconcile never flips a week back and forth), after the heat pass: the longest
easy run that is not done / the heat session (a day before the long run or a quality session last —
a soft preference, 推估). Not for 主要訓練項目 路跑, the 專項期 (the race's own climbs: specific_phase's
長爬坡反覆), the re-entry block or while an injury rule avoids climbing (SP-270). The planned climb
(`climb_m`) is the uphill minutes × CLIMB_M_PER_H (推估: a walk-run on 8–15 % at easy HR) — the
課表's 坡度走路提示 (engine/walk_hint.py) shows the athlete's own climbing rate next to it.

跑下山 (SP-442, owner 2026-10-13: no separate 下坡課 — the downhill goes into the climbing sessions;
it replaces SP-99 / SP-87's engine/downhill.py): the way down is run, not walked — a gentle −5～−10 %
downhill at RPE 3–5 (no HR / power target: both are unreliable downhill; the RPE gives the structure
its IF, as SP-409's 長爬坡反覆), DOWN_FIRST_WORK the base phase's first week, then DOWN_WORK (the old
基礎期 downhill dose: the report's 10–15′), every week, out of the climb's minutes (the session's time
stays). Only when the next A race descends clearly (≥ MIN_DESCENT_M_PER_KM; no A race ahead = a trail
athlete between races keeps it, as the old dose did); a road / flat A race, 賽前第 1–2 週, the race's
real 減量期, its post-race days, the easy days after a B2B or an injury rule avoiding downhill → walking
down as before. Why: the protection of one downhill run lasts 3–6 weeks and is gone by 9 (Bontemps
2020), 10 runs cut the soreness and 5 did not (Bontemps 2025) — a small dose before the 專項期, whose
長爬坡反覆 already runs down at race grade. On a treadmill or stairs the athlete walks down: that week
has no downhill (the detail says so). Doses are 推估.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _

ID = "climb_base"                   # the session id (gen_key); not 「climb」 = the 專項期's race-climb repeats
WARM, COOL = 10, 10                 # 推估: flat warm-up / walking down (the template's 15 / 15 for 90′)
MIN_WORK = 20                       # 推估: a shorter climb is not worth taking the run's place
MIN_MINUTES = WARM + MIN_WORK + COOL
CLIMB_M_PER_H = 400.0               # 推估: easy-HR walk-run on 8–15 % (stairs / treadmill) — the planned climb only
SKIP_IDS = ("climb", "steep", "tech", ID)
# SP-905: a session's 出處 is read part by part (engine/stored_text) — the parts are msgids
SRC_PARTS = (N_("Uphill Athlete（基礎期 90% 以上在一、二區；沒有山就用跑步機坡度、樓梯）"),
             N_("Scott Johnston（樓梯機、陡坡健行）"), N_("CTS（跑步機適合上坡課）"),
             N_("每週一堂、取代一次輕鬆跑、爬升速度為推估"))
SRC = "；".join(SRC_PARTS)
# 跑下山 (SP-442; the old 基礎期 downhill dose, SP-87 decision 3)
DOWN_FIRST_WORK = 10                # 推估: the base phase's first week (the first bout damages most)
DOWN_WORK = 15                      # 推估: the report's 10–15′
DOWN_MIN_WORK = 10                  # 推估: a shorter run-down isn't worth it — walk down instead
DOWN_RPE = (3, 5)
DOWN_M_PER_MIN = 10.0               # 推估: −5～−10 % at an easy jog
MIN_DESCENT_M_PER_KM = 20.0         # 推估: a race descending ≥ 20 m/km has a clear descent (trail ~25–35 m/km)
LAST_WEEKS = 2                      # SP-87: no downhill in 賽前第 1–2 週 (owner 2026-10-05)
DOWN_LAST_DAYS = 14                 # 推估: never closer than 14 days to the race (the 減量期 has none, SP-96)
SRC_DOWN_PARTS = (N_("下山用跑的：下坡跑的保護效果（重複回合效應）維持 3–6 週、9 週消失（Bontemps 2020）"),
                  N_("10 次下坡跑才降低痠痛、5 次不夠（Bontemps 2025）"), N_("每週一次、10–15 分為推估"))
SRC_DOWN = "；".join(SRC_DOWN_PARTS)


def _src(*parts) -> str:
    return "；".join(_(p) for p in parts)


def _d(x) -> Optional[dt.date]:
    if not x:
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def week_context(*, kind: str, mode: str, monday: dt.date, road: bool = False,
                 injury: Optional[dict] = None, behind: Optional[str] = None, events=None,
                 phases=None) -> dict:
    """Whether this week gets the 長爬坡有氧: {"active", "monday", "why"}, and when active whether it
    runs down (SP-442): "run_down", "down_work", "down_first", "down_why" (why it walks down),
    "race", "descent_per_km", "weeks_out", "taper_start", "post_race". `injury`:
    injuries.condition_week's rule (SP-270) — a rule that avoids 「climb」 = none, 「downhill」 = walking
    down. `behind`: the day of this week's stored one when it is already behind (plan_store.hook_days,
    week_plan) — the week has had it, no second one. `events` / `phases`: the plan's (the next A race,
    the base phase's first week, the race's 減量期)."""
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
    return {**info, "active": True, **down_context(monday, events, phases, injury)}


def _first_monday(d: dt.date) -> dt.date:
    """The first Monday on / after `d` (a projected week's phase is the one holding its Monday)."""
    return d + dt.timedelta(days=(7 - d.weekday()) % 7)


def _phase_start(phases, monday: dt.date) -> Optional[dt.date]:
    """The first day of the base phase holding `monday` (Phase objects or dicts), else None."""
    for p in phases or ():
        k = p["kind"] if isinstance(p, dict) else p.kind
        s, e = (_d(p["start"]), _d(p["end"])) if isinstance(p, dict) else (_d(p.start), _d(p.end))
        if k == "base" and s is not None and e is not None and s <= monday <= e:
            return s
    return None


def descent_per_km(e) -> float:
    """The race's descent per km (overview._descent_per_km: the stored GPX, else descent = climb)."""
    from backend.engine.overview import _descent_per_km
    return _descent_per_km(e)


def weeks_out(start: dt.date, monday: dt.date) -> int:
    """賽前第 n 週 of the week starting `monday` (specific_phase.weeks_out)."""
    return -(-(start - monday).days // 7)


def down_context(monday: dt.date, events, phases, injury: Optional[dict] = None) -> dict:
    """Whether this base week's 長爬坡有氧 runs down (SP-442) — the old 基礎期 downhill rule: the next
    A race decides (a road race or one descending < MIN_DESCENT_M_PER_KM → walking down; none ahead →
    running down), never in 賽前第 1–2 週; DOWN_FIRST_WORK the base phase's first week, then
    DOWN_WORK. The day rules (≥ DOWN_LAST_DAYS before the race, not in its 減量期 / post-race days)
    are apply()'s, with the day."""
    walk = {"run_down": False}
    from backend.engine import injuries as INJ
    if INJ.avoids(injury, "downhill"):
        return {**walk, "down_why": "；".join(injury["notes"])}
    ahead = sorted((e for e in events or () if getattr(e, "priority", None) == "A" and e.start > monday),
                   key=lambda e: e.start)
    e = ahead[0] if ahead else None
    out: dict = {}
    if e is not None:
        if e.kind == "road":
            return {**walk, "down_why": _("下一場 A 賽事是路跑：下坡用走的")}
        try:
            dpk = descent_per_km(e)
        except Exception:                   # noqa: BLE001 — the plan must still build
            dpk = 0.0
        out.update(race={"id": e.id, "name": e.name, "start": e.start.isoformat()}, descent_per_km=round(dpk, 1))
        if dpk < MIN_DESCENT_M_PER_KM:
            return {**out, **walk, "down_why": _("賽事下降不明顯（每公里 ↓{d:.0f} m < {min:.0f} m）：下坡用走的",
                                                 d=dpk, min=MIN_DESCENT_M_PER_KM)}
        w = weeks_out(e.start, monday)
        out["weeks_out"] = w
        if w <= LAST_WEEKS:
            return {**out, **walk, "down_why": _("賽前 {n} 週內不跑下坡：下坡用走的", n=LAST_WEEKS)}
    s0 = _phase_start(phases, monday)
    first = s0 is not None and monday == _first_monday(s0)
    # the race's real 減量期 (SP-96 / SP-114, planning.taper_start) and its post-race days: apply()
    # walks down on those days
    t0 = None
    if e is not None:
        from backend.engine import planning as PL
        try:
            t0 = PL.taper_start(phases or (), e)
        except Exception:                   # noqa: BLE001 — the plan must still build
            t0 = None
    return {**out, "run_down": True, "down_first": first, "down_work": DOWN_FIRST_WORK if first else DOWN_WORK,
            "taper_start": t0.isoformat() if t0 else None, "post_race": post_race_days(events, phases, monday)}


def post_race_days(events, phases, monday: dt.date) -> list[str]:
    """The days of the week of `monday` under a race's post-race day rules (engine/post_race.py, SP-98 /
    SP-95): an A race's first week and its flat days after a big downhill, a B race's easy days and
    flat days — no running down there."""
    from backend.engine import post_race as PR
    out = set()
    try:
        wins = PR.a_windows(phases or (), events or (), monday) + PR.b_windows(events or (), monday)
    except Exception:                       # noqa: BLE001 — the plan must still build
        return []
    for w in wins:
        last = max(x for x in (w.get("short"), w.get("easy"), w.get("flat"), w["end"]) if x is not None)
        d = max(w["end"] + dt.timedelta(days=1), monday)
        while d <= min(last, monday + dt.timedelta(days=6)):
            out.add(d.isoformat())
            d += dt.timedelta(days=1)
    return sorted(out)


def steps(work: int, down: Optional[int] = None) -> dict:
    """The 長爬坡有氧 structure (workout_templates long_climb, fitted to `work` uphill minutes): walking
    down as the cool-down, or (`down` minutes, SP-442) running down a gentle slope at RPE 3–5 — no HR /
    power target (SP-409's way: the RPE gives the IF), its descent in the target."""
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    b = WT.B()
    items = [b.warm(WARM, "平路暖身"),
             b.t("work", work * 60, WT.AET, "持續上坡（階梯步道、樓梯、跑步機坡度），跑走混合，心率 ≤ 輕鬆跑上限")]
    if down:
        items.append(b.t("work", down * 60, WT.rpe(*DOWN_RPE, down=descent_of(down)),
                         "下坡用跑的：−5～−10% 緩下坡，放鬆小步"))
    else:
        items.append(b.cool(COOL, "走下來、緩和"))
    return WS.doc(items, origin="template:lib:long_climb")


def climb_of(work: int) -> float:
    """The planned climb (m) of `work` uphill minutes (CLIMB_M_PER_H, 推估), to 10 m."""
    return float(round(work * CLIMB_M_PER_H / 60.0, -1))


def descent_of(down: int) -> int:
    """The run-down's descent (m) of `down` minutes (DOWN_M_PER_MIN, 推估), to 10 m."""
    return int(round(down * DOWN_M_PER_MIN / 10.0) * 10)


def down_minutes(minutes: int, want: int) -> Optional[int]:
    """The run-down that fits a `minutes` session (the climb keeps ≥ MIN_WORK): ≤ `want`, None
    under DOWN_MIN_WORK (walking down)."""
    d = min(int(want), int(minutes or 0) - WARM - MIN_WORK)
    return d if d >= DOWN_MIN_WORK else None


def session_fields(minutes: int, aet: Optional[float] = None, aet_measured: bool = False,
                   down: Optional[int] = None) -> Optional[dict]:
    """The fields that make a run of `minutes` the 長爬坡有氧 of the same time (title, detail,
    terrain, steps, climb_m, source); None when it is shorter than MIN_MINUTES. `down`: minutes of
    running down (SP-442; down_minutes), None = walking down. Also used by the 課表's 「換成長爬坡有氧」
    on a long run (api/plan_sessions, SP-87; walking down)."""
    from backend.engine.hr_profile import easy_cap_label
    m = int(minutes or 0)
    if m < MIN_MINUTES:
        return None
    cap = easy_cap_label(None, aet, aet_measured)
    if down:
        work = m - WARM - down
        climb = climb_of(work)
        # SP-905: the detail's parts (「；」「。」) are msgids — warmup tops 「暖身 N 分」 up in place, so the
        # whole text is no one msgid any more; each part is read on its own (engine/stored_text)
        return {"title": _("長爬坡有氧 {m}′（跑下山 {d}′）", m=m, d=down), "terrain": "trail",
                "steps": steps(work, down), "climb_m": climb, "source": _src(*SRC_PARTS, *SRC_DOWN_PARTS),
                "detail": _("找穩定、好走的長坡持續往上（階梯步道、樓梯，或跑步機坡度 8–15%），跑走混合，心率不超過{cap}", cap=cap)
                + "；" + _("上坡約 {work} 分、爬升約 {climb:.0f} m（推估）", work=work, climb=climb) + "。"
                + _("下山用跑的：−5～−10% 的緩下坡 {d} 分、約 ↓{down} m，RPE 3–5、放鬆小步不衝，心率、功率在下坡不準，看 RPE",
                    d=down, down=descent_of(down))
                + "；" + _("有下坡路就用跑的下來，在跑步機或樓梯就走下來，這週不算下坡") + "。" + _("暖身 {n} 分", n=WARM)}
    work = m - WARM - COOL
    climb = climb_of(work)
    return {"title": _("長爬坡有氧 {m}′（穩定地形）", m=m), "terrain": "trail", "steps": steps(work),
            "climb_m": climb, "source": _src(*SRC_PARTS),
            "detail": _("找穩定、好走的長坡持續往上（階梯步道、樓梯，或跑步機坡度 8–15%），跑走混合，心率不超過{cap}", cap=cap)
            + "；" + _("上坡約 {work} 分、爬升約 {climb:.0f} m（推估）", work=work, climb=climb) + "；"
            + _("下坡用走的，不衝") + "。" + _("暖身、緩和各 {wc} 分", wc=WARM)}


def _walk_day(info: dict, day: dt.date, b2b: Optional[dict]) -> Optional[str]:
    """Why `day` walks down although the week runs down (SP-442: the old downhill day rules), or None."""
    race = _d((info.get("race") or {}).get("start"))
    taper = _d(info.get("taper_start"))
    if race is not None and (race - day).days < DOWN_LAST_DAYS:
        return _("賽前 {n} 天內不跑下坡", n=DOWN_LAST_DAYS)
    if taper is not None and day >= taper:
        return _("減量期不跑下坡")
    if day.isoformat() in set(info.get("post_race") or ()):
        return _("賽後恢復期不跑下坡")
    post = (b2b or {}).get("post") or {}
    until = _d(post.get("until")) if post else None
    if until is not None and day <= until:
        return _("連續長天之後的輕鬆日不跑下坡")
    return None


def apply(ss: list, info: Optional[dict], *, aet: Optional[float] = None, aet_measured: bool = False,
          notes: Optional[list] = None, b2b: Optional[dict] = None, **_kw) -> list:
    """Turn one placed easy run of a base week into the 長爬坡有氧 of the same time, in place;
    sets info["planned"]. Same day, minutes and TSS (an easy run at the same HR cap; the run-down
    is RPE 3–5, an easy session too). `b2b`: this week's B2B info (no running down on its easy days)."""
    if not info or not info.get("active"):
        return ss
    info["planned"] = []
    if any(s.get("id") == ID for s in ss):
        return ss                                       # one a week
    run = bool(info.get("run_down"))
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race", "long"))]
    # a soft preference (推估): not the day before a hard day; the base phase's first run-down also
    # keeps the 2 days after it free of one (the first bout damages most)
    span = (1, 2) if run and info.get("down_first") else (1,)
    cands = [s for s in ss if s.get("kind") == "easy" and not s.get("done") and s.get("day")
             and s.get("id") not in SKIP_IDS and not s.get("heat")]
    if not cands:
        return ss
    pick = min(cands, key=lambda s: (any((h - _d(s["day"])).days in span for h in hard),
                                     -int(s.get("minutes") or 0), s["day"]))
    d = _d(pick["day"])
    m = int(pick.get("minutes") or 0)
    down, why_walk = None, None
    if run:
        why_walk = _walk_day(info, d, b2b)
        if why_walk is None:
            down = down_minutes(m, int(info.get("down_work") or DOWN_WORK))
            if down is None:
                why_walk = _("時間不夠跑下山（上坡至少 {n} 分）", n=MIN_WORK)
    f = session_fields(m, aet, aet_measured, down=down)
    if f is None:
        if notes is not None:
            notes.append({"level": "info", "src": "base_climb",
                          "text": _("基礎期每週 1 堂長爬坡有氧：這週的輕鬆跑都不到 {need} 分，放不下", need=MIN_MINUTES)})
        return ss
    pick.update(id=ID, kind="easy", **f)
    info["planned"].append({"day": pick["day"], "minutes": m, "climb_m": f["climb_m"],
                            **({"down_min": down} if down else {})})
    if notes is not None:
        text = _("本週一堂長爬坡有氧（{day}，{m} 分、爬升約 {climb:.0f} m）：基礎期每週在穩定地形練爬升"
                 "（階梯步道、樓梯、跑步機坡度），心率不超過輕鬆跑上限，算輕鬆課；時間和原本的輕鬆跑一樣",
                 day=f"{d.month}/{d.day}", m=m, climb=f["climb_m"])
        if down:
            text += _("；跑下山 {d} 分（−5～−10% 緩下坡、RPE 3–5）：下坡跑的保護效果維持 3–6 週、9 週沒做就消失，"
                      "所以專項期之前每週小量練（分鐘數為推估）{first}", d=down,
                      first=_("；這是第一次，量小") if info.get("down_first") else "")
        else:
            why = why_walk or info.get("down_why")
            if why:
                text += _("；下坡用走的（{why}）", why=why)
        notes.append({"level": "info", "src": "base_climb", "text": text})
    return ss


PUBLIC = ("active", "why", "planned", "run_down", "down_work", "down_first", "down_why", "race",
          "descent_per_km", "weeks_out")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}
