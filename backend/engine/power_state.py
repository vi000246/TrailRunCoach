"""
功率狀態 (SP-950) — the one answer to 「現在用不用功率」, site-wide.

The owner's rule (2026-10-27, SP-950 問答 Q1; docs/research/no-power-meter-audit.md 「拍板的規則」):
power is in effect only when the runner HAS a power meter (設定 › 基本資料 功率來源 = Stryd; not
set → detected from the data, athlete_profile.effective_power_source) AND the CP is fresh — a CP
test or a race in the last FRESH_DAYS (base_check.cp_stale, SP-493), or (SP-963, owner 2026-10-27)
the Stryd PD fit anchored by recent all-out efforts: in the last FRESH_DAYS at least one short
(EFFORT_SHORT_S, ~3–5 min) and one long (EFFORT_LONG_S, ~12–30 min) mean-max of a Stryd run on the
power curve (≥ EFFORT_ON_CURVE of the model in effect that day) — an interval session, a race, a
hard long run count without being a formal CP test (efforts_on). Three states:

  ① POWER  "power"  Stryd and a fresh CP: power targets, power models, CP tests every 42 days
  ② STALE  "stale"  Stryd but the CP is stale: heart rate / pace; a CP test keeps being suggested
                    and every note says 「CP 已 N 天沒測…做一次 CP 測試就切回功率」 (owner insisted)
  ③ NONE   "none"   no power meter: no power anywhere in planning and models (an old Stryd PD fit,
                    the plan's CP rows, WKO5's mFTP); no CP test, and so a CP test never blocks the
                    AeT test (no other test is suggested in its place: LTHR / threshold pace keep
                    coming from the estimates and manual entry). Nothing is deleted: switching the
                    equipment back restores everything.

Watch (wrist) power is no power source (owner update 2026-10-27): a stored 功率來源 「手錶推估功率」
(and the legacy coros / garmin / other meter) reads as 沒有功率計 (athlete_profile.profile_power_source),
and 進階 「採用手錶推估功率」 is ignored (power_source.effective_accept) — read-time only, nothing stored
is changed.

state(...)        the state of one day from what the caller already has (week_plan: its thresholds)
current(ds, day)  the same, reading the CP test / race days itself (the API, the status page)
fresh_on(...)     was the CP fresh on a past day — SP-952 / SP-954's per-activity rule
                  (「照那次活動當天」); with `ds`, the all-out efforts count too (SP-963)
efforts_on(ds, d) the short / long efforts that anchor the PD fit on `d` and until when (SP-963)
no_meter(ds)      ③ for a Dataset: the source gate of the live threshold estimate
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from backend.i18n import N_, _

POWER, STALE, NONE = "power", "stale", "none"
STATES = (POWER, STALE, NONE)
CODE = {POWER: 1, STALE: 2, NONE: 3}            # ① ② ③ (the audit's numbering)
LABEL = {POWER: N_("功率生效"), STALE: N_("有功率計，CP 過期"), NONE: N_("沒有功率計")}


def fresh_days() -> int:
    """The CP's shelf life: base_check.EP_ALL_OUT_DAYS (42; the cp_stale rule, SP-493)."""
    from backend.engine import base_check as BCK
    return BCK.EP_ALL_OUT_DAYS


# ---------------------------------------------------------------------------
# equipment
# ---------------------------------------------------------------------------

def equipment(profile: Optional[dict], ds=None, today: Optional[dt.date] = None) -> tuple[Optional[str], str]:
    """(stryd | none | None, how): the 功率來源 entered (「設定」; 手錶 reads as none), else detected on `ds`
    (「自動偵測」, athlete_profile.detect_power_source). None = unknown (no setting and no Dataset
    that can tell — a test double without power_source): read as a meter, the long-standing
    behaviour (target_policy's auto power for an unset source)."""
    from backend.engine import athlete_profile as AP
    s = AP.profile_power_source(profile or {})
    if s is not None:
        return s, N_("設定")
    if ds is not None and callable(getattr(ds, "power_source", None)):
        try:
            return AP.detect_power_source(ds, today)["source"], N_("自動偵測")
        except Exception:                   # noqa: BLE001 — unreadable data: unknown
            return None, N_("預設")
    return None, N_("預設")


