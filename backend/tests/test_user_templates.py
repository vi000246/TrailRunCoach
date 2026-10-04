"""
The user's own templates (engine/user_templates.py, api/plan_sessions.py /steps/templates/user,
SP-36): CRUD, categories (built-in + custom, several per template), 儲存成範本, 複製成我的範本,
relative targets resolved when applied, the route GPX and its profile on the chart's time
axis, and a 「長間歇、自由模式」 uphill template pushed to COROS.
"""
import copy
import math
from datetime import date

import pytest

from backend.api import plan_sessions
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env

UAPI = f"{API}/steps/templates/user"


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(plan_sessions, "_tpace", lambda: 280.0)
    monkeypatch.setattr(plan_sessions, "_speeds", lambda: {"v_easy": 10.0, "v_easy_src": "t", "ep_kmh": 7.0})


def st(kind, dur, target=None, note=""):
    if isinstance(dur, int):
        dur = {"type": "time", "value": dur}
    return {"kind": kind, "dur": dur, "target": target or {"type": "none"}, "note": note}


# 「長間歇、自由模式」越野上坡: each rep ends with the lap button, the climb with no target
FREE_UPHILL = {"items": [
    st("warm", 900, {"type": "auto", "intent": "easy"}, "平路暖身"),
    {"kind": "repeat", "times": 5, "last_rest": True, "note": "上坡 ×5", "items": [
        st("work", {"type": "open"}, {"type": "none"}, "上坡（直到按下計圈）"),
        st("rest", {"type": "open"}, {"type": "none"}, "走下坡")]},
    st("cool", 600, {"type": "auto", "intent": "easy"}, "緩和")]}


def gpx_bytes(n=60, climb_m=300.0, km=3.0):
    """A straight route north: up `climb_m` over the first half, back down over the second."""
    pts = []
    for i in range(n + 1):
        f = i / n
        lat = 25.0 + f * km / 111.2
        ele = 100.0 + climb_m * (2 * f if f <= 0.5 else 2 * (1 - f))
        pts.append(f'<trkpt lat="{lat:.6f}" lon="121.5"><ele>{ele:.1f}</ele></trkpt>')
    return ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>坡</name>'
            "<trkseg>" + "".join(pts) + "</trkseg></trk></gpx>").encode()


def _session(e, kind):
    return next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["kind"] == kind)


