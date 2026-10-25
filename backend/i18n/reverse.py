"""
A text `_()` wrote in the source language (zh-TW), shown in another language — for what was frozen as
text with no msgid kept beside it (SP-831: a race-calculator share's snapshot; the built-in examples are
Chinese files).

    from backend.i18n.reverse import from_source
    from_source("全程沒有水站：約帶 3950–5500 ml", "en")   # -> "No water stations on the course: carry about 3950–5500 ml"

The text is matched back against the catalog's msgids:

1. exact: the text is a msgid;
2. a msgid with fields read as a pattern: the fields' values are taken from the text (a field with a number
   format — `{g:.0f}`, `{d:.1%}` — read back as that number) and put into the translation. The msgid filled
   with those values has to give the text back exactly, and a value never starts or ends with a space (the
   msgids keep their spaces outside the fields), so a loose pattern can't take a wrong reading;
3. msgids written one after the other: the parts of a `_("；")` join, and a msgid that continues the one
   before it (「 ＋ 喝 {ml}」「，到下一個有水的站約帶 {ml}」「（每次約 {g:.0f} g 碳水）」).

A field whose value is itself Chinese is translated the same way (「一包能量膠或等量」), or, when it is no msgid,
kept as it is (an athlete's own station name). Of several readings the one leaving the fewest Chinese
characters wins. Nothing matched: None — the caller keeps the text. A msgid the catalog no longer has
(renamed since the text was frozen) can't be matched.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Optional

from backend import i18n

CJK = re.compile(r"[　-〿㐀-鿿豈-﫿！-｠]")     # with the full-width punctuation 「」：（）
FIELD = re.compile(r"\{(\w+)(?:![rsa])?(?::([^{}]*))?\}")
JOIN = "；"                                 # _("；"): the calculator's join of two actions in one cell
# a msgid starting with one of these continues the text before it (「 ＋ 喝 {ml}」「，到下一個…」「（每次約…）」)
CONT = (" ", "，", "；", "（", "、", "＋")
MAX_DEPTH = 12                              # nesting: fields in fields, a text cut in parts
MAX_CUTS = 24                               # split points tried per text
MAX_SPLIT_LEN = 400                         # a longer text is only read as one msgid (or its 「；」 parts)

_LOCK = threading.Lock()
_INDEX: dict[str, tuple[int, "_Index"]] = {}       # locale -> (id of the catalog it was built from, index)


class _Pat:
    __slots__ = ("msgid", "rx", "fields", "head", "lit")

    def __init__(self, msgid: str, rx: re.Pattern, fields: list, head: str, lit: int):
        self.msgid, self.rx, self.fields, self.head, self.lit = msgid, rx, fields, head, lit


def _compile(msgid: str) -> Optional[_Pat]:
    parts, fields, names, pos = [], [], {}, 0
    for m in FIELD.finditer(msgid):
        parts.append(re.escape(msgid[pos:m.start()]))
        name, spec = m.group(1), m.group(2) or ""
        if name in names:
            parts.append(f"(?P=f{names[name]})")
        else:
            names[name] = len(fields)
            fields.append((name, spec))
            parts.append(f"(?P<f{names[name]}>.+?)")
        pos = m.end()
    if not fields:
        return None
    parts.append(re.escape(msgid[pos:]))
    try:
        rx = re.compile("".join(parts), re.S)
    except re.error:
        return None
    head = msgid[:FIELD.search(msgid).start()]
    return _Pat(msgid, rx, fields, head, len(FIELD.sub("", msgid)))


def _has_text(entry: Any) -> bool:
    return (isinstance(entry, str) and bool(entry)) or (isinstance(entry, dict) and any(entry.values()))


class _Index:
    def __init__(self, cat: dict):
        self.cat = cat
        self.by_head: dict[str, list[_Pat]] = {}            # first character of the literal head -> patterns
        leads = {JOIN}
        for msgid, entry in cat.items():
            if not isinstance(msgid, str) or not CJK.search(msgid) or not _has_text(entry):
                continue
            if msgid.startswith(CONT) and len(msgid.strip()) > 1:
                p = FIELD.search(msgid)
                leads.add(msgid[:p.start()] if p else msgid)
            if "{" not in msgid or "{{" in msgid:
                continue
            pat = _compile(msgid)
            if pat is not None:
                self.by_head.setdefault(pat.head[:1], []).append(pat)
        for lst in self.by_head.values():
            lst.sort(key=lambda p: -p.lit)                  # the most literal text first: the most specific
        leads = sorted((x for x in leads if x), key=len, reverse=True)
        self.splits = re.compile("(?=(?:" + "|".join(re.escape(x) for x in leads) + "))")

    def candidates(self, text: str):
        for p in self.by_head.get(text[:1], ()):
            if text.startswith(p.head):
                yield p
        for p in self.by_head.get("", ()):
            yield p


def _index(loc: str) -> _Index:
    cat = i18n.catalog(loc)
    with _LOCK:
        hit = _INDEX.get(loc)
        if hit is not None and hit[0] == id(cat):
            return hit[1]
    idx = _Index(cat)
    with _LOCK:
        _INDEX[loc] = (id(cat), idx)
    return idx


_BAD = object()


def _number(raw: str, spec: str) -> Any:
    """A field's text read back as the value its format spec wrote; _BAD when it isn't one."""
    s = raw.strip().replace(",", "")
    kind = spec[-1:] if spec else ""
    try:
        if kind == "%":
            return float(s.rstrip("%").strip()) / 100.0
        if kind in ("f", "F", "e", "E", "g", "G", "n"):
            return float(s)
        if kind == "d":
            return int(s)
        if spec.endswith(",") or kind.isdigit():
            v = float(s)
            return int(v) if v.is_integer() else v
    except ValueError:
        return _BAD
    return raw


Read = Optional[tuple[int, str]]      # (how many Chinese characters were left as they are, the translation)


class _Ctx:
    """One from_source call: the locale, the index and what has been read so far."""
    __slots__ = ("loc", "idx", "memo", "join")

    def __init__(self, loc: str):
        self.loc, self.idx, self.memo = loc, _index(loc), {}
        self.join = i18n.translate(JOIN, loc)


HAN = re.compile(r"[㐀-鿿豈-﫿]")


def _left(raw: str) -> int:
    """The Chinese characters (not the punctuation) left as they are: a reading that only swapped
    「（）」 for "()" around words no msgid reads has read nothing."""
    return len(HAN.findall(raw))


def _match(p: _Pat, text: str, c: _Ctx, depth: int) -> Read:
    """`text` read by the pattern `p`; None when it doesn't read it."""
    m = p.rx.fullmatch(text)
    if not m:
        return None
    src, out, kept = {}, {}, 0
    for i, (name, spec) in enumerate(p.fields):
        raw = m.group(f"f{i}")
        if raw != raw.strip():                              # a field's value has no space at either end
            return None
        v = _number(raw, spec) if spec else raw
        if v is _BAD:
            return None
        src[name] = v
        if not spec and CJK.search(raw):
            r = _tr(raw, c, depth + 1)
            r = r if r is not None else (_left(raw), raw)
            kept, out[name] = kept + r[0], r[1]
        else:
            out[name] = v
    if i18n.translate(p.msgid, i18n.DEFAULT_LOCALE, **src) != text:      # it has to write the text back
        return None
    return kept, i18n.translate(p.msgid, c.loc, **out)


