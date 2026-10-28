"""總覽提示精簡 (SP-483; docs/research/ui-hint-audit.md §7 T2, docs/spec/ui-hints.spec.md): the week notes' §5
conditions computed on the overview (engine/hints.week_smart: R7 first week, R11 heat, R15, OV8g), the B-race
warning (no 百岳; 「知道了」 puts it into 「詳細」 until its races change), and overview.html's SP-483 layout block
(OVH) and the 5 區 card's options run under node with the real hints.js / readiness.js (SP-678): one place for a test, the
low-intensity words, the phase focus and a B2B; the 精簡 page without OV8d / OV8f / OV8h / OV8i / OV14; the
whole 5 區 ladder in every level; rule D's ⚠ in the week card's hover. Synthetic data only; the node parts are
skipped without node. The page count on the demo athlete is the browser test
(e2e/test_overview_hints_sp483_e2e.py)."""
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import hints as H
from backend.engine import planning as P
from backend.engine import post_race as PR

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "overview.html"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
MON = dt.date(2026, 10, 12)


# ---- the §5 conditions of the week notes (engine/hints.py) -----------------------------------------

def _ctx(**kw):
    base = dict(today=dt.date(2026, 12, 9), monday=dt.date(2026, 12, 7))
    return H.week_context(**{**base, **kw})


def _smart(note, **kw):
    return H.notes([note], _ctx(**kw))[0]["smart"]


def test_r7_cold_start_and_phase_notes_only_in_their_first_week():
    cold = {"level": "info", "src": "cold_start", "text": "資料還在累積"}
    assert _smart(cold, cold_week=1) is True
    assert _smart(cold, cold_week=2) is False and _smart(cold, cold_week=4) is False
    for src in ("transition", "recovery", "phase", "taper"):
        n = {"level": "info", "src": src, "text": "每次跑步 ≤ 60 分"}
        assert _smart(n, phase_start="2026-12-07") is True, src                 # began this Monday
        assert _smart(n, phase_start="2026-12-10") is True, src                 # began this week
        assert _smart(n, phase_start="2026-12-05") is True, src                 # the Saturday before (推估)
        assert _smart(n, phase_start="2026-12-03") is False, src                # the Thursday before
        assert _smart(n, phase_start="2026-11-23") is False, src                # weeks ago
        assert _smart(n, phase_start=None) is False, src


def test_r11_heat_notes_in_the_hot_months_or_before_a_hot_race():
    n = {"level": "info", "src": "heat", "text": "熱適應課需要 ≥ 60 分鐘"}
    assert _smart(n, today=dt.date(2026, 7, 1), monday=dt.date(2026, 6, 29)) is True
    assert _smart(n, today=dt.date(2026, 10, 31), monday=dt.date(2026, 10, 26)) is True
    assert _smart(n) is False                                                   # December, no hot race
    assert _smart(n, hot_race=True) is True


def test_ov8g_only_the_yellow_ones_and_never_the_full_week_line():
    full = H.notes([{"level": "info", "src": "full_week", "text": "剩下的每一天都排了東西"}], _ctx())[0]
    assert (full["id"], full["cls"], full["smart"]) == ("OV8g", 3, False)       # SP-483: its own src, class 3
    assert _smart({"level": "watch", "src": "prefs", "text": "受限於你的偏好：本週少 1.5 小時"}) is True
    assert _smart({"level": "info", "src": "prefs", "text": "本週可練的日子只剩 3 天"}) is False
    assert _smart({"level": "info", "src": "test_day", "text": "這週先不測"}) is False
    assert H.note({"level": "info", "src": "test_day", "text": "x"})["id"] == "OV8g"


