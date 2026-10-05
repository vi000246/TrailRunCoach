"""
不排課日期 — one-off blackout ranges (holiday, trip) on which no session is
planned. Separate from the recurring 可練日 preference (plan.prefs.days).

Stored as the user_settings key `plan.blackouts` (settings/repository.py):
a list of {id, start, end, label[, kind]}, validated there (ISO dates, start <= end,
at most MAX_SPAN days, no overlaps, label <= MAX_LABEL chars). Ranges can be
past or future; only days from today on change the plan.

kind "rest" = a 休息日 the user set on one day from the 課表 calendar's context
menu (api/plan_sessions POST /rest-days). It blocks the day like any range, but
the week keeps its volume: lost_days() skips it, so the generator places the
week's hours on the other days (the placers' own rules) instead of cutting
them — the athlete moved a day off, they didn't lose training time.

Rules (callers: overview.week_plan, projection.project_weeks, reconcile):

  * placement: blocked days are removed from the candidate days before the
    generator places the week, so the existing placers keep their rules (long
    first, hard days apart, strength not the day before the long) and their
    "排不進去" drop path;
  * volume: the week's target hours x (allowed days not blocked / allowed
    days) — `factor()`; a past blocked day that has a workout isn't lost;
  * the week after a week that lost days: the <= 10 % step (at least +0.5 h)
    is taken from what was actually done that week, not from the 4-week mean,
    so the volume doesn't jump back — `step_cap()`;
  * each affected week gets a note (`week_note()`, src "blackout");
  * stored sessions a user edited / added that sit on a blocked day are never
    deleted silently: reconcile reports them as a conflict with `move_to()`
    (the nearest free allowed day of the same week) and applies the user's
    choice (move / delete).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Optional

KEY = "plan.blackouts"
KINDS = ("", "rest")
REST = "rest"
MAX_RANGES = 60
MAX_SPAN = 62                     # days in one range
MAX_LABEL = 30
HARD = {"long", "quality", "test", "hike"}

NOTE = "{rng} 不排課{lbl}，本週少 {h} 小時"
NOTE_STEP = ("上週 {rng} 不排課，實際只練了 {done:.1f} 小時：本週依「週量增幅 ≤ 10%（至少 +0.5 h）」"
             "從實際量起算，上限 {cap:.1f} 小時，不直接跳回原本的量")


@dataclass(frozen=True)
class Blackout:
    id: str
    start: str
    end: str
    label: str = ""
    kind: str = ""                    # "" = 不排課日期, "rest" = 休息日 (volume kept)

    def days(self) -> list[dt.date]:
        a, b = dt.date.fromisoformat(self.start), dt.date.fromisoformat(self.end)
        return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["kind"]:
            del d["kind"]                 # the stored shape (and stamp) of a plain range is unchanged
        return d


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def validate(value) -> None:
    """The stored list (raises ValueError)."""
    if not isinstance(value, list):
        raise ValueError("不排課日期要是一個清單")
    if len(value) > MAX_RANGES:
        raise ValueError(f"最多 {MAX_RANGES} 段不排課日期")
    spans, ids = [], set()
    for r in value:
        if not isinstance(r, dict) or set(r) - {"id", "start", "end", "label", "kind"}:
            raise ValueError("每段不排課日期是 {id, start, end, label}")
        try:
            a, b = dt.date.fromisoformat(str(r.get("start"))), dt.date.fromisoformat(str(r.get("end")))
        except ValueError:
            raise ValueError("不排課日期的格式要是 YYYY-MM-DD")
        if len(str(r.get("start"))) != 10 or len(str(r.get("end"))) != 10:
            raise ValueError("不排課日期的格式要是 YYYY-MM-DD")
        if b < a:
            raise ValueError("結束日不能早於開始日")
        if (b - a).days + 1 > MAX_SPAN:
            raise ValueError(f"一段不排課日期最多 {MAX_SPAN} 天")
        lbl = r.get("label", "")
        if not isinstance(lbl, str) or len(lbl) > MAX_LABEL:
            raise ValueError(f"說明最多 {MAX_LABEL} 個字")
        if r.get("kind", "") not in KINDS:
            raise ValueError(f"不支援的不排課類型：{r.get('kind')!r}")
        rid = r.get("id")
        if not isinstance(rid, str) or not 1 <= len(rid) <= 32 or rid in ids:
            raise ValueError("每段不排課日期要有不重複的 id")
        ids.add(rid)
        spans.append((a, b))
    spans.sort()
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        if a2 <= b1:
            raise ValueError(f"不排課日期重疊：{a2.month}/{a2.day} 已經在另一段裡")


def normalize(body) -> list[dict]:
    """API body -> the stored list (ids filled in, label trimmed, sorted); validated."""
    if not isinstance(body, list):
        raise ValueError("blackouts must be a list")
    out = []
    for r in body:
        if not isinstance(r, dict):
            raise ValueError("每段不排課日期是 {start, end, label}")
        out.append({"id": str(r.get("id") or new_id()), "start": str(r.get("start") or "")[:10],
                    "end": str(r.get("end") or r.get("start") or "")[:10],
                    "label": str(r.get("label") or "").strip(),
                    **({"kind": str(r["kind"])} if r.get("kind") else {})})
    out.sort(key=lambda r: (r["start"], r["end"]))
    validate(out)
    return out


def from_list(values) -> tuple[Blackout, ...]:
    return tuple(Blackout(id=r["id"], start=r["start"], end=r["end"], label=r.get("label") or "",
                           kind=r.get("kind") or "")
                 for r in (values or []))


def load(user_id: int = 1) -> tuple[Blackout, ...]:
    """Synchronous read (read-only sqlite, like plan_prefs.load); bad data -> none."""
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(KEY, [], user_id)
    try:
        validate(v)
        return from_list(v)
    except (TypeError, ValueError, KeyError):
        return ()


def stamp(bos: Iterable[Blackout]) -> str:
    return json.dumps([b.to_dict() for b in bos], sort_keys=True)


def blocked(bos: Iterable[Blackout]) -> dict[str, Blackout]:
    """ISO day -> the range it is in."""
    return {d.isoformat(): b for b in bos for d in b.days()}


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def _runs(days: list[dt.date]) -> list[tuple[dt.date, dt.date]]:
    out: list[list[dt.date]] = []
    for d in sorted(days):
        if out and (d - out[-1][1]).days == 1:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [(a, b) for a, b in out]


def range_text(days: list[dt.date]) -> str:
    return "、".join(_md(a) if a == b else f"{_md(a)}–{_md(b)}" for a, b in _runs(days))


def week_days(monday: dt.date) -> list[dt.date]:
    return [monday + dt.timedelta(days=i) for i in range(7)]


def lost_days(bmap: dict, monday: dt.date, allowed: Optional[Callable[[dt.date], bool]] = None,
              trained: Iterable[dt.date] = ()) -> list[dt.date]:
    """The week's blocked days that were available for training (allowed weekday,
    and not a past day the athlete trained on anyway). A 休息日 (kind rest) is not
    lost: the week's volume goes to its other days."""
    tr = set(trained)
    return [d for d in week_days(monday) if d.isoformat() in bmap and (allowed is None or allowed(d))
            and d not in tr and getattr(bmap[d.isoformat()], "kind", "") != REST]


