"""
負重訓練（Loaded Carry）before a 百岳 / multi-day trip —
docs/research/loaded-carry-training.md (§2 schedule, §3 sessions, §5 app design).

Hooks (small, like engine/b2b.py):

  * overview.week_plan   — plan_context() after the B2B placement, then apply()
    on the placed sessions (the long day gets its pack, an easy run becomes the
    weekday machine session, strength1 becomes ME, the taper's short carry);
  * projection.project_weeks — projected_context() + apply() per projected
    week, the state carried by next_state();
  * the activity-tags API — activity_pack(): the pack carried per activity,
    stored in racepower_hike_meta.json (athlete.set_hike_meta, doc §5.1);
  * the overview card — card(): this week, the planned loaded sessions, every
    loaded session done with its evaluation, the per-stage trend.

When (doc §2.1, §5.2; the target is the next A event with kind 百岳 or days > 1):
  w = weeks to the trip, counted like b2b.py (the week's Monday, rounded up);
  * base, w 14–11      ME gym version only (Johnston: the last 8–12 weeks
                       before the taper; UA: late base) — no pack;
  * specific (w 10–3)  pack 5 % → 10 % of body weight → the trip pack (UA
                       trekking 2025: 5 %, 10 %, then up to the trip weight;
                       ≈ 2 weeks a step; w 10–9 / 8–7 / 6–3 推估). A manual
                       專項期: first quarter 5 %, second 10 %, last half trip;
  * taper              one 30-min carry 8–14 days out at the trip pack, nothing
                       loaded in the last 7 days (UA mountaineering 1-week taper;
                       the 8–14 day carry 推估; 你的筆記 5–7 days without strength).
The stage advances only after the previous one was done once (UA「stairstep」;
推估): a skipped stage is not jumped.

How often (doc §2.2):
  * ≤ 4 loaded sessions per 28 days (Orr 2021 citing Hauschild 2016: > 4 a
    month adds injuries, no performance; Schuh-Renner 2017 ≥ 5 a month OR 2.11);
  * a loaded long day (≥ 2 h with a pack) at most every 10 days (Orr 2021:
    once every 10–14 days) → loaded long weekends alternate with weeks that get
    one weekday machine session (A / B weeks, 推估);
  * a B2B weekend: day 1 is the loaded long day, day 2 (3) carry the same pack
    while the 28-day count allows (doc §2.4, §5.3); no machine session that week.

Caps (doc §2.3): the long day carries ≤ the trip pack; machine sessions ≤
min(1.15 × trip, 20 % body weight) (1.15 推估; 20 % UA trekking / CTS), the
overload only in the last weeks of stage 3 (w ≤ 5, 推估 for「最後兩次」).
The first long day at a new weight: × 0.75, ≤ 180 min (UA「weight or vertical,
not both」; 0.75 / 180 推估). Downhill with the pack from stage 2 (推估;
Blacker 2010: 72 h recovery after loaded downhill).

Intensity: pack sessions HR ≤ AeT (UA: an aerobic session; target from
target_policy()); ME by feel, described in the text (Johnston; Keena「a
little bit of volume above AeT is OK」). ME needs a measured AeT within 10 %
of LTHR (UA / Johnston; quality_gate.ua_gap ≤ UA_GAP_MAX).

Evaluation (doc §5.4): 100 m climbing windows (grade ≥ 10 %) with HR in the
AeT-adjacent band AeT − 15 … AeT + 3 (hikehr.filter_windows hr_band; −15
推估, +3 = workout_review.AET_MARGIN), loaded vs the unloaded trail / hike
windows of the 8 weeks before, per hikehr.GRADE_BANDS:
  * ΔHR@VAM — the unloaded HR ~ VAM line, the loaded windows' median residual;
  * 負重效率 E_L = (loaded VAM ÷ unloaded VAM) ÷ Pandolf's predicted ratio
    (capacity.pack_ratio); tracked within one weight stage only (Pandolf's
    own error ±8–15 % between weights, capacity.SIGMA_PACK);
  * late drift on a loaded long day (last third vs first third), the loaded
    downhill speed ratio (−30 … −10 %, descriptive — Pandolf is not used downhill).
Thresholds ±3 bpm, ±5 % are 推估 (as in b2b.py).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from statistics import median
from typing import Callable, Iterable, Optional

# ---- when --------------------------------------------------------------------
ME_WEEKS = (4, 14)             # ME from w 14 (Johnston 8–12 wk + late base) to w 4 (doc §5.3: stop 3 weeks out)
BASE_ME_FROM = 11              # base phase: ME only in w 14–11 (doc §2.1 table)
STAGE_PCT = (0.05, 0.10)       # UA trekking: 5 %, 10 % of body weight; stage 3 = the trip pack
STAGE_WEEKS = ((9, 1), (7, 2), (0, 3))   # w ≥ 9 → 1, 7–8 → 2, else 3 (doc §2.3 table, 推估)
TSB_MIN = -20.0                # week_plan's 維持量 line (doc §5.2)
# ---- how often ---------------------------------------------------------------
MAX_PER_28 = 4                 # Orr 2021 / Schuh-Renner 2017
LONG_SPACING_DAYS = 10         # Orr 2021: once every 10–14 days
LONG_LOADED_MIN = 120.0        # doc §2.2: a loaded long day = ≥ 2 h with a pack
DONE_FRAC = 0.8                # doc §5.3: done at ≥ 0.8 × the planned pack
# ---- sessions ----------------------------------------------------------------
MACHINE_MIN = (40, 50)         # doc §3.2: 40–50 min (推估)
MACHINE_FLOOR = 30             # 推估: a weekday cap below 30 min → no machine session
FIRST_LONG = (0.75, 180)       # 推估: the first long day at a new weight × 0.75, ≤ 3 h
OVERLOAD = 1.15                # 推估
BW_MAX = 0.20                  # UA trekking / CTS: ≤ 20 % of body weight
OVERLOAD_WEEKS = 5             # 推估: the overload in stage 3's last machine sessions (w ≤ 5)
DOWNHILL_STAGE = 2             # 推估: carry the pack down from stage 2
LAST_LONG_DAYS = 14            # 推估 (doc §2.5): the last loaded long day ≥ 14 days before the trip
NO_PACK_DAYS = 7               # 推估 (doc §2.5): nothing loaded in the last 7 days
TAPER_CARRY = (8, 14)          # 推估: one 30-min carry 8–14 days out
TAPER_CARRY_MIN = 30
TAPER_GAP_DAYS = 5             # doc §5.3: ≥ 5 days after the previous loaded session
NO_WEIGHT_LOADED_KG = 2.0      # 推估: without a body weight, ≥ 2 kg counts as loaded
# ---- evaluation --------------------------------------------------------------
TRAIN_HR_BAND = (-15.0, 3.0)   # AeT − 15 (推估) … AeT + 3 (workout_review.AET_MARGIN)
V_RUN_DEFAULT = 2.6            # 推估: the AeT running speed for Pandolf's metabolic rate (doc table 2.4–2.8 m/s)
REF_DAYS = 56                  # doc §5.4: the unloaded windows of the last 8 weeks
HR_SAME_BPM = 3.0              # 推估 (= AET_MARGIN)
E_SAME = 0.05                  # 推估 (UA 5 %)
DOWN_G = (-0.30, -0.10)        # doc §5.4 downhill band
CARD_DAYS = 120

SRC_WHEN = ("UA trekking（Steve House 2025）：5 % → 10 % → 行程重量、每階段約 2 週；Johnston（Evoke）：ME 在減量前 8–12 週；"
            "對應到賽前第 10–9／8–7／6–3 週、上一階段做過一次才升級為推估")
SRC_FREQ = ("Orr 2021（引 Hauschild 2016）：負重課每 10–14 天一次、每月 > 4 次沒有額外效果且受傷較多；"
            "Schuh-Renner 2017：每月 ≥ 5 次 OR 2.11；背包長天與機器課隔週輪替為推估")
SRC_PROG = "UA ME 頁：重量和爬升一次只加一樣；新重量第一次 × 0.75、最多 3 小時為推估"
SRC_CAP = "UA trekking、CTS（Rutberg 2025）：訓練背包 ≤ 20 % 體重或行程重量；機器課 1.15 倍、最後幾週才加為推估"
SRC_MACHINE = "UA trekking：跑步機坡度、樓梯機可以替代；40–50 分、加重降速度不降坡度為推估；Koop：不要背著跑"
SRC_TAPER = ("UA mountaineering：2–3 天行程減量 1 週、量減半、強度維持；最後一次背包長天 ≥ 賽前 14 天、"
             "賽前 7 天不背包、賽前 8–14 天一次 30 分為推估；你的筆記：賽前 5–7 天停肌力")
SRC_ME = ("Johnston（Evoke 2022）〈Muscular Endurance〉：Split Jump Squat、Squat Jump、Box Step Up、Front Lunge 各 10 下、6 組；"
          "第 1–2 次不負重休 60 秒、第 3 次休 45 秒、第 4–8 次背心 10 % 體重、第 9–14 次 15 %、休到 10–15 秒；"
          "前提：AeT 在 LTHR 的 10 % 內（UA）")
SRC_DOWN = "Blacker 2010：背負下坡後股四頭肌 72 小時才完全恢復；專項 2 起背著下山為推估"
SRC_HR = "UA：負重課是有氧課，心率 ≤ AeT；Simpson 2011：自選速度時背包的代價出現在速度，不一定在心率"
SRC_EVAL = ("ΔHR@VAM：hikehr.fatigue 的同 VAM 心率差（無外部來源）；負重效率：Pandolf 1977（capacity.pack_ratio）；"
            "心率帶 AeT −15…+3（−15 推估、+3 = AET_MARGIN）；±3 bpm、±5 %、AeT 跑速 2.6 m/s 為推估")


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _r5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def _kg(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 1)               # 0.1 kg, as b2b.pack_kg


def md(day) -> str:
    d = _d(day)
    return f"{d.month}/{d.day}"


# ---------------------------------------------------------------------------
# the target trip and the weights
# ---------------------------------------------------------------------------

def qualifies(ev) -> bool:
    """A 百岳 or a multi-day event (doc §5.2)."""
    if not ev:
        return False
    kind = ev.get("kind") if isinstance(ev, dict) else ev.kind
    days = int((ev.get("days") if isinstance(ev, dict) else ev.days) or 1)
    return kind == "baiyue" or days > 1


def trip_kg(ev: Optional[dict]) -> float:
    """Event.pack_kg, else capacity.PACK_DEFAULT_MULTI / _SINGLE (both 9 kg)."""
    from backend.engine.racepower.capacity import PACK_DEFAULT_MULTI, PACK_DEFAULT_SINGLE
    if ev and ev.get("pack_kg") is not None:
        return float(ev["pack_kg"])
    return PACK_DEFAULT_MULTI if int((ev or {}).get("days") or 1) > 1 else PACK_DEFAULT_SINGLE


def stage_kgs(weight: Optional[float], trip: float) -> list[Optional[float]]:
    """[5 %, 10 % of body weight, the trip pack], each ≤ the trip pack (a trip
    pack under 5 % makes the steps equal: the 5 % step is skipped)."""
    if not weight:
        return [None, None, trip]
    return [min(_kg(p * weight), trip) for p in STAGE_PCT] + [trip]


def machine_cap(weight: Optional[float], trip: float) -> float:
    """The heaviest machine pack: min(1.15 × trip, 20 % body weight), ≥ the trip pack."""
    hi = OVERLOAD * trip
    if weight:
        hi = min(hi, BW_MAX * weight)
    return max(trip, math.floor(hi * 10 + 1e-9) / 10)        # a cap: rounded down to 0.1 kg


def loaded_min(kgs: list) -> float:
    return DONE_FRAC * kgs[0] if kgs and kgs[0] else NO_WEIGHT_LOADED_KG


def stage_done(kg: Optional[float], kgs: list) -> int:
    """The highest stage a session with `kg` completes (kg ≥ 0.8 × that stage's pack); 0 = none."""
    if not kg or kg < loaded_min(kgs):
        return 0
    best = 0
    for k, s in enumerate(kgs, 1):
        if s is None or kg >= DONE_FRAC * s:
            best = k
    return best


def nominal_stage(weeks_out: int, phase: Optional[dict], monday: dt.date) -> int:
    """By weeks before the trip (auto phases), or by the share of a manual 專項期."""
    if phase and phase.get("kind") == "specific" and phase.get("auto") is False:
        s, e = _d(phase["start"]), _d(phase["end"])
        span = max(1, (e - s).days + 1)
        f = ((monday + dt.timedelta(days=3)) - s).days / span
        return 1 if f < 0.25 else 2 if f < 0.5 else 3
    for lo, k in STAGE_WEEKS:
        if weeks_out >= lo:
            return k
    return 3


def weeks_out(start: dt.date, monday: dt.date) -> int:
    """賽前第 n 週 as in b2b.week_context (the week's Monday, rounded up)."""
    return -(-(start - monday).days // 7)


# ---------------------------------------------------------------------------
# done loaded sessions (the pack per activity, racepower_hike_meta.json)
# ---------------------------------------------------------------------------

def meta_pack_of(meta: Optional[dict] = None) -> Callable:
    """w → the recorded pack (kg) of a dataset workout, else None."""
    if meta is None:
        from backend.engine.racepower import athlete as A
        meta = A.hike_meta()

    def f(w) -> Optional[float]:
        rec = meta.get(getattr(w.entry, "file", None)) or {}
        v = rec.get("pack_kg")
        return None if v is None else float(v)
    return f


def history(ds, since: dt.date, before: dt.date, pack_of: Callable, kgs: list) -> list[dict]:
    """Loaded endurance activities in [since, before): {day, kg, minutes, long, idx, stage}."""
    from backend.engine import overview as O
    out = []
    for w in O.workouts_between(ds, since, before):
        if O.category(w) not in O.ENDURANCE:
            continue
        kg = pack_of(w)
        if not kg or kg < loaded_min(kgs):
            continue
        m = O.moving_s(w) / 60.0
        out.append({"day": O.wdate(w).isoformat(), "kg": float(kg), "minutes": round(m), "long": m >= LONG_LOADED_MIN,
                    "idx": w.idx, "stage": stage_done(kg, kgs), "done": True})
    return sorted(out, key=lambda r: r["day"])


def me_count(ds, since: dt.date, before: dt.date) -> int:
    """Strength activities since the ME block started (the ME session count, 推估)."""
    from backend.engine import overview as O
    return sum(1 for w in O.workouts_between(ds, since, before) if O.category(w) == "strength")


# ---------------------------------------------------------------------------
# the week's context
# ---------------------------------------------------------------------------

def week_context(*, kind: str, mode: str, monday: dt.date, event: Optional[dict], weight: Optional[float],
                 phase: Optional[dict], state: dict, tsb: Optional[float] = None, me_ok: Optional[bool] = None,
                 me_why: str = "") -> dict:
    """What this week may carry. `state`: {"loaded": history() rows (+ the
    planned ones of the projected weeks before), "me_n": ME sessions so far}."""
    info = {"active": False, "event": event, "step": None, "weeks_out": None, "weight": weight,
            "why": [], "blocked": [], "me": None, "src": SRC_WHEN, "monday": monday.isoformat()}
    if not qualifies(event):
        info["blocked"].append("下一場 A 賽事不是百岳或多日行程" if event else "沒有下一場 A 賽事")
        return info
    start = _d(event["start"])
    w = weeks_out(start, monday)
    trip = trip_kg(event)
    kgs = stage_kgs(weight, trip)
    rows = [r for r in state.get("loaded") or [] if _d(r["day"]) >= start - dt.timedelta(weeks=ME_WEEKS[1])]
    lo28 = monday - dt.timedelta(days=21)
    longs = [r for r in rows if r.get("long")]
    info.update(active=True, weeks_out=w, days_to=(start - monday).days, trip_kg=trip, kgs=kgs,
                machine_cap=machine_cap(weight, trip), phase_kind=kind,
                count28=sum(1 for r in rows if lo28 <= _d(r["day"]) < monday),
                last_long=longs[-1]["day"] if longs else None, last_loaded=rows[-1]["day"] if rows else None,
                max_done=max((r.get("stage") or 0 for r in rows), default=0), done_n=len(rows),
                loaded_days=[r["day"] for r in rows if _d(r["day"]) < monday])
    me_n = int(state.get("me_n") or 0)
    info["me_n"] = me_n
    me_week = (kind == "base" and BASE_ME_FROM <= w <= ME_WEEKS[1]) or (kind == "specific" and w >= ME_WEEKS[0])
    if mode in ("recovery_week", "reentry"):
        info["blocked"].append("恢復週／停訓後恢復期：不背包、不做 ME")
        return info
    if me_week:
        if me_ok:
            info["me"] = {"n": me_n, "weight": weight}
        else:
            info["why"].append(me_why or "沒有實測 AeT 或 AeT–LTHR 差距 > 10 %：維持一般肌力（UA 的 ME 前提）")
    if kind == "base":
        if BASE_ME_FROM <= w <= ME_WEEKS[1]:
            info["step"] = "me"
            info["why"].append(f"基礎後期（賽前第 {w} 週）：只做 ME 健身房版，不背包")
        else:
            info["blocked"].append(f"賽前第 {w} 週：基礎期前段不背包" if w > ME_WEEKS[1] else "基礎期：不背包")
        return info
    if kind == "taper":
        info["step"] = "taper"
        return info
    if kind != "specific":
        info["blocked"].append("只在基礎後期、專項期、減量期排")
        return info
    nominal = nominal_stage(w, phase, monday)
    eff = max(1, min(nominal, info["max_done"] + 1))
    info["step"] = eff
    info["stage_kg"] = kgs[eff - 1]
    info["stage_pct"] = (STAGE_PCT + (None,))[eff - 1]
    if eff < nominal:
        info["why"].append(f"上一階段（{_stage_label(eff, kgs, weight)}）還沒做過一次：先維持，不跳級（UA stairstep，推估）")
    info["machine_kg"] = info["stage_kg"]
    if eff == 3 and w <= OVERLOAD_WEEKS:
        info["machine_kg"] = info["machine_cap"]
    info["long_first"] = not any(r.get("long") and (r.get("stage") or 0) >= eff for r in rows)
    if tsb is not None and tsb < TSB_MIN:
        info["hold"] = True
        info["blocked"].append(f"週初 TSB {tsb:+.0f} < {TSB_MIN:.0f}：這週先不背包")
    info["why"].append(f"專項 {eff}（賽前第 {w} 週）：{_stage_label(eff, kgs, weight)}")
    return info


def _stage_label(k: int, kgs: list, weight: Optional[float]) -> str:
    kg = kgs[k - 1]
    if k == 3:
        return f"行程背包 {kg:g} kg" + (f"（體重的 {kg / weight * 100:.0f}%）" if weight else "")
    pct = STAGE_PCT[k - 1] * 100
    return f"{kg:g} kg（體重的 {pct:.0f}%）" if kg is not None else f"體重的 {pct:.0f}%（沒有體重資料）"


def _event_json(ev) -> Optional[dict]:
    if ev is None:
        return None
    from backend.engine import b2b as B2B
    return B2B.event_json(ev)


def plan_context(ds, status, today: dt.date, monday: dt.date, mode: str, tsb: Optional[float], gate: dict,
                 pack_of: Optional[Callable] = None) -> dict:
    """week_context() from week_plan()'s data. Never raises (a failure = no loaded carry)."""
    try:
        from backend.engine import b2b as B2B
        from backend.engine import quality_gate as QG
        ev = _event_json(B2B.target_event(status.plan.events, today))
        weight = status.plan.weight_on(today) if getattr(status, "plan", None) else None
        if not weight:
            try:
                from backend.engine.wko5expr.dataset import date_to_day
                weight = ds.setting("weight", date_to_day(today))
            except Exception:              # noqa: BLE001
                weight = None
        if not qualifies(ev):
            return week_context(kind=status.kind or "base", mode=mode, monday=monday, event=ev, weight=weight,
                                phase=None, state={})
        pack_of = pack_of or meta_pack_of()
        start = _d(ev["start"])
        kgs = stage_kgs(weight, trip_kg(ev))
        since = start - dt.timedelta(weeks=ME_WEEKS[1] + 1)
        rows = history(ds, since, monday, pack_of, kgs)
        me_from = start - dt.timedelta(weeks=ME_WEEKS[1])
        me_from = me_from - dt.timedelta(days=me_from.weekday())
        me_ok, me_why = me_gate(gate)
        ph = getattr(status, "phase", None)
        phase = {"kind": ph.kind, "start": ph.start, "end": ph.end, "auto": getattr(ph, "auto", True)} if ph else None
        info = week_context(kind=status.kind or "base", mode=mode, monday=monday, event=ev, weight=weight, phase=phase,
                            state={"loaded": rows, "me_n": me_count(ds, me_from, monday) if monday > me_from else 0},
                            tsb=tsb, me_ok=me_ok, me_why=me_why)
        info["history"] = rows
        info["me_ok"], info["me_why"] = me_ok, me_why
        info["phase_auto"] = (phase or {}).get("auto", True)
        return info
    except Exception as e:                  # noqa: BLE001 — the plan must still build
        return {"active": False, "step": None, "error": type(e).__name__}


def me_gate(gate: Optional[dict]) -> tuple[bool, str]:
    """ME needs a measured AeT within 10 % of LTHR (UA; quality_gate.ua_gap)."""
    from backend.engine import quality_gate as QG
    ae, lt = (gate or {}).get("aet") or {}, (gate or {}).get("lthr") or {}
    if not ae.get("measured"):
        return False, "沒有實測 AeT：維持一般肌力（UA：ME 的前提是 AeT 在 LTHR 的 10 % 內）"
    if lt.get("value") is None or lt.get("default"):
        return False, "LTHR 還是預設值：維持一般肌力（ME 前提算不出來）"
    g = QG.ua_gap(ae.get("value"), lt.get("value"))
    if g is None or g > QG.UA_GAP_MAX:
        return False, f"AeT–LTHR 差距 {g * 100:.0f}% > 10%：先補有氧，維持一般肌力（UA）" if g is not None else "ME 前提算不出來"
    return True, f"AeT–LTHR 差距 {g * 100:.0f}% ≤ 10%"


# ---------------------------------------------------------------------------
# the sessions
# ---------------------------------------------------------------------------

def _hr_target(s: dict, aet: Optional[float], prefs=None, th: Optional[dict] = None) -> str:
    """target_policy(): pack sessions are HR sessions (≤ AeT); a forced power basis keeps HR as the cap."""
    from backend.engine.target_policy import target_policy
    pol = target_policy(s, prefs, th or {"aet": aet})
    hr = f"心率 ≤ AeT {aet:.0f} bpm" if aet else "心率 ≤ AeT"
    if pol["basis"] == "none":
        return ""
    if pol["basis"] == "power":
        return f"{hr}（背包時功率低估負荷：用心率當上限）"
    return hr


def _pct(kg: Optional[float], weight: Optional[float]) -> str:
    return f"（體重的 {kg / weight * 100:.0f}%）" if kg and weight else ""


def _rate(s: dict) -> float:
    m = s.get("minutes") or 0
    return float(s.get("tss") or 0.0) / m if m else 0.0


PACK_RE = re.compile(r"背 ?(\d+(?:\.\d+)?) ?kg")
B2B_PACK_RE = re.compile(r"百岳背包：.*?推估）。")


def planned_kg_of_title(title: Optional[str]) -> Optional[float]:
    m = PACK_RE.search(title or "")
    return float(m.group(1)) if m else None


def _mark_title(s: dict, kg: Optional[float]) -> None:
    t = PACK_RE.sub("", s.get("title") or "").rstrip(" ·")
    s["title"] = f"{t} · 背 {kg:g} kg" if kg is not None else t


def apply(ss: list[dict], info: Optional[dict], *, aet: Optional[float] = None, prefs=None, th: Optional[dict] = None,
          b2b: Optional[dict] = None, notes: Optional[list] = None, week_packs: Optional[dict] = None,
          rates: Optional[dict] = None) -> list[dict]:
    """Edit the week's placed sessions (dicts) in place for loaded carry and
    return them. `b2b`: the week's b2b info (due / post); `week_packs`:
    {activity index: recorded kg} of this week's activities (done sessions);
    sets info["planned"] (this week's loaded sessions) and info["week"]."""
    if not info or not info.get("active"):
        return ss
    info["planned"] = []
    info["week"] = {"long": None, "machine": None, "me": None, "taper": None, "notes": []}
    wk = info["week"]
    packs = week_packs or {}
    weight = info.get("weight")
    start = _d(info["event"]["start"])
    hike_rate = (rates or {}).get("hike") or 45.0

    def note(text: str, level: str = "info") -> None:
        wk["notes"].append(text)
        if notes is not None:
            notes.append({"level": level, "src": "loaded", "text": text})

    def done_kg(s: dict) -> Optional[float]:
        idx = (s.get("done_by") or {}).get("index")
        return packs.get(idx) if idx is not None else None

    # ---- ME (strength1 → Johnston's progression) ------------------------------
    me = info.get("me")
    if me is not None:
        st = next((s for s in ss if s.get("kind") == "strength"), None)
        if st is not None:
            _me_session(st, me["n"], weight)
            wk["me"] = {"n": me["n"] + 1, "day": st.get("day")}
    step = info.get("step")
    if step in (None, "me"):
        return ss
    taken = [_d(x) for x in info.get("loaded_days") or []]

    def room(d: Optional[dt.date]) -> bool:
        """Adding `d` keeps every 28-day window that contains it at ≤ 4 loaded sessions
        (the weeks after check theirs when they are planned)."""
        if d is None:
            return False
        for i in range(28):
            end = d + dt.timedelta(days=i)
            if 1 + sum(1 for x in taken if end - dt.timedelta(days=27) <= x <= end) > MAX_PER_28:
                return False
        return True

    def take(s: dict) -> None:
        taken.append(_d(s["day"]))

    # ---- taper: one short carry 8–14 days out, nothing in the last 7 -----------
    if step == "taper":
        last = _d(info.get("last_loaded"))
        kg = info["trip_kg"]
        full_taper = False
        for s in sorted((x for x in ss if x.get("kind") == "easy" and x.get("day")), key=lambda x: x["day"]):
            d = _d(s["day"])
            to = (start - d).days
            if s.get("done"):
                k = done_kg(s)
                if k and k >= DONE_FRAC * kg and TAPER_CARRY[0] <= to <= TAPER_CARRY[1]:
                    _carry_session(s, kg, TAPER_CARRY_MIN, info, aet, prefs, th, hike_rate, taper=True, keep=True)
                    info["planned"].append(_row(s, kg, False))
                    wk["taper"] = {"day": s["day"], "kg": kg, "done": True}
                    return ss
                continue
            if not (TAPER_CARRY[0] <= to <= TAPER_CARRY[1]):
                continue
            if last is not None and (d - last).days < TAPER_GAP_DAYS:
                continue
            if not room(d):
                full_taper = True
                continue
            _carry_session(s, kg, TAPER_CARRY_MIN, info, aet, prefs, th, hike_rate, taper=True)
            info["planned"].append(_row(s, kg, False))
            wk["taper"] = {"day": s["day"], "kg": kg, "done": False}
            return ss
        if full_taper:
            note("減量期：28 天內負重課已 4 次，不再加短課（Orr 2021）")
        if (start - _d(info["monday"])).days <= NO_PACK_DAYS + 6:
            wk["notes"].append(f"賽前 {NO_PACK_DAYS} 天內（{md(start - dt.timedelta(days=NO_PACK_DAYS))} 起）不背包、不做 ME（推估）")
        return ss

    # ---- specific: the loaded long day (B2B day 1) ----------------------------
    stage_kg = info.get("stage_kg")
    hold = bool(info.get("hold"))
    long_s = next((s for s in ss if s.get("id") == "long"), None)
    fol = sorted((s for s in ss if s.get("id") in ("long2", "long3")), key=lambda s: s["id"])
    b2b_due = bool((b2b or {}).get("due")) and bool(fol)
    long_loaded = False
    if long_s is not None and long_s.get("day") and not hold:
        L = _d(long_s["day"])
        last_long = _d(info.get("last_long"))
        why_not = None
        if (start - L).days < LAST_LONG_DAYS:
            why_not = f"離行程不到 {LAST_LONG_DAYS} 天：最後一次背包長天要在賽前 ≥ 14 天（推估）"
        elif last_long is not None and (L - last_long).days < LONG_SPACING_DAYS:
            why_not = (f"上一次背包長天 {md(last_long)} 不到 {LONG_SPACING_DAYS} 天（Orr 2021：每 10–14 天最多一次）"
                       + ("：B2B 這次不背" if b2b_due else "：長天不背，改排平日機器課（有空位時）"))
        elif not room(L):
            why_not = "28 天內負重課已 4 次（Orr 2021）"
        if long_s.get("done"):
            k = done_kg(long_s)
            if k:
                long_loaded = True
                take(long_s)
                info["planned"].append(_row(long_s, k, True, stage=stage_done(k, info["kgs"])))
                wk["long"] = {"day": long_s["day"], "kg": k, "done": True}
        elif why_not is None:
            _long_session(long_s, stage_kg, info, b2b_due, weight)
            long_s["target"] = _hr_target(long_s, aet, prefs, th)       # a pack day: HR ≤ AeT (target_policy)
            long_loaded = True
            take(long_s)
            info["planned"].append(_row(long_s, stage_kg, True, stage=info["step"]))
            wk["long"] = {"day": long_s["day"], "kg": stage_kg, "done": False, "minutes": long_s["minutes"],
                          "first": bool(long_s.get("pack_from"))}
        else:
            wk["long_blocked"] = why_not
        if long_loaded and b2b_due:
            for s in fol:
                if s.get("done"):
                    k = done_kg(s)
                    if k:
                        take(s)
                        info["planned"].append(_row(s, k, True, stage=stage_done(k, info["kgs"])))
                    continue
                if not s.get("day") or not room(_d(s["day"])):
                    note(f"{s.get('title', 'B2B')}：28 天內負重課已 4 次，這天不背包（Orr 2021）")
                    continue
                _follow_session(s, stage_kg, info, weight)
                s["target"] = _hr_target(s, aet, prefs, th)
                take(s)
                info["planned"].append(_row(s, stage_kg, True, stage=info["step"]))
    # ---- the weekday machine session: weeks without a loaded long day, never a B2B week ----
    post = (b2b or {}).get("post") or {}
    if not long_loaded and not b2b_due and not hold and stage_kg is not None:
        wk["machine"] = _machine(ss, info, room, post, aet, prefs, th, hike_rate, done_kg, note)
    elif not long_loaded and not b2b_due and not hold and stage_kg is None:
        note("沒有體重資料：背包重量（體重的 %）算不出來，這週不排機器背包課")
    return ss


def _row(s: dict, kg: Optional[float], long: bool, stage: Optional[int] = None) -> dict:
    return {"day": s.get("day"), "kg": kg, "long": long, "id": s.get("id"), "minutes": s.get("minutes"),
            "done": bool(s.get("done")), "stage": stage}


def _long_session(s: dict, kg: Optional[float], info: dict, b2b_due: bool, weight: Optional[float]) -> None:
    k = info["step"]
    first = info.get("long_first") and not b2b_due
    m = s["minutes"]
    if first:
        new = min(_r5(m * FIRST_LONG[0]), FIRST_LONG[1])
        if new < m:
            rate = _rate(s)
            s["pack_from"] = m
            s["minutes"] = new
            s["tss"] = round(rate * new, 1)
    s["pack_kg"] = kg
    _mark_title(s, kg)
    what = f"背 {kg:g} kg{_pct(kg, weight)}" if kg is not None else f"背體重的 {(info.get('stage_pct') or 0) * 100:.0f}%"
    down = ("背著下山，練百岳第 2、3 天的下坡（推估；Blacker 2010 下坡負重後恢復 72 小時）"
            if k >= DOWNHILL_STAGE else "下山可以把水倒掉減重（UA 的水壺做法）")
    firsttxt = (f"這個重量的第一次：時間 {s['pack_from']} → {s['minutes']} 分，同一重量做過一次才把時間加回去"
                f"（UA 一次只加一樣；× 0.75、≤ 3 小時為推估）。" if s.get("pack_from") else "")
    txt = (f"背包長天（專項 {k}）：{what}，用行程要用的那個背包；全程心率 ≤ AeT，爬坡放慢、不衝；{down}；"
           f"練行程吃的東西。{firsttxt}")
    det = B2B_PACK_RE.sub("", s.get("detail") or "")
    s["detail"] = (det.rstrip() + ("" if det.rstrip().endswith(("。", "；")) or not det.strip() else "。") + txt).strip()
    s["source"] = "；".join(x for x in (s.get("source"), SRC_WHEN, SRC_FREQ, SRC_PROG) if x)


def _follow_session(s: dict, kg: Optional[float], info: dict, weight: Optional[float]) -> None:
    s["pack_kg"] = kg
    _mark_title(s, kg)
    what = f"{kg:g} kg{_pct(kg, weight)}" if kg is not None else "和第 1 天一樣"
    s["detail"] = ((s.get("detail") or "").replace("背包和第 1 天一樣或更輕。", "").rstrip() +
                   f"背包和第 1 天一樣：{what}（多日行程每天只少吃掉的食物）；"
                   + ("背著下山。" if info["step"] >= DOWNHILL_STAGE else "")).strip()


def _carry_session(s: dict, kg: float, minutes: int, info: dict, aet, prefs, th, rate: float,
                   taper: bool = False, keep: bool = False) -> None:
    weight = info.get("weight")
    s.update(id="carry", kind="hike", terrain="hike", pack_kg=kg)
    if not keep:
        s["minutes"] = int(minutes)
    s["tss"] = round(rate * s["minutes"] / 60.0, 1)
    if taper:
        s["title"] = f"背包短課 · 背 {kg:g} kg"
        s["detail"] = (f"減量期的最後一次負重：{s['minutes']} 分跑步機坡度或短爬坡，行程背包 {kg:g} kg{_pct(kg, weight)}，"
                       f"心率 ≤ AeT；量少、強度維持，之後到行程前都不背包。")
        s["source"] = SRC_TAPER
    else:
        over = kg > info["trip_kg"]
        s["title"] = f"背包爬坡機 · 背 {kg:g} kg"
        s["detail"] = (f"跑步機坡度 12–15% 或樓梯機：暖身 5–10 分不背 → 背包上坡 30–40 分 → 緩和 5 分；心率 ≤ AeT，"
                       f"從平常到 AeT 的設定開始，背包加重時降速度、不降坡度；用走的，不背包跑步。"
                       f"背 {kg:g} kg{_pct(kg, weight)}"
                       + (f"：專項 3 的最後幾次可以比行程背包重一點（≤ 1.15 倍、≤ 20% 體重，1.15 為推估）" if over else "")
                       + "。機器沒有下坡，只能補週末山路的不足。")
        s["source"] = "；".join((SRC_MACHINE, SRC_FREQ, SRC_CAP))
    s["target"] = _hr_target(s, aet, prefs, th)


def _machine(ss: list[dict], info: dict, room: Callable, post: dict, aet, prefs, th, rate: float,
             done_kg: Callable, note: Callable) -> Optional[dict]:
    """Turn one easy run into the 40–50 min machine session (minutes from the
    easy total: the week doesn't grow). A done easy run that carried the pack
    becomes the done machine session."""
    kg = info.get("machine_kg") or info.get("stage_kg")
    for s in ss:
        if s.get("kind") == "easy" and s.get("done"):
            k = done_kg(s)
            if k and k >= DONE_FRAC * (info.get("stage_kg") or kg):
                _carry_session(s, k, s["minutes"], info, aet, prefs, th, rate, keep=True)
                info["planned"].append(_row(s, k, False, stage=stage_done(k, info["kgs"])))
                return {"day": s["day"], "kg": k, "done": True, "minutes": s["minutes"]}
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race")
                                                         or s.get("id") in ("long", "long2", "long3"))]
    until = _d(post.get("until")) if post else None
    cap = getattr(prefs, "cap_weekday", None) if prefs is not None and getattr(prefs, "active", False) else None
    cands, full = [], False
    for s in ss:
        if s.get("kind") != "easy" or s.get("done") or not s.get("day"):
            continue
        d = _d(s["day"])
        if not room(d):
            full = True
            continue                                  # ≤ 4 loaded sessions in any 28 days (Orr 2021)
        if any(abs((d - h).days) < 2 for h in hard):
            continue                                  # 48 h from the long day / quality (plan_prefs hard-day rule)
        if until is not None and d <= until:
            continue                                  # the easy days after a B2B
        m = MACHINE_MIN[1] if d.weekday() >= 5 or cap is None else min(MACHINE_MIN[1], int(cap))
        if m < MACHINE_FLOOR:
            continue
        cands.append((d.weekday() >= 5, -min(abs((d - h).days) for h in hard) if hard else 0, d, s, m))
    if not cands:
        note("28 天內負重課已 4 次：這週不排機器背包課（Orr 2021）" if full else
             "這週沒有可以放機器背包課的輕鬆日（長天、強度課的前後一天和 B2B 後的輕鬆日都不放）：這週不排（推估）")
        return None
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    _, _, d, s, m = cands[0]
    m = max(min(m, MACHINE_MIN[1]), min(MACHINE_MIN[0], m))
    delta = m - int(s.get("minutes") or 0)
    if delta:
        # the week's total is unchanged: the other easy runs give / take the difference
        others = [x for x in ss if x is not s and x.get("kind") == "easy" and not x.get("done")]
        for x in sorted(others, key=lambda x: -(x.get("minutes") or 0)):
            r = _rate(x)
            new = max(20, int(x["minutes"]) - delta)
            delta -= int(x["minutes"]) - new
            x["minutes"], x["tss"] = new, round(r * new, 1)
            if delta <= 0:
                break
    _carry_session(s, kg, m, info, aet, prefs, th, rate)
    info["planned"].append(_row(s, kg, False, stage=info["step"]))
    return {"day": s["day"], "kg": kg, "done": False, "minutes": m}