def test_r15_the_easy_power_note_and_the_copied_indicator_line():
    ep = H.notes([{"level": "watch", "src": "easy_power", "text": "建議做一次 CP 測試"}], _ctx())[0]
    assert ep["id"] == "OV8" and ep["smart"] is False                         # the SP-471 block has it
    k = H.note({"level": "watch", "src": "indicator", "text": "測試：排一次 AeT 測試"})
    assert (k["id"], k["show"]) == ("OV8k", "✕✕✕")
    # rows without a condition stay as they were
    assert _smart({"level": "watch", "src": "intensity", "text": "輕鬆跑心率偏高"}) is True
    assert _smart({"level": "info", "src": "z3", "text": "間歇預算"}) is True
    assert H.notes([{"level": "info", "src": "z3", "text": "x"}])[0]["smart"] is True   # no ctx: the old answer


def test_the_engine_gives_the_notes_their_src():
    src = (Path(__file__).resolve().parents[1] / "engine" / "overview.py").read_text("utf-8")
    assert '"src": "full_week", "text": _("剩下的每一天都排了東西' in src
    assert '"src": "indicator", "text": _("{title}：{verdict}", title=i.title, verdict=i.action)' in src   # SP-810
    assert src.count('"src": "test_day"') == 2
    assert not re.search(r'notes\.append\(\{"level": "info", "text"', src)       # every week note has a src


def test_ov10_is_the_whole_ladder_in_every_level():
    assert H.CATALOG["OV10"].show == "●●●"


# ---- the B-race warning: no 百岳; 「知道了」 (R7) ---------------------------------------------------

def _ev(id_, date_, pri="B", **kw):
    return P.Event(id=id_, name=id_, date=date_, priority=pri, **kw)


def test_b_race_warning_leaves_a_baiyue_out():
    """The owner's case: a 65 km 3-day 百岳 B race 13 days before a 21 km A race — longer and close — no warning
    (百岳 is hiking-level, low TSS); the same B race as a trail race does warn."""
    a = _ev("half", "2026-11-07", "A", kind="road", distance_km=21.1, est_hours=1.8)
    trip = _ev("百岳", "2026-10-23", kind="baiyue", days=3, distance_km=65, climbing_m=4500)
    assert PR.b_hints([a, trip], dt.date(2026, 10, 19)) == []
    trail = _ev("越野", "2026-10-25", kind="race", distance_km=65, climbing_m=4500, est_hours=12.0)
    assert PR.b_hints([a, trail], dt.date(2026, 10, 19))


def test_b_race_ctl_warning_has_a_fingerprint_of_its_races():
    a = _ev("a", "2027-01-30", "A", kind="race", distance_km=50, est_hours=8.0)
    b = _ev("b", "2026-10-03", kind="race", distance_km=15, est_hours=1.5)
    ctl = [(MON - dt.timedelta(days=d), 60.0 if d > 10 else 45.0) for d in range(40, -1, -1)]
    one = [n for n in PR.b_hints([a, b], MON, ctl) if "掉到" in n["text"]]
    assert one and one[0]["fp"]
    ctl2 = [(d, v - 2) for d, v in ctl]                                         # another week's numbers
    two = [n for n in PR.b_hints([a, b], MON, ctl2) if "掉到" in n["text"]]
    assert two and two[0]["fp"] == one[0]["fp"] and two[0]["text"] != one[0]["text"]


# ---- node: hints.js + overview.html's SP-483 block + readiness.js ------------------------------------

def _block() -> str:
    m = re.search(r"// ---- SP-483 hint layout.*?// ---- end SP-483 hint layout ----", PAGE.read_text("utf-8"), re.S)
    assert m, "SP-483 block not found"
    return m.group(0)


def _run(expr: str, locale: str = "zh-TW", **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1], loc = process.argv[4];
const cat = {};
for (const ns of ["common", "overview"]) for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(`${dir}/i18n/${loc}/${ns}.json`, "utf8")))) cat[`${ns}.${k}`] = v;
const ctx = { DATA: JSON.parse(process.argv[3]), console };
ctx.window = ctx;
ctx.document = { getElementById: () => ({}), createElement: () => ({}), head: { appendChild() {} }, addEventListener() {} };
ctx.I18N = { t: (k, p) => { let v = cat[k]; if (v && typeof v === "object") v = p && p.n === 1 ? v.one : v.other;
  return String(v ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)); } };
