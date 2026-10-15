"""
門檻變更紀錄 (SP-467; SP-449 Q2 ③ option B): which threshold changed on which day, from what to
what, and where it came from — so a 課表 session whose zones differ from the last one of its kind
can be traced to its cause. Shown on 設定 › 閾值 (`GET /api/v1/plan/threshold-history`).

Read-only and built from what is already stored (the 課表's session format is unchanged):

  * the season plan's dated rows (plan.thresholds): LTHR / AeT / CP / max HR / resting HR, by how
    they were obtained — 手動 (typed), 測試 (a test / race / lab), 套用的自動估算 (an applied
    estimate);
  * the Stryd PD fits the 課表's CP comes from before a plan CP (FitFolderDataset._cp_est: the
    30-day grid plus the last run's day, SP-449) — read off the Dataset, never refitted here;
  * the as-of LTHR (the Dataset's dated `runthr` setting: the 30-day estimate grid, the 0.90 × max
    HR prior or the watch's LTHR, labelled by the Dataset);
  * the watch account's HR settings (athlete.coros_profile_history, SP-67: max / resting HR, the
    zone ratios), which move the 課表心率區間 under % HRR / % HRmax;
  * the 課表心率區間 model (MODEL_HISTORY_KEY) — it kept no history before SP-467, so switches are
    recorded from then on (api/plan.py put_hr_profile) and a note says the older ones are missing.

CP and LTHR entries carry `in_effect`: whether the value the 課表 uses changed that day — read with
the Dataset's own lookups (`ds.cp` / `ds.sport_setting("thr")` on the last run moved to that day,
as zones.cp_in_effect / training_targets do), so a plan test that wins over an estimate, or any rule
deciding when a new fit is taken, shows as 「課表仍用 …」 instead of a change that never happened.
"""
from __future__ import annotations

import bisect
import datetime as dt
import math
from typing import Optional

from backend.i18n import N_, _

MODEL_HISTORY_KEY = "plan.hr_zone_model_history"   # user_settings: [{date, from, to}], oldest first
MODEL_HISTORY_MAX = 200

KEYS = ("cp", "lthr", "aethr", "mhr", "rhr", "hr_model", "hr_zones")
NAMES = {"cp": "CP", "lthr": "LTHR", "aethr": "AeT", "mhr": N_("最大心率"), "rhr": N_("靜息心率"),
         "hr_model": N_("課表心率區間"), "hr_zones": N_("手錶的心率區間設定")}
UNITS = {"cp": "W", "lthr": "bpm", "aethr": "bpm", "mhr": "bpm", "rhr": "bpm"}
SOURCES = {"manual": N_("手動"), "test": N_("測試"), "estimate": N_("套用的自動估算"), "pd_fit": N_("PD 擬合"),
           "auto": N_("自動估算（你的跑步）"), "watch": N_("手錶帳號設定"), "hr_model": N_("心率模型"),
           "hr_model_auto": N_("自動區間換了生效的區間")}
# what a blank 「從」 means for a plan-only threshold (no earlier plan row)
FROM_AUTO = {"aethr": N_("推估（0.89 × LTHR）"), "mhr": N_("自動"), "rhr": N_("自動")}

NOTE_KEPT = N_("課表仍用 {v}")
NOTE_MODEL = N_("課表心率區間（心率模型）的切換以前沒有留紀錄，這裡只有這次更新之後的切換，之前的沒有；目前用 {model}。")
NOTE_WATCH = N_("手錶帳號的心率設定從 {day} 起有紀錄（每次同步時比對，日期是發現改變的那次同步）。")
NOTE_AET = N_("沒有列未套用的 AeT 自動估算：它不改課表的區間（輕鬆跑上限用課表心率區間的 Z2 上緣，量過的 AeT 才優先）。")
NOTE_MHR = N_("最大心率的自動估算（近 365 天跑步）沒有留歷史，沒有列；手錶帳號和你填的值有列。")
NOTE_GRID = N_("自動估算的 LTHR 和 PD 擬合每 30 天算一次（PD 擬合另外算最後一次跑步那天），列的是有變的那幾次。")
NOTE_MSGIDS = (NOTE_KEPT, NOTE_MODEL, NOTE_WATCH, NOTE_AET, NOTE_MHR, NOTE_GRID)
ZONES_CHANGED = N_("區間比例改過")


# ---------------------------------------------------------------------------
# the 課表心率區間 journal (written by api/plan.py put_hr_profile)
# ---------------------------------------------------------------------------

def _model_ok(m) -> bool:
    from backend.engine.hr_profile import SETTING_MODELS
    return m in SETTING_MODELS


def validate_model_history(value) -> None:
    """settings/repository.validate for MODEL_HISTORY_KEY: None or [{date: YYYY-MM-DD, from, to}]."""
    if value is None:
        return
    ok = isinstance(value, list) and len(value) <= MODEL_HISTORY_MAX
    for r in value if ok else ():
        try:
            dt.date.fromisoformat(str(r.get("date")))
        except (AttributeError, ValueError):
            ok = False
            break
        if not (_model_ok(r.get("from")) and _model_ok(r.get("to"))):
            ok = False
            break
    if not ok:
        raise ValueError(f"{MODEL_HISTORY_KEY} must be a list of {{date: YYYY-MM-DD, from, to}} "
                         f"(models lthr / hrr / hrmax, at most {MODEL_HISTORY_MAX}) or null")