def _me_session(s: dict, n: int, weight: Optional[float]) -> None:
    """Johnston's ME progression by the session count n (0-based)."""
    def vest(p):
        return f"背心 {p * 100:.0f}% 體重" + (f"（約 {_kg(p * weight):g} kg）" if weight else "")
    if n < 2:
        dose = "不負重，組間休 60 秒"
    elif n == 2:
        dose = "不負重，組間休 45 秒"
    elif n < 8:
        dose = f"{vest(0.10)}，組間休息從 45 秒逐步縮短"
    else:
        dose = f"{vest(0.15)}，組間休息縮到 10–15 秒"
    s.update(id="me", title=f"肌耐力（ME）第 {n + 1} 次")
    s["detail"] = (f"Johnston 的健身房 ME：Split Jump Squat、Squat Jump、Box Step Up、Front Lunge 各 10 下為一組（約每秒 1 下），"
                   f"共 6 組；這次{dose}。強度看感覺：還能講話、腿在燒、限制是腿不是呼吸，心率可以超過 AeT；"
                   f"有氧量照常、ME 加在上面；之後安排輕鬆日。")
    s["source"] = SRC_ME
    s["target"] = ""


# ---------------------------------------------------------------------------
# projection glue
# ---------------------------------------------------------------------------

PUBLIC = ("active", "event", "step", "weeks_out", "days_to", "weight", "trip_kg", "kgs", "machine_cap", "stage_kg",
          "stage_pct", "machine_kg", "count28", "last_long", "last_loaded", "max_done", "long_first", "why", "blocked",
          "me", "week", "planned", "hold", "src", "error", "me_ok", "me_why", "phase_kind", "me_n", "history",
          "phase_auto")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}


