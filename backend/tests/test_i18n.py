"""i18n P0 infrastructure (docs/plans/i18n.plan.md): language detection, _(),
fmt, render_page, the shell catalogs, view ids, the views sidecar. Synthetic
only: no WKO5 folder, no ~/.wko5coach."""
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import i18n
from backend.i18n import _, N_, UserError, fmt, use_locale
from backend.i18n import pages as P
from backend import request_context as RC


# ---- detection ---------------------------------------------------------------

@pytest.mark.parametrize("tag,loc", [
    ("zh", "zh-TW"), ("zh-TW", "zh-TW"), ("zh-Hant-TW", "zh-TW"), ("zh-HK", "zh-TW"), ("zh_MO", "zh-TW"),
    ("zh-CN", "zh-TW"), ("zh-Hans", "zh-TW"), ("en", "en"), ("en-GB", "en"), ("EN-us", "en"),
    ("fr", None), ("", None), (None, None), ("*", None),
])
def test_normalize(tag, loc):
    assert i18n.normalize(tag) == loc


def test_accept_language_by_q_and_unknown_gets_en():
    assert RC.from_accept_language("fr;q=0.9, en-GB;q=0.8, zh;q=0.5") == "en"
    assert RC.from_accept_language("en;q=0.3, zh-TW") == "zh-TW"
    assert RC.from_accept_language("fr, de;q=0.5") == "en"            # only unsupported -> en (owner decision)
    assert RC.from_accept_language("zh;q=0, en;q=0") is None             # q=0 = "not this": nothing asked for
    assert RC.from_accept_language("") is None
    assert RC.from_accept_language("*") is None


def test_resolve_order(monkeypatch):
    monkeypatch.delenv("TRC_DEFAULT_LOCALE", raising=False)
    assert RC.resolve("en", "zh-TW", "zh-TW", "zh") == ("en", "query")
    assert RC.resolve("xx", None, None, None) == ("en", "query")       # an explicit unknown language -> en
    assert RC.resolve(None, "en", "zh-TW", "zh") == ("en", "cookie")
    assert RC.resolve(None, "garbage", "en", "zh") == ("en", "account")
    assert RC.resolve(None, None, None, "en-US,en;q=0.9") == ("en", "header")
    assert RC.resolve(None, None, None, None) == ("zh-TW", "default")
    monkeypatch.setenv("TRC_DEFAULT_LOCALE", "en")                       # the demo instance
    assert RC.resolve(None, None, None, None) == ("en", "default")
    assert RC.resolve(None, None, None, "zh-TW,zh;q=0.9") == ("zh-TW", "header")


def _mini_app():
    app = FastAPI()
    app.add_middleware(RC.RequestContextMiddleware)

    @app.get("/sync")
    def sync_ep():                                   # runs in the threadpool
        return {"loc": i18n.current_locale(), "msg": _("找不到備份檔")}

    @app.get("/async")
    async def async_ep():
        return {"loc": i18n.current_locale()}

    @app.get("/err")
    def err():
        raise UserError("找不到備份檔", code="NO_BACKUP", status=404, name="x")

    from backend.main import _user_error
    app.add_exception_handler(UserError, _user_error)
    return app


def test_middleware_sets_the_locale_for_sync_and_async_endpoints():
    c = TestClient(_mini_app())
    r = c.get("/sync")
    assert r.json() == {"loc": "zh-TW", "msg": "找不到備份檔"}
    assert r.headers["content-language"] == "zh-TW"
    assert "Accept-Language" in r.headers["vary"] and "Cookie" in r.headers["vary"]
    assert "set-cookie" not in r.headers
    r = c.get("/sync", headers={"Accept-Language": "en-GB,en;q=0.8"})
    assert r.json() == {"loc": "en", "msg": "Backup file not found"}
    assert c.get("/async", headers={"Accept-Language": "fr"}).json()["loc"] == "en"
    assert c.get("/async", headers={"Cookie": "lang=en", "Accept-Language": "zh-TW"}).json()["loc"] == "en"
    # the context does not leak into the next request
    assert TestClient(_mini_app()).get("/async").json()["loc"] == "zh-TW"


def test_query_lang_wins_and_is_remembered():
    c = TestClient(_mini_app())
    r = c.get("/async?lang=en", headers={"Cookie": "lang=zh-TW"})
    assert r.json()["loc"] == "en"
    sc = r.headers["set-cookie"]
    assert sc.startswith("lang=en;") and "SameSite=Lax" in sc and "Max-Age=31536000" in sc and "Path=/" in sc


