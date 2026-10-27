"""
Which number a session is run by — heart rate, power, or none — in ONE place
(docs/plans/workout-editor.plan.md §3.2.3–§3.2.4 `target_policy`). The
generator's text (plan_prefs.shape), the COROS push (sync/coros_workouts),
zones.WORKOUT_TARGETS' primary and the 課表 page all ask this function, which
replaces the four trail rules that disagreed (zones: trail = power;
plan_prefs._terrain_long: trail = HR; schedule.html autoText: non-road = HR;
the push: always HR).

Order: the session's own override (target_basis on the stored session, a user
edit) → 課表偏好 plan.prefs.target_basis (hr / power) → `auto`, by session
type (docs/research/vo2max-gate-and-trail-metric.md §2, zones-and-thresholds.md):
  road easy / long                                   功率 (% CP) with HR ≤ AeT as a cap (athlete's
                                                     call 2026-10-02; HR still guards heat / fatigue)
  trail easy, recovery, trail long days, 越野跑      心率 ≤ AeT (Uphill Athlete: the base
                                                     is built below AeT; long days: HR —
                                                     drift and the late-day cap protect)
  walking: 陡坡健走, 登山 / 健行 / 百岳 (SP-115)       心率 ≤ 75 % HRmax or RPE ≤ 13 uphill, never
                                                     below the easy cap (萩原・山本 2011;
                                                     hr_profile.walk_cap); downhill by feel
  Zone 3 / Zone 5 intervals, hill repeats 3–8 %     功率 (Stryd ≈ fixed metabolic load on
                                                     0–8 % grades, van Rassel 2026; HR lags
                                                     55–70 s, Hunt 2015) — HR only as a cap note
  long climbs                                        none: suggestions only (power above 8 %
                                                     and HR on 20′+ climbs both partial)
  downhill practice                                  none (Stryd power under-reads the descent's
                                                     metabolic load: −7 → +7 % power +90 %, VO2
                                                     +74 %, Gravina-Cognetti 2025; descend by skill)
  CP test                                            power (the all-out bouts are open)
  AeT test                                           by protocol: 徐國峰 90 / Friel → HR (or pace),
                                                     UA 60 / 40 / Evoke → a fixed power (SP-949: a
                                                     fixed pace when power is not in effect)
A forced basis the thresholds can't fill falls back (power without CP → HR, HR
without LTHR/AeT → none) with the reason. SP-493 (owner 2026-10-14): the road intervals' auto
power goes to HR when the CP is stale — no CP test and no race in base_check.EP_ALL_OUT_DAYS
(`cp_stale`, week_plan's thresholds; base_check.cp_stale) — the trail rules above stay HR.
SP-950 (owner 2026-10-27) widens it to one site-wide rule (engine/power_state.py): power only with a
power meter AND a fresh CP. ② a meter but a stale CP, ③ no meter: every power session (road easy /
long → HR ≤ the easy cap, intervals, hill repeats) is run by HR (SP-951: flat intervals by pace), a
chosen 功率 included; in ③ the stored choice is ignored, not deleted.

SP-951 (owner 2026-10-27, SP-950 問答 Q1-3): a first-class 配速 basis. The flat Zone 3 / Zone 5
intervals and tempo (session type "interval": the library's flat variants, the gate's road
sessions; never a hill / trail one) run by % threshold pace (thresholds.estimate_tpace; the % CP
band as pace = threshold pace ÷ (% CP), running power ≈ proportional to flat speed — Stryd, 推估)
with the HR as a cap note in the text, when power is not in effect (② / ③) or 課表偏好 / the
session picks 配速. No threshold pace → heart rate (+ RPE in the text) with the reason. 配速 picked
for a session that is not a flat interval (easy, long, hill repeats, trail) changes nothing there:
those follow 自動 (pace means nothing on a hill; easy / long keep their HR cap / power band).

SP-964 (owner 2026-10-27, 訓練依據 › 進階設定): 目標依據 per session type, plan.prefs.type_basis
{TYPE_KEYS key: basis} — road easy / long (心率／功率), the flat Zone 3 (節奏) / Zone 5 intervals
(心率／配速／功率), hill repeats (心率／功率) and the road long run's marathon-pace segment (心率／配速／功率,
mp_policy). Order: the session's own → the type's → 課表偏好's overall one → 自動. A type absent = the
overall one. Trail days, long climbs, walking, downhill and the tests keep their own rules (not
choosable). The same fallbacks as above (② / ③ a chosen power → pace / HR with the reason; pace
without a threshold pace → HR + RPE).
"""
from __future__ import annotations

