"""
Texts the app generated in zh-TW and stored — a session's title, the automatic run's reason, an
activity's type word — shown in the request's language (SP-839).

The plan's sessions keep zh-TW text: the generator runs in zh-TW (api/plan_sessions._build_inputs:
the plan inputs are one cache for every language) and code reads a title (「N×M 分」, 「長爬坡反覆」,
「陡坡健走 X%」). Showing one in English is a lookup at read time, never a second stored copy:

  1. the text is one msgid as written → its catalog entry;
  2. it is one msgid filled in — `_("長爬坡反覆 {n}×{rep} 分（{g:.0f}% 坡）", …)` → that msgid's entry with
     the same values, a value that is a text itself shown the same way;
  3. a title put together from pieces (「輕鬆越野跑」+「＋坡道衝刺 8×10 秒」, 「LSD」+「（山路）」) → the
     pieces (WORDS, the counts and units of UNITS, the brackets).
Only a result with no Chinese left is used: anything else is the user's own text (a title they typed,
a note) and is shown as written. A race's / route's name inside a generated title (USER_PARAMS:
「賽事模擬｜{name}」) stays as the user wrote it.

to_source() is the way back for a title the page shows and sends again unchanged (the 課表 dialog
saves what is in its title field): the shown text → the stored zh-TW one.
"""
from __future__ import annotations

import re
import string
import threading
from collections import OrderedDict
from typing import Any, Optional

from backend.i18n import DEFAULT_LOCALE, Msg, N_, catalog, current_locale, plural_words, relocalize, translate

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
HAN = re.compile(r"[㐀-鿿]")
USER_PARAMS = frozenset({"name", "race", "route", "event", "lbl"})