def next_state(info: Optional[dict], state: Optional[dict] = None) -> dict:
    """The state carried into the next week: this week's loaded sessions count as done."""
    info = info or {}
    st = state if state is not None else {"loaded": info.get("history") or [], "me_n": info.get("me_n") or 0}
    rows = list(st.get("loaded") or [])
    for r in info.get("planned") or []:
        if r.get("day"):
            rows.append({"day": r["day"], "kg": r.get("kg"), "long": bool(r.get("long")),
                         "stage": r.get("stage") or 0, "done": r.get("done")})
    rows.sort(key=lambda r: r["day"])
    me_n = int(st.get("me_n") or 0)
    if (info.get("week") or {}).get("me"):
        me_n = int(info["week"]["me"]["n"])
    return {"loaded": rows, "me_n": me_n}


def projected_context(kind: str, mode: str, monday: dt.date, cur: Optional[dict], state: dict,
                      phases: Optional[list] = None) -> dict:
    """week_context() for a projected week from week_plan()'s loaded_carry entry."""
    cur = cur or {}
    ph = None
    for p in phases or []:
        g = (lambda k: p.get(k)) if isinstance(p, dict) else (lambda k: getattr(p, k, None))
        if _d(g("start")) <= monday + dt.timedelta(days=3) <= _d(g("end")):
            ph = {"kind": g("kind"), "start": g("start"), "end": g("end"),
                  "auto": g("auto") if g("auto") is not None else cur.get("phase_auto", True)}
    return week_context(kind=kind, mode=mode, monday=monday, event=cur.get("event"), weight=cur.get("weight"),
                        phase=ph, state=state, me_ok=cur.get("me_ok"), me_why=cur.get("me_why") or "")


