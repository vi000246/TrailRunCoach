"""
engine/workout_templates.py (the editor's 插入範本 library) and the time estimate of
distance / lap-button steps (engine/workout_steps.py). Synthetic thresholds only.
"""
import pytest

from backend.engine import workout_steps as WS
from backend.engine import workout_templates as WT

FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0, "tpace": 270.0}


def band(lo, hi, hrp=None):
    """An auto band (the generated sessions' targets), optionally with its own % LTHR."""
    t = {"type": "auto", "intent": "band", "lo": lo, "hi": hi, "cls": ""}
    if hrp:
        t["hrp"] = list(hrp)
    return t


def _ctx(basis, **sp):
    return WS.Ctx.of(FULL, basis, speeds=sp or None)


NATIVE = {"pal_ez": "power", "pal_supra": "power", "stryd_cp_3_12": "power", "ronnestad_3015": "power",
          "friel_cruise": "hr", "ua_z2": "hr", "pfitz_lt": "hr", "dsw_classic": "hr", "steep_10": "hr",
          "daniels_cruise": "pace", "daniels_r": "pace", "canova_specific": "pace", "billat_3030": "pace"}


def test_every_template_has_a_source_a_warm_up_and_its_own_basis():
    seen = set()
    for t in WT.TEMPLATES:
        assert t.key not in seen and t.src and t.url.startswith("http") and t.cat in ("easy", "quality", "test", "trail")
        assert t.cat != "quality" or t.sub in ("z3", "z4", "z5"), t.key
        seen.add(t.key)
        d = WS.normalize({"items": WT.items_of(t)})
        assert d["items"][0]["kind"] == "warm", t.key
        main, every = [], []
        for row in WS.flat(d["items"]):
            st = row["st"]
            tg = st["target"]
            if tg.get("type") == "auto":
                assert tg.get("intent") == "open", (t.key, st)     # no step follows a top-level switch
                continue
            p, h = WS.resolve(st, _ctx("power")), WS.resolve(st, _ctx("hr"))
            assert (p.type, p.lo, p.hi) == (h.type, h.lo, h.hi), (t.key, st)   # the basis doesn't change it
            assert p.type == tg["type"] and not p.err, (t.key, p.err)
            every.append(p.type)
            if st["kind"] == "work":
                main.append(p.type)
        main = main or every                         # e.g. an EZ run with no work step
        if t.key in NATIVE:
            assert main and set(main) <= {NATIVE[t.key]}, (t.key, main)
        if main:
            assert t.basis in main, (t.key, t.basis, main)


def test_no_loaded_carry_template_and_steep_grades():
    assert not any("負重爬坡" in t.title or "背包" in t.title for t in WT.TEMPLATES)
    g = [float(WT.BY_KEY[k].title.split("%")[0].split()[-1]) for k in ("steep_5", "steep_10", "steep_15")]
    assert 12 < g[0] < g[1] < g[2] <= 15.0


def test_template_rows_and_categories():
    T = WS.templates()
    assert [c["id"] for c in T["cats"]] == ["easy", "quality", "test", "trail"]
    rows = {r["key"]: (g["cat"], g["sub"]) for g in T["groups"] for r in g["rows"]}
    assert rows["lib:pal_near"] == ("quality", "z3")
    assert rows["lib:pal_supra"] == ("quality", "z4") and rows["lib:seiler_4x8"] == ("quality", "z4")
    assert rows["lib:billat_3030"] == ("quality", "z5") and rows["lib:ronnestad_3015"] == ("quality", "z5")
    assert rows["t1a"] == ("quality", "z3") and rows["v1a"] == ("quality", "z5")
    assert rows["lib:friel_lthr30"][0] == "test" and rows["lib:stryd_cp_3_12"][0] == "test"
    assert rows["lib:dsw_classic"][0] == "trail" and rows["lib:downhill_ecc"][0] == "trail"
    for g in T["groups"]:
        assert g["cat"] in {c["id"] for c in T["cats"]}
        for r in g["rows"]:
            WS.normalize({"items": r["items"]})
            if r.get("full"):
                WS.normalize({"items": r["full"]})
    main = WT.main_of(WT.items_of(WT.BY_KEY["seiler_4x8"]))
    assert [x["kind"] for x in main] == ["repeat"]


def test_hrp_band_is_kept_and_used_on_hr():
    # (the auto band's own % LTHR stays for structures saved by the first template version)
    st = {"kind": "work", "dur": {"type": "time", "value": 480}, "target": band(1.02, 1.08, (1.00, 1.05))}
    d = WS.normalize({"items": [st]})
    assert d["items"][0]["target"]["hrp"] == [1.0, 1.05]
    r = WS.resolve(d["items"][0], _ctx("hr"))
    assert (r.lo, r.hi) == (168, 176)
    assert WS.resolve(d["items"][0], _ctx("power")).lo == 255


def test_lap_button_steps_with_an_estimate():
    d = WS.normalize({"items": WT.items_of(WT.BY_KEY["stryd_cp_3_12"])})
    lap = [r["st"] for r in WS.flat(d["items"]) if r["st"]["dur"]["type"] == "open"]
    assert lap and all(x["dur"].get("est") for x in lap)
    t = WS.totals(d, _ctx("power"))
    assert t["est"] and t["open"] == 0 and t["sec"] > 50 * 60 and "直到按下計圈" in t["est_note"]
    # a lap step without an estimate still adds nothing
    bare = WS.normalize({"items": [{"kind": "warm", "dur": {"type": "time", "value": 600}},
                                   {"kind": "work", "dur": {"type": "open"}}]})
    t2 = WS.totals(bare, _ctx("power"))
    assert t2["sec"] == 600 and t2["open"] == 1 and not t2["est"]


def _dist(m, target):
    return WS.normalize({"items": [{"kind": "work", "dur": {"type": "distance", "value": m}, "target": target}]})


def test_distance_time_from_the_athletes_speeds():
    easy = band(0.76, 0.80)              # mid 0.78 = the easy anchor
    thr = band(0.98, 1.02)               # mid 1.00 = threshold pace
    c = _ctx("power", v_easy=10.0)          # 10 km/h easy, 4:30/km threshold
    assert WS.totals(_dist(10000, easy), c)["sec"] == pytest.approx(3600, abs=2)
    assert WS.totals(_dist(1000, thr), c)["sec"] == pytest.approx(270, abs=2)
    mid = WS.totals(_dist(1000, band(0.87, 0.91)), c)["sec"]
    assert 270 < mid < 360
    # HR basis: the same steps through hr_to_p, still between the anchors
    hr = WS.totals(_dist(1000, thr), _ctx("hr", v_easy=10.0))["sec"]
    assert 240 < hr < 360
    # trail: effort distance at the athlete's EP speed (scaled by intensity)
    tr = _ctx("power", v_easy=10.0, ep_kmh=8.0, terrain="trail", climb_per_km=50)
    assert WS.totals(_dist(10000, easy), tr)["sec"] == pytest.approx(15 / 8 * 3600, abs=3)
    note = WS.totals(_dist(10000, easy), tr)["est_note"]
    assert "EP" in note and "8.0 km/h" in note and note.endswith("（推估）")
    # nothing known: 6:00/km
    none = WS.Ctx.of({"cp": 250.0}, "power")
    assert WS.totals(_dist(1000, easy), none)["sec"] == pytest.approx(360, abs=1)


def test_road_ignores_climb():
    c = WS.Ctx.of(FULL, "power", speeds={"v_easy": 10.0, "terrain": "road", "climb_per_km": 80})
    assert c.climb_per_km == 0.0 and c.terrain == "road"
