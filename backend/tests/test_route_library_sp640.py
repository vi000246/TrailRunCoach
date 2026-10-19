"""
SP-640: the training routes page (課表 › 路線, static/routelib.html; engine/route_library.py,
api/route_library.py), apart from the templates —
  * the uploaded GPX routes: upload, rename, delete, download, the elevation profile and the 坡段表
    (SP-639's climbs and grade segments, the same thresholds);
  * 「我的路段」 (engine/routes.py: climbs and routes found in the activities) on the same page, with
    a profile and climbs taken from the reference effort's track;
  * a template keeps only its structure: the GPX a template carried (SP-36) moves to the library,
    nothing lost (the template's own copy stays);
  * the route is picked when the structure is put on a day (POST /steps/route): a hill repeat's climb
    ends at the top with the lap button, named 「爬 120 m · 約 6′」; the watch gets the steps and
    targets only, never the route;
  * every person sees only their own routes (per-tenant DB and folder).
"""
import copy
import json
import math
import re

import pytest

from backend import tenancy
from backend.api import route_library as RLA
from backend.api import routes as RA
from backend.engine import route_library as RL
from backend.engine import routes as R
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.tests.test_coros_workouts import make_db, run
from backend.tests.test_plan_store import API, Env
from backend.tests.test_user_templates import FREE_UPHILL, UAPI, _pin, gpx_bytes, st  # noqa: F401 (_pin: autouse)

LAPI = f"{API}/routes/library"
LABEL = re.compile(r"^爬 (\d+) m · 約 (\d+)′$")


class LEnv(Env):
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.c.app.include_router(RLA.router)


def upload(e, data=None, name="slope.gpx"):
    r = e.c.post(LAPI, files={"file": (name, data or gpx_bytes(), "application/gpx+xml")})
    assert r.status_code == 200, r.text
    return r.json()


def flat_then_hill():
    """1 km flat, then 1 km up 100 m (10 %), then 1 km flat."""
    km = [0.0, 1.0] + [1.0 + i / 10 for i in range(1, 11)] + [3.0]
    z = [0.0, 0.0] + [10.0 * i for i in range(1, 11)] + [100.0]
    return km, z


# ---------------------------------------------------------------------------
# the uploaded routes
# ---------------------------------------------------------------------------

def test_upload_rename_profile_table_download_delete(monkeypatch):
    with LEnv(monkeypatch) as e:
        assert e.c.get(LAPI).json()["routes"] == []
        r = upload(e)
        assert r["filename"] == "slope.gpx" and r["name"] == "坡"          # the GPX's own name
        assert math.isclose(r["km"], 3.0, rel_tol=0.05) and r["gain_m"] > 200 and r["n_climbs"] == 1
        assert RL.gpx_path(r["id"]).exists()
        assert e.c.get(f"{LAPI}/{r['id']}/file").content == gpx_bytes()
        lst = e.c.get(LAPI).json()
        assert [x["id"] for x in lst["routes"]] == [r["id"]] and lst["limits"]["name"] == RL.NAME_MAX
        # one route: its profile and the 坡段表 (SP-639's climbs + grade segments, the same thresholds)
        d = e.c.get(f"{LAPI}/{r['id']}").json()
        assert len(d["profile"]["km"]) == len(d["profile"]["z"]) >= 2
        assert d["profile"]["km"][-1] == pytest.approx(r["km"], abs=0.05)
        tb = d["table"]
        assert tb["rule"] == UT.climb_rule() and len(tb["climbs"]) == 1 and tb["segments"]
        c = tb["climbs"][0]
        assert c["gain_m"] == pytest.approx(300, abs=40) and LABEL.match(c["label"])
        assert {lv["key"] for lv in tb["levels"]} == {"easy"} and c["est"]["easy"]["t_s"] > 0
        # rename (trimmed; empty / too long refused)
        assert e.c.patch(f"{LAPI}/{r['id']}", json={"name": "  壽山 "}).json()["name"] == "壽山"
        assert e.c.patch(f"{LAPI}/{r['id']}", json={"name": " "}).status_code == 400
        assert e.c.patch(f"{LAPI}/{r['id']}", json={"name": "x" * (RL.NAME_MAX + 1)}).status_code == 400
        # a named upload
        r2 = e.c.post(LAPI, data={"name": "七星山"}, files={"file": ("q.gpx", gpx_bytes(km=5.0), "application/gpx+xml")}).json()
        assert r2["name"] == "七星山"
        assert [x["name"] for x in e.c.get(LAPI).json()["routes"]] == ["壽山", "七星山"]
        # delete: the row and the file
        assert e.c.delete(f"{LAPI}/{r['id']}").json() == {"removed": r["id"]}
        assert not RL.gpx_path(r["id"]).exists()
        assert e.c.delete(f"{LAPI}/{r['id']}").status_code == 404
        assert e.c.get(f"{LAPI}/{r['id']}").status_code == 404
        assert e.c.get(f"{LAPI}/{r['id']}/file").status_code == 404


