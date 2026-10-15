"""
提示分類、提示多寡設定與每頁上限的共用底層 (SP-482 = docs/research/ui-hint-audit.md §7 T1).

Every hint a page shows (a week note, a calculator warning, …) travels as one object

    {id, cls, prio, smart, text, detail, show, ack, fp}

  id     the row of the audit's inventory (§1: GL1, OV8b, RC14 …; CATALOG below)
  cls    1 會改變行動或安全 (always shown, never capped) · 2 有用但不急 · 3 很少用到或重複
  prio   §5.2's order: 1 安全與傷病 · 2 課表已改或要確認 · 3 比賽來不來得及 · 4 資料錯到會讓建議錯 ·
         5 本週要做的事 · 6 其他解釋
  smart  the §5 condition (R1–R16) holds — a ◐ hint shows only then; the page tickets (SP-483…488)
         compute it, True until they do
  show   three symbols, 完整／標準／精簡 (§1): ● shown · ◐ shown when `smart` · ▷ in 「詳細」 · ✕ not shown
  ack    「知道了」 can put it away (every cls 2 / 3 hint; a cls 1 hint only when the catalog says so —
         OV8b, the B-race warning, R7)
  fp     the condition's fingerprint: a 「知道了」 holds for this fingerprint only, so a changed
         condition (another date, another race) shows again (§4.4 step 4)

The page side is static/hints.js (§4.4's five steps, 3 reminders a page, the 「還有 N 則提醒」 line).

提示多寡 (§4.2): `ui.hint_level` full | standard | lean | None (= automatic). Automatic is 完整 for a
new runner — no 4 good weeks of data (experience.has_history) and the questionnaire says new (or was
never answered) — and 標準 for everyone else. 精簡 is never picked automatically (owner 2026-10-13):
only the runner chooses it; a runner with history, a race in the last year and a ≥ 2 h session in the
last 12 weeks is asked once (`suggest_lean`; 「不要」 is the ack LEAN_ASK). `ui.hint_ack`: the
「知道了」 list [{id, fp, at}].

Module level: stdlib + backend.i18n only — engine/racepower/calc.py imports it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Optional

LEVELS = ("full", "standard", "lean")
LEVEL_KEY = "ui.hint_level"
ACK_KEY = "ui.hint_ack"
ACK_MAX = 300                       # oldest 「知道了」 dropped beyond this (推估: plenty for a season)
CAP = 3                             # §5.2: 3 reminders shown a page (推估; the owner kept 3, 2026-10-13)
LEAN_ASK = "ask.lean"               # the one-time 「要改成精簡嗎？」 answered 「不要」 (§4.2 ③)
SYMBOLS = ("●", "◐", "▷", "✕")
PRIOS = (1, 2, 3, 4, 5, 6)
# §4.2 (推估, the existing constants): a new runner = can't run 30 min, < 3 runs a week or the longest
# < 60 min (experience.py's questionnaire); the one-time lean question: a race result in the last
# LEAN_RACE_DAYS days and a foot session ≥ LEAN_LONG_MIN in the last LEAN_LONG_WEEKS weeks
NEW_RUNS_PER_WEEK = 3
NEW_LONGEST_MIN = 60
LEAN_RACE_DAYS = 365
LEAN_LONG_MIN = 120
LEAN_LONG_WEEKS = 12


@dataclass(frozen=True)
class Entry:
    id: str
    cls: int                        # the row's class (§1 「類」)
    show: str                       # 完／標／精, three of SYMBOLS
    prio: int                       # §5.2
    ack: bool = False               # a cls 1 row 「知道了」 can put away (R7)
    alt: Optional[int] = None       # the row's other class for one case (e.g. 可信度低 → 1); the hint says


# The inventory of docs/research/ui-hint-audit.md §1 (id, 類, 完／標／精, §5.2 priority, flags), one row per
# id; docs/spec/ui-hints.spec.md has each row's text and is checked against this table
# (test_hints.py). Rows the audit merges or removes keep their target display (✕✕✕ = 拿掉／併掉).
# Added (not in the audit): RC (an untagged / old calculator warning: shown as before), RC3w / RC3h (SP-251
# 冷風, SP-253 失溫 — implemented after the audit, registered as cls 1), RC13a (高山症, the 百岳 AMS line),
# PL19 (SP-114 連續賽以哪一段來比) and PL20 (SP-292 資料少: the data source line, 「偏樂觀」) — the feasibility
# card's lines the audit did not list (SP-486); SC15 (SP-542: the 課表 week column's 「這週為什麼這樣排」).
_ROWS = """
GL1 1 ●●● 4
GL2 2 ●●● 6
GL3 2 ●●● 6
GL4 1 ●●● 2
GL5 2 ●◐◐ 5
GL5i 1 ●●● 1
GL6 1 ●●● 4
GL7 2 ▷▷▷ 6
OV1 2 ●◐◐ 6
OV2 1 ●●● 5
OV3 3 ●▷✕ 6
OV4 2 ●▷▷ 6
OV5 2 ●◐▷ 5
OV6 2 ●◐◐ 4 alt=1
OV7 2 ●●● 6
OV8 2 ●◐▷ 6
OV8a 1 ●●● 1
OV8a′ 2 ●▷▷ 1
OV8b 1 ●●● 3 ack
OV8c 1 ●●▷ 5
OV8d 2 ●▷✕ 6
OV8e 1 ●●● 5
OV8f 2 ●◐✕ 6
OV8g 2 ●◐✕ 6 alt=3
OV8h 2 ●◐✕ 6
OV8i 3 ●▷✕ 6
OV8j 2 ●●▷ 2
OV8k 3 ✕✕✕ 5
OV9 2 ▷▷▷ 6
OV10 2 ●●● 5
OV11 2 ●◐◐ 5
OV12 2 ▷▷▷ 6
OV13 2 ●●▷ 6
OV14 3 ●▷✕ 6
SC1 2 ●●● 6
SC2 2 ●●● 6
SC3 3 ✕✕✕ 6
SC4 2 ●◐◐ 5
SC5 1 ●●● 2
SC6 2 ●◐✕ 6
SC7 2 ●◐▷ 6
SC8 1 ●●● 1
SC9 2 ●●● 2 alt=1
SC10 2 ●◐✕ 6
SC11 3 ●▷▷ 6
SC12 1 ●●● 4
SC13 1 ●●● 4
SC14 2 ●●● 6
SC15 2 ●●▷ 6
AC1 1 ●●● 4
AC2 2 ●●● 6
AC3 1 ●●● 1
AC4 3 ●▷✕ 6
AC5 3 ●◐✕ 6
AC6 2 ●▷▷ 6
AC7 2 ●●▷ 6
AC8 3 ●◐✕ 6
AC9 2 ●◐◐ 6
AC10 2 ●▷▷ 6
AC11 1 ●●● 1
AC12 2 ▷▷▷ 6
AN1 2 ●●● 6
AN2 2 ●●● 6
AN3 2 ●▷▷ 6
AN4 1 ●●● 4
AN5 2 ●◐◐ 6
AN6 2 ●◐▷ 6
AN7 2 ●●● 6
AN8 3 ✕✕✕ 6
AN9 2 ●▷▷ 6
AN10 2 ●◐▷ 6 alt=3
AN11 2 ●●● 6
AN12 3 ●◐◐ 6
AN13 3 ●●● 6
AN14 2 ●▷▷ 4 alt=1
AN15 2 ●●● 6
AN16 2 ●●▷ 6
RC 2 ●●● 6
RC1 2 ●▷▷ 6
RC2 1 ◐◐◐ 1
RC3 1 ●●● 1
RC3w 1 ●●● 1
RC3h 1 ●●● 1
RC4 2 ●◐▷ 6
RC5 2 ●●● 6
RC6 1 ●●● 3
RC7 3 ✕✕✕ 6
RC8 3 ▷▷▷ 6
RC9 2 ●▷▷ 6
RC10 2 ●◐▷ 6
RC11 3 ▷▷▷ 6
RC12 2 ●◐▷ 6
RC13 2 ●●▷ 6
RC13a 1 ●●● 1
RC14 1 ●●● 3
RC15 3 ▷▷✕ 6
RC16 1 ●●● 4
RC17 2 ●●▷ 4
RC18 2 ●●● 6
RC19 1 ●●● 5
RC20 1 ●●● 1
RC21 1 ●●● 1
RC22 3 ▷▷✕ 6
RC23 3 ●◐✕ 6
RC24 2 ●◐✕ 6
RC25 2 ●●● 6
RC26 3 ●▷✕ 6
RC27 3 ▷▷✕ 6
RC28 2 ●▷▷ 4 alt=1
RC29 2 ●●▷ 6
RC30 2 ●●● 6
PL1 1 ●●● 4
PL2 2 ●▷▷ 6
PL3 2 ●▷▷ 6
PL4 1 ●●● 3
PL5 3 ●▷✕ 6
PL6 2 ●●▷ 4
PL7 1 ●●● 3
PL8 1 ●●● 3
PL9 1 ●●● 3
PL10 2 ●◐▷ 3 alt=1
PL11 2 ●◐▷ 6
PL12 1 ●●● 5
PL13 2 ●●▷ 6
PL14 1 ●●● 3
PL15 3 ●▷✕ 6
PL16 2 ●●◐ 5
PL17 2 ●▷▷ 6
PL18 2 ▷▷▷ 6
PL19 2 ●▷▷ 6
PL20 2 ●●▷ 4
ST1 3 ●✕✕ 6
ST2 2 ●●● 4 alt=1
ST3 1 ●●● 4
ST4 2 ●●▷ 4
ST5 3 ●▷▷ 6
ST6 2 ●●▷ 6
ST7 2 ●◐◐ 4 alt=1
ST8 2 ●◐◐ 4 alt=1
ST9 2 ●◐◐ 4 alt=1
ST10 1 ●●● 4
ST11 2 ●◐▷ 6
ST12 2 ●●▷ 6
ST13 1 ●●● 4
ST14 2 ▷▷▷ 6
ST15 3 ●●● 6
ST16 2 ▷▷▷ 6
IJ1 1 ●●● 1
IJ2 1 ●●● 1
IJ3 1 ●●● 1
IJ4 2 ●●● 6
IJ5 2 ●●● 4
IJ6 3 ●●● 6
IJ7 3 ●●● 6
IJ8 2 ●▷▷ 6
IJ9 2 ▷▷▷ 6
IJ10 3 ✕✕✕ 6
"""


def _parse(rows: str) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for line in rows.strip().splitlines():
        parts = line.split()
        hid, cls, show, prio, flags = parts[0], int(parts[1]), parts[2], int(parts[3]), parts[4:]
        alt = next((int(f[4:]) for f in flags if f.startswith("alt=")), None)
        assert hid not in out, hid
        assert cls in (1, 2, 3) and prio in PRIOS and len(show) == 3 and set(show) <= set(SYMBOLS), line
        out[hid] = Entry(hid, cls, show, prio, ack="ack" in flags, alt=alt)
    return out


CATALOG: dict[str, Entry] = _parse(_ROWS)


# ---------------------------------------------------------------------------
# one hint
# ---------------------------------------------------------------------------

class Tagged(str):
    """A plain string that knows its catalog id (and, for one case of a row, its class): what the
    engine keeps in its lists of warnings, so every existing string check still works; the response
    turns it into the hint object (`warnings`)."""
    hint_id: str = "RC"
    hint_cls: Optional[int] = None
    hint_smart: bool = True          # the §5 condition (a ◐ row shows only when it holds; SP-485: RC10's R12)

    def __new__(cls, hid: str, text: Any, hint_cls: Optional[int] = None, hint_smart: bool = True):
        s = super().__new__(cls, text)
        s.hint_id, s.hint_cls, s.hint_smart = hid, hint_cls, bool(hint_smart)
        return s

    def __reduce__(self):            # copy / deepcopy / pickle (a cached result) keep the tag
        return (Tagged, (self.hint_id, str(self), self.hint_cls, self.hint_smart))


def tag(hid: str, text: Any, cls: Optional[int] = None, smart: bool = True) -> Tagged:
    """`text` tagged with the catalog row `hid` (a KeyError for an unknown id: a typo fails loudly);
    `smart` = the row's §5 condition holds (False puts a ◐ row in 「詳細」)."""
    entry(hid)
    if isinstance(text, Tagged) and text.hint_id == hid and cls is None and smart is True:
        return text
    return Tagged(hid, text, cls, smart)


