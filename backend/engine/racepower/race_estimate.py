"""
用近期比賽成績推完賽時間 (SP-293; docs/research/cold-start.md §2.1, §4.4, §5 T6; unsourced-rules.md §0.5.5).

The race calculator's model needs a CP (calc.predict: 「沒有 CP：請手動輸入」), so a new runner had no
prediction at all. The race results they already gave — the ONE shared list `athlete.race_results`
(engine/race_results.py: the 跑步經驗問卷's row, SP-290, and the 設定 block / confirmed activities of
SP-276's E pace; nothing is stored twice) — give a 推估 finish time until there is a CP:

  road   the newest usable road race (race_results.usable(trail=False): confirmed, the last 365 days) →
         Riegel on time, t = t_race × (d / d_race)^(1 / (1 + k)) with k = K (−0.07: unsourced-rules
         §0.5.5, ≈ the Stryd race-power table; riegel.power_from_prior_distance's time_ratio — the
         existing Riegel, constant RE). ±5–8 % for one race to another distance (§0.5.5, Vandewalle 2018
         not verified); optimistic towards the marathon (Vickers & Vertosick 2016 [C2]).
  trail  §0.5.5 as it stands: effort km (km + climb / 100, ITRA) × the flat easy pace × TRAIL_EASY_SHARE
         (0.85, 推估). The flat easy pace = the middle of the E pace (e_pace.of_race: Daniels VDOT of the
         same newest road race, SP-276 — the app has no other 「平路輕鬆配速」; the middle is 推估). Trail
         races in the list are not used (climbing distorts the time — SP-276 leaves them out too); the
         ITRA-index lookup of §0.5.5 needs an index the app doesn't have.
  百岳   not here: the walking model needs no CP.

With a CP (any source, or typed in) the calculator's own model is used — has_cp(); this is only the
no-CP fallback (POST /racepower/estimate, the page asks after /plan said 「沒有 CP」). A race of
≥ race_feasibility.MARATHON_KM with a self-reported week under race_feasibility.LOW_WEEK_H adds
「這個估法對週跑量少的人偏樂觀」 (the same line as the race feasibility card, SP-292).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _

K = -0.07                    # unsourced-rules.md §0.5.5 (= calc.DEFAULT_K)
TRAIL_EASY_SHARE = 0.85      # unsourced-rules.md §0.5.5 (推估): race pace per effort km = 0.85 × the flat easy pace
EP_DIVISOR = 100.0           # ITRA's effort km: km + climb / 100

TITLE = N_("還沒有 CP：先用你的比賽成績推估")
TILE = N_("預估完賽")
LABEL = N_("推估")
SOURCE = {"survey": N_("跑步經驗問卷填的"), "manual": N_("設定裡填的"), "activity": N_("你確認的活動")}
TXT_ROAD = N_("用 {src} {race} 推算：Riegel（k {k:+.2f}，平路配速固定）→ 約 {t}。一場成績推另一個距離大約 ±5–8 %（推估）；"
              "有 CP 之後會自動改用賽事計算機的模型")
TXT_TRAIL = N_("用 {src} {race} 推算：平路輕鬆配速約 {pace} /km（E 配速 {fast}–{slow} 的中間，Daniels VDOT {vdot:.1f}），"
               "等效公里 {ekm:.1f}（km + 爬升 ÷ 100）× 配速 × 0.85 → 約 {t}。越野的這個算法是推估；"
               "有 CP 之後會自動改用賽事計算機的模型")
NO_RACE = N_("也沒有最近一年的路跑成績可以推估：在「設定 → 個人資料 → 跑步經驗」或「設定 → 生理數據 → 比賽成績（E 配速）」"
             "填一場最近的路跑成績（距離、時間、日期）")
NO_VDOT = N_("最近的路跑成績算不出平路輕鬆配速（Daniels 的公式只適用 1.5 km 到全馬）：越野先沒辦法推估")
NO_BAIYUE = N_("百岳用步行模型，不需要 CP")


def _fmt(s: float) -> str:
    from backend.engine import race_results as RR
    return RR.fmt_time(int(round(s)))


def race_label(r: dict) -> str:
    """「10 K 45:00（2026-05-01）」 — the race the estimate comes from."""
    from backend.engine.e_pace import fmt_time
    return _("{km:g} K {time}（{date}）", km=round(float(r["distance_km"]), 2), time=fmt_time(r["time_s"]), date=r["date"])


def has_cp(d: dict, body) -> bool:
    """Whether calc.predict finds a CP for `body` on the derived inputs `d` (typed in, the chosen
    source, or the default one) — then the calculator's own model is used."""
    cpd = (d or {}).get("cp") or {}
    srcs = {s.get("id") for s in cpd.get("sources") or ()}
    sid = getattr(body, "cp_source", None)
    return bool(getattr(body, "cp", None)) or bool(sid in srcs and sid) or bool(cpd.get("default"))


