"""總覽本週清單用同一套卡片 (SP-397; docs/research/schedule-chip-ui.md §8.4): overview.html's 本週
card block (sessHtml / extraHtml) run under node with the real sesschip.js — the same icon, left-bar
colour and ring as the 課表 page's card for the same session, no type-coloured tile / ✓ corner / ≠ ◐
prefix / faded strike-through, the % in the hover for every done session, no thresholds, and the
watch state worded with the provider (「COROS 未推送」, never the raw key). Synthetic sessions only;
skipped without node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.tests import test_schedule_cards_sp396 as SCHED

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "overview.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

STUBS = r"""
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n0 = (v) => v == null ? "–" : String(Math.round(v));
const hm = (s) => { if (s == null) return "–"; const m = Math.round(s / 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
const tO = (k, p) => I18N.t("overview." + k, p);
var SP = { coros: { provider_label: DATA.provider || "COROS" }, kinds: {} };
var PlanMark = { lines: (s) => s.mark ? ["↻ 自動調整：疲勞保護"] : [], label: (s) => s.mark ? "自動調整：疲勞保護" : "",
  icon: (s, badge) => s.mark ? `<span class="pmk pmk-auto${badge ? " pmk-badge" : ""}" aria-hidden="true">↻</span>` : "" };
"""


def _block() -> str:
    m = re.search(r"// ---- this week card \(SP-397\) ----.*?// ---- end this week card ----", PAGE.read_text(encoding="utf-8"), re.S)
    assert m, "this week card block not found"
    return m.group(0)


def _run(expr: str, **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1];
const cat = {};
for (const ns of ["common", "overview"]) for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(`${dir}/i18n/zh-TW/${ns}.json`, "utf8")))) cat[`${ns}.${k}`] = v;
const ctx = { DATA: JSON.parse(process.argv[3]) };
ctx.window = ctx;
ctx.I18N = { t: (k, p) => String(cat[k] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)) };
vm.createContext(ctx);
for (const f of ["dashicons.js", "sesschip.js"]) vm.runInContext(fs.readFileSync(`${dir}/${f}`, "utf8"), ctx);
vm.runInContext(process.argv[4] + process.argv[5] + "\nOUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), STUBS, _block()],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _card(s, provider=None):
    return _run('sessHtml(DATA.s, "2026-10-10")', s=s, provider=provider)


def _parts(h: str):
    """(type tile, ring svg, state classes) of a card."""
    tile = re.search(r'<span class="sc-ic".*?</svg>', h, re.S).group(0)
    ring = re.search(r'<svg class="sc-ring.*?</svg>', h, re.S).group(0)
    cls = set(re.search(r'class="([^"]+)"', h).group(1).split()) & {"done", "cp-ok", "cp-part", "cp-off", "miss", "fut", "prov"}
    return tile, ring, cls


def _tip(h: str) -> str:
    return re.search(r'data-tip="([^"]+)"', h).group(1)


CASES = {
    "done": SCHED._done("green"),
    "partial": SCHED._done(short=True),
    "off_plan": SCHED._done("yellow", off_plan=True),
    "missed": {**SCHED._done(), "state": "missed", "done_by": None, "vs": None, "compliance": {"level": "missed", "pct": 0}},
    "future": SCHED._future(),
    "provisional": SCHED._future(provisional=True),
    "heat": SCHED._future(title="熱適應輕鬆跑"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_same_icon_bar_and_ring_as_the_schedule_card(name):
    s = CASES[name]
    assert _parts(_card(s)) == _parts(SCHED._run("chipHtml(DATA.s, false)", s=s)), name


def test_no_old_marks():
    for name, s in CASES.items():
        h = _card(s)
        cls = re.search(r'class="([^"]+)"', h).group(1).split()
        assert {"sx", "sc"} <= set(cls) and not [c for c in cls if c.startswith(("ty-", "cp-g", "cp-y", "cp-r")) or c == "missed"], (name, cls)
        assert 'class="ok"' not in h and "offp" not in h and "≠" not in h and "◐" not in h and "✓" not in h, name
        assert "±20" not in h and "綠" not in h, name                        # no thresholds in the hover


def test_the_percent_is_in_the_hover_for_every_done_session():
    assert "完成 72%（TSS 74%" in _tip(_card(CASES["done"]))
    assert "部分 72% · 強度不足" in _tip(_card(CASES["partial"]))
    assert "TSS 74% · 時間 87% · 強度 72%" in _tip(_card(CASES["partial"]))
    assert "換了課 · 排強度課，實際騎車" in _tip(_card(CASES["off_plan"]))
    assert "跳過" in _tip(_card(CASES["missed"]))
    mn = re.search(r'<span class="mn num">(.*?)</span>', _card(CASES["partial"])).group(1)
    assert "%" not in mn                                                      # the pie says it on the card


def test_watch_words_with_the_provider_never_the_raw_key():
    assert "COROS 未推送" in _tip(_card(SCHED._future(coros={"status": "not_pushed"})))
    assert "not_pushed" not in _card(SCHED._future(coros={"status": "not_pushed"}))
    assert "Garmin 需更新" in _tip(_card(SCHED._future(coros={"status": "outdated"}), provider="Garmin"))
    assert "COROS 已推送" in _tip(_card(SCHED._future(coros={"status": "updated"})))


def test_activity_outside_the_plan_is_a_dotted_card_with_its_icon():
    h = _run("extraHtml(DATA.a)", a={"index": 3, "date": "2026-10-08", "category": "bike", "category_label": "騎車",
                                      "moving_s": 4800, "tss": 55})
    assert re.search(r'class="sx sc act"', h) and _run('DI("bike")') in h and 'class="ok"' not in h and "ty-" not in h


def test_page_and_catalog():
    html = PAGE.read_text(encoding="utf-8")
    assert "/api/v1/static/sesschip.js" in html
    for gone in (".ty-", ".sx .ok", ".sx.missed", ".mini", ".sx .offp", "week.short_aria"):
        assert gone not in html, gone
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "overview.json").read_text("utf-8"))
        assert "week.short_aria" not in cat, loc
        assert all(cat.get(f"week.sy.{k}") for k in ("pushed", "outdated", "failed", "not_pushed", "line")), loc