def test_a_broken_file_is_refused_and_nothing_is_stored(monkeypatch):
    with LEnv(monkeypatch) as e:
        bad = e.c.post(LAPI, files={"file": ("x.gpx", b"<gpx></gpx>", "application/gpx+xml")})
        assert bad.status_code == 400 and bad.json()["detail"]["errors"]
        assert e.c.get(LAPI).json()["routes"] == []
        assert e.c.patch(f"{LAPI}/99", json={"name": "a"}).status_code == 404


def test_a_templates_gpx_moves_to_the_library_once(monkeypatch):
    with LEnv(monkeypatch) as e:
        t = e.c.post(UAPI, json={"name": "坡道", "cats": ["trail"], "steps": FREE_UPHILL}).json()
        e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("old.gpx", gpx_bytes(), "application/gpx+xml")})
        lst = e.c.get(LAPI).json()["routes"]
        assert len(lst) == 1
        m = lst[0]
        assert m["from_tpl"] == t["id"] and m["filename"] == "old.gpx" and m["name"] == "坡道"
        assert math.isclose(m["km"], 3.0, rel_tol=0.05)
        assert e.c.get(f"{LAPI}/{m['id']}/file").content == gpx_bytes()
        # idempotent: listed again, still one; the template keeps its own copy (nothing deleted)
        assert len(e.c.get(LAPI).json()["routes"]) == 1
        tt = e.c.get(UAPI).json()["templates"][0]
        assert tt["gpx"]["filename"] == "old.gpx" and UT.gpx_path(t["id"]).exists()
        # the user deletes the moved one: it is not brought back
        e.c.delete(f"{LAPI}/{m['id']}")
        assert e.c.get(LAPI).json()["routes"] == []
        # the template says where its route went (the 範本 page's note)
        assert e.c.get(UAPI).json()["templates"][0]["gpx"]["filename"] == "old.gpx"


# ---------------------------------------------------------------------------
# picking a route for a day's structure
# ---------------------------------------------------------------------------

def _work(items):
    rep = next(x for x in items if x["kind"] == "repeat")
    return rep["items"][0], rep["items"][1]


