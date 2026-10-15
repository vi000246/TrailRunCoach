"""活動與分析頁的提示精簡 (SP-487 = docs/research/ui-hint-audit.md §7 T6; §1.4 AC, §1.5 AN, §5 R2 / R6 / R9).

活動編輯框: 「自動」 badge gone in 標準 (AC5), 「再標有杖 N 次」 only on the trail runs / hikes the pole chart
counts and, in 標準, while unmarked (R9), the rain hint on 越野 / 登山 only (R2), the dashed 補資料 mark by level
(AC4). 分析頁: no CP-test card off a test (AN8), the HR shading of R6 (< 1 % → none, the seconds in the
legend's ?), the drift chips 3 in 標準 (AN6) with 「心率可疑」 (AN4) in every level, 「參考」 only in the form
page's name (AN10), the halves renamed (AN13), the segments card (AN12). The page logic runs under node with
the real hints.js and the page's own marked blocks (as test_schedule_hints_sp484.py). Synthetic data only."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend.engine import hints as H
from backend.engine import hr_quality as HQ
from backend.engine import workout_review as R
from backend.engine.panels import activity_charts as A
from backend.tests.test_activity_charts import _ds as _cds, _run as _crun, _steady
from backend.tests.test_review_cards import TODAY, _by_id, _ds
from backend.tests.test_workout_review import _run

STATIC = Path(__file__).resolve().parents[1] / "static"
VIEWS = Path(__file__).resolve().parents[2] / "views"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
LEVELS = ("full", "standard", "lean")


def _block(page: str, start: str, end: str) -> str:
    m = re.search(rf"// ---- {re.escape(start)} ----.*?// ---- {re.escape(end)} ----", (STATIC / page).read_text("utf-8"), re.S)
    assert m, start
    return m.group(0)


def _node(page: str, start: str, end: str, stubs: str, expr: str, level: str, **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const ctx = { DATA: JSON.parse(process.argv[3]) };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(`${process.argv[1]}/hints.js`, "utf8"), ctx);
vm.runInContext(process.argv[5] + process.argv[6] + "\nOUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    stub = stubs.replace("__LEVEL__", json.dumps(level))
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), level, stub, _block(page, start, end)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------
# shared: Hints.where and hints.flag
# ---------------------------------------------------------------------------

def test_flag_is_the_display_part_of_a_catalog_row():
    assert H.flag("AN6", smart=False) == {"id": "AN6", "cls": 2, "prio": 6, "smart": False, "show": "●◐▷"}
    assert H.flag("AN4")["cls"] == 1 and H.flag("AN5", smart=True)["show"] == "●◐◐"
    assert H.flag("AN14", cls=1)["show"] == "●●●"                      # a class-1 case of a class-2 row
    with pytest.raises(KeyError):
        H.flag("AN99")


# ---------------------------------------------------------------------------
# 活動編輯框 (activity.html, AC4 / AC5 / AC8)
# ---------------------------------------------------------------------------

AC_STUBS = r"""
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const T = (k) => ({ manual: "手動", auto: "自動", manual_tip: "你改過", auto_tip: "自動判定" })[k] || k;
const S = { hint: { level: __LEVEL__, acks: [] } };
const polesOf = (a) => a.poles || "";
"""


def _ac(expr, level, **data):
    return _node("activity.html", "editor hints (SP-487)", "end editor hints", AC_STUBS, expr, level, **data)


def test_activity_rows_match_the_catalog():
    page = (STATIC / "activity.html").read_text("utf-8")
    rows = {k: (int(c), sh) for k, c, sh in re.findall(r'(AC\d+): \[(\d), "([●◐▷✕]{3})"\]', page)}
    assert set(rows) == {"AC4", "AC5", "AC8"}
    for k, (cls, show) in rows.items():
        assert (H.CATALOG[k].cls, H.CATALOG[k].show) == (cls, show), k
    assert '<script src="/api/v1/static/hints.js"></script>' in page and "S.hint = hint" in page


@needs_node
def test_auto_badge_gone_in_standard_manual_stays():
    out = {lv: _ac("[badge(false, 'why'), badge(true)]", lv) for lv in LEVELS}
    assert "自動" in out["full"][0] and "手動" in out["full"][1]              # 完整: as before
    assert out["standard"][0] == "" and "手動" in out["standard"][1]          # AC5: only 「手動」
    assert out["lean"] == ["", ""]


@needs_node
def test_pole_hint_never_on_a_road_run_and_only_unmarked_in_standard():
    """驗收 1 (杖): a road run (pole_chart false — its mark never counts) has no pole hint in any level."""
    road, trail, marked = {"pole_chart": False}, {"pole_chart": True}, {"pole_chart": True, "poles": "with"}
    for lv in LEVELS:
        assert _ac("poleHintShown(DATA.a)", lv, a=road) is False, lv
    assert _ac("poleHintShown(DATA.a)", "standard", a=trail) is True
    assert _ac("poleHintShown(DATA.a)", "standard", a=marked) is False      # R9: already marked
    assert _ac("poleHintShown(DATA.a)", "full", a=marked) is True           # 完整 ●: the count stays live
    assert _ac("poleHintShown(DATA.a)", "lean", a=trail) is False


@needs_node
def test_supplementary_source_mark_by_level():
    assert [_ac('acWhere("AC4")', lv) for lv in LEVELS] == ["show", "detail", "hidden"]


def test_editor_pole_line_uses_the_rule_and_rain_stays_trail_only():
    page = (STATIC / "activity.html").read_text("utf-8")
    assert "poleHintShown(a) ? esc(poleHint())" in page
    # R2 is the SP-299 rule: rain_kind (越野跑 / 登山健行), 路況 未標 (backend: test_rain_hint.py road runs)
    assert "const rainHint = (a) => a.rain_kind &&" in page


# ---------------------------------------------------------------------------
# 分析頁: CP 測試卡 (AN8), chips (AN6 / AN4), 跑姿 (AN10), 前後半 (AN13)
# ---------------------------------------------------------------------------

def test_no_cp_test_card_off_a_test():
    """驗收 2: a non-test session's cp_test card is hidden (it used to be 「這次不是 CP 測試…」 on every activity)."""
    ds = _ds(_run(TODAY, minutes=52, hr=135.0, power=200.0))
    r = R.review(ds, ds.workouts[0], "cp_test")
    assert r["hide"] is True and not r.get("empty")
    assert r["classification"]["type"] != "test_cp"


