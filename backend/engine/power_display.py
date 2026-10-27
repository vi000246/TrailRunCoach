"""
功率顯示 (SP-954) — how much power a page shows, by THAT day's power state.

The owner's rule (2026-10-27, SP-950 問答 Q2-3; docs/research/no-power-meter-audit.md 「拍板的規則」):

  single activity   trusted power (Stryd) and the CP fresh on its day (power_state.activity_power)
                    → FULL: power line, power zones, % CP, per-rep power;
                    trusted power but the CP stale that day → STALE: the power line, average power and
                    NP only — no % CP, no power zones — noted 「當時 CP 已 N 天沒測」;
                    no trusted power (none, watch power) or no power meter now (③) → NONE: HR / pace only.
  a charts range    the power trend points only on the days the CP was fresh (the other days blank,
                    shaded 「CP 過期」/「沒有功率」); power-only charts (the power curve, the CP history) when
                    the range has any valid power at all.

Whether the CP was fresh on a day is power_state's (fresh_on / day_power / cp_days — SP-963 owns that
definition); this module only turns it into what a page shows. A Dataset that cannot tell (no power_ok:
test doubles, a WKO5 folder without classification) shows everything, the long-standing behaviour.

View(ds)                 one request's context (no meter, the CP test / race days read once)
View.activity(w)         {"mode": full | stale | none, "days": N | None} of one activity
View.day_states(b, e)    the runs of a range, one state per run day (ok | stale | nopower)
spans(states)            the shaded stretches of a range: [{"from", "to", "kind"}]
mask_power(res, ...)     a rendered athlete chart with its power points blanked on the invalid days
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.engine import power_state as PST
from backend.i18n import N_, _

FULL, STALE, NONE = "full", "stale", "none"
OK, STALE_DAY, NOPOWER = "ok", "stale", "nopower"           # a day of a charts range
SPAN_LABEL = {STALE_DAY: N_("CP 過期"), NOPOWER: N_("沒有功率")}
NO_VALID = N_("這段期間沒有有效的功率（當時 CP 過期或沒有功率計）")


def _day(w) -> dt.date:
    from backend.engine.wko5expr.dataset import day_to_date
    return day_to_date(math.floor(w.day))


class View:
    """What power one request may show (SP-954). Reads the equipment and the CP days once."""

    def __init__(self, ds):
        self.ds = ds
        self.tell = callable(getattr(ds, "power_ok", None))
        self.no_meter = False
        self.tests: list[str] = []
        self.races: list[str] = []
        if not self.tell:
            return
        try:
            self.no_meter = PST.no_meter(ds)
        except Exception:                   # noqa: BLE001 — unreadable equipment: a meter, as power_state
            self.no_meter = False
        if not self.no_meter:
            try:
                self.tests, self.races = PST.cp_days(ds)
            except Exception:               # noqa: BLE001 — no plan / data: no CP day
                self.tests, self.races = [], []

    def trusted(self, w) -> bool:
        """The activity has power the models may use (Dataset.power_ok: Stryd; watch power never)."""
        try:
            return bool(self.ds.power_ok(w))
        except Exception:                   # noqa: BLE001
            return False

    def stale_days(self, day: dt.date) -> Optional[int]:
        """Days from the CP test / race the CP stood on to `day` (None: none before it)."""
        tests = [t for t in self.tests if str(t)[:10] <= day.isoformat()]
        races = [r for r in self.races if str(r)[:10] <= day.isoformat()]
        a = PST.anchor(day, max(tests) if tests else None, max(races) if races else None)
        return (day - a).days if a is not None else None

    def activity(self, w) -> dict:
        """{"mode": full | stale | none, "days": N | None} — `days` (STALE only): how long the CP had
        not been tested on the activity's day."""
        if not self.tell:
            return {"mode": FULL, "days": None}
        if self.no_meter or not self.trusted(w):
            return {"mode": NONE, "days": None}
        if PST.activity_power(self.ds, w):
            return {"mode": FULL, "days": None}
        return {"mode": STALE, "days": self.stale_days(_day(w))}

    def day_states(self, b: float, e: float) -> list[tuple[str, str]]:
        """[(ISO day, ok | stale | nopower)] for the run days in [b, e] (Dataset days), oldest first: ok when
        any run that day shows full power, else stale when any had trusted power, else nopower."""
        rank = {OK: 2, STALE_DAY: 1, NOPOWER: 0}
        by_day: dict[str, str] = {}
        for w in getattr(self.ds, "workouts", None) or []:
            if getattr(w, "sport", None) != "run" or not (b <= math.floor(w.day) <= e):
                continue
            m = self.activity(w)["mode"]
            st = OK if m == FULL else STALE_DAY if m == STALE else NOPOWER
            d = _day(w).isoformat()
            if d not in by_day or rank[st] > rank[by_day[d]]:
                by_day[d] = st
        return sorted(by_day.items())


# a single-activity card that is power analysis through and through (power_use's power-only cards but the
# 間歇判讀 verdict, which SP-952 makes by heart rate / pace on a day without power): off unless FULL
DAY_KEEP_SECTIONS = frozenset({"interval_verdict"})


