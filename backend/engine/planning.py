"""
Season plan: target events (races / 百岳), training phases and personal
heart-rate thresholds.

Stored as a user overlay at ~/.wko5coach/plan.json — nothing here touches the
WKO5 files. Phases are generated backwards from each A event unless the user
has saved manual phases:

    base  →  specific (8 wk)  →  taper (2 wk)  →  event  →  recovery  →  transition (3 wk)  →  rebuild

Sources (docs/research/periodization-phase-metrics.md):
  * taper 14 days — Bosquet et al. 2007 meta-analysis (8–14 days most effective)
    (SP-96: 7 days before a 2–3 day 百岳, up to 21 by 課表偏好 for a road marathon / an ultra — taper_days)
  * specific block before the taper, general → specific — Koop; Uphill Athlete; its 8 weeks
    (SPECIFIC_WEEKS) — Friel Build 8–9 wk, Canova 6–8 wk, vert.run last 8–10 wk, 江晏慶 強化
    2–3 wk + 巔峰 ~6 wk (coaches; periodization-cross-sport.md §6.1 [18][130][131][454])
  * recovery / transition after the goal event — Uphill Athlete (2–4 weeks);
    shortened to 1 week below 超馬級 (event_size: ~6 h predicted, else EP 60 — SP-111; a
    heuristic, not a finding)
  * transition after the recovery (SP-73) — Friel (Transition 1–8 weeks, usually 3–4)
    and Canova (4 weeks of easy running ≤ 1 h), coach-schools-zones-periodization.md R5:
    `transition_weeks` (課表偏好, default 3, 0 = off; ≤ 4 weeks, ≤ 6 after a 超馬級+ race — SP-109,
    Koop / Hart / Torrence §4.7.1). It never takes days from the next A
    race's backward-planned 專項期: too close → shortened, < 7 days → skipped, with a note
  * recovery by 賽事大小 + a 回量期 (reverse taper) after the 轉換期 (SP-98, recovery_plan); one size up
    for a race with much more downhill than the athlete trained (SP-111 下坡升級, downhill)
  * two A races close together (SP-90): the first one's recovery yields to the second one's
    taper (recovery_and_taper, 推估), the phases say what was cut short, and A races < 12
    weeks apart get TrainerRoad's hint (periodization-cross-sport.md §4.8)
B events get a mini-taper and a recovery by size inside the surrounding phase, a C event replaces a
quality session or the long run of its week (SP-95, engine/post_race.py) — neither makes phases.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from backend.i18n import N_, _

PLAN_PATH: Optional[Path] = None      # fixed file (tests); None = the tenant's plan.json


def plan_path() -> Path:
    """The current tenant's plan.json (backend/tenancy.py)."""
    if PLAN_PATH is not None:
        return Path(PLAN_PATH)
    from backend import tenancy
    return tenancy.private_path("plan.json")

TAPER_DAYS = 14
# 減量期長度 (SP-96; periodization-cross-sport.md §4.5.1, §6.1): 14 days by default (Wang 2023
# meta-analysis: 8–14 days best); a 2–3 day 百岳 trip 7 days ([114], 教練級); 課表偏好 taper_days
# lengthens a road marathon / an ultra up to 21 days (Strava: 3 weeks > 2, 4 no better; ≥ 22 days
# no effect in the meta-analysis — opening it to ultras is 推估)
BAIYUE_TAPER_DAYS = 7
BAIYUE_SHORT_DAYS = (2, 3)
# SP-114 (baiyue-mountaineering-training.md §3.2, owner 2026-10-05: a multi-day 百岳 tapers 7–10 days, not
# 14): UA — a 2–3 day trip needs one week; 4 days and more (B / C 級) 1–2 weeks (UA, Evoke) → 10 days,
# the top of the owner's 7–10 (推估)
BAIYUE_LONG_TAPER_DAYS = 10
TAPER_DAYS_RANGE = (TAPER_DAYS, 21)
TAPER_SETTING = "plan.prefs.taper_days"
SPECIFIC_WEEKS = 8           # coaches 6–10 wk, 8 inside all of them (module docstring; 江晏慶 → 8 is 推估)
# B event's mini-taper (coaches, periodization-cross-sport.md §4.8.1): TrainerRoad race-week taper
# [362]; Friel 2–3 rest days [431]; Pfitzinger no intervals 5 d / tempo + long 4 d before [437] (二手轉述)
MINI_TAPER_DAYS = 5          # B event: no interval in the 5 days before, no tempo / long run in 4 (SP-95;
                             # engine/post_race.py)
LONG_EVENT_HOURS = 6.0       # recovery: 14 d at/above this (= the 超馬級 size), else 7 d

# 賽事大小 (SP-111, owner 2026-10-05): predicted time → EP → km, never the horizontal km alone
# when more is known — a 30 km / 2000 m trail race (EP 50, ~7 h) is not a short race.
#   short < 2 h / EP 21 · medium < 4 h / 42 · marathon < 6 h / 60 · ultra < 20 h / 160 · hundred
# 2 h / 4 h ≈ an amateur half / full marathon; EP 21 / 42 = the owner's anchors (EP 42 ≈ a road
# marathon); 6 h = LONG_EVENT_HOURS; 20 h from the SP-98 research (100-mile coaches); EP 60 / 160
# from ~7–10 EP an hour on trails. All 推估.
SIZES = ("short", "medium", "marathon", "ultra", "hundred")
SHORT, MEDIUM, MARATHON, ULTRA, HUNDRED = range(len(SIZES))
SIZE_LABEL = {"short": N_("短"), "medium": N_("中"), "marathon": N_("馬拉松級"), "ultra": N_("超馬級"),
              "hundred": N_("100 英里級")}
SIZE_HOURS = (2.0, 4.0, LONG_EVENT_HOURS, 20.0)
SIZE_EP = (21.0, 42.0, 60.0, 160.0)
EP_DIVISOR = 100.0           # ITRA km-effort; terrain_calib's personal divisor when installed
# The race calculator's predicted hours (race_refs.calculator_hours) and the personal climb divisor
# (terrain_calib.divisor), installed by the app (install_size_inputs); None = est_hours / ITRA's 100,
# so the engine and its tests stay pure.
HOURS_OF = None              # Callable[[Event], Optional[float]]
DIVISOR_OF = None            # Callable[[], float]
B_RECOVERY_DAYS = 3          # a short B race's recovery (B_REC_DAYS, SP-95); Pfitzinger ~5 d after a
                             # tune-up [437] (二手轉述); 3 推估 (§4.8.1)
TRANSITION_WEEKS = 3          # 轉換期 after an A race's recovery (SP-73; Friel 3–4, Canova 4 — the low end, 推估)
# The setting's range (課表偏好 transition_weeks; 0 = off). What an A race gets (transition_weeks_for,
# SP-109): ≤ TRANSITION_WEEKS_MAX (Friel's / Canova's upper end), ≤ TRANSITION_WEEKS_ULTRA_MAX after
# a 超馬級+ race (event_size, the same cut as the 14-day recovery): ultra coaches' rest + return is
# 4–8+ weeks — Koop 2–4 weeks no running then 2–4 weeks of 2 runs a week, Hart 2 weeks–2 months
# off then ≥ 4 weeks back, Torrence 1–2 months (教練級, periodization-cross-sport.md §4.7.1);
# 14 days + 6 weeks ≈ 8 weeks (the 6 is 推估). The default stays TRANSITION_WEEKS.
TRANSITION_WEEKS_MAX = 4
TRANSITION_WEEKS_ULTRA_MAX = 6
TRANSITION_WEEKS_RANGE = (0, TRANSITION_WEEKS_ULTRA_MAX)
TRANSITION_MIN_DAYS = 7       # less room before the next race's 專項期 → no 轉換期 (推估)
TRANSITION_SETTING = "plan.prefs.transition_weeks"
# Two A races close together (SP-90): the first one's 恢復期 yields to the second one's 減量期 —
# shortened, never below REC_MIN_DAYS while the taper keeps ≥ REC_MIN_DAYS too; below that the
# free days are split (half taper, rounded up). Both 推估 (no study of re-peaking 3–8 weeks apart,
# periodization-cross-sport.md §4.8). A_GAP_HINT_WEEKS: TrainerRoad's rule — A races ≥ 12 weeks
# apart (§4.8, §6.2); closer ones get a hint to make one of them a B race.
REC_MIN_DAYS = 7
A_GAP_HINT_WEEKS = 12

