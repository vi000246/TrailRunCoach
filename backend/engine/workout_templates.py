"""
The editor's 插入範本 library (docs/research/workout-templates.md): published workouts
from named coaches / papers, each with a warm-up and a source. Every step carries its
SOURCE'S OWN basis (no top-level switch): Palladino and the Stryd tests → power (% CP);
Friel, Uphill Athlete, Pfitzinger, Seiler, 徐國峰's E run, the trail sessions → heart
rate (% LTHR, or ≤ AeT); Daniels' T / I / R, Canova, Billat → pace (× threshold pace).
Where the source's own number isn't one the app has (% HRmax, VO2max pace, RPE) it is
converted and the template's `conv` says so (推估).

Categories follow the session's 類型: easy (輕鬆跑 / LSD), quality (強度課, split by
family_of() into 有氧間歇 / VO2max 間歇 / 速度 — intensity first, then rep length; SP-32,
docs/research/coach-schools-zones-periodization.md R1), test, trail (越野跑, split by the
template's `trail` into 結構化爬升 / 技術地形 / 下坡技術／離心 — SP-62). Every template carries a
one-line `purpose` (訓練目的) from the same report's Finding 7.

Targets
  pw(lo, hi)     power × CP          hr(lo, hi)   heart rate × LTHR
  AET            HR ≤ the easy cap   pace(lo, hi) × threshold pace (bigger = slower)
  OPEN           no target (all-out test bouts, strides, walk recoveries)
  rpe(lo, hi, up, down)  Borg CR-10 + m of climb / descent, no HR / power target (技術地形／下坡:
                 footing, not the heart, sets the pace; the watch gets time + the RPE in the name)
Lap-button steps carry `est` (the protocol's minimum, s) so the total can be estimated.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Callable, Optional

from backend.engine import workout_steps as WS
from backend.i18n import N_, _



def pw(lo: float, hi: float) -> dict:
    return {"type": "power", "mode": "pct", "lo": lo, "hi": hi}


def hr(lo: float, hi: float) -> dict:
    return {"type": "hr", "mode": "pct", "lo": lo, "hi": hi}


def pace(lo: float, hi: float) -> dict:
    return {"type": "pace", "mode": "pct", "lo": lo, "hi": hi}


def rpe(lo: int, hi: int, up: Optional[int] = None, down: Optional[int] = None) -> dict:
    t = {"type": "rpe", "lo": lo, "hi": hi}
    if up:
        t["up"] = up
    if down:
        t["down"] = down
    return t


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
    basis: str = "hr"        # the source's own basis: power | hr | pace | rpe (技術地形／下坡)
    src_kind: str = "coach"  # peer | coach | 推估 (the structure itself)
    conv: str = ""           # what is converted (推估)
    note: str = ""
    purpose: str = ""        # 訓練目的, one line (PURPOSE)
    trail: str = ""          # trail only: climb | technical | downhill (TRAIL_TYPES, SP-62)



# 訓練目的 (one line per session type, shown under each template): the table of
# docs/research/coach-schools-zones-periodization.md Finding 7, with the coaches it cites
# (msgids: translated where shown, row() / variant_purpose())
PURPOSE = {
    "recovery": N_("低於 AeT 的短跑，促進恢復、同時累積有氧量（Pfitzinger；CTS）"),
    "easy": N_("在 LT1 以下建立微血管、粒線體與脂肪代謝，是一切強度的地基（Daniels；Lydiard）"),
    "long": N_("延長在有氧強度下的持續時間，提升耐久性與脂肪利用（Daniels）"),
    "mlr": N_("週中第二個長刺激，增加耐力而不必再加一次長跑（Pfitzinger）"),
    "steady": N_("在 LT2 以下的「有挑戰的有氧強度」累積長時間，提升高端有氧耐久（Koop；Lydiard）"),
    "mp": N_("熟悉比賽配速與補給，代謝效益接近輕鬆跑，量以週量 15–20% 為限（Daniels）"),
    "tempo_t": N_("在乳酸閾值附近跑，提升清除乳酸的能力、推高 LT2（Daniels）"),
    "cruise": N_("用短休息讓乳酸維持穩定，在閾值強度累積比連續跑更多的時間（Daniels）"),
    "long_tempo": N_("在 AeT–AnT 之間累積時間，縮小有氧缺乏差距、建立越野爬坡所需的持續力（Uphill Athlete；Koop）"),
    "vo2max": N_("累積在接近最大攝氧量的時間，提高最大有氧能力（Daniels；Buchheit & Laursen）"),
    "short": N_("用極短休息讓心肺維持在 VO2max 附近，同時限制乳酸累積（Uphill Athlete；Billat）"),
    "rep": N_("在充分休息下以快於 VO2max 的速度跑，改善速度、跑姿與跑步經濟性（Daniels）"),
    "strides": N_("20–30 秒快而放鬆，維持神經肌肉速度與跑姿，幾乎不增加疲勞（Roche）"),
    "hill_sprint": N_("≥ 20% 坡 8–10 秒全力，訓練爆發力與肌肉徵召，不是有氧課（Uphill Athlete；Lydiard）"),
    "hill_rep": N_("在較低衝擊下達到高心肺負荷，訓練有氧能力與爬坡力量（Roche；CTS）"),
    "me": N_("讓腿部在高比例最大力量下重複上千次，限制來自腿而不是呼吸（Uphill Athlete）"),
    "downhill": N_("透過重複回合效應減少賽後肌肉損傷，放在賽前最後幾週（Roche）"),
    "technical": N_("在路況差的技術路段練腳步、判斷與節奏，瓶頸是地形不是心肺，所以看 RPE 不看心率（Koop；Uphill Athlete）"),
    "test": N_("重新校正錨點，讓所有區間跟著真實體能移動（Friel；Uphill Athlete）"),
}
# the interval ladder's rows (no Template of their own): by family_of()
FAMILY_PURPOSE = {("aerobic", "tempo"): "long_tempo", ("aerobic", "cruise"): "cruise",
                  ("aerobic", "supra"): "cruise", ("vo2max", None): "vo2max", ("vo2max", "short"): "short",
                  ("speed", None): "rep"}


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
HRMAX = N_("% LTHR 由來源的 % HRmax ÷ 0.9 換算（推估）")
HRMAX90 = N_("% LTHR 由來源的 % HRmax ÷ 0.9 換算（推估）（上緣壓在 90%）")
MAXHR_SRC = "Polar「How to determine your maximum heart rate」上坡測試（2016，2024 更新）；Boudet 2002"
MAXHR_URL = "https://www.polar.com/blog/calculate-maximum-heart-rate/"
MAXHR_NOTE = N_("條件：戴胸帶（手腕光學常有尖峰）、休息充足（前 48 小時沒有硬課）、沒生病、涼爽、找 2–3 分鐘的坡、"
              "最好有人陪。有心血管疾病或風險、胸痛、頭暈的人不要做，先問醫師（ACSM 運動前篩檢，Riebe 2015）；"
              "不舒服立刻停。替代：最近一場 5 K 比賽的最後衝刺也可以（現場、比賽與實驗室的最高心率沒有差別，Boudet 2002）。"
              "app 的讀法：濾掉尖峰與步頻鎖定後，撐 ≥ 5 秒的最高心率（推估）；結果要在設定頁按「套用」才會寫入")
TP = N_("配速是閾值配速的倍數（app 的 T 配速，推估）")


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
STEEP_SRC = "Pandolf 1977（同代謝率的坡度）；UA trekking（跑步機坡度替代背包）"
STEEP_URL = "https://doi.org/10.1152/jappl.1977.43.4.577"
# 技術地形 / 下坡 (SP-62): the structure is the app's (推估); D+ / D− sized for the time
TECH_SRC = KOOP + "（賽道專項：練和比賽相同的地形）；" + UA
TECH_CONV = N_("結構是這個 app 的建議（推估）：時間＋爬升＋RPE，不設心率、功率目標；爬升量依你的路線調")
DOWNHILL_M = 350
DOWNHILL_CONV = N_("下降量 ≈ 25′ × 約 7 km/h × 12% 坡（推估）；不設心率、功率目標")
STEEP_CONV = N_("坡度由 Pandolf 公式換算：不背包、這個坡度的代謝量 ≈ 在 12% 坡、3.5 km/h 背這個重量（推估）；心率 ≤ 輕鬆跑上限")

TEMPLATES: list[Template] = [
    # ---------------- 輕鬆跑 ----------------
    Template("pal_ez", "easy", "Palladino EZ 輕鬆跑＋3×10″ 加速", PAL, PAL_URL, lambda b: [
        b.t("warm", 24 * 60, pw(0.70, 0.80), "EZ ≤ 80% CP"),
        b.rep(3, [b.t("other", 10, OPEN, "10″ 漸進加速"), b.t("rest", 110, pw(0.65, 0.80), "輕鬆跑")], True, "加速 3×10″"),
        b.t("cool", 5 * 60, pw(0.70, 0.80), "EZ")], basis="power", purpose=PURPOSE["easy"]),
    Template("pfitz_recovery", "easy", "Pfitzinger 恢復跑 30′", PFITZ, PFITZ_URL, lambda b: [
        b.t("warm", 5 * 60, hr(0.70, 0.80), "很慢開始"),
        b.t("work", 25 * 60, hr(0.75, 0.84), "恢復跑 < 76% HRmax"),
        b.t("cool", 5 * 60, OPEN, "走路")], conv=HRMAX, purpose=PURPOSE["recovery"]),
    Template("pfitz_ga", "easy", "Pfitzinger 有氧耐力跑 60′", PFITZ, PFITZ_URL, lambda b: [
        b.warm(10, target=hr(0.75, 0.85)), b.t("work", 45 * 60, hr(0.78, 0.89), "70–81% HRmax"),
        b.cool(5, target=hr(0.70, 0.80))], conv=HRMAX, purpose=PURPOSE["easy"]),
    Template("pfitz_long", "easy", "Pfitzinger 長跑 2 小時", PFITZ, PFITZ_URL, lambda b: [
        b.warm(15, target=hr(0.75, 0.85)), b.t("work", 95 * 60, hr(0.82, 0.90), "74–84% HRmax"),
        b.cool(10, target=hr(0.70, 0.80))], conv=HRMAX90, purpose=PURPOSE["long"]),
    # 主要訓練項目 = 路跑 (engine/overview.road_long_session): Pfitzinger's marathon-pace long run
    Template("pfitz_mp_long", "easy", "Pfitzinger 馬拉松配速長跑（16 km MP）", PFITZ, PFITZ_URL, lambda b: [
        b.warm(20, target=hr(0.75, 0.85)), b.d("work", 16000, pace(1.04, 1.08), "馬拉松配速"),
        b.cool(10, target=hr(0.70, 0.80))], basis="pace", conv=N_("馬拉松配速 ≈ 閾值配速 × 1.06（推估）"), purpose=PURPOSE["mp"]),
    Template("daniels_e", "easy", "Daniels E 輕鬆跑 50′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(10), b.t("work", 35 * 60, hr(0.72, 0.88), "E：65–79% HRmax"), b.cool(5)], conv=HRMAX, purpose=PURPOSE["easy"]),
    Template("xu_e90", "easy", "徐國峰 E 強度 90′（看心率飄移）",
             "徐國峰 部落格 2015-10-14《E 配速心率飄移》；《全方位的馬拉松科學化訓練》2015",
             "http://rocky549.blogspot.com/2015/10/e.html", lambda b: [
        b.warm(10), b.t("work", 80 * 60, hr(0.72, 0.88), "平路 E；記第 10、90 分心率"), b.cool(10)],
        conv=HRMAX, note=N_("飄移 < 10% 有氧很好、< 5% 國手級（徐國峰）"), purpose=PURPOSE["long"]),
    Template("ua_z2", "easy", "Uphill Athlete 有氧基礎 Z2 60′", UA + "「Zone 2 Heart Rate Training」2026",
             "https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/", lambda b: [
        b.warm(10, "Z1：AeT −20%～−10%"), b.t("work", 60 * 60, AET, "Z2：AeT −10%～AeT"), b.cool(10)], purpose=PURPOSE["easy"]),
    Template("seiler_z1", "easy", "Seiler 兩極化低強度長課 100′",
             "Seiler 2010, IJSPP 5(3):276–291（1 區 60–72% HRmax）",
             "https://www.researchgate.net/publication/46403553_What_is_Best_Practice_for_Training_Intensity_and_Duration_Distribution_in_Endurance_Athletes",
             lambda b: [b.warm(10), b.t("work", 80 * 60, hr(0.67, 0.80), "1 區：60–72% HRmax"), b.cool(10)],
             src_kind="peer", conv=HRMAX, purpose=PURPOSE["long"]),

    # ---------------- 強度課 ----------------
    Template("pal_hm_tempo", "quality", "Palladino 半馬功率節奏 2×11′", PAL, PAL_URL, lambda b: [
        *b.pal_warm(12, 2),
        b.rep(2, [b.t("work", 11 * 60, pw(0.91, 0.96), "11′ 91–96% CP"),
                  b.t("rest", 3 * 60, pw(0.65, 0.75), "輕鬆跑")], False, "2×11′"),
        b.cool(5, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", purpose=PURPOSE["cruise"]),
    Template("pal_near", "quality", "Palladino 近閾值 3×7′", PAL, PAL_URL, lambda b: [
        *b.pal_warm(8, 1),
        b.rep(3, [b.t("work", 7 * 60, pw(0.96, 1.02), "7′ 96–102% CP"),
                  b.t("rest", 3 * 60, OPEN, "走路或慢跑（0–75% CP）")], False, "3×7′"),
        b.cool(5, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", purpose=PURPOSE["cruise"]),
    Template("pal_supra", "quality", "Palladino 超閾值 4×4:30", PAL, PAL_URL, lambda b: [
        *b.pal_warm(8, 1),
        b.rep(4, [b.t("work", 270, pw(0.98, 1.04), "4:30 98–104% CP"),
                  b.t("rest", 165, OPEN, "走路或慢跑（0–75% CP）")], False, "4×4:30"),
        b.cool(6, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", purpose=PURPOSE["cruise"]),
    Template("pal_vo2", "quality", "Palladino 最大有氧功率 4×2:40", PAL, PAL_URL, lambda b: [
        *b.pal_warm(12, 2),
        b.rep(4, [b.t("work", 160, pw(1.01, 1.06), "2:40 101–106% CP"),
                  b.t("rest", 150, OPEN, "走路或慢跑（0–75% CP）")], False, "4×2:40"),
        b.cool(9, "EZ 跑到總量", pw(0.65, 0.80))], basis="power", purpose=PURPOSE["vo2max"]),
    Template("daniels_cruise", "quality", "Daniels T 巡航間歇 5×6′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(5, [b.t("work", 6 * 60, pace(0.99, 1.01), "T 配速"),
                  b.t("rest", 60, OPEN, "慢跑 1′")], False, "5×6′"),
        b.cool(10)], basis="pace", conv=TP, purpose=PURPOSE["cruise"]),
    Template("pfitz_lt", "quality", "Pfitzinger 乳酸閾值節奏 25′", PFITZ, PFITZ_URL, lambda b: [
        b.warm(20), b.t("work", 25 * 60, hr(0.91, 1.00), "82–91% HRmax"), b.cool(15)], conv=HRMAX, purpose=PURPOSE["tempo_t"]),
    Template("friel_cruise", "quality", "Friel 巡航間歇 4×8′", "Joe Friel《The Triathlete's Training Bible》", FRIEL_URL, lambda b: [
        b.warm(15),
        b.rep(4, [b.t("work", 8 * 60, hr(0.95, 1.02), "Friel 4–5a 區"),
                  b.t("rest", 2 * 60, hr(0.70, 0.85), "1 區恢復（工作的 1/4）")], False, "4×8′"),
        b.cool(10)], purpose=PURPOSE["cruise"]),
    Template("koop_tempo", "quality", "Koop TempoRun 3×12′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(3, [b.t("work", 12 * 60, hr(0.95, 1.00), "RPE 8–9"),
                  b.t("rest", 6 * 60, AET, "輕鬆跑（工休 2:1）")], False, "3×12′"),
        b.cool(10)], conv=N_("來源只有 RPE：心率推估"), purpose=PURPOSE["cruise"]),
    Template("canova_specific", "quality", "Canova 專項間歇 5×3 km（1 km 浮動）",
             "Arcelli & Canova《Marathon Training – A Scientific Approach》1999",
             "https://runningwritings.com/2023/06/canova-marathon-book.html", lambda b: [
        b.warm(15),
        b.rep(5, [b.d("work", 3000, pace(1.04, 1.08), "100–102% 馬拉松配速"),
                  b.d("rest", 1000, pace(1.12, 1.25), "浮動：85–95% 馬拉松配速")], False, "5×3 km"),
        b.cool(10)], basis="pace", conv=N_("馬拉松配速 ≈ 閾值配速 × 1.06（推估）"), purpose=PURPOSE["mp"]),
    Template("canova_1k", "quality", "Canova 10×1000 m 強化", "Arcelli & Canova 1999（intensive block）",
             "https://runningwritings.com/2023/06/canova-marathon-book.html", lambda b: [
        b.warm(15),
        b.rep(10, [b.d("work", 1000, pace(0.96, 0.99), "111% 馬拉松配速（約 10K）"),
                   b.t("rest", 120, OPEN, "慢跑 2′")], False, "10×1 km"),
        b.cool(10)], basis="pace", conv=N_("馬拉松配速 ≈ 閾值配速 × 1.06（推估）"), purpose=PURPOSE["vo2max"]),
    Template("seiler_4x8", "quality", "Seiler 4×8′", "Seiler et al. 2013, Scand J Med Sci Sports 23:74–83",
             "https://pubmed.ncbi.nlm.nih.gov/21812820/", lambda b: [
        b.warm(15),
        b.rep(4, [b.t("work", 8 * 60, hr(1.00, 1.05), "能撐住的最高強度（≈ 90% HRpeak）"),
                  b.t("rest", 2 * 60, OPEN, "2′ 恢復")], False, "4×8′"),
        b.cool(10)], src_kind="peer", conv=N_("% LTHR 由 % HRpeak ÷ 0.9 換算（推估）"), purpose=PURPOSE["cruise"]),
    Template("pfitz_vo2", "quality", "Pfitzinger VO2max 5×1000 m", PFITZ, PFITZ_URL, lambda b: [
        b.warm(20),
        b.rep(5, [b.d("work", 1000, pace(0.93, 0.96), "5K 配速"),
                  b.t("rest", 150, OPEN, "慢跑 2–4′")], False, "5×1 km"),
        b.cool(10)], basis="pace", conv=N_("5K 配速 ≈ 閾值配速 × 0.93–0.96（推估）"), purpose=PURPOSE["vo2max"]),
    Template("daniels_i", "quality", "Daniels I 間歇 5×3′", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(5, [b.t("work", 3 * 60, pace(0.92, 0.95), "I 配速"),
                  b.t("rest", 3 * 60, OPEN, "等長慢跑")], False, "5×3′"),
        b.cool(10)], basis="pace", conv=N_("I 配速 ≈ 閾值配速 × 0.92–0.95（推估）"), purpose=PURPOSE["vo2max"]),
    Template("koop_vo2", "quality", "Koop RunningIntervals 6×3′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(6, [b.t("work", 3 * 60, hr(1.00, 1.06), "RPE 10"),
                  b.t("rest", 3 * 60, AET, "輕鬆跑 1:1")], False, "6×3′"),
        b.cool(10)], conv=N_("來源只有 RPE：心率推估（3′ 的趟心率會落後）"), purpose=PURPOSE["vo2max"]),
    Template("billat_3030", "quality", "Billat 30-30 ×16", "Billat et al. 2000, Eur J Appl Physiol 81:188–196",
             "https://link.springer.com/article/10.1007/s004210050029", lambda b: [
        b.warm(15),
        b.rep(16, [b.t("work", 30, pace(0.86, 0.90), "30″ vVO2max"),
                   b.t("rest", 30, OPEN, "30″ 50% vVO2max 慢跑")], False, "30-30"),
        b.cool(10)], basis="pace", src_kind="peer", conv=N_("vVO2max ≈ 閾值配速 × 0.86–0.90（推估）"), purpose=PURPOSE["short"]),
    Template("ronnestad_3015", "quality", "Rønnestad 30/15 3×13", "Rønnestad et al. 2020, Scand J Med Sci Sports",
             "https://pubmed.ncbi.nlm.nih.gov/31977120/", lambda b: [
        b.warm(20, target=pw(0.65, 0.75)),
        b.rep(3, [b.rep(13, [b.t("work", 30, pw(1.10, 1.20), "30″"),
                             b.t("rest", 15, pw(0.50, 0.60), "15″")], False),
                  b.t("rest", 3 * 60, pw(0.55, 0.65), "組間 3′")], False, "3 組 30/15"),
        b.cool(10, target=pw(0.55, 0.70))], basis="power", src_kind="peer",
        conv=N_("原研究是自行車功率：跑步的 % CP 推估"), purpose=PURPOSE["short"]),
    Template("daniels_r", "quality", "Daniels R 8×300 m", DANIELS, DANIELS_URL, lambda b: [
        b.warm(15),
        b.rep(8, [b.d("work", 300, pace(0.85, 0.89), "R 配速：快而放鬆"),
                  b.t("rest", 180, OPEN, "完全恢復（2–3 倍）")], False, "8×300 m"),
        b.cool(10)], basis="pace", conv=N_("R 配速 ≈ 閾值配速 × 0.85–0.89（推估）"), purpose=PURPOSE["rep"]),

    # ---------------- 測試 ----------------
    Template("stryd_cp_3_12", "test", "Stryd 內建 CP 測試 3′＋12′", "Stryd 內建課表庫（Palladino）：3′／12′ CP test", PAL_URL, lambda b: [
        b.t("warm", 12 * 60, pw(0.70, 0.80), "EZ ≤ 80% CP"),
        b.rep(2, [b.t("warm", 60, pw(0.99, 1.01), "1′ 99–101% CP"), b.t("warm", 120, pw(0.70, 0.80), "輕鬆跑")], True, "2×1′"),
        b.t("warm", 30, pw(1.03, 1.08), "30″ 103–108% CP"), b.t("warm", 90, pw(0.70, 0.80), "輕鬆跑"),
        b.lap("rest", 120, "走路到呼吸恢復（≥ 2′），直到按下計圈"),
        b.t("work", 3 * 60, OPEN, "3′ 全力、均勻"),
        b.lap("rest", 30 * 60, "走→輕鬆跑 5–10′→走，≥ 30′；直到按下計圈"),
        b.t("work", 12 * 60, OPEN, "12′ 全力、均勻"),
        b.lap("cool", 10 * 60, "走路恢復後輕鬆跑到總量；直到按下計圈", pw(0.65, 0.80))], basis="power",
        note=N_("全力段不設目標（Stryd 留給你填：模型功率 −1%～+5%）"), purpose=PURPOSE["test"]),
    Template("pal_test3", "test", "Palladino 3′ 測試", PAL + "：3 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 3 * 60, OPEN, "3′ 全力：穩穩開始，最後才到極限"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, pw(0.70, 0.80), "EZ 跑到總量")], basis="power", purpose=PURPOSE["test"]),
    Template("pal_test10", "test", "Palladino 10′ 測試", PAL + "：10 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 10 * 60, OPEN, "10′ 全力：前 7′ 照 10′ 模型功率"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, pw(0.70, 0.80), "EZ 跑到總量")], basis="power", purpose=PURPOSE["test"]),
    Template("pal_test20", "test", "Palladino 20′ 測試", PAL + "：20 minute max effort", PAL_URL, lambda b: [
        *b.pal_test_warm(), b.t("work", 20 * 60, OPEN, "20′ 全力：前 15′ 照 20′ 模型功率"),
        b.t("rest", 5 * 60, OPEN, "走路恢復"), b.t("cool", 10 * 60, pw(0.70, 0.80), "EZ 跑到總量")], basis="power", purpose=PURPOSE["test"]),
    Template("stryd_9_3", "test", "Stryd 9′＋3′ CP 測試", "Stryd CP 測試（9/3 分鐘）；JSSM 2023 驗證",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC10499150/", lambda b: [
        b.t("warm", 10 * 60, pw(0.65, 0.75), "EZ"),
        b.rep(5, [b.d("other", 100, OPEN, "100 m 約 80% 力"), b.t("rest", 60, OPEN, "慢跑")], True, "5×100 m"),
        b.t("work", 9 * 60, OPEN, "9′ 全力、均勻"),
        b.t("rest", 30 * 60, pw(0.50, 0.65), "走路或輕鬆跑 30′"),
        b.t("work", 3 * 60, OPEN, "3′ 全力、均勻"), b.t("cool", 10 * 60, pw(0.55, 0.70), "緩和")],
        basis="power", src_kind="peer", purpose=PURPOSE["test"]),
    Template("friel_lthr30", "test", "Friel 30′ 閾值心率測試", "Joe Friel「Quick Guide to Setting Zones」TrainingPeaks", FRIEL_URL, lambda b: [
        b.warm(15), b.t("work", 10 * 60, OPEN, "獨自全力 30′ 的前 10′：第 10′ 按下計圈"),
        b.t("work", 20 * 60, OPEN, "後 20′：平均心率＝LTHR"), b.cool(10)],
        note=N_("只看全力，不設目標；手腕光學心率的平均誤差比胸帶大。條件：< 25 °C、平路環線或田徑場、戴胸帶、"
              "前 48 小時沒有硬課、不在減量期／比賽週；自己一個人跑，不要跟人跑、不要在比賽中測"),
        purpose=PURPOSE["test"]),
    # SP-64: Polar's hill field test (docs/research/zones-and-thresholds.md 附錄 C); the app reads
    # the highest HR held ≥ 5 s after spike / cadence-lock filtering (threshold_confidence.hrmax_result)
    Template("maxhr_hill", "test", "最大心率測試：3 趟上坡、最後一趟全力", MAXHR_SRC, MAXHR_URL, lambda b: [
        b.t("warm", 15 * 60, AET, "平路暖身 15′，慢慢加到平常的訓練配速"),
        b.lap("work", 150, "上坡 ≥ 2′：能撐 20 分鐘的強度，記下心率"),
        b.lap("rest", 180, "慢跑／走下來，心率降 30–40 bpm"),
        b.lap("work", 150, "同一段上坡再跑一次，更快（約 3 km 比賽的力）"),
        b.lap("rest", 180, "慢跑／走下來，心率降 30–40 bpm"),
        b.lap("work", 60, "最後一趟：全力衝 1′（上半段坡），結束前的心率最接近最大心率"),
        b.cool(10, "走路／慢跑緩和 ≥ 10′")],
        note=MAXHR_NOTE, purpose=PURPOSE["test"]),
    Template("ua_aet_drift", "test", "AeT 心率飄移測試 60′", "Steve House, Uphill Athlete「Heart Rate Drift」",
             "https://uphillathlete.com/aerobic-training/heart-rate-drift/", lambda b: [
        b.warm(15, "慢慢加到心率穩定"),
        b.t("work", 60 * 60, AET, "固定對話配速、平路；前後半心率比"),
        b.cool(10)],
        note=N_("飄移 3.5–5% 就是 AeT；< 3.5% 下次 +5 bpm、> 5% 降低（來源要胸帶，手腕心率只看趨勢）"), purpose=PURPOSE["test"]),
    Template("xu_e_drift", "test", "徐國峰 90′ E 配速飄移", "徐國峰 部落格 2015-10-14",
             "http://rocky549.blogspot.com/2015/10/e.html", lambda b: [
        b.warm(10), b.t("work", 80 * 60, pace(1.18, 1.29), "固定 E 配速；比第 10、90 分心率"), b.cool(10)],
        basis="pace", conv=N_("E 配速 ≈ 閾值配速 × 1.18–1.29（Friel 2 區配速，推估）"), purpose=PURPOSE["test"]),

    # ---------------- 越野跑 ----------------
    Template("dsw_classic", "trail", "登山王經典 4×7′ 爬升", "江晏慶「如何提升越野跑的爬升能力－登山王課表」Garmin 台灣 2021-01-27",
             GARMIN_DSW, lambda b: [
        b.warm(12, "階梯步道輕鬆跑／走"),
        b.rep(4, [b.t("work", 7 * 60, hr(0.95, 1.02), "上坡 9 成力（快崩但不爆）"),
                  b.t("rest", 7 * 60, OPEN, "慢慢走下來")], False, "4×7′ 上坡"),
        b.t("cool", 5 * 60, OPEN, "收操、補給")], conv=N_("來源是 RPE：心率推估"), purpose=PURPOSE["hill_rep"], trail="climb"),
    Template("dsw_endurance", "trail", "登山王耐力型 6×快走上坡", "江晏慶 登山王課表（耐力變化）Garmin 台灣 2021", GARMIN_DSW, lambda b: [
        b.warm(12, "輕鬆跑／走"),
        b.rep(6, [b.t("work", 7 * 60, hr(0.88, 0.93), "快走上坡"),
                  b.t("rest", 5 * 60, AET, "慢跑下來")], False, "6×上坡"),
        b.t("cool", 5 * 60, OPEN, "收操")], conv=N_("來源是 RPE：心率推估"), purpose=PURPOSE["long_tempo"], trail="climb"),
    Template("ua_hill_sprints", "trail", "陡坡衝刺 8×10″", UA + "《Training for the Uphill Athlete》2019",
             "https://uphillathlete.com/", lambda b: [
        b.warm(15),
        b.rep(8, [b.t("work", 10, OPEN, "≥ 20% 陡坡 10″ 全力"), b.t("rest", 3 * 60, OPEN, "走到完全恢復")], False, "8×10″"),
        b.cool(10)], note=N_("全力衝刺：心率、功率都不當目標"), purpose=PURPOSE["hill_sprint"], trail="climb"),
    Template("koop_uphill", "trail", "Koop 上坡 TempoRun 3×12′", KOOP, KOOP_URL, lambda b: [
        b.warm(15),
        b.rep(3, [b.t("work", 12 * 60, hr(0.95, 1.00), "上坡 RPE 8–9"),
                  b.t("rest", 6 * 60, AET, "下坡或平路輕鬆")], False, "3×12′ 上坡"),
        b.cool(10)], conv=N_("來源只有 RPE：心率推估"), purpose=PURPOSE["cruise"], trail="climb"),
    Template("long_climb", "trail", "長爬坡有氧 90′", UA + " Zone 2（AeT −10%～AeT）",
             "https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/", lambda b: [
        b.warm(15), b.t("work", 90 * 60, AET, "持續爬升，跑走混合，心率 ≤ 輕鬆跑上限"),
        b.t("cool", 15 * 60, OPEN, "輕鬆下山")], src_kind="推估", purpose=PURPOSE["long"], trail="climb"),
    Template("steep_5", "trail", f"陡坡健走 {_S5['grade']:g}%（模擬背 5% 體重）", STEEP_SRC, STEEP_URL, _S5B,
             conv=STEEP_CONV, note=N_("不背包；百岳前的專項期，第 1 階段"), purpose=PURPOSE["me"], trail="climb"),
    Template("steep_10", "trail", f"陡坡健走 {_S10['grade']:g}%（模擬背 10% 體重）", STEEP_SRC, STEEP_URL, _S10B,
             conv=STEEP_CONV, note=N_("不背包；第 2 階段"), purpose=PURPOSE["me"], trail="climb"),
    Template("steep_15", "trail", f"陡坡健走 {_S15['grade']:g}%（模擬背 15% 體重）", STEEP_SRC, STEEP_URL, _S15B,
             conv=STEEP_CONV, note=N_("不背包；行程背包約體重 15% 時"), purpose=PURPOSE["me"], trail="climb"),
    Template("downhill_ecc", "trail", "下坡離心預適應 25′",
             "Assumpção et al. 2020 Sci Rep；Bontemps et al. 2020 Sports Med；Koop（TrainRight）",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC7606541/", lambda b: [
        b.warm(10, "平路暖身"), b.t("work", 25 * 60, rpe(3, 5, down=DOWNHILL_M), "−10～−15% 下坡，輕鬆到中等"),
        b.cool(10, "平路緩和")],
        basis="rpe", src_kind="peer", conv=DOWNHILL_CONV, note=N_("賽前 ≥ 2 週做；效果約 9 週，之後 2–3 天輕鬆；下坡功率、心率都不準，看 RPE"),
        purpose=PURPOSE["downhill"], trail="downhill"),
    # 技術地形 (SP-62): time + climb + RPE; no HR / power target (footing limits the pace, HR stays
    # low, so hrTSS under-reads — the load is still the watch's record). Low RPE = the long-run
    # slot (aerobic), RPE ≥ 7 = a quality session (workout_steps.rpe_role).
    Template("tech_easy", "trail", "技術地形 60′（低 RPE）", TECH_SRC, KOOP_URL, lambda b: [
        b.warm(10, "好走的路段暖身"), b.t("work", 60 * 60, rpe(3, 4, up=300), "技術路段，跑走混合，練腳步"),
        b.t("cool", 5 * 60, OPEN, "收操")], basis="rpe", src_kind="推估", conv=TECH_CONV,
        note=N_("基礎期：輕鬆的有氧課，可以取代部分長跑"), purpose=PURPOSE["technical"], trail="technical"),
    Template("tech_hard", "trail", "技術地形 90′（中高 RPE、爬升 600 m）", TECH_SRC, KOOP_URL, lambda b: [
        b.warm(15, "好走的路段暖身"), b.t("work", 90 * 60, rpe(6, 7, up=600), "接近比賽路況的技術路段"),
        b.t("cool", 10 * 60, OPEN, "收操")], basis="rpe", src_kind="推估", conv=TECH_CONV,
        note=N_("專項期每週 1 堂，路況接近比賽；RPE 7 算強度課（隔 48 小時、算進強度預算）"),
        purpose=PURPOSE["technical"], trail="technical"),
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


# ---------------------------------------------------------------------------
# 強度課的家族 (SP-32): the single source of the 有氧間歇 / VO2max 間歇 / 速度 split —
# the 插入範本 tabs, the 推薦 block (engine/template_recs.py), the interval ladder's rows
# and the label on a plan's 強度課 (api/plan_sessions.py). Intensity first, then rep length
# (docs/research/coach-schools-zones-periodization.md R1; the user's defaults 2026-10-04):
#   1. main work ≤ 101 % CP (≤ 102 % LTHR, a pace not faster than T) → 有氧間歇 (閾值家族):
#      長 tempo = reps ≥ 15′ or one continuous block ≥ 15′; 巡航間歇 = shorter reps. Reps under
#      6′ at this intensity (6×3′ @ 92–97 %, Palladino 4×4:30 @ 98–104 %) are 巡航 too —
#      Daniels' cruise reps start at 3′ and their purpose (time at threshold on short
#      rests) is the same (推估: the report gives no class below 6′).
#   2. above that, reps 2–5′ → VO2max 間歇; reps < 2′ with a rest < 2 × the rep (30/30,
#      30/15) → VO2max 間歇 too, sub 短間歇 (time at VO2max); reps ≤ 2′ with a rest ≥ 2 × the rep, or
#      power > 116 % CP → 速度 (R, strides, short hill sprints).
#   3. above threshold with reps > 5′ (Seiler 4×8′, 3×6′ @ 105 %) → 巡航間歇's upper end,
#      marked 超閾值 (the report's rule 4).
#   4. no target on the work (strides, all-out sprints): reps ≤ 2′ with a long rest → 速度;
#      otherwise no family (tests, easy runs).
# Distance reps take T pace × the step's own pace (no T pace: 4:48/km = 6:00/km ÷ 1.25, the
# menu's easy-pace assumption, 推估), so a 1000 m rep is ~4–5′.
# ---------------------------------------------------------------------------

AEROBIC_MAX_CP = 1.01        # × CP: Palladino 3 區's top
AEROBIC_MAX_LTHR = 1.02      # × LTHR (the report's HR form of the same line)
AEROBIC_MIN_TPACE = 0.99     # × threshold pace (bigger = slower): T pace or slower
SPEED_MIN_CP = 1.16          # × CP: above Palladino 5 (116 %) → 速度 (Palladino 6, Coggan L6)
EASY_MAX = {"power": 0.85, "hr": 0.88}    # below: an easy step, not interval work (推估)
EASY_MIN_TPACE = 1.12        # × T pace: slower than this = easy (MP ≈ 1.06 is not)
SPEED_MAX_S = 120            # a 速度 rep is ≤ 2′
VO2_MAX_S = 300              # VO2max reps 2–5′ (2′ inclusive: the user, 2026-10-04)
TEMPO_MIN_S = 900            # 長 tempo: reps of 15–30′
SPEED_REST = 2.0             # rest ≥ 2 × the rep = 速度's long rest (Daniels R: 2–3 ×)
T_S_PER_KM = 288.0           # s/km of threshold pace without the athlete's (推估)

FAMILIES = [
    {"id": "aerobic", "label": N_("有氧間歇"),
     "tip": N_("有氧／閾值：主課 ≤ 101% CP（心率 ≤ 102% LTHR、配速不快於閾值配速）。"
               "長 tempo 每趟 15–30 分或連續一段，巡航間歇每趟 6–15 分（更短的也算巡航）")},
    {"id": "vo2max", "label": N_("VO2max 間歇"),
     "tip": N_("主課高於閾值、每趟 2–5 分、休息約 1:1；30/30、30/15 這種短趟短休也算這類")},
    {"id": "speed", "label": N_("速度"),
     "tip": N_("每趟 ≤ 2 分、休息 ≥ 2 倍（R、加速跑、短坡衝刺），或功率 > 116% CP")},
]
FAMILY_IDS = tuple(f["id"] for f in FAMILIES)
FAMILY_LABEL = {f["id"]: f["label"] for f in FAMILIES}
SUB_LABEL = {"tempo": N_("長 tempo"), "cruise": N_("巡航間歇"), "supra": N_("巡航（超閾值）"),
             "short": N_("短間歇")}
# 越野跑's three kinds (SP-62, the user's decision 2026-10-04): by terrain and purpose
TRAIL_TYPES = [
    {"id": "climb", "label": N_("結構化爬升"),
     "tip": N_("階梯步道、坡度穩定的路線（登山王、Koop 上坡 tempo、陡坡健走）：每組時間＋強度，心率／功率上下限照設")},
    {"id": "technical", "label": N_("技術地形"),
     "tip": N_("路況差的技術路段：時間＋爬升＋RPE，不設心率、功率目標（只給參考）；RPE ≥ 7 算強度課")},
    {"id": "downhill", "label": N_("下坡技術／離心"),
     "tip": N_("練下坡：時間＋下降量，不設心率、功率目標（下坡功率不準）")},
]
TRAIL_IDS = tuple(t["id"] for t in TRAIL_TYPES)
TRAIL_LABEL = {t["id"]: t["label"] for t in TRAIL_TYPES}
_CATS = [{"id": "easy", "label": N_("輕鬆跑")}, {"id": "quality", "label": N_("強度課"), "subs": FAMILIES},
         {"id": "test", "label": N_("測試")}, {"id": "trail", "label": N_("越野跑"), "subs": TRAIL_TYPES}]


def cats() -> list:
    """The 插入範本 category tabs (強度課's sub-tabs = the families, 越野跑's = TRAIL_TYPES), in the
    request's language."""
    return [{**c, "label": _(c["label"]),
             **({"subs": [{**f, "label": _(f["label"]), "tip": _(f["tip"])} for f in c["subs"]]}
                if c.get("subs") else {})} for c in _CATS]