def entry(hid: str) -> Entry:
    try:
        return CATALOG[hid]
    except KeyError:
        raise KeyError(f"unknown hint id {hid!r} (engine/hints.py CATALOG)") from None


def fingerprint(hid: str, cond: Any) -> str:
    """A short fingerprint of a hint's condition (the text when nothing better is given)."""
    return hashlib.sha1(f"{hid}\x1f{cond}".encode("utf-8")).hexdigest()[:12]


def make(hid: str, text: Any, detail: Any = None, *, smart: bool = True, cls: Optional[int] = None,
         prio: Optional[int] = None, fp: Optional[str] = None, **extra) -> dict:
    """The hint object of the catalog row `hid`. `cls` / `prio` override the row for one case (the
    row's `alt` class, a safety line inside a group); `fp` = the condition's fingerprint (else the
    text's); `extra` keys travel along (a week note's level / src / week_start)."""
    e = entry(hid)
    c = e.cls if cls is None else int(cls)
    if c not in (1, 2, 3):
        raise ValueError(f"hint class must be 1, 2 or 3, not {cls!r}")
    p = e.prio if prio is None else int(prio)
    if p not in PRIOS:
        raise ValueError(f"hint priority must be 1-6, not {prio!r}")
    out = dict(extra)
    out.update({"id": hid, "cls": c, "prio": p, "smart": bool(smart),
                "text": "" if text is None else str(text), "detail": None if detail is None else str(detail),
                # a class-1 case of a cls-2 row is shown in every level (§4.4 step 1)
                "show": "●●●" if c == 1 and e.cls != 1 else e.show,
                "ack": e.ack if c == 1 else True,
                "fp": fp or fingerprint(hid, text)})
    return out