PHASES = {
    "transition": "轉換期",
    "base": "基礎期",
    "specific": "專項期",
    "taper": "減量期",
    "event": "賽事",
    "recovery": "恢復期",
    # SP-98: after the 恢復期 and the 轉換期, the reverse taper. Its place AFTER the 轉換期 is the
    # owner's call (2026-10-05, delegated, kept): the volume then only goes up from the race on —
    # 恢復期 (REC_SHARE 40 %) → 轉換期 (overview.TRANSITION_SHARE 50 %, ≤ 60-min runs) → 回量期
    # (REBUILD_SHARES 50 → 75 %) → 基礎期 — a monotonic order with no second dip; a 回量期 before the 轉換期 would build volume up
    # and then drop it again for the 轉換期.
    "rebuild": "回量期",
}
KINDS = {"race": "越野賽", "baiyue": "百岳", "road": "路跑賽", "other": "其他"}
PRIORITIES = ("A", "B", "C")
EVENT_HEAT = ("auto", "hot", "cool")      # Event.heat (heat-acclimation.md §5.4)
PACK_MAX_KG = 40.0                         # Event.pack_kg: as athlete.set_hike_meta's 0–40 kg check
CUTOFF_MAX_H = 240.0                       # Event.cutoff_hours: 10 days, past the longest stage race's cutoff
# 賽制 of a ≥ 2 day 越野賽／其他 (SP-114, owner 2026-10-05): 分站 = one stage a day set by the organiser
# (each stage's km / climb in day_plan); 連續 = one non-stop course, the runner decides where to sleep
# (sleep points in the race calculator, not days). 百岳 is always split by day; 路跑 is never asked.
RACE_FORMATS = ("stage", "continuous")
FORMAT_KINDS = ("race", "other")
ULTRA_GPX_KM = 50.0                        # SP-114: 超馬 (越野 ≥ 50 km) without a GPX → a reminder to upload one


class EventError(ValueError):
    """A bad event field (the message is for the user, translated); a bad date stays a plain ValueError."""


def _d(s) -> Optional[dt.date]:
    if s in (None, ""):
        return None
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


@dataclass
class Event:
    id: str
    name: str
    date: str                       # first day, ISO
    kind: str = "race"
    priority: str = "A"
    days: int = 1                   # multi-day 百岳
    distance_km: Optional[float] = None
    climbing_m: Optional[float] = None
    est_hours: Optional[float] = None   # expected moving time
    note: str = ""
    # is this a hot race? auto = the race-day forecast / climatology decides
    # (Hadley > 150, heat.race_is_hot); hot / cool = the user says so
    heat: str = "auto"
    # the trip pack (kg, day 1 = the heaviest) — loaded-carry-training.md §5.1;
    # None = capacity.PACK_DEFAULT_MULTI / _SINGLE (9 kg), see pack()
    pack_kg: Optional[float] = None
    # SP-105 (engine/race_feasibility.py): a race's 關門時間 (h); a 百岳's 撤退時間 = hours from the
    # summit day's start with no summit yet → turn back. None = not set
    cutoff_hours: Optional[float] = None
    # 百岳: km of the summit along the whole trip; None = the GPX's highest point
    summit_km: Optional[float] = None
    # SP-114: a multi-day trip's own numbers per day [{"km", "gain_m", "loss_m" (None = gain_m)}],
    # required on save (upsert_event) — an equal split misjudges the summit day / the hardest stage;
    # distance_km / climbing_m are their sums. None on old events (a hint asks for them)
    day_plan: Optional[list] = None
    # SP-114 賽制 (RACE_FORMATS) of a ≥ 2 day 越野賽／其他; None = 分站 (old events) or not asked
    race_format: Optional[str] = None
    # SP-244 「會用登山杖」: the race calculator adds a hint on steep climbs / descents
    # (seg_targets.pole_hint); no prediction reads it (trekking-poles.md §4)
    poles: bool = False

    @property
    def continuous(self) -> bool:
        """A ≥ 2 day 越野賽／其他 run non-stop (賽制 連續): one course, never cut into days."""
        return self.kind in FORMAT_KINDS and int(self.days or 1) > 1 and self.race_format == "continuous"

    @property
    def split_days(self) -> int:
        """How many days the course is cut into: the trip's days, 1 for a 連續 race."""
        return 1 if self.continuous else max(1, int(self.days or 1))

    @property
    def needs_day_plan(self) -> bool:
        """Every multi-day trip but a road race: 百岳, a 分站 越野賽／其他 (SP-114)."""
        return self.split_days > 1 and self.kind != "road"

    @property
    def day_plan_missing(self) -> bool:
        """An old multi-day event saved without its per-day numbers (not blocked, hinted)."""
        return self.needs_day_plan and len(self.day_plan or []) != self.split_days

    @property
    def gpx_recommended(self) -> bool:
        """SP-114: an ultra (越野賽／其他 ≥ ULTRA_GPX_KM) — the 專項期's climb sessions and the race check
        need the GPX to know where the climbing is; a reminder, never a block."""
        return self.kind in FORMAT_KINDS and float(self.distance_km or 0.0) >= ULTRA_GPX_KM

    @property
    def pack(self) -> float:
        """pack_kg, else the 9 kg default (capacity.PACK_DEFAULT_MULTI / _SINGLE)."""
        if self.pack_kg is not None:
            return float(self.pack_kg)
        from backend.engine.racepower.capacity import PACK_DEFAULT_MULTI, PACK_DEFAULT_SINGLE
        return PACK_DEFAULT_MULTI if (self.days or 1) > 1 else PACK_DEFAULT_SINGLE

    @property
    def start(self) -> dt.date:
        return _d(self.date)

    @property
    def end(self) -> dt.date:
        return self.start + dt.timedelta(days=max(1, int(self.days or 1)) - 1)

    @property
    def climb_per_km(self) -> Optional[float]:
        if self.distance_km and self.climbing_m is not None and self.distance_km > 0:
            return self.climbing_m / self.distance_km
        return None

    @property
    def is_long(self) -> bool:
        """超馬級 or bigger (event_size): the 14-day recovery and B2B (b2b.qualifies)."""
        return event_size(self) >= ULTRA

    @property
    def size(self) -> str:
        return SIZES[event_size(self)]


