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
    assert [IL.structure(c[r]) for r in IL.RUNG_ORDER] == ["3×6 分", "3×8 分", "2×12 分", "5×2 分", "4×3 分",
                                                           "5×3 分", "4×4 分"]
    assert all((v.lo, v.hi) == (0.90, 0.95) for r, v in c.items() if r.startswith("z3"))
    assert (c["z5a"].lo, c["z5a"].hi, c["z5a"].rest_s, c["z5a"].rest_mode) == (1.06, 1.12, 120, "walk")
    assert (c["z5b"].rest_s, c["z5c"].rest_s, c["z5c"].rest_mode) == (180, 150, "walk")
    assert (c["z5d"].lo, c["z5d"].hi, c["z5d"].rest_s, c["z5d"].rest_mode) == (1.04, 1.08, 180, "jog")
    # every rung fits 45 min with the standard blocks; the full blocks (time enough) are longer
    assert all(IL.total_min(v, "std") <= 45 for v in c.values())
    assert [IL.total_min(c[r], "full") for r in ("z3a", "z3b", "z5d")] == [46, 53, 55]
    assert "Seiler 2013" not in " ".join(v.src for vs in IL.LIBRARY.values() for v in vs)
    # 徐國峰: Zone 5 reps ≥ 2 min in every equivalent variant
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
    assert IL.structure(v) == "2-3-4-3-2 分金字塔" and IL.title(IL.get("v1c")) == "VO2max 5×2 分上坡"
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