import re
from typing import Optional

from backend.i18n import N_, _

BASES = ("auto", "hr", "power", "pace")
CHOSEN = ("hr", "power", "pace")          # a basis picked by hand (課表偏好 / the session's own)
LABEL = {"auto": N_("自動（依課表類型）"), "hr": N_("心率"), "power": N_("功率"), "pace": N_("配速"), "none": N_("不設目標")}
SRC = {
    "hr_base": N_("Uphill Athlete（AeT 以下累積有氧基礎）；長天後段心率飄移（Coyle & González-Alonso 2001）"),
    "power_easy": N_("路跑輕鬆／長跑看功率（Palladino Z1–Z2 % CP），心率以輕鬆跑上限為上限：天熱、疲勞時心率先到就放慢"),
    "power_iv": N_("Stryd 功率在 0–8% 坡≈固定代謝負荷（van Rassel 2026）；心率延遲 55–70 秒（Hunt 2015）"),
    # SP-951
    "pace_iv": N_("平路間歇看 % 閾值配速（閾值配速 ÷ % CP：平地上跑步的功率約和速度成正比，推估）；"
                  "心率延遲 55–70 秒（Hunt 2015），只當上限提醒"),
    "climb": N_("長爬坡：> 8% 功率低估、心率在 20 分以上才準（推估）"),
    # SP-247: Gravina-Cognetti et al. 2025 (Sports 13:294; power-gap-trail-validity.md §3 #2)
    "down": N_("下坡：Stryd 功率低估下坡的負荷（Gravina-Cognetti 2025：−7～+7 % 時功率 +90 %、耗氧只 +74 %），看下降量與技術"),
    "cp": N_("CP 測試：全力段不設上下限，事後用功率算 CP"),
    "aet": N_("AeT 測試：依方式（徐國峰 90／Friel 看心率；UA／Evoke 固定功率）"),
    "walk": N_("登山爬坡：75% 最大心率以下或 RPE ≤ 13（萩原・山本 2011）；沒有最大心率時 (220 − 年齡) × 0.75"
               "（長野縣 Safety Book，推估）；下坡看腿的感覺，不看心率"),
}
# the session type in 「自動：<type>看<basis>」 (the 目標用 label's why)
TYPE_WORD = {"easy": N_("輕鬆跑"), "trail_easy": N_("越野輕鬆跑"), "long": N_("長跑"), "trail_long": N_("山路長天"),
             "hike": N_("越野跑"), "walk": N_("登山爬坡"), "interval": N_("間歇"), "hill": N_("爬坡重複"),
             "climb": N_("長爬坡"), "downhill": N_("下坡練習"), "cp_test": N_("CP 測試"), "aet_test": N_("AeT 測試"),
             "other": N_("其他")}
TIP = ("課表的目標用心率還是功率。\n自動：路跑的輕鬆跑、長跑用功率，心率以輕鬆跑上限為上限（天熱、疲勞時心率先到就放慢）；"
       "越野輕鬆跑、山路長天、恢復跑用心率（≤ 輕鬆跑上限）；陡坡健走、登山健行的上坡用心率 ≤ 75% 最大心率或 RPE ≤ 13；3 區／5 區間歇和 3–8% 坡的爬坡重複用功率，"
       "心率只當上限提醒；長爬坡只給建議、不設目標；下坡練習不設目標；CP 測試用功率，AeT 測試依測試方式。\n"
       "心率：全部用心率區間（設定的課表心率區間；輕鬆跑上限＝Z2 上緣，有實測 AeT 時用實測值）。\n功率：全部用功率區間（Palladino % CP）；"
       "測試和下坡照它們自己的規則。\n每次課表也可以在編輯時改「目標用：自動／心率／功率」，只影響這次課表。")


