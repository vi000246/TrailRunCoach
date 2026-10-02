"""
The editor's 插入範本 library (docs/research/workout-templates.md): published workouts
from named coaches / papers, each with a warm-up and a source. Every step carries its
SOURCE'S OWN basis (no top-level switch): Palladino and the Stryd tests → power (% CP);
Friel, Uphill Athlete, Pfitzinger, Seiler, 徐國峰's E run, the trail sessions → heart
rate (% LTHR, or ≤ AeT); Daniels' T / I / R, Canova, Billat → pace (× threshold pace).
Where the source's own number isn't one the app has (% HRmax, VO2max pace, RPE) it is
converted and the template's `conv` says so (推估).

Categories follow the session's 類型: easy (輕鬆跑 / 長時間), quality (強度課, split
三區 88–101 % CP / 四區 101–106 % / 五區 ≥ 106 % — Palladino's running power zones
3 / 4 / 5, the editor chart's colours; set per template from its main set), test,
trail (越野跑).

Targets
  pw(lo, hi)     power × CP          hr(lo, hi)   heart rate × LTHR
  AET            heart rate ≤ AeT    pace(lo, hi) × threshold pace (bigger = slower)
  OPEN           no target (all-out test bouts, strides, walk recoveries)
Lap-button steps carry `est` (the protocol's minimum, s) so the total can be estimated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from backend.engine import workout_steps as WS

SUBS = [{"id": "z3", "label": "三區", "tip": "主課 88–101% CP（Palladino 3 區：閾值）"},
        {"id": "z4", "label": "四區", "tip": "主課 101–106% CP（Palladino 4 區：超閾值）"},
        {"id": "z5", "label": "五區", "tip": "主課 ≥ 106% CP（Palladino 5 區以上：VO2max、短間歇）"}]
CATS = [{"id": "easy", "label": "輕鬆跑"}, {"id": "quality", "label": "強度課", "subs": SUBS},
        {"id": "test", "label": "測試"}, {"id": "trail", "label": "越野跑"}]


def sub_of(mid: float) -> str:
    """三區 / 四區 / 五區 by a power band middle (× CP; the chart's colour steps 3 / 4 / 5)."""
    return "z5" if mid >= 1.06 else "z4" if mid >= 1.01 else "z3"


def pw(lo: float, hi: float) -> dict:
    return {"type": "power", "mode": "pct", "lo": lo, "hi": hi}


def hr(lo: float, hi: float) -> dict:
    return {"type": "hr", "mode": "pct", "lo": lo, "hi": hi}


def pace(lo: float, hi: float) -> dict:
    return {"type": "pace", "mode": "pct", "lo": lo, "hi": hi}


AET = {"type": "hr", "mode": "zone", "zone": "aet"}
OPEN = {"type": "auto", "intent": "open"}


class B:
    """Step builders bound to one id counter."""

    def __init__(self):
        self.ids = WS._Ids("w")

    def t(self, kind, sec, target=None, note=""):
        return WS.step(self.ids, kind, int(sec), dict(target or OPEN), note)

    def d(self, kind, meters, target=None, note=""):
        return WS.step(self.ids, kind, {"type": "distance", "value": int(meters)}, dict(target or OPEN), note)

    def lap(self, kind, est, note, target=None):
        return WS.step(self.ids, kind, {"type": "open", "est": int(est)}, dict(target or OPEN), note)

    def rep(self, n, items, last_rest=True, note=""):
        return WS.rep(self.ids, n, items, last_rest, note)

    # the warm-ups the sources write out
    def pal_warm(self, ez_min=12, after_min=2):
        """Palladino / Stryd: EZ ≤ 80 % CP, 2 × 1′ @ 97–103 % (2′ easy), then easy."""
        return [self.t("warm", ez_min * 60, pw(0.70, 0.80), "EZ ≤ 80% CP"),
                self.rep(2, [self.t("warm", 60, pw(0.97, 1.03), "1′ 97–103% CP"),
                             self.t("warm", 120, pw(0.70, 0.80), "輕鬆跑")], True, "2×1′"),
                self.t("warm", after_min * 60, pw(0.70, 0.80), "輕鬆跑")]

    def pal_test_warm(self):
        """Palladino's test warm-up (3 / 10 / 20 min tests): 12′ EZ, 1′ 97–103 %, 30″
        102–107 %, 10″ acceleration, each with easy recovery, then 3′ walk."""
        ez = pw(0.70, 0.80)
        return [self.t("warm", 12 * 60, ez, "EZ ≤ 80% CP"),
                self.t("warm", 60, pw(0.97, 1.03), "1′ 97–103% CP"), self.t("warm", 120, ez, "輕鬆跑"),
                self.t("warm", 30, pw(1.02, 1.07), "30″ 102–107% CP"), self.t("warm", 90, ez, "輕鬆跑"),
                self.t("other", 10, OPEN, "10″ 漸進加速，放鬆不硬拚"), self.t("warm", 110, ez, "輕鬆跑"),
                self.t("rest", 180, OPEN, "停下走路，呼吸完全恢復")]

    def warm(self, minutes=15, note="暖身", target=AET):
        return self.t("warm", minutes * 60, target, note)

    def cool(self, minutes=10, note="緩和", target=AET):
        return self.t("cool", minutes * 60, target, note)


@dataclass(frozen=True)
class Template:
    key: str
    cat: str                 # easy | quality | test | trail
    title: str
    src: str                 # author, work, year
    url: str
    build: Callable[[B], list]
    basis: str = "hr"        # the source's own basis: power | hr | pace
    sub: Optional[str] = None    # quality: z3 / z4 / z5
    src_kind: str = "coach"  # peer | coach | 推估 (the structure itself)
    conv: str = ""           # what is converted (推估)
    note: str = ""


PAL = "Steve Palladino，Stryd 訓練計畫（Stryd 內建課表）"
PAL_URL = "https://help.stryd.com/en/articles/7065214-stryd-training-plans-by-steve-palladino"
PFITZ = "Pfitzinger & Douglas《Advanced Marathoning》3rd ed. 2019"
PFITZ_URL = "https://www.slideshare.net/slideshow/marathon-training-webinar/11191347"
DANIELS = "Jack Daniels《Daniels' Running Formula》3rd ed. 2013"
DANIELS_URL = "https://www.coachray.nz/2023/05/03/jack-daniels-running-intensity/"
KOOP = "Jason Koop《Training Essentials for Ultrarunning》／TrainRight 2025"
KOOP_URL = "https://trainright.com/decoding-ultramarathon-interval-workouts/"
UA = "Uphill Athlete（House／Johnston）"
FRIEL_URL = "https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/"
GARMIN_DSW = "https://www.garmin.com/zh-TW/blog/running/the-climbing-ability-of-trail-running/"
HRMAX = "% LTHR 由來源的 % HRmax ÷ 0.9 換算（推估）"
TP = "配速是閾值配速的倍數（app 的 T 配速，推估）"


def _steep(pct: float):
    """陡坡健走（模擬負重）: no pack, the Pandolf grade of `pct` × body weight (engine/steep_hill.py)."""
    from backend.engine import steep_hill as SH
    sim = SH.simulated(pct)
    speed = f"{sim['kmh']:g} km/h"

    def build(b: B) -> list:
        return [b.t("warm", 10 * 60, AET, "平路暖身"),
                b.t("work", 30 * 60, AET, f"坡度 {sim['grade']:g}%、{speed}，用走的"),
                b.t("cool", 5 * 60, AET, "平路緩和")]
    return sim, build


_S5, _S5B = _steep(0.05)
_S10, _S10B = _steep(0.10)
_S15, _S15B = _steep(0.15)
STEEP_SRC = "Pandolf 1977（同代謝率的坡度）；UA trekking（跑步機坡度替代背包）；loaded-carry-training.md §1.1、§3.2"
STEEP_URL = "https://doi.org/10.1152/jappl.1977.43.4.577"
STEEP_CONV = "坡度由 Pandolf 公式換算：不背包、這個坡度的代謝量 ≈ 在 12% 坡、3.5 km/h 背這個重量（推估）；心率 ≤ AeT"

TEMPLATES: list[Template] = [
    # ---------------- 輕鬆跑 ----------------
    Template("pal_ez", "easy", "Palladino EZ 輕鬆跑＋3×10″ 加速", PAL, PAL_URL, lambda b: [
        b.t("warm", 24 * 60, pw(0.70, 0.80), "EZ ≤ 80% CP"),
        b.rep(3, [b.t("other", 10, OPEN, "10″ 漸進加速"), b.t("rest", 110, pw(0.65, 0.80), "輕鬆跑")], True, "加速 3×10″"),
        b.t("cool", 5 * 60, pw(0.70, 0.80), "EZ")], basis="power"),
    Template("pfitz_recovery", "easy", "Pfitzinger 恢復跑 30′", PFITZ, PFITZ_URL, lambda b: [
        b.t("warm", 5 * 60, hr(0.70, 0.80), "很慢開始"),
        b.t("work", 25 * 60, hr(0.75, 0.84), "恢復跑 < 76% HRmax"),
        b.t("cool", 5 * 60, OPEN, "走路")], conv=HRMAX),
    Template("pfitz_ga", "easy", "Pfitzinger 有氧耐力跑 60′", PFITZ, PFITZ_URL, lambda b: [
        b.warm(10, target=hr(0.75, 0.85)), b.t("work", 45 * 60, hr(0.78, 0.89), "70–81% HRmax"),
        b.cool(5, target=hr(0.70, 0.80))], conv=HRMAX),
    Template("pfitz_long", "easy", "Pfitzinger 長跑 2 小時", PFITZ, PFITZ_URL, lambda b: [
        b.warm(15, target=hr(0.75, 0.85)), b.t("work", 95 * 60, hr(0.82, 0.90), "74–84% HRmax"),
        b.cool(10, target=hr(0.70, 0.80))], conv=HRMAX + "（上緣壓在 90%）"),
    Template("daniels_e", "easy", "Daniels E 輕鬆跑 50′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(10), b.t("work", 35 * 60, hr(0.72, 0.88), "E：65–79% HRmax"), b.cool(5)], conv=HRMAX),
    Template("xu_e90", "easy", "徐國峰 E 強度 90′（看心率飄移）",
             "徐國峰 部落格 2015-10-14《E 配速心率飄移》；《全方位的馬拉松科學化訓練》2015",
             "http://rocky549.blogspot.com/2015/10/e.html", lambda b: [
        b.warm(10), b.t("work", 80 * 60, hr(0.72, 0.88), "平路 E；記第 10、90 分心率"), b.cool(10)],
        conv=HRMAX, note="飄移 < 10% 有氧很好、< 5% 國手級（徐國峰）"),
    Template("ua_z2", "easy", "Uphill Athlete 有氧基礎 Z2 60′", UA + "「Zone 2 Heart Rate Training」2026",
             "https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/", lambda b: [
        b.warm(10, "Z1：AeT −20%～−10%"), b.t("work", 60 * 60, AET, "Z2：AeT −10%～AeT"), b.cool(10)]),
    Template("seiler_z1", "easy", "Seiler 兩極化低強度長課 100′",
             "Seiler 2010, IJSPP 5(3):276–291（1 區 60–72% HRmax）",
             "https://www.researchgate.net/publication/46403553_What_is_Best_Practice_for_Training_Intensity_and_Duration_Distribution_in_Endurance_Athletes",
             lambda b: [b.warm(10), b.t("work", 80 * 60, hr(0.67, 0.80), "1 區：60–72% HRmax"), b.cool(10)],
             src_kind="peer", conv=HRMAX),

    # ---------------- 強度課 ----------------
    Template("pal_hm_tempo", "quality", "Palladino 半馬功率節奏 2×11′", PAL, PAL_URL, lambda b: [
        *b.pal_warm(12, 2),
        b.rep(2, [b.t("work", 11 * 60, pw(0.91, 0.96), "11′ 91–96% CP"),
                  b.t("rest", 3 * 60, pw(0.65, 0.75), "輕鬆跑")], False, "2×11′"),
        b.cool(5, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", sub="z3"),
    Template("pal_near", "quality", "Palladino 近閾值 3×7′", PAL, PAL_URL, lambda b: [
        *b.pal_warm(8, 1),
        b.rep(3, [b.t("work", 7 * 60, pw(0.96, 1.02), "7′ 96–102% CP"),
                  b.t("rest", 3 * 60, OPEN, "走路或慢跑（0–75% CP）")], False, "3×7′"),
        b.cool(5, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", sub="z3"),
    Template("pal_supra", "quality", "Palladino 超閾值 4×4:30", PAL, PAL_URL, lambda b: [
        *b.pal_warm(8, 1),
        b.rep(4, [b.t("work", 270, pw(0.98, 1.04), "4:30 98–104% CP"),
                  b.t("rest", 165, OPEN, "走路或慢跑（0–75% CP）")], False, "4×4:30"),
        b.cool(6, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", sub="z4"),
    Template("pal_vo2", "quality", "Palladino 最大有氧功率 4×2:40", PAL, PAL_URL, lambda b: [
        *b.pal_warm(12, 2),
        b.rep(4, [b.t("work", 160, pw(1.01, 1.06), "2:40 101–106% CP"),
                  b.t("rest", 150, OPEN, "走路或慢跑（0–75% CP）")], False, "4×2:40"),
        b.cool(9, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", sub="z4"),
    Template("daniels_cruise", "quality", "Daniels T 巡航間歇 5×6′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(5, [b.t("work", 6 * 60, pace(0.99, 1.01), "T 配速"),
                  b.t("rest", 60, OPEN, "慢跑 1′")], False, "5×6′"),
        b.cool(10)], basis="pace", sub="z3", conv=TP),
    Template("pfitz_lt", "quality", "Pfitzinger 乳酸閾值節奏 25′", PFITZ, PFITZ_URL, lambda b: [
        b.warm(20), b.t("work", 25 * 60, hr(0.91, 1.00), "82–91% HRmax"), b.cool(15)], sub="z3", conv=HRMAX),
    Template("friel_cruise", "quality", "Friel 巡航間歇 4×8′", "Joe Friel《The Triathlete's Training Bible》", FRIEL_URL, lambda b: [
        b.warm(15),
        b.rep(4, [b.t("work", 8 * 60, hr(0.95, 1.02), "Friel 4–5a 區"),
                  b.t("rest", 2 * 60, hr(0.70, 0.85), "1 區恢復（工作的 1/4）")], False, "4×8′"),
        b.cool(10)], sub="z3"),
    Template("koop_tempo", "quality", "Koop TempoRun 3×12′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(3, [b.t("work", 12 * 60, hr(0.95, 1.00), "RPE 8–9"),
                  b.t("rest", 6 * 60, AET, "輕鬆跑（工休 2:1）")], False, "3×12′"),
        b.cool(10)], sub="z3", conv="來源只有 RPE：心率推估"),
    Template("canova_specific", "quality", "Canova 專項間歇 5×3 km（1 km 浮動）",
             "Arcelli & Canova《Marathon Training – A Scientific Approach》1999",
             "https://runningwritings.com/2023/06/canova-marathon-book.html", lambda b: [
        b.warm(15),
        b.rep(5, [b.d("work", 3000, pace(1.04, 1.08), "100–102% 馬拉松配速"),
                  b.d("rest", 1000, pace(1.12, 1.25), "浮動：85–95% 馬拉松配速")], False, "5×3 km"),
        b.cool(10)], basis="pace", sub="z3", conv="馬拉松配速 ≈ 閾值配速 × 1.06（推估）"),
    Template("canova_1k", "quality", "Canova 10×1000 m 強化", "Arcelli & Canova 1999（intensive block）",
             "https://runningwritings.com/2023/06/canova-marathon-book.html", lambda b: [
        b.warm(15),
        b.rep(10, [b.d("work", 1000, pace(0.96, 0.99), "111% 馬拉松配速（約 10K）"),
                   b.t("rest", 120, OPEN, "慢跑 2′")], False, "10×1 km"),
        b.cool(10)], basis="pace", sub="z3", conv="馬拉松配速 ≈ 閾值配速 × 1.06（推估）"),
    Template("seiler_4x8", "quality", "Seiler 4×8′", "Seiler et al. 2013, Scand J Med Sci Sports 23:74–83",
             "https://pubmed.ncbi.nlm.nih.gov/21812820/", lambda b: [
        b.warm(15),
        b.rep(4, [b.t("work", 8 * 60, hr(1.00, 1.05), "能撐住的最高強度（≈ 90% HRpeak）"),
                  b.t("rest", 2 * 60, OPEN, "2′ 恢復")], False, "4×8′"),
        b.cool(10)], sub="z4", src_kind="peer", conv="% LTHR 由 % HRpeak ÷ 0.9 換算（推估）"),
    Template("pfitz_vo2", "quality", "Pfitzinger VO2max 5×1000 m", PFITZ, PFITZ_URL, lambda b: [
        b.warm(20),
        b.rep(5, [b.d("work", 1000, pace(0.93, 0.96), "5K 配速"),
                  b.t("rest", 150, OPEN, "慢跑 2–4′")], False, "5×1 km"),
        b.cool(10)], basis="pace", sub="z4", conv="5K 配速 ≈ 閾值配速 × 0.93–0.96（推估）"),
    Template("daniels_i", "quality", "Daniels I 間歇 5×3′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(5, [b.t("work", 3 * 60, pace(0.92, 0.95), "I 配速"),
                  b.t("rest", 3 * 60, OPEN, "等長慢跑")], False, "5×3′"),
        b.cool(10)], basis="pace", sub="z5", conv="I 配速 ≈ 閾值配速 × 0.92–0.95（推估）"),
    Template("koop_vo2", "quality", "Koop RunningIntervals 6×3′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(6, [b.t("work", 3 * 60, hr(1.00, 1.06), "RPE 10"),
                  b.t("rest", 3 * 60, AET, "輕鬆跑 1:1")], False, "6×3′"),
        b.cool(10)], sub="z5", conv="來源只有 RPE：心率推估（3′ 的趟心率會落後）"),
    Template("billat_3030", "quality", "Billat 30-30 ×16", "Billat et al. 2000, Eur J Appl Physiol 81:188–196",
             "https://link.springer.com/article/10.1007/s004210050029", lambda b: [
        b.warm(15),
        b.rep(16, [b.t("work", 30, pace(0.86, 0.90), "30″ vVO2max"),
                   b.t("rest", 30, OPEN, "30″ 50% vVO2max 慢跑")], False, "30-30"),
        b.cool(10)], basis="pace", sub="z5", src_kind="peer", conv="vVO2max ≈ 閾值配速 × 0.86–0.90（推估）"),
    Template("ronnestad_3015", "quality", "Rønnestad 30/15 3×13", "Rønnestad et al. 2020, Scand J Med Sci Sports",
             "https://pubmed.ncbi.nlm.nih.gov/31977120/", lambda b: [
        b.warm(20, target=pw(0.65, 0.75)),
        b.rep(3, [b.rep(13, [b.t("work", 30, pw(1.10, 1.20), "30″"),
                             b.t("rest", 15, pw(0.50, 0.60), "15″")], False),
                  b.t("rest", 3 * 60, pw(0.55, 0.65), "組間 3′")], False, "3 組 30/15"),
        b.cool(10, target=pw(0.55, 0.70))], basis="power", sub="z5", src_kind="peer",
        conv="原研究是自行車功率：跑步的 % CP 推估"),
    Template("daniels_r", "quality", "Daniels R 8×300 m", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(8, [b.d("work", 300, pace(0.85, 0.89), "R 配速：快而放鬆"),
                  b.t("rest", 180, OPEN, "完全恢復（2–3 倍）")], False, "8×300 m"),
        b.cool(10)], basis="pace", sub="z5", conv="R 配速 ≈ 閾值配速 × 0.85–0.89（推估）"),

    # ---------------- 測試 ----------------
    Template("stryd_cp_3_12", "test", "Stryd 內建 CP 測試 3′＋12′", "Stryd 內建課表庫（Palladino）：3′／12′ CP test", PAL_URL, lambda b: [
        b.t("warm", 12 * 60, pw(0.70, 0.80), "EZ ≤ 80% CP"),
        b.rep(2, [b.t("warm", 60, pw(0.99, 1.01), "1′ 99–101% CP"), b.t("warm", 120, pw(0.70, 0.80), "輕鬆跑")], True, "2×1′"),
        b.t("warm", 30, pw(1.03, 1.08), "30″ 103–108% CP"), b.t("warm", 90, pw(0.70, 0.80), "輕鬆跑"),
        b.lap("rest", 120, "走路到呼吸恢復（≥ 2′），按圈開始"),
        b.t("work", 3 * 60, OPEN, "3′ 全力、均勻"),
        b.lap("rest", 30 * 60, "走→輕鬆跑 5–10′→走，≥ 30′；按圈開始"),
        b.t("work", 12 * 60, OPEN, "12′ 全力、均勻"),
        b.lap("cool", 10 * 60, "走路恢復後輕鬆跑到總量；按圈結束", pw(0.65, 0.80))], basis="power",
        note="全力段不設目標（Stryd 留給你填：模型功率 −1%～+5%）"),
    Template("pal_test3", "test", "Palladino 3′ 測試", PAL + "：3 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 3 * 60, OPEN, "3′ 全力：穩穩開始，最後才到極限"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, pw(0.70, 0.80), "EZ 跑到總量")], basis="power"),
    Template("pal_test10", "test", "Palladino 10′ 測試", PAL + "：10 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 10 * 60, OPEN, "10′ 全力：前 7′ 照 10′ 模型功率"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, pw(0.70, 0.80), "EZ 跑到總量")], basis="power"),
    Template("pal_test20", "test", "Palladino 20′ 測試", PAL + "：20 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 20 * 60, OPEN, "20′ 全力：前 15′ 照 20′ 模型功率"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, pw(0.70, 0.80), "EZ 跑到總量")], basis="power"),
    Template("stryd_9_3", "test", "Stryd 9′＋3′ CP 測試", "Stryd CP 測試（9/3 分鐘）；JSSM 2023 驗證",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC10499150/", lambda b: [
        b.t("warm", 10 * 60, pw(0.65, 0.75), "EZ"),
        b.rep(5, [b.d("other", 100, OPEN, "100 m 約 80% 力"), b.t("rest", 60, OPEN, "慢跑")], True, "5×100 m"),
        b.t("work", 9 * 60, OPEN, "9′ 全力、均勻"),
        b.t("rest", 30 * 60, pw(0.50, 0.65), "走路或輕鬆跑 30′"),
        b.t("work", 3 * 60, OPEN, "3′ 全力、均勻"), b.t("cool", 10 * 60, pw(0.55, 0.70), "緩和")],
        basis="power", src_kind="peer"),
    Template("friel_lthr30", "test", "Friel 30′ 閾值心率測試", "Joe Friel「Quick Guide to Setting Zones」TrainingPeaks", FRIEL_URL, lambda b: [
        b.warm(15), b.t("work", 10 * 60, OPEN, "獨自全力 30′ 的前 10′：第 10′ 按圈"),
        b.t("work", 20 * 60, OPEN, "後 20′：平均心率＝LTHR"), b.cool(10)],
        note="只看全力，不設目標；手腕光學心率的平均誤差比胸帶大"),
    Template("ua_aet_drift", "test", "AeT 心率飄移測試 60′", "Steve House, Uphill Athlete「Heart Rate Drift」",
             "https://uphillathlete.com/aerobic-training/heart-rate-drift/", lambda b: [
        b.warm(15, "慢慢加到心率穩定"),
        b.t("work", 60 * 60, AET, "固定對話配速、平路；前後半心率比"),
        b.cool(10)],
        note="飄移 3.5–5% 就是 AeT；< 3.5% 下次 +5 bpm、> 5% 降低（來源要胸帶，手腕心率只看趨勢）"),
    Template("xu_e_drift", "test", "徐國峰 90′ E 配速飄移", "徐國峰 部落格 2015-10-14",
             "http://rocky549.blogspot.com/2015/10/e.html", lambda b: [
        b.warm(10), b.t("work", 80 * 60, pace(1.18, 1.29), "固定 E 配速；比第 10、90 分心率"), b.cool(10)],
        basis="pace", conv="E 配速 ≈ 閾值配速 × 1.18–1.29（Friel 2 區配速，推估）"),

    # ---------------- 越野跑 ----------------
    Template("dsw_classic", "trail", "登山王經典 4×7′ 爬升", "江晏慶「如何提升越野跑的爬升能力－登山王課表」Garmin 台灣 2021-01-27",
             GARMIN_DSW, lambda b: [
        b.warm(12, "階梯步道輕鬆跑／走"),
        b.rep(4, [b.t("work", 7 * 60, hr(0.95, 1.02), "上坡 9 成力（快崩但不爆）"),
                  b.t("rest", 7 * 60, OPEN, "慢慢走下來")], False, "4×7′ 上坡"),
        b.t("cool", 5 * 60, OPEN, "收操、補給")], conv="來源是 RPE：心率推估"),
    Template("dsw_endurance", "trail", "登山王耐力型 6×快走上坡", "江晏慶 登山王課表（耐力變化）Garmin 台灣 2021", GARMIN_DSW, lambda b: [
        b.warm(12, "輕鬆跑／走"),
        b.rep(6, [b.t("work", 7 * 60, hr(0.88, 0.93), "快走上坡"),
                  b.t("rest", 5 * 60, AET, "慢跑下來")], False, "6×上坡"),
        b.t("cool", 5 * 60, OPEN, "收操")], conv="來源是 RPE：心率推估"),
    Template("ua_hill_sprints", "trail", "陡坡衝刺 8×10″", UA + "《Training for the Uphill Athlete》2019",
             "https://uphillathlete.com/", lambda b: [
        b.warm(15),
        b.rep(8, [b.t("work", 10, OPEN, "≥ 20% 陡坡 10″ 全力"), b.t("rest", 3 * 60, OPEN, "走到完全恢復")], False, "8×10″"),
        b.cool(10)], note="全力衝刺：心率、功率都不當目標"),
    Template("koop_uphill", "trail", "Koop 上坡 TempoRun 3×12′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(3, [b.t("work", 12 * 60, hr(0.95, 1.00), "上坡 RPE 8–9"),
                  b.t("rest", 6 * 60, AET, "下坡或平路輕鬆")], False, "3×12′ 上坡"),
        b.cool(10)], conv="來源只有 RPE：心率推估"),
    Template("long_climb", "trail", "長爬坡有氧 90′", UA + " Zone 2（AeT −10%～AeT）",
             "https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/", lambda b: [
        b.warm(15), b.t("work", 90 * 60, AET, "持續爬升，跑走混合，心率 ≤ AeT"),
        b.t("cool", 15 * 60, OPEN, "輕鬆下山")], src_kind="推估"),
    Template("steep_5", "trail", f"陡坡健走 {_S5['grade']:g}%（模擬背 5% 體重）", STEEP_SRC, STEEP_URL, _S5B,
             conv=STEEP_CONV, note="不背包；百岳前的專項期，第 1 階段"),
    Template("steep_10", "trail", f"陡坡健走 {_S10['grade']:g}%（模擬背 10% 體重）", STEEP_SRC, STEEP_URL, _S10B,
             conv=STEEP_CONV, note="不背包；第 2 階段"),
    Template("steep_15", "trail", f"陡坡健走 {_S15['grade']:g}%（模擬背 15% 體重）", STEEP_SRC, STEEP_URL, _S15B,
             conv=STEEP_CONV, note="不背包；行程背包約體重 15% 時"),
    Template("downhill_ecc", "trail", "下坡離心預適應 25′",
             "Assumpção et al. 2020 Sci Rep；Bontemps et al. 2020 Sports Med；Koop（TrainRight）",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC7606541/", lambda b: [
        b.warm(10, "平路暖身"), b.t("work", 25 * 60, AET, "−10～−15% 下坡，輕鬆到中等"), b.cool(10, "平路緩和")],
        src_kind="peer", note="賽前 ≥ 2 週做；效果約 9 週，之後 2–3 天輕鬆；下坡功率不準，看心率"),
]

BY_KEY = {t.key: t for t in TEMPLATES}


def items_of(t: Template) -> list:
    return t.build(B())


def main_of(items: list) -> list:
    """Between the warm-up and the cool-down (the 只換主課 insert)."""
    a, z = 0, len(items)
    while a < z and items[a]["kind"] == "warm":
        a += 1
    while z > a and items[z - 1]["kind"] == "cool":
        z -= 1
    return items[a:z]


BASIS_LABEL = {"power": "功率", "hr": "心率", "pace": "配速"}


def row(t: Template) -> dict:
    full = items_of(t)
    return {"key": f"lib:{t.key}", "label": t.title, "title": t.title, "src": t.src, "url": t.url,
            "src_kind": t.src_kind, "conv": t.conv, "note": t.note, "items": main_of(full) or full, "full": full,
            "equiv": None, "sub": t.sub, "basis": t.basis, "basis_label": BASIS_LABEL[t.basis]}
