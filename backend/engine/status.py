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
import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Optional

from backend.engine.planning import KINDS, PHASES, Plan, goals, phase_on
from backend.engine.wko5expr.dataset import Dataset, Workout, date_to_day
from backend.engine.wko5expr.evaluator import WS, Evaluator
from backend.files.wko5_athlete import day_to_date
from backend.i18n import N_, _

GOOD, WATCH, BAD, INFO, NA = "good", "watch", "bad", "info", "na"

# ---- thresholds, each with its source --------------------------------------
SRC_PALLADINO = N_("Palladino（你的筆記：PMC 訓練負荷 / Ramp rate）")
SRC_TP_TSB = N_("Friel／TrainingPeaks（Simmons 2020）TSB 區間：−10～−30 有效訓練、< −30 過度（教練）；Palladino A/B/C 賽 TSB")
# weekly volume step: > 20 % = the risk line (Nielsen et al. 2014 JOSPT 44:739, DOI 10.2519/jospt.2014.5164;
# Damsted et al. 2019 JOSPT 49:230, DOI 10.2519/jospt.2019.8541 — peer-reviewed); 10–20 % hold = 推估, conservative.
# The 「10 % 法則」 itself has no evidence (unsourced-rules.md §B2)
SRC_VOLUME = N_("週增量 > 20%：Nielsen 2014、Damsted 2019（同儕審查：增 20–30% 以上受傷風險升高）；"
              "10–20% 先維持：推估（保守）")
SRC_UA = "Uphill Athlete"
SRC_SEILER = N_("Seiler 2006 強度分配；Palladino 金字塔 70–90% 輕鬆")
SRC_BOSQUET = N_("Bosquet 2007 減量統合分析")
SRC_KOOP = N_("Koop《Training Essentials for Ultrarunning》")
SRC_CHIANG = N_("江晏慶（你的筆記：越野跑周期化訓練）")
SRC_NOTES = N_("你的筆記")

# CTL/week. warn 5 / block 8: Friel (coach, https://joefrieltraining.com/the-ctl-ramp-rate/ — 5–8 suits
# most athletes, 10 is the ceiling; unsourced-rules.md §B2). "sustain" 3 = Palladino's 1–3 long-term (display)
RAMP = {"sustain": 3.0, "elite": 5.0, "short": 8.0}
SRC_RAMP_FRIEL = N_("Friel：CTL ramp 每週 5–8 適合多數人、10 是上限（教練）；Palladino：每週 +1–3 可長期維持")
TSB_A = (10.0, 20.0)                                        # A race, taper end (Palladino)
TSB_PRODUCTIVE = (-30.0, -10.0)                             # Friel: productive training
TSB_OVERREACH = -30.0
TSB_STALE = 25.0
LOW_SHARE_GOOD, LOW_SHARE_WATCH = 0.75, 0.65                 # Seiler / Palladino
VOLUME_STEP_WATCH = 0.10                                    # 推估 hold band 10–20 %; > 20 % block (SRC_VOLUME)
TAPER_BAND = (0.40, 0.59)                                   # Bosquet: -41…-60%
DRIFT_GOOD, DRIFT_WATCH = 0.05, 0.10                         # Friel (<5%), 徐國峰 (90' E <10%)
SRC_FRIEL = N_("Friel（TrainingPeaks：Aerobic decoupling < 5%）；徐國峰（90 分鐘 E 跑 < 10%）")
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


def tsb_zone(tsb: Optional[float]) -> Optional[str]:
    """The TSB zone code (TrainingPeaks' names in English): high_risk < −30,
    optimal −30…−10, grey_zone −10…+5, fresh +5…+25, transition > +25."""
    if tsb is None:
        return None
    if tsb < TSB_OVERREACH:
        return "high_risk"
    if tsb <= TSB_PRODUCTIVE[1]:
        return "optimal"
    if tsb <= 5:
        return "grey_zone"
    if tsb <= TSB_STALE:
        return "fresh"
    return "transition"


def _src(s: str) -> str:
    """A source line in the request's language (an already translated / plain-ASCII one stays)."""
    return _(s) if s and _CJK.search(s) else s


