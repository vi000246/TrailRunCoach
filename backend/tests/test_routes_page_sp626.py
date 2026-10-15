"""SP-626: the routes page tidied — segments listed under the routes they lie on
(every route, also when one segment is on two), the rest as 「其他路段」; order
starred → renamed → most done; favourites on the server (favorites.json, a user
file), kept through a rebuild / merge by the index's aliases."""
import json

import pytest

from backend.engine import routes as R
from backend.tests.test_route_progress import HILL, LOOP, OUT_BACK, _builder, activity, hill_elev

# the hill out-and-back, and a longer run doing 3 km of flat first and the same hill
LONG = [(-3000, 0)] + OUT_BACK + [(-3000, 0)]


def long_elev(s):
    return 100.0 if s <= 3000 else hill_elev(s - 3000) if s <= 3000 + 5200 else 100.0


@pytest.fixture
def api(tmp_path, monkeypatch):
    from backend.api import routes as API
    acts = [activity(OUT_BACK, elev_of=hill_elev, start="2025-01-01T08:00", file="h0"),
            activity(OUT_BACK, elev_of=hill_elev, start="2025-02-01T08:00", file="h1", noise=(4.0, 0.0), speed=3.2),
            activity(LONG, elev_of=long_elev, start="2025-03-01T08:00", file="x0"),
            activity(LONG, elev_of=long_elev, start="2025-04-01T08:00", file="x1", noise=(4.0, 0.0), speed=2.8),
            activity([(p[0] + 20000, p[1]) for p in LOOP], start="2025-05-01T08:00", file="l0"),
            activity([(p[0] + 20000, p[1]) for p in LOOP], start="2025-06-01T08:00", file="l1", noise=(5.0, 0.0))]
    store = R.RouteStore(tmp_path / "routes")
    bld = R.Builder(store)
    monkeypatch.setattr(API, "STORE", store)
    monkeypatch.setattr(API, "BUILDER", bld)
    monkeypatch.setattr(API, "_ensure_fresh", lambda: None)
    monkeypatch.setattr(API, "_file_to_idx", lambda: {})
    monkeypatch.setattr(API, "_phase_labels", lambda days: [None] * len(days))
    API._CACHE.update(mtime=None, view=None)
    wl, rd = _builder(tmp_path, acts)
    idx = bld.build(wl, rd)
    API.idx, API.bld, API.wl, API.rd = idx, bld, wl, rd
    yield API
    API._CACHE.update(mtime=None, view=None)


def _routes(API):
    return [r for r in API.list_routes(kind="route")["rows"]]


def test_climb_is_listed_under_both_routes_and_not_among_other_segments(api):
    routes = _routes(api)
    hill = next(r for r in routes if {e["file"] for e in api.detail(r["id"])["efforts"]} == {"h0", "h1"})
    longr = next(r for r in routes if {e["file"] for e in api.detail(r["id"])["efforts"]} >= {"x0", "x1"})
    climbs = [r for r in api.list_routes(kind="climb")["rows"]]
    assert len(climbs) == 1
    cid = climbs[0]["id"]
    for r in (hill, longr):
        d = api.detail(r["id"])
        ids = [s["id"] for s in d["segments"]]
        assert cid in ids, r["id"]
        # in order along the route; the climb row has this run's time and rank, and the best
        row = next(s for s in d["segments"] if s["id"] == cid)
        runs = {e["file"] for e in d["efforts"]}
        assert set(row["by_file"]) <= runs and row["by_file"]
        for f, x in row["by_file"].items():
            assert x["rank"] >= 1 and x["time_s"] >= row["best_s"]
        assert [s["pos"] for s in d["segments"]] == sorted(s["pos"] for s in d["segments"])
    seg = api.detail(cid)
    assert {r["id"] for r in seg["on_routes"]} == {hill["id"], longr["id"]}
    # the default list: routes, then the segments on no route; the climb only under its routes
    rows = api.list_routes()["rows"]
    assert cid not in {r["id"] for r in rows}
    sections = [r["section"] for r in rows]
    assert sections == sorted(sections, key=["fav", "route", "other"].index)
    assert all(r["kind"] == "route" for r in rows if r["section"] == "route")
    assert all(not r["on_routes"] for r in rows if r["section"] == "other")
    # the segment list still has every segment, with the routes it is on
    assert next(r for r in api.list_routes(kind="segment")["rows"] if r["id"] == cid)["on_routes"]