def _mid(t: dict) -> Optional[float]:
    lo, hi = t.get("lo"), t.get("hi")
    return (float(lo) + float(hi)) / 2.0 if lo is not None and hi is not None else None


def _level(t: Optional[dict], th: Optional[dict] = None) -> Optional[str]:
    """One step's target → easy | thr (≤ threshold) | above | speed, or None (no target / not
    readable). Relative targets as written; absolute ones need the athlete's anchor in `th`."""
    t = t or {}
    ty, mode = t.get("type"), t.get("mode", "pct")
    th = th or {}
    if ty == "auto":
        it = t.get("intent")
        if it == "easy":
            return "easy"
        if it != "band":
            return None
        ty, mode = "power", "pct"
    if ty not in ("power", "hr", "pace"):
        return None
    if mode == "zone":
        if ty == "hr" and t.get("zone") == "aet":
            return "easy"
        row = next((r for r in WS.ZONES[ty] if r[0] == t.get("zone") and r[1] is not None), None)
        m = (row[1] + row[2]) / 2.0 if row else None
    else:
        m = _mid(t)
        if m is not None and mode == "abs":
            base = th.get({"power": "cp", "hr": "lthr", "pace": "tpace"}[ty])
            m = m / float(base) if base else None
    if m is None:
        return None
    m = round(m, 4)
    if ty == "pace":
        return "easy" if m > EASY_MIN_TPACE else "thr" if m >= AEROBIC_MIN_TPACE else "above"
    if m < EASY_MAX[ty]:
        return "easy"
    if ty == "power" and m > SPEED_MIN_CP:
        return "speed"
    return "thr" if m <= (AEROBIC_MAX_CP if ty == "power" else AEROBIC_MAX_LTHR) else "above"


