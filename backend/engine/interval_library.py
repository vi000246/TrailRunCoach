"""
The interval session library — every ladder step with 2–4 equivalent
variants, the warm-up / cool-down blocks, the time-cap fitting and the
rotation (docs/research/interval-prescription.md Part C, §A5.3).

Ladder (§A5.3, corrected 2026-10-01):
  Zone 3 (Z3sub, 90–95 % CP): T1 3×6′ → T2 3×8′ → T3 2×12′
  Zone 5 (Z5): V1 5×2′ (106–112 %, 2′ walk) → V2 4×3′ (3′ jog) → V3 5×3′ (2.5′ walk)
               → V4 4×4′ (104–108 %, 3′ jog; Helgerud 2007)
  T+ (Z3near, 97–100 %): a maintenance family once Zone 5 is open
Each rung's `canonical` variant is the studied protocol. With the day's time
cap large enough (or no cap) the planner schedules it — full warm-up and
cool-down — so the first exposure can be compared with the literature; the
shorter equivalents are only the fallback for a tight cap (the user,
2026-10-01). Rotation picks among equivalent variants of standard length.

Equivalence (§C2, all must hold; the numbers are 推估 except 徐國峰's):
  1. same class (Z3sub / Z3near / Z5 — by the band's middle)
  2. time in zone (TIZ, Σ work) within ±15 % of the rung's canonical
  3. rep length: Z5 ≥ 2 min (徐國峰); Z3 ≥ 3 min (Haugen 2022's lower end) or one continuous block
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

from dataclasses import dataclass, replace
from typing import Optional

# ---------------------------------------------------------------------------
# the variants
# ---------------------------------------------------------------------------

CLASS_LABEL = {"Z3sub": "閾值", "Z3near": "近閾值", "Z5": "VO2max"}
CLASS_RANGE = {"Z3sub": (0.88, 0.955), "Z3near": (0.955, 1.02), "Z5": (1.02, 1.25)}   # band middle → class
REST_LABEL = {"walk": "走路或極慢跑", "jog": "慢跑", "jog_down": "慢跑／走下坡", "none": ""}


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
PALLADINO_NT = "Palladino near-threshold（工休比 3:1–4:1，你的筆記）"
LIBRARY: dict[str, tuple[Variant, ...]] = {
    # ---- Zone 3 (Z3sub 90–95 % CP) -------------------------------------------
    "z3a": (
        _v("t1a", "z3a", "Z3sub", 3, 360, 90, "jog", 0.90, 0.95, canonical=True,
           src=f"{HAUGEN}；{PALLADINO_NT}；徐國峰：先練 3 區（私訊）"),
        _v("t1b", "z3a", "Z3sub", 6, 180, 60, "jog", 0.92, 0.97, src=f"{HAUGEN}；短趟強度略高：推估"),
        _v("t1c", "z3a", "Z3sub", 3, 360, 120, "jog_down", 0.90, 0.95, "hill", grade="4–6%",
           src="Haugen 2022（坡 5–10%）；Koop／CTS 上坡"),
        _v("t1d", "z3a", "Z3sub", 1, 1200, 0, "none", 0.88, 0.92, src="Daniels：20 分 T 節奏跑（你的筆記）",
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
           src="WKO 研討會 FR:214-218（閾值以下先延長時間）；CTS TempoRun 每趟 8–20 分", src_kind="coach"),
        _v("t3b", "z3c", "Z3sub", 1, 1440, 0, "none", 0.88, 0.92, src="Daniels 節奏跑（你的筆記）", src_kind="coach"),
        _v("t3c", "z3c", "Z3sub", 2, 720, 180, "jog_down", 0.90, 0.95, "hill", grade="長坡 4–6%",
           src="Koop／CTS：長上坡穩定爬升（越野專項）", src_kind="coach"),
    ),
    # ---- T+ (Z3near 97–100 % CP): maintenance once Zone 5 is open -------------
    "tp": (
        _v("tpa", "tp", "Z3near", 3, 420, 120, "jog", 0.97, 1.00, canonical=True,
           src="Palladino near-threshold 每趟 7–10 分、3:1–4:1（你的筆記）", src_kind="coach"),
        _v("tpb", "tp", "Z3near", 4, 300, 90, "jog", 0.98, 1.01,
           src="Palladino near-threshold；Stryd Cruise Intervals 94–100% CP", src_kind="coach"),
        _v("tpc", "tp", "Z3near", 3, 420, 120, "jog_down", 0.97, 1.00, "hill", grade="4–6%",
           src="Palladino near-threshold（上坡版：推估）", src_kind="coach"),
    ),
    # ---- Zone 5 --------------------------------------------------------------------
    "z5a": (
        _v("v1a", "z5a", "Z5", 5, 120, 120, "walk", 1.06, 1.12, canonical=True,
           src="徐國峰：5 區每趟 ≥ 2 分（私訊）；Buchheit & Laursen 2013：休 < 2–3 分用被動恢復、約 10 分 T@VO2max"),
        _v("v1b", "z5a", "Z5", 4, 150, 120, "walk", 1.05, 1.10, src="Palladino MAP 每趟 2.5–3 分（你的筆記）",
           src_kind="coach"),
        _v("v1c", "z5a", "Z5", 5, 120, 120, "jog_down", 1.06, 1.12, "hill", grade="6–10%",
           src="Haugen 2022；Barnes 2013（上坡間歇）；Koop"),
        _v("v1d", "z5a", "Z5", 4, 120, 120, "walk", 1.05, 1.10, pattern=(120, 180, 180, 120),
           src="你的筆記「Pyramid／Fartlek」；結構屬推估", src_kind="推估"),
    ),
    "z5b": (
        _v("v2a", "z5b", "Z5", 4, 180, 180, "jog", 1.05, 1.10, canonical=True,
           src="Koop／CTS 6×3 分、休 3 分；Palladino MAP 1:1（你的筆記）", src_kind="coach"),
        _v("v2b", "z5b", "Z5", 6, 120, 120, "walk", 1.06, 1.12, src="Buchheit & Laursen 2013 Part II 表 1（6–10×2 分）"),
        _v("v2c", "z5b", "Z5", 4, 180, 180, "jog_down", 1.05, 1.10, "hill", grade="6–10%",
           src="Koop：「uphill if possible」", src_kind="coach"),
        _v("v2d", "z5b", "Z5", 3, 240, 180, "jog", 1.04, 1.08, src="Helgerud 2007 的形式（量少一點）"),
    ),
    "z5c": (
        _v("v3a", "z5c", "Z5", 5, 180, 150, "walk", 1.05, 1.10, canonical=True,
           src="Palladino：加組數＋縮短休息（你的筆記）；Wen 2019：每堂 ≥ 15 分效果較大"),
        _v("v3b", "z5c", "Z5", 5, 180, 120, "walk", 1.05, 1.10, pattern=(120, 180, 240, 180, 120),
           src="你的筆記「Pyramid」；結構屬推估", src_kind="推估"),
        _v("v3c", "z5c", "Z5", 6, 150, 120, "walk", 1.05, 1.10, src="Palladino MAP 每趟 2.5–3 分（你的筆記）",
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
       src="Rønnestad 2015／2020 30/15（騎車）；每趟 < 2 分不符合徐國峰"),
)
RUNG_NAME = {"z3a": "T1", "z3b": "T2", "z3c": "T3", "tp": "T+", "z5a": "V1", "z5b": "V2", "z5c": "V3", "z5d": "V4"}
RUNG_ORDER = ("z3a", "z3b", "z3c", "z5a", "z5b", "z5c", "z5d")
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
    city = ("city", c, f"市區輕鬆跑到河濱 {c} 分（≤ 75% CP，最後 2 分漸進到約 85% CP）")
    if level == "full":
        river = ("river", 5 if z5 else 3, f"河濱輕鬆跑 {5 if z5 else 3} 分，漸進")
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
        why.append("列為非同等（30/15：每趟 < 2 分，證據方向不一致）")
    if class_of(v) != class_of(ref) or v.cls != ref.cls:
        why.append(f"強度類別不同（{v.cls} vs {ref.cls}）")
    t, tr = tiz_s(v), tiz_s(ref)
    if tr and abs(t / tr - 1.0) > TIZ_TOL + 1e-9:
        why.append(f"目標區時間 {t / 60:.0f} 分，和 {tr / 60:.0f} 分差 > 15%")
    if is_z5(v):
        if min(v.works) < Z5_MIN_REP_S:
            why.append("5 區每趟 < 2 分（徐國峰）")
        if v.rest_s > min(v.works) or v.rest_s > Z5_MAX_REST_S:
            why.append("組休比每趟長或 > 3 分")
        wr, wref = wprime_per_rep(v), wprime_per_rep(ref)
        if wref and not WPRIME_RATIO[0] <= wr / wref <= WPRIME_RATIO[1]:
            why.append(f"每趟 W′ 是標準課表的 {wr / wref:.2f} 倍（範圍 0.7–1.5）")
    elif not v.continuous:
        if min(v.works) < Z3_MIN_REP_S:
            why.append("3 區每趟 < 3 分")
        ratio = (sum(v.works) / v.n) / v.rest_s if v.rest_s else None
        if ratio is None or not Z3_RATIO[0] - 1e-9 <= ratio <= Z3_RATIO[1] + 1e-9:
            why.append("工休比不在 3:1–6:1")
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


def title(v: Variant) -> str:
    return f"{CLASS_LABEL[v.cls]} {structure(v)}" + ("上坡" if v.terrain == "hill" else "")


def rest_text(v: Variant) -> str:
    if v.continuous:
        return "不休息"
    if v.terrain == "hill":
        return f"慢跑或走下坡恢復（約 {fmt_s(v.rest_s)}）"
    return f"休 {fmt_s(v.rest_s)}（{REST_LABEL.get(v.rest_mode, '慢跑')}）"


def describe(v: Variant, cp: Optional[float] = None) -> dict:
    """The drawer row's static part."""
    pw = f"{v.lo * cp:.0f}–{v.hi * cp:.0f} W（{v.lo * 100:.0f}–{v.hi * 100:.0f}% CP）" if cp else \
        f"{v.lo * 100:.0f}–{v.hi * 100:.0f}% CP"
    ok, why = equivalent(v)
    return {"key": v.key, "rung": v.rung, "rung_name": RUNG_NAME.get(v.rung, ""), "cls": v.cls,
            "title": title(v), "structure": structure(v), "power": pw, "rest": rest_text(v),
            "terrain": v.terrain, "grade": v.grade, "tiz_min": round(tiz_s(v) / 60.0, 1),
            "main_min": round(main_s(v) / 60.0, 1), "canonical": v.canonical, "source": v.src,
            "source_kind": v.src_kind, "equivalent": ok, "not_equivalent_why": why}


def library_table(prefs=None) -> list[dict]:
    """Every variant with its totals at each block level (report / docs)."""
    out = []
    for rung, vs in list(LIBRARY.items()) + [("x", NON_EQUIV)]:
        for v in vs:
            d = describe(v)
            d.update({lv: round(total_min(v, lv, prefs), 1) for lv in LEVELS})
            out.append(d)
    return out
