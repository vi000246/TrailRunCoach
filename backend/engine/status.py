"""
Training status — the layer that turns the charts into one glance.

A handful of indicators, each with a level (good / watch / bad / info / na),
a one-line verdict, the numbers behind it and, when it isn't good, what to
do. The rules are phase-aware (season plan) and every threshold names its
source; see docs/research/periodization-phase-metrics.md and the athlete's
own notes summarised there.

Nothing here is a medical or injury prediction — the levels are coaching
heuristics from the cited sources applied to the athlete's own numbers.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Optional

from backend.engine.planning import KINDS, PHASES, Plan, goals, phase_on
from backend.engine.wko5expr.dataset import Dataset, Workout, date_to_day
from backend.engine.wko5expr.evaluator import WS, Evaluator
from backend.files.wko5_athlete import day_to_date

GOOD, WATCH, BAD, INFO, NA = "good", "watch", "bad", "info", "na"

# ---- thresholds, each with its source --------------------------------------
SRC_PALLADINO = "Palladino（你的筆記：PMC 訓練負荷 / Ramp rate）"
SRC_TP_TSB = "TrainingPeaks / Friel TSB 區間；Palladino A/B/C 賽 TSB"
SRC_UA = "Uphill Athlete"
SRC_SEILER = "Seiler 2006 強度分配；Palladino 金字塔 70–90% 輕鬆"
SRC_BOSQUET = "Bosquet 2007 減量統合分析"
SRC_KOOP = "Koop《Training Essentials for Ultrarunning》"
SRC_CHIANG = "江晏慶（你的筆記：越野跑周期化訓練）"
SRC_NOTES = "你的筆記"

RAMP = {"sustain": 3.0, "elite": 5.0, "short": 7.0}          # CTL/week (Palladino)
TSB_A = (10.0, 20.0)                                        # A race, taper end (Palladino)
TSB_PRODUCTIVE = (-30.0, -10.0)                             # Friel: productive training
TSB_OVERREACH = -30.0
TSB_STALE = 25.0
LOW_SHARE_GOOD, LOW_SHARE_WATCH = 0.75, 0.65                 # Seiler / Palladino
VOLUME_STEP_WATCH = 0.10                                    # UA: >10%/week
TAPER_BAND = (0.40, 0.59)                                   # Bosquet: -41…-60%
DRIFT_GOOD, DRIFT_WATCH = 0.05, 0.10                         # UA (<5%), 徐國峰 (90' E <10%)
EF_TREND = 0.02                                             # ±2% = meaningful (heuristic)
VAM_TREND = 0.03
SPECIFIC_SHARE = 0.70                                       # 江晏慶: 專項期練到比賽的七成
STRENGTH_PER_WEEK = 2                                       # UA
TEST_DAYS_WATCH, TEST_DAYS_BAD = 42, 90                       # CP test every 4–6 weeks (notes)
DAILY_PCT_EASY = 1.0                                        # easy day < 100% of CTL (Palladino)

TRAIL_TAGS = {"runningtrail"}
HIKE_TAGS = {"hiking", "mountaineering"}


@dataclass
class Indicator:
    id: str
    title: str
    level: str
    text: str                      # the number, formatted
    verdict: str                   # one line
    why: str = ""                  # the numbers behind it
    action: str = ""               # what to do when not good
    source: str = ""
    value: Optional[float] = None
    spark: list = field(default_factory=list)   # [[iso date, value], ...]
    extra: dict = field(default_factory=dict)


@dataclass
class Action:
    priority: int                  # 1 = most important
    title: str
    detail: str
    because: list[str]             # indicator ids
    source: str = ""


def _hms(s: Optional[float]) -> str:
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return "–"
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}"


def _n(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _pct(v: Optional[float], d=0) -> str:
    return "–" if v is None else f"{v * 100:.{d}f}%"


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


class Status:
    """Compute everything once for `today`; `.to_dict()` is the API payload."""

    def __init__(self, ds: Dataset, plan: Optional[Plan] = None, today: Optional[dt.date] = None):
        self.ds = ds
        self.plan = plan if plan is not None else ds.plan
        self.today = today or day_to_date(ds.today)
        self.tday = int(math.floor(date_to_day(self.today)))
        self.ev = Evaluator(ds, self.tday - 400, self.tday)
        self.phase = phase_on(self.plan, self.today)
        self.kind = self.phase.kind if self.phase else None
        self.goals = goals(self.plan, self.today)
        self.indicators: list[Indicator] = []
        self.actions: list[Action] = []

    # ---- helpers ---------------------------------------------------------
    def _iso(self, day: float) -> str:
        return day_to_date(day).isoformat()

    def since(self, days: int, sports=None) -> list[Workout]:
        lo = self.tday - days + 1
        return [w for w in self.ds.workouts if lo <= math.floor(w.day) <= self.tday
                and (sports is None or w.sport in sports)]

    def between(self, days_hi: int, days_lo: int) -> list[Workout]:
        """Workouts from `days_hi` days ago up to (not incl.) `days_lo` days ago."""
        lo, hi = self.tday - days_hi + 1, self.tday - days_lo
        return [w for w in self.ds.workouts if lo <= math.floor(w.day) <= hi]

    @staticmethod
    def is_trail(w):
        return bool(TRAIL_TAGS & set(w.tags))

    @staticmethod
    def is_hike(w):
        return bool(HIKE_TAGS & set(w.tags))

    @staticmethod
    def is_road(w):
        return w.sport == "run" and not TRAIL_TAGS & set(w.tags)

    def mountain(self, w):
        return self.is_trail(w) or self.is_hike(w)

    @staticmethod
    def m(w, key):
        return _n(w.metrics.get(key))

    def ws(self, expr: str) -> dict[int, float]:
        """Per-workout values of an athlete-level expression, NaN dropped."""
        r = self.ev.evaluate(expr)
        if not isinstance(r, WS):
            return {}
        return {k: float(v) for k, v in r.items() if _n(v) is not None}

    def weekly_hours(self, weeks: int) -> list[tuple[dt.date, float]]:
        """[(monday, hours)] for the last `weeks` weeks incl. the current one."""
        monday = self.today - dt.timedelta(days=self.today.weekday())
        out = []
        for i in range(weeks - 1, -1, -1):
            s = monday - dt.timedelta(weeks=i)
            e = s + dt.timedelta(days=6)
            lo, hi = date_to_day(s), date_to_day(e)
            h = sum((self.m(w, "duration") or 0) for w in self.ds.workouts
                    if lo <= math.floor(w.day) <= hi) / 3600
            out.append((s, h))
        return out

    # ---- indicators ------------------------------------------------------
    def compute(self) -> "Status":
        self.indicators = []
        self.actions = []
        for fn in (self.i_phase, self.i_fitness, self.i_form, self.i_volume, self.i_intensity,
                   self.i_efficiency, self.i_drift, self.i_climb, self.i_long, self.i_density,
                   self.i_strength, self.i_durability, self.i_testing, self.i_data):
            try:
                ind = fn()
            except Exception as e:  # one broken indicator must not hide the rest
                ind = Indicator(fn.__name__[2:], fn.__name__[2:], NA, "–", f"計算失敗：{type(e).__name__}: {e}")
            if ind is not None:
                self.indicators.append(ind)
        self._recommend()
        return self

    def i_phase(self) -> Indicator:
        p = self.phase
        g = self.goals
        nxt = next((e for e in self.plan.events if e.id == g["next_a"]), None)
        if p is None:
            return Indicator("phase", "周期", INFO, "未設定",
                             "還沒有目標賽事，先當作基礎期來看",
                             "到「賽事周期」頁加一場 A 賽事，周期會自動排出來", source=SRC_UA)
        left = (dt.date.fromisoformat(p.end) - self.today).days
        txt = f"{PHASES[p.kind]}"
        if nxt:
            why = f"{p.start} ～ {p.end}，還有 {left} 天；下一場 A 賽事「{nxt.name}」倒數 {g['days_to_next_a']} 天"
        else:
            why = "沒有接下來的 A 賽事，預設為基礎期；到「賽事周期」頁加一場，周期會自動排出來"
        return Indicator("phase", "周期", INFO, txt, PHASE_GOAL[p.kind], why,
                         extra={"kind": p.kind, "days_left": left, "next_a": None if not nxt else nxt.name,
                                "days_to_next_a": g["days_to_next_a"]})

    def i_fitness(self) -> Indicator:
        ctl = self.ev.evaluate("ctl")
        now = _n(ctl.at(self.tday))
        wk = _n(ctl.at(self.tday - 7))
        mo = _n(ctl.at(self.tday - 28))
        ramp = None if now is None or wk is None else now - wk
        spark = [[self._iso(d), _n(ctl.at(d))] for d in range(self.tday - 90, self.tday + 1, 3)]
        if now is None:
            return Indicator("fitness", "體能 CTL", NA, "–", "沒有訓練資料")
        txt = f"{now:.0f}"
        why = f"CTL {now:.0f}，本週 {ramp:+.1f}/週，4 週 {now - mo:+.0f}" if mo is not None and ramp is not None else f"CTL {now:.0f}"
        level, verdict, action = INFO, "", ""
        k = self.kind
        if ramp is not None:
            if k in ("taper", "event", "recovery"):
                level, verdict = GOOD, "減量／恢復期，體能小幅下降是正常的"
                if ramp > 1:
                    level, verdict, action = WATCH, "減量期 CTL 還在上升，代表量沒有真的減", "把本週時數壓到減量帶內"
            else:
                if ramp >= RAMP["short"]:
                    level, verdict, action = BAD, f"每週 +{ramp:.1f}，Palladino：≥7 是受傷與生病的風險區", "本週維持或減量，不要再加"
                elif ramp >= RAMP["elite"]:
                    level, verdict, action = WATCH, f"每週 +{ramp:.1f}，只能撐一兩週的增幅", "下週安排恢復週"
                elif ramp >= 1:
                    level, verdict = GOOD, f"每週 +{ramp:.1f}，可長期維持的增幅（1–3；菁英 3–5）"
                elif ramp > -1:
                    level, verdict = WATCH, "體能持平" if (k in ("base", "specific")) else "持平"
                    action = "訓練期體能沒有成長：檢查每週時數是否卡住" if k in ("base", "specific") else ""
                else:
                    level, verdict, action = WATCH, f"每週 {ramp:+.1f}，體能在下降", "補回訓練量，或確認是否在恢復"
        return Indicator("fitness", "體能 CTL", level, txt, verdict, why, action, SRC_PALLADINO, now, spark,
                         {"ramp_week": ramp, "delta_28d": None if mo is None else now - mo})

    def i_form(self) -> Indicator:
        tsb = self.ev.evaluate("tsb")
        atl = self.ev.evaluate("atl")
        now, a = _n(tsb.at(self.tday)), _n(atl.at(self.tday))
        if now is None:
            return Indicator("form", "狀況 TSB", NA, "–", "沒有訓練資料")
        spark = [[self._iso(d), _n(tsb.at(d))] for d in range(self.tday - 90, self.tday + 1, 3)]
        txt = f"{now:+.0f}"
        why = f"TSB {now:+.0f}（ATL {a:.0f}）" if a is not None else f"TSB {now:+.0f}"
        k = self.kind
        g = self.goals
        days_to = g["days_to_next_a"]
        if k in ("taper", "event") and days_to is not None and days_to <= 3:
            if TSB_A[0] <= now <= TSB_A[1]:
                lvl, v, act = GOOD, "賽前新鮮度剛好（A 賽目標 +10～+20）", ""
            elif now < TSB_A[0]:
                lvl, v, act = WATCH, "賽前還不夠新鮮", "剩下幾天只做短、輕鬆的活動"
            else:
                lvl, v, act = WATCH, "太新鮮，可能減量太久", "賽前 2 天可以加一次短強度喚醒"
        elif k in ("taper",):
            lvl, v, act = (GOOD, "TSB 正在回升", "") if now > -10 else (WATCH, "減量期 TSB 還很負", "再減量：時數降到平常的 40–60%")
        elif k in ("recovery", "transition"):
            lvl, v, act = (GOOD, "已經恢復", "") if now > 0 else (WATCH, "還沒恢復", "繼續休、不要急著練")
        else:  # base / specific / none
            if now < TSB_OVERREACH:
                lvl, v, act = BAD, "TSB < −30，過度訓練風險", "本週減量到平常的一半，睡眠優先"
            elif TSB_PRODUCTIVE[0] <= now <= TSB_PRODUCTIVE[1]:
                lvl, v, act = GOOD, "−30～−10：有效訓練的區間", ""
            elif now <= 5:
                lvl, v, act = GOOD, "−10～+5：維持中", ""
            elif now <= TSB_STALE:
                lvl, v, act = WATCH, "TSB 偏高，訓練量不足以進步", "把每週量加回來（每週 ≤ +10%）"
            else:
                lvl, v, act = WATCH, "TSB > +25，體能在流失", "恢復規律訓練"
        return Indicator("form", "狀況 TSB", lvl, txt, v, why, act, SRC_TP_TSB, now, spark)

    def i_volume(self) -> Indicator:
        wk = self.weekly_hours(12)
        spark = [[d.isoformat(), round(h, 2)] for d, h in wk]
        this, last = wk[-1][1], wk[-2][1]
        prev4 = [h for _, h in wk[-5:-1]]
        avg4 = _mean(prev4) or 0
        step = None if wk[-3][1] <= 0 else (last - wk[-3][1]) / wk[-3][1]
        base6 = _mean([h for _, h in wk[-8:-2]]) or 0
        txt = f"{last:.1f} h"
        why = f"上週 {last:.1f} h，本週到目前 {this:.1f} h，前 4 週平均 {avg4:.1f} h"
        k = self.kind
        if k in ("taper",):
            lo, hi = base6 * TAPER_BAND[0], base6 * TAPER_BAND[1]
            why += f"；減量帶 {lo:.1f}–{hi:.1f} h（平常 {base6:.1f} h 的 40–59%）"
            if lo <= last <= hi:
                lvl, v, act = GOOD, "上週落在減量帶內", ""
            elif last > hi:
                lvl, v, act = WATCH, "減得不夠", f"本週壓到 {lo:.1f}–{hi:.1f} h，強度和次數維持"
            else:
                lvl, v, act = WATCH, "減太多，體能會流失", f"本週回到 {lo:.1f}–{hi:.1f} h"
        elif k in ("recovery", "transition"):
            lvl, v, act = (GOOD, "量降下來了", "") if last <= base6 * 0.7 else (WATCH, "恢復期量還太多", "本週再降")
        else:
            if step is not None and step > VOLUME_STEP_WATCH * 2:
                lvl, v, act = BAD, f"上週比前一週多 {step * 100:+.0f}%，遠超過 10%", "本週維持上週的量，不要再加"
            elif step is not None and step > VOLUME_STEP_WATCH:
                lvl, v, act = WATCH, f"上週比前一週多 {step * 100:+.0f}%（建議 ≤ 10%）", "本週維持，下週再加"
            elif last < avg4 * 0.6 and avg4 > 1:
                lvl, v, act = WATCH, f"上週只有前 4 週平均的 {last / avg4 * 100:.0f}%", "如果不是刻意恢復，本週補回來"
            else:
                lvl, v, act = GOOD, "量穩定" if step is None or abs(step) < 0.1 else f"週增幅 {step * 100:+.0f}%，在範圍內", ""
        return Indicator("volume", "每週時數", lvl, txt, v, why, act, SRC_UA if k not in ("taper",) else SRC_BOSQUET,
                         last, spark, {"this_week": this, "last_week": last, "avg4": avg4, "step": step})

    def i_intensity(self) -> Indicator:
        low = self.ws("athleterange(today-27, today, sum(if(heartrate < aethr, deltatime)))")
        tot = self.ws("athleterange(today-27, today, sum(if(heartrate > 0, deltatime)))")
        hi = self.ws("athleterange(today-27, today, sum(if(heartrate >= lthr, deltatime)))")
        t_all = sum(tot.values())
        if t_all < 3600:
            return Indicator("intensity", "強度分配", NA, "–", "最近 4 週沒有心率資料")
        share = sum(low.values()) / t_all
        hi_h = sum(hi.values()) / 3600
        # power view for runs (Palladino 3-zone: <80% CP low, ≥95% high)
        plow = self.ws("athleterange(today-27, today, sum(if(power > 0 and power < 0.8*cp, deltatime)))")
        ptot = self.ws("athleterange(today-27, today, sum(if(power > 0, deltatime)))")
        runs = {i for i in ptot if self.ds.workouts[i].sport == "run"}
        p_all = sum(v for i, v in ptot.items() if i in runs)
        pshare = None if p_all < 3600 else sum(v for i, v in plow.items() if i in runs) / p_all
        txt = _pct(share)
        why = f"4 週：低強度（< AeT）{_pct(share)}，高強度（≥ LTHR）{hi_h:.1f} h"
        if pshare is not None:
            why += f"；跑步依功率 < 80% CP 佔 {_pct(pshare)}"
        k = self.kind
        if share >= LOW_SHARE_GOOD:
            lvl, v, act = GOOD, "低強度佔比達標（目標 75–80%）", ""
        elif share >= LOW_SHARE_WATCH:
            lvl, v, act = WATCH, "輕鬆跑有點太快", "把輕鬆跑心率壓在 AeT 以下（{aet} bpm）"
        else:
            lvl, v, act = BAD, "太多時間在中強度（灰色地帶）", "接下來兩週輕鬆跑全部壓在 AeT 以下（{aet} bpm），強度課每週最多一次"
        if k == "taper" and hi_h <= 0.05:
            lvl, v, act = WATCH, "減量期高強度歸零了", "保留一次短的強度課（例：4×3 分鐘 @ 98–102% CP）"
        aet = self._aet_now()
        act = act.format(aet=f"{aet:.0f}" if aet else "AeT")
        return Indicator("intensity", "強度分配", lvl, txt, v, why, act, SRC_SEILER, share,
                         extra={"low_share": share, "high_hours": hi_h, "power_low_share": pshare})

    def _aet_now(self) -> Optional[float]:
        ws = self.since(60, {"run"})
        return self.ds.aethr(ws[-1]) if ws else None

    def i_efficiency(self) -> Indicator:
        eff = self.ws("athleterange(today-181, today, avg(speed)/avg(heartrate))")
        hr = self.ws("athleterange(today-181, today, avg(heartrate))")
        pts = []
        for w in self.between(182, 0):
            if not self.is_road(w) or (self.m(w, "duration") or 0) < 1800:
                continue
            e, h = eff.get(w.idx), hr.get(w.idx)
            aet = self.ds.aethr(w)
            if e is None or h is None or aet is None or h > aet + 3:
                continue                    # only easy runs are comparable
            pts.append((math.floor(w.day), e * 1000 / 60))   # m/min per bpm
        spark = [[self._iso(d), round(v, 3)] for d, v in pts]
        recent = [v for d, v in pts if d >= self.tday - 41]
        before = [v for d, v in pts if self.tday - 181 <= d < self.tday - 41]
        if len(recent) < 3 or len(before) < 3:
            return Indicator("efficiency", "有氧效率 EF", NA, f"{len(recent)} 次",
                             "輕鬆路跑不夠多（6 週內要 ≥ 3 次、前面也要 ≥ 3 次才能比）",
                             "EF = 每下心跳換到的速度，只取心率 ≤ AeT 的路跑", spark=spark, source=SRC_UA)
        r, b = _median(recent), _median(before)
        chg = r / b - 1
        txt = f"{r:.2f}"
        why = f"最近 6 週中位數 {r:.2f}，之前 {b:.2f}（{chg * 100:+.1f}%）"
        if chg >= EF_TREND:
            lvl, v, act = GOOD, "同樣心率跑得更快：有氧引擎在進步", ""
        elif chg <= -EF_TREND:
            lvl, v, act = WATCH, "同樣心率變慢了", "先排除天氣／疲勞；如果持續 4 週以上，增加輕鬆跑的量和長度"
        else:
            lvl, v, act = (WATCH if self.kind == "base" else INFO), "持平", \
                ("基礎期 EF 沒動：拉長輕鬆跑（60–90 分鐘）並確認強度真的低" if self.kind == "base" else "")
        return Indicator("efficiency", "有氧效率 EF", lvl, txt, v, why, act, SRC_UA, r, spark,
                         {"recent": r, "before": b, "change": chg, "n": len(pts)})

    def i_drift(self) -> Indicator:
        pts = []
        for w in self.since(56):
            if not self.is_road(w) or (self.m(w, "duration") or 0) < 2400:
                continue
            d = self.m(w, "pahr")
            if d is None:
                continue
            pts.append((math.floor(w.day), d))
        spark = [[self._iso(d), round(v, 4)] for d, v in pts]
        if len(pts) < 2:
            return Indicator("drift", "心率飄移", NA, "–", "8 週內 > 40 分鐘的路跑不到 2 次", spark=spark, source=SRC_UA)
        med = _median([v for _, v in pts])
        txt = _pct(med, 1)
        why = f"8 週內 {len(pts)} 次 > 40 分鐘路跑，Pa:HR 中位數 {_pct(med, 1)}"
        if med < DRIFT_GOOD:
            lvl, v, act = GOOD, "< 5%：有氧基礎穩", ""
        elif med < DRIFT_WATCH:
            lvl, v, act = WATCH, "5–10%：長跑後段心率往上跑", "長跑再放慢一點，或先做一次 AeT 測試校正"
        else:
            lvl, v, act = BAD, "> 10%：有氧基礎不足或跑太快", "所有輕鬆跑壓在 AeT 以下；暫緩間歇（徐國峰：90 分鐘 E 跑飄移 < 10% 才練間歇）"
        return Indicator("drift", "心率飄移", lvl, txt, v, why, act, SRC_UA, med, spark)

    def i_climb(self) -> Indicator:
        pts = [(math.floor(w.day), self.m(w, "vam")) for w in self.between(182, 0)
               if self.mountain(w) and (self.m(w, "climbing") or 0) > 200 and self.m(w, "vam")]
        spark = [[self._iso(d), round(v, 0)] for d, v in pts]
        recent = [v for d, v in pts if d >= self.tday - 55]
        before = [v for d, v in pts if d < self.tday - 55]
        if len(recent) < 2 or len(before) < 2:
            return Indicator("climb", "爬坡速度 VAM", NA, f"{len(recent)} 次",
                             "8 週內爬升 > 200 m 的越野／登山不到 2 次", spark=spark, source=SRC_KOOP)
        r, b = _mean(recent), _mean(before)
        chg = r / b - 1
        txt = f"{r:.0f} m/h"
        why = f"最近 8 週平均 {r:.0f} m/h（{len(recent)} 次），之前 {b:.0f}（{chg * 100:+.0f}%）"
        if chg >= VAM_TREND:
            lvl, v, act = GOOD, "爬得比之前快", ""
        elif chg <= -VAM_TREND:
            lvl, v, act = (BAD if self.kind == "specific" else WATCH), "爬坡變慢", \
                "每週一次山路長跑或爬坡課（VAM 不是越高越好，和心率一起看）"
        else:
            lvl, v, act = INFO, "持平", ""
        return Indicator("climb", "爬坡速度 VAM", lvl, txt, v, why, act, SRC_KOOP, r, spark)

    def i_long(self) -> Indicator:
        ws = [w for w in self.since(28) if self.mountain(w) or self.is_road(w)]
        best = max(ws, key=lambda w: self.m(w, "movingduration") or 0, default=None)
        longest = None if best is None else (self.m(best, "movingduration") or 0)
        year = [self.m(w, "movingduration") or 0 for w in self.since(365) if self.mountain(w) or self.is_road(w)]
        ymax = max(year) if year else None
        spark = [[self._iso(math.floor(w.day)), round((self.m(w, "movingduration") or 0) / 3600, 2)]
                 for w in self.between(182, 0) if self.mountain(w) or self.is_road(w)]
        g = self.goals["targets"].get("est_hours")
        txt = _hms(longest)
        if longest is None:
            return Indicator("long", "最長單次", NA, "–", "4 週內沒有跑步／登山")
        if g:
            ratio = longest / (g["value"] * 3600)
            why = f"4 週內最長 {_hms(longest)}（{best.sport_type}），目標賽事「{g['event']}」預估 {g['value']:.1f} h → {ratio * 100:.0f}%"
            if self.kind == "specific":
                if ratio >= SPECIFIC_SHARE:
                    lvl, v, act = GOOD, "最長訓練已到比賽的七成以上", ""
                elif ratio >= 0.5:
                    lvl, v, act = WATCH, "最長訓練還不到比賽的七成", f"2 週內安排一次 {g['value'] * SPECIFIC_SHARE:.1f} h 的長天（慢、可以走）"
                else:
                    lvl, v, act = BAD, "最長訓練離比賽太遠", f"接下來每 2 週一次長天，逐步拉到 {g['value'] * SPECIFIC_SHARE:.1f} h"
            elif self.kind == "taper":
                lvl, v, act = (GOOD, "減量期不需要長天", "") if ratio < 0.6 else (WATCH, "減量期還在做長天", "賽前兩週最長不超過 2 小時")
            else:
                lvl, v, act = INFO, "基礎期慢慢加長就好", ""
        else:
            why = f"4 週內最長 {_hms(longest)}，一年內最長 {_hms(ymax)}"
            lvl, v, act = INFO, "沒有目標賽事，只看趨勢", "到「賽事周期」頁填目標賽事的預估時間"
        return Indicator("long", "最長單次", lvl, txt, v, why, act, SRC_CHIANG, longest, spark)

    def i_density(self) -> Indicator:
        ws = [w for w in self.since(28) if self.mountain(w) and (self.m(w, "distance") or 0) > 3]
        dens = [self.m(w, "climbing") / self.m(w, "distance") for w in ws if self.m(w, "climbing") is not None]
        spark = [[self._iso(math.floor(w.day)), round(self.m(w, "climbing") / self.m(w, "distance"), 0)]
                 for w in self.between(182, 0) if self.mountain(w) and (self.m(w, "distance") or 0) > 3
                 and self.m(w, "climbing") is not None]
        g = self.goals["targets"].get("climb_per_km")
        if not dens:
            return Indicator("density", "爬升密度", NA, "–", "4 週內沒有越野／登山", spark=spark, source=SRC_KOOP)
        top = max(dens)
        txt = f"{top:.0f} m/km"
        if g:
            ratio = top / g["value"]
            why = f"4 週內最陡一次 {top:.0f} m/km（平均 {_mean(dens):.0f}），目標「{g['event']}」{g['value']:.0f} m/km → {ratio * 100:.0f}%"
            if self.kind == "specific":
                lvl, v, act = (GOOD, "爬升密度接近比賽", "") if ratio >= SPECIFIC_SHARE else \
                    (WATCH, "訓練路線比比賽平", f"挑每公里爬升 ≥ {g['value'] * SPECIFIC_SHARE:.0f} m 的路線做長跑")
            else:
                lvl, v, act = INFO, "專項期再對照比賽", ""
        else:
            why = f"4 週內最陡一次 {top:.0f} m/km，平均 {_mean(dens):.0f}"
            lvl, v, act = INFO, "沒有目標賽事", ""
        return Indicator("density", "爬升密度", lvl, txt, v, why, act, SRC_KOOP, top, spark)

    def i_strength(self) -> Indicator:
        n = len(self.since(28, {"strength"}))
        per_wk = n / 4
        spark = [[d.isoformat(), len([w for w in self.ds.workouts if w.sport == "strength"
                                       and date_to_day(d) <= math.floor(w.day) <= date_to_day(d) + 6])]
                 for d, _ in self.weekly_hours(12)]
        txt = f"{per_wk:.1f} 次/週"
        why = f"4 週內 {n} 次肌力（手錶有記錄的）"
        if per_wk >= STRENGTH_PER_WEEK:
            lvl, v, act = GOOD, "每週 2 次，達標", ""
        elif per_wk >= 1:
            lvl, v, act = WATCH, "每週不到 2 次", "補到每週 2 次（下肢單腳、核心；你的筆記：膝主導＋臀中肌）"
        else:
            lvl, v, act = (BAD if self.kind in ("transition", "recovery", "base") else WATCH), \
                "幾乎沒有肌力訓練", "每週 2 次 30–40 分鐘；轉換期／基礎期是打底的時候"
        return Indicator("strength", "肌力", lvl, txt, v, why, act, SRC_UA, per_wk, spark)

    def i_durability(self) -> Indicator:
        # Trail runs only: on hikes Pa:HR mixes in rest stops and swings by
        # ±40%, which says nothing about fatigue. |drift| > 30% is treated as
        # a broken reading, not a result.
        pts = [(math.floor(w.day), self.m(w, "pahr")) for w in self.since(84)
               if self.is_trail(w) and (self.m(w, "duration") or 0) > 5400
               and self.m(w, "pahr") is not None and abs(self.m(w, "pahr")) <= 0.30]
        spark = [[self._iso(d), round(v, 4)] for d, v in pts]
        if len(pts) < 2:
            return Indicator("durability", "耐久度", NA, "–", "12 週內 > 90 分鐘的越野跑不到 2 次", spark=spark, source=SRC_KOOP)
        med = _median([v for _, v in pts])
        txt = _pct(med, 1)
        why = f"12 週內 {len(pts)} 次 > 90 分鐘越野跑，後段效率掉幅中位數 {_pct(med, 1)}"
        if med < DRIFT_GOOD:
            lvl, v, act = GOOD, "後段撐得住", ""
        elif med < 0.15:
            lvl, v, act = WATCH, "後段明顯掉", "長天中段加補給（每小時 30–60 g 碳水，你的筆記），並在長天後段練「累了還能維持配速」"
        else:
            lvl, v, act = BAD, "後段掉很多", "拉長時間前先把長天配速放慢；檢查補給與水分"
        return Indicator("durability", "耐久度", lvl, txt, v, why, act, SRC_KOOP, med, spark)

    def i_testing(self) -> Indicator:
        def last(name):
            ds_ = [dt.date.fromisoformat(t.date) for t in self.plan.thresholds if getattr(t, name) is not None]
            return max(ds_) if ds_ else None
        cp, lt, ae = last("cp"), last("lthr"), last("aethr")
        parts, worst = [], GOOD
        for label, d in (("CP", cp), ("LTHR", lt), ("AeT", ae)):
            if d is None:
                parts.append(f"{label} 沒測過")
                worst = BAD
            else:
                age = (self.today - d).days
                parts.append(f"{label} {age} 天前")
                if age > TEST_DAYS_BAD:
                    worst = BAD
                elif age > TEST_DAYS_WATCH and worst != BAD:
                    worst = WATCH
        txt = "OK" if worst == GOOD else "要測"
        why = "；".join(parts)
        days_to = self.goals["days_to_next_a"]
        act = ""
        if worst != GOOD:
            act = "排一次 CP 測試（3'/12'，中間休 30 分鐘）＋ 60 分鐘 AeT 飄移測試；每 4–6 週一次"
            if days_to is not None and 10 <= days_to <= 21:
                act += f"——賽前 {days_to} 天正好是測試的時機（賽前 10–21 天）"
            elif days_to is not None and days_to < 10:
                act = "賽前 10 天內不要測，賽後再測"
                worst = WATCH
        v = "門檻是新的" if worst == GOOD else "門檻過期或沒測，區間和 TSS 都會跟著不準"
        return Indicator("testing", "測試", worst, txt, v, why, act, SRC_NOTES)

    def i_data(self) -> Indicator:
        ath = self.ds.athlete
        hist = ath.settings.get("runthr") or []
        thr_default = bool(hist) and all(d == dt.date(1980, 1, 1) for d, _ in hist) and \
            self.plan.threshold_on("lthr", self.today) is None
        aet_missing = self.plan.threshold_on("aethr", self.today) is None
        cp_src = "測試" if self.plan.threshold_on("cp", self.today) is not None else \
            ("WKO5 mFTP" if self.ds.mftp_run is not None else "WKO5 設定值")
        recent = self.since(28)
        tot = self.ws("athleterange(today-27, today, sum(if(heartrate > 0, deltatime)))")
        with_hr = sum(1 for w in recent if tot.get(w.idx, 0) > 0)
        issues, lvl = [], GOOD
        if thr_default:
            issues.append("LTHR 還是 WKO5 預設值 160")
            lvl = BAD
        if aet_missing:
            issues.append("AeT 用 0.89×LTHR 估")
            lvl = WATCH if lvl == GOOD else lvl
        if recent and with_hr / len(recent) < 0.8:
            issues.append(f"4 週內 {len(recent) - with_hr}/{len(recent)} 筆活動沒心率")
            lvl = WATCH if lvl == GOOD else lvl
        pend = 0
        try:
            from backend.engine.wko5expr.corrections import detect_spikes
            pend = len(detect_spikes(self.ds))
        except Exception:
            pass
        if pend:
            issues.append(f"{pend} 筆功率壞點待核准")
            lvl = WATCH if lvl == GOOD else lvl
        txt = "OK" if not issues else f"{len(issues)} 項"
        why = "；".join(issues) if issues else f"CP 來源：{cp_src}；4 週 {with_hr}/{len(recent)} 筆有心率"
        act = "到「賽事周期」頁填 LTHR／AeT 測試結果" if (thr_default or aet_missing) else ""
        if pend:
            act = (act + "；" if act else "") + "到圖表頁「資料校正」核准壞點修正"
        return Indicator("data", "資料品質", lvl, txt, "資料可信" if not issues else "有幾項會影響判讀", why, act, "",
                         extra={"cp_source": cp_src})

    # ---- recommendations -------------------------------------------------
    def _recommend(self) -> None:
        by = {i.id: i for i in self.indicators}
        acts: list[Action] = []
        rank = {BAD: 0, WATCH: 1}
        k = self.kind or "base"
        # 1. anything broken, ordered by severity then by phase relevance
        order = PHASE_PRIORITY.get(k, PHASE_PRIORITY["base"])
        for i in sorted((i for i in self.indicators if i.level in rank and i.action),
                        key=lambda i: (rank[i.level], order.index(i.id) if i.id in order else 99)):
            acts.append(Action(len(acts) + 1, f"{i.title}：{i.verdict}", i.action, [i.id], i.source))
        # 2. the phase's standing focus, if nothing above already says it
        focus = PHASE_FOCUS[k]
        if not any(focus[0] in a.title for a in acts):
            acts.append(Action(len(acts) + 1, focus[0], focus[1].format(
                aet=self._fmt_aet(), goal_h=self._goal("est_hours"), goal_d=self._goal("climb_per_km")), ["phase"], focus[2]))
        self.actions = acts[:5]

    def _fmt_aet(self) -> str:
        a = self._aet_now()
        return f"{a:.0f}" if a else "AeT"

    def _goal(self, key) -> str:
        g = self.goals["targets"].get(key)
        return "目標" if not g else (f"{g['value']:.1f} h" if key == "est_hours" else f"{g['value']:.0f} m/km")

    # ---- output ------------------------------------------------------------
    def headline(self) -> str:
        p = self.phase
        head = PHASES[p.kind] if p else "未設定周期"
        bad = [i for i in self.indicators if i.level == BAD]
        watch = [i for i in self.indicators if i.level == WATCH]
        if bad:
            return f"{head}：先處理「{bad[0].title}」——{bad[0].verdict}"
        if watch:
            return f"{head}：大致上軌道，注意「{watch[0].title}」——{watch[0].verdict}"
        return f"{head}：一切正常，照計畫練"

    def to_dict(self) -> dict:
        return {
            "today": self.today.isoformat(),
            "phase": None if self.phase is None else {
                "kind": self.phase.kind, "label": PHASES[self.phase.kind],
                "start": self.phase.start, "end": self.phase.end, "goal": PHASE_GOAL[self.phase.kind]},
            "goals": self.goals,
            "headline": self.headline(),
            "indicators": [asdict(i) for i in self.indicators],
            "actions": [asdict(a) for a in self.actions],
            "counts": {lvl: sum(1 for i in self.indicators if i.level == lvl) for lvl in (GOOD, WATCH, BAD, INFO, NA)},
        }


PHASE_GOAL = {
    "transition": "恢復、重建習慣、肌力打底",
    "recovery": "恢復——不追體能，等 TSB 回正",
    "base": "練有氧引擎：大量低強度、EF 往上、飄移 < 5%",
    "specific": "練比賽需要的能力：爬坡、長時間、爬升密度接近賽事",
    "taper": "量減 40–60%、強度保留、TSB 回正",
    "event": "比賽週：短、輕鬆，補給與睡眠",
}

# which indicators matter most in each phase (for ordering the to-do list)
PHASE_PRIORITY = {
    "transition": ["form", "volume", "strength", "data", "testing"],
    "recovery": ["form", "volume", "strength", "data", "testing"],
    "base": ["intensity", "drift", "volume", "efficiency", "fitness", "strength", "testing", "data"],
    "specific": ["long", "density", "climb", "durability", "fitness", "intensity", "testing", "data"],
    "taper": ["volume", "intensity", "form", "long", "testing"],
    "event": ["form", "volume", "intensity"],
}

# standing focus per phase: (title, detail template, source)
PHASE_FOCUS = {
    "transition": ("轉換期重點", "每週 2 次肌力、量低而穩定，等 TSB 回正再進基礎期", SRC_UA),
    "recovery": ("恢復期重點", "先休；TSB 回正、想練了再開始", SRC_UA),
    "base": ("基礎期重點", "每週一次 60–90 分鐘輕鬆長跑（心率 < {aet}），其餘輕鬆跑也壓在 AeT 以下；每週一次 8–15 秒坡衝刺（Palladino 基礎中期）", SRC_UA),
    "specific": ("專項期重點", "每週一次山路長跑，每公里爬升往 {goal_d} 靠；每 2 週一次長天往 {goal_h} 的七成靠；週中一次爬坡課", SRC_KOOP),
    "taper": ("減量期重點", "時數減到平常的 40–60%，次數不變，保留一次短強度；最後 3 天只做 30–40 分鐘輕鬆跑", SRC_BOSQUET),
    "event": ("比賽週", "前 2 天各 20–30 分鐘輕鬆跑＋幾趟加速；補給照你的筆記：每小時 30–60 g 碳水、每 15 分鐘 200 ml", SRC_NOTES),
}


def compute_status(ds: Dataset, plan: Optional[Plan] = None, today: Optional[dt.date] = None) -> dict:
    return Status(ds, plan, today).compute().to_dict()