def _secs(st: dict, th: Optional[dict] = None) -> float:
    d = st.get("dur") or {}
    if d.get("type") == "time":
        return float(d.get("value") or 0)
    if d.get("type") == "distance":
        t = st.get("target") or {}
        tp = float((th or {}).get("tpace") or T_S_PER_KM)
        if t.get("type") == "pace" and _mid(t) is not None:
            per_km = _mid(t) if t.get("mode") == "abs" else _mid(t) * tp
        else:
            per_km = tp
        return float(d.get("value") or 0) / 1000.0 * per_km
    return float(d.get("est") or 0)


def _flat(items: list) -> list:
    """Steps in run order, repeats unrolled (no rest after the last rep when last_rest is off)."""
    out = []
    for it in items or []:
        if it.get("kind") == "repeat":
            n = int(it.get("times") or 1)
            for i in range(n):
                kids = list(it.get("items") or [])
                if i == n - 1 and it.get("last_rest") is False:
                    while kids and kids[-1].get("kind") == "rest":
                        kids.pop()
                out += _flat(kids)
        else:
            out.append(it)
    return out


def classify(work_s: float, rest_s: Optional[float], level: Optional[str]) -> Optional[tuple]:
    """(family, sub) of a main set whose typical rep is `work_s` seconds, followed by
    `rest_s` (None = one continuous block), at `level` (easy | thr | above | speed | None).
    sub: tempo / cruise / supra on 有氧間歇, short (reps < 2′) on VO2max 間歇, else None."""
    if work_s <= 0:
        return None
    long_rest = rest_s is not None and rest_s >= SPEED_REST * work_s
    if level is None:
        return ("speed", None) if work_s <= SPEED_MAX_S and long_rest else None
    if level == "easy":
        return None
    if level == "speed":
        return "speed", None
    if level == "thr":
        return "aerobic", "tempo" if work_s >= TEMPO_MIN_S else "cruise"
    if work_s <= SPEED_MAX_S and long_rest:
        return "speed", None
    if work_s < SPEED_MAX_S:
        return "vo2max", "short"
    if work_s <= VO2_MAX_S:
        return "vo2max", None
    return "aerobic", "supra"