def has_meter(source: Optional[str]) -> bool:
    """A power meter: Stryd or watch power; unknown (None) counts as one (see equipment)."""
    return source != "none"


def accept_watch(profile: Optional[dict] = None, stored: bool = False) -> bool:
    """Whether watch-estimated power may feed the models: never (owner 2026-10-27; the stored 進階
    「採用手錶推估功率」 is ignored, not changed)."""
    return False


def no_meter(ds, today: Optional[dt.date] = None) -> bool:
    """③ for this Dataset's runner (its plan's 功率來源, else detected on it)."""
    try:
        prof = getattr(getattr(ds, "plan", None), "profile", None)
    except Exception:                       # noqa: BLE001 — a Dataset without a plan (tests)
        prof = None
    return not has_meter(equipment(prof, ds, today)[0])


# ---------------------------------------------------------------------------
# CP freshness
# ---------------------------------------------------------------------------

def _d(x) -> Optional[dt.date]:
    if x is None:
        return None
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def anchor(today: dt.date, last_cp_test=None, last_race=None) -> Optional[dt.date]:
    """The latest all-out effort the CP stands on: the later of the last CP test and the last
    race on or before `today`; None without either."""
    days = [d for d in (_d(last_cp_test), _d(last_race)) if d is not None and d <= today]
    return max(days) if days else None


def is_fresh(today: dt.date, last_cp_test=None, last_race=None, efforts_until=None) -> bool:
    """base_check.cp_stale's negation (a CP test or a race in the last fresh_days(), or — SP-963 —
    the all-out efforts that anchor the PD fit still valid on `today`: efforts_on(...)["until"])."""
    from backend.engine import base_check as BCK
    return not BCK.cp_stale(today, None if last_cp_test is None else str(last_cp_test),
                            None if last_race is None else str(last_race),
                            None if efforts_until is None else str(efforts_until))


def fresh_on(day, test_days: Iterable = (), race_days: Iterable = (), ds=None) -> bool:
    """Was the CP fresh on `day`: a CP test or a race in the fresh_days() up to it (only the ones
    on or before `day` count), or — with `ds` (SP-963) — the PD fit anchored by all-out efforts on
    that day (efforts_on). For the per-activity decisions (SP-952 / SP-954: 「照那次活動當天」):
    the caller passes every CP test day it knows (plan rows, the tests found in the data) and the
    race days (plan events) once, then asks per activity (efforts_on is memoised per Dataset)."""
    d = _d(day)
    if d is None:
        return False
    tests = [x for x in (_d(t) for t in test_days) if x is not None and x <= d]
    races = [x for x in (_d(r) for r in race_days) if x is not None and x <= d]
    if is_fresh(d, max(tests) if tests else None, max(races) if races else None):
        return True
    return ds is not None and bool((efforts_on(ds, d) or {}).get("anchored"))


# ---------------------------------------------------------------------------
# SP-963: the PD fit anchored by recent all-out efforts
# ---------------------------------------------------------------------------
# Owner 2026-10-27 (SP-963, SP-950's child; (a)): the CP in effect for a runner without a CP test is
# WKO5's PD fit on the Stryd 90-day mean-max (SP-468). What matters is whether the fit has fresh
# all-out efforts under it, not whether one of them was a formal CP test: in the fresh_days() up to
# the day at least one SHORT and one LONG mean-max of a Stryd run lie on the power curve (≥
# EFFORT_ON_CURVE of the model in effect that day at that duration). The run's whole-activity
# mean-max — the very points the PD fit is built from — so an interval session, a race or a hard long
# run count; watch power never (Stryd and Dataset.power_ok only). Backtest on the owner's data
# (2026-04-01…10-27, SP-963 筆記): 79 of 210 days anchored at 95 % — the September interval block on,
# the summer weeks of easy running off. All 推估.
EFFORT_SHORT_S = (180.0, 300.0)      # 推估: ~3–5 min (the CP test's short bout, a VO2 interval)
EFFORT_LONG_S = (720.0, 1800.0)      # 推估: ~12–30 min (the CP test's long bout, a threshold effort)
EFFORT_ON_CURVE = 0.95               # 推估: ≥ 95 % of the PD model at that duration