def test_drift_chips_carry_their_hint_and_hr_suspect_is_class_1():
    ds = _ds(_run(TODAY, minutes=60, hr=130.0, hr_end=150.0, power=200.0))
    C = _by_id(R.review(ds, ds.workouts[0], "aerobic")["cards"])
    chips = {k: c for k, c in C.items() if c["kind"] == "chip"}
    assert chips and all(c["hint"]["id"] == "AN6" for c in chips.values())
    assert chips["other"]["hint"]["smart"] is False                          # 標準 puts it into 「詳細」
    fake = R._card("chip", hint=H.flag("AN4"))
    assert fake["hint"]["cls"] == 1


def _chip_cards(**kw):
    """a drift card's chips as workout_review makes them (text, level, smart)."""
    mk = lambda cid, smart, hid="AN6": {"kind": "chip", "id": cid, "text": cid, "hint": H.flag(hid, smart=smart)}  # noqa: E731
    return [mk("other", False), mk("cut", False), mk("ramps", False), mk("heat", True), mk("stability", False),
            mk("hr_quality", True, "AN4"), mk("noisy", True), mk("over", True), mk("baseline", False)]


VW_STUBS = r"""
const S = { hint: { level: __LEVEL__, acks: [] } };
"""


def _vw(expr, level, **data):
    return _node("charts.html", "analysis hints (SP-487)", "end analysis hints", VW_STUBS, expr, level, **data)