_CJK = re.compile(r"[一-鿿]")


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

    def __init__(self, ds: Dataset, plan: Optional[Plan] = None, today: Optional[dt.date] = None, prefs=None):
        self.ds = ds
        self.prefs = prefs                  # 課表偏好 (間歇門檻); None = plan_prefs.load()
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
        """[(monday, moving hours)] for the last `weeks` weeks incl. the current
        one. Moving time, not recorded time: a multi-day 百岳 file records the
        nights too (one 51 h trip had about 7 h of walking)."""
        monday = self.today - dt.timedelta(days=self.today.weekday())
        out = []
        for i in range(weeks - 1, -1, -1):
            s = monday - dt.timedelta(weeks=i)
            e = s + dt.timedelta(days=6)
            lo, hi = date_to_day(s), date_to_day(e)
            h = sum((self.m(w, "movingduration") or self.m(w, "duration") or 0) for w in self.ds.workouts
                    if lo <= math.floor(w.day) <= hi) / 3600
            out.append((s, h))
        return out

    # ---- indicators ------------------------------------------------------
    def compute(self) -> "Status":
        self.indicators = []
        self.actions = []
        for fn in (self.i_phase, self.i_fitness, self.i_form, self.i_volume, self.i_intensity,
                   self.i_efficiency, self.i_drift, self.i_gate, self.i_climb, self.i_long, self.i_density,
                   self.i_descent, self.i_strength, self.i_durability, self.i_heat, self.i_testing, self.i_data):
            try:
                ind = fn()
            except Exception as e:  # one broken indicator must not hide the rest
                ind = Indicator(fn.__name__[2:], fn.__name__[2:], NA, "–",
                                _("計算失敗：{err_type}: {err}", err_type=type(e).__name__, err=e))
            if ind is not None:
                self.indicators.append(ind)
        self._recommend()
        return self

    def i_heat(self) -> Indicator:
        """熱適應 (docs/research/heat-acclimation.md §5.3): the index S from
        the per-activity heat exposure (engine/heat.py, heat_data.py). Level:
        acclimatised good, partial watch, none info; bad only when a hot A/B
        race is within 30 days and its projected S < 0.75 (推估)."""
        from backend.engine import heat as HT
        from backend.engine import heat_data as HD
        from backend.engine import heat_plan as HP
        acts, meta = HD.exposures()
        passive = HD.completed_passive_dates()
        acts = acts + [{"date": d, "hot_min": HT.MIN_DOSE_MIN} for d in passive]
        cur = HT.current(acts, self.today)
        s = cur["s"]
        lv = cur["level"]
        level = {"acclimatised": GOOD, "partial": WATCH}.get(lv["id"], INFO)
        hr = HP.hot_race(self.plan.events, self.today, acts)
        s_race, action = None, ""
        if hr:
            e = hr["event"]
            pj = HT.project(s, self.today, e.start)
            s_race = {"center": pj["center"], "low": pj["low"], "high": pj["high"], "event": e.name, "date": e.date}
            if pj["center"] < HT.LEVELS[0][1]:
                level = BAD
                start = e.start - dt.timedelta(days=HP.INDUCT[0])
                action = _("{name}（{date}）預估是熱天，比賽日預估 S {s_center:.0%}：建議 "
                           "{month}/{day} 起排 5–10 天熱適應課（課表會自動排，見排課頁）",
                           name=e.name, date=e.date, s_center=pj['center'], month=start.month, day=start.day)
        verdict = (_("近 14 天有 {n} 天熱暴露", n=cur['days_14']) +
                   (_("，最後一次 {n} 天前", n=cur['since_last_d']) if cur["since_last_d"] is not None
                    else _("，沒有熱暴露紀錄")))
        if meta.get("missing"):
            verdict = _("還沒有每筆活動的歷史天氣（路線頁重建一次、含天氣）：S 當作 0")
            level = INFO if level != BAD else level
        # HRC observation (§2.3): does the HR cost of heat fall as S rises?
        hrc = None
        try:
            if acts:
                rows = HT.hr_cost(HD.steady_segments(self.ds, self.today, acts))
                tr = HT.hrc_trend(rows["rows"], self.today)
                s28 = HT.mean_s(cur["series"], self.today - dt.timedelta(days=55), self.today - dt.timedelta(days=28))
                disagree = (tr["recent"] is not None and tr["before"] is not None and s28 is not None
                            and s > s28 + 0.05 and tr["recent"] >= tr["before"])
                hrc = {**tr, "noise_bpm": rows["noise_bpm"], "rows": rows["rows"][-40:], "disagrees": disagree}
                if disagree:
                    verdict += _("；觀測不支持模型（S 在升、熱天心率成本沒降）")
        except Exception:                   # noqa: BLE001
            hrc = None
        spark = [[d.isoformat(), round(v, 3)] for d, v in cur["series"][-120:]]
        doses = [[d.isoformat(), round(v, 2)] for d, v in sorted(cur["doses"].items())
                 if d >= self.today - dt.timedelta(days=119)]
        return Indicator("heat", _("熱適應"), level, _("{s:.0%}（{label}）", s=s, label=_(lv['label'])), verdict,
                         _("每天 Hadley ≥ {hot_hadley:.0f} 的移動分鐘（130–150 部分計入），≥ {min_dose:.0f} 分 = 滿劑量；"
                           "有暴露 S += {k_in:.3f}·劑量·(1 − S)，沒有 S × (1 − {decay})（Daanen 2018 每天 2.3–2.6 %）。"
                           "S 的模型是推估；你的心率資料目前不支持「夏末熱懲罰比初夏小」",
                           hot_hadley=HT.HOT_HADLEY, min_dose=HT.MIN_DOSE_MIN, k_in=HT.K_IN, decay=HT.DECAY),
                         action, source=_("Pandolf 1998；Racinais 2015 共識；Daanen 2018；模型 [推估]"), value=s,
                         spark=spark,
                         extra={"s_race": s_race, "hrc": hrc, "a": HT.A_RECOVER, "a_range": list(HT.A_RANGE), "doses": doses,
                                "badge": _("推估"), "source": meta.get("attribution"), "passive": passive[-20:],
                                "hot_race": ({"name": hr["event"].name, "date": hr["event"].date,
                                              "source": hr["source"]} if hr else None)})

    def i_phase(self) -> Indicator:
        p = self.phase
        g = self.goals
        nxt = next((e for e in self.plan.events if e.id == g["next_a"]), None)
        if p is None:
            return Indicator("phase", _("周期"), INFO, _("未設定"),
                             _("還沒有目標賽事，先當作基礎期來看"),
                             _("到「賽事周期」頁加一場 A 賽事，周期會自動排出來"), source=SRC_UA)
        left = (dt.date.fromisoformat(p.end) - self.today).days
        txt = _(PHASES[p.kind])
        if nxt:
            why = _("{start} ～ {end}，還有 {left} 天；下一場 A 賽事「{name}」倒數 {days} 天",
                    start=p.start, end=p.end, left=left, name=nxt.name, days=g['days_to_next_a'])
        else:
            why = _("沒有接下來的 A 賽事，預設為基礎期；到「賽事周期」頁加一場，周期會自動排出來")
        return Indicator("phase", _("周期"), INFO, txt, _(PHASE_GOAL[p.kind]), why,
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
            return Indicator("fitness", _("體能 CTL"), NA, "–", _("沒有訓練資料"))
        txt = f"{now:.0f}"
        why = _("CTL {now:.0f}，本週 {ramp:+.1f}/週，4 週 {delta:+.0f}", now=now, ramp=ramp, delta=now - mo) \
            if mo is not None and ramp is not None else f"CTL {now:.0f}"
        level, verdict, action = INFO, "", ""
        k = self.kind
        if ramp is not None:
            if k in ("taper", "event", "recovery"):
                level, verdict = GOOD, _("減量／恢復期，體能小幅下降是正常的")
                if ramp > 1:
                    level, verdict, action = WATCH, _("減量期 CTL 還在上升，代表量沒有真的減"), _("把本週時數壓到減量帶內")
            else:
                if ramp >= RAMP["short"]:
                    level, verdict, action = (BAD, _("每週 +{ramp:.1f}，≥ {short:.0f} 超過 Friel 建議的 5–8",
                                                     ramp=ramp, short=RAMP['short']),
                                              _("本週維持或減量，不要再加"))
                elif ramp >= RAMP["elite"]:
                    level, verdict, action = (WATCH, _("每週 +{ramp:.1f}（5–8：Friel 的上段），只能撐一兩週", ramp=ramp),
                                              _("下週安排恢復週"))
                elif ramp >= 1:
                    level, verdict = GOOD, _("每週 +{ramp:.1f}，可長期維持的增幅（1–3；菁英 3–5）", ramp=ramp)
                elif ramp > -1:
                    level, verdict = WATCH, _("體能持平") if (k in ("base", "specific")) else _("持平")
                    action = _("訓練期體能沒有成長：檢查每週時數是否卡住") if k in ("base", "specific") else ""
                else:
                    level, verdict, action = (WATCH, _("每週 {ramp:+.1f}，體能在下降", ramp=ramp),
                                              _("補回訓練量，或確認是否在恢復"))
        return Indicator("fitness", _("體能 CTL"), level, txt, verdict, why, action, SRC_RAMP_FRIEL, now, spark,
                         {"ramp_week": ramp, "delta_28d": None if mo is None else now - mo})

    def i_form(self) -> Indicator:
        tsb = self.ev.evaluate("tsb")
        atl = self.ev.evaluate("atl")
        now, a = _n(tsb.at(self.tday)), _n(atl.at(self.tday))
        if now is None:
            return Indicator("form", _("狀況 TSB"), NA, "–", _("沒有訓練資料"))
        spark = [[self._iso(d), _n(tsb.at(d))] for d in range(self.tday - 90, self.tday + 1, 3)]
        txt = f"{now:+.0f}"
        why = _("TSB {tsb:+.0f}（ATL {atl:.0f}）", tsb=now, atl=a) if a is not None else f"TSB {now:+.0f}"
        k = self.kind
        g = self.goals
        days_to = g["days_to_next_a"]
        if k in ("taper", "event") and days_to is not None and days_to <= 3:
            if TSB_A[0] <= now <= TSB_A[1]:
                lvl, v, act = GOOD, _("賽前新鮮度剛好（A 賽目標 +10～+20）"), ""
            elif now < TSB_A[0]:
                lvl, v, act = WATCH, _("賽前還不夠新鮮"), _("剩下幾天只做短、輕鬆的活動")
            else:
                lvl, v, act = WATCH, _("太新鮮，可能減量太久"), _("賽前 2 天可以加一次短強度喚醒")
        elif k in ("taper",):
            lvl, v, act = (GOOD, _("TSB 正在回升"), "") if now > -10 else \
                (WATCH, _("減量期 TSB 還很負"), _("再減量：時數降到平常的 40–60%"))
        elif k in ("recovery", "transition"):
            lvl, v, act = (GOOD, _("已經恢復"), "") if now > 0 else (WATCH, _("還沒恢復"), _("繼續休、不要急著練"))
        else:  # base / specific / none
            if now < TSB_OVERREACH:
                lvl, v, act = BAD, _("TSB < −30，過度訓練風險"), _("本週減量到平常的一半，睡眠優先")
            elif TSB_PRODUCTIVE[0] <= now <= TSB_PRODUCTIVE[1]:
                lvl, v, act = GOOD, _("−30～−10：有效訓練的區間"), ""
            elif now <= 5:
                lvl, v, act = GOOD, _("−10～+5：維持中"), ""
            elif now <= TSB_STALE:
                lvl, v, act = WATCH, _("TSB 偏高，訓練量不足以進步"), _("把每週量加回來（每週 ≤ +10%）")
            else:
                lvl, v, act = WATCH, _("TSB > +25，體能在流失"), _("恢復規律訓練")
        return Indicator("form", _("狀況 TSB"), lvl, txt, v, why, act, SRC_TP_TSB, now, spark,
                         {"zone": tsb_zone(now)})

    def i_volume(self) -> Indicator:
        wk = self.weekly_hours(12)
        spark = [[d.isoformat(), round(h, 2)] for d, h in wk]
        this, last = wk[-1][1], wk[-2][1]
        prev4 = [h for _, h in wk[-5:-1]]
        avg4 = _mean(prev4) or 0
        step = None if wk[-3][1] <= 0 else (last - wk[-3][1]) / wk[-3][1]
        base6 = _mean([h for _, h in wk[-8:-2]]) or 0
        txt = f"{last:.1f} h"
        why = _("上週 {last:.1f} h，本週到目前 {this:.1f} h，前 4 週平均 {avg4:.1f} h", last=last, this=this, avg4=avg4)
        k = self.kind
        if k in ("taper",):
            lo, hi = base6 * TAPER_BAND[0], base6 * TAPER_BAND[1]
            why += _("；減量帶 {lo:.1f}–{hi:.1f} h（平常 {base6:.1f} h 的 40–59%）", lo=lo, hi=hi, base6=base6)
            if lo <= last <= hi:
                lvl, v, act = GOOD, _("上週落在減量帶內"), ""
            elif last > hi:
                lvl, v, act = WATCH, _("減得不夠"), _("本週壓到 {lo:.1f}–{hi:.1f} h，強度和次數維持", lo=lo, hi=hi)
            else:
                lvl, v, act = WATCH, _("減太多，體能會流失"), _("本週回到 {lo:.1f}–{hi:.1f} h", lo=lo, hi=hi)
        elif k in ("recovery", "transition"):
            lvl, v, act = (GOOD, _("量降下來了"), "") if last <= base6 * 0.7 else \
                (WATCH, _("恢復期量還太多"), _("本週再降"))
        else:
            if step is not None and step > VOLUME_STEP_WATCH * 2:
                lvl, v, act = (BAD, _("上週比前一週多 {step:+.0f}%（> 20%：Nielsen 2014、Damsted 2019 的受傷風險線）",
                                      step=step * 100),
                               _("本週維持上週的量，不要再加"))
            elif step is not None and step > VOLUME_STEP_WATCH:
                lvl, v, act = (WATCH, _("上週比前一週多 {step:+.0f}%（10–20%：先維持，推估）", step=step * 100),
                               _("本週維持，下週再加"))
            elif last < avg4 * 0.6 and avg4 > 1:
                lvl, v, act = (WATCH, _("上週只有前 4 週平均的 {pct:.0f}%", pct=last / avg4 * 100),
                               _("如果不是刻意恢復，本週補回來"))
            else:
                lvl, v, act = GOOD, _("量穩定") if step is None or abs(step) < 0.1 else \
                    _("週增幅 {step:+.0f}%，在範圍內", step=step * 100), ""
        return Indicator("volume", _("每週時數"), lvl, txt, v, why, act, SRC_VOLUME if k not in ("taper",) else SRC_BOSQUET,
                         last, spark, {"this_week": this, "last_week": last, "avg4": avg4, "step": step})

    def i_intensity(self) -> Indicator:
        low = self.ws("athleterange(today-27, today, sum(if(heartrate < aethr, deltatime)))")
        tot = self.ws("athleterange(today-27, today, sum(if(heartrate > 0, deltatime)))")
        hi = self.ws("athleterange(today-27, today, sum(if(heartrate >= lthr, deltatime)))")
        t_all = sum(tot.values())
        if t_all < 3600:
            return Indicator("intensity", _("強度分配"), NA, "–", _("最近 4 週沒有心率資料"))
        share = sum(low.values()) / t_all
        hi_h = sum(hi.values()) / 3600
        # power view for runs (Palladino 3-zone: <80% CP low, ≥95% high)
        plow = self.ws("athleterange(today-27, today, sum(if(power > 0 and power < 0.8*cp, deltatime)))")
        ptot = self.ws("athleterange(today-27, today, sum(if(power > 0, deltatime)))")
        runs = {i for i in ptot if self.ds.workouts[i].sport == "run"}
        p_all = sum(v for i, v in ptot.items() if i in runs)
        pshare = None if p_all < 3600 else sum(v for i, v in plow.items() if i in runs) / p_all
        txt = _pct(share)
        why = _("4 週：低強度（< AeT）{low}，高強度（≥ LTHR）{hi_h:.1f} h", low=_pct(share), hi_h=hi_h)
        if pshare is not None:
            why += _("；跑步依功率 < 80% CP 佔 {share}", share=_pct(pshare))
        k = self.kind
        if share >= LOW_SHARE_GOOD:
            lvl, v, act = GOOD, _("低強度佔比達標（目標 75–80%）"), ""
        elif share >= LOW_SHARE_WATCH:
            lvl, v, act = WATCH, _("輕鬆跑有點太快"), N_("把輕鬆跑心率壓在 AeT 以下（{aet} bpm）")
        else:
            lvl, v, act = BAD, _("太多時間在中強度（灰色地帶）"), \
                N_("接下來兩週輕鬆跑全部壓在 AeT 以下（{aet} bpm），強度課每週最多一次")
        if k == "taper" and hi_h <= 0.05:
            lvl, v, act = WATCH, _("減量期高強度歸零了"), N_("保留一次短的強度課（例：4×3 分鐘 @ 98–102% CP）")
        aet = self._aet_now()
        act = _(act, aet=f"{aet:.0f}" if aet else "AeT") if act else act
        return Indicator("intensity", _("強度分配"), lvl, txt, v, why, act, SRC_SEILER, share,
                         extra={"low_share": share, "high_hours": hi_h, "power_low_share": pshare})

    def _aet_now(self) -> Optional[float]:
        # the AeT in effect today (a plan row dated after the last run counts)
        from backend.engine.wko5expr.dataset import date_to_day
        from backend.engine.zones import _on_day
        ws = self.since(60, {"run"})
        return self.ds.aethr(_on_day(ws[-1], math.floor(date_to_day(self.today)))) if ws else None

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
            return Indicator("efficiency", _("有氧效率 EF"), NA, _("{n} 次", n=len(recent)),
                             _("輕鬆路跑不夠多（6 週內要 ≥ 3 次、前面也要 ≥ 3 次才能比）"),
                             _("EF = 每下心跳換到的速度，只取心率 ≤ AeT 的路跑"), spark=spark, source=SRC_UA)
        r, b = _median(recent), _median(before)
        chg = r / b - 1
        txt = f"{r:.2f}"
        why = _("最近 6 週中位數 {r:.2f}，之前 {b:.2f}（{chg:+.1f}%）", r=r, b=b, chg=chg * 100)
        if chg >= EF_TREND:
            lvl, v, act = GOOD, _("同樣心率跑得更快：有氧引擎在進步"), ""
        elif chg <= -EF_TREND:
            lvl, v, act = WATCH, _("同樣心率變慢了"), _("先排除天氣／疲勞；如果持續 4 週以上，增加輕鬆跑的量和長度")
        else:
            lvl, v, act = (WATCH if self.kind == "base" else INFO), _("持平"), \
                (_("基礎期 EF 沒動：拉長輕鬆跑（60–90 分鐘）並確認強度真的低") if self.kind == "base" else "")
        return Indicator("efficiency", _("有氧效率 EF"), lvl, txt, v, why, act, SRC_UA, r, spark,
                         {"recent": r, "before": b, "change": chg, "n": len(pts)})

    def i_drift(self) -> Indicator:
        # Informational (docs/research/aerobic-base-readiness.md §4.3): the same
        # per-run drift the 單次活動 review card shows (workout_review: road, ≥ 40
        # min after the 10-min warm-up, avg HR ≤ AeT+3; hilly / stopped / unsteady /
        # fast-finish runs refused; heat is a band, not a refusal). It is not
        # the interval gate any more — that is i_gate (engine/quality_gate.py).
        # Display only, so the 參考 tier counts too (workout_review.DRIFT_REF_MIN_S:
        # 30–40 min after the warm-up, 推估), labelled; the gate reads the strict tier.
        # drift v2 (docs/research/drift-algorithm.md §1.3, §5.4): one run is ±4–6 pp, so the
        # number shown is the mean ± SE of the last 6 fair runs (engine/drift_agg.py), not a
        # verdict on one run; the single runs stay in the spark.
        # heat bands (workout_review.temp_band): runs are compared only within one temperature
        # band (< 25 / 25–28 / > 28 °C, 推估 cut-offs) — the number is the aggregate of the band
        # of the latest fair run (the season the athlete is in), or of the band with the most runs
        # when that one has < 2; the other bands' aggregates ride along in extra["bands"].
        from backend.engine import drift_agg as DA
        from backend.engine import workout_review as WR
        pts = WR.drift_series(self.ds, self.today, ref=True)
        all_fair = [p for p in pts if p["drift"] is not None]
        by_band = {}
        for p in all_fair:
            by_band.setdefault(p.get("band") or "none", []).append(p)
        band = DA.pick_band(all_fair)
        fair = by_band.get(band, []) if band else []
        bands = {b: {"n": len(v), "agg": DA.aggregate(v), "label": _(WR.TEMP_BAND_LABEL.get(b, b))}
                 for b, v in by_band.items()}
        chip = "🌡 " + _(WR.TEMP_BAND_LABEL.get(band, N_("溫度不明"))) if band else ""
        heat = WR.is_heat(band)
        n_ref = sum(1 for p in fair if p.get("tier") == "ref")
        spark = [[p["date"], round(p["drift"], 4)] for p in fair]
        note = _("飄移是 AeT 測試用的，不是間歇門檻")
        ref_extra = {"ref": n_ref, "test": len(fair) - n_ref, "ref_label": _(WR.REF_LABEL), "ref_tip": _(WR.REF_TIP),
                     "band": band, "band_label": _(WR.TEMP_BAND_LABEL.get(band)) if band else None, "chip": chip,
                     "heat": heat, "bands": bands, "band_tip": _(WR.HEAT_TIP)}
        agg = DA.aggregate(fair)
        list_sep = _("、")
        if len(fair) < DA.AGG_MIN:
            other = (_("；分溫度區：") + list_sep.join(_("{label} {n} 次", label=_(WR.TEMP_BAND_LABEL.get(b, b)), n=x['n'])
                                                    for b, x in bands.items())
                     if len(bands) > 1 else "")
            return Indicator("drift", _("心率飄移"), NA, "–",
                             _("8 週內同一溫度區可判讀的輕鬆路跑不到 2 次（{n} 次符合條件{other}）", n=len(pts), other=other),
                             _("只算路跑、暖身後還有 ≥ 40 分鐘（30–40 分算參考）、平均心率 ≤ AeT+3；回程市區段當緩和、"
                               "結尾靜止裁掉；有坡、有停頓、跑走、功率起伏（VI > 1.04）、前後半功率差 > 5%、快速結尾"
                               "的不採用；只和同一溫度區（< 25／25–28／> 28 °C，推估）的跑步比"),
                             "", SRC_FRIEL, spark=spark,
                             extra={"fair": len(fair), "median": None, "agg": agg, **ref_extra})
        med = _median([p["drift"] for p in fair])
        mean = agg["mean"]
        txt = (_pct(mean, 1) + f" ±{agg['se'] * 100:.1f}" +
               (_("（參考）") if n_ref == len(fair) else _("（含參考）") if n_ref else "") + f" · {chip}")
        mix = (_("，其中 {n} 次是{label}", n=n_ref, label=_(WR.REF_LABEL)) if n_ref else "")
        others = [_("{label} {n} 次", label=x['label'], n=x['n']) for b, x in bands.items() if b != band]
        why = (_("8 週內 {chip} 有 {n} 次可判讀的輕鬆路跑{mix}；最近 {n_agg} 次平均 Pa:HR {agg}",
                 chip=chip, n=len(fair), mix=mix, n_agg=agg['n'], agg=DA.text(agg))
               + _("（單次 ±4–6 個百分點，所以看平均，不判單次；中位數 {median}）", median=_pct(med, 1))
               + (_("；只和同一溫度區比，其他區另計（{others}）", others=list_sep.join(others)) if others else "")
               + (_("；{note}", note=_(WR.HEAT_NOTE)) if heat else "") + _("；{note}", note=note))
        med = mean
        if med < DRIFT_GOOD:
            lvl, v, act = INFO, _("< 5%：輕鬆跑後段心率穩"), ""
        elif med < DRIFT_WATCH:
            lvl, v, act = INFO, _("5–10%：長跑後段心率往上跑"), ""
        else:
            lvl, v, act = BAD, _("> 10%：輕鬆跑太快（或太熱、沒補給）"), _("所有輕鬆跑壓在 AeT 以下")
        # the level feeds the base-phase guardrail (overview gate levels,
        # quality_gate): BAD only on the strict tier's own median (≥ 2 test runs), and
        # not in a heat band — heat inflates the drift (Lafrenz 2008), so > 10 % there may be the heat
        test = [p["drift"] for p in fair if p.get("tier") != "ref"]
        if lvl == BAD and not (len(test) >= 2 and _median(test) >= DRIFT_WATCH):
            lvl, act = INFO, ""
            v = _("> 10%（參考值為主，不當警示）：輕鬆跑可能太快")
        elif lvl == BAD and heat:
            lvl, act = INFO, ""
            v = _("> 10%（{note}，不當警示）：輕鬆跑可能太快，涼一點的日子再看", note=_(WR.HEAT_NOTE))
        return Indicator("drift", _("心率飄移"), lvl, txt, v, why, act, SRC_FRIEL, med, spark,
                         {"fair": len(fair), "median": _median([p["drift"] for p in fair]), "agg": agg,
                          **ref_extra})

    def i_gate(self) -> Indicator:
        # 間歇門檻 (engine/quality_gate.py; docs/research/aerobic-base-readiness.md §4)
        from backend.engine import quality_gate as QG
        prefs = self.prefs
        if prefs is None:
            from backend.engine import plan_prefs as PP
            prefs = PP.load()
        by = {i.id: i for i in self.indicators}
        g = QG.evaluate(self.ds, self.plan, self.today, prefs, by, self.phase)   # no phase = base
        t = QG.indicator(g)
        return Indicator("gate", _("間歇門檻"), t["level"], t["text"], t["verdict"], t["why"], t["action"],
                         t["source"], extra=g)

    def i_climb(self) -> Indicator:
        pts = [(math.floor(w.day), self.m(w, "vam")) for w in self.between(182, 0)
               if self.mountain(w) and (self.m(w, "climbing") or 0) > 200 and self.m(w, "vam")]
        spark = [[self._iso(d), round(v, 0)] for d, v in pts]
        recent = [v for d, v in pts if d >= self.tday - 55]
        before = [v for d, v in pts if d < self.tday - 55]
        if len(recent) < 2 or len(before) < 2:
            return Indicator("climb", _("爬坡速度 VAM"), NA, _("{n} 次", n=len(recent)),
                             _("8 週內爬升 > 200 m 的越野／登山不到 2 次"), spark=spark, source=SRC_KOOP)
        r, b = _mean(recent), _mean(before)
        chg = r / b - 1
        txt = f"{r:.0f} m/h"
        why = _("最近 8 週平均 {r:.0f} m/h（{n} 次），之前 {b:.0f}（{chg:+.0f}%）", r=r, n=len(recent), b=b, chg=chg * 100)
        if chg >= VAM_TREND:
            lvl, v, act = GOOD, _("爬得比之前快"), ""
        elif chg <= -VAM_TREND:
            lvl, v, act = (BAD if self.kind == "specific" else WATCH), _("爬坡變慢"), \
                _("每週一次山路長跑或爬坡課（VAM 不是越高越好，和心率一起看）")
        else:
            lvl, v, act = INFO, _("持平"), ""
        return Indicator("climb", _("爬坡速度 VAM"), lvl, txt, v, why, act, SRC_KOOP, r, spark)

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
            return Indicator("long", _("最長單次"), NA, "–", _("4 週內沒有跑步／登山"))
        if g:
            ratio = longest / (g["value"] * 3600)
            why = _("4 週內最長 {longest}（{sport}），目標賽事「{event}」預估 {hours:.1f} h → {pct:.0f}%",
                    longest=_hms(longest), sport=best.sport_type, event=g['event'], hours=g['value'], pct=ratio * 100)
            if self.kind == "specific":
                if ratio >= SPECIFIC_SHARE:
                    lvl, v, act = GOOD, _("最長訓練已到比賽的七成以上"), ""
                elif ratio >= 0.5:
                    lvl, v, act = (WATCH, _("最長訓練還不到比賽的七成"),
                                   _("2 週內安排一次 {hours:.1f} h 的長天（慢、可以走）", hours=g['value'] * SPECIFIC_SHARE))
                else:
                    lvl, v, act = (BAD, _("最長訓練離比賽太遠"),
                                   _("接下來每 2 週一次長天，逐步拉到 {hours:.1f} h", hours=g['value'] * SPECIFIC_SHARE))
            elif self.kind == "taper":
                lvl, v, act = (GOOD, _("減量期不需要長天"), "") if ratio < 0.6 else \
                    (WATCH, _("減量期還在做長天"), _("賽前兩週最長不超過 2 小時"))
            else:
                lvl, v, act = INFO, _("基礎期慢慢加長就好"), ""
        else:
            why = _("4 週內最長 {longest}，一年內最長 {year_max}", longest=_hms(longest), year_max=_hms(ymax))
            lvl, v, act = INFO, _("沒有目標賽事，只看趨勢"), _("到「賽事周期」頁填目標賽事的預估時間")
        return Indicator("long", _("最長單次"), lvl, txt, v, why, act, SRC_CHIANG, longest, spark)

    def i_density(self) -> Indicator:
        ws = [w for w in self.since(28) if self.mountain(w) and (self.m(w, "distance") or 0) > 3]
        dens = [self.m(w, "climbing") / self.m(w, "distance") for w in ws if self.m(w, "climbing") is not None]
        spark = [[self._iso(math.floor(w.day)), round(self.m(w, "climbing") / self.m(w, "distance"), 0)]
                 for w in self.between(182, 0) if self.mountain(w) and (self.m(w, "distance") or 0) > 3
                 and self.m(w, "climbing") is not None]
        g = self.goals["targets"].get("climb_per_km")
        if not dens:
            return Indicator("density", _("爬升密度"), NA, "–", _("4 週內沒有越野／登山"), spark=spark, source=SRC_KOOP)
        top = max(dens)
        txt = f"{top:.0f} m/km"
        if g:
            ratio = top / g["value"]
            why = _("4 週內最陡一次 {top:.0f} m/km（平均 {mean:.0f}），目標「{event}」{goal:.0f} m/km → {pct:.0f}%",
                    top=top, mean=_mean(dens), event=g['event'], goal=g['value'], pct=ratio * 100)
            if self.kind == "specific":
                lvl, v, act = (GOOD, _("爬升密度接近比賽"), "") if ratio >= SPECIFIC_SHARE else \
                    (WATCH, _("訓練路線比比賽平"),
                     _("挑每公里爬升 ≥ {m:.0f} m 的路線做長跑", m=g['value'] * SPECIFIC_SHARE))
            else:
                lvl, v, act = INFO, _("專項期再對照比賽"), ""
        else:
            why = _("4 週內最陡一次 {top:.0f} m/km，平均 {mean:.0f}", top=top, mean=_mean(dens))
            lvl, v, act = INFO, _("沒有目標賽事"), ""
        return Indicator("density", _("爬升密度"), lvl, txt, v, why, act, SRC_KOOP, top, spark)

    def i_descent(self) -> Indicator:
        # Downhill impact load, 7-day vs 28-day daily mean — the same per-workout
        # sum as 訓練量 →「每週下坡衝擊負荷」(chart_metrics.DOWNHILL_EXPR). Our own
        # composite with no validated thresholds, so it only ever says INFO or
        # WATCH (a jump ≥ 1.5×, borrowed from the ACWR bands).
        from backend.engine.algorithms import chart_metrics as CM
        loads = self.ws(f"athleterange(today-90, today, {CM.DOWNHILL_EXPR})")
        per_day: dict[int, float] = {}
        for i, v in loads.items():
            w = self.ds.workouts[i]
            if w.sport in ("run", "walk") or self.is_hike(w):
                d = int(math.floor(w.day))
                per_day[d] = per_day.get(d, 0.0) + v
        daily = [per_day.get(d, 0.0) for d in range(self.tday - 27, self.tday + 1)]
        spark = [[d.isoformat(), round(sum(per_day.get(int(date_to_day(d)) + k, 0.0) for k in range(7)), 2)]
                 for d, _ in self.weekly_hours(12)]
        ratio = CM.acute_chronic(daily)
        a7 = sum(daily[-7:])
        src = _("自訂指標（依 Gottschall & Kram 2005、Keller 1996）")
        if ratio is None:
            return Indicator("descent", _("下坡負荷"), NA, "–", _("近 4 週沒有下坡路段"), spark=spark, source=src)
        txt = f"{ratio:.1f}×"
        why = _("近 7 天 {a7:.1f} 等效 km，近 28 天每週平均 {weekly:.1f}（參考指標，沒有驗證過的門檻）",
                a7=a7, weekly=sum(daily) / 4)
        if ratio >= 1.5 and a7 >= 3:
            lvl, v, act = WATCH, _("這週下坡比最近一個月多很多"), _("接下來幾天避開長下坡，或下坡放慢；股四頭肌痠痛消了再加")
        else:
            lvl, v, act = INFO, _("下坡量跟最近一個月差不多") if ratio >= 0.8 else _("這週下坡比平常少"), ""
        return Indicator("descent", _("下坡負荷"), lvl, txt, v, why, act, src, ratio, spark,
                         {"acute_7d": a7, "chronic_28d": sum(daily)})

    def i_strength(self) -> Indicator:
        n = len(self.since(28, {"strength"}))
        per_wk = n / 4
        spark = [[d.isoformat(), len([w for w in self.ds.workouts if w.sport == "strength"
                                       and date_to_day(d) <= math.floor(w.day) <= date_to_day(d) + 6])]
                 for d, _ in self.weekly_hours(12)]
        txt = _("{per_wk:.1f} 次/週", per_wk=per_wk)
        why = _("4 週內 {n} 次肌力（手錶有記錄的）", n=n)
        if per_wk >= STRENGTH_PER_WEEK:
            lvl, v, act = GOOD, _("每週 2 次，達標"), ""
        elif per_wk >= 1:
            lvl, v, act = WATCH, _("每週不到 2 次"), _("補到每週 2 次（下肢單腳、核心；膝主導＋臀中肌）")
        else:
            lvl, v, act = (BAD if self.kind in ("transition", "recovery", "base") else WATCH), \
                _("幾乎沒有肌力訓練"), _("每週 2 次 30–40 分鐘；轉換期／基礎期是打底的時候")
        return Indicator("strength", _("肌力"), lvl, txt, v, why, act, SRC_UA, per_wk, spark)

    def i_durability(self) -> Indicator:
        # Trail runs only: on hikes Pa:HR mixes in rest stops and swings by
        # ±40%, which says nothing about fatigue. |drift| > 30% is treated as
        # a broken reading, not a result.
        pts = [(math.floor(w.day), self.m(w, "pahr")) for w in self.since(84)
               if self.is_trail(w) and (self.m(w, "duration") or 0) > 5400
               and self.m(w, "pahr") is not None and abs(self.m(w, "pahr")) <= 0.30]
        spark = [[self._iso(d), round(v, 4)] for d, v in pts]
        if len(pts) < 2:
            return Indicator("durability", _("耐久度"), NA, "–", _("12 週內 > 90 分鐘的越野跑不到 2 次"),
                             spark=spark, source=SRC_KOOP)
        med = _median([v for _x, v in pts])
        txt = _pct(med, 1)
        why = _("12 週內 {n} 次 > 90 分鐘越野跑，後段效率掉幅中位數 {median}", n=len(pts), median=_pct(med, 1))
        if med < DRIFT_GOOD:
            lvl, v, act = GOOD, _("後段撐得住"), ""
        elif med < 0.15:
            lvl, v, act = WATCH, _("後段明顯掉"), _("長天中段加補給（每小時 30–60 g 碳水），並在長天後段練「累了還能維持配速」")
        else:
            lvl, v, act = BAD, _("後段掉很多"), _("拉長時間前先把長天配速放慢；檢查補給與水分")
        return Indicator("durability", _("耐久度"), lvl, txt, v, why, act, SRC_KOOP, med, spark)

    def _cp_protocol(self) -> str:
        """課表偏好 CP 測試方式 (plan.prefs.cp_test_protocol; default quick)."""
        from backend.engine import cp_protocols as CPP
        p = getattr(self, "cp_protocol", None)
        if p is None:
            try:
                from backend.engine import plan_prefs as PP
                p = PP.load().cp_test_protocol
            except Exception:                     # noqa: BLE001 — no settings store: the default
                p = None
        return CPP.norm(p)

    def i_testing(self) -> Indicator:
        def last(name):
            ds_ = [dt.date.fromisoformat(t.date) for t in self.plan.thresholds if getattr(t, name) is not None]
            return max(ds_) if ds_ else None
        cp, lt, ae = last("cp"), last("lthr"), last("aethr")
        parts, worst = [], GOOD
        from backend.engine.planning import threshold_row
        for label, d in (("CP", cp), ("LTHR", lt), ("AeT", ae)):
            if label == "AeT":
                # B3 (unsourced-rules.md): no fixed expiry — the AeT test is due for a reason
                # (quality_gate.aet_test_reason, below), not by age
                parts.append(_("AeT 沒測過") if d is None else _("AeT {days} 天前", days=(self.today - d).days))
                continue
            if label == "LTHR" and d is not None:
                # event-driven, not by age (zones-and-thresholds.md §3.4 change 4): an applied
                # estimate is said as one; retests come from the zone events below
                r = threshold_row(self.plan, "lthr", self.today)
                if r is not None and not r["measured"]:
                    parts.append(_("LTHR {label}，不是測試", label=r['label']))
                else:
                    parts.append(f"LTHR {(r or {}).get('label') or _('{days} 天前', days=(self.today - d).days)}")
                continue
            if d is None:
                parts.append(_("{label} 沒測過", label=label))
                worst = BAD
            else:
                age = (self.today - d).days
                parts.append(_("{label} {days} 天前", label=label, days=age))
                if age > TEST_DAYS_BAD:
                    worst = BAD
                elif age > TEST_DAYS_WATCH and worst != BAD:
                    worst = WATCH
        txt = "OK" if worst == GOOD else _("要測")
        sep = _("；")
        why = sep.join(parts)
        days_to = self.goals["days_to_next_a"]
        act = ""
        # which test week_plan should schedule: the CP test measures CP only; the
        # AeT test (engine/aet_test.py) has its own cadence
        cp_due = cp is None or (self.today - cp).days > TEST_DAYS_WATCH
        # a break of ~8 weeks or more: redo the CP baseline after the re-entry block
        # (WKO5 seminar notes: about two months off → new baseline; detraining.md §4.7)
        try:
            from backend.engine import reentry as RE
            brk = RE.find(self.ds, self.today)
        except Exception:                   # noqa: BLE001
            brk = None
        if brk and brk.get("cp_retest") and brk["end"] <= self.today.isoformat() and \
                (cp is None or cp.isoformat() < brk["return"]):
            cp_due = True
            worst = WATCH if worst == GOOD else worst
            why += _("；停跑 {days} 天：恢復期結束後重測 CP（WKO5 研討會筆記）", days=brk['days'])
        from backend.engine import cp_protocols as CPP
        proto = self._cp_protocol()
        if worst != GOOD:
            act = (_("{note}（課表偏好：用比賽）", note=_(CPP.NOTE_RACE)) if proto == "race" else
                   _("排一次 CP 測試（{label}，{hint}）", label=_(CPP.TABLE[proto]['label']),
                     hint=re.split("[；;]", _(CPP.TABLE[proto]['hint']))[0].strip())) + \
                _("＋ AeT 飄移測試（平日 50 分：10 分暖身＋40 分固定功率）；每 4–6 週一次")
            if days_to is not None and 10 <= days_to <= 21:
                act += _("——賽前 {days} 天正好是測試的時機（賽前 10–21 天）", days=days_to)
            elif days_to is not None and days_to < 10:
                act = _("賽前 10 天內不要測，賽後再測")
                worst = WATCH
        v = _("門檻是新的") if worst == GOOD else _("門檻過期或沒測，區間和 TSS 都會跟著不準")
        # the latest 3'/12' CP test in the data (workout_review) vs the CP in effect
        extra = {}
        try:
            from backend.engine import workout_review as WR
            ct = WR.latest_cp_test(self.ds, self.today)
        except Exception:
            ct = None
        if ct and ct.get("cp") is not None:
            # compared only with the previous result of the same method
            # (cp_protocols.reference): rotating quick / standard doesn't flag 要更新
            applied = cp is not None and cp >= dt.date.fromisoformat(ct["date"])
            ct = {**ct, "applied": applied}
            if applied:
                ct["apply"] = None
            extra["cp_test"] = ct
            dlt = ct.get("delta")
            ref = ct.get("ref") or {}
            vs = ("" if dlt is None else
                  (_("，和上一次同方法 {cp:.0f} W{conv} 差 {delta:+.1f}%", cp=ref.get('cp') or 0,
                     conv=_("（換算）") if ref.get('converted') else '', delta=dlt * 100)
                   if ref.get('same_method') else
                   _("，和目前 {cp:.0f} W{conv} 差 {delta:+.1f}%", cp=ref.get('cp') or 0,
                     conv=_("（換算）") if ref.get('converted') else '', delta=dlt * 100)))
            if not applied and ct.get("apply") and (dlt is None or abs(dlt) > WR.CP_DELTA):
                worst = WATCH if worst == GOOD else worst
                txt = _("要更新")
                v = _("{date} 的 CP 測試 {cp:.0f} W（{method}，{quality}）{vs}", date=ct['date'], cp=ct['cp'],
                      method=_(ct.get('method_label', '')) if ct.get('method_label') else '',
                      quality=_(ct.get('quality', '')) if ct.get('quality') else '', vs=vs)
                act = _("套用這次的 CP（{cp:.0f} W）", cp=ct['cp']) + (sep + act if act else "")
            why += _("；最近一次 CP 測試 {date}：{cp:.0f} W", date=ct['date'], cp=ct['cp']) + \
                (f"（{dlt * 100:+.1f}%）" if dlt is not None else "")
        # the latest AeT drift test (engine/aet_test.py): UA's three bands
        try:
            from backend.engine import aet_test as AT
            at = AT.latest_aet_test(self.ds, self.today)
        except Exception:
            at = None
        if at:
            done = AT.applied(self.plan, at)
            extra["aet_test"] = {**at, "applied": done, "apply": None if done else AT.apply_body(at)}
            if at.get("ok") and not done:
                now = f"（目前 {at['aethr_now']:.0f}）" if at.get("aethr_now") else ""
                if at["band"] == "at":
                    worst = WATCH if worst == GOOD else worst
                    txt = "要更新"
                    v = f"{at['date']} 的 AeT 測試：飄移 {at['drift'] * 100:.1f}%，AeT = {at['aethr_suggest']} bpm{now}"
                    act = f"套用這次的 AeT（{at['aethr_suggest']} bpm）" + (f"；{act}" if act else "")
                elif str(at.get("band") or "").startswith("base_"):
                    # 徐國峰 90 分 / Friel: a base check, no AeT number to apply
                    v2 = (f"{at['date']} 的有氧基礎測試：飄移 {at['drift'] * 100:.1f}%"
                          f"（{AT.BAND_LABEL[at['band']]}）")
                    why += f"；{v2}"
                else:
                    step = "+5" if at["band"] == "below" else "−5"
                    act = (f"{at['date']} 的 AeT 測試飄移 {at['drift'] * 100:.1f}%（{AT.BAND_LABEL[at['band']]}）："
                           f"下次起始心率 {step} bpm 再測一次") + (f"；{act}" if act else "")
            why += f"；最近一次 AeT 測試 {at['date']}：" + (f"飄移 {at['drift'] * 100:.1f}%" if at.get("ok")
                                                          else at.get("reason") or "不採用")
        gate = next((i.extra for i in getattr(self, "indicators", []) if i.id == "gate"), None) or {}
        tr = gate.get("aet_test_reason")
        if tr and not (at and at.get("band") == "at" and not extra["aet_test"]["applied"]):
            # B3: the test is due for a reason, not a date (quality_gate.aet_test_reason)
            worst = WATCH if worst == GOOD else worst
            v = f"建議 AeT 測試：{tr['text']}"
            act = ("先維持週量穩定，穩定 3 週後再排 AeT 測試（推估）" if tr.get("wait")
                   else "排一次 AeT 測試（課表偏好的測試方式）") + (f"；{act}" if act else "")
        # event-driven zone updates (engine/zone_events.py): suggestions only — they never
        # set cp_due, so week_plan schedules nothing from them; the 「建議做測試」 UI renders them
        try:
            from backend.engine import zone_events as ZE
            ze = ZE.suggestions(self.ds, self.plan, self.today, brk=brk,
                                aet_validity=((gate.get("aet") or {}).get("validity")))
        except Exception:                   # noqa: BLE001 — no detector, no suggestion
            ze = {"suggestions": [], "events": [], "checks": {}}
        self.test_suggestions = ze["suggestions"]
        extra["test_suggestions"] = ze["suggestions"]
        extra["zone_events"] = ze["events"]
        extra["zone_checks"] = ze["checks"]
        # low-priority suggestions (the AeT lower bound's 8-week reminder) are in the box only:
        # they don't turn the indicator WATCH
        firm = [s for s in ze["suggestions"] if s.get("priority") != "low"]
        if firm:
            worst = WATCH if worst == GOOD else worst
            s0 = firm[0]
            if txt == "OK":
                txt, v = "建議測", s0["title"]
            why += "；" + "；".join(s["title"] for s in firm)
            act = (act + "；" if act else "") + "建議（不會自動排課）：" + "、".join(
                ZE.TEST_LABEL[t] for t in s0["tests"])
        extra["cp_due"] = cp_due
        extra["aet_date"] = ae.isoformat() if ae else None
        extra["aet_last_test"] = at["date"] if at else None
        return Indicator("testing", "測試", worst, txt, v, why, act, SRC_NOTES, extra=extra)

    def i_data(self) -> Indicator:
        ath = self.ds.athlete
        hist = ath.settings.get("runthr") or []
        thr_default = bool(hist) and all(d == dt.date(1980, 1, 1) for d, _ in hist) and \
            self.plan.threshold_on("lthr", self.today) is None
        aet_missing = self.plan.threshold_on("aethr", self.today) is None
        cp_src = "測試" if self.plan.threshold_on("cp", self.today) is not None else \
            ("WKO5 mFTP" if self.ds.mftp_run is not None else
             "WKO5 設定值" if self.ds.settings_from == "wko5" else self.ds.setting_label("runftp"))
        recent = self.since(28)
        tot = self.ws("athleterange(today-27, today, sum(if(heartrate > 0, deltatime)))")
        with_hr = sum(1 for w in recent if tot.get(w.idx, 0) > 0)
        issues, lvl = [], GOOD
        if thr_default:
            issues.append("LTHR 還是 WKO5 預設值 160")
            lvl = BAD
        if aet_missing:
            issues.append("AeT 用 0.89×LTHR 估（推估）")
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
        acts = []
        if thr_default:
            acts.append("到「賽事周期」頁填 LTHR 測試結果")
        if aet_missing:
            acts.append("排一次 AeT 飄移測試（平日，10 分暖身＋40 分固定功率，跑步機或平路）；測了可以改用有氧基礎門檻")
        act = "；".join(acts)
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
            # engine/zone_events.py suggestion objects (schema in its module doc); never scheduled
            "test_suggestions": list(getattr(self, "test_suggestions", None) or []),
            "counts": {lvl: sum(1 for i in self.indicators if i.level == lvl) for lvl in (GOOD, WATCH, BAD, INFO, NA)},
        }