def _model_on(ds, day: dt.date) -> Optional[dict]:
    """The Stryd PD model in effect on `day` ({"date", "params", "mftp"}: FitFolderDataset.
    stryd_pd_params, the model the 個人化區間 use); None for a Dataset without one (WKO5, test doubles)."""
    f = getattr(ds, "stryd_pd_params", None)
    if not callable(f):
        return None
    try:
        r = f(day)
    except Exception:                       # noqa: BLE001 — no fit: nothing anchors
        return None
    return r if isinstance(r, dict) and r.get("params") else None


def _gen(ds) -> tuple:
    """The Dataset's generation for the efforts memo: the workouts and the PD fits in effect."""
    ws = getattr(ds, "workouts", None) or []
    last = ws[-1] if ws else None
    return (len(ws), getattr(getattr(last, "entry", None), "file", None), id(getattr(ds, "_cp_est", None)))


def _effort_memo(ds) -> dict:
    """The per-generation memo of the efforts (in ds.memo; a Dataset without one: on the instance)."""
    memo = _memo(ds)
    if memo is None:
        memo = ds.__dict__.setdefault("_sp963_memo", {}) if hasattr(ds, "__dict__") else {}
    key = ("sp963_efforts", _gen(ds))
    hit = memo.get(key)
    if hit is None:
        for k in [k for k in memo if isinstance(k, tuple) and k and k[0] == "sp963_efforts"]:
            memo.pop(k, None)               # an older generation
        hit = memo[key] = {"runs": None, "curves": {}, "ratios": {}, "days": {}}
    return hit


def _stryd_runs(ds, m: dict) -> list:
    """(date, workout) of the runs whose power is Stryd (power_source + power_ok), by date."""
    if m["runs"] is None:
        from backend.engine import power_source as PS
        from backend.engine.wko5expr.dataset import day_to_date
        out = []
        for w in getattr(ds, "workouts", None) or []:
            if w.sport != "run":
                continue
            try:
                ok = ds.power_source(w) == PS.STRYD and ds.power_ok(w)
            except Exception:               # noqa: BLE001 — cannot tell: no anchor
                ok = False
            if ok:
                out.append((day_to_date(w.day), w))
        m["runs"] = sorted(out, key=lambda x: (x[0], x[1].idx))
    return m["runs"]


def _curve(ds, w, m: dict):
    """The run's power mean-max (xs, ys): the curve the PD fit reads (racepower.athlete._curve), else
    computed from the power channel (a Dataset without curve caches)."""
    if w.idx in m["curves"]:
        return m["curves"][w.idx]
    c = None
    try:
        from backend.engine.racepower import athlete as A
        c = A._curve(ds, w)
    except Exception:                       # noqa: BLE001 — no curve cache (test doubles): from the samples
        c = None
    if not c or not c[0]:
        try:
            from backend.engine.racepower.cptest import mean_max_curve
            p = ds.channel(w.idx, "power")
            c = mean_max_curve(p) if p is not None else None
        except Exception:                   # noqa: BLE001
            c = None
    m["curves"][w.idx] = c
    return c