CP_STALE = N_("CP 太舊（最近沒有 CP 測試或比賽）：間歇改用心率")      # SP-493's (kept: stored texts)
# SP-950 ②, when the caller has only the cp_stale flag (no power_state with the CP's age); SP-963: the
# recent all-out efforts count too, so a hard session / a race brings power back as well (R2: plain, no brackets)
CP_STALE_ALL = N_("CP 太舊，先用心率；做一次 CP 測試就切回功率，跑一次強度課或比賽也行")


def pref_basis(prefs) -> str:
    """課表偏好 target_basis (the old interval_target = hr reads as hr)."""
    b = getattr(prefs, "target_basis", "auto") if prefs is not None else "auto"
    if b == "auto" and getattr(prefs, "interval_target", "power") == "hr":
        return "hr"
    return b if b in BASES else "auto"


# ---- SP-964: 目標依據 per session type (訓練依據 › 進階設定) ------------------------------------------
TYPE_KEYS = ("easy", "long", "z3", "z5", "hill", "mp")
# what each type may be set to (pace only where it means something: the flat intervals and the MP segment;
# power is offered only with a power meter — the page's job, the stored one is ignored in ③ by the rules above)
TYPE_CHOICES = {"easy": ("hr", "power"), "long": ("hr", "power"), "z3": ("hr", "pace", "power"),
                "z5": ("hr", "pace", "power"), "hill": ("hr", "power"), "mp": ("hr", "pace", "power")}
TYPE_KEY_WORD = {"easy": N_("路跑輕鬆跑"), "long": N_("長跑"), "z3": N_("節奏・3 區間歇"), "z5": N_("5 區間歇"),
                 "hill": N_("爬坡重複"), "mp": N_("馬拉松配速段")}
# 「5 區」 words in a quality session's title, when it carries neither a ladder rung, a library variant nor a band
_Z5_WORDS = ("VO2", "5 區", "Zone 5", "短強度", "短間歇", "Supra", "超閾值")
_BAND = re.compile(r"(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*%\s*CP")


def type_map(prefs) -> dict:
    """plan.prefs.type_basis as {key: basis} (a Prefs holds it as ((key, basis), …), the plan inputs as a
    dict); keys / values that are not choosable dropped."""
    raw = getattr(prefs, "type_basis", None) if prefs is not None else None
    try:
        m = dict(raw or ())
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in m.items() if v in TYPE_CHOICES.get(k, ())}


def type_basis(prefs, key: Optional[str]) -> Optional[str]:
    """The basis set for session type `key` in 進階設定, None = follow the overall one."""
    return type_map(prefs).get(key) if key else None


def without_type(prefs, key: str):
    """`prefs` without type `key`'s own basis (what 自動 / the overall basis gives that row)."""
    from dataclasses import is_dataclass, replace
    from types import SimpleNamespace
    rest = tuple(sorted((k, v) for k, v in type_map(prefs).items() if k != key))
    if prefs is None:
        return None
    if is_dataclass(prefs):
        return replace(prefs, type_basis=rest)
    return SimpleNamespace(**{**vars(prefs), "type_basis": dict(rest)})