@needs_node
def test_drift_chips_three_in_standard_and_hr_suspect_in_every_level():
    """AN6: 標準 keeps the three that change the reading (熱 > 25 °C, 資料比較雜, AeT+3 以上 > 10 %); 驗收 4:
    「心率可疑」 (AN4) shows in 完整, 標準 and 精簡."""
    ids = lambda xs: [c["id"] for c in xs]  # noqa: E731
    out = {lv: _vw("chipSplit(DATA.c)", lv, c=_chip_cards()) for lv in LEVELS}
    assert len(out["full"]["shown"]) == 9 and out["full"]["detail"] == []
    assert ids(out["standard"]["shown"]) == ["heat", "hr_quality", "noisy", "over"]
    assert len([c for c in out["standard"]["shown"] if c["hint"]["id"] == "AN6"]) == 3
    assert ids(out["standard"]["detail"]) == ["other", "cut", "ramps", "stability", "baseline"]
    assert ids(out["lean"]["shown"]) == ["hr_quality"] and len(out["lean"]["detail"]) == 8
    for lv in LEVELS:
        assert "hr_quality" in ids(out[lv]["shown"]), lv
    # a chip without a hint (the 本次重點 cards, an older answer): shown, as before
    assert _vw("chipSplit([{id: 'x'}])", "lean")["shown"] == [{"id": "x"}]


def test_viewer_draws_the_put_away_chips_under_details():
    page = (STATIC / "charts.html").read_text("utf-8")
    assert "chipSplit(chipsAll)" in page and 'class="rv-more"' in page and 't("viewer.rv_more"' in page
    assert '<script src="/api/v1/static/hints.js"></script>' in page and "S.hint = hint" in page
    for lang, word in (("zh-TW", "詳細（{n}）"), ("en", "Details ({n})")):
        assert json.loads((STATIC / f"i18n/{lang}/viewer.json").read_text("utf-8"))["rv_more"] == word


def test_form_page_says_reference_once_in_its_name():
    """AN10: 「參考」 only in the page name 跑姿與膝蓋負荷（參考）; the chart titles and the row labels drop it."""
    v = json.loads((VIEWS / "workout.json").read_text("utf-8"))
    page = next(d for d in v["dashboards"] if d["id"] == "form-knee")
    assert page["title"] == "跑姿與膝蓋負荷（參考）"
    assert [c["title"] for c in page["charts"]] == ["前半對後半（依作功切分）", "跑姿依坡度", "跑姿隨疲勞的變化（依作功）", "步頻與衝擊"]
    en = json.loads((VIEWS / "i18n/en.json").read_text("utf-8"))["workout"]
    assert en["dashboards"]["form-knee"]["title"] == "Running form and knee load (reference)"
    assert not any("reference" in en["charts"][c["id"]]["title"] for c in page["charts"])
    src = (Path(R.__file__)).read_text("utf-8")
    assert "指標（參考）" not in src and "判讀（參考）" not in src and '_("參考：' not in src


def test_halves_renamed_apart_from_the_drift_card():
    """AN13: the brushed stats and the HR trend's halves number is 「前後半效率差（含暖身）」, not 心率飄移."""
    zh = json.loads((STATIC / "i18n/zh-TW/viewer.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n/en/viewer.json").read_text("utf-8"))
    assert zh["st.drift"] == "前後半效率差（含暖身）" and en["st.drift"] == "Halves efficiency gap (incl. warm-up)"
    hp = next(c for d in json.loads((VIEWS / "workout.json").read_text("utf-8"))["dashboards"]
              for c in d["charts"] if c["id"] == "hr-power")
    assert "前後半效率差（Pw:HR 前後半的差，含暖身" in hp["description"] and "前後半的心率飄移" not in hp["description"]


# ---------------------------------------------------------------------------
# 心率灰底 (AN5, R6)
# ---------------------------------------------------------------------------

def _quality(monkeypatch, n, **masks):
    def fake(t, hr, cad=None, speed=None, grid=None):
        g = np.arange(n, dtype=float)
        ms = {k: np.zeros(n, dtype=bool) for k in ("range", "spike", "lock", "flat", "step", "high_start")}
        for k, spans in masks.items():
            for a, b in spans:
                ms[k][a:b] = True
        return HQ.Quality(g=g, hr=np.full(n, 140.0), masks=ms, events={})
    monkeypatch.setattr(HQ, "assess", fake)


def _spans(monkeypatch, **masks):
    t, hr, p, v = _steady()                          # 3601 s, HR on every second
    ds = _cds(_crun(t, hr, p, v))
    _quality(monkeypatch, len(t), **masks)
    return A.hr_quality_spans(ds, ds.workouts[0])


