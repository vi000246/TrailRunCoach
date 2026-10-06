"""
基線測試 (SP-71): the two fixed tests the plan is judged by — a full-effort CP test (12′ + 3′)
and an AeT test now, then the same two again every 6–8 weeks.

Why (docs/research/plan-backtest-feasibility.md §1.2, §5.1–§5.2):
  * there is no outcome to compare yet: the CP in use is modelled from everyday runs, the one CP
    test found was not all-out, and there is no AeT test series — without tests there is nothing
    the weekly records (engine/plan_history.py) can be set against;
  * one before / after says nothing: a CP field test is taken as ± 3 % (推估,
    docs/research/racepower-v2.md), so two tests must differ by about 4 % (√2 × 3 %) to be more
    than noise, while recreational runners gain 3.6–5.0 % in 10 weeks (Muñoz 2014, 10 km time) —
    it takes 3–4 tests, 6–8 months, to see a trend;
  * the first two tests carry a learning effect (Hopkins, Schabort & Hawley 2001: + 1.2 %), so
    the baseline is worth doing now;
  * the same method every time: the two-point 12′ + 3′ test (cp_protocols "standard"), whatever
    課表偏好 CP 測試方式 says — methods are not interchangeable (a two-point CP runs ≈ 5 % above a
    30-min one, cp_protocols.CP_2PT_OVER_30MIN). The AeT test follows 課表偏好 (one method per
    athlete already).

Suggested, never scheduled (owner 2026-10-05): due() feeds rows of the floating box
(engine/suggestions.baseline_rows); the athlete picks the day there.

Cadence (REPEAT_DAYS, the owner's 6–8 weeks, 推估; the notes' 4–6 weeks for a CP test is the
same order — coaches, no trial. Uphill Athlete retests the AeT every 4–6 *months*, not weeks —
aerobic-base-readiness.md §8 — so the AeT cadence here is ours, not UA's): due again 6 weeks after
the last valid test; from 8 weeks on the row says it is late.

What counts as the last valid test (latest()):
  cp   the latest CP test the review found (status.i_testing's cp_test =
       workout_review.latest_cp_test) when it is the two-point one and its result may be applied
       (cp_protocols.apply_payload: quality not 不採用); else the latest done stored test session
       of protocol standard — but not the very run the review rejected (a test that was not
       all-out is no baseline);
  aet  the same with the latest AeT test (aet_test.latest_aet_test, `ok`) and the done stored
       AeT test sessions.

Not suggested (as week_plan's own test rules, engine/overview.py): within NO_TEST_DAYS_TO_A days
before an A race, in a 停訓後恢復期, in a 恢復期 / 轉換期 / 回量期 (no hard session there — SP-73,
SP-98), or with an open injury that is not 輕 or an open illness (engine/injuries.py).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

KINDS = ("cp", "aet")
CP_PROTOCOL = "standard"            # cp_protocols: 12′ all-out, 30′ rest, 3′ all-out
CP_METHOD = "2pt"
REPEAT_DAYS = (42, 56)              # owner 2026-10-05: 每 6–8 週重複一次
NO_TEST_DAYS_TO_A = 10              # status.i_testing: 賽前 10 天內不要測
NO_TEST_PHASES = ("recovery", "transition", "rebuild")
NO_TEST_SEVERITY = ("moderate", "severe")


def _day(v) -> Optional[str]:
    try:
        return dt.date.fromisoformat(str(v)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _is_aet(s: dict) -> bool:
    from backend.engine.aet_test import is_aet_session
    return is_aet_session(s)


def _is_cp_standard(s: dict) -> bool:
    from backend.engine import cp_protocols as CPP
    return not _is_aet(s) and CPP.protocol_of(s) == CP_PROTOCOL


def of_kind(s: dict, kind: str) -> bool:
    """A stored test session that is this baseline test (the AeT test / the 12′ + 3′ CP test)."""
    if s.get("kind") != "test":
        return False
    return _is_aet(s) if kind == "aet" else _is_cp_standard(s)


def latest(found: Optional[dict], stored: list[dict], today: str) -> dict:
    """{kind: {"last": the day of the last valid test or None, "rejected": the day of a later
    test that did not count, or None}}. `found`: {"cp": {date, method, protocol, ok},
    "aet": {date, ok}} — the latest test of each kind the review found (None = none; ok = the
    result counts); `stored`: the stored plan sessions (plan_store dicts)."""
    found = found or {}
    out = {}
    for kind in KINDS:
        f = found.get(kind) or {}
        fday = _day(f.get("date"))
        mine = bool(fday) and (kind == "aet" or f.get("method") == CP_METHOD or f.get("protocol") == CP_PROTOCOL)
        good = mine and bool(f.get("ok"))
        bad = fday if (fday and mine and not good) else None
        days = [fday] if good else []
        for s in stored or []:
            d = _day(s.get("day"))
            if d and d <= today and s.get("state") == "done" and of_kind(s, kind) and d != bad:
                days.append(d)
        last = max(days) if days else None
        out[kind] = {"last": last, "rejected": bad if bad and (last is None or bad > last) else None}
    return out


def blocked(ctx: Optional[dict]) -> Optional[str]:
    """Why no baseline test is suggested now (a code), or None. `ctx`: {days_to_a, phase, mode,
    injuries: the open 傷病紀錄 events}."""
    ctx = ctx or {}
    d = ctx.get("days_to_a")
    if d is not None and 0 <= d <= NO_TEST_DAYS_TO_A:
        return "race"
    if ctx.get("mode") == "reentry":
        return "reentry"
    if ctx.get("phase") in NO_TEST_PHASES or ctx.get("mode") in NO_TEST_PHASES:
        return "phase"
    for e in ctx.get("injuries") or []:
        if e.get("category") == "illness" or e.get("severity") in NO_TEST_SEVERITY:
            return "injury"
    return None


def due(today: str, tests: dict, ctx: Optional[dict] = None) -> list[dict]:
    """The baseline tests to suggest today: [{kind, reason first | repeat, last, days, weeks,
    late, rejected}] — `first`: no valid test on record; `repeat`: the last one is REPEAT_DAYS[0]
    days old or more (`late` from REPEAT_DAYS[1]). `tests`: latest(). [] while blocked(ctx).
    ctx["cold_start"] (week_plan's, SP-288): a new runner's first weeks — no CP test (owner 2026-10-06)."""
    if blocked(ctx):
        return []
    t0 = dt.date.fromisoformat(today)
    out = []
    for kind in KINDS:
        if kind == "cp" and (ctx or {}).get("cold_start"):
            continue
        t = tests.get(kind) or {}
        last = t.get("last")
        if last is None:
            out.append({"kind": kind, "reason": "first", "last": None, "days": None, "weeks": None,
                        "late": False, "rejected": t.get("rejected")})
            continue
        n = (t0 - dt.date.fromisoformat(last)).days
        if n >= REPEAT_DAYS[0]:
            out.append({"kind": kind, "reason": "repeat", "last": last, "days": n, "weeks": n // 7,
                        "late": n >= REPEAT_DAYS[1], "rejected": t.get("rejected")})
    return out


def next_due(tests: dict) -> dict:
    """{kind: the day the next repeat falls due (last + REPEAT_DAYS[0]) or None = now}."""
    out = {}
    for kind in KINDS:
        last = (tests.get(kind) or {}).get("last")
        out[kind] = None if last is None else \
            (dt.date.fromisoformat(last) + dt.timedelta(days=REPEAT_DAYS[0])).isoformat()
    return out