# the pieces of a generated title that is not one msgid (plan_prefs, overview, quality_gate, steep_hill …)
WORDS = (
    N_("輕鬆跑"), N_("輕鬆越野跑"), N_("輕鬆健行"), N_("越野跑"), N_("路跑"), N_("登山健行"), N_("健行"),
    N_("LSD"), N_("長跑"), N_("長時間"), N_("山路"), N_("山路越野"), N_("平路"), N_("緩坡"), N_("上坡"), N_("下坡"),
    N_("有氧間歇"), N_("巡航"), N_("超閾值"), N_("VO2max 間歇"), N_("速度"), N_("間歇"), N_("連續"), N_("金字塔"),
    N_("節奏跑"), N_("上坡節奏跑"), N_("短強度"), N_("恢復週"), N_("減量期"), N_("強度課"),
    N_("長爬坡有氧"), N_("長爬坡反覆"), N_("陡坡健走"), N_("模擬負重"), N_("負重爬坡"), N_("跑下山"), N_("穩定地形"),
    N_("坡道衝刺"), N_("坡道"), N_("上坡衝刺"), N_("加速跑"), N_("快步跑"), N_("馬拉松配速"),
    N_("肌力"), N_("肌力維持"), N_("下肢單腳"), N_("核心"), N_("高踏階"), N_("離心下階"), N_("引體向上"),
    N_("平衡"), N_("腳踝"),
    N_("測試"), N_("CP 測試"), N_("AeT 飄移測試"), N_("全力"), N_("比賽"), N_("計時跑"), N_("用比賽當 CP 測試"),
    N_("賽事模擬"), N_("攻頂日模擬"), N_("技術地形"), N_("被動熱適應"), N_("課表待確認"), N_("中強度跑"),
    N_("比賽或計時跑"), N_("徐國峰"), N_("最短版"), N_("手動輸入"), N_("暖身"), N_("緩和"), N_("休"),
    # 插入範本's names and groups (workout_steps.templates: built-in rows, stored as a session's title)
    N_("標準"), N_("不休息"), N_("慢跑或走下坡恢復"), N_("約"), N_("每趟"), N_("不算進階"), N_("走下來"), N_("恢復跑"),
    N_("有氧耐力跑"), N_("強度"), N_("看心率飄移"), N_("有氧基礎"), N_("兩極化低強度長課"), N_("間隔慢跑"),
    N_("半馬功率節奏"), N_("近閾值"), N_("乳酸閾值節奏"), N_("專項間歇"), N_("浮動"), N_("最大有氧功率"), N_("強化"),
    N_("陡坡衝刺"), N_("內建"), N_("閾值心率測試"), N_("最大心率測試"), N_("最後一趟"), N_("心率飄移測試"), N_("配速"),
    N_("配速飄移"), N_("登山王"), N_("經典"), N_("耐力型"), N_("快走上坡"), N_("爬升"), N_("模擬背"), N_("體重"), N_("低"),
    N_("中高"), N_("附加"), N_("加速"), N_("有出處的課表"), N_("只換主課時插在中間"), N_("間歇庫"), N_("進階階梯"),
    N_("加速跑與短坡衝刺"), N_("這個 app 的 CP 測試"), N_("慢跑"), N_("走路或極慢跑"),
)
# generated titles written as f-strings at their source (msgids only here: the generator keeps writing
# zh-TW, so a session made during an English request is stored the same way)
PATTERNS = (
    N_("賽事模擬｜{name}"), N_("賽事模擬｜{name} 第 {i} 天"),     # specific_phase.sim_sessions: a race's name
    # the automatic run's reasons (plan_change_log; the 課表 card's ↻ marker)
    N_("自動調整：降一階到 {rung}（不算進階）"),                    # adapt
    N_("CP {old} → {new} W：功率目標依 % CP 重算"),                   # plan_auto.rescale_sessions
)
# templates read back although their own words are only punctuation (_parse wants two Chinese characters of
# a template's own) — SP-905 R1: an injury's rule as injuries.condition_rule writes it, 「右膝・膝前痛進行中：…」,
# quoted in a session's 說明 by overview.condition_apply (「{why}：改成平路輕鬆跑，時間不變」)
JOINED = frozenset({N_("{label}・{what}")})
# counts with a unit (matched where a piece is expected; the numbers are kept as written)
_NUM = r"\d+(?:\.\d+)?"
UNITS = (
    (re.compile(rf"({_NUM})\s*組\s*[×xX]\s*"), N_("{a} 組 × ")),
    (re.compile(rf"連續\s*({_NUM})\s*(?:分鐘|分)"), N_("連續 {a} 分")),
    (re.compile(rf"({_NUM})\s*[×xX]\s*(\d+(?::\d\d)?(?:\.\d+)?)\s*(?:分鐘|分)"), N_("{a}×{b} 分")),
    (re.compile(rf"({_NUM})\s*[×xX]\s*({_NUM})\s*秒"), N_("{a}×{b} 秒")),
    (re.compile(rf"({_NUM}(?:-{_NUM})*)\s*(?:分鐘|分)"), N_("{a} 分")),
    (re.compile(rf"({_NUM})\s*秒"), N_("{a} 秒")),
    (re.compile(rf"({_NUM})\s*小時"), N_("{a} 小時")),
    (re.compile(rf"({_NUM})\s*%\s*坡"), N_("{a}% 坡")),
    (re.compile(r"第\s*(\d+)\s*天"), N_("第 {a} 天")),
    (re.compile(r"第\s*(\d+)\s*趟"), N_("第 {a} 趟")),
    (re.compile(r"(\d+)\s*趟"), N_("{a} 趟")),
    (re.compile(r"(\d+)\s*區"), N_("{a} 區")),
    (re.compile(r"(\d+)\s*天"), N_("{a} 天")),
    (re.compile(r"(\d+)\s*週"), N_("{a} 週")),
)
# full-width punctuation → the target language's (not zh-TW: there the text is shown as stored)
PUNCT = {"（": " (", "）": ")", "＋": " + ", "／": " / ", "・": " · ", "｜": " | ", "、": ", ", "，": ", ",
         "：": ": ", "；": "; ", "～": "–", "　": " ", "【": " [", "】": "] ", "「": "“", "」": "”"}