PHASE_GOAL = {
    "transition": "恢復、重建習慣、肌力打底",
    "recovery": "恢復——不追體能，等 TSB 回正",
    "base": "練有氧引擎：大量低強度、EF 往上",
    "specific": "練比賽需要的能力：爬坡、長時間、爬升密度接近賽事",
    "taper": "量減 40–60%、強度保留、TSB 回正",
    "event": "比賽週：短、輕鬆，補給與睡眠",
}

# which indicators matter most in each phase (for ordering the to-do list)
PHASE_PRIORITY = {
    "transition": ["form", "volume", "strength", "data", "testing"],
    "recovery": ["form", "volume", "strength", "data", "testing"],
    "base": ["intensity", "gate", "volume", "efficiency", "fitness", "strength", "testing", "data", "drift"],
    "specific": ["long", "density", "climb", "durability", "fitness", "intensity", "testing", "data"],
    "taper": ["volume", "intensity", "form", "long", "testing"],
    "event": ["form", "volume", "intensity"],
}

# standing focus per phase: (title, detail template, source)
PHASE_FOCUS = {
    "transition": ("轉換期重點", "每週 2 次肌力、量低而穩定，等 TSB 回正再進基礎期", SRC_UA),
    "recovery": ("恢復期重點", "先休；TSB 回正、想練了再開始", SRC_UA),
    # the long run / AeT cap is UA's; 8–15 s hill sprints are Palladino's (UA: 8–10 s)
    "base": ("基礎期重點", "每週一次 60–90 分鐘輕鬆長跑（心率 < {aet}），其餘輕鬆跑也壓在 AeT 以下；每週一次 8–15 秒坡衝刺",
             SRC_UA + "（輕鬆長跑、AeT 以下）；Palladino 基礎中期坡衝刺 8–15 秒"),
    "specific": ("專項期重點", "每週一次山路長跑，每公里爬升往 {goal_d} 靠；每 2 週一次長天往 {goal_h} 的七成靠；週中一次爬坡課", SRC_KOOP),
    "taper": ("減量期重點", "時數減到平常的 40–60%，次數不變，保留一次短強度；最後 3 天只做 30–40 分鐘輕鬆跑", SRC_BOSQUET),
    "event": ("比賽週", "前 2 天各 20–30 分鐘輕鬆跑＋幾趟加速；補給：每小時 30–60 g 碳水、每 15 分鐘 200 ml", SRC_NOTES),
}


def compute_status(ds: Dataset, plan: Optional[Plan] = None, today: Optional[dt.date] = None) -> dict:
    return Status(ds, plan, today).compute().to_dict()