def factor(monday: dt.date, lost: list[dt.date], allowed: Optional[Callable[[dt.date], bool]] = None) -> float:
    n = sum(1 for d in week_days(monday) if allowed is None or allowed(d))
    return max(0.0, (n - len(lost)) / n) if n else 1.0


def week_note(bmap: dict, lost: list[dt.date], lost_h: float, extra: str = "") -> dict:
    """「9/30–10/4 不排課（連假出遊），本週少 X 小時」 (labels of the ranges involved)."""
    labels = []
    for d in lost:
        lb = bmap[d.isoformat()].label
        if lb and lb not in labels:
            labels.append(lb)
    lbl = f"（{'、'.join(labels)}）" if labels else ""
    return {"level": "info", "src": "blackout",
            "text": NOTE.format(rng=range_text(lost), lbl=lbl, h=f"{lost_h:.1f}") + extra}


def step_cap(last_done: float) -> float:
    """<= 10 % over what was actually done (at least +0.5 h), as the ramp rule."""
    return max(1.10 * last_done, last_done + 0.5)


def step_note(prev_lost: list[dt.date], last_done: float, cap: float) -> dict:
    return {"level": "info", "src": "blackout",
            "text": NOTE_STEP.format(rng=range_text(prev_lost), done=last_done, cap=cap)}


# ---------------------------------------------------------------------------
# stored sessions on a blocked day (reconcile)
# ---------------------------------------------------------------------------

def _hard(s: dict) -> bool:
    return s.get("kind") in HARD or s.get("gen_key") == "long"


def move_to(s: dict, week: list[dict], bmap: dict, today: str,
            allowed: Optional[Callable[[dt.date], bool]] = None) -> Optional[str]:
    """Nearest day of the session's week (>= today, allowed weekday, not blocked)
    with no other main session; a hard session (long / quality / test / hike)
    never next to another hard day. Strength: a day without strength, not the
    day before the long. Ties go to the earlier day. None = no room."""
    d0 = dt.date.fromisoformat(s["day"])
    monday = d0 - dt.timedelta(days=d0.weekday())
    # sessions still on a blocked day are leaving it: they don't hold a day
    others = [x for x in week if x is not s and x.get("day") and x.get("state") in ("active", "done")
              and not (x.get("state") == "active" and x["day"] in bmap)]
    # heat_passive (a hot bath / sauna, engine/heat_plan.py) goes with strength:
    # it never holds a main day
    main_days = {x["day"] for x in others if x["kind"] not in ("strength", "heat_passive", "notice", "balance")}
    hard_days = {dt.date.fromisoformat(x["day"]) for x in others if _hard(x)}
    str_days = {x["day"] for x in others if x["kind"] == "strength"}
    long_days = {dt.date.fromisoformat(x["day"]) for x in others if x["kind"] in ("long", "hike")
                 or x.get("gen_key") == "long"}
    cands = []
    for d in week_days(monday):
        iso = d.isoformat()
        if iso < today or iso in bmap or (allowed is not None and not allowed(d)):
            continue
        if s["kind"] in ("heat_passive", "balance"):
            pass                                  # any open day: it follows the run, not a slot
        elif s["kind"] == "strength":
            if iso in str_days or any(d == ld - dt.timedelta(days=1) for ld in long_days):
                continue
        else:
            if iso in main_days:
                continue
            if _hard(s) and any(abs((d - h).days) <= 1 for h in hard_days):
                continue
        cands.append(d)
    if not cands:
        return None
    return min(cands, key=lambda d: (abs((d - d0).days), d)).isoformat()
