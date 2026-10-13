"""SP-470: the 課表 card's hover is a laid-out card (static/sesspop.js), not the browser's plain title
text. schedule.html's popModel (the session card block) and SessPop.html run under node with the real
sesschip.js / dashicons.js and the zh-TW / en catalogs: a header (title, type · day, the status pill),
the numbers, then 完成情況 / 目標 / 怎麼練 / 調整 / 手錶 as sections — the target split into its parts
with the basis apart, the detail one bullet per clause (never split inside brackets). Synthetic
sessions only; skipped without node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.tests.test_schedule_cards_sp396 import STUBS, _block, _done, _future

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "schedule.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

EASY_TARGET = "心率 < 154 bpm · 功率 124–153 W · 最近 6 次輕鬆跑：AeT 154 bpm ≈ 7:54/km、169 W（同心率跑更快就自動跟著調；推估）"


def _run(expr: str, loc: str = "zh-TW", **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1], loc = process.argv[7];
const cat = {};
for (const ns of ["common", "schedule"]) for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(`${dir}/i18n/${loc}/${ns}.json`, "utf8")))) cat[`${ns}.${k}`] = v;
const ctx = { DATA: JSON.parse(process.argv[3]) };
ctx.window = ctx;
ctx.I18N = { t: (k, p) => String(cat[k] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)),
  fmt: { date: (iso) => iso.slice(5).replace("-", "/") } };
vm.createContext(ctx);
for (const f of ["dashicons.js", "sesschip.js", "sesspop.js"]) vm.runInContext(fs.readFileSync(`${dir}/${f}`, "utf8"), ctx);
vm.runInContext(process.argv[4] + process.argv[5] + process.argv[6] + "\nOUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), STUBS, _block("session card"),
                        _block("done box"), loc], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _pop(s, loc="zh-TW"):
    return _run("SessPop.html(popModel(DATA.s))", loc, s=s)


def _sections(h):
    return re.findall(r'<section class="sp-sec[^"]*"><h4>([^<]+)</h4>', h)


def test_target_parts_and_the_basis_apart():
    t = _run("SessPop.target(DATA.t)", t=EASY_TARGET)
    assert t["items"] == [{"label": "心率", "value": "< 154 bpm"}, {"label": "功率", "value": "124–153 W"}]
    assert t["basis"] == ["最近 6 次輕鬆跑：AeT 154 bpm ≈ 7:54/km、169 W（同心率跑更快就自動跟著調；推估）"]
    t = _run("SessPop.target(DATA.t)", t="功率 290–310 W（106–113% CP） · RPE 7")
    assert t["items"] == [{"label": "功率", "value": "290–310 W（106–113% CP）"}, {"label": "RPE", "value": "7"}] and not t["basis"]
    t = _run("SessPop.target(DATA.t)", t="HR < 150 bpm · Power 169–208 W · last 3 easy runs: AeT 150 bpm ≈ 5:25/km")
    assert [x["label"] for x in t["items"]] == ["HR", "Power"] and t["basis"][0].startswith("last 3 easy runs")


def test_detail_one_clause_each_never_inside_brackets():
    c = _run("SessPop.clauses(DATA.d)", d="暖身 15 分；3 分 × 5 趟（休 3 分；慢跑）；緩和 10 分。下坡放鬆。")
    assert c == ["暖身 15 分", "3 分 × 5 趟（休 3 分；慢跑）", "緩和 10 分", "下坡放鬆"]
    assert _run("SessPop.clauses(DATA.d)", d="About 8.0 km flat. Warm up 15 min; cool down 10 min") == \
        ["About 8.0 km flat", "Warm up 15 min", "cool down 10 min"]


def test_future_easy_run_sections_in_order():
    s = _future(target=EASY_TARGET, detail="心率不超過輕鬆跑上限 154 bpm；全程能講話", source="Uphill Athlete")
    h = _pop(s)
    assert _sections(h) == ["目標", "怎麼練"]                       # nothing done yet, no change, the watch is fine
    assert '<b class="sp-t">輕鬆跑</b>' in h and '<span class="sp-sub">10/12</span>' in h      # 「輕鬆跑」 is said once
    assert '<small>時間</small><b>45′</b>' in h and '<small>負荷（Load）</small><b>35</b>' in h
    assert "<dt>心率</dt><dd>&lt; 154 bpm</dd>" in h and "<dt>功率</dt><dd>124–153 W</dd>" in h
    assert '<p class="sp-basis"><span class="sp-k">依據</span>最近 6 次輕鬆跑' in h
    assert "<li>心率不超過輕鬆跑上限 154 bpm</li><li>全程能講話</li>" in h
    assert 'class="sp-pill' not in h                                 # a plain session to come: no status pill


def test_done_session_says_how_it_went_first():
    h = _pop({**_done(short=True), "note": "心率帶沒戴好"})
    assert _sections(h)[0] == "完成情況"
    assert 'class="sp-pill sp-partial"' in h and "部分 72%" in h and "sc-r-partial" in h
    assert '<p class="sp-vd">強度不足<span class="sp-nums">負荷 74% · 時間 87% · 強度 72%</span></p>' in h
    assert '<span class="sp-k">實際</span>路跑 · Z5 間歇 0:52' in h and "強度不足：只做到這堂課的 72%" in h
    assert "⚠ 心率帶沒戴好" in h and "<b>70 → 52</b>" in h
    # green: no reason words, the numbers still say how close it was
    ok = _pop(_done("green"))
    assert '<p class="sp-vd"><span class="sp-nums">負荷 74% · 時間 87% · 強度 72%</span></p>' in ok and "sp-pill sp-done" in ok


def test_changes_and_the_watch_get_their_own_sections():
    s = _future(mark={"state": "auto"}, coros={"status": "failed", "error": "COROS 401"})
    h = _pop(s)
    assert _sections(h)[-2:] == ["調整", "手錶"]
    assert '<p class="sp-mk">↻ 自動調整：疲勞保護</p><p>TSB −32</p>' in h
    assert '<p class="sp-bad">推送失敗</p><p class="sp-dim">COROS 401</p>' in h
    prov = _pop(_future(provisional=True))
    assert 'class="sp-pill sp-todo"' in prov and "暫定" in prov


def test_quality_session_family_and_steps():
    s = _future(kind="quality", title="VO2max 間歇 5×3 分", target="功率 290–310 W（106–113% CP）",
                detail="暖身 15 分；3 分 × 5 趟、每趟之間休 3 分慢跑；緩和 10 分", distance_km=9.4, climb_m=120,
                quality_family={"id": "vo2max", "label": "VO2max 間歇", "text": "VO2max 間歇（5 區）", "sub_label": True})
    h = _pop(s)
    assert "強度課（VO2max 間歇（5 區）） · 10/12" in h
    assert '<small>距離</small><b>9.4 km</b>' in h and '<small>爬升</small><b>↑120 m</b>' in h
    assert h.count("<li>") == 3 and _run('DI("z5")') in h


def test_english_labels():
    h = _pop(_done(short=True), "en")
    assert _sections(h) == ["How it went"] and '<span class="sp-k">Actual</span>' in h
    h = _pop(_future(target=EASY_TARGET), "en")
    assert _sections(h) == ["Target"] and '<span class="sp-k">Based on</span>' in h and "<small>Time</small>" in h


def test_text_is_escaped():
    h = _pop(_future(title="<img src=x>", detail="<b>x</b>"))
    assert "<img" not in h and "&lt;img src=x&gt;" in h and "&lt;b&gt;x&lt;/b&gt;" in h


def test_page_wiring():
    html = PAGE.read_text(encoding="utf-8")
    assert html.index("/api/v1/static/sesschip.js") < html.index("/api/v1/static/sesspop.js")
    assert 'SessPop.bind($("cal")' in html
    chip = _block("session card")
    assert 'title=""' in chip and "title=\"${esc(tip)}\"" not in chip     # no plain title text over the card
    core = (STATIC / "offline_core.js").read_text(encoding="utf-8")
    assert '"/api/v1/static/sesspop.js"' in core                          # the PWA cache has it (offline 課表)
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        for k in ("done", "target", "how", "changed", "watch", "basis", "actual", "time", "dist", "climb", "tss"):
            assert cat.get("sesspop." + k), (loc, k)
    js = (STATIC / "sesspop.js").read_text(encoding="utf-8")
    code = re.sub(r"//.*", "", js)
    code = re.sub(r"/(?:\\.|[^/\n])+/[gimsuy]*", "", code)                # regex literals (label words)
    assert not re.search(r"[一-鿿]", code), "UI words go through the catalog"
