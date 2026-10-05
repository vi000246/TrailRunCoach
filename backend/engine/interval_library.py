"""
The interval session library — every ladder step with 2–4 equivalent
variants, the warm-up / cool-down blocks, the time-cap fitting and the
rotation (docs/research/interval-prescription.md Part C, §A5.3).

Ladder (§A5.3, corrected 2026-10-01; two tracks since SP-31, 2026-10-04):
  Zone 3 track (Z3sub, 88–95 % CP; 有氧間歇／節奏, every rep 15–30 min): A1 2×15′ → A2 3×12′
               → A3 2×20′ → A4 1×30′ (UA Zone 3, Friel, Koop, Pfitzinger)
  巡航版 (Z3sub, 90–95 % CP): T1 3×6′ → T2 3×8′ → T3 2×12′ — the Zone 3 track's weekday-cap /
               low-volume fallback and the old Zone 3 rungs (stored rung keys z3a / z3b / z3c)
  Zone 5 (Z5): V1 5×2′ (106–112 %, 2′ walk) → V2 4×3′ (3′ jog) → V3 5×3′ (2.5′ walk)
               → V4 4×4′ (104–108 %, 3′ jog; Helgerud 2007)
  T+ (Z3near, 97–100 %): a maintenance family once Zone 5 is open
Each rung's `canonical` variant is the studied protocol. With the day's time
cap large enough (or no cap) the planner schedules it — full warm-up and
cool-down — so the first exposure can be compared with the literature; the
shorter equivalents are only the fallback for a tight cap (the user,
2026-10-01). Rotation picks among equivalent variants of standard length.

Equivalence (§C2, all must hold; the numbers are 推估 except 台灣教練's):
  1. same class (Z3sub / Z3near / Z5 — by the band's middle)
  2. time in zone (TIZ, Σ work) within ±15 % of the rung's canonical
  3. rep length: Z5 ≥ 2 min (台灣教練); Z3 ≥ 3 min (Haugen 2022's lower end) or one continuous block
  4. work:rest: Z5 rest ≤ the shortest rep and ≤ 3 min (Buchheit: work:rest > 1; Palladino 1:1–2:1);
     Z3 work ÷ rest 3–6 (Palladino 3:1–4:1, Haugen 1–2′ rests) or continuous
  5. Z5 only: W′ per rep (mean) 0.7–1.5 × the canonical's (W′ = (P − CP)·t, CP-relative)
30/15 is never equivalent (reps < 2 min; Rønnestad / Fleckenstein point opposite ways).

Warm-up / cool-down (§C3): the athlete runs ~10 min through the city to reach
the riverside — that is the easy part of every warm-up and is never cut.
  level  Z3 (Z3sub / Z3near)                       Z5
  full   city 10 + riverside 3 (progressive)        city 10 + riverside 5 (progressive)
         + strides 2×20 s = 15                      + drills 2 + strides 3×20 s = 20
  std    city 10 (last 2 progressive) + strides 2   city 10 + drills 2 + strides 3 = 15 (§C3)
         = 12 (§C3)
  min    city 10                                    city 10 + drills 2 + 1 stride = 13 (§C5.3)
  cool-down: full 10, std / min = plan.prefs.cooldown_min (5)
`full` is 推估 (Midgley 2006: 10–15 min warm-up; Bishop 2003; the old app's
15 + 10). The city part = plan.prefs.warmup_commute_min (10).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from backend.i18n import N_, _

# ---------------------------------------------------------------------------
# the variants
# ---------------------------------------------------------------------------

CLASS_LABEL = {"Z3sub": "閾值", "Z3near": "近閾值", "Z4": "超閾值", "Z5": "VO2max"}
# band middle → class, on Palladino's running power zones (engine/zones.py; owner 2026-10-02:
# Palladino everywhere — the editor templates' 三區 88–101 / 四區 101–106 / 五區 ≥ 106 % CP):
# Z3sub = 3A 88–95, Z3near = 3B 95–101, Z4 = 4 101–106 (supra-threshold, not a ladder class),
# Z5 = 5 and above ≥ 106. The old split put Zone 5 at ≥ 102 % CP. The Zone 5 rungs' bands
# (105–110, 104–108) all have their middle at ≥ 106 %, so every one stays Zone 5.
from backend.engine.zones import Z3_LO as _Z3, Z4_LO as _Z4, Z5_LO as _Z5  # noqa: E402

CLASS_RANGE = {"Z3sub": (_Z3, 0.95), "Z3near": (0.95, _Z4), "Z4": (_Z4, _Z5), "Z5": (_Z5, 1.50)}
REST_LABEL = {"walk": N_("走路或極慢跑"), "jog": N_("慢跑"), "jog_down": N_("慢跑／走下坡"), "none": ""}


@dataclass(frozen=True)
class Variant:
    key: str                     # "v3a"
    rung: str                    # "z5c" — the ladder step it serves (quality_gate.Z3 / Z5 keys, "tp" = T+)
    cls: str                     # Z3sub | Z3near | Z5
    reps: int
    work_s: int
    rest_s: int
    rest_mode: str               # walk | jog | jog_down | none (continuous)
    lo: float                    # × CP
    hi: float
    terrain: str                 # flat | hill
    canonical: bool              # the studied protocol (first exposure)
    src: str
    src_kind: str = "peer"       # peer (同儕審查) | coach (教練來源) | 推估
    pattern: Optional[tuple] = None    # pyramids: per-rep work seconds
    sets: int = 1                # 30/15: 2 sets of `reps`
    set_rest_s: int = 0
    listed_equiv: bool = True    # False = the 非同等 options (30/15)
    grade: str = ""              # hill: "4–6%" / "6–10%"

    # ---- derived ----------------------------------------------------------
    @property
    def works(self) -> list[int]:
        if self.pattern:
            return [int(x) for x in self.pattern]
        return [int(self.work_s)] * (int(self.reps) * int(self.sets))

    @property
    def n(self) -> int:
        return len(self.works)

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2.0

    @property
    def continuous(self) -> bool:
        return self.n == 1 and not self.rest_s


def _v(key, rung, cls, reps, work_s, rest_s, rest_mode, lo, hi, terrain="flat", canonical=False, src="",
       src_kind="peer", **kw) -> Variant:
    return Variant(key, rung, cls, reps, work_s, rest_s, rest_mode, lo, hi, terrain, canonical, src, src_kind, **kw)


HAUGEN = "Haugen 2022（threshold intervals 3–15 分、休 1–2 分）"
PALLADINO_NT = "Palladino near-threshold（工休比 3:1–4:1）"
UA_Z3 = "Uphill Athlete Zone 3（每趟 15–60 分、工休 4:1–5:1）"
FRIEL_ME = "Friel Base 2：2×20 分 Zone 3 肌耐力"
DANIELS_TEMPO = "Daniels：連續節奏跑約 20 分起"
KOOP_SSR = "Koop／CTS SteadyStateRun（20–60 分，可上坡）"
LIBRARY: dict[str, tuple[Variant, ...]] = {
    # ---- the Zone 3 track (有氧間歇／節奏, Z3sub 88–95 % CP; SP-31): A1 2×15′ → A2 3×12′ → A3 2×20′ → A4 1×30′
    # — every rep 15–30 min (the user's 有氧間歇). Order 總量 → 每趟長度 (interval-prescription.md §A4.1); the
    # rests and the shorter / continuous equivalents are 推估 within the §C2 rules (continuous: ≥ 85 % of the TIZ)
    "a1": (
        _v("a1a", "a1", "Z3sub", 2, 900, 180, "jog", 0.88, 0.95, canonical=True,
           src=f"{UA_Z3}；起步約週有氧量 5%（UA）", src_kind="coach"),
        _v("a1b", "a1", "Z3sub", 3, 600, 120, "jog", 0.88, 0.95, src=f"{UA_Z3}；Daniels 巡航間歇延長版",
           src_kind="coach"),
        _v("a1c", "a1", "Z3sub", 1, 1560, 0, "none", 0.88, 0.92, src=f"{DANIELS_TEMPO}（平日上限的較短版：推估）",
           src_kind="coach"),
        _v("a1d", "a1", "Z3sub", 2, 900, 180, "jog_down", 0.88, 0.95, "hill", grade="4–6%", src=KOOP_SSR,
           src_kind="coach"),
    ),
    "a2": (
        _v("a2a", "a2", "Z3sub", 3, 720, 180, "jog", 0.88, 0.95, canonical=True,
           src=f"{UA_Z3}；Pfitzinger LT 每次 20 → 35–45 分", src_kind="coach"),
        _v("a2b", "a2", "Z3sub", 2, 1080, 180, "jog", 0.88, 0.95, src=f"{UA_Z3}（每趟加長）", src_kind="coach"),
        _v("a2c", "a2", "Z3sub", 1, 1920, 0, "none", 0.88, 0.92, src=f"{DANIELS_TEMPO}；Pfitzinger 連續 LT 跑",
           src_kind="coach"),
        _v("a2d", "a2", "Z3sub", 3, 720, 180, "jog_down", 0.88, 0.95, "hill", grade="4–6%", src=KOOP_SSR,
           src_kind="coach"),
    ),
    "a3": (
        _v("a3a", "a3", "Z3sub", 2, 1200, 240, "jog", 0.88, 0.95, canonical=True,
           src=f"{FRIEL_ME}；{UA_Z3}", src_kind="coach"),
        _v("a3b", "a3", "Z3sub", 4, 600, 120, "jog", 0.88, 0.95, src="Daniels 巡航間歇（延長到 2×3 英里前的形式）",
           src_kind="coach"),
        _v("a3c", "a3", "Z3sub", 1, 2100, 0, "none", 0.88, 0.92, src=f"Pfitzinger 連續 LT 跑 35–45 分；{DANIELS_TEMPO}",
           src_kind="coach"),
        _v("a3d", "a3", "Z3sub", 2, 1200, 240, "jog_down", 0.88, 0.95, "hill", grade="長坡 4–6%", src=KOOP_SSR,
           src_kind="coach"),
    ),
    "a4": (
        _v("a4a", "a4", "Z3sub", 1, 1800, 0, "none", 0.88, 0.92, canonical=True,
           src=f"{KOOP_SSR}；Pfitzinger 連續 LT 跑", src_kind="coach"),
        _v("a4b", "a4", "Z3sub", 1, 1800, 0, "none", 0.88, 0.92, "hill", grade="長坡 4–6%",
           src="Koop／CTS：長上坡穩定爬升（越野專項）", src_kind="coach"),
        _v("a4c", "a4", "Z3sub", 1, 1560, 0, "none", 0.88, 0.92, src=f"{DANIELS_TEMPO}（平日上限的較短版：推估）",
           src_kind="coach"),
    ),
    # ---- Zone 3 巡航版 T1–T3 (Z3sub 90–95 % CP): the old Zone 3 rungs, kept for the weekday cap and
    # low-volume weeks (the Zone 3 track's fallback) and for the stored sessions (rung_key z3a / z3b / z3c)
    "z3a": (
        _v("t1a", "z3a", "Z3sub", 3, 360, 90, "jog", 0.90, 0.95, canonical=True,
           src=f"{HAUGEN}；{PALLADINO_NT}；台灣教練：先練 3 區"),
        _v("t1b", "z3a", "Z3sub", 6, 180, 60, "jog", 0.92, 0.97, src=f"{HAUGEN}；短趟強度略高：推估"),
        _v("t1c", "z3a", "Z3sub", 3, 360, 120, "jog_down", 0.90, 0.95, "hill", grade="4–6%",
           src="Haugen 2022（坡 5–10%）；Koop／CTS 上坡"),
        _v("t1d", "z3a", "Z3sub", 1, 1200, 0, "none", 0.88, 0.92, src="Daniels：20 分 T 節奏跑",
           src_kind="coach"),
    ),
    "z3b": (
        _v("t2a", "z3b", "Z3sub", 3, 480, 120, "jog", 0.90, 0.95, canonical=True,
           src=f"{HAUGEN}；{PALLADINO_NT}；Daniels：T 一次約 20 分"),
        _v("t2b", "z3b", "Z3sub", 4, 360, 90, "jog", 0.90, 0.95, src=HAUGEN),
        _v("t2c", "z3b", "Z3sub", 5, 300, 60, "jog", 0.90, 0.95,
           src="挪威式短休（Casado 2023；Haugen 2022 「1 min recovery」）"),
        _v("t2d", "z3b", "Z3sub", 3, 480, 120, "jog_down", 0.90, 0.95, "hill", grade="4–6%",
           src="Haugen 2022（坡 5–10%）；Koop／CTS 上坡"),
    ),
    "z3c": (
        _v("t3a", "z3c", "Z3sub", 2, 720, 120, "jog", 0.90, 0.95, canonical=True,
           src="WKO 研討會（閾值以下先延長時間）；CTS TempoRun 每趟 8–20 分", src_kind="coach"),
        _v("t3b", "z3c", "Z3sub", 1, 1440, 0, "none", 0.88, 0.92, src="Daniels 節奏跑", src_kind="coach"),
        _v("t3c", "z3c", "Z3sub", 2, 720, 180, "jog_down", 0.90, 0.95, "hill", grade="長坡 4–6%",
           src="Koop／CTS：長上坡穩定爬升（越野專項）", src_kind="coach"),
    ),
    # ---- T+ (Z3near 97–100 % CP): maintenance once Zone 5 is open -------------
    "tp": (
        _v("tpa", "tp", "Z3near", 3, 420, 120, "jog", 0.97, 1.00, canonical=True,
           src="Palladino near-threshold 每趟 7–10 分、3:1–4:1", src_kind="coach"),
        _v("tpb", "tp", "Z3near", 4, 300, 90, "jog", 0.98, 1.01,
           src="Palladino near-threshold；Stryd Cruise Intervals 94–100% CP", src_kind="coach"),
        _v("tpc", "tp", "Z3near", 3, 420, 120, "jog_down", 0.97, 1.00, "hill", grade="4–6%",
           src="Palladino near-threshold（上坡版：推估）", src_kind="coach"),
    ),
    # ---- Zone 5 --------------------------------------------------------------------
    "z5a": (
        _v("v1a", "z5a", "Z5", 5, 120, 120, "walk", 1.06, 1.12, canonical=True,
           src="台灣教練：5 區每趟 ≥ 2 分；Buchheit & Laursen 2013：休 < 2–3 分用被動恢復、約 10 分 T@VO2max"),
        _v("v1b", "z5a", "Z5", 4, 150, 120, "walk", 1.05, 1.10, src="Palladino MAP 每趟 2.5–3 分",
           src_kind="coach"),
        _v("v1c", "z5a", "Z5", 5, 120, 120, "jog_down", 1.06, 1.12, "hill", grade="6–10%",
           src="Haugen 2022；Barnes 2013（上坡間歇）；Koop"),
        _v("v1d", "z5a", "Z5", 4, 120, 120, "walk", 1.05, 1.10, pattern=(120, 180, 180, 120),
           src="Pyramid／Fartlek；結構屬推估", src_kind="推估"),
    ),
    "z5b": (
        _v("v2a", "z5b", "Z5", 4, 180, 180, "jog", 1.05, 1.10, canonical=True,
           src="Koop／CTS 6×3 分、休 3 分；Palladino MAP 1:1", src_kind="coach"),
        _v("v2b", "z5b", "Z5", 6, 120, 120, "walk", 1.06, 1.12, src="Buchheit & Laursen 2013 Part II 表 1（6–10×2 分）"),
        _v("v2c", "z5b", "Z5", 4, 180, 180, "jog_down", 1.05, 1.10, "hill", grade="6–10%",
           src="Koop：「uphill if possible」", src_kind="coach"),
        _v("v2d", "z5b", "Z5", 3, 240, 180, "jog", 1.04, 1.08, src="Helgerud 2007 的形式（量少一點）"),
    ),
    "z5c": (
        _v("v3a", "z5c", "Z5", 5, 180, 150, "walk", 1.05, 1.10, canonical=True,
           src="Palladino：加組數＋縮短休息；Wen 2019：每堂 ≥ 15 分效果較大"),
        _v("v3b", "z5c", "Z5", 5, 180, 120, "walk", 1.05, 1.10, pattern=(120, 180, 240, 180, 120),
           src="Pyramid；結構屬推估", src_kind="推估"),
        _v("v3c", "z5c", "Z5", 6, 150, 120, "walk", 1.05, 1.10, src="Palladino MAP 每趟 2.5–3 分",
           src_kind="coach"),
        _v("v3d", "z5c", "Z5", 5, 180, 150, "jog_down", 1.05, 1.10, "hill", grade="6–10%",
           src="Haugen 2022；Koop（上坡）"),
    ),
    "z5d": (
        _v("v4a", "z5d", "Z5", 4, 240, 180, "jog", 1.04, 1.08, canonical=True,
           src="Helgerud 2007：4×4 分、休 3 分 @ 70% HRmax（主動）；Buchheit & Laursen 2013 結論 3"),
        _v("v4b", "z5d", "Z5", 8, 120, 90, "walk", 1.06, 1.12, src="Buchheit & Laursen 2013 Part II 表 1；工休比 > 1"),
        _v("v4c", "z5d", "Z5", 4, 240, 180, "jog_down", 1.04, 1.08, "hill", grade="4–6%（長趟用緩坡）",
           src="Gajer（Buchheit & Laursen 2013 Part I §3.1.1.4）：上坡要長趟"),
    ),
}
# 非同等 (§C4): selectable by hand, counts as a Zone 5 session, never progress
NON_EQUIV = (
    _v("x3015", "x", "Z5", 13, 30, 15, "jog", 1.10, 1.20, sets=2, set_rest_s=180, listed_equiv=False,
       src="Rønnestad 2015／2020 30/15（騎車）；每趟 < 2 分不符合 5 區每趟 ≥ 2 分（台灣教練）"),
)
RUNG_NAME = {"a1": "A1", "a2": "A2", "a3": "A3", "a4": "A4", "z3a": "T1", "z3b": "T2", "z3c": "T3", "tp": "T+",
             "z5a": "V1", "z5b": "V2", "z5c": "V3", "z5d": "V4"}
# two tracks (SP-31): the Zone 3 track A1–A4 (巡航版 T1–T3 its weekday / low-volume fallback) and the
# Zone 5 track V1–V4. RUNG_ORDER lists every rung (the editor's groups); PREV_RUNG is the rung a tight
# cap falls back to (fit's 「上一階」): an A rung → the one before, A1 → T3 (巡航版, 「平日用巡航版」),
# V1 → T3 as before (Zone 3 maintenance)
Z3_TRACK = ("a1", "a2", "a3", "a4")
CRUISE_RUNGS = ("z3a", "z3b", "z3c")
Z5_TRACK = ("z5a", "z5b", "z5c", "z5d")
RUNG_ORDER = Z3_TRACK + CRUISE_RUNGS + Z5_TRACK
PREV_RUNG = {"a2": "a1", "a3": "a2", "a4": "a3", "a1": "z3c", "z3b": "z3a", "z3c": "z3b",
             "z5a": "z3c", "z5b": "z5a", "z5c": "z5b", "z5d": "z5c"}


def track_of(rung: Optional[str]) -> Optional[str]:
    """"z3" (A1–A4, T1–T3, T+), "z5" (V1–V4, 30/15) or None for a rung key."""
    if rung in Z3_TRACK or rung in CRUISE_RUNGS or rung == "tp":
        return "z3"
    if rung in Z5_TRACK or rung == "x":
        return "z5"
    return None
ALL: dict[str, Variant] = {v.key: v for vs in LIBRARY.values() for v in vs} | {v.key: v for v in NON_EQUIV}


def get(key: Optional[str]) -> Optional[Variant]:
    return ALL.get(str(key)) if key else None


def canonical(rung: str) -> Optional[Variant]:
    return next((v for v in LIBRARY.get(rung, ()) if v.canonical), None)


def with_reps(v: Variant, reps: Optional[int]) -> Variant:
    """A reduced variant (fewer reps; pyramids drop from the end)."""
    if not reps or int(reps) >= v.n or v.sets > 1:
        return v
    r = max(1, int(reps))
    if v.pattern:
        return replace(v, pattern=tuple(v.pattern[:r]), reps=r)
    return replace(v, reps=r)


# ---------------------------------------------------------------------------
# sizes
# ---------------------------------------------------------------------------

def tiz_s(v: Variant) -> int:
    """Planned time in zone: Σ work seconds."""
    return int(sum(v.works))


def main_s(v: Variant) -> int:
    """Σ work + rests between reps (none after the last; 30/15 adds the set rest)."""
    n = v.n
    rests = (n - v.sets) * v.rest_s + (v.sets - 1) * v.set_rest_s if v.sets > 1 else (n - 1) * v.rest_s
    return int(sum(v.works) + rests)


def is_z5(v: Variant) -> bool:
    return v.cls == "Z5"


def commute_min(prefs=None) -> int:
    return int(getattr(prefs, "warmup_commute_min", 10) if prefs is not None else 10)


def cooldown_floor(prefs=None) -> int:
    return int(getattr(prefs, "cooldown_min", 5) if prefs is not None else 5)


LEVELS = ("full", "std", "min")
LEVEL_LABEL = {"full": "完整", "std": "標準", "min": "下限"}


def blocks(v: Variant, level: str = "std", prefs=None) -> dict:
    """{"warm": [(code, minutes, text)], "warm_min", "cool_min", "cool_text"} (§C3)."""
    c = commute_min(prefs)
    z5 = is_z5(v)
    city = ("city", c, f"輕鬆跑暖身 {c} 分（可以就是跑到間歇地點；≤ 75% CP，最後 2 分漸進到約 85% CP）")
    if level == "full":
        river = ("river", 5 if z5 else 3, f"輕鬆跑 {5 if z5 else 3} 分，漸進")
        warm = [city, river] + ([("drills", 2, "動態伸展、跑姿 drill 2 分（擺腿、高抬腿、小步跑）")] if z5 else []) + \
            [("strides", 3 if z5 else 2, f"快步跑 {3 if z5 else 2}×15–20 秒（不是衝刺），間隔慢跑 40 秒")]
        cool = max(10, cooldown_floor(prefs))
    elif level == "min":
        warm = [city] + ([("drills", 2, "動態伸展、drill 2 分"), ("strides", 1, "快步跑 1×15–20 秒")] if z5 else [])
        cool = cooldown_floor(prefs)
    else:
        warm = [city] + ([("drills", 2, "動態伸展、跑姿 drill 2 分")] if z5 else []) + \
            [("strides", 3 if z5 else 2, f"快步跑 {3 if z5 else 2}×15–20 秒，間隔慢跑 40 秒")]
        cool = cooldown_floor(prefs)
    return {"warm": warm, "warm_min": sum(m for _, m, _ in warm), "cool_min": cool,
            "cool_text": f"緩和 {cool} 分慢跑或走路" + ("（跑回家的話就是這段）" if cool >= 10 else "")}


def total_min(v: Variant, level: str = "std", prefs=None) -> float:
    b = blocks(v, level, prefs)
    return b["warm_min"] + main_s(v) / 60.0 + b["cool_min"]


# ---------------------------------------------------------------------------
# equivalence (§C2)
# ---------------------------------------------------------------------------

TIZ_TOL = 0.15                   # 推估 (Seiler 2013 / Wen 2019: accumulated time matters)
Z5_MIN_REP_S, Z3_MIN_REP_S = 120, 180
Z5_MAX_REST_S = 180
Z3_RATIO = (3.0, 6.0)
WPRIME_RATIO = (0.7, 1.5)        # 推估


def class_of(v: Variant) -> Optional[str]:
    m = v.mid
    return next((c for c, (a, b) in CLASS_RANGE.items() if a <= m < b), None)


def wprime_per_rep(v: Variant) -> float:
    """Mean W′ per rep, CP-relative: Σ (mid − 1)·work / n (0 below CP)."""
    return sum(max(0.0, v.mid - 1.0) * w for w in v.works) / max(1, v.n)


def equivalent(v: Variant, ref: Optional[Variant] = None) -> tuple[bool, list[str]]:
    """(ok, failed reasons) of `v` against `ref` (default: its rung's canonical)."""
    ref = ref or canonical(v.rung) or v
    why = []
    if not v.listed_equiv:
        why.append(_("列為非同等（30/15：每趟 < 2 分，證據方向不一致）"))
    if class_of(v) != class_of(ref) or v.cls != ref.cls:
        why.append(_("強度類別不同（{a} vs {b}）", a=v.cls, b=ref.cls))
    t, tr = tiz_s(v), tiz_s(ref)
    if tr and abs(t / tr - 1.0) > TIZ_TOL + 1e-9:
        why.append(_("目標區時間 {t:.0f} 分，和 {tr:.0f} 分差 > 15%", t=t / 60, tr=tr / 60))
    if is_z5(v):
        if min(v.works) < Z5_MIN_REP_S:
            why.append(_("5 區每趟 < 2 分（台灣教練）"))
        if v.rest_s > min(v.works) or v.rest_s > Z5_MAX_REST_S:
            why.append(_("組休比每趟長或 > 3 分"))
        wr, wref = wprime_per_rep(v), wprime_per_rep(ref)
        if wref and not WPRIME_RATIO[0] <= wr / wref <= WPRIME_RATIO[1]:
            why.append(_("每趟 W′ 是標準課表的 {r:.2f} 倍（範圍 0.7–1.5）", r=wr / wref))
    elif not v.continuous:
        if min(v.works) < Z3_MIN_REP_S:
            why.append(_("3 區每趟 < 3 分"))
        ratio = (sum(v.works) / v.n) / v.rest_s if v.rest_s else None
        if ratio is None or not Z3_RATIO[0] - 1e-9 <= ratio <= Z3_RATIO[1] + 1e-9:
            why.append(_("工休比不在 3:1–6:1"))
    return (not why), why


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

def fmt_s(s: float) -> str:
    """180 → "3 分", 150 → "2:30", 30 → "30 秒"."""
    s = int(round(s))
    if s < 60:
        return f"{s} 秒"
    m, r = divmod(s, 60)
    return f"{m} 分" if not r else f"{m}:{r:02d}"


def _mins(s: float) -> str:
    x = s / 60.0
    return f"{x:.0f}" if abs(x - round(x)) < 0.01 else f"{x:.1f}"


def structure(v: Variant) -> str:
    """「3×8 分」「6×2:30」「2-3-4-3-2 分金字塔」「連續 20 分」「2 組 × 13×30 秒／15 秒」."""
    if v.sets > 1:
        return f"{v.sets} 組 × {v.reps}×{fmt_s(v.work_s)}／{fmt_s(v.rest_s)}"
    if v.continuous:
        return f"連續 {fmt_s(v.work_s)}"
    if v.pattern:
        body = "-".join(_mins(x) for x in v.pattern)
        return f"{body} 分" + ("金字塔" if len(v.pattern) >= 5 else "")
    return f"{v.n}×{fmt_s(v.work_s)}"


# 強度課's three families (SP-79, the user's decision 2026-10-04): a session's title starts with its
# family — 有氧間歇 / VO2max 間歇 / 速度 (workout_templates.FAMILIES; 巡航 = the cruise sub-type) —, not
# the old class word (閾值 / 近閾值 / VO2max). Raw zh-TW msgids: a stored title is never translated.
# The rule is workout_templates.classify's on a variant (Z3: 長 tempo when a rep is ≥ 15′ or one
# continuous block, else 巡航; Z4: 超閾值 when a rep is > 5′, else VO2max) — a test holds it to
# family_of_variant for every library row. 「N×M 分」 stays (five parsers read it off the title).
FAMILY_TITLE = {"aerobic": "有氧間歇", "cruise": "有氧間歇（巡航）", "supra": "有氧間歇（超閾值）",
                "vo2max": "VO2max 間歇", "speed": "速度"}
TEMPO_MIN_REP_S = 900        # = workout_templates.TEMPO_MIN_S
VO2_MAX_REP_S = 300          # = workout_templates.VO2_MAX_S


def _median(xs) -> float:
    xs = sorted(xs)
    n = len(xs)
    return float(xs[n // 2]) if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def title_prefix(v: Variant) -> str:
    rep = _median(v.works)
    if v.cls == "Z5":
        return FAMILY_TITLE["vo2max"]
    if v.cls == "Z4":
        return FAMILY_TITLE["supra" if rep > VO2_MAX_REP_S else "vo2max"]
    return FAMILY_TITLE["aerobic" if v.continuous or rep >= TEMPO_MIN_REP_S else "cruise"]


def _join(prefix: str, rest: str) -> str:
    """「有氧間歇 2×15 分」 but 「有氧間歇（巡航）3×8 分」 (no space after a closing bracket)."""
    return prefix + ("" if prefix.endswith("）") else " ") + rest


def plain_title(v: Variant) -> str:
    """「有氧間歇 2×15 分」 (the 插入範本 row: no 上坡)."""
    return _join(title_prefix(v), structure(v))


def title(v: Variant) -> str:
    return plain_title(v) + ("上坡" if v.terrain == "hill" else "")


def family_word(v: Variant) -> str:
    """「有氧間歇・巡航」: the family in a label that is already in brackets."""
    return title_prefix(v).replace("（", "・").replace("）", "")


# the titles stored before SP-79 → today's (plan_store.display_title, quality_gate's title matchers):
# 「閾值／近閾值 <structure>」 → 有氧間歇 or 有氧間歇（巡航） by the rep length, 「VO2max <structure>」 →
# VO2max 間歇, the fixed sessions' 「閾值節奏」「節奏」 → by the rep length, 「爬坡間歇 N×M 分」 →
# 「VO2max 間歇 N×M 分上坡」, the taper's 「短強度 N×M 分」 (98–102 % CP short reps) → 有氧間歇（巡航）.
# The rest of the title (上坡, （平路）, （只排閾值）…) is kept; anything else
# (the old ladder's 「閾值下 3×8 分」「VO2max 間歇 4×4 分」, a title of your own) is left as written.
_OLD_Z3 = re.compile(r"^(?:閾值|近閾值|閾值節奏|節奏) (?=\S)")
_OLD_Z5 = re.compile(r"^VO2max (?!間歇)(?=\S)")
_OLD_HILL = re.compile(r"^爬坡間歇 ?(\d+\s*[×xX]\s*\d+\s*分)")
_OLD_TAPER = re.compile(r"^短強度 ?(?=\d+\s*[×xX]\s*\d+\s*分)")       # the taper's, renamed 2026-10-05
_OLD_FLAT_HILL = re.compile(r"^間歇 ?(5\s*[×xX]\s*4\s*分)(?=（平路）)")     # the hill set on 平路 (plan_prefs)


def _rep_s(rest: str) -> Optional[float]:
    """The typical rep of a title's structure (seconds); None = one continuous block."""
    if rest.startswith("連續"):
        return None
    m = re.match(r"(?:\d+\s*組\s*×\s*)?\d+\s*[×xX]\s*(\d+)(?::(\d+))?\s*(分|秒)?", rest)
    if m:
        a = int(m.group(1))
        if m.group(2) is not None:
            return a * 60 + int(m.group(2))
        return a if m.group(3) == "秒" else a * 60
    m = re.match(r"([\d.]+(?:-[\d.]+)+)\s*分", rest)           # a pyramid 「2-3-4-3-2 分」
    if m:
        return _median([float(x) * 60 for x in m.group(1).split("-")])
    return None


def renamed(title: Optional[str]) -> Optional[str]:
    """A pre-SP-79 auto title in today's words (unchanged when it isn't one)."""
    if not title:
        return title
    t = str(title)
    m = _OLD_HILL.match(t) or _OLD_FLAT_HILL.match(t)
    if m:
        return _join(FAMILY_TITLE["vo2max"], m.group(1)) + ("上坡" if t.startswith("爬坡") else "") + t[m.end():]
    m = _OLD_Z5.match(t)
    if m:
        return _join(FAMILY_TITLE["vo2max"], t[m.end():])
    m = _OLD_TAPER.match(t)
    if m:
        return _join(FAMILY_TITLE["cruise"], t[m.end():])
    m = _OLD_Z3.match(t)
    if m:
        rest = t[m.end():]
        rep = _rep_s(rest)
        return _join(FAMILY_TITLE["aerobic" if rep is None or rep >= TEMPO_MIN_REP_S else "cruise"], rest)
    return t


def rest_text(v: Variant) -> str:
    if v.continuous:
        return "不休息"
    if v.terrain == "hill":
        return f"慢跑或走下坡恢復（約 {fmt_s(v.rest_s)}）"
    return _("休 {d}（{mode}）", d=fmt_s(v.rest_s), mode=_(REST_LABEL.get(v.rest_mode, N_("慢跑"))))


def describe(v: Variant, cp: Optional[float] = None) -> dict:
    """The drawer row's static part."""
    pw = f"{v.lo * cp:.0f}–{v.hi * cp:.0f} W（{v.lo * 100:.0f}–{v.hi * 100:.0f}% CP）" if cp else \
        f"{v.lo * 100:.0f}–{v.hi * 100:.0f}% CP"
    ok, why = equivalent(v)
    hr = {"Z3sub": "心率 AeT–LTHR（LTHR 還是預設值時約 85–90% HRmax）", "Z3near": "心率 95–100% LTHR",
          "Z5": "最後 1 分鐘 ≥ 90% HRmax（Helgerud；3 分以上的趟才看心率）"}.get(v.cls, "")
    return {"key": v.key, "rung": v.rung, "hr": hr, "rung_name": RUNG_NAME.get(v.rung, ""), "cls": v.cls,
            "title": title(v), "structure": structure(v), "power": pw, "rest": rest_text(v),
            "terrain": v.terrain, "grade": v.grade, "tiz_min": round(tiz_s(v) / 60.0, 1),
            "main_min": round(main_s(v) / 60.0, 1), "canonical": v.canonical, "source": v.src,
            "source_kind": v.src_kind, "equivalent": ok, "not_equivalent_why": why}


# ---------------------------------------------------------------------------
# choosing a variant for a day (§C5.2) and fitting it into the day's cap (§C5.3)
# ---------------------------------------------------------------------------

ROTATE_N = 2                     # 推估 (§C5.2-3): skip a variant used in the last 2 sessions of its rung
STD_LEN = 0.9                    # 推估: main set ≥ 90 % of the canonical's = 「標準長度」; shorter = cap fallback
EQUIV_TIZ = 1.0 - TIZ_TOL        # fewer reps with ≥ 85 % of the TIZ still count as equivalent (§C5.3-3)
MIN_REPS = {"Z5": 3, "Z4": 2, "Z3sub": 2, "Z3near": 2}
BAD = ("unadapted", "too_high")


def terrains(prefs=None, mountain: bool = False) -> tuple[set, bool]:
    """(allowed terrains, prefer hill every 2nd session). Weekdays are flat by
    default; plan.prefs.terrain_quality = hill or a mountain goal adds the uphill
    versions (Koop / Daniels: train the race terrain before it — 推估 cadence)."""
    t = getattr(prefs, "terrain_quality", "any") if prefs is not None else "any"
    if t == "flat":
        return {"flat"}, False
    if t == "hill":
        return {"flat", "hill"}, True
    return ({"flat", "hill"}, True) if mountain else ({"flat"}, False)


def adjust(v: Variant, adj: Optional[dict]) -> Variant:
    """quality_gate's state-machine tweak on a variant: rest + N min, or the band × factor."""
    if not adj:
        return v
    if adj.get("rest_add") and not v.continuous:
        v = replace(v, rest_s=v.rest_s + 60 * int(adj["rest_add"]))
    if adj.get("power"):
        f = float(adj["power"])
        v = replace(v, lo=round(v.lo * f, 3), hi=round(v.hi * f, 3))
    return v


def _rung_hist(rung: str, history) -> list[dict]:
    return [h for h in (history or []) if h.get("rung_key") == rung and h.get("variant_key")]


def _order(vs: list[Variant], rung: str, history, prefer_hill: bool) -> list[Variant]:
    """Rotation order (§C5.2-3, all 推估): not in the last ROTATE_N sessions of the
    rung, not 未適應 the last time it was done, uphill every 2nd session when
    preferred, the canonical first on a tie."""
    rh = _rung_hist(rung, history)
    recent = [h["variant_key"] for h in rh[-ROTATE_N:]]
    last_out = {}
    for h in rh:
        last_out[h["variant_key"]] = h.get("outcome")
    want_hill = prefer_hill and bool(rh) and (get(rh[-1]["variant_key"]) or vs[0]).terrain == "flat"

    def key(v: Variant):
        return (v.key in recent, last_out.get(v.key) in BAD,
                prefer_hill and (v.terrain == "hill") != want_hill, not v.canonical, v.key)
    return sorted(vs, key=key)


def _fit_result(v, level, reps, equiv, progress, reason, action="ok", base=None, **kw) -> dict:
    return {"variant": v, "level": level, "reps": reps, "equiv": equiv, "progress": progress,
            "reason": reason, "action": action, "base": base or v, **kw}


def fit(rung: str, cap: Optional[float] = None, history=(), prefs=None, mountain: bool = False,
        alt_caps: Optional[list] = None, adj: Optional[dict] = None, cap_label: str = "平日上限") -> dict:
    r = _fit(rung, cap, history, prefs, mountain, alt_caps, adj, cap_label)
    if adj and r.get("rung") == rung:
        r["adj"] = adj
    return r


def _fit(rung: str, cap: Optional[float] = None, history=(), prefs=None, mountain: bool = False,
         alt_caps: Optional[list] = None, adj: Optional[dict] = None, cap_label: str = "平日上限") -> dict:
    """The session for `rung` on a day with `cap` minutes (None = no cap), §C5.3:
      1. time enough → the canonical (first exposure) or a rotated equivalent of
         standard length, with the full warm-up / cool-down; then the same with
         the std, then the min blocks (the city run is never cut);
      2. an equivalent shorter variant (min blocks) — progression as usual;
      3. fewer reps of the canonical: ≥ 85 % of its TIZ still equivalent, else
         「縮量版」 (達標 counts as maintenance, the rung doesn't move); floor
         Z5 3 reps, Z3 2;
      4. another day with a bigger cap (`alt_caps` [(label, cap)], already
         filtered for the 48-h / Zone 5 spacing rules) → action "move";
      5. the rung before's equivalent as maintenance with a warning → action "back".
    `adj`: the state machine's tweak (rest + 1 min / power −5 %), applied first.
    {"variant", "base" (before reps / tweak), "level", "reps", "equiv", "progress",
     "reason", "action", "rung", "need_min"}."""
    allowed, prefer_hill = terrains(prefs, mountain)
    vs_all = [adjust(v, adj) for v in LIBRARY.get(rung, ())]
    vs = [v for v in vs_all if v.terrain in allowed] or [v for v in vs_all if v.canonical]
    canon = next(v for v in vs_all if v.canonical)
    std_len = STD_LEN * main_s(canon)
    rh = _rung_hist(rung, history)
    first = not any(h.get("state", "done") == "done" for h in rh)
    std_pool = _order([v for v in vs if main_s(v) >= std_len], rung, history, prefer_hill)
    short_pool = _order([v for v in vs if main_s(v) < std_len], rung, history, prefer_hill)
    if first and canon in std_pool:
        std_pool = [canon] + [v for v in std_pool if v is not canon]
    ok = lambda v, lv: cap is None or total_min(v, lv, prefs) <= float(cap) + 1e-6
    need = total_min(canon, "min", prefs)
    cl = f"{cap_label} {cap:.0f} 分" if cap is not None else ""
    last = get(rh[-1]["variant_key"]) if rh else None
    for lv in LEVELS:
        for v in (std_pool[:1] if first else std_pool):
            if not ok(v, lv):
                continue
            if lv == "full":
                why = ("時間足夠 → 標準版" if v.canonical else "時間足夠 → 同等的標準長度版") + \
                    ("（第一次做這一階：用有研究的那份課表）" if first else "")
            else:
                b = blocks(v, lv, prefs)
                why = f"{cl} → {'標準版' if v.canonical else '同等的標準長度版'}，暖身縮到 {b['warm_min']} 分、緩和 {b['cool_min']} 分"
            if last is not None and last.key != v.key:
                why += f"；上次做 {structure(last)}，這次換 {structure(v)}（同等，不影響進階）"
            return _fit_result(v, lv, None, True, True, why, rung=rung, need_min=need)
    for v in std_pool[1:] if first else []:
        if ok(v, "min"):
            return _fit_result(v, "min", None, True, True, f"{cl} → 同等的標準長度版 {structure(v)}（標準版要 "
                               f"{total_min(canon, 'min', prefs):.0f} 分）", rung=rung, need_min=need)
    for v in short_pool:
        if ok(v, "min"):
            return _fit_result(v, "min", None, True, True,
                               f"{cl} → 同等較短版 {structure(v)}（標準版 {structure(canon)} 要 {need:.0f} 分）",
                               rung=rung, need_min=need)
    floor = MIN_REPS.get(canon.cls, 2)
    for n in range(canon.n - 1, floor - 1, -1):
        r = with_reps(canon, n)
        if not ok(r, "min"):
            continue
        share = tiz_s(r) / tiz_s(canon)
        if share >= EQUIV_TIZ - 1e-9:
            return _fit_result(r, "min", n, True, True, f"{cl} → 減成 {n} 趟（目標區時間 {share * 100:.0f}%，仍算同等）",
                               base=canon, rung=rung, need_min=need)
        return _fit_result(r, "min", n, False, False,
                           f"{cl} → 縮量版 {n} 趟（目標區時間 {share * 100:.0f}% < 85%）：達標也只算維持，這一階不前進",
                           base=canon, rung=rung, need_min=need, reduced=True)
    for label, c, *wd in alt_caps or []:
        if cap is not None and (c is None or c > cap):
            r = fit(rung, c, history, prefs, mountain, None, adj, cap_label=f"{label}上限")
            if r["action"] == "ok" and r["equiv"]:
                return {**r, "action": "move", "move_to": label, "move_wd": wd[0] if wd else None,
                        "reason": f"{cl} 放不下 {RUNG_NAME.get(rung, rung)} → 改到{label}（{r['reason']}）"}
    prev = PREV_RUNG.get(rung)
    if prev:
        r = fit(prev, cap, history, prefs, mountain, None, None, cap_label)
        what = f"{RUNG_NAME.get(rung, rung)} 的 {structure(canon)}"
        return {**r, "action": "back", "equiv": False, "progress": False, "rung": prev, "need_min": need,
                "reason": f"{cl} 放不下 {what}（需要 {need:.0f} 分以上）：本週改排 {RUNG_NAME.get(prev, prev)} 的 "
                          f"{structure(r['variant'])}，不算進階。要進階，把平日上限調到 {need:.0f} 分，或把品質課改到週末。",
                "warn": True}
    r = with_reps(canon, floor)
    return _fit_result(r, "min", floor, False, False,
                       f"{cl} 連 {floor} 趟都放不下：先排 {floor} 趟，達標也只算維持", base=canon, rung=rung,
                       need_min=need, reduced=True, warn=True)


HR_LTHR = {"Z3near": (0.95, 1.00), "Z4": (1.00, 1.03), "Z5": (1.00, 1.05)}    # × LTHR; Z5 only on reps ≥ 3 min (Buchheit)
RATE = {"Z3sub": 65.0, "Z3near": 68.0, "Z4": 70.0, "Z5": 72.0}        # TSS / h of a session (the old ladder's rates)


def session_for(f: dict, th: dict, prefix: str = "", lthr_default: bool = False, prefs=None,
                swap: str = "auto") -> dict:
    """A week-plan session dict for a fit() result. Keeps the tokens the old
    parsers read (N×M 分 in the title of plain reps, 暖身 / 緩和 N 分 in the
    detail); COROS steps are built from the variant (variant_key + variant_reps
    + variant_blocks), not from the text."""
    v, lv = f["variant"], f["level"]
    cp, lthr, aet = th.get("cp"), th.get("lthr"), th.get("aet")
    use_hr = bool(lthr) and not lthr_default
    band = f"{v.lo * 100:.0f}–{v.hi * 100:.0f}% CP"
    parts = [f"功率 {v.lo * cp:.0f}–{v.hi * cp:.0f} W（{band}）"] if cp else [f"RPE 8（{band}）"]
    if use_hr and v.cls == "Z3sub" and aet:
        parts.append(f"心率 {aet:.0f}–{lthr:.0f} bpm")
    elif use_hr and v.cls in HR_LTHR and (v.cls != "Z5" or min(v.works) >= 180):
        a, b = HR_LTHR[v.cls]
        parts.append(f"心率 {a * lthr:.0f}–{b * lthr:.0f} bpm" + ("（最後 1 分鐘）" if v.cls == "Z5" else ""))
    b = blocks(v, lv, prefs)
    warm_txt = "＋".join(t for _, _, t in b["warm"])
    hill = f"上坡（{v.grade} 坡）" if v.terrain == "hill" else ""
    body = f"{hill}{structure(v)}，{band}；{rest_text(v)}"
    total = int(round(total_min(v, lv, prefs)))
    detail = (f"{f['reason']}。{prefix}{body}；暖身 {b['warm_min']} 分（{warm_txt}）、緩和 {b['cool_min']} 分"
              + ("；暖身完 1–2 分內開始第一趟" if is_z5(v) else ""))
    return {"id": "quality", "kind": "quality", "title": title(v), "minutes": total,
            "target": " · ".join(parts), "detail": detail, "source": v.src,
            "tss": total / 60.0 * RATE.get(v.cls, 70.0),
            "terrain": "trail" if v.terrain == "hill" else None,
            "variant_key": v.key, "rung_key": f.get("rung") or v.rung, "equiv": bool(f["equiv"]),
            "swap": swap, "swap_reason": f["reason"], "variant_reps": f.get("reps"),
            "variant_blocks": lv, "variant_adj": f.get("adj") or None,
            "progress": bool(f.get("progress", f["equiv"])),
            **({"prefer_days": f["prefer_days"]} if f.get("prefer_days") else {})}


def resolve(key: Optional[str], reps: Optional[int] = None, adj: Optional[dict] = None) -> Optional[Variant]:
    """The variant a stored session was planned as (library row, fewer reps, the tweak)."""
    v = get(key)
    if v is None:
        return None
    return with_reps(adjust(v, adj if isinstance(adj, dict) else None), reps)


def steps(v: Variant, level: str = "std", prefs=None) -> list[dict]:
    """The session as timed steps (sync/coros_workouts builds the COROS program
    from these; interval_reps matches laps against them):
    [{"kind": warm | work | rest | cool, "s", "code", "text", "lo", "hi", "mode"}]."""
    b = blocks(v, level, prefs)
    out = []
    for code, m, text in b["warm"]:
        out.append({"kind": "warm", "code": code, "s": int(m * 60), "text": text})
    works = v.works
    for i, w in enumerate(works):
        out.append({"kind": "work", "s": int(w), "lo": v.lo, "hi": v.hi, "text": _("第 {i} 趟 {d}", i=i + 1, d=fmt_s(w))})
        if i == len(works) - 1:
            break
        if v.sets > 1 and (i + 1) % v.reps == 0:
            out.append({"kind": "rest", "s": int(v.set_rest_s), "mode": "jog", "text": _("組間 {d}", d=fmt_s(v.set_rest_s))})
        elif v.rest_s:
            out.append({"kind": "rest", "s": int(v.rest_s), "mode": v.rest_mode,
                        "text": _(REST_LABEL.get(v.rest_mode, N_("慢跑")) or N_("恢復"))})
    out.append({"kind": "cool", "code": "cool", "s": int(b["cool_min"] * 60), "text": b["cool_text"]})
    return out


# ---------------------------------------------------------------------------
# the 課表 page: the swap drawer and the editor's templates (§C5.4)
# ---------------------------------------------------------------------------

def _split(v: Variant, level: str, prefs=None) -> str:
    b = blocks(v, level, prefs)
    name = {"city": _("輕鬆跑"), "river": _("漸進"), "drills": "drill", "strides": _("快步跑")}
    warm = _("暖身 {m}（", m=b['warm_min']) + "＋".join(f"{name.get(c, c)} {m}" for c, m, _x in b["warm"]) + "）"
    return _("{warm} · 主課 {main} · 緩和 {cool} ＝ {total:.0f} 分", warm=warm, main=_mins(main_s(v)),
             cool=b['cool_min'], total=total_min(v, level, prefs))


def best_level(v: Variant, cap: Optional[float], prefs=None) -> Optional[str]:
    return next((lv for lv in LEVELS if cap is None or total_min(v, lv, prefs) <= cap + 1e-6), None)


def option_row(v: Variant, cp: Optional[float], cap: Optional[float], prefs=None, history=(),
               consequence: str = "", equiv: bool = True, reps: Optional[int] = None) -> dict:
    lv = best_level(v, cap, prefs)
    d = describe(v, cp)
    last = next((h for h in reversed(list(history or [])) if h.get("variant_key") == v.key), None)
    d.update({"reps": reps, "level": lv or "min", "fits": lv is not None, "total_min": round(total_min(v, lv or "min", prefs)),
              "split": _split(v, lv or "min", prefs),
              "why_not": "" if lv is not None else _("超過今天上限 {cap:.0f} 分（最短也要 {need:.0f} 分）", cap=cap, need=total_min(v, 'min', prefs)),
              "last": {"date": last.get("day"), "outcome": last.get("outcome")} if last else None,
              "equiv": equiv, "consequence": consequence or (_("同等：不影響進階") if equiv else "")})
    return d


def drawer(rung: str, cp: Optional[float] = None, cap: Optional[float] = None, prefs=None, history=(),
           current: Optional[str] = None, mountain: bool = False) -> dict:
    """The swap drawer for a session of `rung`: the equivalent variants (rotation order,
    the ones over today's cap greyed with why) and the non-equivalent choices, each with
    its consequence (§C5.4)."""
    if rung not in LIBRARY:
        return {"rung": rung, "equivalent": [], "other": [], "recommended_key": None}
    rec = fit(rung, cap, history, prefs, mountain)
    allowed, hill = terrains(prefs, mountain)
    vs = _order(list(LIBRARY[rung]), rung, history, hill)
    eq = [option_row(v, cp, cap, prefs, history) for v in vs]
    other = []
    if PREV_RUNG.get(rung):
        p = canonical(PREV_RUNG[rung])
        other.append(option_row(p, cp, cap, prefs, history, _("上一階（{name}）：維持，不算進階", name=RUNG_NAME[p.rung]), False))
    c = canonical(rung)
    if c.n > MIN_REPS.get(c.cls, 2):
        r = with_reps(c, c.n - 1)
        share = tiz_s(r) / tiz_s(c)
        other.append({**option_row(r, cp, cap, prefs, history,
                                   _("縮量版：目標區時間 {pct:.0f}%，達標也不前進", pct=share * 100) if share < EQUIV_TIZ
                                   else _("少一趟（仍同等）"), share >= EQUIV_TIZ, c.n - 1),
                      "title": _("{title}（少 1 趟：{n} 趟）", title=title(c), n=r.n)})
    if c.cls == "Z5":
        other.append(option_row(NON_EQUIV[0], cp, cap, prefs, history, _("30/15：算一堂 5 區（頻率照算），不算進階"), False))
        z3 = canonical("z3b")
        other.append(option_row(z3, cp, cap, prefs, history, _("換成 3 區：這週沒有 5 區，不算進階"), False))
    return {"rung": rung, "rung_name": RUNG_NAME.get(rung, rung), "current_key": current,
            "recommended_key": rec["variant"].key if rec.get("rung") == rung else None,
            "recommended_reason": rec["reason"], "equivalent": eq, "other": other,
            "terrain_note": _("上坡版用一樣的 %CP，但上坡時攝氧量比例較低（Gajer，Buchheit Part I），下坡回程有離心負荷")}


def templates(cp: Optional[float] = None, cap: Optional[float] = None, prefs=None, history=(),
              rung_now: Optional[str] = None) -> dict:
    """Every library session for the editor's dropdown, grouped by rung, the one fit()
    picks for the athlete's current rung and cap marked 推薦."""
    rec = fit(rung_now, cap, history, prefs) if rung_now in LIBRARY else None
    groups = []
    for rung in RUNG_ORDER + ("tp",):
        rows = [option_row(v, cp, cap, prefs, history) for v in LIBRARY[rung]]
        groups.append({"rung": rung, "label": f"{RUNG_NAME[rung]}（{family_word(canonical(rung))}）", "rows": rows})
    groups.append({"rung": "x", "label": _("非同等（不算進階）"),
                   "rows": [option_row(v, cp, cap, prefs, history, _("每趟 < 2 分：算一堂 5 區，不算進階"), False)
                            for v in NON_EQUIV]})
    return {"groups": groups, "recommended_key": rec["variant"].key if rec else None,
            "recommended_reason": _("推薦（依你目前的階段與時間上限）：") + rec["reason"] if rec else ""}


def variant_patch(key: str, rung: Optional[str], th: dict, prefs=None, cap: Optional[float] = None,
                  reps: Optional[int] = None, prefix: str = "") -> dict:
    """The stored fields of a session switched to `key` by the user (swap = user): the
    session keeps its ladder position (`rung`); it counts for progression only when the
    variant is an equivalent of that rung (and keeps ≥ 85 % of the TIZ)."""
    v0 = get(key)
    if v0 is None:
        raise ValueError(f"沒有這個課表：{key}")
    rung = rung or v0.rung
    v = with_reps(v0, reps)
    canon = canonical(rung)
    equiv = v0.rung == rung and v0.listed_equiv and (canon is None or tiz_s(v) >= EQUIV_TIZ * tiz_s(canon) - 1e-9)
    lv = best_level(v, cap, prefs) or "min"
    why = f"你換成 {structure(v)}（{'同等，不影響進階' if equiv else '非同等：這次不算進階'}）"
    s = session_for({"variant": v, "level": lv, "reps": reps if reps and reps < v0.n else None, "equiv": equiv,
                     "progress": equiv, "reason": why, "rung": rung}, th, prefix, prefs=prefs, swap="user")
    return {k: s[k] for k in ("title", "minutes", "target", "detail", "source", "tss", "variant_key", "rung_key",
                              "equiv", "swap", "swap_reason", "variant_reps", "variant_blocks")}


def library_table(prefs=None) -> list[dict]:
    """Every variant with its totals at each block level (report / docs)."""
    out = []
    for rung, vs in list(LIBRARY.items()) + [("x", NON_EQUIV)]:
        for v in vs:
            d = describe(v)
            d.update({lv: round(total_min(v, lv, prefs), 1) for lv in LEVELS})
            out.append(d)
    return out