vm.createContext(ctx);
for (const f of ["hints.js", "readiness.js"]) vm.runInContext(fs.readFileSync(`${dir}/${f}`, "utf8"), ctx);
vm.runInContext(`const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tO = (k, p) => I18N.t("overview." + k, p);
const toBox = (txt) => '<button type="button" class="to-box" data-open-sugg>' + esc(txt) + ' →</button>';
` + process.argv[5] + "\nOUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), locale, _block()],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _ind(id_, level, **kw):
    return {"id": id_, "level": level, "title": id_, "text": "x", **kw}


ACTIONS = [{"title": "肌力：幾乎沒有肌力訓練", "detail": "每週 2 次", "because": ["strength"]},
           {"title": "強度分配：低強度不足", "detail": "放慢", "because": ["intensity"]},
           {"title": "測試：建議：做最大心率測試", "detail": "排一次", "because": ["testing"]},
           {"title": "基礎期重點", "detail": "每週一次 60–90 分鐘輕鬆長跑", "because": ["phase"]}]


@needs_node
def test_one_place_for_a_test_the_low_intensity_words_and_the_phase_focus():
    """R15 (the box has the test), R16 (OV8e has the low-intensity words), OV3 (期別重點 in 「詳細」)."""
    out = {lv: _run("[OVH.todos(DATA.a, DATA.lv, {ov8e: false}), OVH.todos(DATA.a, DATA.lv, {ov8e: true})]",
                    a=ACTIONS, lv=lv) for lv in ("full", "standard", "lean")}
    titles = lambda xs: [x["title"] for x in xs]
    for lv, (no8e, with8e) in out.items():
        assert not any(t.startswith("測試") for t in titles(no8e["shown"] + no8e["detail"])), lv   # R15
        assert "強度分配：低強度不足" in titles(no8e["shown"]), lv                                 # only place
        assert "強度分配：低強度不足" not in titles(with8e["shown"] + with8e["detail"]), lv         # R16
    assert "基礎期重點" in titles(out["full"][0]["shown"])
    assert titles(out["standard"][0]["detail"]) == ["基礎期重點"] and "基礎期重點" not in titles(out["standard"][0]["shown"])
    assert "基礎期重點" not in titles(out["lean"][0]["shown"] + out["lean"][0]["detail"])
    assert _run("OVH.hasNote(DATA.p, 'OV8e')", p={"notes": [H.note({"level": "watch", "src": "intensity", "text": "t"})]})
    assert not _run("OVH.hasNote(DATA.p, 'OV8e')", p={"notes": [H.note({"level": "info", "src": "z3", "text": "t"})]})


@needs_node
def test_ov1_cards_open_by_level_and_not_twice_with_the_todo():
    inds = [_ind("strength", "bad"), _ind("long", "watch"), _ind("fitness", "watch"), _ind("form", "good"),
            _ind("heat", "info"), _ind("drift", "na"),
            _ind("testing", "watch", extra={"thr_check": {"hrmax": {"confidence": "low", "signals": [{"level": "strong"}]}}})]
    run = lambda lv, todo: _run("(() => { const r = OVH.cards(DATA.i, DATA.lv, {todo: DATA.t}); "
                                "return {open: r.open.map((x) => x.id), fold: OVH.foldLine(r.folded, {todo: DATA.t})}; })()",
                                i=inds, lv=lv, t=todo)
    assert run("full", ["strength"])["open"] == [i["id"] for i in inds]
    assert run("standard", []) == {"open": ["strength", "long", "fitness", "testing"], "fold": "其他 3 項正常"}
    assert run("lean", []) == {"open": ["strength", "testing"], "fold": "其他 5 項沒有要處理的"}
    # a card whose 待辦 row is on the page folds (said once, in 待辦); R5 可信度低 opens in every level
    assert run("standard", ["strength", "long"]) == {"open": ["fitness", "testing"], "fold": "其他 5 項（2 項見待辦）"}
    no_low = [i for i in inds if i["id"] != "testing"] + [_ind("testing", "good", extra={"thr_check": {"hrmax": {"confidence": "low", "signals": [{"level": "hint"}]}}})]
    assert "testing" not in _run("OVH.cards(DATA.i, 'lean', {}).open.map((x) => x.id)", i=no_low)
    assert _run("OVH.foldLine([{id: 'a', level: 'good'}], {})", locale="en") == "Other indicators fine: 1"


@needs_node
def test_lean_hides_ov8d_ov8f_ov8h_ov8i_and_ov14():
    notes = [H.note({"level": "info", "src": s, "text": s}) for s in
             ("z3", "cold_start", "transition", "easy_tss", "hr_zones", "taper", "specific")]
    assert {n["id"] for n in notes} == {"OV8d", "OV8f", "OV8h", "OV8i"}
    lean = _run("(() => { const f = Hints.filter(DATA.n, {level: 'lean'}); return {shown: f.shown.length, "
                "more: f.more.length, detail: f.detail.length, must: f.must.length, hidden: f.hidden.length}; })()", n=notes)
    assert lean == {"shown": 0, "more": 0, "detail": 0, "must": 0, "hidden": len(notes)}
    assert _run("[OVH.splitTarget('full'), OVH.splitTarget('standard'), OVH.splitTarget('lean')]") == ["inline", "tip", "none"]
    assert _run("[OVH.pmcMarks('standard'), OVH.pmcMarks('lean')]") == ["chart", "tip"]
    # the page: the lean ? leaves the target out; the 標準 ? says it
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "overview.json").read_text("utf-8"))
        assert "75%" in cat["split.tip"] and "75%" not in cat["split.tip_lean"] and "80%" not in cat["split.tip_lean"]
    src = PAGE.read_text("utf-8")
    assert 'splitHead(OVH.splitTarget(OVH.level(HST)))' in src