def model_history_add(history, day: dt.date, old: str, new: str, reason: Optional[str] = None) -> Optional[list]:
    """The journal with one switch appended; None when nothing switched. `reason` (SP-493): a switch
    the 自動 setting made by itself (old / new = the models in effect), with why."""
    if old == new:
        return None
    rows = [r for r in history if isinstance(r, dict)] if isinstance(history, list) else []
    rows.append({"date": day.isoformat(), "from": old, "to": new,
                 **({"auto": True, "reason": reason} if reason is not None else {})})
    return rows[-MODEL_HISTORY_MAX:]


# ---------------------------------------------------------------------------
# the entries
# ---------------------------------------------------------------------------

def _r(v) -> Optional[float]:
    return None if v is None else round(float(v), 1)


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 0.05


def _entry(day: str, key: str, frm, to, source: str, detail: str = "", note: str = "",
           in_effect: Optional[bool] = None, from_label: str = "", to_label: str = "") -> dict:
    return {"date": day, "key": key, "name": _(NAMES[key]), "from": frm, "to": to, "unit": UNITS.get(key),
            "source": source, "source_label": _(SOURCES[source]), "detail": detail, "note": note,
            "in_effect": in_effect, "from_label": from_label, "to_label": to_label}


def _plan_source(t, key: str) -> tuple[str, str]:
    """(source id, detail) of a plan row's `key`."""
    from backend.engine import planning as P
    if key == "cp":
        if t.cp_manual:
            return "manual", ""
        from backend.engine.cp_protocols import METHOD_LABEL
        return "test", _(METHOD_LABEL[t.cp_method]) if t.cp_method in METHOD_LABEL else ""
    if key in ("lthr", "aethr"):
        m = P.threshold_method(t, key)
    elif key == "mhr":
        m = t.mhr_method or "manual"
    else:
        m = "manual"
    if m == "estimate":
        return "estimate", ""
    if m == "manual":
        return "manual", ""
    return "test", _(P.METHOD_LABEL.get(m, m))


def _plan_rows(plan, key: str, today: dt.date) -> list:
    """(date, value, row) of the plan rows holding `key`, in effect by today, oldest first."""
    rows = [(str(t.date)[:10], float(getattr(t, key)), t) for t in plan.thresholds
            if getattr(t, key, None) is not None and str(t.date)[:10] <= today.isoformat()]
    return sorted(rows, key=lambda r: r[0])


class _OnDay:
    """The value the 課表 uses on a day: the Dataset's own lookup on the last run on or before the
    day, moved to that day (zones._on_day — as zones.cp_in_effect and training_targets)."""

    def __init__(self, ds):
        from backend.engine.wko5expr.dataset import date_to_day
        from backend.engine.zones import _on_day
        self.ds, self._d2d, self._move = ds, date_to_day, _on_day
        self.runs = sorted((w for w in ds.workouts if w.sport == "run"), key=lambda w: w.day)
        self.floors = [math.floor(w.day) for w in self.runs]

    def ref(self, day: dt.date):
        end = int(self._d2d(day))
        i = bisect.bisect_right(self.floors, end)
        return None if i == 0 else self._move(self.runs[i - 1], end)

    def cp(self, day: dt.date) -> Optional[float]:
        ref = self.ref(day)
        return None if ref is None else _r(self.ds.cp(ref))

    def lthr(self, day: dt.date) -> Optional[float]:
        ref = self.ref(day)
        return None if ref is None else _r(self.ds.sport_setting("thr", ref))


def _layered(key: str, plan_rows: list, auto: list, auto_source: str, on, unit: str, auto_detail: str = "") -> list:
    """Entries of a threshold with a plan layer over an automatic one (CP: the PD fits; LTHR: the
    as-of estimates). A plan row: from what was in effect the day before to its value. An automatic
    value that changed: in effect when the 課表's value changed that day, else 「課表仍用 …」. Before
    the first run there is nothing to look up: a plan row then reads from the plan row before it.
    A row or a value equal to what was there before is no change and is left out."""
    out = []
    events = [(d, 0, v, row) for d, v, row in plan_rows] + [(d, 1, v, None) for d, v in auto]
    prev_auto = prev_plan = None
    for d, layer, v, row in sorted(events, key=lambda e: (e[0], e[1])):
        day = dt.date.fromisoformat(d)
        before, after = on(day - dt.timedelta(days=1)), on(day)
        if layer == 0:
            v = _r(v)
            frm = before if before is not None else prev_plan
            prev_plan = v
            if _same(frm, v):
                continue
            src, detail = _plan_source(row, key)
            out.append(_entry(d, key, frm, v, src, detail, row.note or "",
                              in_effect=after is None or _same(after, v)))
            continue
        v = _r(v)
        if _same(v, prev_auto):
            continue
        if not _same(before, after):
            out.append(_entry(d, key, before, after, auto_source, auto_detail, in_effect=True))
        else:
            kept = "–" if after is None else f"{after:g} {unit}"
            out.append(_entry(d, key, prev_auto, v, auto_source, auto_detail, note=_(NOTE_KEPT, v=kept),
                              in_effect=False))
        prev_auto = v
    return out


