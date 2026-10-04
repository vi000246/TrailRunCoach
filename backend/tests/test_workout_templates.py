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
        assert t.purpose and len(t.purpose) <= 80 and "\n" not in t.purpose, t.key     # 訓練目的, one line
        assert t.cat != "quality" or WT.family_of(WT.items_of(t)), t.key
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
    assert [s["id"] for s in T["cats"][1]["subs"]] == ["aerobic", "vo2max", "speed"]
    assert [s["label"] for s in T["cats"][1]["subs"]] == ["有氧間歇", "VO2max 間歇", "速度"]
    rows = {r["key"]: (g["cat"], g["sub"]) for g in T["groups"] for r in g["rows"]}
    assert rows["lib:pal_near"] == ("quality", "aerobic") and rows["lib:pfitz_lt"] == ("quality", "aerobic")
    assert rows["lib:pal_supra"] == ("quality", "aerobic") and rows["lib:seiler_4x8"] == ("quality", "aerobic")
    assert rows["lib:pal_vo2"] == ("quality", "vo2max")         # was filed under 四區 although named VO2max
    assert rows["lib:billat_3030"] == ("quality", "vo2max") and rows["lib:ronnestad_3015"] == ("quality", "vo2max")
    assert rows["lib:daniels_i"] == ("quality", "vo2max") and rows["lib:daniels_r"] == ("quality", "speed")
    assert rows["t1a"] == ("quality", "aerobic") and rows["v1a"] == ("quality", "vo2max")
    assert rows["x3015"] == ("quality", "vo2max") and rows["t3b"] == ("quality", "aerobic")
    assert rows["lib:friel_lthr30"][0] == "test" and rows["lib:stryd_cp_3_12"][0] == "test"
    assert rows["lib:dsw_classic"][0] == "trail" and rows["lib:downhill_ecc"][0] == "trail"
    for g in T["groups"]:
        assert g["cat"] in {c["id"] for c in T["cats"]}
        for r in g["rows"]:
            WS.normalize({"items": r["items"]})
            if r.get("full"):
                WS.normalize({"items": r["full"]})
    # every row of a family tab carries that family and a purpose; ladder rows' purpose by family
    for g in T["groups"]:
        if g["cat"] == "quality":
            assert all(r["family"]["id"] == g["sub"] and r["purpose"] for r in g["rows"]), g["title"]
    lad = {r["key"]: r for g in T["groups"] for r in g["rows"]}
    assert lad["t3b"]["family"]["sub"] == "tempo" and lad["t3b"]["purpose"] == WT.PURPOSE["long_tempo"]
    assert lad["x3015"]["purpose"] == WT.PURPOSE["short"] and lad["v1a"]["purpose"] == WT.PURPOSE["vo2max"]
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


# ---------------- 強度課的家族 (family_of / classify) ----------------

def _reps(n, work, rest, target):
    b = WT.B()
    return [b.rep(n, [b.t("work", work, target), b.t("rest", rest, WT.OPEN)], False)]


def fam(items, th=None):
    f = WT.family_of(items, th)
    return (f["id"], f["sub"]) if f else None


def test_family_intensity_first_101_percent_cp():
    # ≤ 101 % CP (band middle) = 有氧間歇 whatever the rep length; just above = not
    assert fam(_reps(3, 480, 120, WT.pw(0.98, 1.04))) == ("aerobic", "cruise")      # mid 1.01
    assert fam(_reps(3, 480, 120, WT.pw(0.99, 1.04))) == ("aerobic", "supra")       # mid 1.015, reps > 5′
    assert fam(_reps(4, 180, 180, WT.pw(0.99, 1.04))) == ("vo2max", None)
    assert fam(_reps(4, 180, 180, WT.pw(0.98, 1.04))) == ("aerobic", "cruise")      # < 6′ at threshold = 巡航
    # HR (× LTHR, line 1.02) and pace (× T pace, not faster than T) say the same
    assert fam(_reps(4, 180, 180, WT.hr(1.00, 1.04))) == ("aerobic", "cruise")
    assert fam(_reps(4, 180, 180, WT.hr(1.00, 1.06))) == ("vo2max", None)
    assert fam(_reps(4, 180, 180, WT.pace(0.99, 1.01))) == ("aerobic", "cruise")
    assert fam(_reps(4, 180, 180, WT.pace(0.92, 0.95))) == ("vo2max", None)