def _whole(text: str, c: _Ctx, depth: int) -> Read:
    """`text` as one msgid: exact, else the pattern that leaves the fewest Chinese characters."""
    if _has_text(c.idx.cat.get(text)):
        return 0, i18n.translate(text, c.loc)
    best = None
    for p in c.idx.candidates(text):
        r = _match(p, text, c, depth)
        if r is not None and (best is None or r[0] < best[0]):
            best = r
            if not r[0]:
                break
    return best


def _better(a: Read, b: Read) -> Read:
    return b if a is None or (b is not None and b[0] < a[0]) else a


def _tr(text: str, c: _Ctx, depth: int = 0) -> Read:
    if depth > MAX_DEPTH or not text:
        return None
    if text in c.memo:
        return c.memo[text]
    c.memo[text] = None                                 # (a text that comes back to itself reads as nothing)
    best = None
    if JOIN in text:
        # the parts of a 「；」 join, each read on its own (a part no msgid reads stays as it is) — tried first:
        # the calculator's cells join several actions; a msgid with a 「；」 of its own is read below
        parts = [_tr(x, c, depth + 1) if x else (0, "") for x in text.split(JOIN)]
        if any(r is not None for r in parts):
            parts = [r if r is not None else (_left(x), x) for r, x in zip(parts, text.split(JOIN))]
            best = (sum(r[0] for r in parts), c.join.join(r[1] for r in parts))
    if best is None or best[0]:
        best = _better(best, _whole(text, c, depth))
    if (best is None or best[0]) and len(text) <= MAX_SPLIT_LEN:
        # a msgid continuing the one before it: split where it starts, rightmost first
        cuts = sorted({m.start() for m in c.idx.splits.finditer(text) if m.start() > 0}, reverse=True)[:MAX_CUTS]
        for i in cuts:
            left, right = text[:i], text[i:]
            if right.startswith(JOIN):
                continue                                # the join's parts: above
            r = _whole(right, c, depth + 1)
            if r is None:
                continue
            lt = _tr(left, c, depth + 1)
            if lt is not None:
                best = _better(best, (lt[0] + r[0], lt[1] + r[1]))
                if not best[0]:
                    break
    c.memo[text] = best
    return best


_CACHE: dict = {}                                       # (locale, catalog id, text) -> the translation or None
_CACHE_MAX = 4096


def from_source(text: Any, loc: Optional[str] = None) -> Optional[str]:
    """`text` (written by _() in zh-TW) in `loc` (default: the request's); None when it can't be read back
    (no Chinese in it, the source language asked for, or no msgid matches)."""
    loc = loc or i18n.current_locale()
    if loc == i18n.DEFAULT_LOCALE or not isinstance(text, str) or not CJK.search(text):
        return None
    key = (loc, id(i18n.catalog(loc)), text)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    r = _tr(text, _Ctx(loc))
    out = None if r is None or r[0] >= _left(text) else r[1]       # nothing read: None
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out