def test_crud_categories_and_menu(monkeypatch):
    with Env(monkeypatch) as e:
        assert e.c.get(UAPI).json()["templates"] == []
        r = e.c.post(UAPI, json={"name": "", "cats": ["nope"], "steps": {"items": []}})
        assert r.status_code == 400
        errs = r.json()["detail"]["errors"]
        assert any("名稱" in x for x in errs) and any("nope" in x for x in errs) and any("步驟" in x for x in errs)
        # a custom category, then a template in a built-in and the custom one
        c = e.c.post(f"{API}/steps/templates/cats", json={"label": "上坡"}).json()
        assert c["id"].startswith("c") and c["custom"]
        assert e.c.post(f"{API}/steps/templates/cats", json={"label": "上坡"}).status_code == 400
        r = e.c.post(UAPI, json={"name": "長間歇、自由模式", "cats": ["trail", c["id"]], "steps": FREE_UPHILL,
                                 "target_basis": "hr", "note": "越野上坡"})
        assert r.status_code == 200, r.text
        t = r.json()
        assert t["cats"] == ["trail", c["id"]] and t["target_basis"] == "hr" and t["steps"]["origin"] == "user"
        work = t["steps"]["items"][1]["items"][0]
        assert work["dur"] == {"type": "open"} and work["target"] == {"type": "none"}
        # the editor's menu: under 越野跑 (its trail kind) and under the custom tab
        tp = e.c.get(f"{API}/steps/templates").json()
        assert any(x["id"] == c["id"] and x["label"] == "上坡" for x in tp["cats"])
        mine = [g for g in tp["groups"] if g.get("mine")]
        assert {(g["cat"], g["sub"]) for g in mine} == {("trail", "climb"), (c["id"], None)}
        row = mine[0]["rows"][0]
        assert row["key"] == f"user:{t['id']}" and row["target_basis"] == "hr" and row["full"][0]["kind"] == "warm"
        # rename the category, edit the template, delete the category (the template keeps 越野跑)
        assert e.c.patch(f"{API}/steps/templates/cats/{c['id']}", json={"label": "爬坡"}).json()["label"] == "爬坡"
        u = e.c.patch(f"{UAPI}/{t['id']}", json={"name": "長間歇上坡", "cats": ["trail", "quality", c["id"]]}).json()
        assert u["name"] == "長間歇上坡" and u["cats"] == ["trail", "quality", c["id"]]
        # 強度課 with no interval family (no target on the work): listed under every 強度課 sub-tab
        tp = e.c.get(f"{API}/steps/templates").json()
        assert any(g.get("mine") and g["cat"] == "quality" and g["sub"] is None for g in tp["groups"])
        assert e.c.delete(f"{API}/steps/templates/cats/{c['id']}").json()["templates"] == 1
        assert e.c.get(f"{UAPI}").json()["templates"][0]["cats"] == ["trail", "quality"]
        assert e.c.patch(f"{UAPI}/{t['id']}", json={"target_basis": "pace"}).status_code == 400
        assert e.c.delete(f"{UAPI}/{t['id']}").json() == {"removed": t["id"]}
        assert e.c.delete(f"{UAPI}/{t['id']}").status_code == 404
        assert e.c.get(UAPI).json()["templates"] == []


def test_quality_template_goes_to_its_family_tab(monkeypatch):
    with Env(monkeypatch) as e:
        vo2 = {"items": [st("warm", 900, {"type": "auto", "intent": "easy"}),
                         {"kind": "repeat", "times": 5, "items": [
                             st("work", 180, {"type": "power", "mode": "pct", "lo": 1.06, "hi": 1.12}),
                             st("rest", 180)]},
                         st("cool", 600, {"type": "auto", "intent": "easy"})]}
        e.c.post(UAPI, json={"name": "5×3′", "cats": ["quality"], "steps": vo2})
        g = next(g for g in e.c.get(f"{API}/steps/templates").json()["groups"] if g.get("mine"))
        assert (g["cat"], g["sub"]) == ("quality", "vo2max") and g["rows"][0]["family"]["id"] == "vo2max"


def test_save_session_as_template_and_copy_builtin(monkeypatch):
    with Env(monkeypatch) as e:
        q = _session(e, "quality")
        # no stored structure: the derived one
        r = e.c.post(f"{API}/sessions/{q['uid']}/save-as-template", json={"name": "我的閾值", "cats": ["quality"]})
        assert r.status_code == 200, r.text
        t = r.json()
        derived = e.c.post(f"{API}/steps/derive", json={"uid": q["uid"]}).json()["steps"]
        assert len(t["steps"]["items"]) == len(derived["items"])
        # the editor's current structure wins
        r = e.c.post(f"{API}/sessions/{q['uid']}/save-as-template",
                     json={"name": "自由上坡", "cats": ["trail"], "steps": FREE_UPHILL, "target_basis": "power"})
        assert r.json()["steps"]["items"][1]["times"] == 5 and r.json()["target_basis"] == "power"
        assert e.c.post(f"{API}/sessions/nope/save-as-template", json={"name": "x"}).status_code == 404
        # a built-in (read-only) template copied to mine
        lib = next(r for g in e.c.get(f"{API}/steps/templates").json()["groups"] for r in g["rows"]
                   if r["key"] == "lib:pal_ez")
        cp = e.c.post(f"{UAPI}/copy", json={"key": "lib:pal_ez"}).json()
        assert cp["copied_from"] == "lib:pal_ez" and cp["cats"] == ["easy"] and cp["target_basis"] == "power"
        assert [x["kind"] for x in cp["steps"]["items"]] == [x["kind"] for x in lib["full"]]
        assert e.c.post(f"{UAPI}/copy", json={"key": "lib:none"}).status_code == 404


