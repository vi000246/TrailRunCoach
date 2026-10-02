"""
Energy and fuelling for the 賽事功率 page — docs/research/fueling-and-energy.md.

Pure functions on the /plan payload (planner.plan_run / plan_hike segments);
nothing here reads files, settings or the network. The page shows
recommended values only (the athlete prepares the food himself): no product
list, no gut-tolerance input, so carbohydrate never goes above 90 g/h.

Energy (§2.4, §2.5):

    run, -2 % ≤ g ≤ 8 %, not walked:   kcal = P·t ÷ (η · 4184)
        η = 0.221 (flat), 0.216 when g > 4 %   — van Rassel 2026 (Stryd ÷ metabolic)
    steeper, walked, downhill:          kcal = 1.07 · (W + L) · km · C(g)/Cr(0)
        Fletcher 2009 1.07 kcal/kg/km; Minetti 2002 run / walk curve ratio
    no power, HR known:                 Keytel 2005 (no VO2max form, via Hsieh 2025)
    百岳 (pack):                         Pandolf 1977 per segment, M·t ÷ 4184;
                                        downhill: the movement term × Cw(g)/Cw(0) (推估)
        cross-check: Yamamoto CC × (W + L) (the app's existing 百岳 kcal)

The whole race carries a ±10–15 % band (§2.4 error budget, combination 推估).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Optional, Sequence

from backend.engine.algorithms import minetti
from backend.engine.racepower import hike as HK

J_PER_KCAL = 4184.0
ETA_FLAT = 0.221                 # van Rassel 2026: Stryd ÷ metabolic power on the flat
ETA_STEEP = 0.216                # 6 % grade (8 %: 0.213)
ETA_STEEP_FROM = 0.04            # §2.4: above 4 % use the 6–8 % value
POWER_MAX_GRADE = 0.08           # Stryd validated 0–8 % (van Rassel 2026)
POWER_MIN_GRADE = -0.02          # within "flat" (course flat_pct); real descents → Minetti (推估 cut)
C_FLAT = 1.07                    # Fletcher 2009, kcal/kg/km at 85 % LT speed (1.05–1.11)
C_FLAT_RANGE = (1.05, 1.11)
STRYD_ETA = 0.24                 # Stryd's own kcal = kJ (industry)
BAND_POWER = 0.10                # §2.4 error band: ±10 % on power segments …
BAND_MODEL = 0.15                # … ±15 % on Minetti / Keytel / Pandolf ones (combination 推估)
CROSS_TOL = 0.15                 # flag when the Fletcher cross-check is > 15 % off (§2.4)
L_TRAIL = 2.0                    # vest on a trail race (capacity.L_TRAIL)

# carbohydrate by event (§3.2); g/h, never above 90 (no gut-training input)
CHO_MAX = 90.0
CHO_RULES = {
    "short": {"lo": 0.0, "hi": 30.0, "dose_g": None, "every": None, "label": "< 75 分鐘：漱口或少量即可",
              "src": "Jeukendrup 2014；Burke 2011（約 1 h 的運動漱口或少量碳水即可）"},
    "half": {"lo": 30.0, "hi": 60.0, "dose_g": 30.0, "every": (30, 60), "label": "半馬級（< 2–2.5 h）",
             "src": "Jeukendrup 2014；Burke 2011；Vitale & Getzin 2019（< 2.5 h：30–60 g/h）"},
    "long": {"lo": 60.0, "hi": 90.0, "dose_g": 30.0, "every": (20, 30), "label": "全馬／越野 2–6 h：葡萄糖＋果糖",
             "src": "Jeukendrup 2014；Burke 2011（> 2.5 h 最多 90 g/h，混合醣）；Vitale & Getzin 2019；"
                    "筆記「> 3 h 至少 60 g/h」"},
    "ultra": {"lo": 30.0, "hi": 90.0, "dose_g": 25.0, "every": (30, 45), "label": "超過 6 h：30–50 起步，能吃就往 60–90",
              "src": "Tiller 2019 ISSN 超馬立場（30–50 g/h、150–400 kcal/h）；Jeukendrup 2014（超耐力約 90 g/h）"},
    "hike": {"lo": 30.0, "hi": 50.0, "dose_g": 40.0, "every": (45, 60), "label": "百岳行進間（推估）",
             "src": "推估：ISSN 超馬下限（Tiller 2019）＋低強度往下調（Jeukendrup 2014）；每 45–60 分鐘一次（§5.3 推估）"},
}
# water (§4.2): ml/h band moved inside the source range by temperature (推估)
WATER = {"long": (400.0, 800.0, "Vitale & Getzin 2019：400–800 ml/h；上限是流汗率，不能喝到體重增加（NATA 2017、Hew-Butler 2015）"),
         "ultra": (450.0, 750.0, "Tiller 2019 ISSN：450–750 ml/h（約每 20 分鐘 150–250 ml）；不能喝到體重增加（Hew-Butler 2015）"),
         "half": (0.0, 400.0, "Kenefick 2018：< 90 min、涼天口渴再喝；熱天改計畫性喝水")}
WATER_WIDTH = 200.0              # the band at one temperature: 400–600 cool … 600–800 hot (推估)
T_COOL, T_HOT = 10.0, 30.0       # °C ends of the temperature shift (推估)
HOT_HALF_C = 25.0                # a half marathon this hot: planned drinking (Kenefick 2018)
SODIUM = {"long": (300.0, 600.0, "Vitale & Getzin 2019：300–600 mg/h（> 2 h、流汗多）；筆記 400 mg/h"),
          "ultra": (300.0, 600.0, "Vitale 2019 300–600 mg/h；ISSN 超馬：飲料 ≥ 575 mg/L（Tiller 2019）"),
          "hike": (200.0, 300.0, "推估：筆記健行 200 mg/h ＋ Vitale 下限 300 mg/h"),
          "half": (0.0, 0.0, "< 90 min：通常不必另外補鈉（§4.2）")}
ULTRA_NA_PER_L = 575.0           # ISSN: drink sodium > 575 mg/L
CARRY_SPARE = 1.2                # 筆記：補給多帶 20 %（補水補碳:178）
HIKE_WATER_ML_PER_KCAL = (0.7, 0.8)   # Yamamoto: 補 70–80 % of ml ≈ kcal (app's existing rule)
# 百岳 daily (§5.1, §5.2)
CAMP_FACTOR = 1.3                # 營地活動係數 1.2–1.4（推估）
FOOD_DENSITY = 4.4               # kcal/g（筆記 125 kcal/oz）
FOOD_COVERAGE = 0.85             # 補回比例（推估）
PACK_DAILY_DROP = 0.7            # capacity.PACK_DAILY_DROP (app default)
BODY_DEFAULTS = {"height_cm": 175.0, "age": 40.0, "sex": "male"}   # §5.1 example's assumptions (推估)
STEEP_DOWN_NO_FUEL = -0.15       # §6.2: no fuel point on a technical descent
SHIFT_S = 300.0                  # §6.2: move a point onto a walk / climb within ±5 min
STATION_SHIFT_S = 600.0          # eat at a food station within ±10 min of a point (推估)
# aid-station types (the page's editor): what each one supplies; minutes =
# the default stop when the athlete leaves it blank (推估)
STOP_TYPES = {
    "water": {"label": "水站", "water": True, "food": False, "sodium": False, "minutes": 0.5},
    "aid": {"label": "補給站（食物＋水）", "water": True, "food": True, "sodium": False, "minutes": 2.0},
    "big": {"label": "大補給站（含電解質、熱食）", "water": True, "food": True, "sodium": True, "minutes": 5.0},
    "medical": {"label": "醫護站", "water": False, "food": False, "sodium": False, "minutes": 0.0},
    "self": {"label": "自備補給點", "water": True, "food": True, "sodium": True, "minutes": 2.0},
}
STOP_DEFAULT = "aid"             # the old 「km:分」 text had no type
# GPX waypoint names that look like aid stations (「從路線匯入」)
STOP_NAME_HINTS = (("醫護", "medical"), ("medic", "medical"), ("大補", "big"), ("水站", "water"), ("water", "water"),
                   ("自備", "self"), ("drop", "self"), ("補給", "aid"), ("aid", "aid"),
                   ("檢查", "aid"), ("station", "aid"))


def stop_type(st: dict) -> str:
    t = st.get("type")
    return t if t in STOP_TYPES else STOP_DEFAULT


def stops_from_wpts(wpts: Sequence[dict], km_total: Optional[float] = None) -> list[dict]:
    """Course waypoints whose names look like aid stations → editor rows
    {km, type, name}; a name match only (no distance rules)."""
    out = []
    for w in wpts or []:
        name = str(w.get("name") or "").strip()
        low = name.lower()
        typ = next((t for key, t in STOP_NAME_HINTS if key in low), None)
        if typ is None and re.search(r"\bcp\s*\d", low):
            typ = "aid"                     # CP1, CP 2 … (race checkpoints)
        if typ is None or w.get("km") is None:
            continue
        k = float(w["km"])
        if k <= 0.05 or (km_total is not None and k >= km_total - 0.05):
            continue                        # start / finish arches are not stations
        out.append({"km": round(k, 2), "type": typ, "name": name[:40]})
    return sorted(out, key=lambda r: r["km"])


# ---------------------------------------------------------------------------
# energy
# ---------------------------------------------------------------------------

def power_kcal(power_w: float, t_s: float, grade: float = 0.0) -> float:
    """kcal = P·t ÷ (η · 4184), η by grade (van Rassel 2026)."""
    eta = ETA_STEEP if grade > ETA_STEEP_FROM else ETA_FLAT
    return power_w * t_s / (eta * J_PER_KCAL)


def minetti_kcal(dist_m: float, mass_kg: float, grade: float, walking: bool = False,
                 c_flat: float = C_FLAT) -> float:
    """Fletcher's flat cost × Minetti's ratio C(g)/Cr(0): only the ratio is
    taken from Minetti (net vs gross is unverified, §2.2); the raw curve,
    no downhill floor (§2.4)."""
    ratio = minetti.cost_of_transport(grade, walking) / minetti.FLAT_RUN
    return c_flat * mass_kg * dist_m / 1000.0 * ratio


def keytel_kcal(hr: float, minutes: float, weight: float, age: float, sex: str) -> float:
    """Keytel 2005 without VO2max (coefficients via Hsieh 2025, 二手)."""
    if sex == "female":
        kj_min = 0.4472 * hr - 0.1263 * weight + 0.074 * age - 20.4022
    else:
        kj_min = 0.6309 * hr + 0.1988 * weight + 0.2017 * age - 55.0969
    return max(0.0, minutes * kj_min / 4.184)


def energy_band(kcal: float, rel: float = 0.12) -> list[float]:
    return [kcal * (1.0 - rel), kcal * (1.0 + rel)]


def run_energy(segments: Sequence[dict], weight: float, pack_kg: float = 0.0,
               hr_bpm: Optional[float] = None, age: Optional[float] = None, sex: Optional[str] = None) -> list[dict]:
    """Per run segment {kcal, method}: Stryd power where it is validated,
    Minetti × Fletcher elsewhere; Keytel only when a segment has no power."""
    out = []
    for s in segments:
        g = float(s.get("grade") or 0.0)
        p, t, d = s.get("power"), float(s.get("t") or 0.0), float(s.get("dist_m") or 0.0)
        walked = bool(s.get("walk"))
        if p and t > 0 and not walked and POWER_MIN_GRADE <= g <= POWER_MAX_GRADE:
            out.append({"kcal": power_kcal(float(p), t, g), "method": "power"})
        elif p or not hr_bpm or age is None:
            out.append({"kcal": minetti_kcal(d, weight + pack_kg, g, walking=walked),
                        "method": "minetti_walk" if walked else "minetti"})
        else:
            out.append({"kcal": keytel_kcal(hr_bpm, t / 60.0, weight, age, sex or "male"), "method": "keytel"})
    return out


def fletcher_check(segments: Sequence[dict], weight: float) -> dict:
    """§2.4 cross-check: 1.05–1.11 kcal/kg/km × body weight × Minetti
    grade-adjusted km (running curve)."""
    adj_km = sum(float(s.get("dist_m") or 0.0) / 1000.0 * minetti.cost_of_transport(float(s.get("grade") or 0.0))
                 / minetti.FLAT_RUN for s in segments)
    return {"kcal": C_FLAT * weight * adj_km, "range": [C_FLAT_RANGE[0] * weight * adj_km,
                                                        C_FLAT_RANGE[1] * weight * adj_km], "adj_km": adj_km}


def pandolf_kcal(weight: float, load: float, v: float, grade: float, t_s: float, eta: float = 1.0) -> float:
    """Pandolf 1977 M (W) × t. Uphill / flat as published (hike.pandolf);
    downhill (Pandolf refuses it): standing + load terms unchanged, the
    movement term × Minetti walking Cw(g)/Cw(0) (推估, Santee's correction
    is single-source)."""
    if grade >= 0:
        m = HK.pandolf(weight, load, v, 100.0 * grade, eta)
    else:
        stand = 1.5 * weight + 2.0 * (weight + load) * (load / weight) ** 2
        move = eta * (weight + load) * 1.5 * v * v
        m = stand + move * minetti.cost_of_transport(grade, True) / minetti.FLAT_WALK
    return m * t_s / J_PER_KCAL


def yamamoto_kcal(hours: float, km: float, gain_m: float, loss_m: float, mass_kg: float) -> float:
    """Yamamoto CC × (W + L) (predict.yamamoto_cc; 中原・萩原・山本 2006)."""
    return (1.8 * hours + 0.3 * km + 10.0 * gain_m / 1000.0 + 0.6 * loss_m / 1000.0) * mass_kg


def hike_energy(segments: Sequence[dict], weight: float, default_pack: float = 9.0) -> list[dict]:
    """Per 百岳 segment: Pandolf with that day's pack (kcal) and the Yamamoto
    cross-check (kcal_yamamoto)."""
    out = []
    for s in segments:
        t = float(s.get("t") or 0.0)
        d = float(s.get("dist_m") or 0.0)
        g = float(s.get("grade") or 0.0)
        L = float(s.get("pack_kg") if s.get("pack_kg") is not None else default_pack)
        eta = float(s.get("eta_factor") or 1.0)
        v = d / t if t > 0 else 0.0
        out.append({"kcal": pandolf_kcal(weight, L, v, g, t, eta) if t > 0 else 0.0, "method": "pandolf",
                    "kcal_yamamoto": yamamoto_kcal(t / 3600.0, d / 1000.0, float(s.get("gain_m") or 0.0),
                                                   float(s.get("loss_m") or 0.0), weight + L)})
    return out


def mifflin_ree(weight: float, height_cm: float, age: float, sex: str) -> float:
    """Mifflin-St Jeor 1990 (kcal/day)."""
    return 10.0 * weight + 6.25 * height_cm - 5.0 * age + (5.0 if sex != "female" else -161.0)


def baiyue_daily(days: Sequence[dict], weight: float, body: dict, density: float = FOOD_DENSITY,
                 coverage: float = FOOD_COVERAGE, camp: float = CAMP_FACTOR) -> list[dict]:
    """§5.1–5.2 per day: moving kcal (Pandolf and Yamamoto) + the rest of the
    day at REE/24 × camp factor; food kg = the higher total × coverage ÷
    density (the higher one so the pack never runs short, 推估)."""
    ree = mifflin_ree(weight, body["height_cm"], body["age"], body["sex"])
    out = []
    for d in days:
        mh = float(d.get("moving_h") or 0.0)
        rest = max(0.0, 24.0 - mh) * ree / 24.0 * camp
        mp, my = float(d.get("kcal_pandolf") or 0.0), float(d.get("kcal_yamamoto") or 0.0)
        hi = max(mp, my) + rest
        out.append({"day": d.get("day"), "moving_h": mh, "kcal_pandolf": mp, "kcal_yamamoto": my,
                    "rest_kcal": rest, "total": [min(mp, my) + rest, hi], "ree": ree,
                    "food_kg": hi * coverage / density / 1000.0})
    return out


# ---------------------------------------------------------------------------
# event class, carbohydrate, water, sodium
# ---------------------------------------------------------------------------

def event_class(kind: str, hours: float, km: float) -> str:
    """§3.2 rows: road half / full by distance; trail by predicted time."""
    if kind == "baiyue":
        return "hike"
    if hours < 1.25:
        return "short"
    if kind == "road":
        return "half" if km <= 25.0 else "long"
    if hours < 2.0:
        return "half"
    return "long" if hours <= 6.0 else "ultra"


def carb_target(cls: str) -> dict:
    r = CHO_RULES[cls]
    lo, hi = r["lo"], min(CHO_MAX, r["hi"])
    base = [30.0, 50.0] if cls == "ultra" else None
    return {"lo": lo, "hi": hi, "mid": (lo + hi) / 2.0 if base is None else sum(base) / 2.0, "base": base,
            "dose_g": r["dose_g"], "every_min": list(r["every"]) if r["every"] else None,
            "label": r["label"], "src": r["src"], "badge": "推估" if cls == "hike" else None,
            "mix": cls in ("long", "ultra")}


def heat_pos(temp_c: Optional[float]) -> float:
    """0 at ≤ 10 °C … 1 at ≥ 30 °C (推估)."""
    if temp_c is None:
        return 0.5
    return min(1.0, max(0.0, (float(temp_c) - T_COOL) / (T_HOT - T_COOL)))


def hot_flags(segments: Sequence[dict]) -> list[bool]:
    """§6.2 step 7: the hottest quarter of the run (heat_pct) or ≥ 25 °C."""
    hp = sorted((float(s.get("heat_pct") or 0.0) for s in segments), reverse=True)
    pos = [x for x in hp if x > 0]
    cut = pos[max(0, len(pos) // 4 - 1)] if len(pos) >= 4 else (pos[0] if pos else math.inf)
    return [(float(s.get("heat_pct") or 0.0) >= cut and (s.get("heat_pct") or 0) > 0)
            or (s.get("temp_c") is not None and float(s["temp_c"]) >= HOT_HALF_C) for s in segments]


def water_band(cls: str, temp_c: Optional[float], hot: bool) -> Optional[list[float]]:
    """ml/h at this temperature inside the source range (推估 shift)."""
    if cls in ("short", "hike"):
        return None
    if cls == "half":
        return None
    lo0, hi0, _ = WATER[cls]
    p = 1.0 if hot else heat_pos(temp_c)
    width = min(WATER_WIDTH, hi0 - lo0)
    lo = lo0 + (hi0 - lo0 - width) * p
    return [lo, lo + width]


def sodium_band(cls: str, temp_c: Optional[float], hot: bool, water_mid: Optional[float] = None) -> list[float]:
    if cls == "short":
        return [0.0, 0.0]
    lo0, hi0, _ = SODIUM.get(cls, SODIUM["long"])
    if cls in ("half", "hike"):
        return [lo0, hi0]
    p = 1.0 if hot else heat_pos(temp_c)
    lo, hi = lo0 + 200.0 * p, lo0 + 100.0 + 200.0 * p
    if cls == "ultra" and water_mid:
        lo = max(lo, ULTRA_NA_PER_L * water_mid / 1000.0 * 0.8)
        hi = max(hi, ULTRA_NA_PER_L * water_mid / 1000.0)
    return [lo, min(max(hi, lo), max(hi0, lo))]


def loading(weight: float, hours: float, cls: str) -> dict:
    """Pre-race (§6.1): > 90 min 10–12 g/kg the day before (Bussau 2002:
    one day is enough), else 6 g/kg; breakfast 1–4 g/kg 1–4 h before;
    caffeine 3–6 mg/kg (Vitale 2019); 百岳: eat normally (推估)."""
    if cls == "hike":
        return {"kind": "normal", "label": "前一晚正常吃，不必超補", "badge": "推估",
                "src": "推估（§3.2：低強度、多日，重點是每日總熱量）"}
    if hours * 60.0 > 90.0:
        g = [10.0 * weight, 12.0 * weight]
        lab, src = "前一天 10–12 g/kg", "Bussau 2002（10 g/kg 一天肌肝醣就到頂）；Vitale & Getzin 2019（> 90 min：10–12 g/kg/天）"
    else:
        g = [6.0 * weight, 6.0 * weight]
        lab, src = "前一天約 6 g/kg（正常高碳水）", "Vitale & Getzin 2019（< 90 min：6 g/kg/天）"
    return {"kind": "load", "g_day": g, "label": lab, "src": src, "badge": None,
            "breakfast_g": [1.0 * weight, 4.0 * weight], "breakfast_src": "Vitale & Getzin 2019：賽前 1–4 h 吃 1–4 g/kg",
            "caffeine_mg": [3.0 * weight, 6.0 * weight], "caffeine_src": "Vitale & Getzin 2019：3–6 mg/kg，賽前 30–90 min",
            "drink": "賽前 2–3 h 喝 500 ml，10 分鐘前再 300 ml", "drink_src": "筆記 補水補碳:13-14"}


# ---------------------------------------------------------------------------
# schedule (§6.2, generic: no named products)
# ---------------------------------------------------------------------------

def _clock(start: Optional[str], secs: float) -> Optional[str]:
    if not start:
        return None
    try:
        h, m = (int(x) for x in start.split(":")[:2])
    except ValueError:
        return None
    t = dt.datetime(2000, 1, 1, h, m) + dt.timedelta(seconds=round(secs))
    day = (t.date() - dt.date(2000, 1, 1)).days
    return t.strftime("%H:%M") + (f" (+{day})" if day else "")


def _stops_before(stops, km: float) -> float:
    return sum(float(s.get("minutes") or 0) * 60.0 for s in stops or [] if float(s.get("km") or 0) <= km + 1e-6)


def _km_at(segments: Sequence[dict], t_s: float, t0: float = 0.0) -> float:
    """km reached at moving time t_s (segments with cum_s)."""
    prev = t0
    for s in segments:
        if t_s <= s["cum_s"] + 1e-6:
            f = (t_s - prev) / s["t"] if s["t"] else 1.0
            return s["start_km"] + max(0.0, min(1.0, f)) * (s["end_km"] - s["start_km"])
        prev = s["cum_s"]
    return segments[-1]["end_km"] if segments else 0.0


def _t_at_km(segments: Sequence[dict], km: float) -> Optional[float]:
    prev = segments[0]["cum_s"] - segments[0]["t"] if segments else 0.0
    for s in segments:
        if km <= s["end_km"] + 1e-6:
            span = s["end_km"] - s["start_km"]
            f = (km - s["start_km"]) / span if span > 0 else 1.0
            return prev + max(0.0, min(1.0, f)) * s["t"]
        prev = s["cum_s"]
    return None


def _seg_at(segments: Sequence[dict], t_s: float) -> int:
    for i, s in enumerate(segments):
        if t_s <= s["cum_s"] + 1e-6:
            return i
    return len(segments) - 1


def fuel_points(segments: Sequence[dict], every_s: float, t0: float = 0.0, t_end: Optional[float] = None,
                station_times: Sequence[float] = ()) -> list[tuple[float, bool]]:
    """§6.2 steps 1–2: one point per interval, as (time, at_station). A
    point within ±10 min of a food station moves there (推估); otherwise onto
    a walked / steep climb starting within ±5 min; never on a descent
    steeper than −15 %."""
    if not segments or every_s <= 0:
        return []
    t_end = segments[-1]["cum_s"] if t_end is None else t_end
    pts: list[tuple[float, bool]] = []
    tau = t0 + every_s
    while tau < t_end - 600.0:              # nothing in the last 10 minutes
        best, at_st = tau, False
        near = [x for x in station_times if abs(x - tau) <= STATION_SHIFT_S]
        if near:
            best, at_st = min(near, key=lambda x: abs(x - tau)), True
        else:
            for s in segments:
                st = s["cum_s"] - s["t"]
                if abs(st - tau) <= SHIFT_S and (s.get("walk") or (s.get("grade") or 0) > POWER_MAX_GRADE):
                    best = st + 30.0
                    break
            i = _seg_at(segments, best)
            while (segments[i].get("grade") or 0) < STEEP_DOWN_NO_FUEL and i + 1 < len(segments):
                i += 1
                best = segments[i]["cum_s"] - segments[i]["t"] + 30.0
        if best >= t_end - 300.0:
            break
        if not pts or best - pts[-1][0] >= every_s * 0.5:
            pts.append((best, at_st))
        tau = best + every_s if at_st else tau + every_s
    return pts


def _rng(a, b, unit: str, nd: int = 0) -> str:
    fa, fb = round(a, nd), round(b, nd)
    fmt = (lambda x: f"{x:.{nd}f}") if nd else (lambda x: f"{int(x)}")
    return f"{fmt(fa)} {unit}" if fa == fb else f"{fmt(fa)}–{fmt(fb)} {unit}"


def _r50(x: float) -> int:
    return int(round(x / 50.0) * 50)


# ---------------------------------------------------------------------------
# the plan's fuel block
# ---------------------------------------------------------------------------

def plan_fuel(plan: dict, *, weight: float, stops: Optional[list] = None, start_time: Optional[str] = None,
              hr_bpm: Optional[float] = None, body: Optional[dict] = None) -> dict:
    """The 補給 card: kcal ± band, carbohydrate / water / sodium per hour and
    in total, the pre-race load and a generic schedule on the predicted
    splits. Adds kcal, cum_kcal, cho_g, water_ml, na_mg, fuel_action to every
    segment of `plan` (in place)."""
    kind = plan.get("type")
    segs = plan.get("segments") or []
    s = plan.get("summary") or {}
    T = float(s.get("time_s") or sum(x.get("t") or 0 for x in segs))
    hours = T / 3600.0
    km = float(s.get("km") or 0.0)
    cls = event_class(kind, hours, km)
    body = dict(body or {})
    body_src = {k: ("推估" if body.get(k) is None else body.get(k + "_src") or "設定") for k in BODY_DEFAULTS}
    for k, v in BODY_DEFAULTS.items():
        if body.get(k) is None:
            body[k] = v
    if kind == "baiyue":
        return _hike_fuel(plan, segs, weight, cls, body, body_src, stops, start_time)
    warnings: list[str] = []
    pack = L_TRAIL if kind == "trail" else 0.0
    en = run_energy(segs, weight, pack, hr_bpm=hr_bpm, age=body["age"], sex=body["sex"])
    total = sum(e["kcal"] for e in en)
    pw_share = (sum(e["kcal"] for e in en if e["method"] == "power") / total) if total else 0.0
    rel = BAND_POWER * pw_share + BAND_MODEL * (1.0 - pw_share)
    fl = fletcher_check(segs, weight)
    if total and abs(total - fl["kcal"]) / fl["kcal"] > CROSS_TOL:
        warnings.append(f"熱量交叉檢查：功率法 {total:.0f} kcal 和 Fletcher 距離法 {fl['kcal']:.0f} kcal 差超過 15 %")
    kj = sum(float(x.get("power") or 0) * float(x.get("t") or 0) for x in segs) / 1000.0
    keytel = None
    if hr_bpm and body_src["age"] != "推估":
        keytel = keytel_kcal(hr_bpm, T / 60.0, weight, body["age"], body["sex"])
    cho = carb_target(cls)
    hot = hot_flags(segs)
    temps = [x.get("temp_c") for x in segs if x.get("temp_c") is not None]
    t_mean = (sum(float(x.get("temp_c")) * x["t"] for x in segs if x.get("temp_c") is not None) /
              sum(x["t"] for x in segs if x.get("temp_c") is not None)) if temps else None
    thirst = cls in ("short", "half") and not (t_mean is not None and t_mean >= HOT_HALF_C)
    w_cls = "long" if cls in ("half", "short") else cls
    cum = 0.0
    tot_w, tot_na, tot_cho = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]
    for x, e, h in zip(segs, en, hot):
        h_t = float(x.get("t") or 0.0) / 3600.0
        cum += e["kcal"]
        sw = sweat_prior(x.get("temp_c"), e["kcal"] / h_t if h_t else None)
        wb = None if thirst else water_band(w_cls, x.get("temp_c"), h)
        if wb:
            # never more than the sweat rate: no weight gain (NATA 2017, Hew-Butler 2015)
            cap = sw * 1000.0
            wb = [min(wb[0], cap), min(wb[1], cap)]
        nb = sodium_band("half" if thirst else w_cls, x.get("temp_c"), h, sum(wb) / 2 if wb else None)
        cb = [cho["lo"], cho["hi"]] if cho["base"] is None else cho["base"]
        x["_wb"], x["_sweat"] = wb, sw
        x.update(kcal=e["kcal"], kcal_method=e["method"], cum_kcal=cum,
                 cho_g=sum(cb) / 2 * h_t, water_ml=(sum(wb) / 2 * h_t) if wb else None,
                 na_mg=sum(nb) / 2 * h_t, hot=h, fuel_action="")
        for i in (0, 1):
            tot_cho[i] += cb[i] * h_t
            tot_na[i] += nb[i] * h_t
            if wb:
                tot_w[i] += wb[i] * h_t
    water_h = [round(tot_w[0] / hours), round(tot_w[1] / hours)] if hours and not thirst else None
    na_h = [float(round(tot_na[0] / hours)), float(round(tot_na[1] / hours))] if hours else [0.0, 0.0]
    # aid stations on the course (sorted, inside it), with their arrival time
    sts = []
    for st in stops or []:
        k_ = float(st.get("km") or 0.0)
        if 0.0 < k_ < km and segs:
            sts.append({"km": k_, "type": stop_type(st), "name": (st.get("name") or "").strip(),
                        "t_s": _t_at_km(segs, k_) or 0.0})
    sts.sort(key=lambda r: r["km"])

    def st_label(r):
        return STOP_TYPES[r["type"]]["label"] + (f"「{r['name']}」" if r["name"] else "")

    def st_eta(t_s, k_):
        # arrival clock: the stops before this one, not its own
        return _clock(start_time, t_s + _stops_before([x for x in stops or [] if float(x.get("km") or 0) < k_ - 1e-6], k_))
    # schedule
    events = []
    if cho["every_min"] and segs:
        ev_s = sum(cho["every_min"]) / 2.0 * 60.0
        food_t = [r["t_s"] for r in sts if STOP_TYPES[r["type"]]["food"]]
        for tau, at_st in fuel_points(segs, ev_s, station_times=food_t):
            k_ = _km_at(segs, tau)
            i = _seg_at(segs, tau)
            late = tau > T / 2.0
            what = f"約 {cho['dose_g']:.0f} g 碳水（{'一包能量膠或等量' if late else '能量膠、香蕉或等量的固體'}）"
            if at_st:
                what = "在補給站吃 " + what
            drink = None
            if water_h:
                ev_h = ev_s / 3600.0
                drink = [_r50(water_h[0] * ev_h), _r50(water_h[1] * ev_h)]
            events.append({"t_s": tau, "km": k_, "seg": segs[i]["i"], "kind": "fuel", "at_station": at_st,
                           "eta": st_eta(tau, k_), "cho_g": cho["dose_g"],
                           "water_ml": drink, "action": what + (f" ＋ 喝 {_rng(drink[0], drink[1], 'ml')}" if drink else "")})
    elif cls == "short":
        events.append({"t_s": 0.0, "km": 0.0, "seg": segs[0]["i"] if segs else None, "kind": "fuel", "eta": start_time,
                       "cho_g": None, "water_ml": None, "action": "不用吃；想要的話途中用運動飲料漱口"})
    # water to carry from each station that has water to the next one (+20 %, 筆記)
    legs = []
    wet = [r for r in sts if STOP_TYPES[r["type"]]["water"]]
    if segs and not thirst:
        edges = [{"km": 0.0, "t_s": 0.0}] + wet + [{"km": km, "t_s": T}]
        for a, b in zip(edges, edges[1:]):
            lo = hi = 0.0
            for x in segs:
                st0, st1 = x["cum_s"] - x["t"], x["cum_s"]
                ov = max(0.0, min(st1, b["t_s"]) - max(st0, a["t_s"]))
                if ov > 0 and x.get("_wb") and x["t"]:
                    wb = x["_wb"]
                    lo += wb[0] * ov / 3600.0
                    hi += wb[1] * ov / 3600.0
            legs.append({"from_km": a["km"], "to_km": b["km"], "t_s": b["t_s"] - a["t_s"],
                         "carry_ml": [_r50(lo * CARRY_SPARE), _r50(hi * CARRY_SPARE)]})
        if legs:
            events.append({"t_s": 0.0, "km": 0.0, "seg": segs[0]["i"], "kind": "start", "eta": start_time,
                           "cho_g": None, "water_ml": legs[0]["carry_ml"],
                           "action": (f"出發：到第一個有水的站約帶 {_rng(*legs[0]['carry_ml'], 'ml')}" if len(legs) > 1
                                      else f"全程沒有水站：約帶 {_rng(*legs[0]['carry_ml'], 'ml')}")})
        if not sts and kind == "trail":
            warnings.append("沒有輸入補給站：越野賽請在「補給站」加上 km，才能算每段要帶多少水")
    leg_from = {lg["from_km"]: lg for lg in legs[1:]}
    for r in sts:
        sup = STOP_TYPES[r["type"]]
        lg = leg_from.get(r["km"]) if sup["water"] else None
        if thirst:
            act = f"{st_label(r)}：口渴就喝" if sup["water"] else f"{st_label(r)}（不補給）"
        elif not sup["water"]:
            act = f"{st_label(r)}：不補給，水要從上一站帶夠"
        else:
            parts = ["補水"] + (["吃"] if sup["food"] else []) + (["補電解質"] if sup["sodium"] else [])
            act = f"{st_label(r)}：{'、'.join(parts)}" + (f"，到下一個有水的站約帶 {_rng(*lg['carry_ml'], 'ml')}" if lg else "")
        events.append({"t_s": r["t_s"], "km": r["km"], "seg": segs[_seg_at(segs, r["t_s"])]["i"], "kind": "aid",
                       "stop_type": r["type"], "name": r["name"], "eta": st_eta(r["t_s"], r["km"]),
                       "cho_g": None, "water_ml": lg["carry_ml"] if lg else None, "action": act})
    # a fuel point moved onto a food station becomes part of that station's row
    for e in [x for x in events if x.get("at_station")]:
        st_ev = next((a for a in events if a["kind"] == "aid" and abs(a["t_s"] - e["t_s"]) < 1.0), None)
        if st_ev is not None:
            st_ev["action"] += "；" + e["action"].replace("在補給站吃 ", "吃")
            st_ev["cho_g"] = e["cho_g"]
            events.remove(e)
    events.sort(key=lambda e: (e["t_s"], {"start": 0, "aid": 1}.get(e["kind"], 2)))
    for e in events:
        for x in segs:
            if x["i"] == e["seg"]:
                x["fuel_action"] = "；".join(filter(None, [x.get("fuel_action"), e["action"]]))
    # dehydration check: sweat prior vs the planned water (推估 sweat rate, §4.2)
    sweat_l = sum(x.pop("_sweat") * float(x.get("t") or 0) / 3600.0 for x in segs)
    for x in segs:
        x.pop("_wb", None)
    drunk_l = (tot_w[0] + tot_w[1]) / 2000.0
    dehyd = (sweat_l - drunk_l) / weight if weight else None
    if dehyd is not None and dehyd > 0.02 and not thirst:
        warnings.append(f"預估脫水約 {dehyd:.1%} 體重（流汗推估 {sweat_l:.1f} L，喝 {drunk_l:.1f} L）：> 2 % 表現會掉，"
                        "熱天往水量上限靠，但不要喝到體重增加")
    intake = (tot_cho[0] + tot_cho[1]) / 2.0 * 4.0
    method_kcal = {}
    for e in en:
        method_kcal[e["method"]] = method_kcal.get(e["method"], 0.0) + e["kcal"]
    return {"category": cls, "hours": hours, "label": cho["label"],
            "kcal": total, "kcal_band": energy_band(total, rel), "band_rel": rel, "kcal_per_h": total / hours if hours else None,
            "methods": method_kcal, "power_share": pw_share,
            "crosscheck": {"fletcher": fl, "stryd_kj": kj, "stryd_kcal_as_kj": kj, "keytel": keytel,
                           "hr_bpm": hr_bpm},
            "cho": {**cho, "per_h": [cho["lo"], cho["hi"]], "total": tot_cho},
            "water": {"per_h": water_h, "total_ml": tot_w if not thirst else None, "thirst": thirst,
                      "src": (WATER["half"][2] if thirst else WATER[w_cls][2]),
                      "caution": "別喝到體重增加（低血鈉；NATA 2017、Hew-Butler 2015）", "badge": "推估",
                      "legs": legs, "sweat_l": sweat_l, "dehydration": dehyd, "t_mean": t_mean,
                      "hot_segments": sum(1 for h in hot if h)},
            "sodium": {"per_h": na_h, "total_mg": tot_na, "src": SODIUM["half" if thirst else w_cls][2]},
            "intake_kcal": intake, "deficit_kcal": total - intake,
            "loading": loading(weight, hours, cls), "schedule": events, "warnings": warnings,
            "stations": [{k: r[k] for k in ("km", "type", "name", "t_s")} for r in sts],
            "body": {k: body[k] for k in BODY_DEFAULTS}, "body_src": body_src,
            "src": {"power": "van Rassel 2026（Stryd 換算係數 22.1 %，6–8 % 坡 21.6 %）",
                    "minetti": "Fletcher 2009 1.07 kcal/kg/km × Minetti 2002 坡度比例",
                    "band": "Fletcher SD 約 8 %、Lacour & Bourdin 2015 個人差 20 %、疲勞 +10 %、van Rassel 陡坡 2–4 %（組合推估）",
                    "keytel": "Keytel 2005（不含 VO2max 的式子，係數引自 Hsieh 2025）"}}


SWEAT_REF_KCAL_H = 700.0        # a race-pace run; sweat scales with heat production (推估)
SWEAT_SCALE = (0.4, 1.3)


def sweat_prior(temp_c: Optional[float], kcal_per_h: Optional[float] = None) -> float:
    """L/h without a personal record: 1.0 cool (筆記:37) … 1.75 at 30 °C
    (Baker 2017 0.5–2.0 L/h; 筆記:36 夏天可到 3) at race-pace running,
    scaled by the segment's metabolic rate ÷ 700 kcal/h (0.4–1.3) — the
    heat to shed follows the energy burnt (推估)."""
    base = 1.0 + 0.75 * heat_pos(20.0 if temp_c is None else temp_c)
    if kcal_per_h:
        base *= min(SWEAT_SCALE[1], max(SWEAT_SCALE[0], kcal_per_h / SWEAT_REF_KCAL_H))
    return base


def _hike_fuel(plan: dict, segs: list, weight: float, cls: str, body: dict, body_src: dict,
               stops: Optional[list], start_time: Optional[str]) -> dict:
    s = plan.get("summary") or {}
    pack0 = float(s.get("pack_kg") or 9.0)
    en = hike_energy(segs, weight, pack0)
    cho = carb_target("hike")
    na = SODIUM["hike"]
    days_in = plan.get("days") or []
    warnings: list[str] = []
    cum = 0.0
    by_day: dict[int, dict] = {}
    for x, e in zip(segs, en):
        h_t = float(x.get("t") or 0.0) / 3600.0
        cum += e["kcal"]
        w = [HIKE_WATER_ML_PER_KCAL[0] * e["kcal_yamamoto"], HIKE_WATER_ML_PER_KCAL[1] * e["kcal_yamamoto"]]
        x.update(kcal=e["kcal"], kcal_method="pandolf", kcal_yamamoto=e["kcal_yamamoto"], cum_kcal=cum,
                 cho_g=(cho["lo"] + cho["hi"]) / 2 * h_t, water_ml=sum(w) / 2, na_mg=(na[0] + na[1]) / 2 * h_t,
                 fuel_action="")
        d = by_day.setdefault(int(x.get("day") or 1), {"kcal_pandolf": 0.0, "kcal_yamamoto": 0.0, "moving_h": 0.0,
                                                       "water": [0.0, 0.0]})
        d["kcal_pandolf"] += e["kcal"]
        d["kcal_yamamoto"] += e["kcal_yamamoto"]
        d["moving_h"] += h_t
        d["water"][0] += w[0]
        d["water"][1] += w[1]
    if not segs:
        # a manual course: no segments, the plan's own daily Yamamoto kcal; Pandolf cannot run
        for d in days_in:
            by_day[int(d["day"])] = {"kcal_pandolf": float(d.get("kcal") or 0.0), "kcal_yamamoto": float(d.get("kcal") or 0.0),
                                     "moving_h": float(d.get("moving_h") or 0.0), "water": list(d.get("water_ml") or [0, 0])}
        warnings.append("手動路線沒有坡度剖面：行動熱量只用 Yamamoto（Pandolf 需要逐段坡度與速度）")
    rows = [{"day": n, **v} for n, v in sorted(by_day.items())]
    daily = baiyue_daily(rows, weight, body)
    for r, d in zip(rows, daily):
        d["water_ml"] = r["water"]
        d["cho_g"] = [cho["lo"] * r["moving_h"], cho["hi"] * r["moving_h"]]
        d["na_mg"] = [na[0] * r["moving_h"], na[1] * r["moving_h"]]
    tot_p = sum(r["kcal_pandolf"] for r in rows)
    tot_y = sum(r["kcal_yamamoto"] for r in rows)
    if tot_y and abs(tot_p - tot_y) / tot_y > CROSS_TOL:
        warnings.append(f"行動熱量：Pandolf {tot_p:.0f} kcal、Yamamoto {tot_y:.0f} kcal 差超過 15 %"
                        "（Pandolf 對重背負常低估，Looney 2022）；糧食取較高者")
    food = [d["food_kg"] for d in daily]
    mean_food = sum(food) / len(food) if food else None
    if mean_food is not None and abs(mean_food - PACK_DAILY_DROP) > 0.15:
        warnings.append(f"每天糧食約 {mean_food:.2f} kg（每日熱量 × 85 % ÷ 4.4 kcal/g），和背負預設每天 −{PACK_DAILY_DROP:g} kg 不同")
    if any(v == "推估" for v in body_src.values()):
        miss = "、".join({"height_cm": "身高", "age": "年齡", "sex": "性別"}[k] for k, v in body_src.items() if v == "推估")
        warnings.append(f"基礎代謝缺{miss}：用假設值（175 cm、40 歲、男，推估）；到設定頁「個人資料」填")
    # schedule: per day from that day's start, every 45–60 min
    events = []
    ev_s = sum(cho["every_min"]) / 2.0 * 60.0
    for n in sorted(by_day):
        ds = [x for x in segs if int(x.get("day") or 1) == n]
        if not ds:
            continue
        t0 = ds[0]["cum_s"] - ds[0]["t"]
        ratio = float(s.get("moving_ratio") or 1.0)
        for tau, _ in fuel_points(ds, ev_s, t0=t0):
            k_ = _km_at(ds, tau, t0)
            events.append({"t_s": tau, "day": n, "km": k_, "seg": ds[_seg_at(ds, tau)]["i"], "kind": "fuel",
                           "eta": _clock(start_time, (tau - t0) / ratio + _stops_before(stops, k_)),
                           "cho_g": cho["dose_g"], "water_ml": None,
                           "action": "行動糧 30–50 g 碳水（一包能量膠、一根香蕉、一個飯糰或一份堅果加糖）"})
        dw = daily[sorted(by_day).index(n)]["water_ml"]
        events.append({"t_s": t0, "day": n, "km": ds[0]["start_km"], "seg": ds[0]["i"], "kind": "start",
                       "eta": start_time, "cho_g": None, "water_ml": [_r50(dw[0]), _r50(dw[1])],
                       "action": f"第 {n} 天出發：今天約喝 {_rng(dw[0] / 1000, dw[1] / 1000, 'L', 1)}（沒有水源就全帶），"
                                 "早餐 1–2 g/kg 碳水"})
        for st in stops or []:
            k_ = float(st.get("km") or 0.0)
            if ds[0]["start_km"] < k_ < ds[-1]["end_km"]:
                t_a = _t_at_km(ds, k_) or t0
                lab = STOP_TYPES[stop_type(st)]["label"] + (f"「{st['name']}」" if st.get("name") else "")
                events.append({"t_s": t_a, "day": n, "km": k_, "seg": ds[_seg_at(ds, t_a)]["i"], "kind": "aid",
                               "stop_type": stop_type(st), "name": st.get("name") or "",
                               "eta": _clock(start_time, (t_a - t0) / ratio + _stops_before(
                                   [x for x in stops if float(x.get("km") or 0) < k_ - 1e-6], k_)),
                               "cho_g": None, "water_ml": None,
                               "action": lab + ("：補水" if STOP_TYPES[stop_type(st)]["water"] else "")})
    events.sort(key=lambda e: (e["t_s"], {"start": 0, "aid": 1}.get(e["kind"], 2)))
    for e in events:
        for x in segs:
            if x["i"] == e["seg"] and int(x.get("day") or 1) == e["day"]:
                x["fuel_action"] = "；".join(filter(None, [x.get("fuel_action"), e["action"]]))
    T = sum(r["moving_h"] for r in rows)
    band_p = energy_band(tot_p, BAND_MODEL)
    return {"category": "hike", "hours": T, "label": cho["label"],
            "kcal": tot_p, "kcal_band": [min(band_p[0], tot_y), max(band_p[1], tot_y)] if tot_y else band_p,
            "band_rel": BAND_MODEL, "kcal_yamamoto": tot_y, "kcal_per_h": tot_p / T if T else None,
            "methods": {"pandolf" if segs else "yamamoto": tot_p}, "daily": daily, "food_kg_mean": mean_food,
            "food_check": {"default_kg": PACK_DAILY_DROP, "density": FOOD_DENSITY, "coverage": FOOD_COVERAGE,
                           "badge": "推估"},
            "cho": {**cho, "per_h": [cho["lo"], cho["hi"]], "total": [cho["lo"] * T, cho["hi"] * T]},
            "water": {"per_h": None, "total_ml": [sum(d["water_ml"][0] for d in daily), sum(d["water_ml"][1] for d in daily)],
                      "thirst": False, "src": "Yamamoto：脫水量 ml ≈ kcal，補 70–80 %（0.7–0.8 ml/kcal）",
                      "caution": "照量喝，不要刻意灌水；每天看尿色和體重（Hew-Butler 2015）", "badge": None, "legs": []},
            "sodium": {"per_h": [na[0], na[1]], "total_mg": [na[0] * T, na[1] * T], "src": na[2]},
            "loading": loading(weight, T, "hike"), "schedule": events, "warnings": warnings,
            "body": {k: body[k] for k in BODY_DEFAULTS}, "body_src": body_src,
            "src": {"pandolf": "Pandolf, Givoni & Goldman 1977（背負、速度、坡度；下坡用 Minetti 走路曲線推估）",
                    "yamamoto": "中原・萩原・山本 2006 Yamamoto CC × (體重 + 背負)",
                    "ree": "Mifflin-St Jeor 1990 × 營地活動係數 1.3（推估）",
                    "band": "Pandolf ±15 %（推估），範圍含 Yamamoto"}}