def _best(xs, ys, params, lo: float, hi: float, cp: Optional[float]) -> Optional[dict]:
    """The best ratio mean-max / model over durations lo…hi: {"seconds", "watts", "pct"}."""
    import math
    from backend.engine.algorithms import wko5_pdmodel as PDM
    best = None
    for x, y in zip(xs, ys):
        if y is None or x < lo or x > hi:
            continue
        try:
            mdl = PDM.model(params, float(x))
        except (ValueError, OverflowError, ZeroDivisionError):
            continue
        if not mdl or not math.isfinite(mdl) or mdl <= 0:
            continue
        if cp and y > 2.0 * cp:             # a spiking sensor (racepower.athlete.implausible)
            continue
        q = float(y) / mdl
        if best is None or q > best["pct"]:
            best = {"seconds": int(round(float(x))), "watts": round(float(y)), "pct": q}
    return best


def _ratios(ds, w, model: dict, m: dict) -> tuple:
    """(short, long) best efforts of run `w` against `model` (memoised per fit date and run)."""
    key = (str(model.get("date")), w.idx)
    if key not in m["ratios"]:
        c = _curve(ds, w, m)
        if not c or not c[0]:
            m["ratios"][key] = (None, None)
        else:
            xs, ys, p, cp = list(c[0]), list(c[1]), model["params"], model.get("mftp")
            m["ratios"][key] = (_best(xs, ys, p, *EFFORT_SHORT_S, cp), _best(xs, ys, p, *EFFORT_LONG_S, cp))
    return m["ratios"][key]


def efforts_on(ds, day) -> Optional[dict]:
    """SP-963: the all-out efforts that anchor the Stryd PD fit on `day`:

        anchored   a short and a long effort on the power curve in the fresh_days() up to `day`
        short, long  the latest of each kind {"date", "seconds", "watts", "pct"} (None without one)
        until      the last day they keep the CP fresh (the older of the two + fresh_days()); None
        cp, fit_date   the model's mFTP and its fit date

    None for a Dataset without a Stryd PD model (WKO5, test doubles) or a day without a fit.
    Memoised per Dataset generation and day: activity analysis asks it per activity."""
    d = _d(day)
    if d is None or ds is None:
        return None
    m = _effort_memo(ds)
    if d in m["days"]:
        return m["days"][d]
    out = None
    model = _model_on(ds, d)
    if model is not None:
        import bisect
        lim = fresh_days()
        runs = _stryd_runs(ds, m)
        dates = [r[0] for r in runs]
        lo = bisect.bisect_left(dates, d - dt.timedelta(days=lim))     # day 42 still counts (cp_stale)
        hi = bisect.bisect_right(dates, d)
        short = long_ = None
        for wd, w in runs[lo:hi]:           # oldest → newest: the latest qualifying one wins
            s, lg = _ratios(ds, w, model, m)
            if s and s["pct"] >= EFFORT_ON_CURVE:
                short = {**s, "date": wd.isoformat()}
            if lg and lg["pct"] >= EFFORT_ON_CURVE:
                long_ = {**lg, "date": wd.isoformat()}
        anchored = short is not None and long_ is not None
        until = None
        if anchored:
            until = (min(_d(short["date"]), _d(long_["date"])) + dt.timedelta(days=lim)).isoformat()
        out = {"anchored": anchored, "short": short, "long": long_, "until": until,
               "cp": None if model.get("mftp") is None else round(float(model["mftp"]), 1),
               "fit_date": None if _d(model.get("date")) is None else _d(model.get("date")).isoformat()}
    m["days"][d] = out
    return out


def plan_days(plan) -> tuple[list[str], list[str]]:
    """(CP test days, race days) the season plan holds: the CP rows not typed by hand (as
    overview.last_cp_test_day) and the race days (overview.last_race_day's events) — for fresh_on.
    The CP tests found only in the data (workout_review.latest_cp_test) are the caller's to add."""
    tests = sorted({str(t.date)[:10] for t in getattr(plan, "thresholds", None) or []
                    if getattr(t, "cp", None) is not None and not getattr(t, "cp_manual", None)})
    races = []
    for e in getattr(plan, "events", None) or ():
        if getattr(e, "kind", None) in ("baiyue", "other"):
            continue
        cur, end = _d(e.start), _d(e.end)
        while cur is not None and end is not None and cur <= end:
            races.append(cur.isoformat())
            cur += dt.timedelta(days=1)
    return tests, sorted(set(races))


