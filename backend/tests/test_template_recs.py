"""
插入範本's 「推薦」 block (engine/template_recs.py, GET /steps/templates/recs): per tab the
3 best templates for the session, the interval ladder's next step first on 強度課.
Synthetic inputs only (the library itself, the test plan inputs of test_plan_store).
"""
from datetime import date

import pytest

from backend.engine import interval_library as IL
from backend.engine import template_recs as TR
from backend.engine import workout_steps as WS


@pytest.fixture(scope="module")
def tpl():
    return WS.templates()


def keys(r, cat):
    return [x["key"] for x in r["cats"][cat]]


def test_every_tab_gets_at_most_three_with_a_reason(tpl):
    r = TR.recommend(tpl, kind="easy", minutes=50, phase="base")
    assert set(r["cats"]) == {c["id"] for c in tpl["cats"]}
    for cat, picks in r["cats"].items():
        assert 1 <= len(picks) <= 3, cat
        assert len({p["key"] for p in picks}) == len(picks)
        assert all(p["reason"] and "\n" not in p["reason"] for p in picks)
    assert "推估" in r["tip"] and r["inputs"]["phase_label"] == "基礎期"


def test_the_ladder_pick_is_first_on_quality_even_over_the_cap(tpl):
    key, why = TR.ladder_pick("z3b", 45.0)
    f = IL.fit("z3b", 45.0)
    assert key == f["variant"].key and why.startswith("間歇階梯的下一步（T2）")
    r = TR.recommend(tpl, kind="quality", cap=20, minutes=45, phase="base", rung="z3b",
                     ladder_key=key, ladder_reason=why)
    assert keys(r, "quality")[0] == key and r["cats"]["quality"][0]["reason"] == why
    # same rung's equivalents follow; at most 2 from one rung
    rungs = [IL.get(k).rung for k in keys(r, "quality") if IL.get(k)]
    assert all(rungs.count(x) <= 2 for x in rungs)


def test_zone5_closed_never_recommends_zone5(tpl):
    shut = TR.recommend(tpl, kind="quality", minutes=60, phase="specific", z5_open=False)
    z5 = {r["key"] for g in tpl["groups"] if g["cat"] == "quality" and g["sub"] in ("vo2max", "speed")
          for r in g["rows"]}
    assert not z5 & set(keys(shut, "quality"))
    opened = TR.recommend(tpl, kind="quality", minutes=60, phase="specific", z5_open=True, rung="z5c",
                          ladder_key="v3a", ladder_reason="間歇階梯的下一步（V3）")
    assert keys(opened, "quality")[0] == "v3a" and any(k in z5 for k in keys(opened, "quality")[1:])


def test_terrain_time_type_and_phase(tpl):
    road = TR.recommend(tpl, kind="quality", minutes=50, phase="base", rung="z3b", terrain="road")
    trail = TR.recommend(tpl, kind="quality", minutes=50, phase="base", rung="z3b", terrain="trail")
    hill = lambda k: (IL.get(k) is not None and IL.get(k).terrain == "hill")   # noqa: E731
    assert not any(hill(k) for k in keys(road, "quality"))
    assert any(hill(k) for k in keys(trail, "quality"))
    # a 45-min cap pushes the long templates out
    capped = TR.recommend(tpl, kind="easy", cap=45, minutes=40, phase="base")
    rows = {r["key"]: r for g in tpl["groups"] for r in g["rows"]}
    assert all(TR.row_minutes(rows[k]) <= 45.5 for k in keys(capped, "easy"))
    # 長跑日: ≥ 90 min first
    long = TR.recommend(tpl, kind="long", minutes=120, phase="base")
    assert TR.row_minutes(rows[keys(long, "easy")[0]]) >= TR.LONG_MIN
    # 減量期: no downhill eccentric block; 專項期: trail-specific climbs first
    taper = TR.recommend(tpl, kind="hike", minutes=50, phase="taper", terrain="trail")
    assert "lib:downhill_ecc" not in keys(taper, "trail")
    spec = TR.recommend(tpl, kind="hike", minutes=100, phase="specific", terrain="trail")
    assert keys(spec, "trail")[0] in TR.TRAIL_SPECIFIC
    # near-duplicates (the three 陡坡健走) at most once
    assert sum(k.startswith("lib:steep_") for k in keys(spec, "trail")) <= 1