def family_of(items: list, th: Optional[dict] = None) -> Optional[dict]:
    """The family of a structure (a whole session or its main set): its `work` steps — the
    median rep length, the median rest between reps, the intensity holding most of the work
    time. {"id", "sub", "label", "sub_label", "text"} or None (no interval work)."""
    flat = _flat(items)
    works, rests, by_level = [], [], {}
    gap = None
    for st in flat:
        sec = _secs(st, th)
        if st.get("kind") == "work":
            if gap is not None:
                rests.append(gap)
            works.append(sec)
            lv = _level(st.get("target"), th)
            if lv is not None:
                by_level[lv] = by_level.get(lv, 0.0) + sec
            gap = 0.0
        elif gap is not None and st.get("kind") != "cool":
            gap += sec
    if not works:
        return None
    level = max(by_level, key=by_level.get) if by_level else None
    got = classify(median(works), median(rests) if rests else None, level)
    return label(*got) if got else None


def label(fam: str, sub: Optional[str] = None) -> dict:
    name = _(FAMILY_LABEL[fam])
    sl = _(SUB_LABEL[sub]) if sub else ""
    return {"id": fam, "sub": sub, "label": name, "sub_label": sl, "text": f"{name}・{sl}" if sl else name}


def family_of_variant(v) -> Optional[dict]:
    """An interval-ladder variant (engine/interval_library.py) through the same rule."""
    return family_of(WS.main_set(v))