def flag(hid: str, *, smart: bool = True, cls: Optional[int] = None) -> dict:
    """The display part of a hint for something that is not a sentence in a list — a chip, a badge,
    a shaded stretch (SP-487): {id, cls, prio, show, smart}. The page asks static/hints.js
    `Hints.where` whether it is shown, goes into 「詳細」 or is hidden (§4.4 steps 1–4, no cap)."""
    e = entry(hid)
    c = e.cls if cls is None else int(cls)
    if c not in (1, 2, 3):
        raise ValueError(f"hint class must be 1, 2 or 3, not {cls!r}")
    return {"id": hid, "cls": c, "prio": e.prio, "smart": bool(smart),
            "show": "●●●" if c == 1 and e.cls != 1 else e.show}


def text(h: Any) -> str:
    """A hint's text: the object's `text`, or the old plain string (saved results, shares)."""
    if isinstance(h, dict):
        return str(h.get("text") or "")
    return "" if h is None else str(h)


def warning(w: Any, default: str = "RC") -> dict:
    """One calculator warning → the hint object. An object stays (missing fields filled), a
    Tagged string keeps its id, a plain string (an old saved result, a line not tagged yet) gets
    `default` — RC, shown as before."""
    if isinstance(w, dict):
        hid = w.get("id") if w.get("id") in CATALOG else default
        keep = {k: v for k, v in w.items() if k not in ("id", "text", "detail", "smart", "cls", "prio", "fp",
                                                         "show", "ack")}
        return make(hid, w.get("text"), w.get("detail"), smart=w.get("smart", True) is not False,
                    cls=w.get("cls"), prio=w.get("prio"), fp=w.get("fp"), **keep)
    if isinstance(w, Tagged):
        return make(w.hint_id, str(w), cls=w.hint_cls, smart=getattr(w, "hint_smart", True))
    return make(default, w)