# ---------------------------------------------------------------------------
# the state
# ---------------------------------------------------------------------------

def state(today: dt.date, profile: Optional[dict] = None, *, ds=None, source: Optional[str] = None,
          how: Optional[str] = None, last_cp_test=None, last_race=None, cp: Optional[float] = None,
          cp_date: Optional[str] = None, cp_source: Optional[str] = None, efforts=Ellipsis) -> dict:
    """The power state of `today` (JSON-safe):

        state        power | stale | none (① ② ③), code 1 | 2 | 3, label
        power        bool — power is in effect (①)
        meter        bool — the runner has a power meter
        equipment    stryd | none | None (unknown), equipment_how (設定 / 自動偵測 / 預設)
        fresh        the CP is fresh (a CP test or a race in fresh_days(), or — SP-963 — the PD fit
                     anchored by recent all-out efforts)
        last_cp_test, last_race, anchor   the CP test / race days (ISO); anchor = the later one
        days         days since `anchor` (None without one) — 「CP 已 N 天沒測」
        efforts      SP-963: efforts_on(ds, today) — {anchored, short, long, until, cp, fit_date}; None
        basis        what keeps the CP fresh longest: test | race | efforts | None (stale)
        valid_until  the last day the CP is fresh (ISO; the later of anchor + fresh_days() and the
                     efforts' until); None without either
        left         days from today to valid_until (≥ 0 while fresh); None without it
        stale_on     the first day the CP is stale (valid_until + 1); None without one
        fresh_days   the shelf life (42)
        cp, cp_date, cp_source            the CP on file (shown in ②, never used for targets)
        reason       one zh-TW line (the request's language): ① names what keeps the CP fresh and
                     until when (「CP 195 W · 9/30 的 5 分鐘 228 W 和 12 分鐘 222 W 撐著，有效到 11/11」)
        suggest      cp (① ② — the CP test keeps it / brings it back) | None (③: no test in its place)

    `source` / `how`: the equipment when the caller has it, else read from `profile` / `ds`.
    `efforts`: efforts_on's dict when the caller has it; not given → read on `ds` (None without)."""
    if source is None and how is None:
        source, how = equipment(profile, ds, today)
    meter = has_meter(source)
    if efforts is Ellipsis:
        try:
            efforts = efforts_on(ds, today) if ds is not None and meter else None
        except Exception:                   # noqa: BLE001 — unreadable data: no efforts
            efforts = None
    eff_until = (efforts or {}).get("until") if (efforts or {}).get("anchored") else None
    fresh = is_fresh(today, last_cp_test, last_race, eff_until)
    st = NONE if not meter else POWER if fresh else STALE
    a = anchor(today, last_cp_test, last_race)
    lim = fresh_days()
    days = (today - a).days if a is not None else None
    a_until = a + dt.timedelta(days=lim) if a is not None else None
    e_until = _d(eff_until)
    until = max([x for x in (a_until, e_until) if x is not None], default=None)
    basis = None
    if fresh:
        if e_until is not None and (a_until is None or e_until > a_until or a_until < today):
            basis = "efforts"
        else:
            basis = "test" if a is not None and a == _d(last_cp_test) else "race"
    out = {"state": st, "code": CODE[st], "label": _(LABEL[st]), "power": st == POWER, "meter": meter,
           "equipment": source, "equipment_how": _(how) if how else None, "fresh": fresh,
           "last_cp_test": None if _d(last_cp_test) is None else _d(last_cp_test).isoformat(),
           "last_race": None if _d(last_race) is None else _d(last_race).isoformat(),
           "anchor": a.isoformat() if a else None, "days": days,
           "efforts": efforts, "basis": basis,
           "valid_until": until.isoformat() if until else None,
           "left": (until - today).days if until else None,
           "stale_on": (until + dt.timedelta(days=1)).isoformat() if until else None, "fresh_days": lim,
           "cp": cp, "cp_date": cp_date, "cp_source": cp_source,
           "suggest": None if st == NONE else "cp"}
    out["reason"] = reason(out)
    return out