def test_ladder_rows_say_they_are_ladder_variants(tpl):
    rows = [r for g in tpl["groups"] for r in g["rows"]]
    lad = [r for r in rows if r.get("variant")]
    assert lad and all(IL.get(r["key"]) is not None and r["rung"] == IL.get(r["key"]).rung for r in lad)
    assert not any(r.get("variant") for r in rows if r["key"].startswith("lib:"))


def test_api_recs_first_is_the_old_dropdowns_pick(monkeypatch):
    from backend.api import plan_sessions
    from backend.sync import coros_workouts as CW
    from backend.tests.test_plan_store import API, Env
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(plan_sessions, "_tpace", lambda: 280.0)
    monkeypatch.setattr(plan_sessions, "_speeds", lambda: {"v_easy": 10.0, "v_easy_src": "t", "ep_kmh": 7.0})
    with Env(monkeypatch) as e:
        q = next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["kind"] == "quality")
        r = e.c.get(f"{API}/steps/templates/recs", params={"kind": "quality", "day": q["day"], "uid": q["uid"],
                                                          "minutes": q["minutes"]})
        assert r.status_code == 200, r.text
        b = r.json()
        old = e.c.get(f"{API}/variants", params={"day": q["day"]}).json()["templates"]
        if old["recommended_key"]:
            assert b["cats"]["quality"][0]["key"] == old["recommended_key"]
        assert b["inputs"]["phase"] == "base" and b["inputs"]["terrain"] in ("road", "trail")
        hike = e.c.get(f"{API}/steps/templates/recs", params={"kind": "hike", "day": q["day"]}).json()
        assert hike["inputs"]["terrain"] == "trail" and hike["cats"]["trail"]


def test_family_drives_the_phase_rules(tpl):
    # 基礎期: 有氧間歇 first (no Zone 5 yet); 專項期 with Zone 5 closed: still no VO2max / 速度,
    # and the cruise intervals stay in (閾值課不停)
    base = TR.recommend(tpl, kind="quality", minutes=50, phase="base", rung="z3b", terrain="road")
    fam = {r["key"]: (r.get("family") or {}) for g in tpl["groups"] for r in g["rows"]}
    assert all(fam[k].get("id") == "aerobic" for k in keys(base, "quality"))
    spec = TR.recommend(tpl, kind="quality", minutes=60, phase="specific", z5_open=False)
    assert all(fam[k].get("id") == "aerobic" for k in keys(spec, "quality"))
    assert any(fam[k].get("sub") in ("cruise", "supra") for k in keys(spec, "quality"))
    # a library row in the current rung's family gets the 同一類 reason
    z5 = TR.recommend(tpl, kind="quality", minutes=60, phase="specific", z5_open=True, rung="z5c",
                      ladder_key="v3a", ladder_reason="間歇階梯的下一步（V3）")
    assert any("同一類（VO2max 間歇）" in x["reason"] for x in z5["cats"]["quality"])


def test_technical_terrain_by_phase(tpl):
    # SP-62: the base phase favours the low-RPE technical session, the 專項期 the race-like one
    base = TR.recommend(tpl, kind="hike", minutes=75, phase="base", terrain="trail")
    assert "lib:tech_easy" in keys(base, "trail")
    spec = TR.recommend(tpl, kind="hike", minutes=115, phase="specific", terrain="trail")
    assert "lib:tech_hard" in keys(spec, "trail")


# 我的範本 in the 推薦 block (SP-36): ranked with the built-ins by the same rules, tagged mine
def _st(kind, sec, tg=None):
    return {"kind": kind, "dur": {"type": "time", "value": sec}, "target": tg or {"type": "auto", "intent": "easy"},
            "note": ""}


def _mine(tid, name, cats, items):
    return {"id": tid, "name": name, "cats": cats, "steps": WS.normalize({"items": items})}


MY_CRUISE = _mine(1, "我的巡航", ["quality"], [_st("warm", 900), {"kind": "repeat", "times": 3, "items": [
    _st("work", 600, {"type": "power", "mode": "pct", "lo": 0.95, "hi": 0.99}), _st("rest", 120)]}, _st("cool", 600)])
MY_DOWN = _mine(2, "我的下坡", ["trail"], [_st("warm", 900), _st("work", 1200, {"type": "rpe", "lo": 4, "hi": 5, "down": 300}),
                                         _st("cool", 600)])
MY_TECH = _mine(3, "我的技術路", ["trail"], [_st("warm", 900), _st("work", 1800, {"type": "rpe", "lo": 7, "hi": 8, "up": 300}),
                                          _st("cool", 600)])