def test_picking_a_route_turns_each_climb_into_lap_at_the_top(monkeypatch):
    with LEnv(monkeypatch) as e:
        r = upload(e)
        steps = {"items": copy.deepcopy(FREE_UPHILL["items"])}
        res = e.c.post(f"{API}/steps/route", json={"kind": "hike", "steps": steps, "route": {"src": "gpx", "id": r["id"]}})
        assert res.status_code == 200, res.text
        b = res.json()
        s = b["steps"]
        route = s["route"]
        assert route["src"] == {"kind": "gpx", "id": str(r["id"])} and route["name"] == "坡"
        assert route["climb"]["no"] == 1 and route["rep_km"] == pytest.approx(route["climb"]["start_km"], abs=1e-6)
        assert b["applied"] == 1 and len(b["climbs"]) == 1 and b["climb"] == 1
        w, rest = _work(s["items"])
        m = LABEL.match(w["note"])
        assert m and abs(int(m.group(1)) - round(route["climb"]["gain_m"])) <= 1
        assert w["dur"]["type"] == "open" and w["dur"]["est"] > 0
        assert int(m.group(2)) == max(1, round(w["dur"]["est"] / 60))
        assert w["target"] == FREE_UPHILL["items"][1]["items"][0]["target"]           # the target as it was
        assert rest["note"] == "走下坡" and rest["dur"] == {"type": "open"}                # the way down as it was
        assert s["items"][0]["note"] == "平路暖身" and "tpl" not in s
        # the chart: each rep from the climb's foot, up and back
        chk = e.c.post(f"{API}/steps/check", json={"kind": "hike", "steps": s}).json()
        el = chk["elev"]
        wi = [i for i, o in enumerate(chk["order"]) if o["kind"] == "work"]
        assert len(wi) == 5 and all(el["r"][i][0] == pytest.approx(route["rep_km"], abs=0.02) for i in wi)
        assert all(el["up"][i] > 50 for i in wi)
        # saved on a day: kept as is; pushed to COROS: the steps and their names only, no route
        sv = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "hike", "title": "反覆爬坡", "steps": s})
        assert sv.status_code == 200, sv.text
        kept = sv.json()["steps"]
        assert kept["route"]["src"] == route["src"] and kept["route"]["climb"] == route["climb"]
        assert _work(kept["items"])[0]["note"] == w["note"]
        assert e.c.post(f"{API}/push-coros?scope=day&day=2026-10-03").status_code == 200
        prog = next(p for p in e.fake.programs.values() if "反覆爬坡" in p["name"])
        group = next(x for x in prog["exercises"] if x.get("isGroup"))
        kids = [x for x in prog["exercises"] if x.get("groupId") == str(group["id"])]
        from backend.sync import coros_workouts as CW
        assert kids[0]["name"] == w["note"]
        assert kids[0]["targetType"] == CW.TARGET_OPEN and group["sets"] == 5
        dump = json.dumps(prog, ensure_ascii=False)
        assert "slope.gpx" not in dump and '"route"' not in dump and '"lat"' not in dump


def test_a_climb_can_be_left_out_and_short_reps_are_not_climbs(monkeypatch):
    with LEnv(monkeypatch) as e:
        r = upload(e)
        steps = {"items": copy.deepcopy(FREE_UPHILL["items"])}
        # climb 0: draw the route only, the structure as it is
        b = e.c.post(f"{API}/steps/route", json={"kind": "hike", "steps": steps,
                                                 "route": {"src": "gpx", "id": r["id"], "climb": 0}}).json()
        assert b["applied"] == 0 and "climb" not in b["steps"]["route"] and "rep_km" not in b["steps"]["route"]
        assert _work(b["steps"]["items"])[0]["note"] == "上坡（直到按下計圈）"
        # 20″ strides on the route: not a climb rep
        strides = {"items": [st("warm", 900), {"kind": "repeat", "times": 6, "items": [
            st("other", 20, None, "快步跑"), st("rest", 40, None, "慢跑")]}]}
        b = e.c.post(f"{API}/steps/route", json={"kind": "easy", "steps": strides, "route": {"src": "gpx", "id": r["id"]}}).json()
        assert b["applied"] == 0 and _work(b["steps"]["items"])[0]["dur"] == {"type": "time", "value": 20}
        # no route: the copy and the template link go
        b = e.c.post(f"{API}/steps/route", json={"kind": "hike", "steps": {**steps, "tpl": 3, "route": {"km": [0, 1], "z": [0, 1]}},
                                                 "route": None}).json()
        assert "route" not in b["steps"] and "tpl" not in b["steps"]
        assert e.c.post(f"{API}/steps/route", json={"kind": "hike", "steps": steps,
                                                    "route": {"src": "gpx", "id": 999}}).status_code == 404
        assert e.c.post(f"{API}/steps/route", json={"kind": "hike", "steps": steps,
                                                    "route": {"src": "nope", "id": 1}}).status_code == 400