# ---------------------------------------------------------------------------
# the pack per activity (the activity-tags API)
# ---------------------------------------------------------------------------

def planned_kg_on(day: dt.date, db_path=None) -> Optional[float]:
    """The pack of a stored plan session on `day` (its title 「… · 背 X kg」)."""
    try:
        from backend.engine import plan_store as PS
        rows = PS._plan_rows(db_path, ("long", "hike", "easy"), _PLANNED_CACHE)
    except Exception:                       # noqa: BLE001
        return None
    for r in rows:
        if r.get("day") == day.isoformat():
            kg = planned_kg_of_title(r.get("title"))
            if kg is not None:
                return kg
    return None


_PLANNED_CACHE: dict = {}


def activity_pack(w, meta: Optional[dict] = None, planned: Optional[float] = None) -> dict:
    """{pack_kg (recorded), recorded, planned_kg, default_kg, range, note} of one dataset workout."""
    if meta is None:
        from backend.engine.racepower import athlete as A
        meta = A.hike_meta()
    rec = (meta.get(getattr(w.entry, "file", None)) or {}).get("pack_kg")
    if planned is None:
        try:
            planned = planned_kg_on(w.entry.start.date())
        except Exception:                   # noqa: BLE001
            planned = None
    return {"pack_kg": None if rec is None else float(rec), "recorded": rec is not None, "planned_kg": planned,
            "default_kg": planned if rec is None else float(rec), "range": [0, 40], "file": w.entry.file,
            "note": "這次背多少（kg）：寫進 racepower_hike_meta.json，負重訓練的評估與百岳預測都會用；空白 = 沒記錄"}


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def train_windows(rows: list[dict], aet: Optional[float]) -> list[dict]:
    """Climbing windows in the AeT-adjacent band (hikehr.filter_windows hr_band)."""
    from backend.engine.racepower import hikehr as HH
    if not aet:
        return []
    return HH.filter_windows(sorted(rows, key=lambda r: r.get("k", 0)), aet, hr_band=TRAIN_HR_BAND)