def flat_zone(s: dict) -> str:
    """z3 | z5 of a flat interval session: the caller's hint (s["_zone"]), the ladder rung (rung_key), the
    library variant's rung (interval_library.track_of), the band's middle (power_pct, variant_adj["band"] or
    「lo–hi% CP」 in the 說明 / 目標: ≥ Palladino Zone 4 = the Zone 5 track, interval_library.CLASS_RANGE),
    then the title's words; else z3."""
    from backend.engine import interval_library as IL
    z = s.get("_zone")
    if z in ("z3", "z5"):
        return z
    tr = IL.track_of(s.get("rung_key"))
    if tr:
        return tr
    v = IL.get(s.get("variant_key"))
    if v is not None:
        return IL.track_of(v.rung) or ("z5" if v.cls in ("Z4", "Z5") else "z3")
    band = s.get("power_pct") or ((s.get("variant_adj") or {}).get("band") if isinstance(s.get("variant_adj"), dict)
                                  else None)
    if not band:
        m = _BAND.search(f"{s.get('detail') or ''} {s.get('target') or ''}")
        band = (float(m.group(1)) / 100.0, float(m.group(2)) / 100.0) if m else None
    if band:
        from backend.engine.zones import Z4_LO
        return "z5" if (float(band[0]) + float(band[1])) / 2.0 >= Z4_LO else "z3"
    title = str(s.get("title") or "")
    return "z5" if any(w in title for w in _Z5_WORDS) else "z3"


def type_key(s: dict) -> Optional[str]:
    """The 進階設定 type of a session (TYPE_KEYS, without mp: a segment, mp_policy) or None (not choosable)."""
    t = session_type(s)
    if t in ("easy", "long", "hill"):
        return t
    if t == "interval":
        return flat_zone(s)
    return None


def _aet_basis(s: dict) -> str:
    from backend.engine.aet_test import protocol_of_title
    p = protocol_of_title(s.get("title"))
    return "hr" if p in ("xu90", "friel") else "power"


# a walking session (SP-115): the 陡坡健走 the planner makes (engine/steep_hill.py), SP-114's
# 攻頂日模擬 (the multi-day 百岳's long day, specific_phase.summit_session) and 「ME 負重爬坡」 (its
# quality session, specific_phase.me_session, id "me" — kind quality, the legs not the breathing the
# limit), or a session the user titled 登山 / 健行 / 百岳 — not the 越野跑 kind "hike" (a trail run;
# the 技術地形 session stays one: owner 2026-10-05)
WALK_WORDS = ("陡坡健走", "登山", "健行", "百岳", "攻頂日模擬")
WALK_KINDS = ("easy", "long", "hike", "mountain")
WALK_IDS = ("steep", "me")                    # the planner's own walking sessions, any kind
ME_WORDS = ("負重爬坡",)                       # SP-114's ME title once stored (id gone): kind quality


def is_walk(s: dict) -> bool:
    """A walking session: its uphill HR cap is 75 % HRmax (hr_profile.walk_cap)."""
    sid = str(s.get("id") or s.get("gen_key") or "")
    if sid in WALK_IDS:
        return True
    title = str(s.get("title") or "")
    if s.get("kind") == "quality":
        return any(w in title for w in ME_WORDS)
    if s.get("kind") not in WALK_KINDS:
        return False
    return any(w in title for w in WALK_WORDS)


def session_type(s: dict) -> str:
    """easy / long / trail_long / hike / walk / interval / hill / climb / downhill / cp_test / aet_test / other."""
    kind, title = s.get("kind"), str(s.get("title") or "")
    if kind == "test":
        from backend.engine.aet_test import is_aet_session
        return "aet_test" if is_aet_session(s) else "cp_test"
    if "下坡" in title and kind in ("easy", "long", "quality"):
        return "downhill"
    if "長爬坡" in title:
        return "climb"
    if is_walk(s):
        return "walk"
    if kind == "mountain":
        return "trail_long"
    if kind == "quality":
        return "hill" if ("爬坡" in title or "上坡" in title or s.get("terrain") == "trail") else "interval"
    if kind == "hike":
        return "hike"
    if kind == "long":
        return "trail_long" if s.get("terrain") in ("trail", "hike") or "山路" in title else "long"
    if kind in ("easy", "heat_passive"):
        return "trail_easy" if s.get("terrain") in ("trail", "hike") or "山路" in title or "越野" in title else "easy"
    return "other"