def test_segments_on_routes_needs_a_route_run_on_it():
    seg = {"id": "s1", "family": "foot", "lat": [24.0, 24.001], "lon": [121.0, 121.0], "efforts": [{"file": "other"}]}
    rte = {"id": "r1", "family": "foot", "lat": [24.0, 24.0005, 24.001], "lon": [121.0] * 3, "members": ["m"]}
    assert R.segments_on_routes([seg], [rte]) == {}
    seg["efforts"].append({"file": "m"})
    assert [s for s, _ in R.segments_on_routes([seg], [rte])["r1"]] == ["s1"]
    assert R.segments_on_routes([dict(seg, family="bike")], [rte]) == {}


def test_order_is_star_then_renamed_then_count(api):
    rows = _routes(api)
    tops = [r for r in rows if not r["depth"]]          # a sub-route stays right under its parent
    assert len(tops) >= 2
    last = tops[-1]
    assert rows[0]["id"] != last["id"]
    api.rename(last["id"], api.RenameBody(name="我的路線"))
    rows2 = _routes(api)
    assert rows2[0]["id"] == last["id"] and rows2[0]["renamed"]
    star = [r for r in rows2 if not r["depth"]][-1]
    api.favorite(star["id"], api.FavBody(on=True))
    rows3 = _routes(api)
    assert rows3[0]["id"] == star["id"] and rows3[0]["fav"] and rows3[0]["section"] == "fav"
    assert rows3[1]["id"] == last["id"]
    # the default list: the star on top, in its own section
    top = api.list_routes()["rows"][0]
    assert top["id"] == star["id"] and top["section"] == "fav"
    # ★ filter
    assert [r["id"] for r in api.list_routes(fav=True)["rows"]] == [star["id"]]
    api.favorite(star["id"], api.FavBody(on=False))
    assert api.list_routes(fav=True)["rows"] == []


def test_segments_can_be_starred_too_and_it_is_kept_on_the_server(api):
    cid = api.list_routes(kind="climb")["rows"][0]["id"]
    api.favorite(cid, api.FavBody(on=True))
    # another device = another request: read back from favorites.json
    assert cid in json.loads(api.STORE.favorites_path.read_text("utf-8"))
    api._CACHE.update(mtime=None, view=None)
    rows = api.list_routes()["rows"]
    assert rows[0]["id"] == cid and rows[0]["fav"]        # a starred on-route segment is on top too
    assert api.detail(cid)["fav"] is True
    hill = next(r for r in api.detail(cid)["on_routes"])
    assert next(s for s in api.detail(hill["id"])["segments"] if s["id"] == cid)["fav"] is True


def test_star_and_name_survive_a_merge_through_the_aliases(api):
    rid = _routes(api)[0]["id"]
    # an older build's route, merged into this one: the user starred / named the old id
    api.STORE.set_favorite("rOLD", True)
    api.STORE.set_name("rOLD", "老名字")
    idx = api.STORE.load_index()
    next(r for r in idx["routes"] if r["id"] == rid)["aliases"] = ["rOLD"]
    api.STORE.save_index(idx)
    api._CACHE.update(mtime=None, view=None)
    row = next(r for r in _routes(api) if r["id"] == rid)
    assert row["fav"] and row["name"] == "老名字"
    assert api.detail("rOLD")["fav"] is True
    # unstarring / clearing the name drops the old id's entry as well
    api.favorite(rid, api.FavBody(on=False))
    api.rename(rid, api.RenameBody(name=""))
    row = next(r for r in _routes(api) if r["id"] == rid)
    assert not row["fav"] and not row["renamed"]
    assert api.STORE.favorites() == {} and "rOLD" not in api.STORE.names()


def test_star_survives_a_full_rebuild(api):
    rid = _routes(api)[0]["id"]
    api.favorite(rid, api.FavBody(on=True))
    api.bld.build(api.wl, api.rd, full=True)
    api._CACHE.update(mtime=None, view=None)
    assert next(r for r in _routes(api) if r["id"] == rid)["fav"]


def test_favorites_file_is_registered_as_user_data():
    from backend import data_registry as DR
    f = DR.classify("routes/favorites.json")
    assert f.cls == DR.USER and f.backup == DR.ALWAYS


def test_page_in_both_languages():
    from backend.i18n.pages import render
    zh, en = render("routes", "zh-TW"), render("routes", "en")
    assert "★ 最愛" in zh and "其他路段" in zh
    assert "★ Favourites" in en and "Route progress" in en
    assert "prompt(" not in zh
