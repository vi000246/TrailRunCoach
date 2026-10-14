"""拆掉課表與總覽的舊樣式、圖例收成一顆 ? (SP-399; docs/research/schedule-chip-ui.md §7, §10 ⑤): no
type colours (.k-* / --k / .ty-* / --s1…s7 / CAT_VAR), no ✓ ◐ ≠ 熱 marks or red completion in the two
pages; one legend (SessChip.legend: 類型 / 完成度 / 其他, thresholds from the payload) behind a single
「? 圖例」 on both pages; no orphan keys in the catalogs these pages use. Repo files only; the legend runs
under node (skipped without it)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGES = ("schedule.html", "overview.html")
NODE = shutil.which("node")


def test_the_old_marks_are_gone_from_both_pages():
    """The ticket's grep: grep -c "\\-\\-k\\b\\|\\.k-\\|\\.ty-\\|offp-i\\|part-i\\|heat-tag\\|cp-red" = 0."""
    pat = re.compile(r"--k\b|\.k-|\.ty-|offp-i|part-i|heat-tag|cp-red")
    for page in PAGES:
        src = (STATIC / page).read_text(encoding="utf-8")
        assert not pat.findall(src), (page, sorted(set(pat.findall(src))))
    sch = (STATIC / "schedule.html").read_text(encoding="utf-8")
    for gone in ("CAT_VAR =", "fam-aerobic", ".wk.prov", "\n.cpw", "\n.offp {", "\n.part {", 'class="sw"', "cp-green", "cp-yellow"):
        assert gone not in sch, gone


def test_schedule_page_has_no_series_colours():
    src = (STATIC / "schedule.html").read_text(encoding="utf-8")
    assert not re.findall(r"--s[1-7]\b", src), sorted(set(re.findall(r"--s[1-7]\b", src)))
    assert "--qf2" not in src and "--qf3" not in src


def test_overview_cards_use_no_series_colours():
    """The 總覽's charts keep --s* (other uses); the week cards and their legend don't."""
    src = (STATIC / "overview.html").read_text(encoding="utf-8")
    week = src[src.index("// ---- this week card (SP-397) ----"):src.index("// ---- end this week card ----")]
    assert "--s" not in week and "--c" not in week


def test_one_legend_button_on_both_pages():
    sch = (STATIC / "schedule.html").read_text(encoding="utf-8")
    body = sch[sch.index("function renderLegend() {"):]
    body = body[:body.index("\n}\n")]
    assert "SessChip.legend(" in body and "k-quality" not in body and "legend." + '" + k' not in body
    ov = (STATIC / "overview.html").read_text(encoding="utf-8")
    assert 'SessChip.legend(' in ov


def _catalog(loc):
    return {f"{ns}.{k}": v for ns in ("common", "schedule", "overview")
            for k, v in json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8")).items()}


def test_no_orphan_keys_in_the_catalogs_these_pages_use():
    """Every key of common / schedule / overview is used: literally, as data-i18n, or under a prefix
    the code builds from ("sy." + st). The keys the legend replaced are gone in both languages."""
    srcs = {"schedule": [STATIC / "schedule.html"], "overview": [STATIC / "overview.html"],
            "common": sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.html"))}
    for ns, files in srcs.items():
        cat = json.loads((STATIC / "i18n" / "zh-TW" / f"{ns}.json").read_text("utf-8"))
        src = "\n".join(f.read_text("utf-8") for f in files)
        orphan = []
        for k in cat:
            if f'"{k}"' in src or f"'{k}'" in src or f'{ns}.{k}"' in src:
                continue
            parts = k.split(".")
            if any(f'"{".".join(parts[:i])}.' in src or f'"{ns}.{".".join(parts[:i])}.' in src for i in range(1, len(parts))):
                continue
            orphan.append(k)
        assert orphan == [], (ns, orphan)
    for loc in ("zh-TW", "en"):
        cat = _catalog(loc)
        gone = [k for k in cat if k.startswith("schedule.legend.") or k in (
            "schedule.vs.short_legend", "schedule.vs.levels_tip", "overview.week.short_aria")]
        assert gone == [], (loc, gone)


def _legend(expr: str, loc: str = "zh-TW"):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1], loc = process.argv[3];
const cat = {};
for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(`${dir}/i18n/${loc}/common.json`, "utf8")))) cat[`common.${k}`] = v;
const used = new Set();
const ctx = {};
ctx.window = ctx;
ctx.I18N = { t: (k, p) => { used.add(k); return String(cat[k] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)); } };
vm.createContext(ctx);
for (const f of ["dashicons.js", "sesschip.js"]) vm.runInContext(fs.readFileSync(`${dir}/${f}`, "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify({ out: ctx.OUT, used: [...used] }));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, loc], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_legend_table_three_sections_with_the_payload_thresholds():
    got = _legend('SessChip.legend({ levels: { green: 0.25, yellow: 0.5 }, other: [["<i>x</i>", "頁面自己的列"]] })'
                  ' + "\\n" + SessChip.levelsText({ green: 0.25 }) + SessChip.hrNote({ uncertain: true })')
    h = got["out"]
    assert h.startswith('<details class="sc-leg"') and h.count("<summary") == 1 and "圖例" in h
    assert "完成：時間和訓練負荷（Training load）都在計畫 ±25% 內" in h
    heads = re.findall(r'<th colspan="2"[^>]*>(.*?)</th>', h)
    assert len(heads) == 3 and heads[0].startswith("類型") and heads[1].startswith("完成度") and heads[2].startswith("其他")
    for k in ("easy", "z3", "z5", "long", "hike", "test", "strength", "race", "heat", "rest", "notice", "bike", "other"):
        assert _legend(f'DI("{k}")')["out"] in h, k
    for st in ("done", "partial", "off_plan", "missed", "todo"):
        assert f"sc-r-{st}" in h, st
    assert "±25%" in h and "±20%" not in h                                   # the threshold from the payload
    assert "頁面自己的列" in h and "--bad" not in h
    # every common.legend.* key is read by the legend, the card words or the type names
    cat = json.loads((STATIC / "i18n" / "zh-TW" / "common.json").read_text("utf-8"))
    keys = {f"common.{k}" for k in cat if k.startswith("legend.")}
    assert keys <= set(got["used"]), sorted(keys - set(got["used"]))
    en = _legend("SessChip.legend({})", "en")["out"]
    assert "Legend" in en and not re.search(r"[一-鿿]", re.sub(r"<[^>]+>", "", en))


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_levels_text_for_the_done_box_question_mark():
    t = _legend("SessChip.levelsText({ green: 0.2, yellow: 0.5 })")["out"]
    assert t.splitlines()[0].startswith("完成：") and "±20%" in t and "換了課：" in t and "跳過：" in t
    assert "✓" not in t and "≠" not in t and "◐" not in t


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_legend_table_ignores_a_page_s_own_table_look():
    """Review shot SP-399 #4 (2026-10-13): the 總覽's `th, td { white-space: nowrap; border-bottom }` made
    the legend's lines run past its box with rules between them."""
    css = _legend("SessChip.CSS")["out"]
    assert re.search(r"\.sc-leg th, \.sc-leg td \{ border: 0; white-space: normal;", css)