def warnings(ws: Optional[Iterable], default: str = "RC") -> list[dict]:
    """A list of warnings (strings, Tagged or objects) → hint objects, order kept, repeats dropped."""
    out, seen = [], set()
    for w in ws or ():
        h = warning(w, default)
        k = (h["id"], h["text"])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
    return out


# ---------------------------------------------------------------------------
# week notes (overview.week_plan / projection: {level: info | watch | bad, src, text})
# ---------------------------------------------------------------------------

# src → catalog row (§1.2 OV8a–k). A note without a src: watch / bad = the copied indicator line (OV8k,
# overview.week_plan's 「{指標}：{結論}」), info = a 課表偏好 line (OV8g)
NOTE_SRC: dict[str, str] = {
    "injury_light": "OV8a", "injury_condition": "OV8a", "illness": "OV8a",
    "injury": "OV8a′", "reentry": "OV8a′",
    "race": "OV8b",                  # watch: the B-race warnings (post_race.b_hints); info → OV8d below
    "carb_load": "OV8c",
    "z3": "OV8d", "quality_share": "OV8d", "specific": "OV8d", "b2b": "OV8d", "base_climb": "OV8d",
    "steep": "OV8d", "climb": "OV8d", "technical": "OV8d", "strength": "OV8d", "volume": "OV8d",
    "season": "OV8d", "user": "OV8d",
    "intensity": "OV8e",
    "cold_start": "OV8f",
    "prefs": "OV8g", "blackout": "OV8g",
    "transition": "OV8h", "phase": "OV8h", "recovery": "OV8h", "taper": "OV8h",
    "easy_tss": "OV8i", "hr_zones": "OV8i",
    "long_cap": "OV8j",
    "heat": "OV5",
    "overhard": "SC4",               # 「輕鬆跑連續偏強」 (R3, SP-484: api/plan_sessions._streak_note)
    # SP-483: the notes that had no src — 「剩下每天都排了」 (OV8g's class-3 line), a test day that did not
    # fit (課表偏好's test days), and the copied indicator line (OV8k: the 待辦 OV2 says it, not shown)
    "full_week": "OV8g", "test_day": "OV8g", "indicator": "OV8k",
}
CLS3_SRC = ("full_week",)            # the class-3 case of its row (OV8g alt=3)


