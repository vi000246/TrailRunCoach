"""The interval session library (engine/interval_library.py) —
docs/research/interval-prescription.md §A5.3, Part C."""
import pytest

from backend.engine import interval_library as IL
from backend.engine import plan_prefs as PP
from backend.settings import repository as SR

# §C4 「總時間」 with the §C3 standard blocks (Z3 warm-up 12, Z5 15, cool-down 5); the main set
# no longer counts a rest after the last rep (§A5.2-3). t2b / tpb / v4b are the .5-minute rows.
DOC_TOTALS = {"t1a": 38, "t1b": 40, "t1c": 39, "t1d": 37, "t2a": 45, "t2b": 45.5, "t2c": 46, "t2d": 45,
              "t3a": 43, "t3b": 41, "t3c": 44, "tpa": 42, "tpb": 41.5, "tpc": 42,
              "v1a": 38, "v1b": 36, "v1c": 38, "v1d": 36, "v2a": 41, "v2b": 42, "v2c": 41, "v2d": 38,
              "v3a": 45, "v3b": 42, "v3c": 45, "v3d": 45, "v4a": 45, "v4b": 46.5, "v4c": 45, "x3015": 42}


@pytest.mark.parametrize("key, total", sorted(DOC_TOTALS.items()))
def test_each_rows_total_time(key, total):
    assert IL.total_min(IL.get(key), "std") == pytest.approx(total)