_LOCK = threading.Lock()
_MEMO: "OrderedDict[tuple, str]" = OrderedDict()        # (loc, text, depth) -> shown
_BACK: "OrderedDict[tuple, str]" = OrderedDict()        # (loc, shown) -> text (to_source)
_MEMO_MAX = 4096
_TPL: dict = {}                                          # loc -> (catalog id, [template])
_WORDS: dict = {}                                        # loc -> (catalog id, [(zh, shown)] longest first)


def _remember(d: OrderedDict, k, v) -> None:
    with _LOCK:
        d[k] = v
        d.move_to_end(k)
        while len(d) > _MEMO_MAX:
            d.popitem(last=False)


# ---- 1. one text as written ------------------------------------------------------------------------

_PHR: dict = {}                                          # loc -> (catalog id, {zh: shown})


def _phrases(loc: str) -> dict:
    """Labels made of two table words with nothing between them, which no template can read back
    (「{side}{area}」: two fields side by side) — an injury's body part, 「右膝」 → "Knee (right)"
    (injuries.full_label; a custom area is the user's own label and isn't one of these)."""
    cat = catalog(loc)
    hit = _PHR.get(loc)
    if hit is not None and hit[0] == id(cat):
        return hit[1]
    from backend.engine import injuries as INJ
    areas = list(INJ.AREAS.values()) + [N_("未指定部位")]
    sides = [INJ.SIDES["left"], INJ.SIDES["right"], N_("雙側")]
    out = {}
    for a in areas:
        for s in sides:
            zh = translate("{side}{area}", DEFAULT_LOCALE, side=s, area=a)
            out[zh] = translate("{side}{area}", loc, side=translate(s, loc), area=translate(a, loc))
    _PHR[loc] = (id(cat), out)
    return out


def _exact(s: str, loc: str) -> Optional[str]:
    """`s` as one text with nothing to fill in: a catalog msgid, or a label of _phrases."""
    entry = catalog(loc).get(s)
    if isinstance(entry, str) and entry:
        return entry
    if isinstance(entry, dict) and entry.get("other"):
        return entry["other"]
    return _phrases(loc).get(s)


# ---- 2. one msgid filled in ------------------------------------------------------------------------

_NUM_SPEC = re.compile(r"[-+ ]?#?0?\d*,?(?:\.\d+)?[dfgen%]$")
_NUM_RX = r"[-+−]?\d[\d,]*(?:\.\d+)?%?"


# fields written with fmt.weekday (adapt.wd: 「週四」): one may stand right before another field
WEEKDAY_FIELDS = frozenset({"day", "was", "to"})


def _weekday_rx() -> str:
    from backend.i18n import fmt
    return "|".join(re.escape(w) for w in fmt.weekdays("short", DEFAULT_LOCALE))


class _Tpl:
    __slots__ = ("msgid", "key", "size", "_rx", "fields")

    def __init__(self, msgid: str, parts: list):
        self.msgid = msgid
        lits = [lit for lit, _f, _s in parts]
        self.key = max(lits, key=len)
        self.size = sum(len(HAN.findall(x)) for x in lits)
        self.fields = parts
        self._rx = None

    def rx(self) -> re.Pattern:
        if self._rx is None:
            out, seen = [], set()
            nxt = self.fields[1:] + [("x", None, None)]
            for (lit, name, spec), (lit2, name2, _s2) in zip(self.fields, nxt):
                out.append(re.escape(lit))
                if name is None:
                    continue
                if name in seen:
                    out.append(f"(?P={name})")
                    continue
                seen.add(name)
                if name2 is not None and not lit2:          # a weekday right before another field (_parse)
                    out.append(f"(?P<{name}>{_weekday_rx()})")
                    continue
                out.append(f"(?P<{name}>{_NUM_RX})" if spec and _NUM_SPEC.match(spec) else f"(?P<{name}>.+?)")
            self._rx = re.compile("".join(out), re.S)
        return self._rx