def note_id(note: dict) -> str:
    src, level = note.get("src"), note.get("level")
    if src == "race" and level not in ("watch", "bad"):
        return "OV8d"                # B / C race week shaping: explains the plan
    if src in NOTE_SRC:
        return NOTE_SRC[src]
    if src is None:
        return "OV8k" if level in ("watch", "bad") else "OV8g"
    return "OV8"


def note(n: dict) -> dict:
    """A week note with its hint fields (a new dict; level / src / text and the rest kept). The class
    follows the row; inside the catch-all OV8 a 'bad' note is class 1 (§3.1: 不看會做錯事) and the
    level decides the priority. An already tagged note keeps its id."""
    if not isinstance(n, dict):
        return make("OV8", n)
    hid = n.get("id") if n.get("id") in CATALOG else note_id(n)
    level = n.get("level")
    cls = n.get("cls")
    prio = n.get("prio")
    if hid == "OV8" and cls is None:
        cls = 1 if level == "bad" else 2
        prio = prio or (4 if level == "bad" else 5 if level == "watch" else 6)
    if cls is None and n.get("src") in CLS3_SRC:
        cls = 3
    if hid == "OV5" and level != "watch":
        prio = prio or 6             # the heat block's explanation; watch = 「本週排熱適應」 (5)
    keep = {k: v for k, v in n.items() if k not in ("id", "text", "detail", "smart", "cls", "prio", "fp",
                                                    "show", "ack")}
    return make(hid, n.get("text"), n.get("detail"), smart=n.get("smart", True) is not False, cls=cls,
                prio=prio, fp=n.get("fp"), **keep)