def _band(g: float) -> Optional[tuple]:
    from backend.engine.racepower import hikehr as HH
    for lo, hi in HH.GRADE_BANDS:
        if lo <= g < hi:
            return (lo, hi)
    return None


def _down_v(rows: list[dict]) -> list[float]:
    return [r["v"] for r in rows if r.get("g") is not None and r.get("v") and DOWN_G[0] <= r["g"] <= DOWN_G[1]
            and r["v"] * 3.6 > 1.5]


def evaluate(loaded_rows: list[dict], ref_rows: list[dict], kg: float, weight: Optional[float],
             aet: Optional[float], ref_kg: float = 0.0, v_run: Optional[float] = None) -> dict:
    """One loaded session against the unloaded windows: ΔHR@VAM, 負重效率 E_L
    (per grade band, weighted by the loaded windows), late drift, downhill."""
    import numpy as np
    from backend.engine.racepower import capacity as CAP
    from backend.engine.racepower import hikehr as HH
    lw, rw = train_windows(loaded_rows, aet), train_windows(ref_rows, aet)
    res, e_parts, bands = [], [], []
    e_aet = CAP.aet_power(weight, v_run or V_RUN_DEFAULT) if weight else None
    for band in sorted({_band(w["g"]) for w in lw} - {None}):
        lb = [w for w in lw if _band(w["g"]) == band]
        rb = [w for w in rw if _band(w["g"]) == band]
        row = {"band": f"{band[0]:.0%}–{band[1]:.0%}" if band[1] < 1 else f"≥ {band[0]:.0%}",
               "n_loaded": len(lb), "n_ref": len(rb), "hr_shift_bpm": None, "vam_ratio": None, "pred": None, "e": None}
        if len(rb) >= HH.FAT_MIN_N and len(lb) >= 1:
            x = np.array([w["vam"] for w in rb], float)
            y = np.array([w["hr"] for w in rb], float)
            if np.ptp(x) > 1e-6:
                b, a = np.polyfit(x, y, 1)
                r = [w["hr"] - (a + b * w["vam"]) for w in lb]
            else:
                r = [w["hr"] - float(np.median(y)) for w in lb]
            res.extend((w.get("k", 0), v) for w, v in zip(lb, r))
            row["hr_shift_bpm"] = round(float(median(r)), 1)
        if len(rb) >= HH.FAT_MIN_N and len(lb) >= HH.FAT_MIN_N:
            obs = median(w["vam"] for w in lb) / median(w["vam"] for w in rb)
            row["vam_ratio"] = round(float(obs), 3)
            if e_aet:
                g = median(w["g"] for w in lb)
                pred = CAP.pack_ratio(g, kg, ref_kg, weight, e_aet)
                if pred > 0:
                    row["pred"] = round(pred, 3)
                    row["e"] = round(obs / pred, 3)
                    e_parts.append((row["e"], len(lb)))
        bands.append(row)
    hr_shift = round(float(median(v for _, v in res)), 1) if len(res) >= HH.FAT_MIN_N else None
    e_l = round(sum(e * n for e, n in e_parts) / sum(n for _, n in e_parts), 3) if e_parts else None
    drift = None
    if len(res) >= 9:
        res.sort()
        t = len(res) // 3
        drift = round(float(median(v for _, v in res[-t:]) - median(v for _, v in res[:t])), 1)
    dl, dr = _down_v(loaded_rows), _down_v(ref_rows)
    down = round(float(median(dl) / median(dr)), 3) if len(dl) >= HH.FAT_MIN_N and len(dr) >= HH.FAT_MIN_N else None
    return {"hr_shift_bpm": hr_shift, "e_l": e_l, "late_drift_bpm": drift, "down_ratio": down, "bands": bands,
            "n": {"loaded": len(lw), "ref": len(rw)}, "kg": kg, "label": "推估",
            "enough": hr_shift is not None or e_l is not None,
            "text": _eval_text(hr_shift, e_l, len(lw))}