def _mine_view(file="2026/a.fit"):
    item = {"id": "c1", "kind": "climb", "kind_zh": "爬坡", "family": "run", "direction": "up", "time_key": "elapsed_s",
            "auto_name": "爬坡 壽山", "length_m": 1000.0, "gain_m": 100.0, "ref_file": file, "lat": [0], "lon": [0],
            "sports": ["trail running"], "n_efforts": 3, "n_activities": 3, "n_reversed": 0, "best_s": 540.0,
            "last_s": 600.0, "last_date": "2026-10-01T07:00:00", "first_date": "2026-09-01T07:00:00",
            "parent": None, "partials": [], "merged_from": [], "derived": False,
            "efforts": [{"id": "e1", "file": file, "i0": 10, "i1": 20, "start": "2026-10-01T07:00:00", "elapsed_s": 600.0}]}
    flat = {**item, "id": "f1", "kind": "stretch", "direction": "flat", "auto_name": "路段", "gain_m": 0.0}
    once = {**item, "id": "o1", "n_activities": 1, "n_efforts": 1}
    return {"built_at": "2026-10-02T00:00:00", "items": {"c1": item, "f1": flat, "o1": once}, "aliases": {}, "weather": None}


def _mine_track(tmp_path, file="2026/a.fit"):
    """30 points 50 m apart north; points 10–20 climb 100 m over 500 m."""
    n = 30
    lat = [25.0 + i * 0.05 / 111.2 for i in range(n)]
    e = [0.0 if i <= 10 else (10.0 * (i - 10) if i <= 20 else 100.0) for i in range(n)]
    store = R.RouteStore(tmp_path / "routes")
    store.save_track(file, {"file": file, "lat": lat, "lon": [121.5] * n, "e": e, "d": [i * 0.05 for i in range(n)]})
    return store


def test_my_segments_are_on_the_page_and_can_be_picked(monkeypatch, tmp_path):
    view = _mine_view()
    monkeypatch.setattr(RA, "_index", lambda: view)
    monkeypatch.setattr(RA, "STORE", _mine_track(tmp_path))
    monkeypatch.setattr(RA, "_names", lambda v=None: {"c1": "壽山坡"})
    with LEnv(monkeypatch) as e:
        rows = e.c.get(f"{API}/routes/mine").json()["rows"]
        assert [x["id"] for x in rows] == ["c1", "f1"]            # done ≥ 2 times, climbs first
        m = rows[0]
        assert m["name"] == "壽山坡" and m["kind"] == "climb" and m["best_s"] == 540.0 and m["n_efforts"] == 3
        d = e.c.get(f"{API}/routes/mine/c1").json()
        assert d["name"] == "壽山坡" and d["profile"]["km"][-1] == pytest.approx(0.5, abs=0.05)
        assert max(d["profile"]["z"]) - min(d["profile"]["z"]) == pytest.approx(100, abs=20)   # smoothed (the course builder)
        assert len(d["table"]["climbs"]) == 1 and d["link"].endswith("#c1")
        assert e.c.get(f"{API}/routes/mine/nope").status_code == 404
        b = e.c.post(f"{API}/steps/route", json={"kind": "hike", "steps": {"items": copy.deepcopy(FREE_UPHILL["items"])},
                                                 "route": {"src": "seg", "id": "c1"}}).json()
        route = b["steps"]["route"]
        assert route["src"] == {"kind": "seg", "id": "c1"} and route["name"] == "壽山坡"
        assert LABEL.match(_work(b["steps"]["items"])[0]["note"])


# ---------------------------------------------------------------------------
# the stored copy and the chart
# ---------------------------------------------------------------------------

def test_the_route_copy_keeps_where_it_came_from():
    r = {"km": [0, 1, 2], "z": [0, 50, 60], "name": "r", "src": {"kind": "gpx", "id": "4"},
         "climb": {"no": 1, "gain_m": 50, "dist_m": 1000, "grade": 0.05, "start_km": 0, "end_km": 1}, "rep_km": 0}
    d = WS.normalize({"items": [st("work", 600)], "route": r})["route"]
    assert d["src"] == {"kind": "gpx", "id": "4"} and d["climb"]["gain_m"] == 50 and d["rep_km"] == 0
    for bad in ({"kind": "x", "id": "4"}, {"kind": "gpx"}, "gpx"):
        assert "src" not in WS.normalize({"items": [st("work", 600)], "route": {**r, "src": bad}})["route"]
    d = WS.normalize({"items": [st("work", 600)], "route": {**r, "rep_km": 9, "climb": {"no": "a"}}})["route"]
    assert "rep_km" not in d and "climb" not in d