def notes(ns: Optional[Iterable], ctx: Optional[dict] = None) -> list[dict]:
    """The week notes with their hint fields; `ctx` (week_smart's) = the overview's §5 conditions
    computed (SP-483), else every note's own `smart` (True when it has none)."""
    out = [note(n) for n in ns or ()]
    if ctx is not None:
        for h in out:
            h["smart"] = bool(h["smart"]) and week_smart(h, ctx)
    return out


# ---------------------------------------------------------------------------
# 總覽 (SP-483, §7 T2): the §5 conditions of the week notes — a ◐ note shows only when its condition
# holds (§4.4 step 3), else it goes into 「詳細」
# ---------------------------------------------------------------------------

HEAT_MONTHS = range(4, 11)          # R11: 4–10 月 the heat card / notes matter (推估: Taiwan's hot season)
PHASE_FIRST_LEAD_D = 2              # R7: a phase that began on the weekend before counts this week as its
                                    # first too (推估: two days are not a week to read the note in)
# R15: a test suggestion lives in the suggestion box only — this note's test is the SP-471 block's
TEST_NOTE_SRC = ("easy_power",)


def first_week(start: Any, monday: dt.date) -> bool:
    """R7: the week of `monday` is the first of something that began on `start` (ISO or date): the week
    holding that day, or the next one when it began on the weekend before (PHASE_FIRST_LEAD_D)."""
    if not start:
        return False
    try:
        d = start if isinstance(start, dt.date) else dt.date.fromisoformat(str(start)[:10])
    except ValueError:
        return False
    return monday - dt.timedelta(days=PHASE_FIRST_LEAD_D) <= d <= monday + dt.timedelta(days=6)


def week_context(today: dt.date, monday: dt.date, phase_start: Any = None, cold_week: Optional[int] = None,
                 hot_race: bool = False) -> dict:
    """The overview week's facts the §5 conditions read: R11 heat (a hot month or a hot A / B race
    ahead — heat_data.hot_race; SP-569: the week plan has no heat notes any more), R7 the current phase's first week and the cold start's week (1 = first)."""
    return {"heat": today.month in HEAT_MONTHS or bool(hot_race),
            "phase_first": first_week(phase_start, monday),
            "cold_first": cold_week is None or int(cold_week) <= 1}


def week_smart(h: dict, ctx: dict) -> bool:
    """The §5 condition of one week note (by its catalog row; a row without one: True):
      OV5   R11  熱適應: the hot season or a hot race ahead
      OV8f  R7   冷啟動: its first week only
      OV8h  R7   轉換期／回量期／減量期: the phase's first week only
      OV8g       偏好與排日: only the yellow ones (少了時數、改排別天); the info lines explain, and
                 「剩下每天都排了」 (cls 3) never (推估: the audit's §1 OV8g)
      OV8   R15  the easy-power note's test is in the suggestion box (SP-471's block): 「詳細」"""
    hid = h.get("id")
    if hid == "OV5":
        return bool(ctx.get("heat"))
    if hid == "OV8f":
        return bool(ctx.get("cold_first", True))
    if hid == "OV8h":
        return bool(ctx.get("phase_first"))
    if hid == "OV8g":
        return h.get("cls") != 3 and h.get("level") in ("watch", "bad")
    if hid == "OV8" and h.get("src") in TEST_NOTE_SRC:
        return False
    return True


# ---------------------------------------------------------------------------
# 提示多寡 (§4.2)
# ---------------------------------------------------------------------------

def validate_level(v: Any) -> None:
    if v is not None and v not in LEVELS:
        raise ValueError(f"ui.hint_level must be one of {LEVELS} or null (automatic)")