def _eval_text(hr: Optional[float], e: Optional[float], n: int) -> str:
    if hr is None and e is None:
        return f"AeT 附近的爬坡段不夠（{n} 段，要 ≥ 5 段 100 m、坡度 ≥ 10%，還要有不背包的對照）：只記時間和重量"
    parts = []
    if hr is not None:
        parts.append(f"同樣爬坡速度，背包時心率 {hr:+.1f} bpm")
    if e is not None:
        parts.append(f"負重效率 {e:.2f}（1 = 和 Pandolf 預測一樣；> 1 背得比預測好）")
    return "；".join(parts)


def stage_trend(sessions: list[dict]) -> list[dict]:
    """Within each weight stage: the first vs the last judged session (E_L up
    ≥ 5 % or ΔHR down ≥ 3 bpm = 進步; 推估). Different stages are never compared."""
    out = []
    for k in sorted({s.get("stage") or 0 for s in sessions} - {0}):
        js = [s for s in sessions if (s.get("stage") or 0) == k and (s.get("eval") or {}).get("enough")]
        row = {"stage": k, "n": sum(1 for s in sessions if (s.get("stage") or 0) == k), "judged": len(js),
               "direction": None, "text": "同一重量要至少兩次有判讀才看得出趨勢", "label": "推估"}
        if len(js) >= 2:
            a, b = js[0]["eval"], js[-1]["eval"]
            de = (b["e_l"] - a["e_l"]) if a.get("e_l") is not None and b.get("e_l") is not None else None
            dh = (b["hr_shift_bpm"] - a["hr_shift_bpm"]) if a.get("hr_shift_bpm") is not None and \
                b.get("hr_shift_bpm") is not None else None
            better = (de is not None and de >= E_SAME) or (dh is not None and dh <= -HR_SAME_BPM)
            worse = (de is not None and de <= -E_SAME) or (dh is not None and dh >= HR_SAME_BPM)
            row["direction"] = "better" if better and not worse else "worse" if worse and not better else "flat"
            row["change"] = {"e_l": None if de is None else round(de, 3), "hr_bpm": None if dh is None else round(dh, 1)}
            row["text"] = {"better": "同一重量背得比較輕鬆了", "worse": "同一重量比上次吃力：看睡眠、補給、熱",
                           "flat": "同一重量差不多"}[row["direction"]]
        out.append(row)
    return out