def test_user_error_answers_code_and_translated_message():
    c = TestClient(_mini_app())
    r = c.get("/err")
    assert r.status_code == 404
    assert r.json() == {"detail": {"code": "NO_BACKUP", "message": "找不到備份檔", "params": {"name": "x"}}}
    assert c.get("/err?lang=en").json()["detail"]["message"] == "Backup file not found"


# ---- _() ------------------------------------------------------------------------

@pytest.fixture
def catalog(tmp_path, monkeypatch):
    d = tmp_path / "locales"
    d.mkdir()
    (d / "en.json").write_text(json.dumps({
        "每週 +{ramp:.1f}": "+{ramp:.1f} a week",
        "{n} 堂課": {"one": "{n} session", "other": "{n} sessions"},
        "還沒翻": None,
    }, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(i18n, "LOCALES_DIR", d)
    i18n._CATALOGS.clear()
    yield d
    i18n._CATALOGS.clear()


def test_gettext_formats_and_falls_back(catalog):
    assert _("每週 +{ramp:.1f}", ramp=2.345) == "每週 +2.3"            # zh-TW: the msgid itself
    with use_locale("en"):
        assert i18n.current_locale() == "en"
        assert _("每週 +{ramp:.1f}", ramp=2.345) == "+2.3 a week"     # the format spec survives
        assert _("{n} 堂課", n=1) == "1 session"
        assert _("{n} 堂課", n=3) == "3 sessions"
        assert _("還沒翻") == "還沒翻"                                   # null -> the original
        assert _("沒有這句 {x}", x=1) == "沒有這句 1"
    with use_locale("en", debug=True):
        assert _("還沒翻") == "⟦還沒翻⟧"
    assert i18n.current_locale() == "zh-TW"
    assert N_("路跑") == "路跑"
    assert _("{a}", b=1) == "{a}"                                       # a wrong param never raises


def test_catalog_reloads_when_the_file_changes(catalog):
    import os
    with use_locale("en"):
        assert _("還沒翻") == "還沒翻"
        p = catalog / "en.json"
        data = json.loads(p.read_text("utf-8"))
        data["還沒翻"] = "Not yet"
        p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        st = p.stat()
        os.utime(p, (st.st_atime, st.st_mtime + 5))
        assert _("還沒翻") == "Not yet"


def test_background_thread_defaults_to_zh_tw():
    import threading
    got = []
    with use_locale("en"):
        t = threading.Thread(target=lambda: got.append(i18n.current_locale()))
        t.start(); t.join()
    assert got == ["zh-TW"]                     # a new thread starts from the default; use use_locale() there


# ---- fmt ------------------------------------------------------------------------

def test_fmt_zh_tw_matches_the_old_strings_and_en():
    import datetime as dt
    d = dt.date(2026, 10, 1)                    # a Thursday
    assert fmt.weekday(d) == "週四" and fmt.weekday("2026-10-01", "narrow") == "四"
    assert fmt.date("2026-10-01", "mdw") == "10/1（週四）"
    assert "".join(fmt.weekdays("narrow")) == "一二三四五六日"
    assert fmt.dur(12000) == "3 小時 20 分" and fmt.dur(300) == "5 分" and fmt.dur(12005, "hms") == "3:20:05"
    with use_locale("en"):
        assert fmt.weekday(d) == "Thu" and fmt.date(d, "mdw") == "Thu 10/1"
        assert fmt.dur(12000) == "3h 20m"
    assert fmt.num(12345.678, 1) == "12,345.7" and fmt.pct(0.153) == "15%"
    assert fmt.pace(365) == "6:05 /km" and fmt.dist(42.195) == "42.2 km" and fmt.elev(1250.4) == "1,250 m"
    assert fmt.num(None) == "" and fmt.pace(None) == ""


def test_engine_weekday_helpers_unchanged_in_zh_tw():
    from backend.engine import adapt, b2b, suggestions
    assert adapt.wd("2026-10-04") == "週日"
    assert b2b.wd("2026-10-03") == "週六"
    assert suggestions._md("2026-10-02") == "10/2（週五）"
    with use_locale("en"):
        assert adapt.wd("2026-10-04") == "Sun"


# ---- render_page ------------------------------------------------------------------

def test_apply_catalog_replaces_marked_text_and_attributes_only():
    src = ('<p data-i18n="x.title">原文</p><input data-i18n-attr="placeholder:x.ph;title:x.tt" placeholder="舊">'
           '<b data-i18n="x.missing">留著</b><script>const s = `<i data-i18n="x.title">原文</i>`;</script>')
    out = P.apply_catalog(src, {"x.title": "Title & <more>", "x.ph": "Type \"here\"", "x.tt": "Tip"})
    assert '<p data-i18n="x.title">Title &amp; &lt;more&gt;</p>' in out
    assert 'placeholder="Type &quot;here&quot;"' in out and 'title="Tip"' in out
    assert '<b data-i18n="x.missing">留著</b>' in out
    assert '<i data-i18n="x.title">原文</i>' in out                       # scripts are left to t()


def test_page_catalog_falls_back_to_zh_tw(tmp_path, monkeypatch):
    for loc, data in {"zh-TW": {"a": "甲", "b": "乙 {n}"}, "en": {"a": "A", "b": None}}.items():
        (tmp_path / loc).mkdir()
        (tmp_path / loc / "t.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(P, "CATALOG_DIR", tmp_path)
    assert P.page_catalog(["t"], "en") == {"t.a": "A", "t.b": "乙 {n}"}
    assert P.page_catalog(["t"], "en", debug=True)["t.b"] == "⟦乙 {n}⟧"
    assert P.page_catalog(["t"], "zh-TW") == {"t.a": "甲", "t.b": "乙 {n}"}


def _boot(html: str) -> dict:
    m = re.search(r"window\.__I18N__ = (\{.*?\});</script>", html)
    return json.loads(m.group(1))


def test_pages_render_in_the_request_language():
    from backend.main import app
    c = TestClient(app)
    r = c.get("/api/v1/overview/page")
    assert r.status_code == 200 and r.headers["content-language"] == "zh-TW"
    assert '<html lang="zh-TW">' in r.text
    b = _boot(r.text)
    assert b["locale"] == "zh-TW" and b["catalog"]["shell.page.home.name"] == "總覽"
    assert r.text.index("window.__I18N__") < r.text.index("/api/v1/static/i18n/i18n.js") < r.text.index("shell.js")
    r = c.get("/api/v1/overview/page?lang=en")
    assert '<html lang="en">' in r.text and r.headers["set-cookie"].startswith("lang=en")
    assert _boot(r.text)["catalog"]["shell.page.home.name"] == "Overview"
    # a page opened by its file name goes through the same rendering
    r = c.get("/api/v1/static/compare.html", headers={"Accept-Language": "en"})
    assert r.status_code == 200 and '<html lang="en">' in r.text
    assert c.get("/api/v1/static/nope.html").status_code == 404
    assert c.get("/api/v1/static/i18n/i18n.js").status_code == 200


def test_every_page_route_uses_render_page():
    from pathlib import Path
    api = Path(__file__).resolve().parents[1] / "api"
    for p in api.glob("*.py"):
        assert not re.search(r'FileResponse\([^)]*\.html', p.read_text("utf-8")), p.name


# ---- the shell ----------------------------------------------------------------------

def test_shell_catalogs_cover_every_page_and_the_switch():
    from backend.i18n.pages import CATALOG_DIR
    zh = json.loads((CATALOG_DIR / "zh-TW" / "shell.json").read_text("utf-8"))
    en = json.loads((CATALOG_DIR / "en" / "shell.json").read_text("utf-8"))
    assert set(zh) == set(en)
    js = (P.STATIC / "shell.js").read_text("utf-8")
    ids = re.findall(r'\{ id: "(\w+)"', js)
    assert len(ids) == 9                     # 成就 became a tab of 活動列表
    for i in ids:
        for f in ("name", "short", "purpose"):
            assert f"page.{i}.{f}" in zh
    assert "an-lang" in js and "setLocale" in js
    i18n_js = (P.STATIC / "i18n" / "i18n.js").read_text("utf-8")
    assert "lang=${loc}; Max-Age=${365 * 24 * 3600}; Path=/; SameSite=Lax" in i18n_js


# ---- view ids, fixes, sidecar ---------------------------------------------------------

def test_slug_and_ensure_ids():
    from backend.engine.wko5expr.viewids import ensure_ids, slug
    assert slug("Palladino Run Summary Report") == "palladino-run-summary-report"
    s = slug("ATL CTL Ratio 訓練負荷比 (only run)")
    assert re.fullmatch(r"atl-ctl-ratio-only-run-[0-9a-f]{6}", s) and s == slug("ATL CTL Ratio 訓練負荷比 (only run)")
    assert re.fullmatch(r"c-[0-9a-f]{6}", slug("每週爬升"))
    v = {"dashboards": [{"title": "A", "charts": [{"title": "X"}, {"title": "X"}, {"id": "keep", "title": "Y"}]},
                        {"title": "A", "charts": [{"title": "X"}]}]}
    ensure_ids(v)
    assert [d["id"] for d in v["dashboards"]] == ["a", "a-2"]
    assert [c["id"] for d in v["dashboards"] for c in d["charts"]] == ["x", "x-2", "keep", "x-3"]


def test_fixes_match_by_id_before_title():
    from backend.engine.wko5expr.chartfixes import apply_fixes
    views = {"V": {"view": "V", "dashboards": [{"id": "d1", "title": "D", "charts": [
        {"id": "c1", "title": "同名", "axes": [{"id": "NONE", "min": 0}], "series": []},
        {"id": "c2", "title": "同名", "axes": [{"id": "NONE", "min": 0}], "series": []}]}]}}
    out = apply_fixes(views, [{"view": "V", "chart_id": "c2", "chart": "同名", "axis": "NONE",
                               "set": {"min": None}, "note": "n"}])
    a, b = out["V"]["dashboards"][0]["charts"]
    assert a["axes"][0]["min"] == 0 and "fixes" not in a
    assert b["axes"][0]["min"] is None and b["fixes"] == ["n"]
    out = apply_fixes(views, [{"view": "V", "dashboard_id": "nope", "chart_id": "c2", "axis": "NONE",
                               "set": {"min": None}}])
    assert out["V"]["dashboards"][0]["charts"][1]["axes"][0]["min"] == 0


def test_parse_view_keeps_and_fills_ids():
    from backend.engine.wko5expr.customviews import parse_view
    v = parse_view({"name": "N", "dashboards": [{"id": "dd", "title": "儀表", "charts": [
        {"id": "cc", "title": "圖", "series": [{"name": "s", "expression": "tss"}]},
        {"title": "Weekly TSS", "series": [{"name": "s", "expression": "tss"}]}]}]})
    d = v["dashboards"][0]
    assert d["id"] == "dd" and [c["id"] for c in d["charts"]] == ["cc", "weekly-tss"]


def test_sidecar_translates_bundled_views_by_id(tmp_path, monkeypatch):
    from backend.engine.wko5expr import viewi18n as VI
    from backend.engine.wko5expr.customviews import REPO_VIEWS
    (tmp_path / "en.json").write_text(json.dumps({"training": {
        "name": "My training",
        "dashboards": {"load": {"title": "Load", "description": None}},
        "charts": {"pmc": {"title": "PMC", "series": {"CTL 體能": "CTL Fitness"}, "variants": {"tss": "TSS!"}}},
    }}, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(VI, "SIDECAR_DIR", tmp_path)
    VI._CACHE.clear()
    chart = {"id": "pmc", "title": "原標題", "description": "說明", "zoned": {"line": "CTL 體能"},
             "series": [{"name": "CTL 體能"}, {"name": "ATL 疲勞"}],
             "variants": [{"key": "tss", "label": "TSS", "series": [{"name": "CTL 體能"}]}]}
    bundled = {"view": "我的訓練", "source": "custom", "path": str(REPO_VIEWS / "training.json"),
               "dashboards": [{"id": "load", "title": "負荷", "description": "原說明", "charts": [chart]}]}
    mine = {**bundled, "path": str(tmp_path / "training.json")}          # a user's own file: not translated
    views = {"我的訓練": bundled, "mine": mine}
    assert VI.translate_views(views, "zh-TW") is views
    out = VI.translate_views(views, "en")
    v = out["我的訓練"]
    assert v["view"] == "我的訓練" and v["label"] == "My training"       # the name stays the URL key
    d = v["dashboards"][0]
    assert d["title"] == "Load" and d["description"] == "原說明"
    c = d["charts"][0]
    assert c["title"] == "PMC" and c["description"] == "說明"
    assert [s["name"] for s in c["series"]] == ["CTL Fitness", "ATL 疲勞"] and c["zoned"]["line"] == "CTL Fitness"
    assert c["variants"][0]["label"] == "TSS!" and c["variants"][0]["series"][0]["name"] == "CTL Fitness"
    assert bundled["dashboards"][0]["charts"][0]["title"] == "原標題"     # the input is not modified
    assert out["mine"] is mine
    VI._CACHE.clear()


def test_render_cache_key_has_the_locale_only_outside_zh_tw():
    from backend.engine.wko5expr.render_cache import chart_key
    k = chart_key({"title": "x"}, {"d": 1}, "fp")
    with use_locale("zh-TW"):
        assert chart_key({"title": "x"}, {"d": 1}, "fp") == k
    with use_locale("en"):
        assert chart_key({"title": "x"}, {"d": 1}, "fp") != k
