"""Translation coverage (docs/plans/i18n.plan.md §3.2).

1. Ratchet: backend/i18n/baseline.json has, per file, how many Chinese UI
   strings are not wrapped in _() / t() / data-i18n yet; a file may only go
   down. While baseline "enforce" is false (until the pages are translated)
   a file over its baseline is only REPORTED (a warning), not failed.
2. Finished namespaces / modules (baseline "complete"): every key has en.
3. Placeholders equal in zh and en, no Chinese in en, the glossary's terms.
4. views: every dashboard / chart has a unique id; the sidecar points only at
   ids that exist; wko5_fixes.json addresses charts by id.

Reads only repo files (no WKO5 folder, no ~/.wko5coach)."""
import json
import warnings

import pytest

from backend.scripts import i18n_extract as X


@pytest.fixture(scope="module")
def scans():
    return X.scan_repo()


def test_ratchet_new_untranslated_ui_strings(scans):
    b = X.load_baseline()
    over = X.over_baseline(scans, b)
    if not over:
        return

    def msg(rows):
        return ("new Chinese UI strings not wrapped in _() / t() / data-i18n (or update the baseline with "
                "`python -m backend.scripts.i18n_extract --update-baseline` once they are):\n  "
                + "\n  ".join(f"{p}: {now} > {allowed}  e.g. {scans[p].unwrapped[-1][1][:50]!r}" for p, now, allowed in rows))
    hard = [r for r in over if X.enforced(r[0], b)]
    soft = [r for r in over if not X.enforced(r[0], b)]
    if soft:
        warnings.warn("[report only] " + msg(soft))
    if hard:                           # the finished pages' files (baseline enforce_files)
        pytest.fail(msg(hard), pytrace=False)


def test_finished_pages_are_enforced():
    """P1 overview, P2 race power: their files fail the ratchet (not just report)."""
    b = X.load_baseline()
    for p in ("backend/static/overview.html", "backend/static/racepower.html", "backend/static/share.html",
              "backend/engine/status.py", "backend/engine/racepower/planner.py"):
        assert X.enforced(p, b), p
    assert not X.enforced("backend/static/schedule.html", b)
    assert {"overview", "autoplan", "suggestions", "racepower", "share"} <= set(b["complete"])


def test_scanner_finds_and_skips_the_right_things():
    py = X.scan_python(X.ROOT / "backend" / "x.py", '"""模組說明"""\n'
                       "from backend.i18n import _\n"
                       "def f(n):\n"
                       '    """函式說明"""\n'
                       '    a = _("包好了 {n}", n=n)\n'
                       '    b = f"沒包 {n} 次"\n'
                       '    c = "沒包"\n'
                       "    d = 'ascii only'\n"
                       '    return _(f"錯 {n}")\n')
    assert [m for _l, m in py.msgids] == ["包好了 {n}"]
    assert [s for _l, s in py.unwrapped] == ["沒包 {…} 次", "沒包"]
    assert py.problems and "f-string" in py.problems[0]
    js = X.scan_js_source("a.js", '// 註解\nconst r = /週[一二]/g; /* 也是註解 */\n'
                                 'const s = "中文", u = `樣板 ${x ? "內層" : `巢狀`} 尾`, e = t("k.key");\n'
                                 "const half = a / 2, q = '單引號';\n")
    assert [s for _l, s in js.unwrapped] == ["中文", "內層", "巢狀", "樣板 ${…} 尾", "單引號"]
    html = X.scan_html(X.ROOT / "backend" / "x.html",
                       '<p data-i18n="a.b">已標記</p><p>沒標記</p><input placeholder="提示" title="ok">'
                       '<input data-i18n-attr="placeholder:a.c" placeholder="已標">'
                       '<script>const z = "腳本";</script><style>.a::after{content:"樣式"}</style>')
    assert sorted(s for _l, s in html.unwrapped) == sorted(["沒標記", "提示", "腳本"])


def test_complete_namespaces_have_english():
    b = X.load_baseline()
    cats = X.frontend_catalogs()
    en_backend = X.load_json(X.LOCALES / "en.json", {})
    scans = None
    for item in b["complete"]:
        if item.endswith((".py", ".html", ".js")):
            scans = scans or X.scan_repo()
            fs = scans[item]
            assert not fs.unwrapped, f"{item}: {len(fs.unwrapped)} unwrapped, e.g. {fs.unwrapped[:3]}"
            missing = [m for _l, m in fs.msgids if not en_backend.get(m)]
            assert not missing, f"{item}: no en for {missing[:5]}"
        else:
            zh = cats["zh-TW"][item]
            en = cats["en"].get(item, {})
            missing = [k for k in zh if not en.get(k)]
            assert not missing, f"{item}: no en for {missing}"


def test_catalogs_placeholders_language_and_glossary(scans):
    probs = X.catalog_problems(scans)
    assert probs == {"placeholders": [], "cjk_in_en": [], "glossary": []}


def test_frontend_catalogs_have_no_keys_missing_from_zh_tw():
    for ns, cov in X.frontend_coverage().items():
        assert cov["en_extra"] == [], ns


def test_glossary_has_the_owner_decisions():
    terms = {z: t for t in X.glossary_terms() for z in t["zh"]}
    assert terms["過度疲勞"]["en"] == "High Risk" and terms["有效訓練"]["en"] == "Optimal"
    assert terms["持平"]["en"] == "Grey Zone" and terms["比賽狀態"]["en"] == "Fresh"
    assert terms["休息過久"]["en"] == "Transition"
    assert terms["百岳"]["en"] == "Taiwan's 100 Peaks (Baiyue)"
    assert X.glossary_violations("過度疲勞", "Overreaching") and not X.glossary_violations("過度疲勞", "high risk")


def test_views_have_unique_ids_and_the_sidecar_matches():
    cov = X.views_coverage()
    assert set(cov) >= {"training", "periodization", "workout"}
    for stem, c in cov.items():
        assert c["missing_ids"] == 0, stem
        assert c["duplicate_chart_ids"] == [] and c["duplicate_dashboard_ids"] == [], stem
        assert c["unknown_sidecar_ids"] == [], stem
    sc = json.loads((X.VIEWS / "i18n" / "en.json").read_text("utf-8"))
    assert set(sc) - {"_about"} <= set(cov)


def test_fixes_address_charts_by_id():
    from backend.engine.wko5expr.chartfixes import load_fixes
    from backend.engine.wko5expr.viewids import valid_id
    fixes = load_fixes()
    assert fixes and all(valid_id(f.get("chart_id")) for f in fixes)
    assert all(valid_id(f["dashboard_id"]) for f in fixes if "dashboard" in f)
