"""
功率狀態 (SP-950) — the one answer to 「現在用不用功率」, site-wide.

The owner's rule (2026-10-27, SP-950 問答 Q1; docs/research/no-power-meter-audit.md 「拍板的規則」):
power is in effect only when the runner HAS a power meter (設定 › 基本資料 功率來源 = Stryd; not
set → detected from the data, athlete_profile.effective_power_source) AND the CP is fresh — a CP
test or a race in the last FRESH_DAYS (base_check.cp_stale, SP-493). Three states:

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
                  (「照那次活動當天」)
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


def is_fresh(today: dt.date, last_cp_test=None, last_race=None) -> bool:
    """base_check.cp_stale's negation (a CP test or a race in the last fresh_days())."""
    from backend.engine import base_check as BCK
    return not BCK.cp_stale(today, None if last_cp_test is None else str(last_cp_test),
                            None if last_race is None else str(last_race))


def fresh_on(day, test_days: Iterable = (), race_days: Iterable = ()) -> bool:
    """Was the CP fresh on `day`: a CP test or a race in the fresh_days() up to it (only the ones
    on or before `day` count). For the per-activity decisions (SP-952 / SP-954: 「照那次活動當天」):
    the caller passes every CP test day it knows (plan rows, the tests found in the data) and the
    race days (plan events) once, then asks per activity."""
    d = _d(day)
    if d is None:
        return False
    tests = [x for x in (_d(t) for t in test_days) if x is not None and x <= d]
    races = [x for x in (_d(r) for r in race_days) if x is not None and x <= d]
    return is_fresh(d, max(tests) if tests else None, max(races) if races else None)


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
          cp_date: Optional[str] = None, cp_source: Optional[str] = None) -> dict:
    """The power state of `today` (JSON-safe):

        state        power | stale | none (① ② ③), code 1 | 2 | 3, label
        power        bool — power is in effect (①)
        meter        bool — the runner has a power meter
        equipment    stryd | none | None (unknown), equipment_how (設定 / 自動偵測 / 預設)
        fresh        the CP is fresh (a CP test or a race in fresh_days())
        last_cp_test, last_race, anchor   the days the freshness stands on (ISO)
        days         days since `anchor` (None without one)
        stale_on     the first day the CP is stale (anchor + fresh_days() + 1); None without one
        fresh_days   the shelf life (42)
        cp, cp_date, cp_source            the CP on file (shown in ②, never used for targets)
        reason       one zh-TW line (the request's language)
        suggest      cp (① ② — the CP test keeps it / brings it back) | None (③: no test in its place)

    `source` / `how`: the equipment when the caller has it, else read from `profile` / `ds`."""
    if source is None and how is None:
        source, how = equipment(profile, ds, today)
    meter = has_meter(source)
    fresh = is_fresh(today, last_cp_test, last_race)
    st = NONE if not meter else POWER if fresh else STALE
    a = anchor(today, last_cp_test, last_race)
    lim = fresh_days()
    days = (today - a).days if a is not None else None
    out = {"state": st, "code": CODE[st], "label": _(LABEL[st]), "power": st == POWER, "meter": meter,
           "equipment": source, "equipment_how": _(how) if how else None, "fresh": fresh,
           "last_cp_test": None if _d(last_cp_test) is None else _d(last_cp_test).isoformat(),
           "last_race": None if _d(last_race) is None else _d(last_race).isoformat(),
           "anchor": a.isoformat() if a else None, "days": days,
           "stale_on": (a + dt.timedelta(days=lim + 1)).isoformat() if a else None, "fresh_days": lim,
           "cp": cp, "cp_date": cp_date, "cp_source": cp_source,
           "suggest": None if st == NONE else "cp"}
    out["reason"] = reason(out)
    return out


def reason(ps: dict) -> str:
    """The state's one line: ① why power, ② 「CP 已 N 天沒測…做一次 CP 測試就切回功率」, ③ no meter."""
    st = ps.get("state")
    if st == NONE:
        return _("沒有功率計：課表、模型和門檻都用心率／配速")
    if st == STALE:
        return stale_text(ps)
    if ps.get("days") is not None:
        return _("用功率：最近一次 CP 測試或比賽在 {n} 天前（{lim} 天內有效）", n=ps["days"], lim=ps.get("fresh_days"))
    return _("用功率")


def stale_text(ps: dict) -> str:
    """② (owner 2026-10-27: always say it): how old the CP is, what is used now, how to get power back."""
    if ps.get("days") is not None:
        return _("CP 已 {n} 天沒測，目前用心率／配速；做一次 CP 測試就切回功率", n=ps["days"])
    return _("還沒有 CP 測試或比賽，目前用心率／配速；做一次 CP 測試就切回功率")


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
    (fresh_on over cp_days). True for a Dataset that cannot tell (see above)."""
    if not _can_tell(ds):
        return True
    if _no_meter_memo(ds):
        return False
    tests, races = cp_days(ds)
    return fresh_on(day, tests, races)


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