# road easy / long: power first with an HR cap (the athlete's call, 2026-10-02: power has no lag and
# needs only CP; the HR cap still guards heat and fatigue). Trail stays HR (Stryd only validated 3–8 %).
AUTO = {"easy": ("power", "power_easy"), "long": ("power", "power_easy"), "trail_easy": ("hr", "hr_base"),
        "trail_long": ("hr", "hr_base"),
        "hike": ("hr", "hr_base"), "walk": ("hr", "walk"), "interval": ("power", "power_iv"), "hill": ("power", "power_iv"),
        "climb": ("none", "climb"), "downhill": ("none", "down"), "cp_test": ("power", "cp"),
        "aet_test": (None, "aet"), "other": ("hr", "hr_base")}


def _auto_power_ok() -> bool:
    """A power meter per the 一般設定 power source (SP-950: Stryd; a stored 手錶推估功率 reads as 沒有功率計,
    athlete_profile.profile_power_source); False for 沒有功率計. Unset = the data decides (no CP falls
    back to HR below). Used when the caller has no power state
    (power_state.of(th) — week_plan's thresholds carry one)."""
    try:
        from backend.engine import athlete_profile as AP
        from backend.engine.planning import Plan
        return AP.profile_power_source(Plan.load().profile) != "none"
    except Exception:                       # noqa: BLE001
        return True


def _state(th: Optional[dict], cp_stale: Optional[bool], power_state: Optional[dict] = None) -> tuple[str, Optional[dict]]:
    """(power | stale | none, the power state dict or None): `power_state`, else th["power_state"]
    (engine/power_state.py, week_plan), else the 一般設定 power source and `cp_stale` / th["cp_stale"] (SP-493)."""
    from backend.engine import power_state as PST
    ps = PST.of({"power_state": power_state}) or PST.of(th)
    if ps is not None:
        return ps["state"], ps
    if not _auto_power_ok():
        return PST.NONE, None
    stale = cp_stale if cp_stale is not None else bool((th or {}).get("cp_stale"))
    return (PST.STALE if stale else PST.POWER), None


PACE_TYPES = ("interval",)               # SP-951: the flat Zone 3 / Zone 5 intervals and tempo


def _tpace_of(th: Optional[dict], tpace=None):
    """The threshold pace (s/km) the caller knows: `tpace`, else th["tpace"]; None = none known.
    `False` when `th` is a real thresholds dict (it has an lthr / aet / cp / power_state key) without
    one — the pace is known to be missing (week_plan leaves "tpace" out when there is none)."""
    if tpace:
        return float(tpace)
    if tpace is False:                      # the caller knows there is none
        return False
    th = th or {}
    if th.get("tpace"):
        return float(th["tpace"])
    if any(k in th for k in ("lthr", "aet", "cp", "power_state", "tpace")):
        return False
    return None


def _no_power_basis(t: str, th: dict) -> str:
    """The basis of a would-be power session when power is not in effect (② / ③): heart rate (road
    easy / long: HR ≤ the easy-run cap; hill repeats: the HR targets + RPE), and SP-951: the flat
    Zone 3 / Zone 5 intervals and tempo by "pace" (% threshold pace) with the HR cap in the text
    (target_policy turns it into heart rate, with the reason, when the threshold pace is missing)."""
    return "pace" if t in PACE_TYPES or t == "aet_test" else "hr"


NO_TPACE_HR = N_("沒有閾值配速：改用心率＋RPE")
NO_METER_PACE = N_("沒有功率計：平路間歇用配速，心率當上限")
NO_POWER_AET = N_("功率不生效：AeT 測試改用固定配速")         # SP-949


