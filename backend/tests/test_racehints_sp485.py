"""SP-485 (docs/research/ui-hint-audit.md §7 T4): the race calculator's result hints.

static/racehints.js under node (with static/hints.js, SP-482): the class-1 box on top (沒填補給站, 脫水 > 2 %,
目標太快 in every level), the method lines in 「這次怎麼算的」, rain by R1, the poles line by R8, the 「推估」
marks by R10, the heat block by R11. The server side: weather.rain_alert's `top` (R1) and the planner's RC10
condition (R12). Synthetic data only; the node tests are skipped without node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import hints as H
from backend.engine.racepower import planner as PL
from backend.engine.racepower import weather as WX

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def _run(expr: str, locale: str = "zh-TW", **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const ctx = { DATA: JSON.parse(process.argv[3]), console };
ctx.window = ctx;
const cat = {};
for (const ns of ["common", "racepower"]) {
  const d = JSON.parse(fs.readFileSync(process.argv[1] + "/i18n/" + process.argv[4] + "/" + ns + ".json", "utf8"));
  for (const [k, v] of Object.entries(d)) cat[ns + "." + k] = v;
}
const tr = (k, p) => {
  let v = cat[k];
  if (v && typeof v === "object") v = p && p.n === 1 ? v.one : v.other;
  return String(v ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m));
};
ctx.I18N = { t: tr };
ctx.T = (k, p) => tr("racepower." + k, p);
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/hints.js", "utf8"), ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/racehints.js", "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), locale],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _page(hints, level="standard", how=()):
    """RaceHints.page → which hint texts are in the box / the reminders / 「這次怎麼算的」 (in order)."""
    return _run("(() => { const r = RaceHints.page(DATA.h, {level: DATA.level, acks: [], how: DATA.how});"
                " const ids = (a) => a.map((x) => x.text);"
                " const re = /<(?:div class=\"hint-row\"|li) data-hint-id=\"[^\"]*\">(?:<span class=\"hint-text\">)?([^<]*)/g;"
                " const texts = (html) => [...html.matchAll(re)].map((m) => m[1]);"
                " return {box: texts(r.box), rem: ids(r.f.shown), more: ids(r.f.more), how: texts(r.how), counts: r.counts}; })()",
                h=hints, level=level, how=list(how))


# the calculator's warnings of a typical trail race, as the server sends them (calc.respond)
def _trail_warnings():
    return [H.warning(w) for w in [
        H.tag("RC12", "熱修正用單一溫度"),
        H.tag("RC14", "預估時間超過你最長有效紀錄的 1.5 倍"),
        H.tag("RC9", "79% 的路段坡度超過 8 %"),
        H.tag("RC10", "外插", smart=True),
        H.tag("RC7", "越野整場移動時間用心率配速模型"),
        H.tag("RC8", "分段維持平均分配"),
        H.tag("RC11", "熱：每段溫度由天氣點換算"),
        H.tag("RC15", "配速幅度 ≥ 4 %"),
        H.tag("RC16", "模型 CP 低於你實際撐過的功率"),
        H.tag("RC13", "超過 2800 m 的海拔修正是外插"),
        H.tag("RC29", "沒有預報：比賽日溫度用氣候值"),
    ]] + [H.warning(w) for w in [   # the 補給 card's
        H.tag("RC22", "熱量交叉檢查"),
        H.tag("RC21", "沒有輸入補給站"),
        H.tag("RC20", "預估脫水約 3.1 % 體重"),
    ]]


# ---- the box on top: class 1 in every level, first by §5.2's priority ---------------------------------

@needs_node
@pytest.mark.parametrize("level", H.LEVELS)
def test_no_stops_dehydration_and_a_too_fast_goal_are_on_top_in_every_level(level):
    goal = {"id": "RC6", "cls": 1, "show": "●●●", "prio": 3, "smart": True, "text": "比模型預測快 12%，很可能撐不住"}
    out = _page(_trail_warnings() + [goal], level)
    box = out["box"]
    # the three of the acceptance: first in the box (priority 1 安全 → 3 比賽來不來得及), every level
    assert box[:2] == ["沒有輸入補給站", "預估脫水約 3.1 % 體重"], box
    assert box.index("比模型預測快 12%，很可能撐不住") < box.index("模型 CP 低於你實際撐過的功率")
    assert set(box) == {"沒有輸入補給站", "預估脫水約 3.1 % 體重", "比模型預測快 12%，很可能撐不住",
                        "預估時間超過你最長有效紀錄的 1.5 倍", "模型 CP 低於你實際撐過的功率"}
    # never in the reminders, never in 「這次怎麼算的」
    assert not set(box) & set(out["rem"] + out["more"] + out["how"])


@needs_node
def test_the_method_lines_go_to_how_it_was_worked_out_in_standard_and_lean():
    methods = {"熱：每段溫度由天氣點換算", "越野整場移動時間用心率配速模型", "分段維持平均分配",
               "79% 的路段坡度超過 8 %", "配速幅度 ≥ 4 %", "熱量交叉檢查"}
    for level in ("standard", "lean"):
        out = _page(_trail_warnings(), level)
        assert methods <= set(out["how"]), (level, out["how"])
        assert not methods & set(out["rem"] + out["more"] + out["box"]), level
    # 完整: RC9 is ● there (a reminder), the other method lines still in 「這次怎麼算的」
    out = _page(_trail_warnings(), "full")
    assert "79% 的路段坡度超過 8 %" in out["rem"] + out["more"]
    assert methods - {"79% 的路段坡度超過 8 %"} <= set(out["how"])


@needs_node
def test_at_most_three_reminders_and_the_rest_folded():
    out = _page(_trail_warnings(), "standard")
    # RC12, RC10, RC13 and RC29 are reminders (all §5.2 priority 6: the engine's order); the 4th is folded
    assert out["rem"] == ["熱修正用單一溫度", "外插", "超過 2800 m 的海拔修正是外插"]
    assert out["more"] == ["沒有預報：比賽日溫度用氣候值"]


@needs_node
def test_extrapolation_only_when_its_condition_holds():
    ws = [H.warning(H.tag("RC10", "外插", smart=False))]
    assert _page(ws, "standard")["how"] == ["外插"]
    assert _page(ws, "full")["rem"] == ["外插"]                           # 完整 ●
    assert _page([H.warning(H.tag("RC10", "外插", smart=True))], "standard")["rem"] == ["外插"]


@needs_node
def test_the_pages_own_lines_can_be_sent_to_how():
    out = _run("RaceHints.page([], {level: 'standard', how: [RaceHints.make('RC1', '整場用心率模型'),"
               " RaceHints.make('RC4', '', {html: '<b>熱影響</b>'})]}).how")
    assert "整場用心率模型" in out and "<b>熱影響</b>" in out


# ---- R1 rain, R8 poles, R10 badges, R11 heat ---------------------------------------------------------

@needs_node
def test_rain_place():
    f = "[null, {alert: false, top: false}, {alert: true, top: false}, {alert: true, top: true}, {alert: true}]" \
        ".map((r) => RaceHints.rainPlace(r))"
    assert _run(f) == [None, None, "card", "top", "top"]      # an old answer without `top`: on top, as before


ROWS = [
    {"start_km": 0.0, "grade": 0.02, "pole_hint": None},
    {"start_km": 2.4, "grade": 0.123, "pole_hint": {"key": "up", "text": "用杖：自覺比較輕鬆，速度差不多", "src": "Giovanelli"}},
    {"start_km": 3.1, "grade": 0.181, "pole_hint": {"key": "up", "text": "用杖：自覺比較輕鬆，速度差不多", "src": "Giovanelli"}},
    {"start_km": 5.0, "grade": -0.17, "pole_hint": {"key": "down", "text": "用杖：膝蓋負擔少 12–25 %", "src": "Schwameder"}},
]


@needs_node
@pytest.mark.parametrize("locale,want", [
    ("zh-TW", "會用杖：陡上坡（≥ 12 %）自覺比較輕鬆，速度差不多、陡下坡（≤ −15 %）膝蓋負擔少 12–25 %；第一段在 2.4 km"),
    ("en", "With poles: steep climbs (≥ 12 %) feel easier at about the same speed, steep descents (≤ −15 %) put 12–25 % "
           "less load on the knees; the first such segment is at 2.4 km"),
])
def test_poles_one_line_for_the_card(locale, want):
    assert _run("RaceHints.poleLine(RaceHints.poleSummary(DATA.rows), T)", locale, rows=ROWS) == want
    assert _run("RaceHints.poleSummary(DATA.rows.slice(0, 1))", rows=ROWS) is None


@needs_node
def test_poles_per_segment_only_in_full_and_the_line_not_in_lean():
    out = _run("['full', 'standard', 'lean'].map((l) => RaceHints.poleShow(l))")
    assert out == [{"line": True, "perSeg": True}, {"line": True, "perSeg": False}, {"line": False, "perSeg": False}]


@needs_node
def test_estimate_badges_by_level():
    assert _run("['full', 'standard', 'lean', null].map((l) => RaceHints.estMode(l))") == ["each", "card", "tip", "card"]


@needs_node
def test_heat_block_only_when_the_heat_changes_the_time():
    f = "DATA.x.map((b) => RaceHints.heatSmart({band: b}))"
    bands = [{"raw_penalty_pct": 0.0, "center": {"penalty_pct": 0.0}},     # a cool day: nothing to say
             {"raw_penalty_pct": 0.6, "center": {"penalty_pct": 0.3}},
             {"raw_penalty_pct": 1.4, "center": {"penalty_pct": 1.4}},     # race-day heat ≥ 1 %
             {"raw_penalty_pct": 3.0, "center": {"penalty_pct": 1.8}}]
    assert _run(f, x=bands) == [False, False, True, True]
    assert _run("RaceHints.heatSmart(null)") is False


@needs_node
def test_the_pages_own_rows_follow_the_catalog():
    rows = _run("RaceHints.ROWS")
    for hid, (cls, show, prio) in rows.items():
        e = H.CATALOG[hid]
        assert (cls, show, prio) == (e.cls, e.show, e.prio), hid
    assert set(_run("RaceHints.METHOD_IDS")) == {"RC1", "RC7", "RC8", "RC9", "RC11", "RC15", "RC22"}


# ---- the server: R1 and R12 --------------------------------------------------------------------------

DAY = "2026-10-07"
WIN = (WX.dt.datetime(2026, 10, 7, 6), WX.dt.datetime(2026, 10, 7, 12))


def _rows(pop, mm_each, n=5):
    return [{"start": f"{DAY}T{h:02d}:00", "end": f"{DAY}T{h + 1:02d}:00", "pop_pct": pop, "mm": mm_each}
            for h in range(6, 6 + n)]


def test_rain_60_percent_but_1_mm_is_not_on_top():
    r = WX.rain_alert(_rows(60, 0.2), WIN)                   # 1.0 mm over the race
    assert r["alert"] is True and r["top"] is False and r["total_mm"] == pytest.approx(1.0)


@pytest.mark.parametrize("pop,mm,kind,top", [
    (60, 1.0, "trail", True),       # 5 mm at 60 %
    (30, 1.0, "trail", False),      # 5 mm at 30 %: a line on the card
    (30, 2.0, "trail", True),       # 10 mm whatever the PoP
    (60, 1.0, "road", False),       # road: twice the amounts
    (60, 2.0, "road", True),
    (30, 4.0, "road", True),        # 20 mm
    (40, 0.1, "trail", False),      # nothing to say at all (alert False)
])
def test_rain_top_follows_r1(pop, mm, kind, top):
    assert WX.rain_alert(_rows(pop, mm), WIN, kind)["top"] is top


def test_rain_without_an_amount_stays_on_top_as_before():
    rows = [{"start": f"{DAY}T06:00", "end": f"{DAY}T09:00", "pop_pct": 70, "mm": None}]   # CWA: PoP only
    r = WX.rain_alert(rows, WIN)
    assert r["alert"] is True and r["top"] is True


def test_extrapolated_share_of_the_time():
    segs = [{"t": 600, "trusted": True}, {"t": 100, "trusted": False}, {"t": 300, "trusted": True}]
    assert PL.extrap_share(segs) == pytest.approx(0.1)
    assert PL.extrap_share(segs) < PL.EXTRAP_HINT_SHARE
    assert PL.extrap_share(segs + [{"t": 200, "trusted": False}]) == pytest.approx(0.25)
    assert PL.extrap_share([]) == 0.0


def test_tagged_carries_its_condition_to_the_hint():
    w = H.tag("RC10", "外插", smart=False)
    assert isinstance(w, str) and w == "外插"
    assert H.warning(w)["smart"] is False
    assert H.warning(H.tag("RC10", "外插"))["smart"] is True
    import copy
    import pickle
    assert H.warning(copy.deepcopy(w))["smart"] is False
    assert H.warning(pickle.loads(pickle.dumps(w)))["smart"] is False