def _flow():
    """SP-678: the 強度課表解鎖條件 data (phase_checks.unlock): zone 3 open, zone 5 missing its AeT test, the easy
    share under the floor (its card and line are what R16 drops)."""
    import datetime as dt
    from backend.engine import phase_checks as PC
    gate = {"z3": {"open": True, "path_label": "x", "since": "2026-10-01", "weeks": 4,
                   "tests": [{"key": "weeks", "ok": True}]}, "z5": {"aet_tested": None},
            "lthr": {"value": 165, "measured": True}, "rebase": None}
    card = {"state": "unconfirmed", "z5_gate": {"open": False, "aet_ok": False, "z3_ok": True},
            "z3": {"done": 2, "need": 2}, "base": {"tests": []}}
    return PC.unlock(card, gate, dt.date(2026, 10, 16), low_share=0.70, last_run=dt.date(2026, 10, 15))


@needs_node
def test_the_whole_5_zone_ladder_in_every_level_with_r15_and_r16():
    u = _flow()
    plain = _run("Readiness.unlockHtml(DATA.u, {sel: 5, schedule: '/s'})", u=u)       # the 基礎期 card: as before
    assert "輕鬆跑壓在 AeT 以下" in plain and "排一次：平路 AeT 附近跑 60 分鐘" in plain and plain.count("安排課表") == 2
    page = _run("(() => { const o = OVH.z5Opt(); return Readiness.unlockHtml(DATA.u, {sel: 5, schedule: '/s', skip: o.skip, "
                "testNote: () => toBox('建議做的測試在建議框'), testAction: () => toBox('在建議框排入')}); })()", u=u)
    assert page.count('class="ru-tile ') == 2                                     # both tiles
    assert "輕鬆跑壓在 AeT 以下" not in page                                       # R16: OV8e says it
    assert "排一次：平路" not in page and "建議做的測試在建議框" in page              # R15: a link to the box
    assert page.count("安排課表") == 1 and page.count("在建議框排入") == 1          # the zone's own link stays
    # the 總覽's options do not depend on the level: the same card in 完整／標準／精簡
    assert _run("JSON.stringify(OVH.z5Opt())") == json.dumps({"skip": ["intensity"], "testNote": True, "testAction": True},
                                                             separators=(",", ":"))
    assert "OVH.z5Opt()" in PAGE.read_text("utf-8") and "level" not in re.search(r"z5Opt: \(\) => .*", _block()).group(0)