def test_family_rep_length_boundaries():
    z5 = WT.pw(1.05, 1.10)
    assert WT.classify(120, 120, "above") == ("vo2max", None)          # 2′ with 1:1 = VO2max (2–5′)
    assert WT.classify(120, 240, "above") == ("speed", None)           # 2′ with a long rest = 速度
    assert WT.classify(300, 300, "above") == ("vo2max", None)          # 5′ still VO2max
    assert WT.classify(301, 180, "above") == ("aerobic", "supra")      # > 5′ above threshold
    assert WT.classify(360, 90, "thr") == ("aerobic", "cruise")        # 6′
    assert WT.classify(899, 120, "thr") == ("aerobic", "cruise")
    assert WT.classify(900, 180, "thr") == ("aerobic", "tempo")        # 15′
    assert WT.classify(1500, None, "thr") == ("aerobic", "tempo")      # one continuous block
    assert WT.classify(600, 120, "easy") is None and WT.classify(0, None, "thr") is None
    assert fam(_reps(5, 120, 120, z5)) == ("vo2max", None)
    assert fam(_reps(4, 300, 180, z5)) == ("vo2max", None)
    assert fam(_reps(2, 900, 180, WT.pw(0.88, 0.95))) == ("aerobic", "tempo")
    assert fam(_reps(3, 360, 90, WT.pw(0.90, 0.95))) == ("aerobic", "cruise")


def test_family_short_reps_by_rest():
    # 30/30 (rest = rep) → VO2max 短間歇; R 300 m / strides / hill sprints (rest ≥ 2×) → 速度
    assert fam(_reps(16, 30, 30, WT.pace(0.86, 0.90))) == ("vo2max", "short")
    assert fam(_reps(8, 75, 180, WT.pace(0.85, 0.89))) == ("speed", None)
    assert fam(_reps(4, 20, 40, WT.OPEN)) == ("speed", None)            # strides: no target
    assert fam(_reps(8, 10, 180, WT.OPEN)) == ("speed", None)
    assert fam(_reps(3, 600, 120, WT.OPEN)) is None                     # an all-out test bout: no family
    assert fam(_reps(6, 60, 60, WT.pw(1.20, 1.30))) == ("speed", None)  # > 116 % CP
    assert fam([WT.B().t("work", 45 * 60, WT.AET)]) is None             # an easy run


def test_family_of_distance_reps_and_absolute_targets():
    b = WT.B()
    km = [b.rep(5, [b.d("work", 1000, WT.pace(0.93, 0.96)), b.t("rest", 150, WT.OPEN)], False)]
    assert fam(km) == ("vo2max", None)                                  # ~4.5′ at 5K pace
    hr_abs = _reps(3, 600, 120, {"type": "hr", "mode": "abs", "lo": 160, "hi": 166})
    assert fam(hr_abs) is None                                          # bpm without LTHR: unknown
    assert fam(hr_abs, {"lthr": 168}) == ("aerobic", "cruise")
    assert fam(hr_abs, {"lthr": 155}) == ("aerobic", "supra")


def test_session_family_is_computed_for_quality_sessions_only():
    v = WT.session_family({"kind": "quality", "variant_key": "v1a", "title": "VO2max 5×2 分", "minutes": 60})
    assert v["id"] == "vo2max" and v["text"] == "VO2max 間歇"
    t = WT.session_family({"kind": "quality", "variant_key": "t3b", "minutes": 50})
    assert (t["id"], t["sub"], t["text"]) == ("aerobic", "tempo", "有氧間歇・長 tempo")
    txt = WT.session_family({"kind": "quality", "title": "閾值 2×15 分", "minutes": 60,
                             "detail": "暖身 15 分，休 3 分", "target": ""})
    assert (txt["id"], txt["sub"]) == ("aerobic", "tempo")
    own = WT.session_family({"kind": "quality", "steps": {"items": _reps(16, 30, 30, WT.pace(0.86, 0.90))}})
    assert own["id"] == "vo2max"
    assert WT.session_family({"kind": "easy", "minutes": 40}) is None