def test_short_cadence_lock_under_one_percent_is_not_shaded(monkeypatch):
    """驗收 3: only brief cadence locks (< 1 %, each < 60 s): no key stretch, smart false, the seconds named."""
    q = _spans(monkeypatch, lock=[(600, 615), (1800, 1810)])
    assert [r[2] for r in q["spans"]] == ["lock", "lock"]                    # 完整 still has both
    assert q["key_spans"] == [] and q["flagged_s"] == 25 and q["key_s"] == 0
    assert q["hint"] == H.flag("AN5", smart=False)
    assert "有 25 秒可能不準" in q["secs_note"] and "跟著步頻走" in q["secs_note"] and "0.7%" in q["secs_note"]
    assert "沒有畫灰底" in q["quiet_note"]


def test_long_lock_and_spikes_shaded_when_they_reach_one_percent(monkeypatch):
    q = _spans(monkeypatch, lock=[(600, 620), (1000, 1080)], spike=[(2000, 2010)])
    assert [r[2] for r in q["key_spans"]] == ["lock", "spike"]                # the 80-s lock and the spike
    assert q["key_s"] == 90 and q["flagged_s"] == 110 and q["hint"]["smart"] is True     # 90 / 3601 ≥ 1 %
    small = _spans(monkeypatch, spike=[(2000, 2020)])                         # 20 s < 1 %
    assert small["key_s"] == 20 and small["hint"]["smart"] is False


def test_shading_note_is_translated(monkeypatch):
    from backend.i18n import use_locale
    with use_locale("en"):
        q = _spans(monkeypatch, lock=[(600, 620)])
    assert q["secs_note"].startswith("20 s") and "not shaded" in q["quiet_note"]


HQ_DATA = {"spans": [[600, 620, "lock"], [1000, 1080, "lock"], [2000, 2010, "spike"]],
           "key_spans": [[1000, 1080, "lock"], [2000, 2010, "spike"]], "labels": {"lock": "跟著步頻走", "spike": "突然跳動"},
           "note": "灰底是…", "key_note": "只畫會影響數字的段落", "secs_note": "有 110 秒可能不準", "quiet_note": "沒有畫灰底"}


@needs_node
def test_hr_shading_by_level():
    on = {**HQ_DATA, "hint": H.flag("AN5", smart=True)}
    off = {**HQ_DATA, "key_spans": [], "hint": H.flag("AN5", smart=False)}
    full = _vw("hrqView(DATA.q)", "full", q=off)
    assert full["shaded"] and len(full["spans"]) == 3                        # 完整: every flag, as before
    std = _vw("hrqView(DATA.q)", "standard", q=on)
    assert std["spans"] == on["key_spans"] and "只畫會影響數字的段落" in std["tip"] and "110 秒" in std["tip"]
    for lv in ("standard", "lean"):                                          # 驗收 3: < 1 % → no shading
        quiet = _vw("hrqView(DATA.q)", lv, q=off)
        assert quiet["spans"] == [] and not quiet["shaded"]
        assert "有 110 秒可能不準" in quiet["tip"] and "沒有畫灰底" in quiet["tip"]
    old = {k: v for k, v in HQ_DATA.items() if k not in ("key_spans",)}       # an answer from before SP-487
    assert _vw("hrqView(DATA.q)", "lean", q=old)["spans"] == HQ_DATA["spans"]


def test_viewer_legend_keeps_the_question_mark_without_shading():
    page = (STATIC / "charts.html").read_text("utf-8")
    assert 'data-hrq="${HV.shaded ? "shaded" : "quiet"}"' in page and "rvQ(HV.tip)" in page
    assert "...(HV && HV.shaded ? { markArea" in page


# ---------------------------------------------------------------------------
# 重複路段卡 (AN12) and the static files
# ---------------------------------------------------------------------------

def test_segments_card_default_page_only_and_no_empty_card():
    js = (STATIC / "segments_card.js").read_text("utf-8")
    assert '{ id: "AN12", cls: 3, show: "●◐◐" }' in js and H.CATALOG["AN12"].show == "●◐◐"
    assert "if (res.built && !keepNone(state())) { none.add(idx); card.remove(); return; }" in js
    assert "if (!onPage(s) || (none.has(s.workout) && !keepNone(s))) { cur?.remove(); return; }" in js


def test_hints_js_is_offline():
    assert '"/api/v1/static/hints.js"' in (STATIC / "offline_core.js").read_text("utf-8")