def test_hill_repeats_start_at_the_climbs_foot():
    km, z = flat_then_hill()
    c = WS.Ctx.of({"cp": 250.0}, "power", speeds={"v_easy": 6.0, "ep_kmh": 6.0, "terrain": "trail"})
    tg = {"type": "power", "mode": "pct", "lo": 0.77, "hi": 0.79}
    steps = WS.normalize({"items": [st("warm", 300, tg), {"kind": "repeat", "times": 2, "last_rest": True, "items": [
        st("work", 240, tg), st("rest", 300)]}]})
    el = UT.route_elevation(steps, c, {"km": km, "z": z, "rep_km": 1.0})
    assert el["r"][0][0] == 0.0 and el["r"][0][1] == pytest.approx(0.5, abs=0.01)     # the warm-up on the flat
    assert el["r"][1][0] == pytest.approx(1.0) and el["r"][3][0] == pytest.approx(1.0)  # each rep from the foot
    assert el["up"][1] > 10 and el["down"][2] > 10
    # without rep_km: from where the warm-up ended (SP-639 as before)
    el0 = UT.route_elevation(steps, c, {"km": km, "z": z})
    assert el0["r"][1][0] == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# tenants
# ---------------------------------------------------------------------------

def _t(tmp_path, tid):
    d = tmp_path / tid
    d.mkdir(parents=True, exist_ok=True)
    return tenancy.Tenant(id=tid, kind=tenancy.USER, root=d, shared=d, caps=tenancy.USER_CAPS)


def test_each_person_sees_only_their_own_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(RL, "ROOT", None)                  # the tenant's own folder
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")

    async def go():
        da, dbb = await make_db(), await make_db()
        with tenancy.use(a):
            ra = await RL.create(da, gpx_bytes(), "a.gpx")
            assert (a.root / "route_gpx" / f"{ra['id']}.gz").exists()
        with tenancy.use(b):
            assert await RL.list_all(dbb) == []
            with pytest.raises(RL.RouteError):
                await RL.get(dbb, ra["id"])
            assert RL.read_file(ra["id"]) is None
            rb = await RL.create(dbb, gpx_bytes(km=5.0), "b.gpx")
            assert rb["id"] == ra["id"]                       # the same id, another person's row and file
            assert RL.read_file(rb["id"]) == gpx_bytes(km=5.0)
        with tenancy.use(a):
            assert [r["filename"] for r in await RL.list_all(da)] == ["a.gpx"]
            assert RL.read_file(ra["id"]) == gpx_bytes()
            await RL.delete(da, ra["id"])
        with tenancy.use(b):
            assert [r["filename"] for r in await RL.list_all(dbb)] == ["b.gpx"]
            assert RL.read_file(rb["id"]) == gpx_bytes(km=5.0)
    run(go())


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def test_the_routes_page_is_the_uploaded_tab_of_routes(monkeypatch):
    """SP-693: once the fourth tab of 課表; now 路線 › 上傳的 (the old URL redirects there; the rest of the
    move is backend/tests/test_routes_page_sp693.py)."""
    e = LEnv(monkeypatch)
    e.c.app.include_router(RA.router)
    with e:
        p = e.c.get(f"{API}/routes/page")
        assert p.status_code == 200 and 'id="rl-list"' in p.text
        assert str(p.url).endswith("/api/v1/routes/uploaded/page")
        assert 'href="/api/v1/routes/uploaded/page" aria-current="page"' in p.text
    from backend.i18n import use_locale
    from backend.i18n.pages import render_page
    for page in ("schedule", "compliance", "templates"):
        assert f'href="{API}/routes/page"' not in render_page(page).body.decode()
    with use_locale("en"):
        en = render_page("routelib").body.decode()
    assert ">Routes</title>" in en and "Uploaded routes" in en
