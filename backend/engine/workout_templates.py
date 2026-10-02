"""
The editor's 插入範本 library (docs/research/workout-templates.md): published workouts
from named coaches / papers, each with a warm-up, a source, and targets in BOTH power
(× CP) and heart rate (× LTHR), so 「目標用：心率／功率」 switches every step.

Categories follow the session's 類型: easy (輕鬆跑 / 長時間), quality (強度課, split by
the main set's band middle into 三區 88–101 % CP / 四區 101–106 % / 五區 ≥ 106 % —
Palladino's running power zones 3 / 4 / 5, the same steps as the editor chart's colours),
test, trail (越野跑).

Targets
  band(lo, hi, hrp)   auto band: power lo–hi × CP; heart rate hrp × LTHR. When a source
                      gives only one of them, the other is the Palladino-zone ↔ Friel-zone
                      mapping HRP below (推估, the template's `conv` says which).
  easy(plo, phi)      auto easy: power plo–phi × CP; heart rate ≤ AeT (the app's easy rule)
  open                no target (all-out test bouts, strides, walk recoveries)
Lap-button steps carry `est` (the protocol's minimum, s) so the total can be estimated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from backend.engine import interval_library as IL
from backend.engine import workout_steps as WS

# Palladino power zone → Friel run HR zone (推估; docs/research/workout-templates.md §2)
HRP = [(0.80, (0.70, 0.85)), (0.88, (0.85, 0.90)), (0.95, (0.90, 0.95)), (1.01, (0.95, 1.00)),
       (1.06, (1.00, 1.03)), (9.99, (1.03, 1.06))]

SUBS = [{"id": "z3", "label": "三區", "tip": "主課 88–101% CP（Palladino 3 區：閾值）"},
        {"id": "z4", "label": "四區", "tip": "主課 101–106% CP（Palladino 4 區：超閾值）"},
        {"id": "z5", "label": "五區", "tip": "主課 ≥ 106% CP（Palladino 5 區以上：VO2max、短間歇）"}]
CATS = [{"id": "easy", "label": "輕鬆跑"}, {"id": "quality", "label": "強度課", "subs": SUBS},
        {"id": "test", "label": "測試"}, {"id": "trail", "label": "越野跑"}]


def hrp_of(mid: float) -> tuple:
    return next(h for top, h in HRP if mid < top)


def sub_of(mid: float) -> str:
    """三區 / 四區 / 五區 by the band middle (the chart's colour steps 3 / 4 / 5)."""
    return "z5" if mid >= 1.06 else "z4" if mid >= 1.01 else "z3"


def band(lo: float, hi: float, hrp: Optional[tuple] = None) -> dict:
    m = (lo + hi) / 2
    cls = next((c for c, (a, b) in IL.CLASS_RANGE.items() if a <= m < b), "")
    h = hrp or hrp_of(m)
    return {"type": "auto", "intent": "band", "lo": lo, "hi": hi, "cls": cls, "hrp": [h[0], h[1]]}


def easy(plo: float = 0.65, phi: float = 0.80) -> dict:
    return {"type": "auto", "intent": "easy", "plo": plo, "phi": phi}


OPEN = {"type": "auto", "intent": "open"}


class B:
    """Step builders bound to one id counter."""

    def __init__(self):
        self.ids = WS._Ids("w")

    def t(self, kind, sec, target=None, note=""):
        return WS.step(self.ids, kind, int(sec), target or OPEN, note)

    def d(self, kind, meters, target=None, note=""):
        return WS.step(self.ids, kind, {"type": "distance", "value": int(meters)}, target or OPEN, note)

    def lap(self, kind, est, note, target=None):
        return WS.step(self.ids, kind, {"type": "open", "est": int(est)}, target or OPEN, note)

    def rep(self, n, items, last_rest=True, note=""):
        return WS.rep(self.ids, n, items, last_rest, note)

    # the warm-ups the sources write out
    def pal_warm(self, ez_min=12, after_min=2):
        """Palladino / Stryd: EZ ≤ 80 % CP, 2 × 1′ @ 97–103 % (2′ easy), then easy."""
        return [self.t("warm", ez_min * 60, easy(0.70, 0.80), "EZ ≤ 80% CP"),
                self.rep(2, [self.t("warm", 60, band(0.97, 1.03), "1′ 97–103% CP"),
                             self.t("warm", 120, easy(0.70, 0.80), "輕鬆跑")], True, "2×1′"),
                self.t("warm", after_min * 60, easy(0.70, 0.80), "輕鬆跑")]

    def pal_test_warm(self):
        """Palladino's test warm-up (3 / 10 / 20 min tests): 12′ EZ, 1′ 97–103 %, 30″
        102–107 %, 10″ acceleration, each with easy recovery, then 3′ walk."""
        return [self.t("warm", 12 * 60, easy(0.70, 0.80), "EZ ≤ 80% CP"),
                self.t("warm", 60, band(0.97, 1.03), "1′ 97–103% CP"), self.t("warm", 120, easy(0.70, 0.80), "輕鬆跑"),
                self.t("warm", 30, band(1.02, 1.07), "30″ 102–107% CP"), self.t("warm", 90, easy(0.70, 0.80), "輕鬆跑"),
                self.t("other", 10, OPEN, "10″ 漸進加速，放鬆不硬拚"), self.t("warm", 110, easy(0.70, 0.80), "輕鬆跑"),
                self.t("rest", 180, OPEN, "停下走路，呼吸完全恢復")]

    def warm(self, minutes=15, note="暖身"):
        return self.t("warm", minutes * 60, easy(0.65, 0.75), note)

    def cool(self, minutes=10, note="緩和"):
        return self.t("cool", minutes * 60, easy(0.55, 0.70), note)


@dataclass(frozen=True)
class Template:
    key: str
    cat: str                 # easy | quality | test | trail
    title: str
    src: str                 # author, work, year
    url: str
    build: Callable[[B], list]
    src_kind: str = "coach"  # peer | coach | 推估 (the structure itself)
    conv: str = ""           # which target is converted (推估)
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
HRC = "心率由 Palladino 功率區對 Friel 心率區換算（推估）"
PWC = "功率由心率區換算（推估）"
GARMIN_DSW = "https://www.garmin.com/zh-TW/blog/running/the-climbing-ability-of-trail-running/"

TEMPLATES: list[Template] = [
    # ---------------- 輕鬆跑 ----------------
    Template("pal_ez", "easy", "Palladino EZ 輕鬆跑＋3×10″ 加速", PAL, PAL_URL, lambda b: [
        b.t("warm", 24 * 60, easy(0.70, 0.80), "EZ ≤ 80% CP"),
        b.rep(3, [b.t("other", 10, OPEN, "10″ 漸進加速"), b.t("rest", 110, easy(0.65, 0.80), "輕鬆跑")], True, "加速 3×10″"),
        b.t("cool", 5 * 60, easy(0.70, 0.80), "EZ")], conv="心率用 ≤ AeT（app 的輕鬆規則）"),
    Template("pfitz_recovery", "easy", "Pfitzinger 恢復跑 30′", PFITZ, PFITZ_URL, lambda b: [
        b.t("warm", 5 * 60, easy(0.55, 0.65), "很慢開始"),
        b.t("work", 25 * 60, band(0.60, 0.75, (0.75, 0.84)), "恢復跑 < 76% HRmax"),
        b.t("cool", 5 * 60, OPEN, "走路")], conv="% LTHR 由 % HRmax ÷ 0.9 換算；功率推估"),
    Template("pfitz_ga", "easy", "Pfitzinger 有氧耐力跑 60′", PFITZ, PFITZ_URL, lambda b: [
        b.warm(10), b.t("work", 45 * 60, band(0.75, 0.85, (0.78, 0.89)), "70–81% HRmax"), b.cool(5)],
        conv="% LTHR 由 % HRmax ÷ 0.9 換算；功率推估"),
    Template("pfitz_long", "easy", "Pfitzinger 長跑 2 小時", PFITZ, PFITZ_URL, lambda b: [
        b.warm(15), b.t("work", 95 * 60, band(0.78, 0.87, (0.82, 0.90)), "74–84% HRmax"), b.cool(10)],
        conv="% LTHR 由 % HRmax ÷ 0.9 換算（上緣壓在 90%）；功率推估"),
    Template("daniels_e", "easy", "Daniels E 輕鬆跑 50′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(10), b.t("work", 35 * 60, band(0.65, 0.80, (0.72, 0.88)), "E：65–79% HRmax"), b.cool(5)],
        conv="% LTHR 由 % HRmax ÷ 0.9 換算；功率推估"),
    Template("xu_e90", "easy", "徐國峰 E 強度 90′（看心率飄移）",
             "徐國峰 部落格 2015-10-14《E 配速心率飄移》；《全方位的馬拉松科學化訓練》2015",
             "http://rocky549.blogspot.com/2015/10/e.html", lambda b: [
        b.warm(10), b.t("work", 80 * 60, band(0.65, 0.80, (0.72, 0.88)), "平路 E；記第 10、90 分心率"), b.cool(10)],
        conv="% LTHR 由 % HRmax ÷ 0.9 換算；功率推估", note="飄移 < 10% 有氧很好、< 5% 國手級（徐國峰）"),
    Template("ua_z2", "easy", "Uphill Athlete 有氧基礎 Z2 60′", UA + "「Zone 2 Heart Rate Training」2026",
             "https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/", lambda b: [
        b.t("warm", 10 * 60, easy(0.65, 0.75), "Z1：AeT −20%～−10%"),
        b.t("work", 60 * 60, easy(0.75, 0.85), "Z2：AeT −10%～AeT"), b.cool(10)],
        conv="心率 ≤ AeT（來源以 AeT 定區）；功率推估"),
    Template("seiler_z1", "easy", "Seiler 兩極化低強度長課 100′",
             "Seiler 2010, IJSPP 5(3):276–291（1 區 60–72% HRmax）",
             "https://www.researchgate.net/publication/46403553_What_is_Best_Practice_for_Training_Intensity_and_Duration_Distribution_in_Endurance_Athletes",
             lambda b: [b.warm(10), b.t("work", 80 * 60, band(0.65, 0.78, (0.67, 0.80)), "1 區：60–72% HRmax"), b.cool(10)],
             src_kind="peer", conv="% LTHR 由 % HRmax ÷ 0.9 換算；功率推估"),

    # ---------------- 強度課 ----------------
    Template("pal_hm_tempo", "quality", "Palladino 半馬功率節奏 2×11′", PAL, PAL_URL, lambda b: [
        *b.pal_warm(12, 2),
        b.rep(2, [b.t("work", 11 * 60, band(0.91, 0.96), "11′ 91–96% CP"),
                  b.t("rest", 3 * 60, easy(0.65, 0.75), "輕鬆跑")], False, "2×11′"),
        b.cool(5, "EZ 跑到總量")], conv=HRC),
    Template("pal_near", "quality", "Palladino 近閾值 3×7′", PAL, PAL_URL, lambda b: [
        *b.pal_warm(8, 1),
        b.rep(3, [b.t("work", 7 * 60, band(0.96, 1.02), "7′ 96–102% CP"),
                  b.t("rest", 3 * 60, OPEN, "走路或慢跑")], False, "3×7′"),
        b.cool(5, "EZ 跑到總量")], conv=HRC),
    Template("pal_supra", "quality", "Palladino 超閾值 4×4:30", PAL, PAL_URL, lambda b: [
        *b.pal_warm(8, 1),
        b.rep(4, [b.t("work", 270, band(0.98, 1.04), "4:30 98–104% CP"),
                  b.t("rest", 165, OPEN, "走路或慢跑")], False, "4×4:30"),
        b.cool(6, "EZ 跑到總量")], conv=HRC),
    Template("pal_vo2", "quality", "Palladino 最大有氧功率 4×2:40", PAL, PAL_URL, lambda b: [
        *b.pal_warm(12, 2),
        b.rep(4, [b.t("work", 160, band(1.01, 1.06), "2:40 101–106% CP"),
                  b.t("rest", 150, OPEN, "走路或慢跑")], False, "4×2:40"),
        b.cool(9, "EZ 跑到總量")], conv=HRC),
    Template("daniels_cruise", "quality", "Daniels T 巡航間歇 5×6′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(5, [b.t("work", 6 * 60, band(0.92, 1.00, (0.95, 1.00)), "T：88–92% HRmax"),
                  b.t("rest", 60, easy(0.55, 0.65), "慢跑 1′")], False, "5×6′"),
        b.cool(10)], conv="% LTHR 由 % HRmax ÷ 0.9 換算（上緣壓在 100%）；功率推估"),
    Template("pfitz_lt", "quality", "Pfitzinger 乳酸閾值節奏 25′", PFITZ, PFITZ_URL, lambda b: [
        b.warm(20), b.t("work", 25 * 60, band(0.90, 0.97, (0.91, 1.00)), "82–91% HRmax"), b.cool(15)],
        conv="% LTHR 由 % HRmax ÷ 0.9 換算；功率推估"),
    Template("friel_cruise", "quality", "Friel 巡航間歇 4×8′", "Joe Friel《The Triathlete's Training Bible》", FRIEL_URL, lambda b: [
        b.warm(15),
        b.rep(4, [b.t("work", 8 * 60, band(0.92, 1.00, (0.95, 1.02)), "Friel 4–5a 區"),
                  b.t("rest", 2 * 60, easy(0.55, 0.65), "1 區恢復（工作的 1/4）")], False, "4×8′"),
        b.cool(10)], conv=PWC),
    Template("koop_tempo", "quality", "Koop TempoRun 3×12′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(3, [b.t("work", 12 * 60, band(0.90, 0.98, (0.95, 1.00)), "RPE 8–9"),
                  b.t("rest", 6 * 60, easy(0.55, 0.70), "輕鬆跑（工休 2:1）")], False, "3×12′"),
        b.cool(10)], conv="來源只有 RPE：功率、心率都推估"),
    Template("canova_specific", "quality", "Canova 專項間歇 5×3 km（1 km 浮動）",
             "Arcelli & Canova《Marathon Training – A Scientific Approach》1999",
             "https://runningwritings.com/2023/06/canova-marathon-book.html", lambda b: [
        b.warm(15),
        b.rep(5, [b.d("work", 3000, band(0.86, 0.92, (0.90, 0.95)), "100–102% 馬拉松配速"),
                  b.d("rest", 1000, band(0.75, 0.82, (0.82, 0.88)), "浮動：85–95% 馬拉松配速")], False, "5×3 km"),
        b.cool(10)], conv="來源是配速：功率、心率都推估"),
    Template("canova_1k", "quality", "Canova 10×1000 m 強化", "Arcelli & Canova 1999（intensive block）",
             "https://runningwritings.com/2023/06/canova-marathon-book.html", lambda b: [
        b.warm(15),
        b.rep(10, [b.d("work", 1000, band(0.98, 1.03), "111% 馬拉松配速（約 10K）"),
                   b.t("rest", 120, easy(0.55, 0.65), "慢跑 2′")], False, "10×1 km"),
        b.cool(10)], conv="來源是配速：功率推估；" + HRC),
    Template("seiler_4x8", "quality", "Seiler 4×8′", "Seiler et al. 2013, Scand J Med Sci Sports 23:74–83",
             "https://pubmed.ncbi.nlm.nih.gov/21812820/", lambda b: [
        b.warm(15),
        b.rep(4, [b.t("work", 8 * 60, band(1.02, 1.08, (1.00, 1.05)), "能撐住的最高強度（≈ 90% HRpeak）"),
                  b.t("rest", 2 * 60, OPEN, "2′ 恢復")], False, "4×8′"),
        b.cool(10)], src_kind="peer", conv="% LTHR 由 % HRpeak ÷ 0.9 換算；功率推估"),
    Template("pfitz_vo2", "quality", "Pfitzinger VO2max 5×1000 m", PFITZ, PFITZ_URL, lambda b: [
        b.warm(20),
        b.rep(5, [b.d("work", 1000, band(1.03, 1.08, (1.00, 1.05)), "5K 配速：93–98% HRmax"),
                  b.t("rest", 150, easy(0.50, 0.65), "慢跑 2–4′")], False, "5×1 km"),
        b.cool(10)], conv="心率在 2–4′ 的趟裡追不上；% LTHR、功率都推估"),
    Template("daniels_i", "quality", "Daniels I 間歇 5×3′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(5, [b.t("work", 3 * 60, band(1.05, 1.12), "I：95–100% VO2max"),
                  b.t("rest", 3 * 60, easy(0.50, 0.65), "等長慢跑")], False, "5×3′"),
        b.cool(10)], conv=HRC + "；功率推估"),
    Template("koop_vo2", "quality", "Koop RunningIntervals 6×3′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(6, [b.t("work", 3 * 60, band(1.05, 1.15), "RPE 10"),
                  b.t("rest", 3 * 60, easy(0.50, 0.65), "輕鬆跑 1:1")], False, "6×3′"),
        b.cool(10)], conv="來源只有 RPE：功率、心率都推估"),
    Template("billat_3030", "quality", "Billat 30-30 ×16（每趟 < 2 分）", "Billat et al. 2000, Eur J Appl Physiol 81:188–196",
             "https://link.springer.com/article/10.1007/s004210050029", lambda b: [
        b.warm(15),
        b.rep(16, [b.t("work", 30, band(1.10, 1.20), "30″ vVO2max"),
                   b.t("rest", 30, band(0.55, 0.65), "30″ 50% vVO2max")], False, "30-30"),
        b.cool(10)], src_kind="peer", conv="短趟心率追不上，以功率為準；兩者都推估"),
    Template("ronnestad_3015", "quality", "Rønnestad 30/15 3×13（每趟 < 2 分）", "Rønnestad et al. 2020, Scand J Med Sci Sports",
             "https://pubmed.ncbi.nlm.nih.gov/31977120/", lambda b: [
        b.warm(20),
        b.rep(3, [b.rep(13, [b.t("work", 30, band(1.10, 1.20), "30″"),
                             b.t("rest", 15, band(0.50, 0.60), "15″")], False),
                  b.t("rest", 3 * 60, easy(0.55, 0.65), "組間 3′")], False, "3 組 30/15"),
        b.cool(10)], src_kind="peer", conv="原研究是自行車；跑步版功率、心率都推估"),
    Template("daniels_r", "quality", "Daniels R 8×300 m", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(8, [b.d("work", 300, band(1.15, 1.30), "R：快而放鬆"),
                  b.t("rest", 180, OPEN, "完全恢復（2–3 倍）")], False, "8×300 m"),
        b.cool(10)], conv="短趟心率追不上；功率、心率都推估"),

    # ---------------- 測試 ----------------
    Template("stryd_cp_3_12", "test", "Stryd 內建 CP 測試 3′＋12′", "Stryd 內建課表庫（Palladino）：3′／12′ CP test", PAL_URL, lambda b: [
        b.t("warm", 12 * 60, easy(0.70, 0.80), "EZ ≤ 80% CP"),
        b.rep(2, [b.t("warm", 60, band(0.99, 1.01), "1′ 99–101% CP"), b.t("warm", 120, easy(0.70, 0.80), "輕鬆跑")], True, "2×1′"),
        b.t("warm", 30, band(1.03, 1.08), "30″ 103–108% CP"), b.t("warm", 90, easy(0.70, 0.80), "輕鬆跑"),
        b.lap("rest", 120, "走路到呼吸恢復（≥ 2′），按圈開始"),
        b.t("work", 3 * 60, OPEN, "3′ 全力、均勻"),
        b.lap("rest", 30 * 60, "走→輕鬆跑 5–10′→走，≥ 30′；按圈開始"),
        b.t("work", 12 * 60, OPEN, "12′ 全力、均勻"),
        b.lap("cool", 10 * 60, "走路恢復後輕鬆跑到總量；按圈結束", easy(0.65, 0.80))],
        conv="全力段不設目標（Stryd 留給你填：模型功率 −1%～+5%）"),
    Template("pal_test3", "test", "Palladino 3′ 測試", PAL + "：3 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 3 * 60, OPEN, "3′ 全力：穩穩開始，最後才到極限"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, easy(0.70, 0.80), "EZ 跑到總量")]),
    Template("pal_test10", "test", "Palladino 10′ 測試", PAL + "：10 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 10 * 60, OPEN, "10′ 全力：前 7′ 照 10′ 模型功率"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, easy(0.70, 0.80), "EZ 跑到總量")]),
    Template("pal_test20", "test", "Palladino 20′ 測試", PAL + "：20 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 20 * 60, OPEN, "20′ 全力：前 15′ 照 20′ 模型功率"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, easy(0.70, 0.80), "EZ 跑到總量")]),
    Template("stryd_9_3", "test", "Stryd 9′＋3′ CP 測試", "Stryd CP 測試（9/3 分鐘）；JSSM 2023 驗證",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC10499150/", lambda b: [
        b.warm(10), b.rep(5, [b.d("other", 100, OPEN, "100 m 約 80% 力"), b.t("rest", 60, OPEN, "慢跑")], True, "5×100 m"),
        b.t("work", 9 * 60, OPEN, "9′ 全力、均勻"),
        b.t("rest", 30 * 60, easy(0.50, 0.65), "走路或輕鬆跑 30′"),
        b.t("work", 3 * 60, OPEN, "3′ 全力、均勻"), b.cool(10)], src_kind="peer"),
    Template("friel_lthr30", "test", "Friel 30′ 閾值心率測試", "Joe Friel「Quick Guide to Setting Zones」TrainingPeaks", FRIEL_URL, lambda b: [
        b.warm(15), b.t("work", 10 * 60, OPEN, "獨自全力 30′ 的前 10′：第 10′ 按圈"),
        b.t("work", 20 * 60, OPEN, "後 20′：平均心率＝LTHR"), b.cool(10)],
        note="只看全力，不設目標；手腕光學心率的平均誤差比胸帶大"),
    Template("ua_aet_drift", "test", "AeT 心率飄移測試 60′", "Steve House, Uphill Athlete「Heart Rate Drift」",
             "https://uphillathlete.com/aerobic-training/heart-rate-drift/", lambda b: [
        b.t("warm", 15 * 60, easy(0.65, 0.75), "慢慢加到心率穩定"),
        b.t("work", 60 * 60, easy(0.75, 0.80), "固定對話配速、平路；前後半心率比"),
        b.cool(10)], conv="心率 ≤ AeT；功率推估",
        note="飄移 3.5–5% 就是 AeT；< 3.5% 下次 +5 bpm、> 5% 降低（來源要胸帶，手腕心率只看趨勢）"),
    Template("xu_e_drift", "test", "徐國峰 90′ E 配速飄移", "徐國峰 部落格 2015-10-14",
             "http://rocky549.blogspot.com/2015/10/e.html", lambda b: [
        b.warm(10), b.t("work", 80 * 60, band(0.65, 0.80, (0.72, 0.88)), "固定 E 配速；比第 10、90 分心率"), b.cool(10)],
        conv="% LTHR 由 % HRmax ÷ 0.9 換算；功率推估"),

    # ---------------- 越野跑 ----------------
    Template("dsw_classic", "trail", "登山王經典 4×7′ 爬升", "江晏慶「如何提升越野跑的爬升能力－登山王課表」Garmin 台灣 2021-01-27",
             GARMIN_DSW, lambda b: [
        b.t("warm", 12 * 60, easy(0.65, 0.75), "階梯步道輕鬆跑／走"),
        b.rep(4, [b.t("work", 7 * 60, band(0.95, 1.02, (0.95, 1.02)), "上坡 9 成力（快崩但不爆）"),
                  b.t("rest", 7 * 60, OPEN, "慢慢走下來")], False, "4×7′ 上坡"),
        b.t("cool", 5 * 60, OPEN, "收操、補給")], conv="來源是 RPE：功率、心率都推估"),
    Template("dsw_endurance", "trail", "登山王耐力型 6×快走上坡", "江晏慶 登山王課表（耐力變化）Garmin 台灣 2021", GARMIN_DSW, lambda b: [
        b.t("warm", 12 * 60, easy(0.65, 0.75), "輕鬆跑／走"),
        b.rep(6, [b.t("work", 7 * 60, band(0.85, 0.92, (0.88, 0.93)), "快走上坡"),
                  b.t("rest", 5 * 60, easy(0.55, 0.70), "慢跑下來")], False, "6×上坡"),
        b.t("cool", 5 * 60, OPEN, "收操")], conv="來源是 RPE：功率、心率都推估"),
    Template("ua_hill_sprints", "trail", "陡坡衝刺 8×10″", UA + "《Training for the Uphill Athlete》2019",
             "https://uphillathlete.com/", lambda b: [
        b.warm(15),
        b.rep(8, [b.t("work", 10, OPEN, "≥ 20% 陡坡 10″ 全力"), b.t("rest", 3 * 60, OPEN, "走到完全恢復")], False, "8×10″"),
        b.cool(10)], note="全力衝刺：心率、功率都不當目標"),
    Template("koop_uphill", "trail", "Koop 上坡 TempoRun 3×12′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(3, [b.t("work", 12 * 60, band(0.90, 1.00, (0.95, 1.00)), "上坡 RPE 8–9"),
                  b.t("rest", 6 * 60, easy(0.55, 0.70), "下坡或平路輕鬆")], False, "3×12′ 上坡"),
        b.cool(10)], conv="來源只有 RPE：功率、心率都推估"),
    Template("long_climb", "trail", "長爬坡有氧 90′", UA + " Zone 2（AeT −10%～AeT）",
             "https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/", lambda b: [
        b.warm(15), b.t("work", 90 * 60, easy(0.80, 0.88), "持續爬升，跑走混合，心率 ≤ AeT"),
        b.t("cool", 15 * 60, OPEN, "輕鬆下山")], src_kind="推估", conv="心率 ≤ AeT；上坡功率常比平路同心率高，功率推估"),
    Template("ua_me", "trail", "負重爬坡肌耐力 45′", UA + "「Muscular Endurance Training」2016",
             "https://uphillathlete.com/aerobic-training/vertical-beast-mode-what-is-muscular-endurance-why-it-is-important-for-any-alpinist-or-mountaineer-and-how-do-you-train-it/",
             lambda b: [b.warm(15), b.t("work", 45 * 60, easy(0.70, 0.85), "30–100% 坡，背 10–30% 體重的水；呼吸可對話"),
                        b.t("cool", 15 * 60, OPEN, "倒掉水、空手下山")],
             conv="心率 ≤ AeT；負重時功率不準（推估）", note="一週一次，6–10 次；重量和爬升一次只加一個"),
    Template("downhill_ecc", "trail", "下坡離心預適應 25′",
             "Assumpção et al. 2020 Sci Rep；Bontemps et al. 2020 Sports Med；Koop（TrainRight）",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC7606541/", lambda b: [
        b.t("warm", 10 * 60, easy(0.65, 0.75), "平路暖身"),
        b.t("work", 25 * 60, easy(0.60, 0.75), "−10～−15% 下坡，輕鬆到中等"), b.cool(10, "平路緩和")],
        src_kind="peer", conv="心率 ≤ AeT；下坡功率不準（推估）",
        note="賽前 ≥ 2 週做；效果約 9 週，之後 2–3 天輕鬆"),
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


def work_mid(items: list) -> Optional[float]:
    """The middle (× CP) of the main set: the work steps' band middles, time-weighted."""
    tot = acc = 0.0
    for row in WS.flat(items):
        st = row["st"]
        tg = st.get("target") or {}
        if st["kind"] == "work" and tg.get("intent") == "band":
            w = st["dur"].get("value") or 60
            acc += (tg["lo"] + tg["hi"]) / 2 * w
            tot += w
    return acc / tot if tot else None


def row(t: Template) -> dict:
    full = items_of(t)
    m = work_mid(full)
    return {"key": f"lib:{t.key}", "label": t.title, "title": t.title, "src": t.src, "url": t.url,
            "src_kind": t.src_kind, "conv": t.conv, "note": t.note, "items": main_of(full) or full, "full": full,
            "equiv": None, "sub": sub_of(m) if t.cat == "quality" and m else None}
