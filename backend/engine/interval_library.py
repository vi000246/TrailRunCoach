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
    warm_floor: int = 0          # 每堂課前加熱身 (SP-364): the warm-up's minimum, minutes (variant_adj "warm")
    opt: int = -1                # 個人化間歇 (SP-590): the level (0–5) it was re-cut to (variant_adj "opt"); -1 = not

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


# the sources (stored as a session's 出處, zh-TW): msgids, several joined by 「；」 (_src) — an English page
# shows each part (engine/stored_text, SP-905)
HAUGEN = N_("Haugen 2022（threshold intervals 3–15 分、休 1–2 分）")
PALLADINO_NT = N_("Palladino near-threshold（工休比 3:1–4:1）")
UA_Z3 = N_("Uphill Athlete Zone 3（每趟 15–60 分、工休 4:1–5:1）")
FRIEL_ME = N_("Friel Base 2：2×20 分 Zone 3 肌耐力")
DANIELS_TEMPO = N_("Daniels：連續節奏跑約 20 分起")
KOOP_SSR = N_("Koop／CTS SteadyStateRun（20–60 分，可上坡）")
DANIELS_TEMPO_SHORT = N_("Daniels：連續節奏跑約 20 分起（平日上限的較短版：推估）")
KOOP_CLIMB = N_("Koop／CTS：長上坡穩定爬升（越野專項）")
HAUGEN_HILL = N_("Haugen 2022（坡 5–10%）")
PAL_MAP = N_("Palladino MAP 每趟 2.5–3 分")
PFITZ_LT = N_("Pfitzinger 連續 LT 跑")
STRUCT_EST = N_("結構屬推估")
GRADE_LONG = N_("長坡 4–6%")


def _src(*parts: str) -> str:
    return "；".join(parts)