def test_relative_targets_resolve_with_the_days_thresholds():
    tpl = WS.normalize({"items": [
        st("work", 600, {"type": "power", "mode": "pct", "lo": 0.9, "hi": 0.95}),
        st("work", 600, {"type": "hr", "mode": "pct", "lo": 0.9, "hi": 0.95}),
        st("work", 600, {"type": "power", "mode": "abs", "lo": 230, "hi": 240})]})
    a = WS.Ctx.of({"cp": 250.0, "lthr": 170.0}, "power")
    b = WS.Ctx.of({"cp": 300.0, "lthr": 180.0}, "power")
    ra = [WS.resolve(x, a) for x in tpl["items"]]
    rb = [WS.resolve(x, b) for x in tpl["items"]]
    assert (ra[0].lo, ra[0].hi) == (225, 238) and (rb[0].lo, rb[0].hi) == (270, 285)
    assert (ra[1].lo, rb[1].lo) == (153, 162)
    assert (ra[2].lo, ra[2].hi) == (rb[2].lo, rb[2].hi) == (230, 240)       # absolute W: as stored


def test_applied_template_targets_follow_the_session_thresholds(monkeypatch):
    with Env(monkeypatch) as e:
        steps = {"items": [st("work", 1200, {"type": "power", "mode": "pct", "lo": 0.9, "hi": 0.95}, "穩定")]}
        t = e.c.post(UAPI, json={"name": "20′ 穩定", "cats": ["easy"], "steps": steps, "target_basis": "power"}).json()
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "easy", "title": t["name"],
                                              "steps": {"items": t["steps"]["items"]}, "target_basis": "power"})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["target_basis"] == "power" and s["steps"]["items"][0]["target"]["mode"] == "pct"
        cp = (e.inp.get("thresholds") or {}).get("cp")
        pv = e.c.get(f"{API}/sessions/{s['uid']}/coros-preview").json()
        assert pv["lines"][0]["target"] == f"功率 {round(0.9 * cp)}–{round(0.95 * cp)} W"


def test_gpx_upload_replace_remove_and_profile_on_the_chart(monkeypatch):
    with Env(monkeypatch) as e:
        t = e.c.post(UAPI, json={"name": "坡道", "cats": ["trail"], "steps": {"items": [
            st("warm", 600, {"type": "auto", "intent": "easy"}),
            st("work", 1800, {"type": "rpe", "lo": 6, "hi": 7, "up": 300}, "爬"),
            st("cool", 1200, {"type": "auto", "intent": "easy"})]}}).json()
        bad = e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("x.gpx", b"<gpx></gpx>", "application/gpx+xml")})
        assert bad.status_code == 400
        r = e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("slope.gpx", gpx_bytes(), "application/gpx+xml")})
        assert r.status_code == 200, r.text
        g = r.json()["gpx"]
        assert g["filename"] == "slope.gpx" and math.isclose(g["km"], 3.0, rel_tol=0.05) and g["gain_m"] > 200
        assert UT.gpx_path(t["id"]).exists()
        assert e.c.get(f"{UAPI}/{t['id']}/gpx/file").content == gpx_bytes()
        # the editor's check of a structure made from it: the profile on the time axis
        tpl = e.c.get(f"{API}/steps/templates").json()
        row = next(r for g_ in tpl["groups"] for r in g_["rows"] if r["key"] == f"user:{t['id']}")
        assert row["gpx"]["filename"] == "slope.gpx"
        chk = e.c.post(f"{API}/steps/check", json={"kind": "hike", "steps": {"items": row["full"], "tpl": t["id"]}}).json()
        el = chk["elev"]
        total = sum(o["sec"] for o in chk["order"])
        assert el["t"][0] == 0 and el["t"] == sorted(el["t"]) and el["t"][-1] <= total + 1
        assert max(el["z"]) == pytest.approx(el["z_max"], abs=15) and el["note"]
        # replace with a longer route: not covered by the hour → ends at the workout's end
        e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("long.gpx", gpx_bytes(km=30.0), "application/gpx+xml")})
        el2 = e.c.post(f"{API}/steps/check", json={"kind": "hike", "steps": {"items": row["full"], "tpl": t["id"]}}).json()["elev"]
        assert el2["complete"] is False and el2["t"][-1] == pytest.approx(total, abs=1) and 0 < el2["km"] < 30
        # a session saved from it keeps the link (its chart shows the same profile)
        s = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "hike", "title": "坡道",
                                              "steps": {"items": row["full"], "tpl": t["id"]}}).json()
        assert s["steps"]["tpl"] == t["id"]
        d = e.c.post(f"{API}/steps/derive", json={"uid": s["uid"]}).json()
        assert d["steps"]["tpl"] == t["id"]
        assert e.c.post(f"{API}/steps/check", json={"uid": s["uid"], "steps": d["steps"]}).json()["elev"]["t"]
        # remove: no profile any more, the file is gone
        assert e.c.delete(f"{UAPI}/{t['id']}/gpx").json()["gpx"] is None
        assert not UT.gpx_path(t["id"]).exists()
        assert e.c.delete(f"{UAPI}/{t['id']}/gpx").status_code == 404
        assert e.c.post(f"{API}/steps/check", json={"kind": "hike", "steps": {"items": row["full"], "tpl": t["id"]}}).json()["elev"] is None


