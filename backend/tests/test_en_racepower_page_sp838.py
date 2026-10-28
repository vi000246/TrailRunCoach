"""SP-838: the race calculator (賽事計算機) and the season plan (賽事周期) in English — the pages' own text.

racepower.html: the tabs, the target and course form, 進階計算選項, the result cards (road / trail / 登山), the
segment table, 補給, 準確度, the analyses and every ? tip go through the racepower catalog (T(k) = t("racepower."+k));
a result saved with a plan event is shown only on a page in the language it was computed in. plan.html: its
last literals; api/plan.py / engine/planning.py: the goal labels and the threshold-method words in the request's
language. The Chinese pages are unchanged (the zh-TW catalog keeps the exact text).

Synthetic data only (a tmp Plan, the page sources and catalogs)."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from backend.i18n import use_locale

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = (STATIC / "racepower.html").read_text("utf-8")
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _markup(html: str) -> str:
    return re.sub(r"<script\b[^>]*>.*?</script>|<style>.*?</style>|<!--.*?-->", "", html, flags=re.S)


# ---- no Chinese left in the sources ------------------------------------------------------------------------

def test_the_pages_have_no_unwrapped_chinese():
    from backend.scripts import i18n_extract as X
    scans = X.scan_repo()
    for f in ("backend/static/racepower.html", "backend/static/plan.html", "backend/api/plan.py",
              "backend/engine/planning.py"):
        assert scans[f].unwrapped == [], (f, scans[f].unwrapped[:5])


def test_the_page_scripts_have_no_chinese_punctuation_either():
    """The scanner looks for ideographs; a script string of only fullwidth punctuation (（）、：～) would still
    show on the English page."""
    from backend.scripts import i18n_extract as X
    allow = X.load_allow()
    for name in ("racepower.html", "plan.html"):
        src = (STATIC / name).read_text("utf-8")
        left = []
        for m in re.finditer(r"<script>(.*?)</script>", src, re.S):
            left += [s for _ln, s in X.js_strings(m.group(1)) if CJK.search(s) and not allow.text_ok(s)]
        assert left == [], (name, left[:5])


# ---- the catalogs -------------------------------------------------------------------------------------------

def test_every_key_the_calculator_asks_for_exists_in_both_languages():
    zh, en = _cat("zh-TW", "racepower"), _cat("en", "racepower")
    assert set(zh) == set(en)
    # not a prefix: T("goal." + k), T("tdiff.what_" + k)
    asked = {k for k in re.findall(r'\bT[R]?\("([\w.]*\w)"', PAGE) if not k.endswith("_")}
    asked |= set(re.findall(r'data-i18n="racepower\.([\w.]+)"', PAGE))
    asked |= {k.split(":", 1)[1].strip()[len("racepower."):] for a in re.findall(r'data-i18n-attr="([^"]+)"', PAGE)
              for k in a.split(";") if "racepower." in k}
    assert len(asked) > 700
    assert sorted(asked - set(zh)) == []
    for k, v in en.items():
        for t in ([v] if isinstance(v, str) else list(v.values())):
            assert t and not CJK.search(t), (k, t)


def test_the_chinese_text_is_the_original():
    zh = _cat("zh-TW", "racepower")
    assert zh["calc"] == "計算" and zh["bt.tab"] == "準確度"
    assert zh["res.meth_trail_hr"] == "整場用你的越野心率配速模型（推估），功率只當參考"
    assert zh["fuel.c_tip_short"] == "不到 75 分鐘：漱口或少量碳水就有效。"
    assert zh["eff.can_hold"] == "這個強度你大約可以撐 <b>{x} ×</b> 目標時間（約 {t}）"
    assert zh["hk.alt_coffman"] == "Coffman 2020 固定心率負重：−3.8 %（換算）"
    assert zh["bt.intro.how"].startswith("每一場都只用那天以前、不含這一場的資料")
    assert zh["paren"] == "（{v}）" and zh["adv.sep"] == "、" and zh["grp.sep"] == "；"
    assert _cat("zh-TW", "plan")["range"] == "～"


def test_english_counts_take_the_right_word_and_own_terms():
    from backend.i18n import plural_words
    en = _cat("en", "racepower")
    assert plural_words(en["course.n_segs"], {"n": 1}).format(n=1) == "1 segment"
    assert plural_words(en["course.n_segs"], {"n": 12}).format(n=12) == "12 segments"
    assert plural_words(en["hk.days_norest"], {"n": 1}).format(n=1) == "1 day, without rests"
    assert plural_words(en["stop.bad"], {"n": 2}).format(n=2, list="x") == "2 rows need fixing: x (not counted)"
    assert plural_words(en["stop.bad"], {"n": 1}).format(n=1, list="x") == "1 row needs fixing: x (not counted)"
    text = " ".join(v if isinstance(v, str) else " ".join(v.values()) for v in en.values())
    for word in ("TSS", " IF ", "CTL", "ATL", "TSB", "mFTP", "FRC", "W′", "W'", "Stryd 功率"):
        assert word not in text, word
    # the new keys say "estimate", and anaerobic capacity in words
    assert en["in.wprime"] == "Anaerobic capacity J" and en["est.badge"] == "estimate"


# ---- the rendered pages -------------------------------------------------------------------------------------

def test_the_english_calculator_markup_has_no_chinese():
    from backend.i18n.pages import render
    html = render("racepower", "en")
    assert CJK.findall(_markup(html)) == []
    assert '<button data-tab="acc" data-i18n="racepower.bt.tab">Accuracy</button>' in html
    assert "Drop a .gpx / .fit file here, or" in html and 'placeholder="e.g. 1:55:00"' in html
    assert "One aid station per row" in html                           # the aid stations' ? tip


def test_the_chinese_calculator_is_unchanged():
    from backend.i18n.pages import render
    html = render("racepower", "zh-TW")
    assert '<button data-tab="calc" class="on" data-i18n="racepower.calc">計算</button>' in html
    assert '<span data-i18n="racepower.tt.a">目標</span><span class="bf" data-i18n="racepower.tt.moving">移動</span>' \
           '<span data-i18n="racepower.tt.b">時間 (h:mm:ss)</span>' in html
    assert 'placeholder="例 1:55:00"' in html and "每一列一個補給站，依 km 自動排序。" in html
    assert "Riegel 是什麼：撐得越久，能出的功率越低。" in html


def test_the_english_season_plan_markup_has_no_chinese():
    from backend.i18n.pages import render
    assert CJK.findall(_markup(render("plan", "en"))) == []
    assert _cat("en", "plan")["range"] == "–"
    # the timeline's auto-schedule note
    en = _cat("en", "plan")
    for k in ("tl.auto", "tl.auto_pref", "tl.manual", "tl.specific_src"):
        assert en[k] and not CJK.search(en[k]), k


# ---- functions with a local T use TR (a `const T` shadows the catalog's T in the whole function) -------------

def _body(name: str) -> str:
    i = PAGE.index(f"function {name}(")
    j = PAGE.index("\n}\n", i)
    return PAGE[i:j]


def test_functions_with_a_local_T_call_TR():
    for fn in ("otherCalcs", "renderBacktest"):
        body = _body(fn)
        assert re.search(r"const (?:[\w, =.|{}]*\b)?T = ", body), fn
        assert re.findall(r'(?<![\w.$])T\("', body) == [], fn
        assert 'TR("' in body


# ---- a saved result is shown only in the language it was computed in ----------------------------------------

def test_a_saved_result_is_shown_only_in_its_language():
    assert 'const LOCALE = (window.I18N && I18N.locale) || "zh-TW";' in PAGE
    assert "return { v: 1, locale: LOCALE, type," in _body("snapshot")       # saved with the inputs
    choose = PAGE[PAGE.index("async function chooseEvent("):PAGE.index("function fromCairn(")]
    assert 'if (saved.result?.summary && saved.result?.v1 && (saved.inputs.locale || "zh-TW") === LOCALE) {' in choose
    assert "schedule();" in choose                                            # else it is computed again
    # the saved weather (its labels) of the other language is fetched again
    assert 'if ((x.locale || "zh-TW") !== LOCALE) { WX = null; WX_KEY = null; }' in _body("restore")


# ---- the plan API's words -----------------------------------------------------------------------------------

def test_the_goal_labels_and_threshold_methods_follow_the_language():
    from backend.engine import planning as P
    today = dt.date(2026, 10, 1)
    plan = P.Plan(events=[P.Event(id="a", name="測試山徑 50K", date="2026-11-21", kind="race", priority="A",
                                  distance_km=50, climbing_m=2800, est_hours=7.5)],
                  thresholds=[P.Threshold(date="2026-09-29", aethr=150, aethr_method="test")])
    zh = P.goals(plan, today)["targets"]
    assert zh["distance_km"]["label"] == "距離" and zh["climb_per_km"]["label"] == "每公里爬升"
    with use_locale("en"):
        en = P.goals(plan, today)["targets"]
        assert {k: v["label"] for k, v in en.items() if v} == {
            "distance_km": "Distance", "climbing_m": "Climb", "climb_per_km": "Climb per km",
            "est_hours": "predicted time", "days": "Days"}
        assert en["distance_km"]["event"] == "測試山徑 50K"                    # the user's own name
        assert P.threshold_row(plan, "aethr", today)["label"] == "AeT test 2026-09-29"
    assert P.threshold_row(plan, "aethr", today)["label"] == "AeT 測試 2026-09-29"
    assert P.METHOD_LABEL["manual"] == "手動輸入"                              # the msgids stay Chinese


def test_the_weight_source_and_gpx_errors_are_translated():
    src = (Path(__file__).resolve().parents[1] / "api" / "plan.py").read_text("utf-8")
    assert '"這場賽事沒有 GPX")' not in src.replace('_("這場賽事沒有 GPX")', "")
    assert '_("對照資料夾")' in src and "ci.get(\"source\") == MFTP_SRC" in src
    assert "404 if EG.get(eid) is None else 400" in src                       # not by the message's words
    from backend.i18n import translate
    assert translate("這場賽事沒有 GPX", "en") == "This race has no GPX"
    assert translate("每公里爬升", "en") == "Climb per km" and translate("天數", "en") == "Days"