def steps_family(s: dict, th: Optional[dict] = None) -> Optional[dict]:
    """The family a 強度課's structure reads as: its own steps, else the derived ones (the
    ladder variant / the 「N×M 分」 text). None for another kind or no interval work."""
    if s.get("kind") != "quality":
        return None
    steps = s.get("steps") or WS.derive(s, th)
    return family_of(steps["items"], th) if steps and steps.get("items") else None


_DERIVE = object()


def session_family(s: dict, th: Optional[dict] = None, derived=_DERIVE) -> Optional[dict]:
    """A plan session's family (強度課 only, SP-79): the one the user picked in the 課表 editor
    (stored `family`; the sub-type kept when the steps agree), else steps_family — so a session
    stored before SP-79, or never given one, still shows its family. `derived`: steps_family's
    result when the caller already has it."""
    if s.get("kind") != "quality":
        return None
    own = s.get("family") if s.get("family") in FAMILY_IDS else None
    got = steps_family(s, th) if derived is _DERIVE else derived
    if own is None or (got and got["id"] == own):
        return got
    return label(own)


BASIS_LABEL = {"power": "功率", "hr": "心率", "pace": "配速", "rpe": "RPE"}


def trail_type_of(items: list) -> str:
    """The 越野跑 kind of a structure (SP-62): an RPE work step with only a descent → downhill,
    any other RPE work step → technical (time + climb + RPE), else climb (HR / power bands on
    a steady grade). A library template says its own (`Template.trail`)."""
    rp = [(st.get("target") or {}) for st in _flat(items) if st.get("kind") in ("work", "other")
          and (st.get("target") or {}).get("type") == "rpe"]
    if not rp:
        return "climb"
    return "downhill" if all(t.get("down") and not t.get("up") for t in rp) else "technical"