def test_route_elevation_aligns_by_estimated_speed():
    prof = {"km": [0.0, 1.0, 2.0], "z": [0.0, 100.0, 100.0], "route_km": 2.0}
    # 1 km + 100 m climb = 2 km effort; 1 km flat = 1 km effort. At 6 km/h EP the climb takes 20′, the flat 10′
    c = WS.Ctx.of({"cp": 250.0}, "power", speeds={"v_easy": 6.0, "ep_kmh": 6.0, "terrain": "trail"})
    steps = WS.normalize({"items": [st("work", 3600, {"type": "power", "mode": "pct", "lo": 0.77, "hi": 0.79})]})
    el = UT.route_elevation(steps, c, prof)
    assert el["t"][:3] == [0.0, 1200.0, 1800.0] and el["complete"] is True and el["km"] == 2.0
    # a lap-button step is drawn 90 s wide: the route is mapped onto that width
    lap = WS.normalize({"items": [st("work", {"type": "open"}, {"type": "power", "mode": "pct", "lo": 0.77, "hi": 0.79})]})
    el = UT.route_elevation(lap, c, prof)
    assert el["t"][-1] == WS.OPEN_CHART_S and el["complete"] is False


def test_free_mode_uphill_template_pushes_to_coros(monkeypatch):
    with Env(monkeypatch) as e:
        t = e.c.post(UAPI, json={"name": "長間歇、自由模式", "cats": ["trail"], "steps": FREE_UPHILL}).json()
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "hike", "title": "長間歇上坡",
                                              "steps": {"items": copy.deepcopy(t["steps"]["items"])}})
        assert r.status_code == 200, r.text
        res = e.c.post(f"{API}/push-coros?scope=day&day=2026-10-03")
        assert res.status_code == 200, res.text
        prog = next(p for p in e.fake.programs.values() if "長間歇上坡" in p["name"])
        ex = prog["exercises"]
        group = next(x for x in ex if x.get("isGroup"))
        assert group["sets"] == 5
        kids = [x for x in ex if x.get("groupId") == str(group["id"])]
        assert [k["targetType"] for k in kids] == [CW.TARGET_OPEN, CW.TARGET_OPEN]
        assert all(k["intensityType"] == CW.INT_NONE for k in kids)
        assert [k["name"] for k in kids] == ["上坡（直到按下計圈）", "走下坡"]