def card(ds, today: dt.date, events, phase=None, cur: Optional[dict] = None, weeks: Optional[list] = None,
         aet_of: Optional[Callable] = None, weight: Optional[float] = None, pack_of: Optional[Callable] = None) -> dict:
    """The overview's 負重訓練 card."""
    from backend.engine import b2b as B2B
    from backend.engine import overview as O
    ev = _event_json(B2B.target_event(events, today))
    pack_of = pack_of or meta_pack_of()
    lc = (cur or {}).get("loaded_carry") or {}
    weight = weight or lc.get("weight")
    if not weight:
        try:
            from backend.engine.wko5expr.dataset import date_to_day
            weight = ds.setting("weight", date_to_day(today))
        except Exception:                  # noqa: BLE001
            weight = None
    trip = trip_kg(ev) if ev else None
    kgs = stage_kgs(weight, trip) if trip else [None, None, None]
    lo = today - dt.timedelta(days=CARD_DAYS)
    acts = [w for w in O.workouts_between(ds, lo - dt.timedelta(days=REF_DAYS), today + dt.timedelta(days=1))
            if O.category(w) in O.ENDURANCE]
    wins: dict = {}

    def windows(w):
        if w.idx not in wins:
            try:
                wins[w.idx] = B2B._windows(ds, w)
            except Exception:              # noqa: BLE001 — one broken file never breaks the card
                wins[w.idx] = []
        return wins[w.idx]

    done = []
    for w in acts:
        kg = pack_of(w)
        day = O.wdate(w)
        if day < lo or not kg or kg < loaded_min(kgs):
            continue
        aet = aet_of(day) if aet_of else None
        refs = [x for x in acts if O.category(x) in O.MOUNTAIN and x.idx != w.idx
                and day - dt.timedelta(days=REF_DAYS) <= O.wdate(x) < day
                and (pack_of(x) == 0 or (pack_of(x) is None and O.category(x) == "trail"))]
        rows = windows(w)
        mins = O.moving_s(w) / 60.0
        if not any(r.get("g") is not None and r["g"] >= 0.10 for r in rows):
            r = {"enough": False, "text": "沒有 GPS 爬升（機器課或平路）：只記時間和重量", "n": {"loaded": 0, "ref": 0},
                 "label": "推估"}
        else:
            # each reference activity's windows keep their own runs (k offset per activity)
            ref_rows = [dict(y, k=y.get("k", 0) + 100000 * (i + 1)) for i, rr in enumerate(refs) for y in windows(rr)]
            r = evaluate(rows, ref_rows, float(kg), weight, aet)
        done.append({"day": day.isoformat(), "idx": w.idx, "kg": float(kg), "pct": (kg / weight) if weight else None,
                     "minutes": round(mins), "long": mins >= LONG_LOADED_MIN, "category": O.category(w),
                     "stage": stage_done(kg, kgs), "aet": aet, "eval": r})
    planned = []
    for src_w, this in [((cur or {}), True)] + [(x, False) for x in weeks or []]:
        L = src_w.get("loaded_carry") or {}
        for p in L.get("planned") or []:
            if not p.get("done"):
                planned.append({**p, "this_week": this})
    recent = []
    for w in acts:
        day = O.wdate(w)
        if day < today - dt.timedelta(days=21):
            continue
        rec = pack_of(w)
        plan_kg = next((p.get("kg") for p in lc.get("planned") or [] if p.get("day") == day.isoformat()), None)
        if plan_kg is None:
            plan_kg = planned_kg_on(day)
        recent.append({"idx": w.idx, "day": day.isoformat(), "category": O.category(w),
                       "minutes": round(O.moving_s(w) / 60.0), "pack_kg": rec, "planned_kg": plan_kg})
    recent.sort(key=lambda r: r["day"], reverse=True)
    return {"today": today.isoformat(), "event": ev, "active": qualifies(ev) or bool(done),
            "weight": weight, "trip_kg": trip, "kgs": kgs,
            "machine_cap": machine_cap(weight, trip) if trip else None,
            "this_week": {k: lc.get(k) for k in ("step", "weeks_out", "stage_kg", "machine_kg", "count28", "last_long",
                                                 "why", "blocked", "week", "me", "me_why", "long_first")} if lc else None,
            "planned": planned, "done": done, "trend": stage_trend(done), "recent": recent[:12],
            "thresholds": {"hr_bpm": HR_SAME_BPM, "e": E_SAME, "hr_band": list(TRAIN_HR_BAND), "label": "推估"},
            "rules": {"when": SRC_WHEN, "freq": SRC_FREQ, "prog": SRC_PROG, "cap": SRC_CAP, "machine": SRC_MACHINE,
                      "taper": SRC_TAPER, "me": SRC_ME, "down": SRC_DOWN, "hr": SRC_HR, "eval": SRC_EVAL}}