def pick_road(rows: Optional[list], today: dt.date) -> Optional[dict]:
    """The newest usable road race of the shared list (race_results.usable: confirmed, last 365 days)."""
    from backend.engine import race_results as RR
    got = RR.usable(rows, today, trail=False)
    return got[0] if got else None


def riegel_time(race_km: float, race_s: float, km: float, k: float = K) -> float:
    """t = t_race × (km / race_km)^(1 / (1 + k)) — riegel.power_from_prior_distance's time ratio (D1)."""
    from backend.engine.racepower import riegel as R
    return float(race_s) * R.power_from_prior_distance(1.0, float(race_km), float(km), k)["time_ratio"]


def estimate(kind: str, km: float, gain_m: float, rows: Optional[list], today: dt.date,
             survey_h: Optional[float] = None) -> dict:
    """The no-CP estimate for a `kind` (road / trail) race of `km` / `gain_m` from the shared race
    results `rows`: {available, time_s, km, method (riegel / effort_pace), race, title, tile, label,
    text, note} — or {available: False, reason}. `survey_h`: the questionnaire's weekly hours
    (race_feasibility.survey_hours) for the low-volume line."""
    from backend.engine import race_feasibility as RF
    if kind == "baiyue":
        return {"available": False, "reason": _(NO_BAIYUE)}
    race = pick_road(rows, today)
    if race is None or not km or km <= 0:
        return {"available": False, "reason": _(NO_RACE)}
    src = _(SOURCE.get(race.get("source"), SOURCE["manual"]))
    out = {"available": True, "km": round(float(km), 2), "title": _(TITLE), "tile": _(TILE), "label": _(LABEL),
           "race": {k: race.get(k) for k in ("date", "distance_km", "time_s", "source", "name")}, "note": None}
    if kind == "road":
        t = riegel_time(race["distance_km"], race["time_s"], km)
        out.update(method="riegel", k=K, time_s=round(t),
                   text=_(TXT_ROAD, src=src, race=race_label(race), k=K, t=_fmt(t)))
    else:
        from backend.engine import e_pace as EP
        e = EP.of_race({"distance_m": float(race["distance_km"]) * 1000.0, "time_s": race["time_s"],
                        "date": race["date"], "source": race.get("source")}, today)
        if e is None:
            return {"available": False, "reason": _(NO_VDOT)}
        pace = (e["e_fast"] + e["e_slow"]) / 2.0
        ekm = float(km) + float(gain_m or 0.0) / EP_DIVISOR
        t = ekm * pace * TRAIL_EASY_SHARE
        out.update(method="effort_pace", time_s=round(t), pace_s_per_km=round(pace), effort_km=round(ekm, 1),
                   vdot=e["vdot"],
                   text=_(TXT_TRAIL, src=src, race=race_label(race), pace=EP.fmt_pace(pace), fast=EP.fmt_pace(e["e_fast"]),
                          slow=EP.fmt_pace(e["e_slow"]), vdot=e["vdot"], ekm=ekm, t=_fmt(t)))
    if survey_h is not None and survey_h < RF.LOW_WEEK_H and float(km) >= RF.MARATHON_KM:
        out["note"] = _(RF.OPTIMISTIC, h=survey_h)              # Vickers & Vertosick 2016 [C2]
    return out
