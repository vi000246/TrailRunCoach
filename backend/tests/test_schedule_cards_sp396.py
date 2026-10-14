"""課表頁的新卡片 (SP-396; docs/research/schedule-chip-ui.md §8.1, §8.3): schedule.html's session
card block (chipHtml / actHtml / the verdict) and the dialog's 完成盒 (vsHtml) run under node with the
real sesschip.js / dashicons.js and the zh-TW catalog — type icon, left bar + ring by status, no
type colour / ✓ / ◐ / ≠ / 熱 / 未完成 / line-through, the watch only when it needs attention, the
aria-label order of §7.1, and a done box with one verdict line, the three numbers and one ?.
Synthetic sessions only; skipped without node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "schedule.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _block(name: str) -> str:
    m = re.search(rf"// ---- {name} \(SP-396\) ----.*?// ---- end {name} ----", PAGE.read_text(encoding="utf-8"), re.S)
    assert m, f"{name} block not found"
    return m.group(0)


STUBS = r"""
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n0 = (v) => v == null ? "–" : String(Math.round(v));
const hm = (s) => { if (s == null) return "–"; const m = Math.round(s / 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
const tt = (k, p) => I18N.t("schedule." + k, p);
const D = { today: "2026-10-10", coros: { provider_label: "COROS" } };
const S = {};
const KINDS = { easy: "輕鬆跑", long: "LSD", quality: "強度課", test: "測試", hike: "越野跑", strength: "肌力", heat_passive: "被動熱適應", notice: "課表待確認", race: "比賽" };
const editable = (s) => s.state === "active" && s.day >= D.today;
const SYNC = { pushed: "ok", updated: "ok", outdated: "warn", failed: "bad", not_pushed: "none" };
const syLabel = (st) => (SYNC[st] ? tt("sy." + (st === "updated" ? "pushed" : st)) : "");
const syIcon = (st) => SYNC[st] ? `<span class="sy sy-${SYNC[st]}" role="img" aria-label="${esc(syLabel(st))}"></span>` : "";
var PlanMark = { lines: (s) => s.mark ? ["↻ 自動調整：疲勞保護", "TSB −32"] : [], label: (s) => s.mark ? "自動調整：疲勞保護" : "",
  icon: (s, badge) => s.mark ? `<span class="pmk pmk-auto${badge ? " pmk-badge" : ""}" aria-hidden="true">↻</span>` : "" };
const MATCH_TIP = "配對規則", OFF_TIP = () => tt("vs.levels_tip"), INT_LB = { hard: "強度", easy: "輕鬆" };
const VIEWER = (a) => "/viewer?workout=" + a.index;
const linkSelect = () => "";
"""


def _run(expr: str, **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1];
const cat = {};
for (const ns of ["common", "schedule"]) for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(`${dir}/i18n/zh-TW/${ns}.json`, "utf8")))) cat[`${ns}.${k}`] = v;
const ctx = { DATA: JSON.parse(process.argv[3]) };
ctx.window = ctx;
ctx.I18N = { t: (k, p) => String(cat[k] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)) };
vm.createContext(ctx);
for (const f of ["dashicons.js", "sesschip.js"]) vm.runInContext(fs.readFileSync(`${dir}/${f}`, "utf8"), ctx);
vm.runInContext(process.argv[4] + process.argv[5] + process.argv[6] + "\nOUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), STUBS, _block("session card"),
                        _block("done box")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _done(level="yellow", **cp):
    return {"uid": "d1", "kind": "quality", "title": "VO2max 間歇 5×3′", "day": "2026-10-07", "state": "done", "minutes": 60,
            "tss_est": 70, "quality_family": {"id": "vo2max", "label": "VO2max 間歇", "text": "VO2max 間歇"},
            "done_by": {"tss": 52, "moving_s": 3120, "category_label": "路跑", "index": 7, "session": {"type_label": "Z5 間歇"}},
            "vs": {"short": level == "yellow" and cp.get("short"), "short_text": "強度不足：只做到這堂課的 72%", "planned": "hard", "actual": "hard",
                   "off_plan": cp.get("off_plan"), "text": "換了課：排強度課，實際騎車" if cp.get("off_plan") else ""},
            "compliance": {"level": level, "pct": 72, "tss_pct": 74, "duration_pct": 87, "intensity_pct": 72,
                           "label": "強度不足" if cp.get("short") else "符合計畫", **cp}}


def _future(**kw):
    return {"uid": "f1", "kind": "easy", "title": "輕鬆跑", "day": "2026-10-12", "state": "active", "minutes": 45, "tss_est": 35,
            "coros": {"status": "pushed"}, **kw}


def _chip(s, big=False):
    return _run(f"chipHtml(DATA.s, {str(big).lower()})", s=s)


OLD = ("✓", "◐", "≠", "熱</span>", "heat-tag", "未完成", "line-through", "offp-i", "part-i", "--k", "k-easy", "k-quality", "fam-")


def test_partial_card_is_a_yellow_bar_with_a_pie_and_no_old_marks():
    h = _chip(_done(short=True))
    cls = re.search(r'class="([^"]+)"', h).group(1).split()
    assert {"chip", "sc", "done", "cp-part"} <= set(cls) and not [c for c in cls if c.startswith(("st-", "k-", "fam-", "cp-y", "cp-r"))]
    assert 'class="sc-ic"' in h and _run('DI("z5")') in h                  # 強度課 VO2max → lightning, no colour
    assert "sc-r-partial" in h and "stroke-dasharray" in h
    assert not [o for o in OLD if o in h], [o for o in OLD if o in h]
    # SP-470: no plain title text — the hover is sesspop.js's card, from popModel (same words)
    assert 'title=""' in h
    pm = _run("popModel(DATA.s)", s=_done(short=True))
    assert pm["label"] == "部分 72%" and pm["verdict"] == "強度不足" and pm["nums"] == "負荷 74% · 時間 87% · 強度 72%"
    shown = json.dumps(pm, ensure_ascii=False)
    assert "±20" not in shown and "綠" not in shown                         # no thresholds on the card


def test_done_off_plan_and_skipped():
    done = _chip(_done("green"))
    assert "cp-ok" in done and "sc-r-done" in done and 'd="m5 8.3 2 2L11 6"' in done
    off = _chip(_done("yellow", off_plan=True))
    assert "cp-off" in off and "sc-r-off_plan" in off and "換了課 · 排強度課，實際騎車" in off
    miss = _chip({**_done(), "state": "missed", "done_by": None, "vs": None, "compliance": {"level": "missed", "pct": 0}})
    cls = re.search(r'class="([^"]+)"', miss).group(1).split()
    assert "miss" in cls and "done" not in cls and "sc-r-missed" in miss and "跳過" in miss
    assert not [o for o in OLD if o in miss]


def test_big_card_says_why_in_the_status_words():
    h = _chip(_done(short=True), big=True)
    assert '<span class="c-x c-why">部分 72% · 強度不足</span>' in h
    assert "實際 路跑 · Z5 間歇" in h


def test_future_card_outline_ring_and_the_watch_only_when_it_needs_attention():
    for st, cls in (("pushed", None), ("updated", None), ("not_pushed", None), ("outdated", "sy-warn"), ("failed", "sy-bad")):
        h = _chip(_future(coros={"status": st}))
        assert "fut" in h and "sc-r-todo" in h and "done" not in re.search(r'class="([^"]+)"', h).group(1).split()
        assert (f'class="sy {cls}"' in h) if cls else ('class="sy ' not in h), st
    prov = _chip(_future(provisional=True))
    assert re.search(r'class="chip sc fut prov', prov) and 'stroke-dasharray="2 1.6"' in prov


def test_heat_and_the_mark_live_in_the_tile():
    h = _chip(_future(title="熱適應輕鬆跑", mark={"state": "auto"}))
    tile = re.search(r'<span class="sc-ic".*?</span></span>', h, re.S).group(0)
    assert _run('DI("heat")') in tile and "pmk-badge" in tile and "heat-tag" not in h
    assert 'class="c-r"><svg' in h                                           # the right column: ring first, no ↻


def test_aria_label_order():
    s = _future(provisional=True, mark={"state": "auto"}, coros={"status": "outdated"}, title="長 tempo")
    lbl = re.search(r'aria-label="([^"]+)"', _chip(s)).group(1)
    parts = ["輕鬆跑：長 tempo", "45 分鐘", "負荷 35", "暫定", "自動調整：疲勞保護", "需更新"]
    pos = [lbl.index(p) for p in parts]
    assert pos == sorted(pos), lbl
    done = re.search(r'aria-label="([^"]+)"', _chip(_done(short=True))).group(1)
    assert done.index("VO2max 間歇 5×3′") < done.index("60 分鐘") < done.index("負荷 70") < done.index("部分 72%")


def test_activity_card_dotted_with_its_icon():
    h = _run("actHtml(DATA.a)", a={"index": 3, "category": "bike", "category_label": "騎車", "moving_s": 4800, "tss": 55, "distance_km": 30})
    assert re.search(r'class="chip sc act"', h) and _run('DI("bike")') in h and "●" not in h and "--k" not in h


def test_done_box_one_verdict_line_three_numbers_one_question_mark():
    h = _run("vsHtml(DATA.s)", s=_done(short=True))
    head = re.search(r'<div class="vs-h">.*?</div>', h, re.S).group(0)
    assert "sc-r-partial" in head and "部分 72%" in head and "強度不足" in head
    assert h.count('class="qtip"') == 1
    assert '<div class="vs-nums">負荷 74% · 時間 87% · 強度 72%</div>' in h
    assert h.index('class="vs-h"') < h.index('class="vs-nums"') < h.index('<dl class="vs">')
    shown = re.sub(r'data-tip="[^"]*"', "", h)                             # the ? text is the legend's (SP-399)
    assert "已完成" not in shown and "≠" not in shown and "◐" not in shown and "完成度 7" not in shown   # no old 「完成度 72%（…）」
    off = _run("vsHtml(DATA.s)", s=_done("yellow", off_plan=True))
    assert "sc-r-off_plan" in off and "換了課" in off and off.count('class="qtip"') == 1


def test_page_wiring():
    html = PAGE.read_text(encoding="utf-8")
    assert "/api/v1/static/sesschip.js" in html and "/api/v1/static/dashicons.js" in html
    assert ".wk.prov .cell" not in html                                      # no 135° provisional hatch
    assert ".chip.st-done" not in html and ".chip.st-missed" not in html and "#b36b00" not in html
    assert 'id="sd-watch"' in html
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "schedule.json").read_text("utf-8"))
        assert cat.get("sy.line") and cat.get("vs.time_pct"), loc


def test_a_narrow_month_grid_keeps_tile_bar_and_ring_only():
    """Review shot SP-396 #7 (2026-10-13): side by side the ring was cut off in a 45 px day — stacked now."""
    html = PAGE.read_text(encoding="utf-8")
    phone = html[html.index("@media (max-width: 699px) {"):]
    phone = phone[:phone.index("\n}\n")]
    assert re.search(r"\.cal:not\(\.week\) \.chip \{ grid-template-columns: minmax\(0, 1fr\); justify-items: center;", phone)
    assert ".cal:not(.week) .chip .c-t, .cal:not(.week) .chip .c-m, .cal:not(.week) .chip.act .c-r { display: none; }" in phone