LIBRARY: dict[str, tuple[Variant, ...]] = {
    # ---- the Zone 3 track (有氧間歇／節奏, Z3sub 88–95 % CP; SP-31): A1 2×15′ → A2 3×12′ → A3 2×20′ → A4 1×30′
    # — every rep 15–30 min (the user's 有氧間歇). Order 總量 → 每趟長度 (interval-prescription.md §A4.1); the
    # rests and the shorter / continuous equivalents are 推估 within the §C2 rules (continuous: ≥ 85 % of the TIZ)
    "a1": (
        _v("a1a", "a1", "Z3sub", 2, 900, 180, "jog", 0.88, 0.95, canonical=True,
           src=_src(UA_Z3, N_("起步約週有氧量 5%（UA）")), src_kind="coach"),
        _v("a1b", "a1", "Z3sub", 3, 600, 120, "jog", 0.88, 0.95, src=_src(UA_Z3, N_("Daniels 巡航間歇延長版")),
           src_kind="coach"),
        _v("a1c", "a1", "Z3sub", 1, 1560, 0, "none", 0.88, 0.92, src=DANIELS_TEMPO_SHORT,
           src_kind="coach"),
        _v("a1d", "a1", "Z3sub", 2, 900, 180, "jog_down", 0.88, 0.95, "hill", grade="4–6%", src=KOOP_SSR,
           src_kind="coach"),
    ),
    "a2": (
        _v("a2a", "a2", "Z3sub", 3, 720, 180, "jog", 0.88, 0.95, canonical=True,
           src=_src(UA_Z3, N_("Pfitzinger LT 每次 20 → 35–45 分")), src_kind="coach"),
        _v("a2b", "a2", "Z3sub", 2, 1080, 180, "jog", 0.88, 0.95,
           src=N_("Uphill Athlete Zone 3（每趟 15–60 分、工休 4:1–5:1）（每趟加長）"), src_kind="coach"),
        _v("a2c", "a2", "Z3sub", 1, 1920, 0, "none", 0.88, 0.92, src=_src(DANIELS_TEMPO, PFITZ_LT),
           src_kind="coach"),
        _v("a2d", "a2", "Z3sub", 3, 720, 180, "jog_down", 0.88, 0.95, "hill", grade="4–6%", src=KOOP_SSR,
           src_kind="coach"),
    ),
    "a3": (
        _v("a3a", "a3", "Z3sub", 2, 1200, 240, "jog", 0.88, 0.95, canonical=True,
           src=_src(FRIEL_ME, UA_Z3), src_kind="coach"),
        _v("a3b", "a3", "Z3sub", 4, 600, 120, "jog", 0.88, 0.95, src=N_("Daniels 巡航間歇（延長到 2×3 英里前的形式）"),
           src_kind="coach"),
        _v("a3c", "a3", "Z3sub", 1, 2100, 0, "none", 0.88, 0.92, src=_src(N_("Pfitzinger 連續 LT 跑 35–45 分"), DANIELS_TEMPO),
           src_kind="coach"),
        _v("a3d", "a3", "Z3sub", 2, 1200, 240, "jog_down", 0.88, 0.95, "hill", grade=GRADE_LONG, src=KOOP_SSR,
           src_kind="coach"),
    ),
    "a4": (
        _v("a4a", "a4", "Z3sub", 1, 1800, 0, "none", 0.88, 0.92, canonical=True,
           src=_src(KOOP_SSR, PFITZ_LT), src_kind="coach"),
        _v("a4b", "a4", "Z3sub", 1, 1800, 0, "none", 0.88, 0.92, "hill", grade=GRADE_LONG,
           src=KOOP_CLIMB, src_kind="coach"),
        _v("a4c", "a4", "Z3sub", 1, 1560, 0, "none", 0.88, 0.92, src=DANIELS_TEMPO_SHORT,
           src_kind="coach"),
    ),
    # ---- Zone 3 巡航版 T1–T3 (Z3sub 90–95 % CP): the old Zone 3 rungs, kept for the weekday cap and
    # low-volume weeks (the Zone 3 track's fallback) and for the stored sessions (rung_key z3a / z3b / z3c)
    "z3a": (
        _v("t1a", "z3a", "Z3sub", 3, 360, 90, "jog", 0.90, 0.95, canonical=True,
           src=_src(HAUGEN, PALLADINO_NT, N_("台灣教練：先練 3 區"))),
        _v("t1b", "z3a", "Z3sub", 6, 180, 60, "jog", 0.92, 0.97, src=_src(HAUGEN, N_("短趟強度略高：推估"))),
        _v("t1c", "z3a", "Z3sub", 3, 360, 120, "jog_down", 0.90, 0.95, "hill", grade="4–6%",
           src=_src(HAUGEN_HILL, N_("Koop／CTS 上坡"))),
        _v("t1d", "z3a", "Z3sub", 1, 1200, 0, "none", 0.88, 0.92, src=N_("Daniels：20 分 T 節奏跑"),
           src_kind="coach"),
    ),
    "z3b": (
        _v("t2a", "z3b", "Z3sub", 3, 480, 120, "jog", 0.90, 0.95, canonical=True,
           src=_src(HAUGEN, PALLADINO_NT, N_("Daniels：T 一次約 20 分"))),
        _v("t2b", "z3b", "Z3sub", 4, 360, 90, "jog", 0.90, 0.95, src=HAUGEN),
        _v("t2c", "z3b", "Z3sub", 5, 300, 60, "jog", 0.90, 0.95,
           src=N_("挪威式短休（Casado 2023；Haugen 2022 「1 min recovery」）")),
        _v("t2d", "z3b", "Z3sub", 3, 480, 120, "jog_down", 0.90, 0.95, "hill", grade="4–6%",
           src=_src(HAUGEN_HILL, N_("Koop／CTS 上坡"))),
    ),
    "z3c": (
        _v("t3a", "z3c", "Z3sub", 2, 720, 120, "jog", 0.90, 0.95, canonical=True,
           src=_src(N_("教練研討會（閾值以下先延長時間）"), N_("CTS TempoRun 每趟 8–20 分")), src_kind="coach"),
        _v("t3b", "z3c", "Z3sub", 1, 1440, 0, "none", 0.88, 0.92, src=N_("Daniels 節奏跑"), src_kind="coach"),
        _v("t3c", "z3c", "Z3sub", 2, 720, 180, "jog_down", 0.90, 0.95, "hill", grade=GRADE_LONG,
           src=KOOP_CLIMB, src_kind="coach"),
    ),
    # ---- T+ (Z3near 97–100 % CP): maintenance once Zone 5 is open -------------
    "tp": (
        _v("tpa", "tp", "Z3near", 3, 420, 120, "jog", 0.97, 1.00, canonical=True,
           src=N_("Palladino near-threshold 每趟 7–10 分、3:1–4:1"), src_kind="coach"),
        _v("tpb", "tp", "Z3near", 4, 300, 90, "jog", 0.98, 1.01,
           src=_src("Palladino near-threshold", "Stryd Cruise Intervals 94–100% CP"), src_kind="coach"),
        _v("tpc", "tp", "Z3near", 3, 420, 120, "jog_down", 0.97, 1.00, "hill", grade="4–6%",
           src=N_("Palladino near-threshold（上坡版：推估）"), src_kind="coach"),
    ),
    # ---- Zone 5 --------------------------------------------------------------------
    "z5a": (
        _v("v1a", "z5a", "Z5", 5, 120, 120, "walk", 1.06, 1.12, canonical=True,
           src=_src(N_("台灣教練：5 區每趟 ≥ 2 分"), N_("Buchheit & Laursen 2013：休 < 2–3 分用被動恢復、約 10 分 T@VO2max"))),
        _v("v1b", "z5a", "Z5", 4, 150, 120, "walk", 1.05, 1.10, src=PAL_MAP,
           src_kind="coach"),
        _v("v1c", "z5a", "Z5", 5, 120, 120, "jog_down", 1.06, 1.12, "hill", grade="6–10%",
           src=_src("Haugen 2022", N_("Barnes 2013（上坡間歇）"), "Koop")),
        _v("v1d", "z5a", "Z5", 4, 120, 120, "walk", 1.05, 1.10, pattern=(120, 180, 180, 120),
           src=_src("Pyramid／Fartlek", STRUCT_EST), src_kind="推估"),
    ),
    "z5b": (
        _v("v2a", "z5b", "Z5", 4, 180, 180, "jog", 1.05, 1.10, canonical=True,
           src=_src(N_("Koop／CTS 6×3 分、休 3 分"), "Palladino MAP 1:1"), src_kind="coach"),
        _v("v2b", "z5b", "Z5", 6, 120, 120, "walk", 1.06, 1.12, src=N_("Buchheit & Laursen 2013 Part II 表 1（6–10×2 分）")),
        _v("v2c", "z5b", "Z5", 4, 180, 180, "jog_down", 1.05, 1.10, "hill", grade="6–10%",
           src=N_("Koop：「uphill if possible」"), src_kind="coach"),
        _v("v2d", "z5b", "Z5", 3, 240, 180, "jog", 1.04, 1.08, src=N_("Helgerud 2007 的形式（量少一點）")),
    ),
    "z5c": (
        _v("v3a", "z5c", "Z5", 5, 180, 150, "walk", 1.05, 1.10, canonical=True,
           src=_src(N_("Palladino：加組數＋縮短休息"), N_("Wen 2019：每堂 ≥ 15 分效果較大"))),
        _v("v3b", "z5c", "Z5", 5, 180, 120, "walk", 1.05, 1.10, pattern=(120, 180, 240, 180, 120),
           src=_src("Pyramid", STRUCT_EST), src_kind="推估"),
        _v("v3c", "z5c", "Z5", 6, 150, 120, "walk", 1.05, 1.10, src=PAL_MAP,
           src_kind="coach"),
        _v("v3d", "z5c", "Z5", 5, 180, 150, "jog_down", 1.05, 1.10, "hill", grade="6–10%",
           src=_src("Haugen 2022", N_("Koop（上坡）"))),
    ),
    "z5d": (
        _v("v4a", "z5d", "Z5", 4, 240, 180, "jog", 1.04, 1.08, canonical=True,
           src=_src(N_("Helgerud 2007：4×4 分、休 3 分 @ 70% HRmax（主動）"), N_("Buchheit & Laursen 2013 結論 3"))),
        _v("v4b", "z5d", "Z5", 8, 120, 90, "walk", 1.06, 1.12,
           src=_src(N_("Buchheit & Laursen 2013 Part II 表 1"), N_("工休比 > 1"))),
        _v("v4c", "z5d", "Z5", 4, 240, 180, "jog_down", 1.04, 1.08, "hill", grade=N_("4–6%（長趟用緩坡）"),
           src=N_("Gajer（Buchheit & Laursen 2013 Part I §3.1.1.4）：上坡要長趟")),
    ),
}
# 非同等 (§C4): selectable by hand, counts as a Zone 5 session, never progress
NON_EQUIV = (
    _v("x3015", "x", "Z5", 13, 30, 15, "jog", 1.10, 1.20, sets=2, set_rest_s=180, listed_equiv=False,
       src=_src(N_("Rønnestad 2015／2020 30/15（騎車）"), N_("每趟 < 2 分不符合 5 區每趟 ≥ 2 分（台灣教練）"))),
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


def warm_floor(v: Variant, prefs=None) -> int:
    """每堂課前加熱身 (SP-364, engine/warmup.py): the warm-up's minimum in minutes — the
    preference's, or the one the stored session carries (variant_adj "warm")."""
    from backend.engine.warmup import floor_min
    return max(int(v.warm_floor or 0), floor_min(prefs))


# SP-951: the city warm-up's words without power in effect (② / ③: no % CP in a 說明)
CITY_NO_POWER = N_("輕鬆跑暖身 {c} 分（可以就是跑到間歇地點；照輕鬆跑心率，最後 2 分漸進）")


def _warm(v: Variant, level: str, c: int) -> list:
    """The warm-up blocks of a level with a `c`-minute city part (§C3)."""
    z5 = is_z5(v)
    city = ("city", c, _("輕鬆跑暖身 {c} 分（可以就是跑到間歇地點；≤ 75% CP，最後 2 分漸進到約 85% CP）", c=c))
    if level == "full":
        river = ("river", 5 if z5 else 3, _("輕鬆跑 {m} 分，漸進", m=5 if z5 else 3))
        return [city, river] + ([("drills", 2, _("動態伸展、跑姿 drill 2 分（擺腿、高抬腿、小步跑）"))] if z5 else []) + \
            [("strides", 3 if z5 else 2, _("快步跑 {n}×15–20 秒（不是衝刺），間隔慢跑 40 秒", n=3 if z5 else 2))]
    if level == "min":
        return [city] + ([("drills", 2, _("動態伸展、drill 2 分")), ("strides", 1, _("快步跑 1×15–20 秒"))] if z5 else [])
    return [city] + ([("drills", 2, _("動態伸展、跑姿 drill 2 分"))] if z5 else []) + \
        [("strides", 3 if z5 else 2, _("快步跑 {n}×15–20 秒，間隔慢跑 40 秒", n=3 if z5 else 2))]


def blocks(v: Variant, level: str = "std", prefs=None, floor: Optional[int] = None) -> dict:
    """{"warm": [(code, minutes, text)], "warm_min", "cool_min", "cool_text"} (§C3).
    The warm-up is at least `floor` minutes (None = warm_floor(v, prefs); SP-364): the city
    part grows by what is missing, a longer warm-up is kept."""
    c = commute_min(prefs)
    warm = _warm(v, level, c)
    lack = (warm_floor(v, prefs) if floor is None else int(floor)) - sum(m for _, m, _ in warm)
    if lack > 0:
        warm = _warm(v, level, c + lack)
    cool = max(10, cooldown_floor(prefs)) if level == "full" else cooldown_floor(prefs)
    return {"warm": warm, "warm_min": sum(m for _, m, _ in warm), "cool_min": cool,
            "cool_text": _("緩和 {m} 分慢跑或走路（跑回家的話就是這段）", m=cool) if cool >= 10
            else _("緩和 {m} 分慢跑或走路", m=cool)}


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
            why.append(_("每趟用掉的無氧容量是標準課表的 {r:.2f} 倍（範圍 0.7–1.5）", r=wr / wref))
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


def fmt_dur(s: float) -> str:
    """fmt_s in the request's language (a rest's / a step's text); structure() keeps fmt_s — titles are
    stored in zh-TW and read by code."""
    s = int(round(s))
    if s < 60:
        return _("{s} 秒", s=s)
    m, r = divmod(s, 60)
    return _("{m} 分", m=m) if not r else f"{m}:{r:02d}"


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
        return _("不休息")
    if v.terrain == "hill":
        return _("慢跑或走下坡恢復（約 {d}）", d=fmt_dur(v.rest_s))
    return _("休 {d}（{mode}）", d=fmt_dur(v.rest_s), mode=_(REST_LABEL.get(v.rest_mode, N_("慢跑"))))


def describe(v: Variant, cp: Optional[float] = None) -> dict:
    """The drawer row's static part."""
    pw = f"{v.lo * cp:.0f}–{v.hi * cp:.0f} W（{v.lo * 100:.0f}–{v.hi * 100:.0f}% CP）" if cp else \
        f"{v.lo * 100:.0f}–{v.hi * 100:.0f}% CP"
    ok, why = equivalent(v)
    hr = {"Z3sub": N_("心率 AeT–LTHR（LTHR 還是預設值時約 85–90% HRmax）"), "Z3near": N_("心率 95–100% LTHR"),
          "Z5": N_("最後 1 分鐘 ≥ 90% HRmax（Helgerud；3 分以上的趟才看心率）")}.get(v.cls)
    hr = _(hr) if hr else ""
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
    o = adj.get("opt")
    if isinstance(o, dict) and o.get("work_s") and o.get("reps"):
        # 個人化間歇 (SP-590, power_zone_plan.optimized): the model's reps / rest / band, first — the
        # state machine's tweaks below apply on top of it
        v = replace(v, reps=int(o["reps"]), work_s=int(o["work_s"]), rest_s=int(o.get("rest_s") or 0),
                    lo=float(o["lo"]), hi=float(o["hi"]), pattern=None, sets=1, set_rest_s=0,
                    opt=int(o.get("level", 0)))
    if adj.get("rest_add") and not v.continuous:
        v = replace(v, rest_s=v.rest_s + 60 * int(adj["rest_add"]))
    if adj.get("power"):
        f = float(adj["power"])
        v = replace(v, lo=round(v.lo * f, 3), hi=round(v.hi * f, 3))
    if adj.get("warm"):
        v = replace(v, warm_floor=int(adj["warm"]))      # 每堂課前加熱身 (SP-364): the stored floor
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


CAP_DAYS = (N_("平日"), N_("週六"), N_("週日"))      # the days a cap is named by: cap_label 「<day>上限」


def _moved(r: dict, rung: str, wd: str, cl: dict, warm: Optional[int]) -> str:
    """「平日上限 40 分 放不下 A3 → 改到週六（<that day's reason>）」, after 「熱身 N 分＋主課放不進…：」 when the
    warm-up floor is why (`warm`). One msgid each, the 「；上次做 …」 of the day's reason written out in it:
    nested as fields it would sit too deep to read back inside a session's 說明 (engine/stored_text)."""
    kw = {"rung": rung, "wd": wd, **cl}
    if r.get("why_swap"):
        kw.update(why=r["why_base"], a=r["why_swap"][0], b=r["why_swap"][1])
        if warm is not None:
            return _("熱身 {w} 分＋主課放不進{day}上限 {cap:.0f} 分：{day}上限 {cap:.0f} 分 放不下 {rung} → 改到{wd}（{why}；"
                     "上次做 {a}，這次換 {b}（同等，不影響進階））", w=warm, **kw)
        return _("{day}上限 {cap:.0f} 分 放不下 {rung} → 改到{wd}（{why}；上次做 {a}，這次換 {b}（同等，不影響進階））", **kw)
    if warm is not None:
        return _("熱身 {w} 分＋主課放不進{day}上限 {cap:.0f} 分：{day}上限 {cap:.0f} 分 放不下 {rung} → 改到{wd}（{why}）",
                 w=warm, why=r["reason"], **kw)
    return _("{day}上限 {cap:.0f} 分 放不下 {rung} → 改到{wd}（{why}）", why=r["reason"], **kw)


def _cap_day(cap_label: str) -> str:
    """「平日上限」 → 「平日」 (the reasons write 「{day}上限 N 分」); every cap_label passed ends in 上限."""
    return _(cap_label[:-2] if cap_label.endswith("上限") else cap_label)


def fit(rung: str, cap: Optional[float] = None, history=(), prefs=None, mountain: bool = False,
        alt_caps: Optional[list] = None, adj: Optional[dict] = None, cap_label: str = "平日上限",
        hill: bool = False) -> dict:
    r = _fit(rung, cap, history, prefs, mountain, alt_caps, adj, cap_label, hill)
    if adj and r.get("rung") == rung:
        r["adj"] = adj
    r.setdefault("cap", cap)            # the day's cap the 個人化間歇 (SP-590, session_for) fits into
    return r


def _fit(rung: str, cap: Optional[float] = None, history=(), prefs=None, mountain: bool = False,
         alt_caps: Optional[list] = None, adj: Optional[dict] = None, cap_label: str = "平日上限",
         hill: bool = False) -> dict:
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
    `hill` (SP-75, the 越野 專項期: 「階梯目前那一階的上坡版」 — Koop prescribes ~80 % of the intervals
    uphill; specific-phase-progression.md §4.1, §4.4): only the rung's uphill variants, from the first
    session on, and a tight cap cuts the uphill one's reps; 課表偏好 terrain_quality = flat still wins.
    {"variant", "base" (before reps / tweak), "level", "reps", "equiv", "progress",
     "reason", "action", "rung", "need_min"}."""
    allowed, prefer_hill = terrains(prefs, mountain)
    vs_all = [adjust(v, adj) for v in LIBRARY.get(rung, ())]
    vs = [v for v in vs_all if v.terrain in allowed] or [v for v in vs_all if v.canonical]
    canon = next(v for v in vs_all if v.canonical)
    ref = canon                         # the variant a tight cap cuts reps from
    if hill and "hill" in allowed and any(v.terrain == "hill" for v in vs):
        vs = [v for v in vs if v.terrain == "hill"]
        ref = vs[0]
    std_len = STD_LEN * main_s(canon)
    rh = _rung_hist(rung, history)
    first = not any(h.get("state", "done") == "done" for h in rh)
    std_pool = _order([v for v in vs if main_s(v) >= std_len], rung, history, prefer_hill)
    short_pool = _order([v for v in vs if main_s(v) < std_len], rung, history, prefer_hill)
    if first and canon in std_pool:
        std_pool = [canon] + [v for v in std_pool if v is not canon]
    ok = lambda v, lv: cap is None or total_min(v, lv, prefs) <= float(cap) + 1e-6
    need = total_min(canon, "min", prefs)
    # 「平日上限 45 分」: every reason below that names the cap starts with it ({day}上限 {cap} 分, SP-905:
    # one msgid each, so an English page reads it back); without a cap they are never reached
    cl = {"day": _cap_day(cap_label), "cap": cap or 0}
    # 每堂課前加熱身 (SP-364): when its floor is what makes the rung not fit, the reasons below say so
    w_min = blocks(canon, "min", prefs)["warm_min"]
    floor_short = cap is not None and w_min > blocks(canon, "min", prefs, floor=0)["warm_min"]

    def wn(text: str) -> str:
        return _("熱身 {w} 分＋主課放不進{day}上限 {cap:.0f} 分：{rest}", w=w_min, rest=text, **cl) \
            if floor_short else text
    last = get(rh[-1]["variant_key"]) if rh else None
    for lv in LEVELS:
        for v in (std_pool[:1] if first else std_pool):
            if not ok(v, lv):
                continue
            if lv == "full":
                why = (_("時間足夠 → 標準版（第一次做這一階：用有研究的那份課表）") if first and v.canonical
                       else _("時間足夠 → 標準版") if v.canonical else _("時間足夠 → 同等的標準長度版"))
            else:
                b = blocks(v, lv, prefs)
                bw = {"w": b["warm_min"], "c": b["cool_min"]}
                why = _("{day}上限 {cap:.0f} 分 → 標準版，暖身縮到 {w} 分、緩和 {c} 分", **cl, **bw) if v.canonical \
                    else _("{day}上限 {cap:.0f} 分 → 同等的標準長度版，暖身縮到 {w} 分、緩和 {c} 分", **cl, **bw)
            swap = {}
            if last is not None and last.key != v.key:
                swap = {"why_base": why, "why_swap": (structure(last), structure(v))}      # the move below quotes it
                why += "；" + _("上次做 {a}，這次換 {b}（同等，不影響進階）", a=structure(last), b=structure(v))
            return _fit_result(v, lv, None, True, True, why, rung=rung, need_min=need, **swap)
    for v in std_pool[1:] if first else []:
        if ok(v, "min"):
            return _fit_result(v, "min", None, True, True,
                               _("{day}上限 {cap:.0f} 分 → 同等的標準長度版 {s}（標準版要 {need:.0f} 分）",
                                 s=structure(v), need=need, **cl), rung=rung, need_min=need)
    for v in short_pool:
        if ok(v, "min"):
            return _fit_result(v, "min", None, True, True,
                               _("{day}上限 {cap:.0f} 分 → 同等較短版 {s}（標準版 {std} 要 {need:.0f} 分）",
                                 s=structure(v), std=structure(canon), need=need, **cl),
                               rung=rung, need_min=need)
    floor = MIN_REPS.get(canon.cls, 2)
    for n in range(ref.n - 1, floor - 1, -1):
        r = with_reps(ref, n)
        if not ok(r, "min"):
            continue
        share = tiz_s(r) / tiz_s(ref)
        if share >= EQUIV_TIZ - 1e-9:
            return _fit_result(r, "min", n, True, True,
                               wn(_("{day}上限 {cap:.0f} 分 → 減成 {n} 趟（目標區時間 {pct:.0f}%，仍算同等）",
                                    n=n, pct=share * 100, **cl)),
                               base=ref, rung=rung, need_min=need)
        return _fit_result(r, "min", n, False, False,
                           wn(_("{day}上限 {cap:.0f} 分 → 縮量版 {n} 趟（目標區時間 {pct:.0f}% < 85%）：達標也只算維持，"
                                "這一階不前進", n=n, pct=share * 100, **cl)),
                           base=ref, rung=rung, need_min=need, reduced=True)
    for label, c, *wd in alt_caps or []:
        if cap is not None and (c is None or c > cap):
            r = fit(rung, c, history, prefs, mountain, None, adj, cap_label=f"{label}上限", hill=hill)
            if r["action"] == "ok" and r["equiv"]:
                return {**{k: x for k, x in r.items() if k not in ("why_base", "why_swap")},
                        "action": "move", "move_to": label, "move_wd": wd[0] if wd else None,
                        "reason": _moved(r, RUNG_NAME.get(rung, rung), _(label), cl, w_min if floor_short else None)}
    prev = PREV_RUNG.get(rung)
    if prev:
        r = fit(prev, cap, history, prefs, mountain, None, None, cap_label, hill)
        return {**r, "action": "back", "equiv": False, "progress": False, "rung": prev, "need_min": need,
                "reason": wn(_("{day}上限 {cap:.0f} 分 放不下 {rung} 的 {s}（需要 {need:.0f} 分以上）：本週改排 {prev} 的 "
                               "{s2}，不算進階", rung=RUNG_NAME.get(rung, rung), s=structure(canon), need=need,
                               prev=RUNG_NAME.get(prev, prev), s2=structure(r["variant"]), **cl))
                + "。" + _("要進階，把平日上限調到 {need:.0f} 分，或把品質課改到週末", need=need) + "。",
                "warn": True}
    r = with_reps(ref, floor)
    return _fit_result(r, "min", floor, False, False,
                       wn(_("{day}上限 {cap:.0f} 分 連 {n} 趟都放不下：先排 {n} 趟，達標也只算維持", n=floor, **cl)),
                       base=ref, rung=rung, need_min=need, reduced=True, warn=True)


HR_LTHR = {"Z3near": (0.95, 1.00), "Z4": (1.00, 1.03), "Z5": (1.00, 1.05)}    # × LTHR; Z5 only on reps ≥ 3 min (Buchheit)
RATE = {"Z3sub": 65.0, "Z3near": 68.0, "Z4": 70.0, "Z5": 72.0}        # TSS / h of a session (the old ladder's rates)


def _hr_text(lo, hi, last_min: bool = False) -> str:
    """「心率 150–170 bpm」, 「…（最後 1 分鐘）」 for a Zone 5 rep."""
    return _("心率 {lo}–{hi} bpm（最後 1 分鐘）", lo=lo, hi=hi) if last_min else _("心率 {lo}–{hi} bpm", lo=lo, hi=hi)


# 「暖身 15 分（輕鬆跑暖身 10 分（…）＋快步跑 2×15–20 秒，…）、緩和 5 分」 by the number of warm-up parts
# (each part a msgid of _warm; 「＋」 between them is the msgid's own)
_WARM_TEXT = (N_("暖身 {w} 分（{p1}）、緩和 {c} 分"), N_("暖身 {w} 分（{p1}＋{p2}）、緩和 {c} 分"),
              N_("暖身 {w} 分（{p1}＋{p2}＋{p3}）、緩和 {c} 分"), N_("暖身 {w} 分（{p1}＋{p2}＋{p3}＋{p4}）、緩和 {c} 分"))


def _warm_text(b: dict) -> str:
    ps = [t for _c, _m, t in b["warm"]]            # _warm: 1–4 parts
    return _(_WARM_TEXT[len(ps) - 1], w=b["warm_min"], c=b["cool_min"], **{f"p{i + 1}": p for i, p in enumerate(ps)})


def _main_text(v: Variant, band: str, pre: str = "") -> str:
    """「<pre>上坡（4–6% 坡）3×8 分，88–95% CP」 (msgids: the grade is a text of its own, the prefix a field),
    else 「<pre>3×8 分，88–95% CP」 (the structure and the band read back as they are; the prefix reads
    the rest as its own field, quality_gate.PREFIX_READ)."""
    if v.terrain == "hill":
        kw = {"grade": _(v.grade), "s": structure(v), "band": band}
        return _("{pre}上坡（{grade} 坡）{s}，{band}", pre=pre, **kw) if pre else _("上坡（{grade} 坡）{s}，{band}", **kw)
    return f"{pre}{structure(v)}，{band}"


def session_for(f: dict, th: dict, prefix: str = "", lthr_default: bool = False, prefs=None,
                swap: str = "auto") -> dict:
    """A week-plan session dict for a fit() result. Keeps the tokens the old
    parsers read (N×M 分 in the title of plain reps, 暖身 / 緩和 N 分 in the
    detail); COROS steps are built from the variant (variant_key + variant_reps
    + variant_blocks), not from the text."""
    v, lv = f["variant"], f["level"]
    cp, lthr, aet = th.get("cp"), th.get("lthr"), th.get("aet")
    use_hr = bool(lthr) and not lthr_default
    pm = th.get("power_model") if isinstance(th.get("power_model"), dict) else None
    # 個人化間歇 (SP-590): under 個人化區間 an interval at / above threshold takes the model's reps
    v, opt, opt_note = _personal(f, v, lv, pm, cp, prefs, prefix)
    # 課表功率區間 (SP-363): the rep's band in the chosen set (Palladino: the variant's own)
    blo, bhi = plan_band(v, pm)
    band = f"{blo * 100:.0f}–{bhi * 100:.0f}% CP"
    # SP-951: a flat rep with power not in effect (② / ③) or 配速 picked → % threshold pace, the HR as a
    # cap note (target_policy.flat_pace / pace_text; the card's summary says the same)
    from backend.engine import target_policy as TP
    pace = v.terrain != "hill" and TP.flat_pace(th, prefs)
    if pace:
        parts = [TP.pace_text(blo, bhi, float(th["tpace"]))]
        band = TP.pace_pct_text(blo, bhi, float(th["tpace"]))      # the 說明's 「3×8 分，105–114% 閾值配速」
    elif cp:
        parts = [_("功率 {lo}–{hi} W（{band}）", lo=f"{blo * cp:.0f}", hi=f"{bhi * cp:.0f}", band=band)]
    else:
        # no CP: RPE; power not in effect (SP-950 ② / ③) → no % CP in the 目標 either
        parts = ["RPE 8" if TP.no_power(th) else f"RPE 8（{band}）"]
    # SP-493: under the 儲備心率 / 最大心率 課表心率區間 the HR is the class's zone of that model (what the
    # push gives a library step, hr_profile.work_band) — not × LTHR; the LTHR model keeps HR_LTHR
    from backend.engine.hr_profile import class_hr
    zb = class_hr(th.get("hr_model") if isinstance(th.get("hr_model"), dict) else None, v.cls)
    hr = None
    if zb and use_hr and (v.cls != "Z5" or min(v.works) >= 180):
        hr = (zb[0], zb[1])
    elif zb:
        pass                                        # a short Z5 rep: no HR, as under the LTHR model
    elif use_hr and v.cls == "Z3sub" and aet:
        hr = (f"{aet:.0f}", f"{lthr:.0f}")
    elif use_hr and v.cls in HR_LTHR and (v.cls != "Z5" or min(v.works) >= 180):
        a, b = HR_LTHR[v.cls]
        hr = (f"{a * lthr:.0f}", f"{b * lthr:.0f}")
    if hr is not None:
        parts.append(TP.cap_text(hr[1]) if pace else _hr_text(hr[0], hr[1], v.cls == "Z5"))
    nop = TP.no_power(th)
    if nop and not pace:
        # SP-951 (owner: no % CP / watts anywhere without power in effect): the 說明's band in HR / RPE words
        band = _hr_text(hr[0], hr[1], v.cls == "Z5") if hr is not None else "RPE 8"
    b = blocks(v, lv, prefs)
    if nop:
        b = {**b, "warm": [(c, m, _(CITY_NO_POWER, c=m) if c == "city" else t) for c, m, t in b["warm"]]}
    total = int(round(total_min(v, lv, prefs)))
    # the 說明 (SP-905: parts joined by 「；」「。」, each one msgid): the reason, then the gate's prefix (no
    # separator after it — a msgid ending in 「：」 / 「，」) with the 個人化間歇 note or the main set, the rest,
    # the warm-up. The prefix is read back as a field: {pre} of the msgids here, or the text after it as
    # {rest} of quality_gate.PREFIX_READ (the plain main set 「3×8 分，88–95% CP」).
    if opt_note:
        head = opt_note + _main_text(v, band, "")          # opt_note starts with the prefix (_opt_text)
    else:
        head = _main_text(v, band, prefix)
    detail = (f"{f['reason']}。{head}；{rest_text(v)}；{_warm_text(b)}"
              + ("；" + _("暖身完 1–2 分內開始第一趟") if is_z5(v) else ""))
    adj = dict(f.get("adj") or {})
    if opt:
        adj["opt"] = opt                    # SP-590: the push / the structure / the judging read it back
    if warm_floor(v, prefs):
        # 每堂課前加熱身 (SP-364): the floor travels with the session, so the push / the editor
        # (no prefs there) build the same warm-up
        adj["warm"] = warm_floor(v, prefs)
    return {"id": "quality", "kind": "quality", "title": title(v), "minutes": total,
            "target": " · ".join(parts), "detail": detail, "source": TP.source_without_power(v.src, th),
            "tss": total / 60.0 * RATE.get(v.cls, 70.0),
            "terrain": "trail" if v.terrain == "hill" else None,
            "variant_key": v.key, "rung_key": f.get("rung") or v.rung, "equiv": bool(f["equiv"]),
            "swap": swap, "swap_reason": f["reason"], "variant_reps": None if opt else f.get("reps"),
            "variant_blocks": lv, "variant_adj": adj or None,
            "progress": bool(f.get("progress", f["equiv"])),
            **({"prefer_days": f["prefer_days"]} if f.get("prefer_days") else {})}


def plan_band(v: Variant, pm: Optional[dict]) -> tuple:
    """(lo, hi) × CP the 課表 writes a variant's reps in: a 個人化間歇 (SP-590) its own model band, any
    other the 課表功率區間's level for its band (SP-363; Palladino: the variant's own)."""
    if v.opt >= 0:
        return v.lo, v.hi
    from backend.engine.power_zone_plan import band as pz_band
    return pz_band(v.lo, v.hi, pm)


def _opt_of(v: Variant) -> dict:
    """The variant_adj["opt"] of an already re-cut variant (fewer reps of a stored 個人化間歇)."""
    return {"level": v.opt, "work_s": int(v.work_s), "reps": int(v.reps), "rest_s": int(v.rest_s),
            "lo": v.lo, "hi": v.hi}


def _personal(f: dict, v: Variant, lv: str, pm: Optional[dict], cp: Optional[float], prefs=None, pre: str = ""):
    """(variant, opt dict or None, detail text) of a session under 個人化區間 (SP-590): an interval at /
    above threshold re-cut to its level of the model's optimized intervals (power_zone_plan.optimized),
    rebuilt from the library row so the stored tweaks (variant_adj) apply on top of it the way
    resolve() does; its reps cut to the day's cap (f["cap"]) down to the level's minimum. The 30/15
    (not an equivalent, picked by hand) is left as it is. Anything else: (v, None, ""). `pre`: the
    detail's prefix, the text's start (_opt_text)."""
    if v.opt >= 0:                                      # a stored one, fewer reps (overview._shorten)
        return v, _opt_of(v), _opt_text(v, pm, cp, None, pre)
    if not v.listed_equiv or pm is None or pm.get("system") != "personal":
        return v, None, ""
    from backend.engine import power_zone_plan as PZP
    base = get(v.key) or v
    o = PZP.optimized(base.lo, base.hi, _median(base.works), tiz_s(base), pm, cp)
    if not o:
        return v, None, ""
    adj = {k: x for k, x in (f.get("adj") or {}).items() if k != "opt"}
    out = adjust(base, {**adj, "opt": o})
    cap, cut = f.get("cap"), None
    if cap is not None:
        while out.reps > int(o.get("min_reps") or 1) and total_min(out, lv, prefs) > float(cap) + 1e-6:
            o = {**o, "reps": out.reps - 1, "rest_s": o["rest_s"] if out.reps - 1 > 1 else 0}
            out = adjust(base, {**adj, "opt": o})
            cut = cap
    o = {k: o[k] for k in ("level", "work_s", "reps", "rest_s", "lo", "hi", "w")}
    return out, o, _opt_text(out, pm, cp, cut, pre)


def _opt_text(v: Variant, pm: Optional[dict], cp: Optional[float], cut: Optional[float], pre: str = "") -> str:
    """「個人化間歇（閾值）：每趟 …；趟數與休息：…[；今天上限 …]。」 after the detail's prefix `pre` (the
    gate's / the dose note's, quality_gate.prefix — a field of the first msgid, so it reads back, SP-905)."""
    from backend.engine import power_zone_plan as PZP
    w = f"{(v.lo + v.hi) / 2 * cp:.0f}" if cp else "–"
    d = fmt_dur(v.work_s)
    kw = {"name": _(PZP.OPT_NAMES[v.opt]), "d": d, "w": w}
    # parts joined by 「；」 (each read back on its own)
    txt = (_("{pre}個人化間歇（{name}）：每趟 {d}、約 {w} W，依你的功率–時間模型", pre=pre, **kw) if pre
           else _("個人化間歇（{name}）：每趟 {d}、約 {w} W，依你的功率–時間模型", **kw)) \
        + "；" + _("趟數與休息：{rule}", rule=PZP.opt_rule(v.opt))
    if cut is not None:
        txt += "；" + _("今天上限 {cap:.0f} 分，減成 {n} 趟", cap=cut, n=v.n)
    return txt + "。"


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
        out.append({"kind": "work", "s": int(w), "lo": v.lo, "hi": v.hi, "text": _("第 {i} 趟 {d}", i=i + 1, d=fmt_dur(w))})
        if i == len(works) - 1:
            break
        if v.sets > 1 and (i + 1) % v.reps == 0:
            out.append({"kind": "rest", "s": int(v.set_rest_s), "mode": "jog", "text": _("組間 {d}", d=fmt_dur(v.set_rest_s))})
        elif v.rest_s:
            out.append({"kind": "rest", "s": int(v.rest_s), "mode": v.rest_mode,
                        "text": _(REST_LABEL.get(v.rest_mode, N_("慢跑")) or N_("恢復"))})
    out.append({"kind": "cool", "code": "cool", "s": int(b["cool_min"] * 60), "text": b["cool_text"]})
    return out


# ---------------------------------------------------------------------------
# the 課表 page: the swap drawer and the editor's templates (§C5.4)
# ---------------------------------------------------------------------------

_SPLIT_WARM = (N_("暖身 {m}（{p1}）"), N_("暖身 {m}（{p1}＋{p2}）"), N_("暖身 {m}（{p1}＋{p2}＋{p3}）"),
               N_("暖身 {m}（{p1}＋{p2}＋{p3}＋{p4}）"))


def _split(v: Variant, level: str, prefs=None) -> str:
    b = blocks(v, level, prefs)
    name = {"city": _("輕鬆跑"), "river": _("漸進"), "drills": "drill", "strides": _("快步跑")}
    ps = [f"{name.get(c, c)} {m}" for c, m, _x in b["warm"]]            # _warm: 1–4 parts
    warm = _(_SPLIT_WARM[len(ps) - 1], m=b["warm_min"], **{f"p{i + 1}": p for i, p in enumerate(ps)})
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


def _fit_shown(rung: str, cap: Optional[float], history, prefs, mountain: bool = False) -> dict:
    """fit() for the swap drawer / the editor's templates (the request's language): its reason made in
    zh-TW, like a stored one, and shown in the request's (engine/stored_text) — the structures in it
    (「3×8 分」) are zh-TW words that only read back that way (SP-905)."""
    from backend.engine import stored_text as ST
    from backend.i18n import DEFAULT_LOCALE, use_locale
    with use_locale(DEFAULT_LOCALE):
        r = fit(rung, cap, history, prefs, mountain)
    return {**r, "reason": ST.shown(r["reason"])}


def drawer(rung: str, cp: Optional[float] = None, cap: Optional[float] = None, prefs=None, history=(),
           current: Optional[str] = None, mountain: bool = False) -> dict:
    """The swap drawer for a session of `rung`: the equivalent variants (rotation order,
    the ones over today's cap greyed with why) and the non-equivalent choices, each with
    its consequence (§C5.4)."""
    if rung not in LIBRARY:
        return {"rung": rung, "equivalent": [], "other": [], "recommended_key": None}
    rec = _fit_shown(rung, cap, history, prefs, mountain)
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
    rec = _fit_shown(rung_now, cap, history, prefs) if rung_now in LIBRARY else None
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
        raise ValueError(_("沒有這個課表：{key}", key=key))
    rung = rung or v0.rung
    v = with_reps(v0, reps)
    canon = canonical(rung)
    equiv = v0.rung == rung and v0.listed_equiv and (canon is None or tiz_s(v) >= EQUIV_TIZ * tiz_s(canon) - 1e-9)
    lv = best_level(v, cap, prefs) or "min"
    why = _("你換成 {s}（同等，不影響進階）", s=structure(v)) if equiv \
        else _("你換成 {s}（非同等：這次不算進階）", s=structure(v))
    s = session_for({"variant": v, "level": lv, "reps": reps if reps and reps < v0.n else None, "equiv": equiv,
                     "progress": equiv, "reason": why, "rung": rung}, th, prefix, prefs=prefs, swap="user")
    out = {k: s[k] for k in ("title", "minutes", "target", "detail", "source", "tss", "variant_key", "rung_key",
                             "equiv", "swap", "swap_reason", "variant_reps", "variant_blocks")}
    if s.get("variant_adj"):
        out["variant_adj"] = s["variant_adj"]       # 每堂課前加熱身 (SP-364): the warm-up floor goes with it
    return out


def library_table(prefs=None) -> list[dict]:
    """Every variant with its totals at each block level (report / docs)."""
    out = []
    for rung, vs in list(LIBRARY.items()) + [("x", NON_EQUIV)]:
        for v in vs:
            d = describe(v)
            d.update({lv: round(total_min(v, lv, prefs), 1) for lv in LEVELS})
            out.append(d)
    return out