def _parse(msgid: str) -> Optional[list]:
    try:
        parts = [(lit, name, spec) for lit, name, spec, _conv in string.Formatter().parse(msgid)]
    except ValueError:
        return None
    names = [n for _l, n, _s in parts if n is not None]
    if not names or any(not re.fullmatch(r"[A-Za-z_]\w*", n) for n in names):
        return None
    # two fields side by side can't be told apart — unless the first is a weekday (WEEKDAY_FIELDS: one of the
    # seven, 「{day}{title}取消：…」, SP-909 R1); a template has to say enough on its own
    for (_l1, n1, _s1), (l2, n2, _s2) in zip(parts, parts[1:]):
        if n1 is not None and n2 is not None and not l2 and n1 not in WEEKDAY_FIELDS:
            return None
    # words of its own, not only brackets: 「{a}（{b}）」 would read every bracketed title
    if msgid not in JOINED and sum(len(HAN.findall(lit)) for lit, _n, _s in parts) < 2:
        return None
    return parts


def _templates(loc: str) -> list:
    cat = catalog(loc)
    hit = _TPL.get(loc)
    if hit is not None and hit[0] == id(cat):
        return hit[1]
    out = []
    for msgid, entry in cat.items():
        if "{" not in msgid or not entry:
            continue
        parts = _parse(msgid)
        if parts:
            out.append(_Tpl(msgid, parts))
    out.sort(key=lambda t: -t.size)
    _TPL[loc] = (id(cat), out)
    return out


_SPEC_OUT = re.compile(r"\{(\w+)(?:![rsa])?:[^{}]*\}")


def _fill(entry: Any, params: dict) -> Optional[str]:
    if isinstance(entry, dict):
        n = params.get("n", params.get("count"))
        entry = entry.get("one" if n is not None and str(n).strip() == "1" else "other") or entry.get("other")
    if not isinstance(entry, str) or not entry:
        return None
    text = _SPEC_OUT.sub(r"{\1}", plural_words(entry, params))
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return None


def _by_template(s: str, loc: str, depth: int, whole: bool = False) -> Optional[str]:
    """`whole`: `s` is a run of 「；」-joined parts read as one msgid (_by_segments) — a field may not hold
    a separator of its own (it would swallow the parts after it), unless the field is one msgid as
    written (SP-905 R1: an injury's rule 「…；…」 inside 「{label}・{what}」)."""
    cat = catalog(loc)
    for t in _templates(loc):
        if t.key not in s:
            continue
        m = t.rx().fullmatch(s)
        if not m:
            continue
        params, ok, kept = {}, True, False
        for k, v in m.groupdict().items():
            if whole and k not in USER_PARAMS and len(_segments(v)) > 1 and _exact(v, loc) is None:
                ok = False
                break
            if CJK.search(v):
                tv = _shown(v, loc, depth + 1)
                if CJK.search(tv) and "、" in v:
                    tv = _by_list(v, loc, depth + 1) or tv
                if CJK.search(tv):
                    # a user's name kept as written — in this field, or in a text this field was read as
                    # (SP-909 R1: 「{why}，移到 {md}」 whose why is 「在不排課日期內（{name}）」)
                    if k not in USER_PARAMS and not _KEPT.get((loc, v)):
                        ok = False
                        break
                    kept = True
                v = tv
            params[k] = v
        if not ok:
            continue
        out = _fill(cat.get(t.msgid), params)
        if out is not None:
            if kept:
                _remember(_KEPT, (loc, s), True)
            return out
    return None


_KEPT: "OrderedDict[tuple, bool]" = OrderedDict()      # (loc, text) read with a user's name left as written


def _by_list(v: str, loc: str, depth: int) -> Optional[str]:
    """A field that is a 「、」 list of texts (strength_plan's moves: 「高踏階 8 下／腳、離心下階 …」), each
    item shown on its own (SP-905); None unless every item comes out without Chinese."""
    items, buf, d = [], [], 0
    for c in v:
        d += c in _OPEN
        d -= c in _CLOSE and d > 0
        if c == "、" and d == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(c)
    items.append("".join(buf))
    out = []
    for it in items:
        t = it.strip()
        e = _shown(t, loc, depth) if t else ""
        if CJK.search(e):
            return None
        out.append(_lower(e) if out and e else e)
    return ", ".join(out)