@needs_node
def test_a_suggested_b2b_is_the_boxs():
    assert _run("OVH.b2bNow(DATA.w)", w={"suggest": True}) is False
    assert _run("OVH.b2bNow(DATA.w)", w={"due": True, "index": 1}) is True
    assert _run("OVH.b2bNow(DATA.w)", w={"suggest": True, "post": {"until": "2026-10-20"}}) is True
    src = PAGE.read_text("utf-8")
    assert 'else if (W.suggest && OVH.b2bNow(W)) now =' in src


@needs_node
def test_got_it_on_the_b_race_warning_keeps_it_out_of_the_reminders():
    a = _ev("a", "2026-11-07", "A", kind="road", distance_km=42.2, est_hours=4.5)
    b = _ev("b", "2026-10-17", distance_km=21, climbing_m=800, est_hours=3.0)
    notes = H.notes(PR.b_hints([a, b], MON), H.week_context(MON, MON))
    assert notes and all(n["id"] == "OV8b" for n in notes)
    acks = [{"id": "OV8b", "fp": n["fp"]} for n in notes]
    out = _run("(() => { const r = Hints.render(DATA.n, {level: 'standard', acks: DATA.a}); return r; })()", n=notes, a=acks)
    assert out["html"] == "" and all(n["text"] in out["detailHtml"] for n in notes)     # 「詳細」, with 再提醒我
    # next week, the same races: still put away; the B race moved: back in the box
    nxt = H.notes(PR.b_hints([a, b], MON + dt.timedelta(days=0)), H.week_context(MON, MON))
    assert _run("Hints.filter(DATA.n, {level: 'standard', acks: DATA.a}).must.length", n=nxt, a=acks) == 0
    moved = H.notes(PR.b_hints([a, _ev("b", "2026-10-18", distance_km=21, climbing_m=800, est_hours=3.0)], MON))
    assert _run("Hints.filter(DATA.n, {level: 'lean', acks: DATA.a}).must.length", n=moved, a=acks) == len(moved) > 0


def test_rule_d_warning_in_the_week_cards_hover():
    """OV9: the overview's card hover carries rule D's ⚠ as the 課表 page's does (sesspop.js)."""
    if NODE is None:
        pytest.skip("node not installed")
    from backend.tests import test_overview_cards_sp397 as C
    s = {**C.CASES["done"], "note": "輕鬆跑偏強（平均功率 > 80% CP）"}
    import html
    assert "⚠ 輕鬆跑偏強（平均功率 > 80% CP）" in html.unescape(C._tip(C._card(s)))
    assert "⚠" not in C._tip(C._card(C.CASES["done"]))
    assert "⚠" not in C._tip(C._card({**C.CASES["future"], "note": "你偏好的日子"}))   # a plan note, not rule D


def test_page_strings_in_both_languages():
    keys = ("hint.in_box", "hint.test_in_box", "hint.schedule_in_box", "hint.see_notes", "hint.detail",
            "hint.others_ok", "hint.others_ok_lean", "hint.others_todo", "split.tip_lean")
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "overview.json").read_text("utf-8"))
        assert all(cat.get(k) for k in keys), loc
    js = (STATIC / "suggestions.js").read_text("utf-8")
    assert "window.AppSuggestions = { refresh, load, open };" in js
