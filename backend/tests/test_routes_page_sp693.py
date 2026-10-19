"""
SP-693: one 路線 page with two tabs — 我跑過的 (static/routes.html, the repeated segments and routes found
in the activities, SP-626) and 上傳的 (static/routelib.html, the uploaded GPX routes, SP-640) — instead of
課表 › 路線 and 更多 › 路線進步 side by side:
  * 課表 has no 路線 tab any more; its old URL redirects to 上傳的 (a #g<id> bookmark keeps its fragment,
    a #m<id> one is sent on to 我跑過的 by the page);
  * 「我的路段」 are listed once, on 我跑過的 — not again under 上傳的;
  * every route on either tab can be planned (「用這條排課」: POST /steps/from-route, src gpx or seg), and
    the 課表 editor's 「▲ 路線」 still lists both;
  * a whole route's default 地形難度 (SP-731) moved with 「我的路段」 to 我跑過的.
"""
import json
from pathlib import Path

from backend.api import routes as RA
from backend.engine.routes import RouteStore
from backend.i18n import use_locale
from backend.i18n.pages import render_page
from backend.tests.test_plan_store import API
from backend.tests.test_route_library_sp640 import LEnv

STATIC = Path(__file__).resolve().parents[1] / "static"
RAN = "/api/v1/routes/page"
UPLOADED = "/api/v1/routes/uploaded/page"


def _page(name: str) -> str:
    return render_page(name).body.decode()


def test_the_old_plan_tab_redirects_to_the_uploaded_tab(monkeypatch):
    with LEnv(monkeypatch) as e:
        r = e.c.get(f"{API}/routes/page", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == UPLOADED
        r = e.c.get(f"{API}/routes/page?x=1", follow_redirects=False)
        assert r.headers["location"] == f"{UPLOADED}?x=1"
    src = (STATIC / "routelib.html").read_text("utf-8")
    # a bookmark to one of 「我的路段」 (#m<id>) goes on to 我跑過的
    assert "location.replace(`/api/v1/routes/page#${location.hash.slice(2)}`)" in src


def _nav(html: str) -> str:
    return html[html.index('<nav class="rtabs"'):html.index("</nav>", html.index('<nav class="rtabs"'))]


def test_one_routes_page_with_two_tabs():
    ran, up = _page("routes"), _page("routelib")
    for html, cur in ((ran, RAN), (up, UPLOADED)):
        nav = _nav(html)
        assert f'href="{cur}" aria-current="page"' in nav
        assert nav.count('aria-current="page"') == 1 and nav.count("<a ") == 2
        assert f'href="{RAN}"' in html and f'href="{UPLOADED}"' in html
        assert 'data-page="routes"' in html                # the side bar marks 路線 on both tabs
    assert ">我跑過的<" in ran and ">上傳的<" in ran and ">我跑過的<" in up and ">上傳的<" in up
    with use_locale("en"):
        en = _page("routelib")
    assert ">Routes</title>" in en and ">Run before<" in en and ">Uploaded<" in en


def test_the_plan_pages_have_no_routes_tab():
    for page in ("schedule", "compliance", "templates"):
        html = _page(page)
        assert html.count('class="mcard"') == 3, page
        assert "/overview/plan/routes/page" not in html, page
    tpl = (STATIC / "templates.html").read_text("utf-8")
    assert 'const ROUTES = "/api/v1/routes/uploaded/page"' in tpl      # a template's moved GPX opens on 上傳的
    we = (STATIC / "workout_editor.js").read_text("utf-8")
    assert 'const page = "/api/v1/routes/uploaded/page"' in we
    for loc in ("zh-TW", "en"):
        for ns in ("schedule", "compliance", "templates"):
            cat = json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))
            assert "tabs.routes" not in cat and "tabs.routes_sub" not in cat, (loc, ns)


def test_my_segments_are_listed_once():
    up = (STATIC / "routelib.html").read_text("utf-8")
    assert 'id="rl-mine"' not in up and "/routes/mine" not in up and "我的路段" not in up
    ran = (STATIC / "routes.html").read_text("utf-8")
    assert 'id="list"' in ran                              # 我跑過的: the list of the segments and routes
    for loc in ("zh-TW", "en"):
        rl = json.loads((STATIC / "i18n" / loc / "routelib.json").read_text("utf-8"))
        assert not any(k.startswith(("sec_mine", "mine_", "none_mine", "tdiff.")) for k in rl), loc


def test_every_route_can_be_planned_from_either_tab():
    ran = (STATIC / "routes.html").read_text("utf-8")
    assert 'id="gen-btn"' in ran and "/steps/from-route" in ran and 'route: { src: "seg", id }' in ran
    assert "route: `seg:${S.detail.id}`" in ran and "/schedule/page?" in ran
    up = (STATIC / "routelib.html").read_text("utf-8")
    assert 'id="rl-gen-btn"' in up and "/steps/from-route" in up
    for loc in ("zh-TW", "en"):
        r = json.loads((STATIC / "i18n" / loc / "routes.json").read_text("utf-8"))
        rl = json.loads((STATIC / "i18n" / loc / "routelib.json").read_text("utf-8"))
        assert r["plan.btn"] == rl["gen_btn"], loc                # the same words on both tabs
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "routes.json").read_text("utf-8"))
    assert zh["plan.btn"] == "用這條排課"
    # the 課表 editor's 「▲ 路線」 still lists both: the uploaded routes and the ones run before
    we = (STATIC / "workout_editor.js").read_text("utf-8")
    assert "/routes/library`" in we and "/routes/mine`" in we


def _route_view():
    item = {"id": "r1", "kind": "route", "kind_zh": "路線", "family": "run", "direction": "flat", "time_key": "elapsed_s",
            "auto_name": "路線 壽山", "length_m": 5000.0, "gain_m": 300.0, "efforts": [], "partials": [],
            "segments": [], "on_routes": [], "sub_routes": [], "parent": None, "n_efforts": 2, "n_activities": 2}
    return {"items": {"r1": item, "c1": {**item, "id": "c1", "kind": "climb", "direction": "up"}},
            "aliases": {"old": "r1"}, "weather": None}


def test_the_detail_carries_the_route_default_and_can_be_planned(tmp_path, monkeypatch):
    view = _route_view()
    st = RouteStore(tmp_path)
    st.set_terrain("old", "technical")                     # stored under an id merged into r1
    monkeypatch.setattr(RA, "STORE", st)
    monkeypatch.setattr(RA, "_index", lambda: view)
    monkeypatch.setattr(RA, "_names", lambda v=None: {})
    monkeypatch.setattr(RA, "_favs", lambda v=None: {})
    monkeypatch.setattr(RA, "_file_to_idx", lambda: {})
    monkeypatch.setattr(RA, "_status", lambda: {})
    d = RA.detail("r1")
    assert d["terrain"] == "technical" and d["plannable"] is True
    c = RA.detail("c1")
    assert "terrain" not in c and c["plannable"] is True
    ran = (STATIC / "routes.html").read_text("utf-8")
    assert 'id="rt-tdiff"' in ran and "/terrain`" in ran     # set on 我跑過的 now