def target_policy(s: dict, prefs=None, th: Optional[dict] = None, cp_stale: Optional[bool] = None,
                  power_state: Optional[dict] = None, tpace=None) -> dict:
    """{"basis": hr | power | pace | none, "chosen": auto | hr | power | pace (where it came from),
        "type", "why", "source", "hr_cap": bool (an HR cap note on power / pace sessions),
        "fallback": "" | reason, "power_state": power | stale | none}.

    SP-950 (owner 2026-10-27, engine/power_state.py): power is in effect only with a power meter and a
    fresh CP. ② (a meter, the CP stale — th["power_state"], else `cp_stale` / th["cp_stale"]) and
    ③ (no meter): no session is run by power, whatever was chosen — the CP test keeps its open bouts
    in ② (it is what brings power back); a UA / Evoke AeT test holds a fixed pace (SP-949); in ③
    a stored 目標依據 = 功率 (課表偏好 or the session's own) is ignored, not deleted.
    SP-951: the flat intervals there (and wherever 配速 is picked) by % threshold pace (`tpace`, else
    th["tpace"]); a threshold pace known to be missing → heart rate with the reason."""
    from backend.engine import power_state as PST
    t = session_type(s)
    own = s.get("target_basis")
    # SP-964: the session's own → its type's (進階設定) → the overall 目標依據 → 自動
    tk = type_key(s) if t in ("easy", "long", "hill", "interval") else None
    tb = type_basis(prefs, tk)
    if own in CHOSEN:
        chosen, src = own, "own"
    elif tb:
        chosen, src = tb, "type"
    else:
        chosen, src = pref_basis(prefs), "pref"
    base, key = AUTO[t]
    if base is None:
        base = _aet_basis(s)
    basis = base
    # the why: one msgid each (with the fallback in brackets when there is one), filled in at the end
    how = "auto"
    # 配速 picked: only the flat intervals take it; the other types follow 自動 (pace means nothing on a
    # hill, the easy / long runs keep their HR cap / power band)
    picked = chosen in ("hr", "power") or (chosen == "pace" and t in PACE_TYPES)
    if picked and t not in ("cp_test", "aet_test", "downhill", "climb"):
        basis = chosen
        how = src
    fb = ""
    th = th or {}
    tp = _tpace_of(th, tpace)
    state, ps = _state(th, cp_stale, power_state)
    keep = state == PST.STALE and t == "cp_test"      # ②: the CP test keeps its power (SP-949: not the AeT test)
    if basis == "power" and state != PST.POWER and not keep:
        basis = _no_power_basis(t, th)
        if t == "aet_test":
            # SP-949: UA / Evoke hold a fixed pace (the athlete's own HR → pace, else the E pace / the
            # talk test — aet_test.start_target), not the threshold pace
            fb = _(NO_POWER_AET)
        elif basis == "pace" and state == PST.NONE and tp is not False:
            fb = _(NO_METER_PACE)
        else:
            fb = PST.short_reason(ps) if ps is not None else \
                _("沒有功率計：用心率") if state == PST.NONE else _(CP_STALE_ALL)
    if basis == "power" and th and not th.get("cp") and not (keep and t == "cp_test"):
        basis = "pace" if (t in PACE_TYPES and tp) or t == "aet_test" else "hr"
        fb = _("沒有 CP：改用配速") if basis == "pace" else _("沒有 CP：改用心率")
    if basis == "pace" and tp is False and t != "aet_test":
        basis = "hr"
        fb = f"{fb}；{_(NO_TPACE_HR)}" if fb else _(NO_TPACE_HR)
    if basis == "hr" and th and not (th.get("aet") or th.get("lthr")):
        basis, fb = "none", _("沒有 AeT／LTHR：不設目標")
    if how == "auto":
        kw = {"type": _(TYPE_WORD[t]), "basis": _(LABEL[base])}
        why = _("自動：{type}看{basis}（{fb}）", fb=fb, **kw) if fb else _("自動：{type}看{basis}", **kw)
    elif how == "own":
        why = _("這次課表你選了{basis}（{fb}）", basis=_(LABEL[chosen]), fb=fb) if fb \
            else _("這次課表你選了{basis}", basis=_(LABEL[chosen]))
    elif how == "type":
        kw = {"type": _(TYPE_KEY_WORD[tk]), "basis": _(LABEL[chosen])}
        why = _("進階設定：{type}用{basis}（{fb}）", fb=fb, **kw) if fb else _("進階設定：{type}用{basis}", **kw)
    else:
        why = _("課表偏好：{basis}（{fb}）", basis=_(LABEL[chosen]), fb=fb) if fb \
            else _("課表偏好：{basis}", basis=_(LABEL[chosen]))
    if basis == "pace" and t != "aet_test":
        key = "pace_iv"
    return {"basis": basis, "chosen": chosen, "how": how, "type_key": tk, "type": t, "why": why,
            "source": _(SRC[key]),
            "hr_cap": (basis == "power" and t in ("interval", "hill", "easy", "long"))
            or (basis == "pace" and t != "aet_test"),
            "fallback": fb, "power_state": state}