def _md(day) -> str:
    d = _d(day)
    return f"{d.month}/{d.day}" if d else "–"


def _dur(seconds) -> str:
    """5 分鐘 / 4 分 23 秒 (an effort's duration)."""
    s = int(round(float(seconds or 0)))
    if s % 60 == 0:
        return _("{m} 分鐘", m=s // 60)
    return _("{m} 分 {s} 秒", m=s // 60, s=s % 60)


def efforts_text(eff: Optional[dict]) -> str:
    """The efforts that anchor the CP: 「10/22 的 5 分鐘 228 W 和 10/18 的 20 分鐘 205 W」 (one run:
    「9/30 的 5 分鐘 228 W 和 12 分鐘 222 W」); "" without both."""
    sh, lg = (eff or {}).get("short"), (eff or {}).get("long")
    if not sh or not lg:
        return ""
    a = _("{t} {w} W", t=_dur(sh["seconds"]), w=sh["watts"])
    b = _("{t} {w} W", t=_dur(lg["seconds"]), w=lg["watts"])
    if sh["date"] == lg["date"]:
        return _("{d} 的 {a} 和 {b}", d=_md(sh["date"]), a=a, b=b)
    first, second = sorted([(sh["date"], a), (lg["date"], b)], reverse=True)
    return _("{d1} 的 {a} 和 {d2} 的 {b}", d1=_md(first[0]), a=first[1], d2=_md(second[0]), b=second[1])


def reason(ps: dict) -> str:
    """The state's one line: ① what keeps the CP fresh and until when (SP-963: 「CP 195 W · 10/22 的
    … 和 10/18 的 … 撐著，有效到 11/29」), ② 「CP 已 N 天沒測…做一次 CP 測試就切回功率（或最近跑一次
    強度課／比賽）」, ③ no meter."""
    st = ps.get("state")
    if st == NONE:
        return _("沒有功率計：課表、模型和門檻都用心率／配速")
    if st == STALE:
        return stale_text(ps)
    basis, until = ps.get("basis"), ps.get("valid_until")
    what = ""
    if basis == "efforts":
        what = efforts_text(ps.get("efforts"))
    elif basis == "test" and ps.get("anchor"):
        what = _("{d} 的 CP 測試", d=_md(ps["anchor"]))
    elif basis == "race" and ps.get("anchor"):
        what = _("{d} 的比賽", d=_md(ps["anchor"]))
    if not what or not until:
        return _("用功率")
    what += " " if what[-1:].isascii() else ""       # 「228 W 撐著」, 「CP 測試撐著」
    line = _("{what}撐著，有效到 {until}", what=what, until=_md(until)).strip()
    cp = ps.get("cp")
    return _("CP {cp} W · {line}", cp=round(float(cp)), line=line) if cp else line


def stale_text(ps: dict) -> str:
    """② (owner 2026-10-27: always say it): how old the CP is, what is used now, how to get power back
    (SP-963: a CP test, or a recent hard session / race whose efforts land on the power curve)."""
    if ps.get("days") is not None:
        return _("CP 已 {n} 天沒測，目前用心率／配速；做一次 CP 測試就切回功率（或最近跑一次強度課／比賽）", n=ps["days"])
    return _("還沒有 CP 測試或比賽，目前用心率／配速；做一次 CP 測試就切回功率（或最近跑一次強度課／比賽）")


def short_reason(ps: Optional[dict]) -> str:
    """The fallback reason a target carries (target_policy's 「自動：…看功率（…）」)."""
    st = (ps or {}).get("state")
    if st == NONE:
        return _("沒有功率計：用心率")
    if st == STALE:
        return stale_text(ps or {})
    return ""


def current(ds, today: dt.date, plan=None, profile: Optional[dict] = None) -> dict:
    """state() reading what it stands on itself: the last CP test (plan rows not typed by hand, or
    the latest test found in the data — overview.last_cp_test_day / workout_review.latest_cp_test),
    the last race (overview.last_race_day) and the CP in effect (zones.cp_in_effect). For the API
    and the pages; week_plan passes what it already has to state()."""
    from backend.engine import overview as O
    plan = plan if plan is not None else getattr(ds, "plan", None)
    profile = profile if profile is not None else getattr(plan, "profile", None)
    try:
        from backend.engine import workout_review as WR
        rv = (WR.latest_cp_test(ds, today) or {}).get("date")
    except Exception:                       # noqa: BLE001 — no data / a test double
        rv = None
    cp = {}
    try:
        from backend.engine.wko5expr.dataset import date_to_day
        from backend.engine.zones import cp_in_effect
        cp = cp_in_effect(ds, int(date_to_day(today)))
    except Exception:                       # noqa: BLE001 — no runs yet: no CP
        cp = {}
    return state(today, profile, ds=ds, last_cp_test=O.last_cp_test_day(plan, today, rv),
                 last_race=O.last_race_day(plan, today), cp=cp.get("value"), cp_date=cp.get("date"),
                 cp_source=cp.get("source"))


def of(th: Optional[dict]) -> Optional[dict]:
    """The power state a thresholds dict carries (week_plan's thresholds["power_state"]); None."""
    ps = (th or {}).get("power_state")
    return ps if isinstance(ps, dict) and ps.get("state") in STATES else None


# ---------------------------------------------------------------------------
# per activity (SP-952): judge by power only when power was in effect THAT day
# ---------------------------------------------------------------------------
# Owner 2026-10-27 (SP-950 Q2-3; docs/research/no-power-meter-audit.md 「活動判讀…照那次活動當天」):
# an activity is judged by power when the runner has a power meter (not ③), the activity's power is
# trusted (Dataset.power_ok: Stryd — watch power never) and the CP was fresh on its date (fresh_on);
# otherwise by heart rate / pace. A Dataset that cannot tell (no power_ok: test doubles) keeps the
# long-standing behaviour (power whenever there is a CP), as equipment() reads an unknown source.

def _can_tell(ds) -> bool:
    return callable(getattr(ds, "power_ok", None))


def _memo(ds) -> Optional[dict]:
    m = getattr(ds, "memo", None)
    return m if isinstance(m, dict) else None


def _plan_sig(ds) -> tuple:
    plan = getattr(ds, "plan", None)
    return (id(plan), len(getattr(plan, "thresholds", None) or []), len(getattr(plan, "events", None) or []),
            len(getattr(ds, "workouts", None) or []))


def _data_test_days(ds) -> list[str]:
    """The CP tests in the data beyond the plan's CP rows, read WITHOUT measuring the runs (measure()
    asks activity_power, so it can't be asked back): a run with trusted power that a stored CP-test
    session was marked done by (workout_review.scheduled_test's done_by), that the user tagged 測試,
    or whose title says CP test (workout_review.session_class's title rule: 「CP」/「測試」, not an AeT /
    max-HR test). A run on a plan race day is a race (plan_days)."""
    import re
    from backend.engine import workout_review as WR
    out: set[str] = set()
    try:
        from backend.engine.threshold_confidence import HRMAX_TITLE
    except Exception:                       # noqa: BLE001
        HRMAX_TITLE = None
    done = {}
    try:
        from backend.engine.aet_test import is_aet_session
        for s in WR._plan_test_sessions(ds):
            db = s.get("done_by") or {}
            if s.get("state") == "done" and db.get("index") is not None and not is_aet_session(s) \
                    and not (HRMAX_TITLE and HRMAX_TITLE.search(s.get("title") or "")):
                done[db.get("index")] = str(db.get("date") or s.get("day") or "")[:10]
    except Exception:                       # noqa: BLE001 — no plan store: nothing scheduled
        done = {}
    try:
        from backend.engine import activity_tags as AT
        tags = AT.load()                    # once: user_of reads the store on every call otherwise
    except Exception:                       # noqa: BLE001 — no tag store
        AT, tags = None, []
    for w in getattr(ds, "workouts", None) or []:
        if w.sport != "run":
            continue
        try:
            title = WR._title(w)
        except Exception:                   # noqa: BLE001
            title = ""
        hit = w.idx in done
        if not hit and title and not WR.AET_TITLE.search(title) and re.search(r"\bCP\b|測試", title) \
                and not (HRMAX_TITLE and HRMAX_TITLE.search(title)):
            hit = True
        if not hit and tags:
            try:
                hit = AT.user_type(AT.user_of(w, tags)) == "test" and not WR.AET_TITLE.search(title or "")
            except Exception:               # noqa: BLE001
                hit = False
        if not hit:
            continue
        try:
            if not ds.power_ok(w):
                continue
        except Exception:                   # noqa: BLE001
            continue
        out.add(WR._wdate(w).isoformat())
    return sorted(out)


def cp_days(ds) -> tuple[list[str], list[str]]:
    """(CP test days, race days) for fresh_on on this Dataset: plan_days + _data_test_days (memoised
    in ds.memo per plan / workout count)."""
    import time
    memo = _memo(ds)
    key = ("sp952_cp_days", _plan_sig(ds))
    hit = memo.get(key) if memo is not None else None
    # the stored test sessions and activity tags aren't in the key: re-read after CP_DAYS_TTL_S
    if hit is not None and time.monotonic() - hit[0] < CP_DAYS_TTL_S:
        return hit[1]
    tests, races = plan_days(getattr(ds, "plan", None))
    try:
        tests = sorted(set(tests) | set(_data_test_days(ds)))
    except Exception:                       # noqa: BLE001 — unreadable data: the plan's rows only
        pass
    out = (tests, races)
    if memo is not None:
        memo[key] = (time.monotonic(), out)
    return out


CP_DAYS_TTL_S = 60.0


def _no_meter_memo(ds) -> bool:
    memo = _memo(ds)
    prof = getattr(getattr(ds, "plan", None), "profile", None) or {}
    key = ("sp952_no_meter", _plan_sig(ds)[0], len(getattr(ds, "workouts", None) or []),
           repr(sorted((k, str(v)) for k, v in prof.items() if "power" in str(k))))
    if memo is not None and key in memo:
        return memo[key]
    try:
        v = no_meter(ds)
    except Exception:                       # noqa: BLE001
        v = False
    if memo is not None:
        memo[key] = v
    return v


def day_power(ds, day) -> bool:
    """Power is in effect on `day` for this Dataset's runner: not ③ and the CP fresh on that day
    (fresh_on over cp_days, and — SP-963 — the all-out efforts on the power curve, efforts_on). True
    for a Dataset that cannot tell (see above)."""
    if not _can_tell(ds):
        return True
    if _no_meter_memo(ds):
        return False
    tests, races = cp_days(ds)
    return fresh_on(day, tests, races, ds=ds)


def activity_power(ds, w) -> bool:
    """SP-952: judge this activity by power — day_power on its date and its power trusted
    (Dataset.power_ok: Stryd only). True for a Dataset that cannot tell (test doubles)."""
    if not _can_tell(ds):
        return True
    try:
        if not ds.power_ok(w):
            return False
    except Exception:                       # noqa: BLE001
        return False
    from backend.engine.wko5expr.dataset import day_to_date
    return day_power(ds, day_to_date(w.day))
