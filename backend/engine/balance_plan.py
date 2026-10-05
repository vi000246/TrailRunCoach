"""
平衡／腳踝 (SP-120, reworked by the owner 2026-10-05) — docs/research/baiyue-technical-terrain.md §3.2,
§6.1; strength-session-design.md §3.5, §5 (「和平衡小課：可以同一天，接在肌力課最後」).

Why: the ankle is the first injury of hikers who fall (42.4 %, Faulhaber 2020) and 75 % of the
falls happen descending (Faulhaber 2017); balance / proprioceptive training cuts ankle sprains by
35 % (RR 0.65, Schiftan 2015 meta-analysis), 1 recurrence prevented per 9 athletes after an
8-week home programme (Hupperets 2009, BMJ). Dose per session: Lesinski 2015 (meta-analysis,
healthy young adults; the outcome is balance performance, not injuries) — 11–15 min, 4 exercises
× 2 sets × 21–40 s.

What (owner 2026-10-05): balance is part of strength training. It is a BLOCK at the end of the
week's strength session(s) (attach: the title says so, +MINUTES minutes, no TSS of its own), not a
session on its own days and not a kind of its own. One fixed set of moves (MOVES), no stages, no
week counter; in a 百岳's 專項期 / 減量期 the stance moves may be done with a pack (one line).
Warm-up: 徐國峰's loaded ankle mobility (stand, weight on the feet, roll in / out — 跑力提升 8.2.1.3,
教練級).

When: only while the next A race is a 越野賽 / 百岳 (strength_plan.next_trail_a), in its 轉換期,
回量期, 基礎期, 專項期 and 減量期 — never on / after the race start; not in the 恢復期 (PHASES). Where
the strength session is gone — the SP-86 stop in the 減量期, the race week — balance stays (SP-120:
「賽前照排」): ensure() adds one balance-only strength session (kind strength, so the strength rules
apply: a side session, not pushed to the watch) on an easy-run day, else a free day, before the race.
Applied after the day rules (overview.week_plan, projection.project_weeks), so the SP-86 stop and
the post-race rules don't remove it again.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from backend.engine import strength_plan as STP
from backend.i18n import N_, _

MINUTES = 12                     # Lesinski 2015: 11–15 min
PHASES = ("transition", "rebuild", "base", "specific", "taper")
ID = "balance"                   # the balance-only strength session (ensure)
MOVES = N_("單腳站（張眼 → 閉眼）；單腳站、另一腳往前、側、後伸出去點地；單腳往前、側小跳，落地停住 2 秒；"
           "單腳提踵慢放")
PACK = N_("百岳的專項期、減量期：單腳站和伸腳點地可以背 5–10% 體重的背包做")
SRC = N_("Schiftan 2015（平衡訓練讓踝扭傷少 35 %）；Hupperets 2009；Lesinski 2015（劑量，指標是平衡表現）；"
         "徐國峰（熱身，教練級）；動作組合是推估")


def _g(x, k):
    return x.get(k) if isinstance(x, dict) else getattr(x, k, None)


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def week_context(events, monday: dt.date, kind: str) -> dict:
    """{active, race, race_kind, race_start, pack} of the week: `kind` = the week's phase (week_plan's
    status.kind / the projection's phase_kind)."""
    r = STP.next_trail_a(events, monday)
    if not r or kind not in PHASES:
        return {"active": False}
    return {"active": True, "race": r["name"], "race_kind": r["kind"], "race_start": r["start"],
            "pack": r["kind"] == "baiyue" and kind in ("specific", "taper")}


def block(ctx: dict) -> str:
    """The balance block's text (the warm-up, the moves, the dose; the pack line for a 百岳)."""
    t = _("平衡／腳踝 {m} 分：熱身 1–2 分承重式腳踝活動度——彈性站姿、體重壓在腳上，慢慢做內翻、外翻（徐國峰）；"
          "接著 4 個動作，每個 2 組 × 20–40 秒／腳：{moves}", m=MINUTES, moves=_(MOVES))
    if ctx.get("pack"):
        t += "；" + _(PACK)
    return t


def title_tag() -> str:
    return _("＋平衡／腳踝")


def _before_race(s, ctx) -> bool:
    d = _d(_g(s, "day"))
    return d is None or d < _d(ctx["race_start"])


def _set(s, **kw) -> None:
    for k, v in kw.items():
        if isinstance(s, dict):
            s[k] = v
        else:
            setattr(s, k, v)


def attach(ss: list, ctx: Optional[dict]) -> int:
    """Append the balance block to the week's not-done strength sessions before the race (dicts or
    Session objects, in place): title + title_tag(), +MINUTES minutes, the block in the detail, the
    sources. Their TSS stays (no conversion for balance). Returns how many carry it."""
    if not ctx or not ctx.get("active"):
        return 0
    n = 0
    for s in ss:
        if _g(s, "kind") != "strength" or _g(s, "done") or _g(s, "id") == ID or not _before_race(s, ctx):
            continue
        n += 1
        if title_tag() in str(_g(s, "title") or ""):
            continue                                    # already (a projected week passed twice)
        _set(s, title=str(_g(s, "title") or "") + title_tag(), minutes=int(_g(s, "minutes") or 0) + MINUTES,
             detail="；".join(x for x in (_g(s, "detail"), block(ctx)) if x),
             source="；".join(x for x in (_g(s, "source"), _(SRC)) if x))
    return n


def session(ctx: dict, day: Optional[str]) -> dict:
    """The balance-only strength session (a plain dict, as week_plan's sessions)."""
    return {"id": ID, "kind": "strength", "title": _("肌力：平衡／腳踝 {m} 分", m=MINUTES), "minutes": MINUTES,
            "target": "", "detail": block(ctx) + "；" + _("賽前停了一般肌力，平衡照做（負荷低，不累腿）"),
            "source": _(SRC), "tss": 0.0, "day": day, "done": False, "done_by": None}


def ensure(ss: list, ctx: Optional[dict], days: list, allowed: Optional[Callable] = None) -> Optional[dict]:
    """When the week has no strength session before the race (the SP-86 stop, the race week), add
    the balance-only one to `ss` (dicts, placed) on one of `days` (dates still open: not past, not
    不排課): an easy-run day first (after the run), else a day without a session, else any; before the
    race start and on a 課表偏好 可練日 (`allowed`). Returns it, or None."""
    if not ctx or not ctx.get("active"):
        return None
    if any(_g(s, "kind") == "strength" and _before_race(s, ctx) for s in ss):
        return None
    rs = _d(ctx["race_start"])
    cand = sorted(d for d in days if d < rs and (allowed is None or allowed(d)))
    if not cand:
        return None
    by: dict = {}
    for s in ss:
        d = _d(_g(s, "day"))
        if d is not None:
            by.setdefault(d, set()).add(_g(s, "kind"))
    easy = [d for d in cand if "easy" in by.get(d, set())]
    empty = [d for d in cand if not by.get(d)]
    s = session(ctx, (easy or empty or cand)[0].isoformat())
    ss.append(s)
    return s
