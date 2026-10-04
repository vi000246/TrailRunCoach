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
