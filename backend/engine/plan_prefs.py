"""
課表偏好 — the athlete's training-plan preferences, applied to the generated
week (overview.week_plan) and the projected weeks (projection.project_weeks).

Stored as user_settings keys `plan.prefs.*` (settings/repository.py DEFAULTS
and validation). Every default reproduces the planner's own behaviour:
`Prefs()` is inactive and the callers keep their original code path, so an
athlete who never opens the panel gets exactly today's plan.

Order of application (both callers):

  1. The volume target is computed as before (CTL ramp, <= 10 % step, 3:1).
     A custom weekly-hours cap only lowers it.
  2. The caller builds its usual session template (long, quality / test,
     strength, easy fill). `shape()` then applies the preferences to it:
       * terrain -> title, target, TSS rate (trail easy = HR-only target
         <= AeT: pace and power are unreliable on trail; a hike long day is
         time-based);
       * interval target power / HR;
       * quality count 0 / 1 / 2 (the base-phase drift gate still decides
         whether there is any quality at all — `allow_quality` from the caller);
       * strength count;
       * the week's minutes distributed over the allowed run count, each
         session <= its cap. When the target does not fit (target > count x
         cap): hard -> the week is capped with a note; soft -> the excess goes
         on the long day first and weekday sessions stay within the cap.
  3. `place()` puts the sessions on allowed days (unchecked days are rest
     days), the long session on the chosen long day, strength on the chosen
     days or with easy runs.

Caps and hard sessions: a quality session over the weekday cap is shortened —
warm-up 15 -> 10 min, cool-down 10 -> 5 min, then one rep fewer (never below
2) — and its detail text is rewritten so the COROS step builder still parses
it. The CP test protocol (3' + 30' + 12') is fixed: it is exempt from the cap,
with a note. The AeT drift test picks its length from the weekday cap instead
(engine/aet_test.py variant_for: 80 min, or UA's 50-min minimum under a cap
< 80; never shorter) and its day from aet_test_days (pick_day, weekday first).
The protocol itself (cp_test_protocol, engine/cp_protocols.py) is
not a shaping preference: it is left out of `active`, and week_plan reads it
from the Prefs even when the rest are defaults.

User-edited sessions are never touched: preferences only change what the
generator produces, and reconcile keeps edited / custom sessions (rule 3).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Iterable, Optional

from backend.engine.hr_profile import below
from backend.i18n import _

KEY_FIELDS = {                       # user_settings key -> Prefs field
    "plan.prefs.days": "days",
    "plan.prefs.long_day": "long_day",
    "plan.prefs.cap_weekday": "cap_weekday",
    "plan.prefs.cap_long": "cap_long",
    "plan.prefs.cap_mode": "cap_mode",
    "plan.prefs.runs_per_week": "runs",
    "plan.prefs.quality_per_week": "quality",
    "plan.prefs.strength_per_week": "strength",
    "plan.prefs.strength_days": "strength_days",
    "plan.prefs.weekly_hours": "weekly_hours",
    "plan.prefs.terrain_easy": "terrain_easy",
    "plan.prefs.terrain_long": "terrain_long",
    "plan.prefs.terrain_quality": "terrain_quality",
    "plan.prefs.interval_target": "interval_target",
    "plan.prefs.target_basis": "target_basis",
    "plan.prefs.cp_test_protocol": "cp_test_protocol",
    "plan.prefs.heat": "heat",
    "plan.prefs.heat_method": "heat_method",
    "plan.prefs.quality_gate": "quality_gate",
    "plan.prefs.quality_gate_weeks": "quality_gate_weeks",
    "plan.prefs.aet_test_days": "aet_test_days",
    "plan.prefs.aet_test_protocol": "aet_test_protocol",
    "plan.prefs.warmup_commute_min": "warmup_commute_min",
    "plan.prefs.cooldown_min": "cooldown_min",
    "plan.prefs.pref_days": "pref_days",
    "plan.prefs.pref_keep": "pref_keep",
    "plan.prefs.b2b": "b2b",
    "plan.prefs.transition_weeks": "transition_weeks",
    "plan.prefs.taper_days": "taper_days",
    "plan.prefs.strength_moves": "strength_moves",
    "plan.prefs.strength_no_gear": "strength_no_gear",
}
# 間歇門檻 (engine/quality_gate.py): decides whether base phase gets intervals,
# not how sessions are shaped, so these alone don't switch shape() / place() on
GATE_FIELDS = ("quality_gate", "quality_gate_weeks")
# fields that only add sessions for a specific reason and never reshape the
# week: they are not part of `active` (the default plan stays untouched)
# aet_test_days: where the AeT test goes, applied by every placement path
# (aet_test.pick_day) whether or not the other preferences are set
# warmup_commute_min / cooldown_min: the interval warm-up's city part and the cool-down
# (engine/interval_library.py blocks) — read for every interval session, not shaping
# b2b: whether a due B2B weekend is suggested at all (engine/b2b.py) — a suggestion, not shaping
# transition_weeks: the 轉換期 after an A race (engine/planning.auto_phases) — a phase, not shaping
# taper_days: the 減量期 length of a road marathon / an ultra (planning.taper_days, SP-96) — a phase too
# strength_moves / strength_no_gear: which move each strength type uses (engine/strength_moves.py,
# SP-191) — the strength texts only, read by strength_plan / balance_plan whether or not `active`
NOT_SHAPING = ("cp_test_protocol", "heat", "heat_method", "aet_test_days", "aet_test_protocol",
               "warmup_commute_min", "cooldown_min", "b2b", "transition_weeks", "taper_days",
               "strength_moves", "strength_no_gear") + GATE_FIELDS
WD = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WD_ZH = "一二三四五六日"
LONG_WD = {d: i for i, d in enumerate(WD)}      # 長跑日: any weekday (was sat / sun only)
# rest = 休息日偏好 (SP-82): the weekdays the athlete would rather rest, first / second choice (engine/rest_days.py)
PREF_KINDS = ("quality", "aet_test", "cp_test", "strides", "rest")
PREF_LABEL = {"long": "LSD", "quality": "間歇", "aet_test": "AeT 測試", "cp_test": "CP 測試", "strides": "坡道衝刺／加速跑",
              "rest": "休息日"}
MIN_EASY = 20                        # never generate an easy session shorter than this
TRIM_WARM, TRIM_COOL, MIN_REPS = 10, 5, 2

NOTE_HARD = "受限於你的偏好，本週少 {h} 小時；想補量可以多排一天或放寬長跑日上限"
NOTE_SOFT = "單次上限 {cap} 分：多出的 {m} 分鐘放在長跑日（盡量不超過）"
NOTE_AET_TEST = "AeT 飄移測試至少要 10 分暖身＋40 分固定功率（共 50 分，UA 不建議更短），不受單次時間上限"


def note_test(protocol: Optional[str]) -> str:
    """The cap-exemption note of the CP test, by protocol (cp_protocols.cap_note)."""
    from backend.engine import cp_protocols as CPP
    return CPP.cap_note(protocol)


NOTE_TEST = note_test("standard")      # the 70-min protocol; quick (37 min) rarely hits a cap


@dataclass(frozen=True)
class Prefs:
    days: tuple = (True,) * 7
    long_day: str = "auto"
    cap_weekday: Optional[int] = None
    cap_long: Optional[int] = None
    cap_mode: str = "soft"
    runs: Optional[int] = None
    quality: Optional[int] = None
    strength: Optional[int] = None
    strength_days: tuple = ()
    weekly_hours: Optional[float] = None
    terrain_easy: str = "any"
    terrain_long: str = "auto"
    terrain_quality: str = "any"
    interval_target: str = "power"     # legacy 「間歇目標」: hr reads as target_basis = hr (from_settings)
    # 目標依據 (engine/target_policy.py): auto = by session type (HR for easy / long / trail days,
    # power for intervals and 3–8 % hill repeats), hr = HR zones, power = power zones
    target_basis: str = "auto"
    # CP 測試方式 (engine/cp_protocols.py). Not part of `active`: choosing a
    # protocol only changes the test session, not the shaping of the week.
    cp_test_protocol: str = "quick"
    # 熱適應課 (engine/heat_plan.py, heat-acclimation.md §5.4): auto adds heat
    # sessions only before a hot A/B race; off never. Not part of `active`.
    heat: str = "auto"
    heat_method: str = "run"
    # 間歇門檻 (engine/quality_gate.py): not part of `active` either (GATE_FIELDS)
    quality_gate: str = "auto"
    quality_gate_weeks: int = 8
    # AeT 飄移測試的日子 (engine/aet_test.py pick_day): weekday (default; the
    # athlete trail-runs on weekends) | any. Not part of `active`: it moves one
    # session, it doesn't reshape the week.
    aet_test_days: str = "weekday"
    # AeT 測試方式 (engine/aet_test.py PROTOCOLS): auto = 徐國峰 90 分鐘 (the weekend LSD), UA 40 as the
    # backup; xu90 / ua60 / ua40 / evoke60 / friel. Not part of `active`.
    aet_test_protocol: str = "auto"
    # 間歇的暖身／緩和 (engine/interval_library.py §C3): the ~10-min easy run through the
    # city to the riverside (never cut) and the cool-down (10 when running home). Not `active`.
    warmup_commute_min: int = 10
    cooldown_min: int = 5
    # 偏好的星期 per session type (the long run is long_day, any weekday now): ((kind, (wd1, wd2)), …)
    # for quality / aet_test / cp_test / strides; () = 不指定. pref_keep = the conflict codes
    # (day_conflicts) the athlete chose to keep anyway.
    pref_days: tuple = ()
    pref_keep: tuple = ()
    # 建議 B2B（連續兩天長天，engine/b2b.py）: on = a due B2B weekend is suggested (the user
    # picks the days, 排入 / 不要); off = never suggested. Not part of `active`.
    b2b: bool = True
    # 轉換期 (SP-73; engine/planning.auto_phases): weeks of 轉換期 after an A race's 恢復期, 0 = off.
    # Default 3 (Friel 一般 3–4 週, Canova 4 週; the low end, 推估). Not part of `active`.
    transition_weeks: int = 3
    # 減量期天數 (SP-96; planning.taper_days): 14 (default, Wang 2023: 8–14 days best) up to 21 for a road
    # marathon or an ultra (Strava: 3 weeks > 2; ≥ 22 days no effect). Not part of `active`.
    taper_days: int = 14
    # 肌力動作 (SP-191, engine/strength_moves.py): ((type, move), …) the athlete picked — a type's
    # default is never stored, () = every default; strength_no_gear: the equipment they don't have
    # (bar / band), a move that needs it gives way to one that doesn't. Not part of `active`.
    strength_moves: tuple = ()
    strength_no_gear: tuple = ()

    @property
    def active(self) -> bool:
        """Anything that shapes sessions differs from the defaults (the CP-test
        protocol, the heat and the 間歇門檻 fields don't shape the week)."""
        return replace(self, **{f: getattr(Prefs, f) for f in NOT_SHAPING}) != Prefs()

    @property
    def long_cap(self) -> Optional[int]:
        """長跑日上限; None (同平日) = the weekday cap."""
        return self.cap_long if self.cap_long is not None else self.cap_weekday

    def allowed(self, d: dt.date) -> bool:
        return bool(self.days[d.weekday()])

    def to_dict(self) -> dict:
        d = asdict(self)
        d["days"], d["strength_days"] = list(self.days), list(self.strength_days)
        d["pref_days"] = {k: list(v) for k, v in self.pref_days}
        d["pref_keep"] = list(self.pref_keep)
        d["strength_moves"] = dict(self.strength_moves)
        d["strength_no_gear"] = list(self.strength_no_gear)
        return d

    def pref_of(self, kind: str) -> tuple:
        """The preferred weekdays (first choice, second choice) of a session type; () = 不指定."""
        return next((tuple(v) for k, v in self.pref_days if k == kind), ())

    def stamp(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def settings(self) -> dict:
        """As user_settings keys (days None when every day is allowed)."""
        d = self.to_dict()
        out = {k: d[f] for k, f in KEY_FIELDS.items()}
        if all(self.days):
            out["plan.prefs.days"] = None
        return out


def from_settings(values: dict, lenient: bool = True) -> Prefs:
    """Prefs from {user_settings key: value}; missing / None -> the default.
    `lenient` (stored values): a 間歇門檻 that is no longer a mode — e.g. the
    dropped "xu_signals" (an old unlock path) — reads as "auto"."""
    kw = {}
    for k, f in KEY_FIELDS.items():
        v = values.get(k)
        if v is None:
            continue
        if f in ("days", "strength_days", "pref_keep"):
            v = tuple(v)
        if f == "pref_days":
            v = tuple(sorted((str(k), tuple(int(x) for x in (d or [])[:2])) for k, d in dict(v).items()
                             if k in PREF_KINDS and d))
        if f == "weekly_hours":
            v = float(v)
        if f in ("strength_moves", "strength_no_gear"):
            # a move / an equipment no longer offered: dropped when stored, refused when new (SP-191)
            from backend.engine import strength_moves as SM
            v = (SM.clean_moves if f == "strength_moves" else SM.clean_gear)(v, strict=not lenient)
        if f == "quality_gate" and lenient:
            from backend.engine.quality_gate import MODES
            v = v if v in MODES else "auto"
        kw[f] = v
    # migration: the old 「間歇目標 = 心率」 is 目標依據 = 心率 now (both stay readable)
    if kw.get("interval_target") == "hr" and kw.get("target_basis") in (None, "auto"):
        kw["target_basis"] = "hr"
    if lenient and kw.get("target_basis") not in (None, "auto", "hr", "power"):
        kw["target_basis"] = "auto"
    # 長跑地形「登山」 is gone (hiking is not a workout): a stored one reads as 越野跑 (trail)
    if kw.get("terrain_long") == "hike":
        kw["terrain_long"] = "trail"
    return Prefs(**kw)


def from_body(body: dict) -> Prefs:
    """Prefs from the API body (field names); unknown fields are rejected
    (and an unknown 間歇門檻 too, by check())."""
    names = {f.name for f in fields(Prefs)}
    bad = set(body) - names
    if bad:
        raise ValueError(f"unknown preference(s): {sorted(bad)}")
    return from_settings({k: body.get(f) for k, f in KEY_FIELDS.items()}, lenient=False)


def day_owners(p: Prefs) -> list[tuple]:
    """(kind, weekday) of every chosen 偏好的星期, in priority order: the long run first,
    then PREF_KINDS, each type's first choice before its second."""
    out = [("long", LONG_WD[p.long_day])] if p.long_day in LONG_WD else []
    return out + [(k, wd) for k in PREF_KINDS for wd in p.pref_of(k)]


def overlaps(p: Prefs) -> list[dict]:
    """Weekdays given to two session types: [{"kind", "wd", "owner"}] for each later one
    (`owner` = the type that has the day first, day_owners order)."""
    seen, out = {}, []
    for k, wd in day_owners(p):
        if wd in seen and seen[wd] != k:
            out.append({"kind": k, "wd": wd, "owner": seen[wd]})
        seen.setdefault(wd, k)
    return out


def drop_overlaps(p: Prefs) -> tuple:
    """Stored values from before the one-type-per-weekday rule: keep the first type of a
    shared weekday (day_owners order), drop it from the others. -> (Prefs, overlaps(p))."""
    bad = overlaps(p)
    if not bad:
        return p, []
    drop = {(b["kind"], b["wd"]) for b in bad}
    pd = tuple((k, tuple(wd for wd in v if (k, wd) not in drop)) for k, v in p.pref_days)
    return replace(p, pref_days=tuple(x for x in pd if x[1])), bad


def check(p: Prefs) -> None:
    """Cross-field rules the per-key validation can't see."""
    for b in overlaps(p):
        raise ValueError(_("週{day}已給{taken}，不能再排{kind}（一天只能指定一種課）", day=WD_ZH[b["wd"]],
                           taken=PREF_LABEL[b["owner"]], kind=PREF_LABEL[b["kind"]]))
    n_days = sum(bool(x) for x in p.days)
    if p.runs is not None and p.runs > n_days:
        raise ValueError(_("每週跑步次數 {runs} 比可練日（{days} 天）多", runs=p.runs, days=n_days))
    if p.runs is not None and p.quality is not None and p.quality >= p.runs:
        raise ValueError(_("品質課次數要比每週跑步次數少（至少留一次輕鬆或長跑）"))
    if p.cap_long is not None and p.cap_weekday is not None and p.cap_long < p.cap_weekday:
        raise ValueError(_("長跑日上限不能比平日上限短"))
    from backend.engine.quality_gate import MODES, WEEKS_RANGE
    if p.quality_gate not in MODES:
        raise ValueError(_("間歇門檻要是 {choices} 其中之一", choices=MODES))
    if not isinstance(p.b2b, bool):
        raise ValueError(_("建議 B2B 要是 true／false"))
    if p.aet_test_days not in ("weekday", "any"):
        raise ValueError(_("AeT 測試日要是 weekday 或 any"))
    from backend.engine.aet_test import PROTOCOL_CHOICES
    if p.aet_test_protocol not in PROTOCOL_CHOICES:
        raise ValueError(_("AeT 測試方式要是 {choices} 其中之一", choices=PROTOCOL_CHOICES))
    from backend.engine.planning import TRANSITION_WEEKS_RANGE as TR
    if isinstance(p.transition_weeks, bool) or not isinstance(p.transition_weeks, int) or \
            not TR[0] <= p.transition_weeks <= TR[1]:
        raise ValueError(_("轉換期週數要在 {lo}–{hi} 週（0 = 關閉）", lo=TR[0], hi=TR[1]))
    from backend.engine.planning import TAPER_DAYS_RANGE as TD
    if isinstance(p.taper_days, bool) or not isinstance(p.taper_days, int) or not TD[0] <= p.taper_days <= TD[1]:
        raise ValueError(_("減量期天數要在 {lo}–{hi} 天", lo=TD[0], hi=TD[1]))
    if isinstance(p.quality_gate_weeks, bool) or not isinstance(p.quality_gate_weeks, int) or \
            not WEEKS_RANGE[0] <= p.quality_gate_weeks <= WEEKS_RANGE[1]:
        raise ValueError(_("週數法的週數要在 {lo}–{hi} 週", lo=WEEKS_RANGE[0], hi=WEEKS_RANGE[1]))


def load(user_id: int = 1) -> Prefs:
    """Synchronous read of the stored preferences (read-only sqlite, like
    wko5expr.datasource) — the planner runs in a worker thread."""
    from backend.engine.wko5expr.datasource import read_setting
    vals = {k: read_setting(k, None, user_id) for k in KEY_FIELDS}
    try:
        return drop_overlaps(from_settings(vals))[0]
    except (TypeError, ValueError):
        return Prefs()


# ---------------------------------------------------------------------------
# shaping the session template
# ---------------------------------------------------------------------------

def _r5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def _hr_part(target: str) -> str:
    parts = [p.strip() for p in (target or "").split("·")]
    hr = [p for p in parts if p.startswith("心率")]
    return " · ".join(hr) if hr else (target or "")


def easy_hr_text(aet: Optional[float], measured: bool = False) -> str:
    """「心率 ≤ 輕鬆跑上限 N bpm」 (hr_profile.easy_cap_hr; `aet` = the easy cap)."""
    from backend.engine.hr_profile import easy_cap_hr
    return easy_cap_hr(aet, measured)


def trim_quality(s: dict, cap: int) -> bool:
    """Shorten a quality session to `cap` minutes (warm-up, cool-down, then
    reps); rewrite title / detail so the COROS step builder parses the new
    structure. True when it now fits."""
    m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*分", s["title"])
    if not m or s["minutes"] <= cap:
        return s["minutes"] <= cap
    reps, work = int(m.group(1)), int(m.group(2))
    det = s.get("detail") or ""
    rest = re.search(r"休\s*(?:\d+\s*[–-]\s*)?(\d+)\s*分", det)
    rest = int(rest.group(1)) if rest else work
    warm = re.search(r"暖身\s*(\d+)\s*分", det)
    cool = re.search(r"緩和\s*(\d+)\s*分", det)
    warm_v, cool_v = (int(warm.group(1)) if warm else 15), (int(cool.group(1)) if cool else 10)
    old = s["minutes"]
    over = old - cap
    cut_w = min(over, max(0, warm_v - TRIM_WARM))
    warm_v -= cut_w
    over -= cut_w
    cut_c = min(over, max(0, cool_v - TRIM_COOL))
    cool_v -= cut_c
    over -= cut_c
    new_reps = reps
    while over > 0 and new_reps > MIN_REPS:
        new_reps -= 1
        over -= work + rest
    minutes = old - cut_w - cut_c - (reps - new_reps) * (work + rest)
    if new_reps != reps:
        s["title"] = s["title"][:m.start()] + f"{new_reps}×{work} 分" + s["title"][m.end():]
    if warm:
        det = det[:warm.start()] + f"暖身 {warm_v} 分" + det[warm.end():]
    cool = re.search(r"緩和\s*(\d+)\s*分", det)
    if cool:
        det = det[:cool.start()] + f"緩和 {cool_v} 分" + det[cool.end():]
    if not warm and not cool:
        det = (det + "；" if det else "") + f"暖身 {warm_v} 分、緩和 {cool_v} 分"
    s["minutes"] = int(minutes)
    s["detail"] = det
    if old:
        s["tss"] = float(s.get("tss") or 0.0) * s["minutes"] / old
    return s["minutes"] <= cap


@dataclass
class Ctx:
    """What shape() needs from the caller."""
    kind: str                         # phase kind (base / specific / taper / …)
    mode: str                         # week mode (… / recovery_week)
    allow_quality: bool               # the caller's quality gate (drift streak in base)
    rates: dict                       # TSS per hour: road / trail / hike / strength
    aet: Optional[float] = None       # the easy-run HR cap (課表心率區間 Z2 top, or a measured AeT)
    slots: int = 7                   # allowed days for main sessions
    notes: list = field(default_factory=list)
    quality_cap: Optional[int] = None  # 間歇門檻 guardrail mode: base phase ≤ 1 (engine/quality_gate.py)
    aet_measured: bool = False        # the cap is a measured AeT (「（實測 AeT）」 in the texts)

    def cap(self) -> str:
        """「輕鬆跑上限 N bpm」 (hr_profile.easy_cap_label)."""
        from backend.engine.hr_profile import easy_cap_label
        return easy_cap_label(None, self.aet, self.aet_measured)

    def rate(self, cat: str) -> float:
        return float(self.rates.get(cat) or self.rates.get("road") or 50.0)


def _terrain_long(s: dict, p: Prefs, c: Ctx) -> None:
    t = "trail" if p.terrain_long == "hike" else p.terrain_long      # the old 登山 = 越野跑
    if t == "auto" or (t == "road" and "馬拉松配速" in (s.get("title") or "")):
        return                  # (a 路跑 專項期 long run with its marathon-pace segment is road already)
    s["terrain"] = t
    if t == "road":
        s["title"] = "LSD（路跑）"
        s["detail"] = f"平路或緩坡；全程心率壓在{below(c.cap())}"
    else:
        s["title"] = "LSD（山路越野）"
        s["target"] = easy_hr_text(c.aet, c.aet_measured)
        s["detail"] = f"山路越野，陡坡用走的；只看心率（≤ {c.cap()}），山路的配速和功率不準"


def _easy(template: Optional[dict], i: int, minutes: float, p: Prefs, c: Ctx) -> dict:
    base = dict(template) if template else {
        "kind": "easy", "title": "輕鬆跑", "target": "", "detail": f"心率不超過{c.cap()}", "source": "Uphill Athlete",
        "day": None, "done": False, "done_by": None}
    strides = i == 0 and any(w in (template or {}).get("title", "") for w in ("衝刺", "加速跑"))
    s = {**base, "id": f"easy{i + 1}", "kind": "easy", "minutes": _r5(minutes), "day": None,
         "done": False, "done_by": None}
    if not strides:
        s["title"] = "輕鬆跑"
        s["detail"] = f"心率不超過{c.cap()}"
    t = p.terrain_easy
    if t == "trail":
        from backend.engine.overview import TRANSITION_STRIDES
        tr = strides and TRANSITION_STRIDES[0] in (template or {}).get("title", "")    # 轉換期 (SP-103): kept as is
        s["terrain"] = "trail"
        s["title"] = "輕鬆越野跑" + ((TRANSITION_STRIDES[0] if tr else "＋坡道衝刺 8×10 秒") if strides else "")
        s["target"] = easy_hr_text(c.aet, c.aet_measured)
        s["detail"] = f"山路或步道；只看心率 ≤ {c.cap()}，配速和功率在山路不準" + \
            ((TRANSITION_STRIDES[1] if tr else "；最後 8 趟 10 秒上坡衝刺，走下來恢復") if strides else "")
        cat = "trail"
    else:
        if t == "road":
            s["terrain"] = "road"
            s["title"] = s["title"].replace("輕鬆跑", "輕鬆跑（路跑）", 1)
        cat = "road"
    s["tss"] = s["minutes"] / 60.0 * c.rate(cat)
    return s


def _quality_terrain(s: dict, p: Prefs) -> None:
    t = p.terrain_quality
    if t == "any" or s["kind"] != "quality":
        return
    if s["title"].endswith(("（平路）", "（坡道）")):          # carried over from this week: already shaped
        s["terrain"] = "road" if s["title"].endswith("（平路）") else "trail"
        return
    # 「VO2max 間歇 5×4 分上坡」 (SP-79; a stored older row: 「爬坡間歇 5×4 分」)
    uphill = "爬坡" in s["title"] or s["title"].endswith("上坡")
    if t == "flat" and uphill:
        s["title"] = re.sub(r"上坡$", "", s["title"]).replace("爬坡間歇", "間歇") + "（平路）"
        s["detail"] = re.sub(r"上坡 (\d+) 分鐘（[^）]*），慢跑或走下來恢復", r"平路 \1 分鐘，慢跑 \1 分鐘恢復",
                             s.get("detail") or "")
        s["terrain"] = "road"
    elif t == "flat":
        s["title"] += "（平路）"
        s["detail"] = "平路或跑步機；" + (s.get("detail") or "")
        s["terrain"] = "road"
    elif t == "hill" and not uphill:
        s["title"] += "（坡道）"
        s["detail"] = "找 4–8% 的長坡，上坡跑、下坡慢跑回來；" + (s.get("detail") or "")
        s["terrain"] = "trail"
    else:
        s["terrain"] = "trail"


def shape(ss: list[dict], total_min: float, p: Prefs, c: Ctx) -> list[dict]:
    """Apply the preferences to a session template (dicts with id / kind /
    title / minutes / target / detail / source / tss). Returns the new list;
    notes go to `c.notes`."""
    ss = [dict(s) for s in ss]
    easy_t = next((s for s in ss if s["kind"] == "easy"), None)
    strength_t = next((s for s in ss if s["kind"] == "strength"), None)
    long_s = next((s for s in ss if s["id"] == "long"), None)
    hard = [s for s in ss if s["kind"] in ("quality", "test")]
    rest = [s for s in ss if s["kind"] not in ("easy", "strength", "quality", "test") and s["id"] != "long"]
    long_rate = (float(long_s.get("tss") or 0.0) / long_s["minutes"] * 60.0
                 if long_s is not None and long_s.get("minutes") else c.rate("road"))

    # ---- quality count + terrain + interval target ------------------------
    if p.quality == 0 and hard:
        if any(s["kind"] == "test" for s in hard):
            c.notes.append({"level": "info", "src": "prefs", "text": "偏好每週 0 次品質課：CP 測試也先不排"})
        hard = []
    q = [s for s in hard if s["kind"] == "quality"]
    if p.quality == 2 and c.allow_quality and len(q) == 1 and c.mode != "recovery_week" and (c.quality_cap or 2) >= 2:
        # only one track open (SP-31: with both, the caller already planned one Zone 3 + one Zone 5):
        # the second session repeats the first — unless the two together go over the week's interval
        # total (quality_gate.QUALITY_SHARE_MAX of the running time, 推估 80/20)
        from backend.engine.overview import session_tiz_min
        from backend.engine.quality_gate import QUALITY_SHARE_MAX
        if 2 * session_tiz_min(q[0]) <= QUALITY_SHARE_MAX * total_min + 1e-6:
            hard.append({**q[0], "id": "quality2"})
        else:
            c.notes.append({"level": "info", "src": "quality_share",
                            "text": f"偏好每週 2 堂品質課，但兩堂「{q[0]['title']}」會超過一週間歇總量上限"
                                    f"（跑步時間 {QUALITY_SHARE_MAX:.0%}，80/20；推估）：本週排 1 堂"})
    elif len(q) > 1 and (p.quality == 1 or (c.quality_cap or 2) < 2):
        hard = [s for s in hard if s["kind"] != "quality" or s is q[0]]
    for s in hard:
        if not s.get("variant_key"):
            # a library variant already carries its terrain (interval_library.terrains)
            _quality_terrain(s, p)
    from backend.engine import target_policy as TP
    for s in hard + rest + ([long_s] if long_s is not None else []):
        # 目標依據 (engine/target_policy.py, the one rule): the target text of the chosen basis
        pol = TP.target_policy(s, p)
        if pol["chosen"] in ("hr", "power") and s["kind"] in ("quality", "long", "hike", "mountain"):
            s["target"] = TP.target_text(s.get("target", ""), pol["basis"])
    if p.cap_weekday is not None:
        for s in hard:
            if s.get("variant_key"):
                continue                        # fitted to the cap already (interval_library.fit, §C5.3)
            if s["kind"] == "test" and s["minutes"] > p.cap_weekday:
                from backend.engine import aet_test as AT
                if AT.is_xu(s):
                    continue                    # on the long day, not a weekday session
                c.notes.append({"level": "info", "src": "prefs",
                                "text": NOTE_AET_TEST if s["id"] == "test_aet"
                                else note_test(s.get("protocol") or "standard")})
            elif s["kind"] == "quality":
                trim_quality(s, p.cap_weekday)

    # ---- long --------------------------------------------------------------
    long_cap = p.long_cap
    if long_s is not None and long_cap is not None and long_s["minutes"] > long_cap:
        long_s["minutes"] = long_cap

    # ---- strength ----------------------------------------------------------
    strength = [s for s in ss if s["kind"] == "strength"]
    if p.strength is not None:
        tpl = strength_t or {"kind": "strength", "title": "肌力（下肢單腳＋核心）", "minutes": 35, "target": "",
                             "detail": "膝主導＋臀中肌；安排在輕鬆日或跑完後", "source": "Uphill Athlete",
                             "tss": 35 / 60 * c.rate("strength"), "day": None, "done": False, "done_by": None}
        strength = [{**tpl, "id": f"strength{i + 1}", "day": None} for i in range(p.strength)]

    # ---- easy fill, counts and caps -----------------------------------------
    n_fixed = len(hard) + (1 if long_s is not None else 0) + len(rest)
    used = sum(s["minutes"] for s in hard + rest) + (long_s["minutes"] if long_s is not None else 0)
    left = max(0.0, total_min - used)
    cap = p.cap_weekday
    # auto: at most rest_days.AUTO_MAX_RUNS runs a week, at least one rest day (SP-82); 每週跑步次數 wins
    from backend.engine.rest_days import AUTO_MAX_RUNS
    room = max(0, min(c.slots, p.runs if p.runs is not None else AUTO_MAX_RUNS) - n_fixed)
    if p.runs is not None:
        n_e = min(room, int(left // MIN_EASY))
    else:
        n_e = 0 if left < 25 else max(1, min(5, int(round(left / 50.0))))
        if cap is not None and left >= 25:
            n_e = max(n_e, math.ceil(left / cap))
        n_e = min(n_e, room)
    per = left / n_e if n_e else 0.0
    excess = 0.0
    if cap is not None and per > cap:
        excess, per = (per - cap) * n_e, float(cap)
    elif n_e == 0 and left >= 25 and (p.runs is not None or cap is not None or c.slots < 7):
        excess = left                                        # no slot left for it
    easies = [_easy(easy_t, i, per, p, c) for i in range(n_e)]

    # ---- excess: long day first; soft may exceed its cap, hard drops it -----
    if excess >= 5:
        target = long_s
        if target is None and p.cap_mode == "soft" and easies:
            target = easies[0]
            target["long_day"] = True                        # place() puts it on the long weekday
        if target is not None:
            lim = long_cap if target is long_s else cap
            fit = excess if lim is None else max(0.0, min(excess, lim - target["minutes"]))
            target["minutes"] = _r5(target["minutes"] + fit)
            excess -= fit
            if excess >= 5 and p.cap_mode == "soft":
                target["minutes"] = _r5(target["minutes"] + excess)
                c.notes.append({"level": "info", "src": "prefs", "text": NOTE_SOFT.format(cap=cap if cap is not None else long_cap,
                                                                          m=_r5(excess))})
                excess = 0.0
        if excess >= 5:
            c.notes.append({"level": "watch", "src": "prefs", "text": NOTE_HARD.format(h=f"{excess / 60.0:.1f}")})

    # ---- terrain and TSS of the long session ----------------------------------
    if long_s is not None:
        _terrain_long(long_s, p, c)
        cat = {"trail": "trail", "hike": "hike", "road": "road"}.get(long_s.get("terrain"))
        long_s["tss"] = long_s["minutes"] / 60.0 * (c.rate(cat) if cat else long_rate)
    return ([long_s] if long_s is not None else []) + hard + rest + strength + easies


# ---------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------

QUALITY_ORDER = (1, 2, 3, 0, 4, 5, 6)          # Tue, Wed, Thu, Mon, Fri, Sat, Sun
SRC_GAP = "台灣教練：5 區一週最多 2 次、間隔至少 2 天；強度課與長跑隔 ≥ 48 小時"
LONG_MIN_TYPICAL = 90                            # 推估: an LSD rarely fits under 90 min


def _gap(a: int, b: int) -> int:
    """Days between two weekdays, week over week (a Sunday long run and a Monday interval: 1)."""
    d = abs(a - b)
    return min(d, 7 - d)


def _better_day(p: Prefs, long_wd: int, taken: tuple = ()) -> Optional[int]:
    for wd in QUALITY_ORDER:
        if p.days[wd] and wd != long_wd and _gap(wd, long_wd) >= 2 and all(_gap(wd, t) >= 2 for t in taken):
            return wd
    return None


def day_conflicts(p: Prefs, auto_long_wd: int = 5) -> list[dict]:
    """Where the preferred weekdays break the default rules: [{"code", "kind", "wd", "rule",
    "source", "text", "action", "keep"}]. `keep` = the athlete chose 照我的偏好 for that code
    (pref_keep): the planner then keeps the preference; otherwise it moves the session and
    says so. Codes: gap48, after_long, not_allowed, cap_long, z5_twice, aet_weekday, aet_gap."""
    out = []
    lw = long_weekday(p, auto_long_wd)
    q = p.pref_of("quality")

    def add(code, kind, wd, rule, source, text, action):
        out.append({"code": code, "kind": kind, "wd": wd, "rule": rule, "source": source, "text": text,
                    "action": action, "keep": code in p.pref_keep})
    for kind in PREF_KINDS:
        for wd in p.pref_of(kind):
            if not p.days[wd] and kind != "rest":          # (a day that isn't 可練日 is a rest day anyway)
                add(f"not_allowed:{kind}:{wd}", kind, wd, "可練日", "課表偏好", f"{PREF_LABEL[kind]}偏好週{WD_ZH[wd]}，但週{WD_ZH[wd]}不是可練日",
                    "改排在可練的日子")
    for wd in q[:1]:
        g = _gap(wd, lw)
        alt = _better_day(p, lw)
        alt_t = f"週{WD_ZH[alt]}" if alt is not None else "別天"
        if (wd - lw) % 7 == 1:
            add("after_long", "quality", wd, "長跑隔天不排強度課", SRC_GAP,
                f"間歇排在長跑（週{WD_ZH[lw]}）的隔天：長跑後腿還沒恢復（建議 ≥ 2 天，台灣教練）：要改到{alt_t}嗎？",
                f"改到{alt_t}" if alt is not None else "改到離長跑最遠的一天")
        elif g < 2:
            add("gap48", "quality", wd, "強度課與長跑隔 ≥ 48 小時", SRC_GAP,
                f"間歇和 LSD 只隔 {g} 天（建議 ≥ 2 天，台灣教練）：要改到{alt_t}嗎？",
                f"改到{alt_t}" if alt is not None else "改到離長跑最遠的一天")
    if len(q) >= 2 and _gap(q[0], q[1]) < 2:
        add("z5_twice", "quality", q[1], "5 區每週最多 2 次、間隔 ≥ 2 天", SRC_GAP,
            f"兩個間歇偏好日週{WD_ZH[q[0]]}、週{WD_ZH[q[1]]}只隔 {_gap(q[0], q[1])} 天（台灣教練：至少隔 2 天）",
            "第二堂改到隔 ≥ 2 天的日子")
    if lw < 5 and p.cap_long is None and p.cap_weekday is not None and p.cap_weekday < LONG_MIN_TYPICAL:
        add("cap_long", "long", lw, "平日時間上限", "課表偏好（單次時間上限）",
            f"長跑排在週{WD_ZH[lw]}，但平日上限只有 {p.cap_weekday} 分，LSD 放不下（通常 ≥ {LONG_MIN_TYPICAL} 分，推估）："
            "設定「長跑日上限」或改到週末", "長跑照平日上限縮短")
    for kind in ("aet_test",):
        for wd in p.pref_of(kind)[:1]:
            if p.aet_test_days == "weekday" and wd >= 5:
                add("aet_weekday", kind, wd, "AeT 測試只排平日（課表偏好 AeT 測試日）", "課表偏好",
                    f"AeT 測試偏好週{WD_ZH[wd]}，但「AeT 測試日」設成只排平日", "改排平日（或把 AeT 測試日改成「任何一天」）")
            if _gap(wd, lw) < 2 or (q and _gap(wd, q[0]) < 1):
                add("aet_gap", kind, wd, "測試前後不排長跑／強度課", "測試前一天輕鬆（推估）",
                    f"AeT 測試偏好週{WD_ZH[wd]}，離長跑（週{WD_ZH[lw]}）或間歇太近：測出來的飄移會失真", "建議日期會避開，偏好日排在後面")
    return out


def blocked_pref_notes(p: Prefs, monday: dt.date, blocked) -> list[dict]:
    """A preferred weekday that falls on a 不排課日期 this week: say so (the session moves)."""
    out = []
    for kind in PREF_KINDS:
        for wd in p.pref_of(kind)[:1]:
            d = (monday + dt.timedelta(days=wd)).isoformat()
            if d in (blocked or ()):
                out.append({"level": "info", "src": "prefs",
                            "text": f"{PREF_LABEL[kind]}偏好的週{WD_ZH[wd]}（{int(d[5:7])}/{int(d[8:10])}）是不排課日期：這週改排別天"})
    return out


def _pref_ok(p: Prefs, kind: str, wd: int, conflicts: list[dict]) -> bool:
    """A preferred weekday is used unless one of its conflicts wasn't kept."""
    return not any(c["kind"] == kind and c["wd"] == wd and not c["keep"] for c in conflicts)


def long_weekday(p: Prefs, auto_wd: int) -> int:
    return LONG_WD.get(p.long_day, auto_wd)


def place(ss: list[dict], free: list[dt.date], long_wd: int, p: Prefs,
          notes: Optional[list] = None, long_done: Optional[dt.date] = None,
          hard_done: Optional[list] = None, run_done: Iterable[dt.date] = ()) -> list[dict]:
    """Put `ss` (not done, day None) on `free` days. Main sessions only on
    allowed days, one per day; strength on the chosen weekdays, else with an
    easy run / on an allowed day, never the day before the long session.
    The AeT test goes on the aet_test_days (aet_test.pick_day: a weekday by
    default, ≥ 2 days from the long run where possible). `long_done` = the
    day of a long run already done this week. The easy runs take the days that put the rest days
    where they belong — next to the long run, between hard days, on the 休息日偏好 (pref_days rest)
    when possible (engine/rest_days.py, SP-82); `run_done` = the days already trained this week.
    Returns the sessions that found no day; notes go to `notes`."""
    from backend.engine import aet_test as AT
    from backend.engine import rest_days as RD
    avail = [d for d in free if p.allowed(d)]
    main = [s for s in ss if s["kind"] != "strength"]
    long_day = long_done
    aet_days = AT.test_days(p)
    rest_pref = p.pref_of("rest")
    easy_q: list = []

    def pick_near(wd: int) -> Optional[dt.date]:
        if not avail:
            return None
        exact = [d for d in avail if d.weekday() == wd]
        # like week_plan(): the long weekday, else the last allowed day left
        return exact[0] if exact else avail[-1]

    order = {"long": 0, "test": 1, "quality": 1}
    for s in sorted(main, key=lambda s: (0 if s["id"] == "long" or AT.is_xu(s) else
                                         order.get(s["kind"], 3 if not s.get("long_day") else 2))):
        if not avail:
            break
        if s["kind"] == "test" and AT.is_xu(s):
            # 徐國峰's 90-min test is the weekend LSD (it replaced the long run this week)
            pick = AT.pick_day_xu(avail, long_wd, p.cap_weekday)
            if pick is None:
                if notes is not None:
                    notes.append({"level": "info", "src": "prefs",
                                  "text": "徐國峰 90 分鐘測試排在週末長跑日，本週沒有可練的週末：這週先不測"})
                continue
            s["day"] = pick.isoformat()
            avail.remove(pick)
            long_day = pick
            continue
        if s["id"] == "long" or s.get("long_day"):
            pick = pick_near(long_wd)
            if s["id"] == "long":
                long_day = pick
        elif s["kind"] == "test" and aet_days is not None and AT.is_aet_session(s):
            hard_days = [dt.date.fromisoformat(x["day"]) for x in main
                         if x["kind"] in ("quality", "test") and x["day"]]
            r = AT.pick_day(avail, long_day, hard_days, aet_days, weekend_ok=not AT.is_short(s))
            if r["day"] is None:
                if notes is not None:
                    notes.append({"level": "info", "src": "prefs", "text": r["note"]})
                continue
            s["day"] = r["day"].isoformat()
            avail.remove(r["day"])
            continue
        elif s["kind"] in ("quality", "test"):
            # ≥ 2 days between hard days: 台灣教練 — Zone 5 at most twice a week, ≥ 2 days apart
            hard_days = [dt.date.fromisoformat(x["day"]) for x in main if x["kind"] in ("quality", "test") and x["day"]]
            hard_days += list(hard_done or [])   # done hard days this week (workout_review.HARD_TYPES)
            ok = lambda d:(long_day is None or abs((d - long_day).days) >= 2) and \
                all(abs((d - h).days) >= 2 for h in hard_days)
            cands = sorted(avail, key=lambda d: (d.weekday() in rest_pref, QUALITY_ORDER.index(d.weekday())))
            if s.get("prefer_days"):
                # interval_library.fit moved it to a day with a bigger cap (§C5.3-4)
                cands = sorted(cands, key=lambda d: d.weekday() not in s["prefer_days"])
            pref = p.pref_of("quality") if s["kind"] == "quality" and not s.get("prefer_days") else ()
            if s["kind"] == "test" and not AT.is_aet_session(s):
                pref = p.pref_of("cp_test")
            pick = None
            if pref:
                conf = day_conflicts(p, long_day.weekday() if long_day else 5)
                for wd in pref:
                    d = next((x for x in avail if x.weekday() == wd), None)
                    if d is None:
                        continue            # past, blocked (不排課日期; see blocked_pref_notes) or taken
                    kept = any(c["kind"] == "quality" and c["wd"] == wd and c["keep"] for c in conf)
                    if ok(d) or kept:
                        pick = d
                        break
                    if notes is not None:
                        why = next((c["text"] for c in conf if c["kind"] == "quality" and c["wd"] == wd),
                                   f"週{WD_ZH[wd]}離長跑或另一堂強度課不到 2 天（台灣教練）")
                        notes.append({"level": "watch", "src": "prefs", "text": f"{why} → 這週改排別天（要照偏好排，到課表偏好選「照我的偏好」）"})
            pick = pick or next((d for d in cands if ok(d)), None) or next(
                (d for d in cands if long_day is None or abs((d - long_day).days) >= 1), cands[0])
        elif s["kind"] == "easy" and any(w in (s.get("title") or "") for w in ("衝刺", "加速跑")) \
                and p.pref_of("strides"):
            # 坡道衝刺／加速跑 on its preferred weekday (not the day before the long run)
            pick = next((d for wd in p.pref_of("strides") for d in avail if d.weekday() == wd
                         and (long_day is None or d != long_day - dt.timedelta(days=1))), avail[0])
        elif s["kind"] == "easy":
            easy_q.append(s)                # placed together below (rest days, SP-82)
            continue
        else:
            pick = avail[0]
        s["day"] = pick.isoformat()
        avail.remove(pick)
    if easy_q and avail:
        mon = min(free) - dt.timedelta(days=min(free).weekday())
        runs = [dt.date.fromisoformat(x["day"]) for x in main if x["day"]] + list(run_done or ())
        qd = [dt.date.fromisoformat(x["day"]) for x in main if x["kind"] in ("quality", "test") and x["day"]]
        for s, d in zip(easy_q, RD.pick_days(len(easy_q), avail, [mon + dt.timedelta(days=i) for i in range(7)],
                                             runs, long_day, long_wd, qd + list(hard_done or ()), rest_pref)):
            s["day"] = d.isoformat()
            avail.remove(d)
    easy_days = [dt.date.fromisoformat(s["day"]) for s in main if s["kind"] == "easy" and s["day"]]
    taken: set = set()
    for s in [s for s in ss if s["kind"] == "strength"]:
        if p.strength_days:
            cands = [d for d in free if d.weekday() in p.strength_days and d not in taken]
        else:
            # an easy-run day first, a free day only when none fits, a 休息日偏好 day last (SP-82)
            cands = [d for d in RD.strength_days(easy_days, avail, [long_day - dt.timedelta(days=1)] if long_day
                                                 else [], rest_pref) if d not in taken]
        if cands:
            s["day"] = cands[0].isoformat()
            taken.add(cands[0])
            if cands[0] in avail:
                avail.remove(cands[0])
    for s in ss:
        s.pop("long_day", None)
    return [s for s in main if not s["day"]]
