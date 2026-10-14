"""課表的提示精簡 (SP-484 = docs/research/ui-hint-audit.md §7 T3; §5 R3, R4, §5.2): one 「偏強」 (rule D's) —
plan_match's ◐ 偏強 merged away; a single 偏強 is a dot, 2 of the last 3 easy runs one week note (SC4);
太強 (SC5) shown in every level; 上坡改走 a 「≥ X% 走」 tag in 標準; one explanation line a card; the week
reminders through hints.js (cap 3, 「詳細」). The card and the reminders run under node with the real
sesschip.js / hints.js and the zh-TW catalogs (as test_schedule_cards_sp396.py). Synthetic data only."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engine import adapt as A
from backend.engine import compliance as C
from backend.engine import hints as H
from backend.engine import plan_match as PM

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "schedule.html"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
LEVELS = ("full", "standard", "lean")


def _block(start: str, end: str) -> str:
    m = re.search(rf"// ---- {re.escape(start)} ----.*?// ---- {re.escape(end)} ----", PAGE.read_text("utf-8"), re.S)
    assert m, start
    return m.group(0)


STUBS = r"""
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n0 = (v) => v == null ? "–" : String(Math.round(v));
const hm = (s) => { if (s == null) return "–"; const m = Math.round(s / 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
const md = (d) => d.slice(5).replace("-", "/");
const tt = (k, p) => I18N.t("schedule." + k, p);
const D = { today: "2026-10-10", coros: { provider_label: "COROS" }, plan_notes: [] };
const S = {};
const KINDS = { easy: "輕鬆跑", long: "LSD", quality: "強度課", test: "測試", hike: "越野跑", strength: "肌力", race: "比賽" };
const editable = (s) => s.state === "active" && s.day >= D.today;
const syLabel = (st) => st, syIcon = () => "";
var PlanMark = { lines: () => [], label: () => "", icon: () => "" };
const BOX = { innerHTML: "", hidden: true, dataset: {}, addEventListener() {} };
const $ = (id) => BOX;
"""


def _run(expr: str, level: str = "standard", **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1];
const cat = {};
for (const ns of ["common", "schedule"]) for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(`${dir}/i18n/zh-TW/${ns}.json`, "utf8")))) cat[`${ns}.${k}`] = v;
const ctx = { DATA: JSON.parse(process.argv[3]) };
ctx.window = ctx;
ctx.HINT_STATE = { level: process.argv[4], acks: [] };
ctx.I18N = { t: (k, p) => {
  let v = cat[k];
  if (v && typeof v === "object") v = p && p.n === 1 ? v.one : v.other;
  return String(v ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m));
} };
vm.createContext(ctx);
for (const f of ["dashicons.js", "sesschip.js", "hints.js"]) vm.runInContext(fs.readFileSync(`${dir}/${f}`, "utf8"), ctx);
vm.runInContext(process.argv[5] + process.argv[6] + process.argv[7] + "\nOUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), level, STUBS,
                        _block("session card (SP-396)", "end session card"),
                        _block("week reminders (SP-484)", "end week reminders")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _chip(s, level="standard", big=True):
    return _run(f"chipHtml(DATA.s, {str(big).lower()})", level=level, s=s)


def _pop(s, level="standard"):
    return _run("popModel(DATA.s)", level=level, s=s)


# ---- a done easy run, graded by the real backend (plan_match.compare + compliance), its rule D note ------

OVER_NOTE = "輕鬆跑偏強（平均功率 236 W > 80% CP（224 W））：只標示，課表不變"
HARD_NOTE = "輕鬆跑跑成強度課（閾值課）：已調整之後的強度課"


def _easy_done(z3_s=720, tss=36.0, minutes=45, moving_min=45, note=None, uid="e1", day="2026-10-07", idx=26,
               typ="quality"):
    """A planned 45′ easy run, done with `z3_s` seconds of Zone 3 (720 = plan_match's 偏強: 1.2× the bar),
    graded as api/plan_sessions._decorate does, with rule D's `note` and _view's `overhard`."""
    s = {"uid": uid, "kind": "easy", "title": "輕鬆跑", "day": day, "week_start": "2026-10-05", "state": "done",
         "minutes": minutes, "tss": 36.0, "tss_est": 36.0, "note": note,
         "done_by": {"index": idx, "start": f"{day}T07:00:00", "date": day, "category": "road", "category_label": "路跑",
                     "moving_s": moving_min * 60.0, "tss": tss, "hard_s": float(z3_s),
                     "session": {"type": typ, "type_label": "", "z3_s": z3_s, "t_vo2_eq_s": None}}}
    vs = PM.compare(s, 36.0)
    s.update(vs=vs, compliance=C.with_plan_check(C.session_compliance(s, 36.0), vs))
    od = A.note_kind(note)
    if od:
        s["overhard"] = od
    return s


# ---- 驗收 1: a card never has both ◐ 偏強 and ⚠ 偏強 ------------------------------------------------

@needs_node
def test_a_card_never_has_both_the_half_ring_too_hard_and_the_rule_d_mark():
    s = _easy_done(note=OVER_NOTE)
    assert s["vs"]["grade"] == "over" and not s["vs"]["short"]          # plan_match's 偏強 …
    assert C.status_of(s, s["compliance"], "2026-10-08") == "done"      # … is no longer ◐ 部分
    for lv in LEVELS:
        h = _chip(s, lv)
        assert "sc-r-partial" not in h and "sc-r-done" in h, lv           # the ring: done, not ◐
        assert "強度偏高" not in h and "部分" not in h, lv
        assert h.count('class="od od-dot"') == 1 and "od-hard" not in h, lv   # one 偏強 mark: rule D's dot
        pm = _pop(s, lv)
        assert pm["label"] == "完成" and pm["verdict"] == "" and pm["note"] == OVER_NOTE   # the reason in the hover
    # time off as well: ◐ 部分 for the time (its own reason), still not a 「偏強」 ◐
    late = _easy_done(note=OVER_NOTE, moving_min=70, tss=56.0)
    h = _chip(late)
    assert "sc-r-partial" in h and "偏強" not in re.sub(r'aria-label="[^"]*"', "", h).replace("od-dot", "")
    assert "強度偏高" not in h and _pop(late)["verdict"] == ""


# ---- 驗收 2: a single 偏強 is a dot; 2 of the last 3 → one week note ---------------------------------

@needs_node
def test_a_single_over_hard_run_is_only_a_dot():
    s = _easy_done(note=OVER_NOTE)
    for lv in ("standard", "lean"):
        h = _chip(s, lv)
        assert 'class="od od-dot" role="img" aria-label="輕鬆跑偏強"' in h
        assert OVER_NOTE not in h and "c-warn" not in h and "c-d" not in h, lv     # no line on the card
    assert '<span class="c-d c-warn">輕鬆跑偏強（平均功率 236 W &gt; 80% CP' in _chip(s, "full")   # 完整: its reason as the line
    assert 'class="od od-dot"' in _chip(s, "lean", big=False)                     # the month view too


def _run_row(day, idx, hard, kind="easy"):
    return {"uid": f"u{idx}", "kind": kind, "day": day, "state": "done", "tss": 36.0, "note": None,
            "done_by": {"index": idx, "date": day, "start": f"{day}T07:00:00", "tss": 36.0}}, \
        {"avg_power": 240.0 if hard else 180.0, "cp": 280.0, "tss": 36.0, "session_type": "easy"}


def _streak(rows, today):
    sessions, reviews = [], {}
    for day, hard, *kind in rows:
        s, r = _run_row(day, len(sessions) + 1, hard, *kind)
        sessions.append(s)
        reviews[s["done_by"]["index"]] = r
    return A.overhard_streak(sessions, reviews, today)


def test_two_of_the_last_three_easy_runs_make_a_streak():
    # across the week line (Mon 10/12): Thu, Sat of last week, Mon of this week
    st = _streak([("2026-10-08", True), ("2026-10-10", False), ("2026-10-12", True)], "2026-10-13")
    assert st == {"n": 2, "of": 3, "days": ["2026-10-08", "2026-10-12"]}
    assert _streak([("2026-10-08", True), ("2026-10-10", False), ("2026-10-12", False)], "2026-10-13") is None
    # only the last three count (an older 偏強 drops out) …
    assert _streak([("2026-10-01", True), ("2026-10-03", True), ("2026-10-05", False), ("2026-10-08", False),
                    ("2026-10-10", True)], "2026-10-11") is None
    # … but two within 7 days is enough (R3's 「或 7 天內 2 次」): the last 3 have none, the 7 days 2 of 5
    st = _streak([("2026-10-05", True), ("2026-10-06", True), ("2026-10-07", False), ("2026-10-08", False),
                  ("2026-10-09", False)], "2026-10-11")
    assert st == {"n": 2, "of": 5, "days": ["2026-10-05", "2026-10-06"]}
    # long runs, strength and runs older than STREAK_LOOKBACK days don't count; a future day neither
    assert _streak([("2026-10-08", True, "long"), ("2026-10-10", True), ("2026-10-12", False)], "2026-10-13") is None
    assert _streak([("2026-09-20", True), ("2026-09-22", True)], "2026-10-13") is None


def test_the_streak_reads_rule_d_too_hard_and_the_stored_note_without_a_review():
    s1, _r = _run_row("2026-10-08", 1, False)
    s2, _r = _run_row("2026-10-10", 2, False)
    reviews = {1: {"session_type": "quality", "stimulus": "z3", "tss": 50.0},     # 太強 (the classifier's hard class)
               2: {"avg_power": 180.0, "cp": 280.0, "tss": 36.0}}
    assert A.overhard_streak([s1, s2], reviews, "2026-10-11") is None
    s2["note"] = OVER_NOTE
    assert A.overhard_streak([s1, s2], {1: reviews[1]}, "2026-10-11")["n"] == 2    # no row → the stored note
    assert A.note_kind(OVER_NOTE) == "over" and A.note_kind(HARD_NOTE) == "too_hard"
    assert A.note_kind("Easy run too hard (x): marked only, the plan stays as it is") == "over"
    assert A.note_kind("Easy run turned into a hard session (threshold session): x") == "too_hard"
    assert A.note_kind("長跑延到週六") is None and A.note_kind(None) is None


def _inp(today, reviews):
    return {"today": today, "adapt": {"reviews": reviews}, "cur": {"week": {"start": "2030-01-07"}, "notes": []},
            "weeks": []}


def test_the_streak_is_one_week_note_on_the_schedule():
    from backend.api import plan_sessions as API
    rows = [("2030-01-03", True), ("2030-01-05", False), ("2030-01-08", True)]    # today is past the real clock
    sessions, reviews = [], {}
    for day, hard in rows:
        s, r = _run_row(day, len(sessions) + 1, hard)
        sessions.append(s)
        reviews[s["done_by"]["index"]] = r
    inp = _inp("2030-01-09", reviews)
    note = API._streak_note(sessions, inp)
    assert note["id"] == "SC4" and note["week_start"] == "2030-01-07" and note["src"] == "overhard"
    assert note["text"] == "輕鬆跑連續偏強：最近 3 堂有 2 堂偏強，下一次放慢"
    for start, end in (("2030-01-07", "2030-01-13"), ("2029-12-31", "2030-02-03")):    # a week, a month in view
        ns = API._plan_notes(inp, start, end, note)
        sc4 = [n for n in ns if n["id"] == "SC4"]
        assert len(sc4) == 1 and sc4[0]["cls"] == 2 and sc4[0]["prio"] == 5 and sc4[0]["show"] == "●◐◐"
        assert sc4[0]["fp"] == H.fingerprint("SC4", "2030-01-03,2030-01-08")
    assert [n for n in API._plan_notes(inp, "2030-01-14", "2030-01-20", note) if n["id"] == "SC4"] == []
    # one 偏強 only: no note
    reviews[3] = {**reviews[3], "avg_power": 180.0}
    assert API._streak_note(sessions, _inp("2030-01-09", reviews)) is None
    # the English text
    from backend.i18n import use_locale
    with use_locale("en"):
        reviews[3] = {**reviews[3], "avg_power": 240.0}
        assert API._streak_note(sessions, _inp("2030-01-09", reviews))["text"].startswith("Easy runs too hard again: 2 of the last 3")


@needs_node
def test_the_streak_note_shows_in_every_level_on_the_schedule():
    note = H.note({"week_start": "2026-10-05", "level": "watch", "src": "overhard", "id": "SC4",
                   "text": "輕鬆跑連續偏強：最近 3 堂有 2 堂偏強，下一次放慢", "fp": "abc"})
    for lv in LEVELS:
        out = _run("(() => { D.plan_notes = DATA.ns; renderNotes(); return { html: BOX.innerHTML, hidden: BOX.hidden }; })()",
                   level=lv, ns=[note])
        assert not out["hidden"] and out["html"].count("輕鬆跑連續偏強") == 1, lv
        assert "pn-detail" not in out["html"], lv                         # on the page, not in 「詳細」


# ---- 驗收 3: no 「課前要吃」 in 精簡; in 標準 not on a 強度課 / 輕鬆跑 ------------------------------------

@needs_node
def test_no_eat_before_line_in_lean_and_not_on_other_sessions_in_standard():
    """R4 (SP-305 Q4): 標準 only on a ≥ 2 h long run, a race, a test; 精簡 none. SP-286 (owner 2026-10-07) took
    the line out of every session, so there is none at all — this pins that 精簡 has none and 標準 has none on
    the sessions R4 leaves out (and nothing on the others: SP-286's removal stands)."""
    from backend.api import plan_sessions as API
    prov = SimpleNamespace(status_of=lambda *a: {"status": "not_pushed"}, row_view=lambda r: {})
    base = {"week_start": "2026-10-12", "day": "2026-10-15", "state": "active", "terrain": "trail", "detail": "暖身 15 分；主課 3×8 分"}
    sessions = [{**base, "uid": "q", "kind": "quality", "title": "閾值 3×8 分", "minutes": 60},
                {**base, "uid": "e", "kind": "easy", "title": "輕鬆越野跑", "minutes": 50},
                {**base, "uid": "l", "kind": "long", "title": "LSD（山路）", "minutes": 150},
                {**base, "uid": "r", "kind": "race", "title": "北插天山", "minutes": 240},
                {**base, "uid": "t", "kind": "test", "title": "CP 測試", "minutes": 50}]
    walk = {"vam": 600.0, "grade": 10.0, "personal": False, "n": 5}
    views = [API._view(s, {"thresholds": {}}, {}, "2026-10-12", prov, walk) for s in sessions]
    assert not [k for v in views for k in v if "meal" in k or "fuel" in k]
    for lv in LEVELS:
        for v in views:
            shown = _chip(v, lv) + json.dumps(_pop(v, lv), ensure_ascii=False)
            if lv == "lean" or v["kind"] in ("quality", "easy"):
                assert "課前" not in shown and "吃" not in shown, (lv, v["kind"])
            assert "課前要吃" not in shown                                 # SP-286: none anywhere


# ---- 驗收 4: 太強 (SC5) in every level -----------------------------------------------------------

@needs_node
def test_too_hard_adjusted_shows_in_every_level():
    s = _easy_done(z3_s=1000, tss=50.0, note=HARD_NOTE, typ="hard_long")   # 1.67× → plan_match: 換了課
    assert s["overhard"] == "too_hard"
    for lv in LEVELS:
        h = _chip(s, lv)
        assert 'class="od od-hard" role="img" aria-label="輕鬆跑跑成強度課">⚠</span>' in h, lv
        assert f'<span class="c-d c-warn">⚠ {HARD_NOTE}</span>' in h, lv           # its line, in place of the verdict
        assert "c-why" not in h and "od-dot" not in h, lv
        assert "⚠" in _chip(s, lv, big=False), lv                                 # the month view: the mark
        assert _pop(s, lv)["note"] == HARD_NOTE


# ---- one explanation line, 上坡改走 -----------------------------------------------------------------

WALK = "提示：照你輕鬆心率的爬升速度（約 600 m/h），坡度超過約 10% 用走的比較省（預設值）；規則仍是心率上限"


def _hill(**kw):
    return {"uid": "h1", "kind": "long", "title": "LSD（山路）", "day": "2026-10-17", "state": "active", "minutes": 150,
            "tss_est": 110, "target": "心率 134–150 bpm", "detail": "這次目標爬升約 800 m；全程心率壓在輕鬆跑上限以下",
            "walk_hint": WALK, "walk_grade": 10, "coros": {"status": "pushed"}, **kw}


@needs_node
def test_walk_hint_is_a_short_tag_in_standard_the_sentence_in_full_hover_only_in_lean():
    std = _chip(_hill(), "standard")
    assert '<span class="walk-tag">≥ 10% 走</span>' in std and WALK not in std
    assert "這次目標爬升約 800 m" in std                                  # the tag goes in front of the detail
    full = _chip(_hill(), "full")
    assert WALK in full and "walk-tag" not in full and "這次目標爬升" not in full
    lean = _chip(_hill(), "lean")
    assert "walk-tag" not in lean and WALK not in lean and "這次目標爬升" in lean
    for lv in LEVELS:
        assert _pop(_hill(), lv)["walk"] == WALK                          # the hover has the sentence


@needs_node
def test_a_card_has_at_most_one_explanation_line():
    cards = [_hill(), _easy_done(note=OVER_NOTE), _easy_done(z3_s=1000, tss=50.0, note=HARD_NOTE, typ="hard_long"),
             _easy_done(moving_min=70, tss=56.0, note=OVER_NOTE)]
    for lv in LEVELS:
        for s in cards:
            h = _chip(s, lv)
            assert len(re.findall(r'class="c-(?:d|x c-why)\b', h)) <= 1, (lv, s["uid"])
    css = PAGE.read_text("utf-8")
    assert re.search(r"\.cal\.week \.chip \.c-d \{[^}]*white-space: nowrap; overflow: hidden; text-overflow: ellipsis;", css)
    assert re.search(r"\.cal\.week \.chip \.c-why \{ white-space: nowrap; \}", css)
    assert ".cal.week .chip .c-d.c-warn, .ag-day .chip .c-d.c-warn { white-space: normal; }" in css   # 太強 wraps


# ---- the week reminders: cap 3, the rest folded, ▷ into 「詳細」 -------------------------------------

@needs_node
def test_week_reminders_follow_the_cap_and_put_the_rest_away():
    ns = [H.note({"week_start": "2026-10-05", "level": "info", "src": "prefs", "text": f"偏好 {i}"}) for i in range(4)]
    ns.append(H.note({"week_start": "2026-10-05", "level": "info", "src": "easy_power", "text": "輕鬆跑功率帶"}))
    draw = "(() => { window.HINT_STATE.acks = DATA.acks; D.plan_notes = DATA.ns; renderNotes(); return BOX.innerHTML; })()"
    acks = [{"id": ns[-1]["id"], "fp": ns[-1]["fp"]}]                   # 「知道了」 → 「詳細」
    out = _run(draw, level="standard", ns=ns, acks=acks)
    shown = out.split('<details class="hint-more">')[0]
    assert len(re.findall(r'class="pn info"', shown)) == 3 and "還有 1 則提醒" in out
    assert '<details class="pn-detail"><summary>詳細</summary>' in out and "輕鬆跑功率帶" in out.split("pn-detail")[1]
    assert "輕鬆跑功率帶" not in out.split("pn-detail")[0] and "data-open-prefs" in out
    lean = _run(draw, level="lean", ns=ns, acks=[])
    assert "偏好 0" not in lean and "data-open-prefs" not in lean     # OV8g is ✕ in 精簡


def test_the_view_carries_the_rule_d_mark_and_the_walk_grade():
    from backend.api import plan_sessions as API
    prov = SimpleNamespace(status_of=lambda *a: {"status": "not_pushed"}, row_view=lambda r: {})
    inp = {"thresholds": {}}
    done = {"uid": "d", "week_start": "2026-10-05", "day": "2026-10-07", "kind": "easy", "title": "輕鬆跑", "state": "done",
            "minutes": 45, "note": OVER_NOTE, "done_by": {"index": 1}}
    assert API._view(done, inp, {}, "2026-10-08", prov)["overhard"] == "over"
    assert API._view({**done, "note": HARD_NOTE}, inp, {}, "2026-10-08", prov)["overhard"] == "too_hard"
    assert "overhard" not in API._view({**done, "note": "別的備註"}, inp, {}, "2026-10-08", prov)
    hill = {"uid": "a", "week_start": "2026-10-05", "minutes": 50, "day": "2026-10-08", "kind": "easy", "title": "輕鬆越野跑",
            "terrain": "trail", "state": "active", "detail": "x"}
    v = API._view(hill, inp, {}, "2026-10-06", prov, {"vam": 600.0, "grade": 10.0, "personal": False, "n": 5})
    assert v["walk_grade"] == 10.0 and v["walk_hint"].startswith("提示：")