def validate_acks(v: Any) -> None:
    """[{id, fp, at}] — at most ACK_MAX, one per (id, fp)."""
    if not isinstance(v, list) or len(v) > ACK_MAX:
        raise ValueError(f"ui.hint_ack must be a list of at most {ACK_MAX} {{id, fp, at}}")
    seen = set()
    for a in v:
        ok = (isinstance(a, dict) and set(a) <= {"id", "fp", "at"}
              and isinstance(a.get("id"), str) and 0 < len(a["id"]) <= 40
              and isinstance(a.get("fp"), str) and 0 < len(a["fp"]) <= 64
              and (a.get("at") is None or isinstance(a["at"], str)))
        if not ok:
            raise ValueError("ui.hint_ack entries must be {id, fp, at}")
        if (a["id"], a["fp"]) in seen:
            raise ValueError("ui.hint_ack has the same (id, fp) twice")
        seen.add((a["id"], a["fp"]))


def new_runner(exp: Optional[dict]) -> bool:
    """§4.2 ①'s questionnaire half: never answered, can't run 30 minutes, < 3 runs a week or the
    longest < 60 minutes (a blank answer says nothing)."""
    if not isinstance(exp, dict) or not exp.get("at"):
        return True
    if exp.get("can_run_30") is False:
        return True
    n, lng = exp.get("runs_per_week"), exp.get("longest_min")
    return (n is not None and n < NEW_RUNS_PER_WEEK) or (lng is not None and lng < NEW_LONGEST_MIN)


def auto_level(has_history: Optional[bool], exp: Optional[dict]) -> tuple[str, str]:
    """(level, reason): full for a new runner (no history AND the questionnaire says new), standard
    for everyone else — the owner too. Never lean. `has_history` None (the data could not be read):
    standard."""
    if has_history is False and new_runner(exp):
        return "full", "new_runner"
    return "standard", "default"


def suggest_lean(has_history: Optional[bool], races: Iterable[dict], longest_min: Optional[float],
                 today: dt.date, acks: Iterable[dict] = ()) -> bool:
    """§4.2 ③: ask once whether 精簡 fits — history, a confirmed race in the last LEAN_RACE_DAYS days and a
    session ≥ LEAN_LONG_MIN in the last LEAN_LONG_WEEKS weeks; never again after 「不要」 (LEAN_ASK)."""
    if not has_history or any(a.get("id") == LEAN_ASK for a in acks or ()):
        return False
    lo = (today - dt.timedelta(days=LEAN_RACE_DAYS)).isoformat()
    recent = any(isinstance(r, dict) and r.get("confirmed", True) and lo <= str(r.get("date") or "") <= today.isoformat()
                 for r in races or ())
    return recent and (longest_min or 0) >= LEAN_LONG_MIN


def resolve(chosen: Optional[str], has_history: Optional[bool], exp: Optional[dict]) -> dict:
    """{level, chosen, auto, auto_level, reason}: the user's choice wins; None = automatic."""
    a, why = auto_level(has_history, exp)
    if chosen in LEVELS:
        return {"level": chosen, "chosen": chosen, "auto": False, "auto_level": a, "reason": "chosen"}
    return {"level": a, "chosen": None, "auto": True, "auto_level": a, "reason": why}


def with_ack(acks: Optional[list], hid: str, fp: str, now: Optional[dt.datetime] = None,
             undo: bool = False) -> list:
    """The 「知道了」 list with (hid, fp) added (moved to the end) or, `undo`, removed; the oldest go
    beyond ACK_MAX."""
    now = now or dt.datetime.now(dt.timezone.utc)
    keep = [a for a in acks or () if isinstance(a, dict) and (a.get("id"), a.get("fp")) != (hid, fp)]
    if not undo:
        keep.append({"id": hid, "fp": fp, "at": now.isoformat(timespec="seconds")})
    return keep[-ACK_MAX:]


def acked(h: dict, acks: Iterable[dict]) -> bool:
    """§4.4 step 4: this hint's id and condition were put away with 「知道了」."""
    return any(a.get("id") == h.get("id") and a.get("fp") == h.get("fp") for a in acks or ())