def day_power_only(ch: dict) -> bool:
    """This single-activity chart shows only with full power that day (power zones, the CP test, W′, the
    per-rep power cards)."""
    from backend.engine.wko5expr import power_use as PU
    if ch.get("kind") == "review" and ch.get("section") in DAY_KEEP_SECTIONS:
        return False
    return PU.chart_needs_power(ch)


def activity_view(ds, w) -> dict:
    """View(ds).activity(w) for one activity."""
    return View(ds).activity(w)


def spans(states: list[tuple[str, str]]) -> list[dict]:
    """The stretches of a range without valid power: consecutive run days without it merged into one
    (a stretch runs from its first run day to the day before the next valid one), so a watch-power run in the
    middle of a stale-CP stretch doesn't cut it in pieces: [{"from", "to", "kind", "label"}] — kind 「stale」
    when any run in it had a Stryd power on a stale CP (「CP 過期」), else 「nopower」 (「沒有功率」)."""
    out: list[dict] = []
    for i, (d, st) in enumerate(states):
        nxt = states[i + 1][0] if i + 1 < len(states) else None
        end = d if nxt is None else (dt.date.fromisoformat(nxt) - dt.timedelta(days=1)).isoformat()
        if st == OK:
            continue
        if out and out[-1]["_next"] == d:
            out[-1]["to"], out[-1]["_next"] = end, nxt
            out[-1]["kind"] = STALE_DAY if STALE_DAY in (out[-1]["kind"], st) else NOPOWER
            continue
        out.append({"from": d, "to": end, "kind": st, "_next": nxt})
    return [{"from": s["from"], "to": s["to"], "kind": s["kind"], "label": _(SPAN_LABEL[s["kind"]])} for s in out]


def state_on(states: list[tuple[str, str]], day: str) -> Optional[str]:
    """The state a day of the range is in: the last run day's on or before it (the first run day's before
    any); None without runs."""
    if not states:
        return None
    cur = states[0][1]
    for d, st in states:
        if d > day:
            break
        cur = st
    return cur


def is_power_series(s: dict) -> bool:
    from backend.engine.wko5expr import power_use as PU
    return PU._classify(s.get("expression") or "") == "power"


def mask_power(res: dict, states: list[tuple[str, str]]) -> dict:
    """A rendered athlete chart with the points of its power series (power_use's classification: power,
    NP, Pw:HR, CP…) blanked on the days without valid power, the shaded stretches as `power_spans`.
    Unchanged when it has no dated power series."""
    series = res.get("series") or []
    hit = False
    out = []
    for s in series:
        d = s.get("data") or {}
        if d.get("kind") == "points" and d.get("x") in ("date", "datetime") and is_power_series(s):
            pts = [[x, y if state_on(states, str(x)[:10]) in (OK, None) else None] for x, y in d.get("points") or []]
            out.append({**s, "data": {**d, "points": pts}})
            hit = True
        else:
            out.append(s)
    if not hit:
        return res
    sp = spans(states)
    res = {**res, "series": out, "power_spans": sp}
    from backend.engine.wko5expr.render import chart_is_empty
    if not res.get("empty") and chart_is_empty(out):
        res["empty"] = _(NO_VALID)
    return res


def _is_cp_col(name) -> bool:
    import re
    return bool(re.match(r"^\s*%\s?CP\b", str(name or "")))


def strip_cp(res: dict, mode: str = STALE) -> dict:
    """A single-activity card without what needs power in effect that day (a STALE / NONE activity): the
    「%CP」 columns (the climbs table), the 本次重點 tiles' 有氧／無氧訓練效果 (power against the PD model) and the
    「CP …」 under 平均功率; NONE drops every power tile (`power` cards: 平均功率 too)."""
    out = res
    series = res.get("series")
    if series and any(_is_cp_col(s.get("name")) for s in series):
        out = {**out, "series": [s for s in series if not _is_cp_col(s.get("name"))]}
    cards = res.get("cards")
    if cards:
        kept = []
        for c in cards:
            if not isinstance(c, dict):
                kept.append(c)
            elif c.get("id") == "tis" or (mode == NONE and c.get("power")):
                continue
            elif c.get("id") == "power" and c.get("sub"):
                kept.append({k: v for k, v in c.items() if k != "sub"})
            else:
                kept.append(c)
        if kept != cards:
            out = {**out, "cards": kept}
    return out


def no_valid(res: dict) -> dict:
    """A power-only athlete chart over a range without any valid power: its note instead of a drawing."""
    return {**res, "empty": _(NO_VALID)}


PRIMARY_NO_POWER = N_("心率／配速")


def targets_by_state(targets: dict, ps: Optional[dict]) -> dict:
    """The 課表建議強度 table (zones.training_targets) by today's power state: unchanged in ①; in ② / ③ no
    watts and no 「看功率」 (the 課表 trains by heart rate / pace then, target_policy) — `power_off` tells
    the page to drop the CP line and the power column."""
    if not ps or ps.get("power"):
        return targets
    rows = [{**r, "power": [None, None], "power_pct": [None, None],
             "primary": _(PRIMARY_NO_POWER) if r.get("primary") == "功率" else r.get("primary")}
            for r in targets.get("rows") or []]
    return {**targets, "rows": rows, "power_off": True}


def range_valid(states: list[tuple[str, str]]) -> bool:
    """The range has any day with valid power (a power-only chart is shown)."""
    return any(st == OK for _d, st in states)
