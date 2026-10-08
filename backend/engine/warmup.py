"""
課表偏好「每堂課前加熱身」 (SP-364; plan.prefs.warmup_on / warmup_min, 5–30 min, default off / 10):
every generated run session — and every template inserted (workout_steps.templates) — starts with
a warm-up of at least the set minutes. The owner's rule: a session or template that already has a
warm-up keeps it and is only topped up to the set minutes (a longer one is never shortened).

How the minutes are counted (decided for SP-364; 推估 where it says so):
  * **No warm-up of its own** (easy run, long run, 越野／健行, the heat run's own 10′): the first N
    minutes of the session ARE the warm-up — a warm-up at easy intensity is the same running, so the
    planned minutes, the TSS, the week's time budget and the day caps stay as planned. The detail gets
    「含暖身 N 分」 (MARK) and the step builders (workout_steps.derive → the COROS push) split the
    session there. A run too short to keep MIN_MAIN minutes after the warm-up (plus its strides /
    marathon-pace / cool-down) is lengthened to fit (TSS scaled with the minutes).
  * **Its own warm-up** (intervals, CP / AeT tests, the structured 技術地形／下坡 sessions): topped up to
    N, the main set is never shortened. The extra minutes count in the session (minutes + TSS at the
    easy-run rate). A library interval gets the floor inside its warm-up block
    (interval_library.blocks): the day-cap fitting sees it and the week's easy runs give the minutes
    back; the text intervals and tests are topped up before the easy runs are sized (week_plan /
    projection.week_sessions), so the week keeps its total; sessions the later passes add or rebuild
    (技術地形／下坡 with their 10′ warm-up, the 減量期 / B-race intervals) get it on top of the week
    (only when the floor is longer than their own warm-up). Day caps: a library interval is fitted
    with the floor (interval_library.fit); a text interval is never trimmed below it
    (plan_prefs.trim_quality: a rep goes first); the CP / AeT tests are cap-exempt as before.
Not decorated (known gap): plan_auto's automatic downgrades (engine/adapt.py) keep their text.
Never: strength, rest, race, notices, passive heat, the injury walk-run stages (a clinical
protocol), a done session, the user's own / edited sessions (only the generator's output is
decorated; reconcile keeps edited rows) — and nothing at all while the switch is off.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from backend.i18n import N_

MIN_MAIN = 10                     # 推估: minutes of easy running an easy-type session keeps after its warm-up
MIN_RANGE = (5, 30)               # plan.prefs.warmup_min (settings/repository.PREF_INTS: the same)
SKIP_KINDS = ("strength", "rest", "race", "notice", "heat_passive")
EASY_KINDS = ("easy", "long", "hike", "mountain")
TEXT_KINDS = ("quality", "test")
MARK = N_("含暖身 {n} 分")          # the easy-type session's warm-up (workout_steps.derive splits there)
WARM_TEXT = N_("暖身 {n} 分")       # the token the interval / test parsers read (workout_steps, coros_workouts)


def _pat(tmpl: str, lookbehind: str = "") -> re.Pattern:
    """「X {n} 分」 → X\\s*(\\d+)\\s*分 (the parsers' spacing), optionally not preceded by `lookbehind`."""
    a, b = tmpl.split("{n}")
    return re.compile((f"(?<!{re.escape(lookbehind)})" if lookbehind else "")
                      + re.escape(a.strip()) + r"\s*(\d+)\s*" + re.escape(b.strip()))


MARK_RE = _pat(MARK)
WARM_RE = _pat(WARM_TEXT, MARK[:MARK.index(WARM_TEXT[:2])])     # 「暖身 N 分」 but not the mark
DEFAULT_WARM = {"quality": 15}    # workout_steps._quality_text's default when the text has none


def floor_min(prefs) -> int:
    """The warm-up floor in minutes; 0 = off (no prefs, the switch off)."""
    if prefs is None or getattr(prefs, "warmup_on", False) is not True:
        return 0
    try:
        return max(0, int(getattr(prefs, "warmup_min", 10) or 0))
    except (TypeError, ValueError):
        return 0


def mark_min(s: dict) -> Optional[int]:
    """The 「含暖身 N 分」 of an easy-type session, or None."""
    m = MARK_RE.search(s.get("detail") or "")
    return int(m.group(1)) if m else None


def _rate(rates: Optional[dict]) -> float:
    r = rates or {}
    return float(r.get("easy") or r.get("road") or 60.0)


def _grow(s: dict, minutes: float, rate: float) -> None:
    s["minutes"] = int(round((s.get("minutes") or 0) + minutes))
    s["tss"] = float(s.get("tss") or 0.0) + minutes / 60.0 * rate


def _extra_min(s: dict) -> float:
    """Minutes of an easy-type session that are neither warm-up nor easy running (strides, the
    marathon-pace part and its easy tail, the heat run's walk cool-down)."""
    from backend.engine import workout_steps as WS
    st = WS.strides_of(s.get("title")) if s.get("kind") == "easy" else None
    if st:
        return st[0] * (st[1] + 60) / 60.0
    mp = WS.mp_minutes(s) if s.get("kind") == "long" else None
    if mp:
        return mp + WS.MP_TAIL_S / 60.0
    if s.get("kind") == "easy" and WS.heat_run(s):
        return WS.HEAT_COOL_S / 60.0
    return 0.0


def _carve(s: dict, n: int) -> bool:
    """An easy-type session: its first `n` minutes are the warm-up (the mark in the detail)."""
    old = int(s.get("minutes") or 0)
    if old <= 0:
        return False
    need = int(math.ceil(n + MIN_MAIN + _extra_min(s)))
    if mark_min(s) == n and old >= need:
        return False
    det = s.get("detail") or ""
    mark = MARK.format(n=n)
    det = MARK_RE.sub(mark, det) if MARK_RE.search(det) else (det + "；" if det else "") + mark
    s["detail"] = det
    if old < need:
        s["tss"] = float(s.get("tss") or 0.0) * need / old
        s["minutes"] = need
    return True


def _text(s: dict, n: int, rate: float) -> bool:
    """An interval / test whose warm-up is in its text (「暖身 N 分」): top it up to `n`."""
    det, tgt = s.get("detail") or "", s.get("target") or ""
    m = WARM_RE.search(det) or WARM_RE.search(tgt)
    if m:
        cur = int(m.group(1))
    elif s.get("kind") == "test":
        from backend.engine import aet_test as AT
        from backend.engine import cp_protocols as CPP
        if AT.is_aet_session(s):
            cur = 10
        else:
            proto = CPP.protocol_of(s) or "standard"
            if proto not in CPP.TABLE or not s.get("minutes"):
                return False                            # 用比賽當 CP 測試: nothing to warm up
            cur = int(CPP.TABLE[proto]["warm"])
    else:
        cur = DEFAULT_WARM.get(s.get("kind"), 15)
    if cur >= n:
        return False

    def up(text: str) -> str:
        return WARM_RE.sub(lambda x: WARM_TEXT.format(n=n) if int(x.group(1)) < n else x.group(0), text)
    if m:
        s["detail"], s["target"] = up(det), up(tgt) if tgt else tgt
    else:
        s["detail"] = (det + "；" if det else "") + WARM_TEXT.format(n=n)
    _grow(s, n - cur, rate)
    return True


def _variant(s: dict, n: int, prefs, rate: float) -> bool:
    """A library interval built without the floor (interval_library.session_for stores it in
    variant_adj["warm"] when it is): the floor goes into variant_adj, the extra minutes on top."""
    from backend.engine import interval_library as IL
    adj = dict(s.get("variant_adj") or {})
    if int(adj.get("warm") or 0) >= n:
        return False
    v = IL.resolve(s.get("variant_key"), s.get("variant_reps"), adj)
    if v is None:
        return _text(s, n, rate)
    lv = s.get("variant_blocks") or "std"
    b0 = IL.blocks(v, lv, prefs, floor=int(adj.get("warm") or 0))
    b1 = IL.blocks(v, lv, prefs, floor=n)
    adj["warm"] = n
    s["variant_adj"] = adj
    add = b1["warm_min"] - b0["warm_min"]
    if add > 0:
        s["detail"] = WARM_RE.sub(WARM_TEXT.format(n=b1["warm_min"]), s.get("detail") or "", count=1)
        _grow(s, add, rate)
    return True


def _steps(s: dict, n: int, rate: float) -> bool:
    from backend.engine import workout_steps as WS
    st = s.get("steps")
    if not isinstance(st, dict) or not st.get("items"):
        return False
    items, add = WS.ensure_warm(st["items"], n * 60)
    if not add:
        return False
    s["steps"] = {**st, "items": items}
    _grow(s, add / 60.0, rate)
    return True


def apply_one(s: dict, prefs, rates: Optional[dict] = None) -> bool:
    """Give one generated session (a dict) its warm-up; True when it changed. Idempotent."""
    n = floor_min(prefs)
    kind = s.get("kind")
    if not n or s.get("done") or kind in SKIP_KINDS or str(s.get("id") or "").startswith("walkrun"):
        return False
    rate = _rate(rates)
    if s.get("steps"):
        return _steps(s, n, rate)
    if kind == "quality" and s.get("variant_key"):
        return _variant(s, n, prefs, rate)
    if kind in TEXT_KINDS:
        return _text(s, n, rate)
    if kind in EASY_KINDS:
        return _carve(s, n)
    return False


def apply(sessions: list, prefs, rates: Optional[dict] = None) -> int:
    """apply_one over a list of session dicts (in place); the number changed."""
    if not floor_min(prefs):
        return 0
    return sum(1 for s in sessions if isinstance(s, dict) and apply_one(s, prefs, rates))


FIELDS = ("minutes", "tss", "detail", "target", "variant_adj", "steps")


def apply_objs(sessions: list, prefs, rates: Optional[dict] = None) -> int:
    """The same on overview.Session objects (their extra attributes, e.g. _long_day, kept)."""
    if not floor_min(prefs):
        return 0
    n = 0
    for s in sessions:
        d = {k: getattr(s, k, None) for k in ("id", "kind", "title", "done", "heat") + FIELDS}
        if apply_one(d, prefs, rates):
            for k in FIELDS:
                setattr(s, k, d[k])
            n += 1
    return n