def row(t: Template) -> dict:
    """One 插入範本 row; 強度課 rows also carry their family (family_of: `sub` = the tab id),
    越野跑 rows their kind (`sub` = climb / technical / downhill; `role` = workout_steps.rpe_role)."""
    full = items_of(t)
    fam = family_of(full) if t.cat == "quality" else None
    sub = fam["id"] if fam else (t.trail or trail_type_of(full)) if t.cat == "trail" else None
    return {"key": f"lib:{t.key}", "label": t.title, "title": t.title, "src": t.src, "url": t.url,
            "src_kind": t.src_kind, "conv": _(t.conv) if t.conv else "", "note": _(t.note) if t.note else "", "items": main_of(full) or full, "full": full,
            "equiv": None, "sub": sub, "family": fam, "purpose": _(t.purpose) if t.purpose else "",
            "basis": t.basis, "basis_label": BASIS_LABEL[t.basis], "role": WS.rpe_role(full)}


def session_role(s: dict) -> Optional[str]:
    """How the scheduler counts a session (SP-62): "quality" for 強度課 / tests and for an
    RPE-set (技術地形) structure whose RPE reaches 很累 (≥ 7: 48 h spacing, the week's quality
    budget), "easy" for an RPE-set one below that (the long-run slot), else None (by its kind)."""
    if s.get("kind") in ("quality", "test"):
        return "quality"
    steps = s.get("steps")
    if isinstance(steps, str):
        import json
        try:
            steps = json.loads(steps)
        except ValueError:
            steps = None
    return WS.rpe_role((steps or {}).get("items") or []) if isinstance(steps, dict) else None


def variant_purpose(fam: Optional[dict]) -> str:
    """訓練目的 of an interval-ladder row, by its family."""
    k = FAMILY_PURPOSE.get((fam["id"], fam["sub"])) if fam else None
    return _(PURPOSE[k]) if k else ""