MY_UP = _mine(4, "我的上坡", ["trail", "c1"], [_st("warm", 900), {"kind": "repeat", "times": 5, "items": [
    _st("work", 300, {"type": "hr", "mode": "pct", "lo": 0.9, "hi": 0.95}), _st("rest", 180)]}, _st("cool", 600)])


@pytest.fixture(scope="module")
def mine_tpl():
    return WS.templates(user={"templates": [MY_CRUISE, MY_DOWN, MY_TECH, MY_UP],
                              "cats": [{"id": "c1", "label": "上坡", "custom": True}]})


def _row(tpl, key):
    return next((g, r) for g in tpl["groups"] for r in g["rows"] if r["key"] == key)


def _s(**kw):
    s = {"kind": "quality", "cap": None, "minutes": 60.0, "terrain": "road", "phase": "base", "z5_open": False,
         "rung": None, "ladder_key": None, "ladder_reason": "", "sport": "trail"}
    return {**s, **kw}


def test_my_templates_score_by_the_same_rules(mine_tpl):
    g, r = _row(mine_tpl, "user:1")
    assert r["mine"] and r["family"]["id"] == "aerobic"
    sc = TR._score(r, "quality", g["sub"], _s())
    assert any("基礎期先練有氧間歇" in w for _p, w in sc.plus)
    # the ladder's rung family: the same 同一類 bonus as a library row
    sc = TR._score(r, "quality", g["sub"], _s(rung="z3b"))
    assert any("同一類（有氧間歇）" in w for _p, w in sc.plus)
    # trail phase rules by the row's kind: downhill / hard technical are race-specific, a downhill
    # one is out in the taper; a climb one is base work
    _g, d = _row(mine_tpl, "user:2")
    assert d["trail_sub"] == "downhill"
    assert any("練賽道的爬升／下坡" in w for _p, w in TR._score(d, "trail", "downhill", _s(kind="hike", phase="specific")).plus)
    assert TR._score(d, "trail", "downhill", _s(kind="hike", phase="taper")).v <= TR.DROP_BELOW
    _g, t = _row(mine_tpl, "user:3")
    assert t["trail_sub"] == "technical" and t["role"] == "quality"
    assert any("接近比賽的路況" in w for _p, w in TR._score(t, "trail", "technical", _s(kind="hike", phase="build")).plus)
    _g, u = _row(mine_tpl, "user:4")
    assert any("有氧爬坡" in w for _p, w in TR._score(u, "trail", "climb", _s(kind="hike", phase="base")).plus)


def test_my_templates_are_recommended_and_tagged(mine_tpl):
    r = TR.recommend(mine_tpl, kind="hike", minutes=50, phase="specific", terrain="trail")
    assert all(p["mine"] == p["key"].startswith("user:") for c in r["cats"].values() for p in c)
    # a custom tab holds only 我的範本: its 推薦 is them
    assert [p["key"] for p in r["cats"]["c1"]] == ["user:4"] and r["cats"]["c1"][0]["mine"] is True
    taper = TR.recommend(mine_tpl, kind="hike", minutes=45, phase="taper", terrain="trail")
    assert "user:2" not in keys(taper, "trail")
    road = TR.recommend(mine_tpl, kind="hike", minutes=50, phase="specific", terrain="trail", sport="road")
    assert road["cats"]["trail"] == []
    assert "我的範本" in r["tip"]


def test_api_recs_include_my_templates(monkeypatch):
    from backend.api import plan_sessions
    from backend.sync import coros_workouts as CW
    from backend.tests.test_plan_store import API, Env
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(plan_sessions, "_tpace", lambda: 280.0)
    monkeypatch.setattr(plan_sessions, "_speeds", lambda: {"v_easy": 10.0, "v_easy_src": "t", "ep_kmh": 7.0})
    with Env(monkeypatch) as e:
        c = e.c.post(f"{API}/steps/templates/cats", json={"label": "上坡"}).json()
        t = e.c.post(f"{API}/steps/templates/user", json={"name": "我的上坡", "cats": ["trail", c["id"]],
                                                          "steps": {"items": MY_UP["steps"]["items"]}}).json()
        b = e.c.get(f"{API}/steps/templates/recs", params={"kind": "hike", "minutes": 50}).json()
        assert b["cats"][c["id"]] == [{"key": f"user:{t['id']}", "reason": b["cats"][c["id"]][0]["reason"], "mine": True}]
        # the 插入範本 menu has the row the pick points at
        menu = e.c.get(f"{API}/steps/templates").json()
        assert any(r["key"] == f"user:{t['id']}" for g in menu["groups"] if g["cat"] == c["id"] for r in g["rows"])