def flat_pace(th: Optional[dict], prefs=None, tpace: Optional[float] = None, s: Optional[dict] = None) -> bool:
    """SP-951: the flat intervals of this plan run by pace (target_policy's basis of a flat interval
    with these thresholds and 課表偏好). The text builders (interval_library.session_for,
    quality_gate.session, overview.taper_q_target) ask it, so the session's 目標 says the same as
    its structure and the push. Needs the plan's power state (th["power_state"]) or 配速 picked;
    otherwise False (a caller without them keeps its old texts). SP-964: `s` = the session (or a hint
    {"_zone": "z3" | "z5"}) — its type's own 目標依據 (進階設定) counts; None = a Zone 3 one."""
    from backend.engine import power_state as PST
    th = th or {}
    probe = {**(s or {}), "kind": "quality", "title": (s or {}).get("title") or "", "id": "quality"}
    own = probe.get("target_basis")
    picked = own if own in CHOSEN else (type_basis(prefs, flat_zone(probe)) or pref_basis(prefs))
    if PST.of(th) is None and picked != "pace":
        return False
    return target_policy(probe, prefs, th, tpace=tpace)["basis"] == "pace"


# ---- SP-964: the road long run's marathon-pace segment (workout_steps.mp_target, the push, the card) ----
def mp_policy(prefs=None, th: Optional[dict] = None, cp_stale: Optional[bool] = None,
              power_state: Optional[dict] = None, tpace=None, goal: Optional[float] = None) -> dict:
    """{"basis": pace | hr | power | none, "chosen", "how": auto | type, "why", "fallback"} of the MP segment:
    進階設定's mp, else pace (the overall 目標依據 never reached it — unchanged). A chosen power when power is not
    in effect (② / ③) or without a CP → pace with the reason; pace without a goal pace and a threshold pace
    known missing → heart rate (+ RPE, the MP HR band × LTHR: workout_steps.MP_HR); HR without an LTHR → none."""
    from backend.engine import power_state as PST
    chosen = type_basis(prefs, "mp")
    basis = chosen or "pace"
    fb = ""
    th = th or {}
    state, ps = _state(th, cp_stale, power_state)
    if basis == "power" and state != PST.POWER:
        basis = "pace"
        fb = _("沒有功率計：用配速") if state == PST.NONE else \
            PST.short_reason(ps) if ps is not None else _(CP_STALE_ALL)
    if basis == "power" and th and not th.get("cp"):
        basis, fb = "pace", _("沒有 CP：改用配速")
    tp = _tpace_of(th, tpace)
    if basis == "pace" and not goal and tp is False:
        basis = "hr"
        fb = f"{fb}；{_(NO_TPACE_HR)}" if fb else _(NO_TPACE_HR)
    if basis == "hr" and th and not th.get("lthr"):
        basis, fb = "none", _("沒有 LTHR：不設目標")
    if chosen:
        kw = {"type": _(TYPE_KEY_WORD["mp"]), "basis": _(LABEL[chosen])}
        why = _("進階設定：{type}用{basis}（{fb}）", fb=fb, **kw) if fb else _("進階設定：{type}用{basis}", **kw)
    else:
        kw = {"type": _(TYPE_KEY_WORD["mp"]), "basis": _(LABEL["pace"])}
        why = _("自動：{type}看{basis}（{fb}）", fb=fb, **kw) if fb else _("自動：{type}看{basis}", **kw)
    return {"basis": basis, "chosen": chosen or "auto", "how": "type" if chosen else "auto", "why": why,
            "fallback": fb, "power_state": state}


# the threshold-dict keys the session text builders read (overview._gate_session, session_suggest …):
# SP-951 adds the threshold pace and the power state, so they can write a pace target (flat_pace)
TEXT_KEYS = ("cp", "lthr", "aet", "hr_model", "power_model", "tpace", "power_state", "cp_stale")