def _num(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def clean_day_plan(data: dict, days: int) -> Optional[list]:
    """The per-day numbers of an event being saved (SP-114): [{km, gain_m, loss_m}] of exactly
    `days` days when the trip needs them (Event.needs_day_plan: 百岳, a 分站 越野賽／其他 of ≥ 2 days),
    else None. km > 0 and the climb are required for every day; the descent is optional (None =
    the climb, as plan_course assumes). A missing day / number is an EventError: 「少一天不能存」."""
    probe = Event(id="", name="", date="2000-01-01", kind=data.get("kind") or "race", days=days,
                  race_format=data.get("race_format"))
    if not probe.needs_day_plan:
        return None
    rows = list(data.get("day_plan") or [])
    if len(rows) != days:
        raise EventError(_("多天行程要填每天的距離和爬升：{days} 天要填 {days} 列（現在 {n} 列）", days=days, n=len(rows)))
    out = []
    for i, r in enumerate(rows, 1):
        r = r if isinstance(r, dict) else {}
        km, gain, loss = _num(r.get("km")), _num(r.get("gain_m")), _num(r.get("loss_m"))
        if km is None or km <= 0 or gain is None or gain < 0:
            raise EventError(_("第 {n} 天的距離和爬升沒填完（距離要大於 0，爬升可以填 0）", n=i))
        if loss is not None and loss < 0:
            raise EventError(_("第 {n} 天的下降不能是負的", n=i))
        out.append({"km": round(km, 2), "gain_m": round(gain), "loss_m": None if loss is None else round(loss)})
    return out


def _tier(x: float, cuts: tuple) -> int:
    return next((i for i, c in enumerate(cuts) if x < c), len(cuts))


def event_ep(ev, divisor: Optional[float] = None) -> Optional[float]:
    """EP (effort km) = km + climb ÷ divisor (overview.ep_km's ITRA convention; the personal
    divisor when installed, terrain_calib); None without a distance or a climb."""
    if not ev.distance_km or ev.climbing_m is None:
        return None
    if divisor is None:
        try:
            divisor = float(DIVISOR_OF()) if DIVISOR_OF is not None else EP_DIVISOR
        except Exception:                   # noqa: BLE001 — a size must still come out
            divisor = EP_DIVISOR
    return float(ev.distance_km) + float(ev.climbing_m) / (divisor if divisor and divisor > 0 else EP_DIVISOR)


def event_hours(ev) -> Optional[float]:
    """The race calculator's predicted moving hours when installed (HOURS_OF), else est_hours."""
    if HOURS_OF is not None:
        try:
            h = HOURS_OF(ev)
        except Exception:                   # noqa: BLE001 — the plan's own estimate is the fallback
            h = None
        if h:
            return float(h)
    return float(ev.est_hours) if ev.est_hours else None


def event_size(ev, hours: Optional[float] = None, divisor: Optional[float] = None) -> int:
    """賽事大小 SHORT … HUNDRED (SP-111): the predicted time (`hours`, else event_hours) →
    EP → km when not even the climb is known. A multi-day trip (百岳縱走, stage race) is
    超馬級: low intensity each day, so its whole-trip EP would overstate the recovery — 百岳's
    own rules are left for later research (as `days > 1` was before)."""
    if (ev.days or 1) > 1:
        return ULTRA
    h = hours if hours else event_hours(ev)
    if h:
        return _tier(h, SIZE_HOURS)
    ep = event_ep(ev, divisor)
    if ep is None:
        ep = float(ev.distance_km or 0.0)
    return _tier(ep, SIZE_EP)


def taper_days(ev, pref: Optional[int] = None) -> int:
    """The 減量期 length of an A event (SP-96): BAIYUE_TAPER_DAYS for a 2–3 day 百岳 trip,
    BAIYUE_LONG_TAPER_DAYS for a longer one (SP-114: every multi-day 百岳 tapers 7–10 days); the
    課表偏好 `pref` (TAPER_DAYS_RANGE, ≤ 21) for a road marathon or bigger and for an ultra-size
    race (event_size); else TAPER_DAYS."""
    days = int(ev.days or 1)
    if ev.kind == "baiyue" and BAIYUE_SHORT_DAYS[0] <= days <= BAIYUE_SHORT_DAYS[1]:
        return BAIYUE_TAPER_DAYS
    if ev.kind == "baiyue" and days > BAIYUE_SHORT_DAYS[1]:
        return BAIYUE_LONG_TAPER_DAYS
    if pref and pref > TAPER_DAYS and ev.kind != "baiyue":
        size = event_size(ev)
        if (ev.kind == "road" and size >= MARATHON) or size >= ULTRA:
            return int(min(pref, TAPER_DAYS_RANGE[1]))
    return TAPER_DAYS


def size_basis(ev) -> str:
    """How event_size judged `ev` (SP-111: the plan page says it): 「多日行程」, 「預估時間 N h」,
    「EP N」 or 「公里數 N」."""
    if (ev.days or 1) > 1:
        return _("多日行程")
    h = event_hours(ev)
    if h:
        return _("預估時間 {h:.1f} h", h=h)
    ep = event_ep(ev)
    if ep is not None:
        return _("EP {ep:.0f}", ep=ep)
    return _("公里數 {km:.0f}", km=float(ev.distance_km or 0.0))


# ---- 賽後恢復與回量期 (SP-98; periodization-cross-sport.md §4.6, §4.6.1, §6.1「SP-98」) --------------
# Two parts after an A race, both 教練級: a 恢復期 (the first days without running) and a 回量期
# that mirrors the 減量期 (Higdon's reverse taper [446]; no structured intensity — Koop [434], Uphill
# Athlete [441]). Length by 賽事大小 (event_size, SP-111 — never the horizontal km alone):
#   馬拉松級  7 days + 1 回量 week   (Torrence [445], Johnston [442]: ~2 weeks back to serious training)
#   超馬級   14 days                (Johnston [442]: ~2 weeks)
#   100 英里級 14 days + 2 回量 weeks (Johnston [442] 「easily a month」, Koop [434] weeks 2–3 easy)
#   短 / 中   7 days (unchanged)
# The cuts are 推估. Volume: the 恢復期 REC_SHARE of the 4 complete weeks before the taper
# (pre_race_mondays, as the 轉換期 — the last 4 weeks hold the taper and the race), the 回量期 from
# REBUILD_SHARES[0] to [1] (the taper's 50 % → 40 % mirrored, Daniels' 50 → 75 % re-entry steps;
# the shares are 推估). The first REC_NO_RUN_DAYS after the race no running, then runs ≤
# REC_SHORT_MIN until day REC_SHORT_DAYS (48 h on, a 40-min easy run did no harm — a controlled
# trial [256]; Higdon: 3 days off, ~20 min on day 4 [446]).
# Order: 恢復期 → 轉換期 (課表偏好, SP-73) → 回量期 → 基礎期: the 回量期 is the ramp back into normal
# training, after the off-season block when there is one (with 轉換期 off it follows the 恢復期).
REC_BY_SIZE = {SHORT: (7, 0), MEDIUM: (7, 0), MARATHON: (7, 1), ULTRA: (14, 0), HUNDRED: (14, 2)}
REC_SHARE = 0.40
REBUILD_SHARES = (0.50, 0.75)
REC_NO_RUN_DAYS = 2
REC_SHORT_DAYS = 7
REC_SHORT_MIN = 40
REBUILD_MIN_DAYS = 7               # a shorter remainder before the next race's 專項期 → no 回量期 (推估)
# B races (SP-95; Pfitzinger, via [437], 二手轉述): 短 3 days, 中 5 days of easy running only;
# 馬拉松級 and up as an A race's 恢復期 days (no 回量期). Cuts by event_size, 推估.
B_REC_DAYS = {SHORT: 3, MEDIUM: 5}
# Two A races (SP-95, §4.8.1): closer than A_GAP_HINT_WEEKS → 恢復 → (回量) → 中間訓練 (the 專項期
# left, its weekly hours ≤ INTER_PEAK of the first race's pre-taper level — Higdon [429] / [430]:
# 4 weeks apart 50–65 %, 6 weeks 75–90 %; 推估) → 再減量, no 轉換期. A trail / 超馬級 first race
# closer than CLOSE_TRAIL_WEEKS: 恢復 + 回量 + 減量 only, no training block (推估, [428][434]).
CLOSE_TRAIL_WEEKS = 8
INTER_PEAK = ((4, 0.65), (6, 0.90))

# 下坡升級 (SP-111 phase 1, docs/research/downhill-recovery.md): the race's downhill impact
# (chart_metrics.DOWNHILL_EXPR units — 「下坡衝擊等效 km」, Gottschall & Kram 2005, Keller 1996)
# ÷ the athlete's biggest single activity of the last DOWNHILL_WEEKS[0] weeks (DOWNHILL_WEEKS[0]–[1]
# weeks back linearly down-weighted to 0 — the repeated-bout effect lasts 3–6 weeks, not 9).
# ≥ DOWNHILL_BUMP → the recovery size one tier up (7 → 14 days: Millet 2011, Chalchat 2022; a size
# already ≥ 14 days → one more 回量 week, 推估); ≥ DOWNHILL_HINT → only a hint more; nothing
# downhill in the window = over. ≥ DOWNHILL_BIG (a big downhill for you) → the first
# DOWNHILL_FLAT_DAYS days no hard session and no downhill (Bontemps 2020: 3–5 days). Thresholds 推估.
DOWNHILL_BUMP = 1.5
DOWNHILL_HINT = 2.0
DOWNHILL_BIG = 1.0
DOWNHILL_WEEKS = (6, 9)
DOWNHILL_FLAT_DAYS = 3
# The race's downhill impact from its GPX (None = no GPX) and the athlete's weighted max before a
# day — installed by the app (engine/downhill_recovery.install); None = not judged, so the engine
# and its tests stay pure.
RACE_DOWNHILL_OF = None      # Callable[[Event], Optional[float]]
ATHLETE_DOWNHILL_OF = None   # Callable[[dt.date], Optional[float]]


def downhill_ratio(race: float, mine: Optional[float]) -> dict:
    """The 下坡升級 verdict of a race's downhill impact against the athlete's (SP-111): {"race",
    "mine", "ratio" (inf with nothing downhill), "bump", "hint", "big"}."""
    ratio = race / mine if mine and mine > 0 else math.inf
    return {"race": race, "mine": mine or 0.0, "ratio": ratio, "bump": ratio >= DOWNHILL_BUMP,
            "hint": ratio >= DOWNHILL_HINT, "big": ratio >= DOWNHILL_BIG and race > 0}


def downhill(ev) -> Optional[dict]:
    """downhill_ratio of `ev` from the installed hooks; {"gpx": False} without its GPX; None when the
    hooks are not installed (tests, a pure engine) or fail."""
    if RACE_DOWNHILL_OF is None or ATHLETE_DOWNHILL_OF is None:
        return None
    try:
        race = RACE_DOWNHILL_OF(ev)
        if race is None:
            return {"gpx": False}
        return {"gpx": True, **downhill_ratio(float(race), ATHLETE_DOWNHILL_OF(ev.start))}
    except Exception:                       # noqa: BLE001 — the plan must still build
        return None


def recovery_plan(ev, b: bool = False) -> dict:
    """The recovery after event `ev` (SP-98, SP-95, SP-111 下坡升級): {"size", "rec_size", "days",
    "rebuild" (weeks), "downhill", "bumped" (None / "size" / "rebuild"), "text"}. `b`: a B race —
    B_REC_DAYS for 短 / 中, else the A days; never a 回量期."""
    size = event_size(ev)
    rec = size
    days, rb = REC_BY_SIZE[size]
    dh = downhill(ev)
    bumped = None
    if dh and dh.get("bump"):
        if days >= 14 and not b:
            rb, bumped = rb + 1, "rebuild"
        else:
            rec, bumped = min(size + 1, HUNDRED), "size"
            days, rb = REC_BY_SIZE[rec]
    if b:
        days, rb = B_REC_DAYS.get(rec, days), 0
    out = {"size": size, "rec_size": rec, "days": days, "rebuild": rb, "downhill": dh, "bumped": bumped}
    base = recovery_plan_text(ev, out, b)
    return {**out, "text": base}


def recovery_plan_text(ev, rp: dict, b: bool = False) -> str:
    """「馬拉松級（預估時間 4.5 h）：恢復 7 天＋回量 1 週」 + the downhill verdict, no 「；」 inside."""
    size = SIZES[rp["size"]]
    if rp["rebuild"]:
        what = _("恢復 {days} 天＋回量 {n} 週", days=rp["days"], n=rp["rebuild"])
    else:
        what = _("恢復 {days} 天", days=rp["days"])
    txt = _("{size}（{basis}）：{what}", size=_(SIZE_LABEL[size]), basis=size_basis(ev), what=what)
    dh = rp.get("downhill")
    if dh is None:
        return txt
    if not dh.get("gpx"):
        return txt + _("，沒有賽事 GPX：恢復天數沒有看下坡（上傳 GPX 才能算）")
    if dh["mine"] <= 0:
        txt += _("，近 {w} 週沒有下坡紀錄，這場的下坡當成超過你練過的", w=DOWNHILL_WEEKS[1])
    else:
        txt += _("，這場的下坡是你近 {w} 週最大一次的 {r:.1f} 倍", w=DOWNHILL_WEEKS[0], r=dh["ratio"])
    if rp["bumped"] == "rebuild":
        txt += _("，回量期多 1 週")
    elif rp["bumped"] == "size":
        base_days = (B_REC_DAYS.get(rp["size"], REC_BY_SIZE[rp["size"]][0]) if b
                     else REC_BY_SIZE[rp["size"]][0])
        if rp["days"] > base_days:
            txt += _("，恢復多給 {d} 天", d=rp["days"] - base_days)
        elif rp["rebuild"] > REC_BY_SIZE[rp["size"]][1]:
            txt += _("，回量期多 1 週")
    if dh.get("hint"):
        txt += _("，遠超過你練過的，賽後前幾天特別注意")
    if dh.get("big"):
        txt += _("，賽後 {h} 小時內不排硬課、不跑下坡", h=DOWNHILL_FLAT_DAYS * 24)
    return txt


def rebuild_share(phase, monday: dt.date) -> tuple[float, int, int]:
    """(share, week index from 0, weeks) of the 回量期 `phase` (Phase or dict) in the week of
    `monday`: REBUILD_SHARES[0] → [1] linearly over its weeks."""
    def get(k):
        return phase.get(k) if isinstance(phase, dict) else getattr(phase, k)
    s, e = _d(get("start")), _d(get("end"))
    n = max(1, math.ceil(((e - s).days + 1) / 7))
    i = min(n - 1, max(0, (monday - s).days // 7))
    lo, hi = REBUILD_SHARES
    return (lo if n == 1 else lo + (hi - lo) * i / (n - 1)), i, n


def inter_peak(gap_days: int) -> float:
    """The 中間訓練's weekly-hours cap, as a share of the first race's pre-taper level, for two A
    races `gap_days` apart (INTER_PEAK, linear between its points)."""
    (w0, s0), (w1, s1) = INTER_PEAK
    w = gap_days / 7.0
    if w <= w0:
        return s0
    if w >= w1:
        return s1
    return s0 + (s1 - s0) * (w - w0) / (w1 - w0)


def intermediate(phases_: list, monday: dt.date) -> Optional[dict]:
    """The 中間訓練 between two A races < A_GAP_HINT_WEEKS apart (SP-95): when the 專項期 holding
    `monday` follows an A race that ended less than that before its own race — {"share" (inter_peak),
    "gap" (days), "prev_end"}; else None. `phases_`: Phase objects or dicts."""
    def get(p, k):
        return p.get(k) if isinstance(p, dict) else getattr(p, k, None)
    sp = next((p for p in phases_ or () if get(p, "kind") == "specific"
               and _d(get(p, "start")) <= monday <= _d(get(p, "end"))), None)
    if sp is None:
        return None
    ev_p = next((p for p in phases_ if get(p, "kind") == "event" and _d(get(p, "start")) > _d(get(sp, "end"))), None)
    prev = [p for p in phases_ if get(p, "kind") == "event" and _d(get(p, "end")) < _d(get(sp, "start"))]
    if ev_p is None or not prev:
        return None
    pe = max(_d(get(p, "end")) for p in prev)
    gap = (_d(get(ev_p, "start")) - pe).days
    if gap >= A_GAP_HINT_WEEKS * 7:
        return None
    return {"share": inter_peak(gap), "gap": gap, "prev_end": pe.isoformat()}


def transition_weeks_for(ev, weeks: Optional[int]) -> int:
    """The 轉換期 weeks after A event `ev` (SP-109): the 課表偏好 `weeks`, capped at
    TRANSITION_WEEKS_ULTRA_MAX after a 超馬級+ race (event_size), else TRANSITION_WEEKS_MAX."""
    if not weeks or weeks <= 0:
        return 0
    cap = TRANSITION_WEEKS_ULTRA_MAX if event_size(ev) >= ULTRA else TRANSITION_WEEKS_MAX
    return int(min(int(weeks), cap))


def install_size_inputs() -> None:
    """The app's 賽事大小 inputs: the race calculator (memoised per tenant / event / day, 10 min)
    and the personal climb divisor. Called once at startup; tests leave them unset."""
    global HOURS_OF, DIVISOR_OF
    import contextvars
    import threading
    import time
    lock, memo, busy = threading.Lock(), {}, contextvars.ContextVar("event_size_busy", default=False)

    def hours_of(ev) -> Optional[float]:
        if busy.get() or not ev.distance_km:  # the calculator itself reads the plan: no recursion
            return None
        from backend import tenancy
        # SP-114: day_plan is a list (unhashable in astuple): the event as sorted JSON
        key = (tenancy.current().id, json.dumps(asdict(ev), sort_keys=True, default=str), dt.date.today())
        with lock:
            hit = memo.get(key)
        if hit and time.time() - hit[0] < 600.0:
            return hit[1]
        tok = busy.set(True)
        try:
            from backend.engine.panels.race_refs import calculator_hours, course_of
            hs = calculator_hours(ev, course_of(ev))
        finally:
            busy.reset(tok)
        h = sum(hs) if hs else None
        with lock:
            if len(memo) > 256:
                memo.clear()
            memo[key] = (time.time(), h)
        return h

    def divisor_of() -> float:
        from backend.engine import terrain_calib
        return terrain_calib.divisor()

    HOURS_OF, DIVISOR_OF = hours_of, divisor_of
    # 下坡升級 (SP-111 phase 1, SP-98): the race's and the athlete's downhill impact
    from backend.engine import downhill_recovery
    downhill_recovery.install()


@dataclass
class Phase:
    kind: str
    start: str
    end: str                        # inclusive
    event_id: Optional[str] = None
    auto: bool = True
    # why an automatic phase is not the standard length (SP-73: a shortened / skipped 轉換期)
    note: str = ""

    @property
    def label(self) -> str:
        return PHASES.get(self.kind, self.kind)


@dataclass
class Threshold:
    """Personal HR thresholds from a dated test. Any field may be blank."""
    date: str
    lthr: Optional[float] = None    # lactate-threshold HR (WKO5 `thr`)
    aethr: Optional[float] = None   # aerobic-threshold HR (Uphill Athlete AeT test)
    mhr: Optional[float] = None     # maximum HR (設定 → 心率; engine/hr_profile.py)
    rhr: Optional[float] = None     # resting HR (設定 → 心率; engine/hr_profile.py)
    cp: Optional[float] = None      # running critical power, W (a CP test, engine/cp_protocols.py)
    note: str = ""
    # how `cp` was measured (cp_protocols.METHOD_LABEL): 2pt / 1pt_prior / tt20 /
    # race; None = entered by hand or a legacy 3'/12' row. `wprime` J, only
    # when measured (two-point) — a prior is not the athlete's W′.
    wprime: Optional[float] = None
    cp_method: Optional[str] = None
    # how `lthr` / `aethr` were obtained (LTHR_METHODS / AETHR_METHODS); None =
    # a legacy row, read from its note (threshold_method). docs/research/
    # zones-and-thresholds.md §3.4 change 1: an applied estimate is not a test.
    lthr_method: Optional[str] = None
    aethr_method: Optional[str] = None
    # how `mhr` was obtained (MHR_METHODS; SP-64): test = a max-HR test read by
    # engine/threshold_confidence.py, estimate = its sustained-peak candidate,
    # manual = 設定 → 心率; None = a legacy row (= manual)
    mhr_method: Optional[str] = None

    THRESHOLD_FIELDS = ("lthr", "aethr", "mhr", "rhr", "cp")


_THRESHOLD_KEYS = ("date", "lthr", "aethr", "mhr", "rhr", "cp", "note", "wprime", "cp_method",
                   "lthr_method", "aethr_method", "mhr_method")

# estimate = thresholds.estimate applied with 「套用估計」; friel30 = Friel's
# 30-min solo TT (last 20 min HR); test = an AeT drift test (engine/aet_test.py);
# race / lab / manual = entered from a race, a lab test, by hand
LTHR_METHODS = ("estimate", "friel30", "race", "lab", "manual")
AETHR_METHODS = ("estimate", "test", "lab", "manual")
MHR_METHODS = ("test", "race", "lab", "estimate", "manual")
METHOD_LABEL = {"estimate": "自動估算", "friel30": "30 分鐘測試", "test": "AeT 測試", "race": "比賽",
                "lab": "實驗室測試", "manual": "手動輸入"}
_FIELD_NAME = {"lthr": "LTHR", "aethr": "AeT"}


def threshold_method(t: "Threshold", name: str) -> Optional[str]:
    """How the row's `name` (lthr / aethr) was obtained. Rows written before
    the *_method fields existed are read from their note: 「LTHR 自動估算」 /
    「AeT 自動估算」 / 「由活動資料自動估算」 (the season-plan page's and
    apply-estimate's notes) = estimate, 「AeT … 測試」 (apply_body of an AeT
    drift test) = test, anything else = manual."""
    if getattr(t, name, None) is None:
        return None
    m = getattr(t, f"{name}_method", None)
    if m:
        return m
    note = t.note or ""
    label = _FIELD_NAME.get(name, name)
    if f"{label} 自動估算" in note or "由活動資料自動估算" in note:
        return "estimate"
    if name == "aethr" and "AeT" in note and "測試" in note:
        return "test"
    return "manual"


def threshold_row(plan: "Plan", name: str, day: dt.date) -> Optional[dict]:
    """The plan row whose `name` (lthr / aethr) is in effect on `day`:
    {"value", "date", "method", "measured", "label"}; None without one.
    measured = obtained by a test / race / lab / by hand, not an applied
    estimate. label: 「自動估算（已套用 2026-01-15）」, 「30 分鐘測試 2026-…」 …"""
    rows = sorted((t for t in plan.thresholds if getattr(t, name, None) is not None and _d(t.date) <= day),
                  key=lambda t: t.date)
    if not rows:
        return None
    t = rows[-1]
    m = threshold_method(t, name)
    d = t.date[:10]
    label = f"自動估算（已套用 {d}）" if m == "estimate" else f"{METHOD_LABEL.get(m, '手動輸入')} {d}"
    return {"value": float(getattr(t, name)), "date": d, "method": m, "measured": m != "estimate",
            "label": label}


@dataclass
class Weight:
    date: str
    kg: float


PROFILE_FIELDS = {
    "sex": ("male", "female"),
    "power_meter": ("stryd", "coros", "garmin", "other"),
}


@dataclass
class Plan:
    events: list[Event] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)       # manual; empty = auto
    thresholds: list[Threshold] = field(default_factory=list)
    weights: list[Weight] = field(default_factory=list)     # dated body weight
    profile: dict = field(default_factory=dict)             # sex, height_cm, power_meter

    def weight_on(self, day: dt.date) -> Optional[float]:
        """Body weight in effect on `day` (earliest entry before the first)."""
        ws = sorted((_d(w.date), w.kg) for w in self.weights if w.kg)
        val = None
        for d, kg in ws:
            if d <= day:
                val = kg
        return val if val is not None else (ws[0][1] if ws else None)

    # ---- persistence ------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Plan":
        path = path or plan_path()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(
            events=[Event(**e) for e in raw.get("events", [])],
            phases=[Phase(**{**p, "auto": False}) for p in raw.get("phases", [])],
            thresholds=[Threshold(**{k: v for k, v in t.items() if k in _THRESHOLD_KEYS})
                        for t in raw.get("thresholds", [])],
            weights=[Weight(**w) for w in raw.get("weights", [])],
            profile=dict(raw.get("profile", {})),
        )

    def save(self, path: Optional[Path] = None) -> None:
        path = path or plan_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "events": [asdict(e) for e in sorted(self.events, key=lambda e: e.date)],
            "phases": [{k: v for k, v in asdict(p).items() if k != "auto"} for p in self.phases],
            "thresholds": [asdict(t) for t in sorted(self.thresholds, key=lambda t: t.date)],
            "weights": [asdict(w) for w in sorted(self.weights, key=lambda w: w.date)],
            "profile": self.profile,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)

    # ---- edits ------------------------------------------------------------
    def upsert_event(self, data: dict) -> Event:
        data = {k: v for k, v in data.items() if k in Event.__dataclass_fields__}
        if data.get("priority") not in PRIORITIES:
            data["priority"] = "A"
        if data.get("kind") not in KINDS:
            data["kind"] = "other"
        if data.get("heat") not in EVENT_HEAT:
            data["heat"] = "auto"
        data["poles"] = bool(data.get("poles"))
        if data.get("pack_kg") in ("",):
            data["pack_kg"] = None
        if data.get("pack_kg") is not None:
            data["pack_kg"] = float(data["pack_kg"])
            if not 0 <= data["pack_kg"] <= PACK_MAX_KG:
                raise EventError(_("行程背包要在 0–{max:g} kg", max=PACK_MAX_KG))
        for k, hi in (("cutoff_hours", CUTOFF_MAX_H), ("summit_km", None)):
            if data.get(k) in ("",):
                data[k] = None
            if data.get(k) is not None:
                data[k] = float(data[k])
                if data[k] <= 0 or (hi is not None and data[k] > hi):
                    raise EventError(_("關門／撤退時間要在 0–{max:g} 小時", max=hi) if hi else _("山頂公里數要大於 0"))
        days = max(1, int(data.get("days") or 1))
        data["days"] = days
        data["race_format"] = ((data.get("race_format") if data.get("race_format") in RACE_FORMATS else "stage")
                               if data["kind"] in FORMAT_KINDS and days > 1 else None)
        data["day_plan"] = clean_day_plan(data, days)
        if data["day_plan"]:
            data["distance_km"] = round(sum(d["km"] for d in data["day_plan"]), 2)
            data["climbing_m"] = round(sum(d["gain_m"] for d in data["day_plan"]))
        _d(data["date"])  # validate
        eid = data.get("id") or uuid.uuid4().hex[:8]
        ev = Event(**{**data, "id": eid})
        self.events = [e for e in self.events if e.id != eid] + [ev]
        return ev

    def delete_event(self, eid: str) -> bool:
        n = len(self.events)
        self.events = [e for e in self.events if e.id != eid]
        return len(self.events) != n

    # ---- thresholds -------------------------------------------------------
    def threshold_on(self, name: str, day: dt.date) -> Optional[float]:
        """Latest non-blank `name` (lthr / aethr / mhr / cp) dated on or before `day`,
        else None. A test never applies to the days before it was done (fixed
        2026-10-01: the earliest row used to apply backwards, so a recent test's
        CP / LTHR row leaked into every earlier date). Callers then fall
        back to what existed on `day`: WKO5's dated setting history
        (Dataset.setting / cp / aethr) or an estimate as of that date
        (racepower.athlete.thresholds_as_of)."""
        val = None
        for d, v in sorted((_d(t.date), getattr(t, name)) for t in self.thresholds
                           if getattr(t, name) is not None):
            if d <= day:
                val = v
        return val


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------

def auto_phases(events: list[Event], begin: dt.date, end: dt.date,
                transition_weeks: int = TRANSITION_WEEKS, taper_pref: Optional[int] = None) -> list[Phase]:
    """Phases covering [begin, end], built backwards from each A event; each taper taper_days(ev,
    `taper_pref`) long (SP-96: 7 for a 2–3 day 百岳, up to 21 by 課表偏好).
    After each A event's recovery: `transition_weeks` of 轉換期 (0 = none), ending before
    the next A event's 專項期 — shortened when that starts sooner (`note` says so), skipped
    when fewer than TRANSITION_MIN_DAYS are left (the recovery phase's `note` says so).

    Two A races close together (SP-90): the first one's 恢復期 yields to the second one's 減量期
    (recovery_and_taper) — the second race keeps its event days and a taper, its 專項期 gets what
    is left, and the phases say so (`note`, shown with the week plan and on the timeline), with a
    hint when they are < A_GAP_HINT_WEEKS apart. A race starting inside the previous one's own
    days has nothing left to plan and is skipped."""
    one = dt.timedelta(days=1)
    a_events = sorted((e for e in events if e.priority == "A"), key=lambda e: e.start)
    out: list[Phase] = []
    cursor = begin                  # first day not yet assigned
    taper_of: dict[str, int] = {}   # a taper shortened for the previous A race (SP-90)
    prev: Optional[Event] = None    # the last A race planned

    def add(kind, s, e, eid=None, note=""):
        s = max(s, cursor)
        if s <= e:
            out.append(Phase(kind, s.isoformat(), e.isoformat(), eid, note=note))
            return out[-1]
        return None

    def taper_len(ev: Event) -> int:
        return taper_of.get(ev.id, taper_days(ev, taper_pref))

    def spec_start_of(ev: Event) -> dt.date:
        return ev.start - dt.timedelta(days=taper_len(ev)) - dt.timedelta(weeks=SPECIFIC_WEEKS)

    for i, ev in enumerate(a_events):
        if ev.end < cursor:
            continue                # inside the previous A race's own days: nothing left to plan
        taper_start = ev.start - dt.timedelta(days=taper_len(ev))
        spec_start = spec_start_of(ev)
        add("base", cursor, spec_start - one)
        notes = _after_prev_notes(ev, prev, cursor, spec_start, taper_start, taper_len(ev),
                                  taper_days(ev, taper_pref)) if prev else []
        firsts = [add("specific", spec_start, taper_start - one, ev.id), add("taper", taper_start, ev.start - one, ev.id),
                  add("event", ev.start, ev.end, ev.id)]
        first = next((p for p in firsts if p is not None), None)
        if first is not None and notes:
            first.note = "；".join(notes)
        cursor = max(cursor, ev.end + one)
        prev = ev
        nxt = next((x for x in a_events[i + 1:] if x.start > ev.end), None)
        rp = recovery_plan(ev)                  # 7 / 14 days + 回量 weeks by event_size (SP-98, SP-111)
        rec_days = rp["days"]
        rec_notes: list[str] = []
        gap = (nxt.start - ev.end).days if nxt is not None else None
        close = gap is not None and gap < A_GAP_HINT_WEEKS * 7                  # SP-95: no 轉換期
        only_rec = close and gap < CLOSE_TRAIL_WEEKS * 7 and (ev.kind != "road" or event_size(ev) >= ULTRA)
        if nxt is not None:
            rec_days, t = recovery_and_taper(gap - 1, rec_days, taper_len(nxt))
            if t < taper_len(nxt):
                taper_of[nxt.id] = t
            if rec_days < rp["days"]:
                rec_notes.append(_("恢復期縮短為 {days} 天：下一場 A 賽事「{name}」{date}",
                                   days=rec_days, name=nxt.name, date=_md(nxt.start)))
        rec_end = ev.end + dt.timedelta(days=rec_days)
        rec = add("recovery", cursor, rec_end, ev.id)
        cursor = max(cursor, rec_end + one)
        at = rec_end + one                      # the next post-race phase's first day (before `begin` too)
        # what is left before the next A race's 專項期 (its 減量期 when only 恢復＋減量): that phase is
        # planned backwards from its race and wins; the 回量期 gets it first, the 轉換期 the rest
        # (two A races < 12 weeks apart, SP-95: the 回量期 comes out of the 中間訓練, i.e. that 專項期)
        limit = None if nxt is None else \
            (nxt.start - dt.timedelta(days=taper_len(nxt)) - one if close else spec_start_of(nxt) - one)
        room = None if limit is None else max(0, (limit - at).days + 1)
        rb_want = rp["rebuild"] * 7
        rb_days = room if only_rec else (rb_want if room is None else min(rb_want, room))
        if not only_rec and rb_days < REBUILD_MIN_DAYS:
            rb_days = 0
        rb_note = ""
        if only_rec and rb_days:
            rb_note = _("只排恢復、回量、減量：越野／超馬級的 A 賽和下一場「{name}」相隔不到 {w} 週，中間不排訓練（推估）",
                        name=nxt.name, w=CLOSE_TRAIL_WEEKS)
        elif rb_want and rb_days < rb_want and rb_days:
            rb_note = _("回量期縮短為 {days} 天：下一場 A 賽事「{name}」{date}",
                        days=rb_days, name=nxt.name, date=_md(nxt.start))
        elif rb_want and not rb_days:
            rec_notes.append(_("沒有回量期：下一場 A 賽事「{name}」{date} 太近", name=nxt.name, date=_md(nxt.start)))
        room_t = None if room is None else room - rb_days
        tw = 0 if close else transition_weeks_for(ev, transition_weeks)   # ≤ 4 weeks, ≤ 6 after an ultra (SP-109)
        if close and not only_rec and transition_weeks_for(ev, transition_weeks) and not rec_notes:
            # SP-95: two A races < 12 weeks apart — 恢復 → 中間訓練 → 再減量, no 轉換期
            rec_notes.append(_("沒有轉換期：離下一場 A 賽事「{name}」只剩 {span}，恢復期後直接進專項期",
                               name=nxt.name, span=_span((nxt.start - at).days)))
        if tw > 0:
            # 轉換期 (SP-73): never into the next A race's 專項期
            if room_t is not None and room_t < tw * 7:
                days = room_t
                t_last = at + dt.timedelta(days=days - 1)
                if days >= TRANSITION_MIN_DAYS:
                    note = _("轉換期縮短為 {days} 天：下一場 A 賽事「{name}」的專項期 {start} 開始",
                             days=days, name=nxt.name, start=(limit + one).isoformat())
                    add("transition", cursor, t_last, ev.id, note)
                    cursor, at = max(cursor, t_last + one), t_last + one
                elif not rec_notes and days > 0:
                    rec_notes.append(_("沒有轉換期：下一場 A 賽事「{name}」的專項期 {start} 開始，只剩 {days} 天",
                                       name=nxt.name, start=(limit + one).isoformat(), days=days))
                elif not rec_notes:
                    # its 專項期 already started (SP-90: never cite a date in the past)
                    rec_notes.append(_("沒有轉換期：離下一場 A 賽事「{name}」只剩 {span}，恢復期後直接進專項期",
                                       name=nxt.name, span=_span((nxt.start - at).days)))
            else:
                t_end = at + dt.timedelta(weeks=tw) - one
                capped = _("轉換期 {weeks} 週：設定的 {pref} 週只用在超馬級以上的 A 賽事",
                           weeks=tw, pref=int(transition_weeks)) if transition_weeks > tw else ""
                add("transition", cursor, t_end, ev.id, capped)
                cursor, at = max(cursor, t_end + one), t_end + one
        if rb_days:
            # 回量期 (SP-98): the reverse taper back into training, no intensity
            r_end = at + dt.timedelta(days=rb_days - 1)
            add("rebuild", cursor, r_end, ev.id, rb_note)
            cursor = max(cursor, r_end + one)
        if rec is not None:
            rec.note = "；".join(rec_notes + [rp["text"]])
    if cursor <= end:
        add("base", cursor, end)    # no A event ahead: open-ended base
    return [p for p in out if _d(p.start) <= end]


def recovery_and_taper(free: int, rec_days: int, taper_days: int) -> tuple[int, int]:
    """(recovery days, taper days) between two A races with `free` days between them (SP-90):
    the full `rec_days` + `taper_days` when they fit; else the recovery shrinks first (the taper
    is what the next race is run on, Bosquet 2007), down to REC_MIN_DAYS; below REC_MIN_DAYS +
    REC_MIN_DAYS both are short and the free days are split, the taper getting the odd day. 推估."""
    free = max(0, free)
    if free >= rec_days + taper_days:
        return rec_days, taper_days
    if free - taper_days >= REC_MIN_DAYS:
        return free - taper_days, taper_days
    if free >= 2 * REC_MIN_DAYS:
        return REC_MIN_DAYS, free - REC_MIN_DAYS
    t = min(taper_days, math.ceil(free / 2))
    return free - t, t


def week_phase_notes(phases_: list, monday: dt.date) -> list[tuple[str, str]]:
    """[(phase kind, text)] of the automatic phase notes a week shows: the phase holding `monday`
    and every phase starting later that week (a 減量期 cut short from a Thursday, SP-90); a
    phase's notes are joined by 「；」. `phases_`: Phase objects or dicts."""
    def get(p, k):
        return p.get(k) if isinstance(p, dict) else getattr(p, k, None)
    sunday = monday + dt.timedelta(days=6)
    out: list[tuple[str, str]] = []
    for p in phases_:
        s, e = _d(get(p, "start")), _d(get(p, "end"))
        if not get(p, "note") or s is None or e is None:
            continue
        if s <= monday <= e or monday < s <= sunday:
            out += [(get(p, "kind"), t) for t in str(get(p, "note")).split("；") if t and (get(p, "kind"), t) not in out]
    return out


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def _span(days: int) -> str:
    """「N 週」 from 14 days on, else 「N 天」."""
    days = max(0, int(days))
    return _("{n} 週", n=days // 7) if days >= 14 else _("{n} 天", n=days)


def _after_prev_notes(ev: Event, prev: Event, cursor: dt.date, spec_start: dt.date, taper_start: dt.date,
                      taper_len: int, full_taper: int) -> list[str]:
    """The notes of an A race planned right after another (SP-90): a 專項期 / 減量期 cut short by
    the previous race's recovery, and TrainerRoad's ≥ A_GAP_HINT_WEEKS hint."""
    out = []
    left = (taper_start - max(cursor, spec_start)).days
    when = _md(prev.end)
    t_left = (ev.start - max(cursor, taper_start)).days
    if taper_len < full_taper and t_left > 0:
        out.append(_("減量期縮短為 {days} 天：上一場 A 賽事「{name}」{date} 才結束",
                     days=t_left, name=prev.name, date=when))
    elif taper_len < full_taper:
        out.append(_("沒有減量期：上一場 A 賽事「{name}」{date} 才結束", name=prev.name, date=when))
    elif spec_start < cursor and left > 0:
        out.append(_("專項期縮短為 {days} 天（原本 {weeks} 週）：上一場 A 賽事「{name}」{date} 才結束",
                     days=left, weeks=SPECIFIC_WEEKS, name=prev.name, date=when))
    elif spec_start < cursor:
        out.append(_("沒有專項期：上一場 A 賽事「{name}」{date} 才結束", name=prev.name, date=when))
    gap = (ev.start - prev.end).days
    if gap < A_GAP_HINT_WEEKS * 7:
        out.append(_("和上一場 A 賽事只隔 {span}：教練建議至少 {weeks} 週，考慮把其中一場改成 B 賽",
                     span=_span(gap), weeks=A_GAP_HINT_WEEKS))
    return out


def transition_weeks_setting(user_id: int = 1) -> int:
    """課表偏好 轉換期週數 (plan.prefs.transition_weeks), read-only like plan_prefs.load;
    TRANSITION_WEEKS when unset or unreadable."""
    try:
        from backend.engine.wko5expr.datasource import read_setting
        v = read_setting(TRANSITION_SETTING, TRANSITION_WEEKS, user_id)
    except Exception:                       # noqa: BLE001 — phases must still build
        return TRANSITION_WEEKS
    if isinstance(v, bool) or not isinstance(v, int):
        return TRANSITION_WEEKS
    return max(TRANSITION_WEEKS_RANGE[0], min(TRANSITION_WEEKS_RANGE[1], v))


def taper_days_setting(user_id: int = 1) -> int:
    """課表偏好 減量期天數 (plan.prefs.taper_days, SP-96; road marathon / ultra only, taper_days),
    read-only; TAPER_DAYS when unset or unreadable."""
    try:
        from backend.engine.wko5expr.datasource import read_setting
        v = read_setting(TAPER_SETTING, TAPER_DAYS, user_id)
    except Exception:                       # noqa: BLE001 — phases must still build
        return TAPER_DAYS
    if isinstance(v, bool) or not isinstance(v, int):
        return TAPER_DAYS
    return max(TAPER_DAYS_RANGE[0], min(TAPER_DAYS_RANGE[1], v))


def phases(plan: Plan, begin: dt.date, end: dt.date, transition_weeks: Optional[int] = None,
           taper_pref: Optional[int] = None) -> list[Phase]:
    """The manual phases when the user saved any (they win), else auto_phases.
    `transition_weeks` / `taper_pref` None = the 課表偏好 settings (transition_weeks_setting,
    taper_days_setting)."""
    if plan.phases:
        return plan.phases
    tw = transition_weeks_setting() if transition_weeks is None else transition_weeks
    tp = taper_days_setting() if taper_pref is None else taper_pref
    return auto_phases(plan.events, begin, end, tw, tp)


def phase_on(plan: Plan, day: dt.date, begin: Optional[dt.date] = None,
             transition_weeks: Optional[int] = None) -> Optional[Phase]:
    begin = begin or day - dt.timedelta(days=400)
    for p in phases(plan, begin, day + dt.timedelta(days=400), transition_weeks):
        if _d(p.start) <= day <= _d(p.end):
            return p
    return None


POST_RACE_KINDS = ("recovery", "transition", "rebuild")   # the planned post-race phases (SP-73, SP-98)


def phase_days(plan: Plan, begin: dt.date, end: dt.date, kinds: tuple,
               transition_weeks: Optional[int] = None) -> set[dt.date]:
    """The days in [begin, end] inside a phase of one of `kinds` (auto or manual). Empty on any
    plan error."""
    out: set[dt.date] = set()
    try:
        ps = phases(plan, begin - dt.timedelta(days=400), end, transition_weeks)
    except Exception:                       # noqa: BLE001 — the callers must still work
        return out
    for p in ps:
        if p.kind not in kinds:
            continue
        a, b = _d(p.start), _d(p.end)
        if a is None or b is None:
            continue
        d = max(a, begin)
        while d <= min(b, end):
            out.add(d)
            d += dt.timedelta(days=1)
    return out


def transition_days(plan: Plan, begin: dt.date, end: dt.date,
                    transition_weeks: Optional[int] = None) -> set[dt.date]:
    """The days in [begin, end] inside a 轉換期 (auto or manual)."""
    return phase_days(plan, begin, end, ("transition",), transition_weeks)


def post_race_days(plan: Plan, begin: dt.date, end: dt.date,
                   transition_weeks: Optional[int] = None) -> set[dt.date]:
    """The days in [begin, end] inside a planned post-race phase — the A race's 恢復期 (7–14
    days) or the 轉換期 after it (auto or manual). SP-73 (owner 2026-10-05): both are planned
    rest / easy / cross-training blocks, so their days without a run are not a running break
    for the re-entry block (reentry.find_all) or the Zone 3 gate's gap / re-lock
    (quality_gate.z3_consistency), and their weeks are not a volume-step baseline
    (status.i_volume). Empty on any plan error."""
    return phase_days(plan, begin, end, POST_RACE_KINDS, transition_weeks)


def pre_race_mondays(phases_: list, day: dt.date, n: int = 4) -> list[dt.date]:
    """The Mondays of the `n` complete weeks before the taper of the A race whose 轉換期 /
    恢復期 contains `day` (the last `event` phase that ended before `day`; its `taper` phase
    start, else the event start − TAPER_DAYS) — the training level the 轉換期 volume is a share
    of (SP-73). [] without such an event. `phases_`: Phase objects or dicts."""
    def get(p, k):
        return p[k] if isinstance(p, dict) else getattr(p, k)
    evs = [p for p in phases_ if get(p, "kind") == "event" and _d(get(p, "end")) < day]
    if not evs:
        return []
    ev = max(evs, key=lambda p: _d(get(p, "start")))
    ev_start = _d(get(ev, "start"))
    tap = [p for p in phases_ if get(p, "kind") == "taper" and _d(get(p, "end")) == ev_start - dt.timedelta(days=1)]
    t0 = _d(get(tap[0], "start")) if tap else ev_start - dt.timedelta(days=TAPER_DAYS)
    first = t0 - dt.timedelta(days=t0.weekday())        # the taper's own week is not counted
    return [first - dt.timedelta(weeks=k) for k in range(n, 0, -1)]


def taper_start(phases_: list, ev, pref: Optional[int] = None) -> dt.date:
    """The first day of A event `ev`'s 減量期 as planned (SP-96): its taper phase (auto: by
    event_id; manual: the taper phase ending the day before the race), else ev.start −
    taper_days(ev, pref). `phases_`: Phase objects or dicts."""
    def get(p, k):
        return p.get(k) if isinstance(p, dict) else getattr(p, k, None)
    day_before = ev.start - dt.timedelta(days=1)
    for p in phases_ or ():
        if get(p, "kind") == "taper" and (get(p, "event_id") == ev.id or _d(get(p, "end")) == day_before):
            return _d(get(p, "start"))
    return ev.start - dt.timedelta(days=taper_days(ev, pref))


def b_event_windows(events: list[Event]) -> list[dict]:
    """Mini-taper / recovery windows around B events (markers, not phases; the week plan applies
    them — engine/post_race.py, SP-95): MINI_TAPER_DAYS before, recovery_plan(b=True) days after."""
    out = []
    for e in events:
        if e.priority != "B":
            continue
        rp = recovery_plan(e, b=True)
        out.append({"event_id": e.id, "kind": "mini_taper",
                    "start": (e.start - dt.timedelta(days=MINI_TAPER_DAYS)).isoformat(),
                    "end": (e.start - dt.timedelta(days=1)).isoformat()})
        out.append({"event_id": e.id, "kind": "mini_recovery",
                    "start": (e.end + dt.timedelta(days=1)).isoformat(),
                    "end": (e.end + dt.timedelta(days=rp["days"])).isoformat(), "text": rp["text"]})
    return out


# ---------------------------------------------------------------------------
# combined targets across several upcoming events
# ---------------------------------------------------------------------------

GOAL_FIELDS = {
    "distance_km": "距離",
    "climbing_m": "爬升",
    "climb_per_km": "每公里爬升",
    "est_hours": "預估時間",
    "days": "天數",
}


def goals(plan: Plan, today: dt.date, horizon_days: int = 182) -> dict:
    """Train for the hardest demand among the upcoming A and B events.

    Each goal is the maximum over events dated within `horizon_days` (A events
    always count, whatever the date, up to and including the next one), with
    the event that sets it, so the page can say which race drives which
    target."""
    upcoming = sorted((e for e in plan.events if e.end >= today and e.priority in ("A", "B")),
                      key=lambda e: e.start)
    horizon = today + dt.timedelta(days=horizon_days)
    next_a = next((e for e in upcoming if e.priority == "A"), None)
    pool = [e for e in upcoming if e.start <= horizon or (next_a and e.start <= next_a.start)]
    out = {}
    for key, label in GOAL_FIELDS.items():
        best = None
        for e in pool:
            v = getattr(e, key)
            if v is None:
                continue
            if best is None or v > best[0]:
                best = (v, e)
        out[key] = None if best is None else {
            "label": label, "value": best[0], "event_id": best[1].id, "event": best[1].name}
    return {
        "events": [e.id for e in pool],
        "next_a": None if next_a is None else next_a.id,
        "days_to_next_a": None if next_a is None else (next_a.start - today).days,
        "targets": out,
    }


def event_json(e: Event, today: dt.date) -> dict:
    return {**asdict(e), "end": e.end.isoformat(), "climb_per_km": e.climb_per_km,
            "kind_label": KINDS.get(e.kind, e.kind), "days_to": (e.start - today).days,
            "day_plan_missing": e.day_plan_missing, "gpx_recommended": e.gpx_recommended}


def phase_json(p: Phase) -> dict:
    return {**asdict(p), "label": p.label,
            "days": (_d(p.end) - _d(p.start)).days + 1}