def _plan_only(plan, key: str, today: dt.date) -> list:
    """The plan rows of a threshold alone (AeT / max / resting HR; CP / LTHR without a Dataset):
    from = the plan row before; a row repeating the value before it is no change."""
    out, prev = [], None
    for d, v, row in _plan_rows(plan, key, today):
        v = _r(v)
        if _same(prev, v):
            continue
        src, detail = _plan_source(row, key)
        out.append(_entry(d, key, prev, v, src, detail, row.note or "",
                          from_label=_(FROM_AUTO[key]) if prev is None and key in FROM_AUTO else ""))
        prev = v
    return out


def _watch(history, tz) -> tuple[list, Optional[str]]:
    """Entries of the watch account's HR settings (coros_compare history) and its first day."""
    from backend.engine.coros_compare import _day, _values
    rows = [r for r in history if isinstance(r, dict)] if isinstance(history, list) else []
    out = []
    for a, b in zip(rows, rows[1:]):
        day = _day(b.get("at"), tz)
        if day is None:
            continue
        for k, key in (("max_hr", "mhr"), ("rest_hr", "rhr")):
            if a.get(k) != b.get(k):
                out.append(_entry(day, key, a.get(k), b.get(k), "watch"))
        va, vb = _values(a), _values(b)
        if va["ratios"] != vb["ratios"] or va["hr_zone_type"] != vb["hr_zone_type"]:
            out.append(_entry(day, "hr_zones", None, None, "watch", note=_(ZONES_CHANGED)))
    return out, (_day(rows[0].get("at"), tz) if rows else None)


def _models(history) -> list:
    from backend.engine.hr_profile import MODEL_SHORT
    rows = [r for r in history if isinstance(r, dict)] if isinstance(history, list) else []
    lab = lambda m: _(MODEL_SHORT[m]) if m in MODEL_SHORT else str(m)    # noqa: E731
    # SP-493: a switch the 自動 setting made by itself ({auto: true, reason}) says why
    return [_entry(r["date"], "hr_model", r.get("from"), r.get("to"), "hr_model_auto" if r.get("auto") else "hr_model",
                   detail=str(r.get("reason") or "") if r.get("auto") else "",
                   from_label=lab(r.get("from")), to_label=lab(r.get("to")))
            for r in rows if r.get("date")]


def build(plan, ds, today: dt.date, model_history=None, coros_history=None, model_now: Optional[str] = None,
          tz=None) -> dict:
    """{"entries": newest first [{date, key, name, from, to, unit, source, source_label, detail,
    note, in_effect, from_label, to_label}], "notes": [what is not in the list and why]}.
    `ds` = the charts' Dataset (its PD fits and as-of LTHR; None = the plan rows only)."""
    entries = []
    if ds is not None:
        on = _OnDay(ds)
        fits = [(r["date"].isoformat(), r["cp"]) for r in getattr(ds, "_cp_est", None) or []
                if r.get("cp") and r["date"] <= today]
        entries += _layered("cp", _plan_rows(plan, "cp", today), fits, "pd_fit", on.cp, "W")
        auto = [(d.isoformat(), v) for d, v in (ds.athlete.settings.get("runthr") or [])
                if v is not None and d <= today]
        # where the dated LTHR comes from, as the Dataset says it (the estimate grid, the max-HR prior,
        # the watch, a WKO5 file)
        label = ds.setting_label("runthr") if hasattr(ds, "setting_label") else ""
        entries += _layered("lthr", _plan_rows(plan, "lthr", today), auto, "auto", on.lthr, "bpm", label)
    else:
        for key in ("cp", "lthr"):
            entries += _plan_only(plan, key, today)
    for key in ("aethr", "mhr", "rhr"):
        entries += _plan_only(plan, key, today)
    watch, watch_from = _watch(coros_history, tz)
    entries += watch
    entries += _models(model_history)
    order = {k: i for i, k in enumerate(KEYS)}
    entries.sort(key=lambda e: (e["date"], -order[e["key"]]), reverse=True)
    from backend.engine.hr_profile import DEFAULT_PLAN_MODEL, MODEL_SHORT
    m = model_now if model_now in MODEL_SHORT else DEFAULT_PLAN_MODEL
    notes = [_(NOTE_GRID), _(NOTE_MODEL, model=_(MODEL_SHORT[m]))]
    if watch_from:
        notes.append(_(NOTE_WATCH, day=watch_from))
    notes += [_(NOTE_AET), _(NOTE_MHR)]
    return {"entries": entries, "notes": notes}