def test_the_corrected_ladder_canonicals():
    c = {r: IL.canonical(r) for r in IL.RUNG_ORDER}
    assert [IL.structure(c[r]) for r in IL.RUNG_ORDER] == ["2×15 分", "3×12 分", "2×20 分", "連續 30 分",
                                                           "3×6 分", "3×8 分", "2×12 分", "5×2 分", "4×3 分",
                                                           "5×3 分", "4×4 分"]
    assert all((v.lo, v.hi) == (0.90, 0.95) for r, v in c.items() if r.startswith("z3"))
    # SP-31 the Zone 3 track: 88–95 % CP (continuous 88–92), reps ≥ 12 min, rests ≤ 4 min
    assert all(v.cls == "Z3sub" and v.lo == 0.88 and min(v.works) >= 720 and v.rest_s <= 240
               for r, v in c.items() if r in IL.Z3_TRACK)
    assert [IL.tiz_s(c[r]) // 60 for r in IL.Z3_TRACK] == [30, 36, 40, 30]
    assert IL.PREV_RUNG["a1"] == "z3c" and IL.PREV_RUNG["a2"] == "a1" and IL.PREV_RUNG["z5a"] == "z3c"
    assert [IL.track_of(r) for r in ("a1", "z3b", "tp", "z5c", "x", None)] == ["z3", "z3", "z3", "z5", "z5", None]
    c = {r: IL.canonical(r) for r in IL.CRUISE_RUNGS + IL.Z5_TRACK}
    assert (c["z5a"].lo, c["z5a"].hi, c["z5a"].rest_s, c["z5a"].rest_mode) == (1.06, 1.12, 120, "walk")
    assert (c["z5b"].rest_s, c["z5c"].rest_s, c["z5c"].rest_mode) == (180, 150, "walk")
    assert (c["z5d"].lo, c["z5d"].hi, c["z5d"].rest_s, c["z5d"].rest_mode) == (1.04, 1.08, 180, "jog")
    # every rung fits 45 min with the standard blocks; the full blocks (time enough) are longer
    assert all(IL.total_min(v, "std") <= 45 for v in c.values())
    assert [IL.total_min(c[r], "full") for r in ("z3a", "z3b", "z5d")] == [46, 53, 55]
    assert "Seiler 2013" not in " ".join(v.src for vs in IL.LIBRARY.values() for v in vs)
    # 台灣教練: Zone 5 reps ≥ 2 min in every equivalent variant
    assert all(min(v.works) >= 120 for r in ("z5a", "z5b", "z5c", "z5d") for v in IL.LIBRARY[r])


def test_every_listed_variant_is_equivalent_to_its_rungs_canonical_and_30_15_is_not():
    for rung, vs in IL.LIBRARY.items():
        assert 2 <= len(vs) - 1 <= 3 and sum(v.canonical for v in vs) == 1, rung
        assert any(v.terrain == "hill" for v in vs), rung                    # an uphill version per rung
        for v in vs:
            ok, why = IL.equivalent(v)
            assert ok, (v.key, why)
            for u in vs:                                                    # the C4 rows are mutually equivalent
                assert IL.equivalent(v, u)[0] or IL.equivalent(u, v)[0], (v.key, u.key)
    ok, why = IL.equivalent(IL.get("x3015"), IL.get("v4a"))
    assert not ok and any("2 分" in w for w in why)
    # different classes are never equivalent
    assert not IL.equivalent(IL.get("t2a"), IL.get("v1a"))[0]
    assert not IL.equivalent(IL.get("tpa"), IL.get("t2a"))[0]
    # a deeper rep of the same length is not: 4×4′ @ 110 % uses 37 % of W′ vs 19 % (§C2-5)
    deep = IL.replace(IL.get("v4a"), key="deep", lo=1.08, hi=1.12)
    assert not IL.equivalent(deep, IL.get("v4a"))[0]


def test_tiz_main_blocks_and_text():
    v = IL.get("v3b")
    assert IL.tiz_s(v) == 840 and IL.main_s(v) == 840 + 4 * 120
    assert IL.structure(v) == "2-3-4-3-2 分金字塔" and IL.title(IL.get("v1c")) == "VO2max 間歇 5×2 分上坡"
    assert IL.structure(IL.get("v1b")) == "4×2:30" and IL.structure(IL.get("t1d")) == "連續 20 分"
    assert IL.structure(IL.get("x3015")) == "2 組 × 13×30 秒／15 秒"
    b = IL.blocks(IL.get("v4a"), "std")
    assert b["warm_min"] == 15 and [c for c, _, _ in b["warm"]] == ["city", "drills", "strides"]
    assert IL.blocks(IL.get("v4a"), "min")["warm_min"] == 13 and IL.blocks(IL.get("t2a"), "min")["warm_min"] == 10
    assert IL.blocks(IL.get("t2a"), "full")["warm_min"] == 15 and IL.blocks(IL.get("t2a"), "full")["cool_min"] == 10
    # the city part and the cool-down are 課表偏好
    p = PP.Prefs(warmup_commute_min=12, cooldown_min=10)
    assert IL.total_min(IL.get("t2a"), "std", p) == 12 + 2 + 28 + 10
    assert not p.active and PP.from_settings(p.settings()) == p
    assert SR.DEFAULTS["plan.prefs.warmup_commute_min"] == 10 and SR.DEFAULTS["plan.prefs.cooldown_min"] == 5
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.cooldown_min", 40)
    assert IL.with_reps(IL.get("v3b"), 3).pattern == (120, 180, 240)
    assert IL.with_reps(IL.get("t2a"), 2).n == 2
    rows = IL.library_table()
    assert {r["key"] for r in rows} == set(IL.ALL) and all({"full", "std", "min"} <= set(r) for r in rows)


# ---------------------------------------------------------------------------
# S2: choosing the variant and fitting it into the day's cap (§C5.2–§C5.3)
# ---------------------------------------------------------------------------

def _done(rung, key, outcome="met"):
    return {"rung_key": rung, "variant_key": key, "state": "done", "outcome": outcome, "day": "2026-09-01"}


def test_time_enough_means_the_standard_full_length_session():
    f = IL.fit("z3b", None)
    assert f["variant"].key == "t2a" and f["level"] == "full" and f["equiv"] and f["progress"]
    assert f["reason"].startswith("時間足夠 → 標準版")
    assert IL.total_min(f["variant"], "full") == 53                  # 15 + 28 + 10
    f = IL.fit("z5d", 60)                                             # a cap that fits the full session
    assert (f["variant"].key, f["level"]) == ("v4a", "full")
    s = IL.session_for(f, {"cp": 204.0, "lthr": 170.0, "aet": 150.0})
    assert s["title"] == "VO2max 間歇 4×4 分" and s["minutes"] == 55 and s["variant_key"] == "v4a"
    assert s["detail"].startswith("時間足夠 → 標準版") and "暖身 20 分" in s["detail"] and "緩和 10 分" in s["detail"]
    assert "212–220 W" in s["target"] and "心率" in s["target"]


@pytest.mark.parametrize("rung, cap, key, level, equiv", [
    ("z3b", 45, "t2a", "std", True),        # 12 + 28 + 5 = 45: the standard version, blocks at §C3
    ("z3b", 50, "t2a", "std", True),        # 53 with the full blocks > 50
    ("z5c", 43, "v3a", "min", True),        # 13 + 25 + 5
    ("z5c", 42, "v3b", "min", True),        # no standard-length variant fits: the shorter equivalent
    ("z5a", 36, "v1a", "min", True),
    ("z3b", 40, "t2a", "min", False),       # 2×8′ = 67 % of the TIZ: 縮量版
    ("z5d", 40, "v4a", "min", False),       # 3×4′ = 75 %
])
def test_the_cap_rules(rung, cap, key, level, equiv):
    f = IL.fit(rung, cap)
    assert (f["variant"].key, f["level"], f["equiv"]) == (key, level, equiv), f["reason"]
    assert IL.total_min(f["variant"], f["level"]) <= cap
    assert f["reason"].startswith(f"平日上限 {cap} 分 → ")
    if not equiv:
        assert f["reps"] is not None and "達標也只算維持" in f["reason"] and not f["progress"]


def test_fewer_reps_with_85_percent_of_the_tiz_still_count():
    # §C4 v4b note: 8×2′ cut to 7×2′ keeps 14 of 16 min (88 %) — inside the ±15 % rule
    r = IL.with_reps(IL.get("v4b"), 7)
    assert IL.tiz_s(r) / IL.tiz_s(IL.get("v4b")) >= IL.EQUIV_TIZ and IL.equivalent(r, IL.canonical("z5d"))[0]


def test_rotation_and_first_exposure():
    # first time on a rung: the studied protocol even if another variant was used elsewhere
    assert IL.fit("z5b", None, [_done("z5a", "v1b")])["variant"].key == "v2a"
    # after v2a, an equivalent standard-length variant; the reason says it
    f = IL.fit("z5b", None, [_done("z5b", "v2a")])
    assert f["variant"].key == "v2b" and "上次做 4×3 分，這次換 6×2 分（同等，不影響進階）" in f["reason"]
    # not within the last 2 sessions of the rung, and not the one judged 未適應 last time
    # (with time enough the pool is the flat standard-length variants: v2a / v2b alternate;
    # the shorter 3×4′ is only the tight-cap fallback)
    f = IL.fit("z5c", None, [_done("z5c", "v3a"), _done("z5c", "v3c")])
    assert f["variant"].key == "v3a"                       # both used recently: the canonical on the tie
    # both recent, the one judged 未適應 last time loses the tie
    assert IL.fit("z5b", None, [_done("z5b", "v2b"), _done("z5b", "v2a", "unadapted")])["variant"].key == "v2b"
    assert IL.fit("z3b", None, [_done("z3b", "t2a"), _done("z3b", "t2b")])["variant"].key == "t2c"
    assert IL.fit("z3b", None, [_done("z3b", "t2c"), _done("z3b", "t2a"), _done("z3b", "t2b")])["variant"].key == "t2c"
    # uphill versions only when the prefs / a mountain goal allow them; then every 2nd session
    assert all(IL.fit(r, None, [_done(r, IL.canonical(r).key)])["variant"].terrain == "flat" for r in IL.LIBRARY)
    hill = PP.Prefs(terrain_quality="hill")
    assert IL.fit("z5c", None, [_done("z5c", "v3a")], hill)["variant"].key == "v3d"
    assert IL.fit("z5c", None, [_done("z5c", "v3a")], mountain=True)["variant"].terrain == "hill"
    assert IL.fit("z5c", None, [_done("z5c", "v3a")], PP.Prefs(terrain_quality="flat"), mountain=True)[
        "variant"].terrain == "flat"


def test_another_day_then_the_step_before():
    # 30 min on weekdays: 3×8′ can't be cut to fit (2×8′ = 33′) → Saturday without a cap
    f = IL.fit("z3b", 30, alt_caps=[("週六", None, 5)])
    assert f["action"] == "move" and f["move_wd"] == 5 and f["variant"].key == "t2a" and f["level"] == "full"
    assert f["reason"].startswith("平日上限 30 分 放不下 T2 → 改到週六")
    # no other day: the rung before as maintenance, with the doc's warning
    f = IL.fit("z3b", 30)
    assert f["action"] == "back" and f["rung"] == "z3a" and not f["equiv"] and not f["progress"] and f["warn"]
    assert "放不下 T2 的 3×8 分（需要 43 分以上）：本週改排 T1 的" in f["reason"] and "不算進階" in f["reason"]
    assert "把平日上限調到 43 分，或把品質課改到週末" in f["reason"]


def test_the_state_machines_tweak_is_applied_before_fitting():
    f = IL.fit("z5a", None, adj={"rest_add": 1})
    assert f["variant"].rest_s == 180 and f["adj"] == {"rest_add": 1}
    assert IL.resolve("v1a", None, {"rest_add": 1}).rest_s == 180
    f = IL.fit("z3a", None, adj={"power": 0.95})
    assert f["variant"].lo == round(0.90 * 0.95, 3)


@pytest.mark.parametrize("cap", [45, 50, None])
def test_the_second_rung_stays_the_second_rung_under_any_cap(cap):
    # §C5.5 S2: 「上限 45／50／無：z3b 不會變成 z3a」 — the old trim_quality cut 4×8′ to 3×8′ and the
    # title then read as the first rung
    from backend.engine import overview as O
    from backend.engine import quality_gate as QG
    gate = {"state": "none", "mode": "auto", "guard": {}, "dose": {"step": 1}, "lthr": {"default": False}}
    dec = {"spec": QG.CRUISE[1], "advance": True}
    prefs = PP.Prefs(cap_weekday=cap) if cap else PP.Prefs()
    q_cap, alt = O.quality_caps(prefs, 6)
    s = O._gate_session(gate, dec, {"cp": 204.0, "lthr": 170.0, "aet": 150.0}, 5.0, prefs, [], False, q_cap, alt)
    assert s["rung_key"] == "z3b" and s["equiv"] and s["progress"]
    assert s["minutes"] <= (cap or 999) and s["variant_key"] in {v.key for v in IL.LIBRARY["z3b"]}
    assert s["detail"].startswith("時間足夠 → 標準版" if cap is None else f"平日上限 {cap} 分 → 標準版")
    # 課表偏好 shape() leaves a fitted variant alone (no trim, no title suffix)
    c = PP.Ctx(kind="base", mode="base", allow_quality=True, rates={"road": 50.0})
    out = PP.shape([{**s, "day": None, "done": False, "done_by": None}], 300, PP.Prefs(cap_weekday=40, terrain_quality="flat"), c)
    q = next(x for x in out if x["kind"] == "quality")
    assert (q["title"], q["minutes"]) == (s["title"], s["minutes"])


@pytest.mark.parametrize("cap, hours, key, rung, counts", [
    (None, 6.0, "a1a", "a1", True),          # time enough, 10 % of 6 h = 36′ ≥ 30′: the standard 2×15′
    (45, 6.0, "a1c", "a1", True),            # weekday cap 45: the equivalent 1×26′ continuous
    (None, 4.0, "t1a", "a1", True),          # 10 % of 4 h = 24′ < 30′: 巡航版 T1 3×6′, still the A1 session
    (40, 4.0, "t1a", "a1", True),
])
def test_the_zone3_track_under_the_cap_and_the_weekly_volume(cap, hours, key, rung, counts):
    # SP-31: A1 2×15′ (30′ in zone) — the weekday cap picks an equivalent; over 10 % of the week
    # (Daniels) the 巡航版 of the same position stands in and still counts (rung_key a1, equiv)
    from backend.engine import overview as O
    from backend.engine import quality_gate as QG
    gate = {"state": "none", "mode": "auto", "guard": {}, "lthr": {"default": False}}
    prefs = PP.Prefs(cap_weekday=cap) if cap else PP.Prefs()
    q_cap, alt = O.quality_caps(prefs, 6)
    notes = []
    s = O._gate_session(gate, {"spec": QG.Z3[0], "track": "z3", "first": False}, {"cp": 250.0}, hours, prefs, [],
                        False, q_cap, alt, notes)
    assert (s["variant_key"], s["rung_key"], s["equiv"], s["progress"]) == (key, rung, counts, counts)
    assert s["minutes"] <= (cap or 999)
    if key.startswith("t"):
        assert "巡航版" in s["detail"] and notes and notes[0]["src"] == "z3" and "10%" in notes[0]["text"]
    else:
        assert not notes
    # the track's first session starts at 5 % (UA): 6 h → 18′ → T1 3×6′
    s = O._gate_session(gate, {"spec": QG.Z3[0], "track": "z3", "first": True}, {"cp": 250.0}, 6.0, PP.Prefs())
    assert s["variant_key"] == "t1a" and s["rung_key"] == "a1" and "5%" in s["detail"]
    assert QG.cruise_for("a3", 30.0) == "z3c" and QG.cruise_for("a3", 20.0) == "z3a" and QG.cruise_for("a1", 1) == "z3a"


def test_quality_caps_weekend_alternatives():
    from backend.engine import overview as O
    p = PP.Prefs(cap_weekday=40, cap_long=120, long_day="sun")
    cap, alt = O.quality_caps(p, 6)
    assert cap == 40 and alt == []                         # Saturday is 1 day from the Sunday long run
    p = PP.Prefs(cap_weekday=40, cap_long=120, days=(True, True, True, True, False, True, True))
    assert O.quality_caps(p, 2)[1] == [("週六", 120, 5), ("週日", 120, 6)]
    assert O.quality_caps(PP.Prefs(), 6) == (None, [])


def test_the_swap_drawer_and_the_templates():
    d = IL.drawer("z5c", cp=204.0, cap=42, current="v3a")
    eq = {r["key"]: r for r in d["equivalent"]}
    assert set(eq) == {"v3a", "v3b", "v3c", "v3d"} and all(r["equiv"] for r in eq.values())
    assert not eq["v3a"]["fits"] and "超過今天上限 42 分" in eq["v3a"]["why_not"]      # 43 min at the floor
    assert eq["v3b"]["fits"] and eq["v3b"]["split"] == "暖身 15（輕鬆跑 10＋drill 2＋快步跑 3） · 主課 22 · 緩和 5 ＝ 42 分"
    other = {r["key"]: r["consequence"] for r in d["other"]}
    assert other["v2a"].startswith("上一階（V2）：維持，不算進階") and "30/15" in other["x3015"]
    assert "這週沒有 5 區" in other["t2a"] and any("縮量版" in r["consequence"] for r in d["other"])
    assert d["recommended_key"] == "v3b"
    t = IL.templates(204.0, 45, None, (), "z3b")
    assert t["recommended_key"] == "t2a" and t["recommended_reason"].startswith("推薦（依你目前的階段與時間上限）")
    keys = {r["key"] for g in t["groups"] for r in g["rows"]}
    assert keys == set(IL.ALL) and any(r["terrain"] == "hill" for g in t["groups"] for r in g["rows"])
    assert all(r["hr"] for g in t["groups"] for r in g["rows"])


def test_a_user_swap_keeps_the_rung_and_says_whether_it_counts():
    th = {"cp": 204.0, "lthr": 170.0, "aet": 150.0}
    p = IL.variant_patch("v3c", "z5c", th, None, 45)
    assert p["equiv"] and p["swap"] == "user" and p["rung_key"] == "z5c" and "同等，不影響進階" in p["swap_reason"]
    p = IL.variant_patch("v2a", "z5c", th, None, 45)          # the step before
    assert not p["equiv"] and p["rung_key"] == "z5c" and "這次不算進階" in p["swap_reason"]
    p = IL.variant_patch("x3015", "z5d", th)
    assert not p["equiv"]
    p = IL.variant_patch("v3a", "z5c", th, None, None, reps=4)        # 12 of 15 min = 80 % < 85 %
    assert not p["equiv"] and p["variant_reps"] == 4
    with pytest.raises(ValueError):
        IL.variant_patch("nope", "z5c", th)


def test_steps_carry_the_blocks_and_the_walk_rests():
    st = IL.steps(IL.get("v1a"), "std")
    kinds = [s["kind"] for s in st]
    assert kinds[:3] == ["warm"] * 3 and kinds[-1] == "cool"
    assert kinds.count("work") == 5 and kinds.count("rest") == 4                  # no rest after the last rep
    assert all(s["mode"] == "walk" for s in st if s["kind"] == "rest")
    assert sum(s["s"] for s in st) == IL.total_min(IL.get("v1a"), "std") * 60
    x = IL.steps(IL.get("x3015"), "std")
    assert sum(1 for s in x if s["kind"] == "rest" and s["s"] == 180) == 1


@pytest.mark.parametrize("rung, cap, key", [("a1", 40, "t1a"), ("a2", 45, "t2a"), ("a3", 45, "t3a")])
def test_the_weekday_cap_cruise_fallback_counts_like_the_volume_one(rung, cap, key):
    # owner 2026-10-04 (SP-31 follow-up): a Zone 3 rung the weekday cap can't fit becomes the 巡航版 of
    # the same position that fits, stored under the A rung (equiv) — its 達標 moves the ladder, the
    # same rule as the 10 %-volume fallback
    from backend.engine import overview as O
    from backend.engine import quality_gate as QG
    gate = {"state": "none", "mode": "auto", "guard": {}, "lthr": {"default": False}}
    prefs = PP.Prefs(cap_weekday=cap)
    q_cap, alt = O.quality_caps(prefs, 6)
    notes = []
    spec = next(r for r in QG.Z3 if r[0] == rung)
    s = O._gate_session(gate, {"spec": spec, "track": "z3", "first": False}, {"cp": 250.0}, 8.0, prefs, [],
                        False, q_cap, alt, notes)
    assert (s["variant_key"], s["rung_key"], s["equiv"], s["progress"]) == (key, rung, True, True)
    assert s["minutes"] <= cap and "巡航版" in s["detail"] and notes[0]["src"] == "z3" and "算這一階" in notes[0]["text"]
    # done and 達標 → the Zone 3 step moves (dose_step reads the stored row like the volume fallback's)
    v = IL.get(key)
    cp = 250.0
    bouts = [{"power": cp * (v.lo + v.hi) / 2, "duration_s": w, "hr_at60": None} for w in v.works]
    at = [r[0] for r in QG.Z3].index(rung)
    prior = [{"date": f"2026-09-0{i + 1}", "variant_key": IL.canonical(QG.Z3[i][0]).key, "rung_key": QG.Z3[i][0],
              "equiv": True, "track": "z3", "cp": cp,
              "bouts": [{"power": cp * 0.92, "duration_s": w, "hr_at60": None} for w in IL.canonical(QG.Z3[i][0]).works]}
             for i in range(at)]
    row = {"date": "2026-09-20", "variant_key": key, "rung_key": rung, "equiv": True, "track": "z3", "cp": cp,
           "bouts": bouts}
    d = QG.dose_step(prior + [row], None, "z3")
    assert d["step"] == at + 1 and d["met"] == at + 1