def text_th(th: Optional[dict]) -> dict:
    """The keys of `th` a session text builder reads (TEXT_KEYS; missing ones left out)."""
    th = th or {}
    return {k: th.get(k) for k in TEXT_KEYS if k in th}


def pace_band(lo: float, hi: float, tpace: float) -> tuple[float, float]:
    """(fast, slow) s/km of a % CP band (lo, hi) at threshold pace `tpace`: threshold pace ÷ (% CP)
    (running power ≈ proportional to flat speed — Stryd; 推估). The 88–95 % CP Zone 3 band → 105–114 %
    of threshold pace (≈ Friel pace Z3 106–114 %)."""
    return tpace / hi, tpace / lo


def pace_text(lo: float, hi: float, tpace: float) -> str:
    """「配速 6:00–6:27 /km（105–114% 閾值配速）」 of a % CP band — the 課表 card's words (the session
    builders and session_structure.card_summary both write it, so the card says one thing)."""
    from backend.engine.workout_steps import mmss
    a, b = pace_band(lo, hi, tpace)
    return _("配速 {a}–{b} /km（{pa}–{pb}% 閾值配速）", a=mmss(a), b=mmss(b), pa=f"{a / tpace * 100:.0f}",
             pb=f"{b / tpace * 100:.0f}")


def pace_pct_text(lo: float, hi: float, tpace: float) -> str:
    """「105–114% 閾值配速」 of a % CP band (the step's sub, a 說明's band)."""
    a, b = pace_band(lo, hi, tpace)
    return _("{pa}–{pb}% 閾值配速", pa=f"{a / tpace * 100:.0f}", pb=f"{b / tpace * 100:.0f}")


def no_power(th: Optional[dict]) -> bool:
    """The plan's power state (th["power_state"]) says power is not in effect (② / ③); False without one."""
    from backend.engine import power_state as PST
    ps = PST.of(th)
    return ps is not None and ps["state"] != PST.POWER


_BAND_PART = re.compile(r"^\s*\d+\s*[–-]\s*\d+\s*%\s*CP\s*$")
# a 出處 part that names a % CP band, and its words without it (SP-951: ② / ③ show no % CP)
SOURCE_NO_POWER = {
    "Palladino 恢復週保留 98–101% CP fartlek": N_("Palladino 恢復週保留 fartlek"),
    "Stryd Cruise Intervals 94–100% CP": "Stryd Cruise Intervals",
}


def source_without_power(src: str, th: Optional[dict]) -> str:
    """SP-951 (owner: no % CP / watts anywhere without power in effect): a session's 出處 without its % CP
    parts when the plan's power state is ② / ③ (no_power) — a bare band part dropped, a source that
    names its band said without it (SOURCE_NO_POWER); as it is with power."""
    if not src or not no_power(th):
        return src
    out = [(_(SOURCE_NO_POWER[p]) if p in SOURCE_NO_POWER else p)
           for p in src.split("；") if not _BAND_PART.match(p)]
    return "；".join(out)


def cap_text(hi) -> str:
    """「心率 ≤ 172 bpm」: the HR cap note of a pace session (SP-951)."""
    return _("心率 ≤ {hr} bpm", hr=f"{float(hi):.0f}")


def target_text(target: str, basis: str) -> str:
    """The target text of the chosen basis: keep the 功率 or the 心率 parts (the other as
    a cap note on power sessions is the caller's choice); 配速 keeps the pace and the HR parts
    (the HR as the cap note)."""
    parts = [p.strip() for p in (target or "").split("·") if p.strip()]
    if basis == "hr":
        keep = [p for p in parts if p.startswith("心率")]
    elif basis == "power":
        keep = [p for p in parts if p.startswith(("功率", "RPE"))]
    elif basis == "pace":
        keep = [p for p in parts if p.startswith(("配速", "心率"))]
    else:
        keep = []
    return " · ".join(keep) if keep else ("" if basis == "none" else target or "")