# ---- 3. a title from pieces -------------------------------------------------------------------------

def _words(loc: str) -> list:
    cat = catalog(loc)
    hit = _WORDS.get(loc)
    if hit is not None and hit[0] == id(cat):
        return hit[1]
    out = sorted(((w, translate(w, loc)) for w in WORDS), key=lambda x: -len(x[0]))
    _WORDS[loc] = (id(cat), out)
    return out


def _lower(w: str) -> str:
    """「Road」 → 「road」 inside a title, but AeT / VO2max / CP and a name (Achilles) stay as they are."""
    if w.startswith(KEEP_CAP):
        return w
    return w[0].lower() + w[1:] if re.match(r"[A-Z][a-z]+\b(?![-\s][A-Z])", w) else w


# SP-905 R1: 「…; Achilles tendon (left) · …」; SP-909 R1: a weekday inside a list 「(Monday, Tuesday)」
KEEP_CAP = ("Achilles", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _ascii_run(s: str, i: int) -> int:
    """The end of the text that is not Chinese from i: a word like 「B2B」「Zone」「fartlek」 stays whole;
    a number after a space or a bracket starts a piece of its own (UNITS: 「8×10 秒」)."""
    j, n = i + 1, len(s)
    while j < n and not CJK.match(s[j]) and s[j] not in PUNCT:
        if s[j].isdigit() and not s[j - 1].isalnum():
            break
        j += 1
    if s[i].isdigit():
        j = i + 1
        while j < n and (s[j].isalnum() or s[j] in ".×xX′″-–%:") and not CJK.match(s[j]):
            j += 1
    return j


def _by_pieces(s: str, loc: str, cap: bool = True) -> Optional[str]:
    words = _words(loc)
    out, i, n = [], 0, len(s)
    # a piece after another piece, in brackets or after ＋／、 is part of a name already begun: lower case
    # (「Strength maintenance (high step-up + …)」); after a word of the title's own (「A1」「Pfitzinger」) not
    depth, joined = 0, False
    while i < n:
        c = s[i]
        if c in PUNCT:
            depth += c in "（【「"
            depth -= c in "）】」"
            joined = c in "＋／、，・（"
            out.append(PUNCT[c])
            i += 1
            continue
        hit = None
        for rx, msgid in UNITS:
            m = rx.match(s, i)
            if m:
                hit = (translate(msgid, loc, **dict(zip("ab", m.groups()))), m.end())
                break
        if hit is None:
            for zh, shown in words:
                if s.startswith(zh, i):
                    hit = (_lower(shown) if depth > 0 or joined else shown, i + len(zh))
                    break
        if hit is not None:
            joined = True
        else:
            if CJK.match(c):
                return None
            j = _ascii_run(s, i)
            hit = (s[i:j], j)
            if hit[0].strip():
                joined = False
        if out and out[-1] and hit[0] and not out[-1][-1].isspace() and not hit[0][0].isspace() \
                and out[-1][-1] not in "(“[–/" and hit[0][0] not in ")”],.;:%′":
            out.append(" ")
        out.append(hit[0])
        i = hit[1]
    text = re.sub(r"\s+", " ", "".join(out)).replace("( ", "(").replace(" )", ")").strip()
    m = re.search(r"[A-Za-z]", text)
    # a capital only for a whole text (SP-905: not inside another text's field — 「uphill 2×15 min」), and not
    # after a number (SP-906: 「45 min · 5×(2 min + 2 min)」, the editor's structure line)
    if m and cap and not re.search(r"\d", text[:m.start()]):
        return text[:m.start()] + text[m.start()].upper() + text[m.start() + 1:]
    return text


# ---- 4. sentences put together (SP-905) --------------------------------------------------------------

# a session's 說明 / 出處 is often several texts joined (「…；…」, 「…。…」: overview, specific_phase, warmup …)
SEG_JOIN = {"；": "; ", "。": ". ", "\n": "\n", "·": " · "}
_OPEN, _CLOSE = "（(「《【[", "）)」》】]"


def _segments(s: str) -> list:
    """`s` cut at SEG_JOIN's separators outside brackets: [text, sep, text, sep, …, text]."""
    out, buf, depth = [], [], 0
    for c in s:
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        if c in SEG_JOIN and depth == 0:
            out += ["".join(buf), c]
            buf = []
        else:
            buf.append(c)
    out.append("".join(buf))
    return out


def _by_segments(s: str, loc: str, depth: int) -> Optional[str]:
    """Every part between the separators shown on its own (one msgid, filled in, or pieces); None
    unless every part with Chinese comes out without it (a user's name, USER_PARAMS, may stay)."""
    parts = _segments(s)
    if len(parts) < 3:
        return None
    texts, seps = parts[0::2], parts[1::2]
    n = len(texts)

    def ok(src: str, got: str) -> bool:
        return not CJK.search(got) or (got != src and _user_kept(src, got))

    out = []
    i = 0
    while i < n:
        hit = None
        # a msgid of its own may hold 「；」 (strength_plan, aet_test …): the longest run of parts that is
        # one text first, then the part alone (which may be pieces or a msgid filled in)
        for j in range(n - 1, i, -1):
            run = "".join(texts[k] + seps[k] for k in range(i, j)) + texts[j]
            t = run.strip()
            if t and CJK.search(t):
                got = _one(t, loc, depth + 1)
                if got is not None and ok(t, got):
                    hit = (got, j)
                    break
        if hit is None:
            t = texts[i].strip()
            got = _shown(t, loc, depth + 1) if t else ""
            if t and not ok(t, got):
                return None
            hit = (got, i)
        got, j = hit
        if got:
            if i and seps[i - 1] in "；·" and CJK.match(texts[i].strip()[:1] or " "):
                got = _lower(got)                   # 「…; warm-up 15 min」 (AeT, VO2max, CP, a name stay)
            out.append(got)
        if j < n - 1:
            out.append(SEG_JOIN[seps[j]] if j < n - 2 or texts[-1].strip() else SEG_JOIN[seps[j]].rstrip())
        i = j + 1
    text = re.sub(r"[ \t]+", " ", "".join(out)).strip()
    if depth == 0 and text[:1].islower():
        text = text[0].upper() + text[1:]          # a whole 說明 starts with a capital (「Warm-up 15 min; …」)
    return text


def _one(s: str, loc: str, depth: int) -> Optional[str]:
    """`s` as one text: a catalog msgid, or one msgid filled in (no splitting at separators)."""
    hit = _exact(s, loc)
    if hit is not None:
        return hit
    if "{" in s or depth > 3:
        return None
    return _by_template(s, loc, depth, whole=True)


# ---- the entry points ------------------------------------------------------------------------------

def _shown(s: str, loc: str, depth: int = 0) -> str:
    if not s or not CJK.search(s):
        return s
    if depth > 3:                          # past the depth limit only a text as written (SP-905 R1: 「右膝」)
        return _exact(s, loc) or s
    key = (loc, s, depth)                  # depth: a whole text gets a capital, a part not; the depth limit
    hit = _MEMO.get(key)
    if hit is not None:
        return hit
    out = _exact(s, loc)
    if out is None and "{" not in s:
        # parts joined by 「；」「。」「·」 first (SP-905): a msgid's field must not swallow the parts after
        # it (「平路；全程心率壓在…以下」 is two texts, not 「{label}以下」)
        out = _by_segments(s, loc, depth)
        if out is None:
            out = _by_template(s, loc, depth)
    if out is None:
        out = _by_pieces(s, loc, cap=depth == 0)
    if out is None or (CJK.search(out) and depth == 0 and not _user_kept(s, out)):
        out = s
    _remember(_MEMO, key, out)
    if out != s and depth == 0:
        _remember(_BACK, (loc, out), s)
    return out


def _reloc(m: Msg, loc: str) -> str:
    """i18n.relocalize, whose plain-text parameters are shown the same way (a title inside a note)."""
    ps = {k: _reloc(v, loc) if isinstance(v, Msg) else _shown(v, loc, 1) if isinstance(v, str) else v
          for k, v in m.params.items()}
    return translate(m.msgid, loc, **ps)


def _user_kept(s: str, out: str) -> bool:
    """A result whose Chinese is only a user's name (USER_PARAMS) carried over from `s`."""
    return all(part in s for part in CJK.findall(out))


def shown(text: Any, loc: Optional[str] = None) -> Any:
    """A stored zh-TW text in `loc` (default: the request's), as described at the top; an M_() text
    re-rendered (i18n.relocalize); anything that isn't one of the app's texts as it is."""
    loc = loc or current_locale()
    if isinstance(text, Msg):
        if loc == DEFAULT_LOCALE:
            return relocalize(text, loc)
        return _shown(_reloc(text, loc), loc)
    if not isinstance(text, str) or loc == DEFAULT_LOCALE:
        return text
    return _shown(text, loc)


def to_source(text: Any, loc: Optional[str] = None) -> Any:
    """The way back: a title shown in `loc` (an English page sends it again unchanged) → the stored
    zh-TW text; a text that is no shown one (the user's own words) as it is."""
    loc = loc or current_locale()
    if not isinstance(text, str) or not text or loc == DEFAULT_LOCALE or CJK.search(text):
        return text
    hit = _BACK.get((loc, text))
    if hit is not None:
        return hit
    cat = catalog(loc)
    for msgid, entry in cat.items():           # a plain catalog text (no fields): its msgid
        if entry == text and "{" not in msgid:
            return msgid
    return text


# the fields of an API response that hold such texts, and the parts never to touch: the watch's copy
# (coros: what the watch shows), the user's settings and thresholds (texts that go back into a session).
# SP-905: a session's 目標 / 說明 / 出處 (target, detail, source) and a template's tip / src are shown too —
# the 課表 dialog sends them back as stored (SRC_KEYS: `<key>_src`, and to_source on the server)
TEXT_KEYS = frozenset({"title", "label", "type_label", "category_label", "reason", "swap_reason", "mode_label",
                       "phase_label", "text", "why", "off_why", "target", "detail", "source", "tip", "src"})
SKIP_KEYS = frozenset({"coros", "prefs", "thresholds", "steps", "targets", "note", "name"})
# the texts a page may send back as it shows them: a changed one keeps the stored one as `<key>_src`
SRC_KEYS = ("title", "target", "detail", "source")


def shown_tree(obj: Any, loc: Optional[str] = None, keys=TEXT_KEYS, skip=SKIP_KEYS) -> Any:
    """A copy of an API response with every M_() text re-rendered and the texts under `keys` shown in
    `loc`; a dict whose `title` (target, detail, source: SRC_KEYS) changed keeps the stored one as
    `title_src` (…: what the page's code reads a title's words from, and sends back). zh-TW: M_() texts
    only (they are already zh-TW)."""
    loc = loc or current_locale()

    def walk(x, k=None):
        if isinstance(x, Msg):
            return shown(x, loc)
        if isinstance(x, dict):
            out = {}
            for kk, v in x.items():
                if kk in skip:
                    out[kk] = v
                elif kk in keys and isinstance(v, str):
                    out[kk] = shown(v, loc)
                elif kk in keys and isinstance(v, list) and all(isinstance(i, str) for i in v):
                    out[kk] = [shown(i, loc) for i in v]
                else:
                    out[kk] = walk(v, kk)
            for sk in SRC_KEYS:
                t = x.get(sk)
                if sk in keys and isinstance(t, str) and out.get(sk) != t and f"{sk}_src" not in x:
                    out[f"{sk}_src"] = t
            return out
        if isinstance(x, list):
            return [walk(v, k) for v in x]
        return x
    return walk(obj)
