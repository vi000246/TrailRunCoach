"""
熱適應課 — docs/research/heat-acclimation.md §3.3, §3.4, §5.4. Applied after a
week's sessions are placed (overview.week_plan, projection.week_sessions).

When (all of them, 推估 from §3.4):
  1. plan.prefs.heat ≠ off;
  2. an A or B event within 30 days that is hot (Event.heat hot, or auto →
     heat_data.event_is_hot);
  3. the projected race-day S (centre) < 0.75;
  4. not within 2 days of the race, not a recovery week / phase.
What:
  * induction block race − 21 → race − 8 days: every placed easy run (and the
    long run first — a hot long run is heat exposure already, rule 1 of the
    50-min cap) becomes a heat session;
  * maintenance race − 7 → race − 3: one every MAINTAIN_EVERY_D days.
  * a heat run keeps kind "easy" (+ heat=True): HR ≤ AeT, ≥ 60 min in the
    hottest part of the day (Racinais 2015: ≥ 60 min/day). Over the weekday
    cap it is exempt (like the CP test) with NOTE_HEAT; with cap_mode "hard"
    it becomes a 40-min run + a hot bath ≤ 40 min (Zurawlew 2016).
  * heat_method: run (default) / overdress (only on a cool day, Hadley < 120)
    / bath / sauna (a kind "heat_passive" session the same day: never a main
    day, TSS 0, never pushed to COROS) / mixed (alternating run and bath).
Nothing changes without a hot A/B race: the default plan is untouched.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.engine import heat as HT
from backend.engine import hr_profile as HP

METHODS = ("run", "overdress", "bath", "sauna", "mixed")
WINDOW_D = 30
INDUCT = (21, 8)                # days before the race
MAINTAIN = (7, 3)
NO_HEAT_BEFORE_D = 2
HEAT_RUN_MIN = 60
HARD_CAP_RUN_MIN = 40
PASSIVE_MIN = {"bath": 40, "sauna": 30}
MIN_BLOCK_DAYS = 5
NOTE_HEAT = "熱適應課需要 ≥ 60 分鐘才有效（Racinais 2015），不受單次時間上限"
NOTE_SHORT = "熱適應日只有 {n} 天（< 5 天效果有限，Daanen 2018）"
SAFETY = ("頭暈、頭痛、噁心、發冷、突然不流汗、意識混亂就停下來降溫；發燒、腸胃炎、睡不好、"
          "曾熱中暑未經醫師評估時不要做（ACSM 2023；症狀清單待驗證）")
SRC_RUN = "Racinais 2015 共識（≥ 60 分/天）"
SRC_OVER = "Greenfield 2025；Ely 2018"
SRC_BATH = "Zurawlew 2016/2019"
SRC_SAUNA = "Scoon 2007"


def _d(x) -> dt.date:
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def hot_race(events, today: dt.date, acts: list) -> Optional[dict]:
    """The first A / B event within WINDOW_D days that is hot."""
    from backend.engine import heat_data as HD
    for e in sorted(events or [], key=lambda e: e.start):
        if (e.priority or "").upper() not in ("A", "B"):
            continue
        days = (e.start - today).days
        if days < 0 or days > WINDOW_D:
            continue
        h = HD.event_is_hot(e, acts)
        if h["hot"]:
            return {"event": e, "days_to": days, "source": h["source"]}
    return None


def _heat_run(s: dict, method: str, aet: Optional[float], cap: Optional[int], cap_mode: str,
              notes: list, i: int, aet_measured: bool = False) -> list[dict]:
    """Turn one placed easy / long session into a heat session; returns the
    extra heat_passive session(s). `aet` = the easy-run cap (hr_profile)."""
    m = method if method != "mixed" else ("run" if i % 2 == 0 else "bath")
    extra = []
    s["heat"] = True
    if s["kind"] == "easy":
        if m in ("bath", "sauna") or (cap_mode == "hard" and cap is not None and cap < HEAT_RUN_MIN):
            s["minutes"] = min(s["minutes"], HARD_CAP_RUN_MIN) if m != "sauna" else s["minutes"]
            pm = "bath" if m != "sauna" else "sauna"
            s["title"] = "輕鬆跑（跑後" + ("熱水浴" if pm == "bath" else "桑拿") + "）"
            extra.append(passive(s, pm))
        else:
            new = max(s["minutes"], HEAT_RUN_MIN)
            if cap is not None and new > cap and cap < HEAT_RUN_MIN:
                if NOTE_HEAT not in [n.get("text") for n in notes]:
                    notes.append({"level": "info", "src": "heat", "text": NOTE_HEAT})
            if new != s["minutes"] and s.get("minutes"):
                s["tss"] = float(s.get("tss") or 0.0) * new / s["minutes"]
            s["minutes"] = new
            s["title"] = "熱適應輕鬆跑" + ("（多穿衣服）" if m == "overdress" else "")
        s["target"] = HP.easy_cap_hr(aet, aet_measured)
        s["detail"] = (("涼爽天（Hadley < 120）多穿長袖或防風外套；" if m == "overdress" else
                        "一天最熱的時段；" if m == "run" else "在涼爽環境跑，跑完立刻") +
                       "照心率不照配速，配速會自然變慢。" + SAFETY)
        s["source"] = SRC_OVER if m == "overdress" else SRC_RUN if m == "run" else (SRC_BATH if m == "bath" else SRC_SAUNA)
    else:                                   # the long run: a hot long run is the exposure
        s["title"] = s["title"] + "（熱適應）"
        s["detail"] = (s.get("detail") or "") + "；在熱的時段跑，照心率不照配速。" + SAFETY
        s["source"] = (s.get("source") or "") + "；" + SRC_RUN
    return extra


def passive(s: dict, kind: str) -> dict:
    """A heat_passive session on the day of `s` (hot bath / sauna): not a main
    session, TSS 0 (no conversion was found), counted as a heat dose."""
    n = PASSIVE_MIN[kind]
    title = "跑後熱水浴 40 °C ≤ 40 分" if kind == "bath" else "跑後桑拿 80–90 °C 約 30 分"
    return {"id": f"heat_passive_{s['id']}", "kind": "heat_passive", "title": title, "minutes": n, "target": "",
            "detail": ("第一次縮短（熱水浴 ≤ 20 分、桑拿 ≤ 15 分，推估）；旁邊要有人、慢慢起身、事後補水。" + SAFETY),
            "source": SRC_BATH if kind == "bath" else SRC_SAUNA, "tss": 0.0, "day": s.get("day"),
            "done": False, "done_by": None, "heat": True}


def apply(sessions: list[dict], *, events, today: dt.date, prefs=None, aet: Optional[float] = None,
          mode: str = "", kind: str = "", notes: Optional[list] = None, acts: Optional[list] = None,
          s_now: Optional[float] = None, aet_measured: bool = False) -> dict:
    """Mutates `sessions` (dicts with day / kind / minutes …, already placed)
    and appends heat_passive sessions. Returns the heat info for the page."""
    notes = notes if notes is not None else []
    pref = getattr(prefs, "heat", "auto") or "auto"
    method = getattr(prefs, "heat_method", "run") or "run"
    if pref == "off":
        return {"active": False, "reason": "熱適應課已關閉"}
    if acts is None:
        from backend.engine import heat_data as HD
        acts, _ = HD.exposures()
        acts = acts + [{"date": d, "hot_min": HT.MIN_DOSE_MIN} for d in HD.completed_passive_dates()]
    hr = hot_race(events, today, acts)
    if hr is None:
        return {"active": False, "reason": "30 天內沒有熱天的 A / B 賽"}
    ev = hr["event"]
    race = ev.start
    cur = HT.current(acts, today) if s_now is None else {"s": s_now}
    base = HT.project(cur["s"], today, race)
    info = {"active": False, "event": {"id": ev.id, "name": ev.name, "date": ev.date}, "hot_source": hr["source"],
            "s_now": cur["s"], "s_race_before": base["center"], "method": method}
    if base["center"] >= HT.LEVELS[0][1]:
        return {**info, "reason": f"預估比賽日 S {base['center']:.0%} ≥ 75 %：不用加課"}
    if mode == "recovery_week" or kind in ("recovery", "transition"):
        return {**info, "reason": "恢復週 / 恢復期不排熱適應"}
    lo_i, hi_i = race - dt.timedelta(days=INDUCT[0]), race - dt.timedelta(days=INDUCT[1])
    lo_m, hi_m = race - dt.timedelta(days=MAINTAIN[0]), race - dt.timedelta(days=MAINTAIN[1])
    placed = sorted((s for s in sessions if s.get("day") and not s.get("done")
                     and s["kind"] in ("easy", "long") and s.get("kind") != "heat_passive"),
                    key=lambda s: (s["day"], s["kind"] != "long"))
    cap = getattr(prefs, "cap_weekday", None)
    cap_mode = getattr(prefs, "cap_mode", "soft") or "soft"
    picked, last_m = [], None
    for s in placed:
        d = _d(s["day"])
        if (race - d).days < NO_HEAT_BEFORE_D:
            continue
        if lo_i <= d <= hi_i:
            picked.append(s)
        elif lo_m <= d <= hi_m and (last_m is None or (d - last_m).days >= HT.MAINTAIN_EVERY_D):
            picked.append(s)
            last_m = d
    if not picked:
        return {**info, "reason": "這週沒有落在熱適應區塊（賽前 21–8 天誘導、7–3 天維持）的課"}
    extra = []
    for i, s in enumerate(picked):
        extra += _heat_run(s, method, aet, cap, cap_mode, notes, i, aet_measured)
    sessions.extend(extra)
    days = sorted({s["day"] for s in picked})
    induct = [x for x in days if lo_i <= _d(x) <= hi_i]
    week_block = [today + dt.timedelta(days=k) for k in range(7)]
    in_block = [d for d in week_block if lo_i <= d <= hi_i]
    if in_block and len(induct) < min(MIN_BLOCK_DAYS, len(in_block)):
        notes.append({"level": "info", "src": "heat", "text": NOTE_SHORT.format(n=len(induct))})
    planned = {_d(x): 1.0 for x in days}
    after = HT.project(cur["s"], today, race, planned)
    notes.append({"level": "watch", "src": "heat",
                  "text": f"{ev.name}（{ev.date}）預估是熱天：比賽日 S {base['center']:.0%} < 75 %，本週排 {len(days)} 次熱適應"
                          f"（排完約 {after['center']:.0%}，推估）"})
    return {**info, "active": True, "days": days, "s_race_after": after["center"],
            "s_race_band": [after["low"], after["high"]], "sessions": [s["id"] for s in picked + extra]}
