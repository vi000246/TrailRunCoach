"""SP-812: the A layer's per-person parameters take engine/calibrate.py's fitted items too (research
doc §14.5 item 5) — the person's own fit only, read from the tenant without writing, every group and
field still of >= k people, and every field cut the one same way so no cut of the report can be
subtracted from another (§14.4 item 6). Synthetic people and tenants only."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import numpy as np
import pytest

from backend.engine import calibrate as C
from backend.research import a_layer as A
from backend.tests.test_research_a_layer_sp587 import _people
from backend.tests.test_research_pool_sp586 import (HOME, M_LAT, TODAY, TRACKS, _consent, _loader, _tenant,  # noqa: F401
                                                    _track, _user, _utc, server)

BETA = "calib.aet_heat_beta"
SLOPE = "calib.hr_power_slope"


def _entry(source="fitted", value=1.2, n=40, personal=1.5):
    e = {"value": value, "se": 0.1, "n": n, "fitted_at": "2026-10-01", "source": source}
    if personal is not None:
        e["personal"] = personal
        e["w"] = 0.8
    return e


def _settings(folder, entries: dict, user_id=1):
    con = sqlite3.connect(folder / "wko5coach.db")
    for name, e in entries.items():
        con.execute("INSERT INTO user_settings (user_id, key, value_json, updated_at) VALUES (?, ?, ?, '2026-10-01')",
                    (user_id, C.key(name) if not name.startswith("athlete.") else name, json.dumps(e)))
    con.commit()
    con.close()


# ---------------------------------------------------------------------------- which items, which value

def test_the_fields_are_the_track_three_and_every_item_a_persons_data_can_fit():
    fs = A.fields()
    assert fs[:3] == A.TRACK_FIELDS == ("easy_flat_speed_ms", "uphill_vam_mh", "drift_pct")
    fit_items = [n for n, it in C._registry().items() if not it.manual_only]
    assert list(fs[3:]) == [A.CALIB_PREFIX + n for n in fit_items] and len(fit_items) >= 10
    assert not any(A.CALIB_PREFIX + n in fs for n, it in C._registry().items() if it.manual_only)


def test_only_the_persons_own_fit_is_read_before_shrinkage(tmp_path):
    folder = tmp_path / "t"
    _tenant(folder, [])
    _settings(folder, {
        "aet_heat_beta": _entry(value=1.1, personal=1.4),                 # fitted: the personal fit
        "hr_power_slope": _entry(value=1.45, personal=None, n=20),        # fitted, no personal: the value
        "easy_run_if": _entry(source="default", value=None, n=4, personal=None),   # SP-807 count only
        "climb_divisor_run": _entry(source="user", value=90.0, personal=None),     # typed by hand
        "home_temp_c": _entry(n=3),                                       # below the item's min_n (10)
        "pack_daily_drop_kg": _entry(),                                   # manual-only: no fit
        "no_such_item": _entry(),
        "athlete.weight": {"x": 1},
    })
    _settings(folder, {"interval_in_band_tol": _entry()}, user_id=2)      # another athlete id
    before = (folder / "wko5coach.db").read_bytes()
    assert A.calib_params(folder) == {BETA: 1.4, SLOPE: 1.45}
    assert (folder / "wko5coach.db").read_bytes() == before              # read-only
    assert A.calib_params(tmp_path / "none") == {}


def test_a_broken_entry_is_no_parameter(tmp_path):
    folder = tmp_path / "t"
    _tenant(folder, [])
    con = sqlite3.connect(folder / "wko5coach.db")
    for name, raw in (("aet_heat_beta", "not json"), ("hr_power_slope", json.dumps([1])),
                      ("easy_run_if", json.dumps(_entry(personal=float("nan")) | {"personal": "x"})),
                      ("rpe_if_scale", json.dumps(_entry(n=True)))):
        con.execute("INSERT INTO user_settings (user_id, key, value_json, updated_at) VALUES (1, ?, ?, '2026-10-01')",
                    (C.key(name), raw))
    con.commit()
    con.close()
    assert A.calib_params(folder) == {}


def test_a_tenant_db_that_cannot_be_read_is_told_apart_from_one_without_a_fit(tmp_path):
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "wko5coach.db").write_bytes(b"not a database" * 100)
    assert A.calib_params(folder) is None                                # collect counts it as unreadable
    assert A.calib_params(tmp_path / "none") == {}                       # no DB: simply no fit


# ---------------------------------------------------------------------------- k and the one partition

def _with_calib(params, **cols):
    """Give each row the calibration columns: name -> list of values (None: the person has none)."""
    for name, vals in cols.items():
        for p, v in zip(params, vals):
            if v is not None:
                p[name] = v
    return params


@pytest.mark.parametrize("k", [5, 10])
def test_every_calibration_field_given_rests_on_k_people_and_shares_the_groups(k):
    params = _people({("female", "30-39"): 12, ("male", "30-39"): 12, ("male", "40-49"): 11})
    rng = np.random.default_rng(1)
    _with_calib(params, **{BETA: [float(rng.uniform(0.5, 2)) for _ in params],
                           SLOPE: [float(rng.uniform(1, 2)) if i % 4 else None for i in range(len(params))]})
    groups, held = A.aggregate_params(params, k)
    assert groups and all(g["n"] >= k for g in groups)
    for g in groups:
        for f in A.fields():
            assert g[f] is None or g[f]["n"] >= k, (g["sex"], g["age_band"], f)
    assert A.one_partition(groups, len(params))
    # the groups are the same with or without the calibration columns: no field gets a cut of its own
    plain, _ = A.aggregate_params(_people({("female", "30-39"): 12, ("male", "30-39"): 12, ("male", "40-49"): 11}), k)
    assert [(g["sex"], g["age_band"], g["n"]) for g in groups] == [(g["sex"], g["age_band"], g["n"]) for g in plain]


def test_a_thin_calibration_field_is_held_in_its_group_never_given_on_a_coarser_one():
    params = _people({("female", "30-39"): 6, ("male", "30-39"): 6})
    _with_calib(params, **{BETA: [1.0, 1.1, 1.2, None, None, None] + [1.5] * 6})   # 3 women have it
    groups, held = A.aggregate_params(params, 5)
    by = {g["sex"]: g for g in groups}
    assert by["female"][BETA] is None and by["male"][BETA]["n"] == 6
    assert {"output": "params", "group": "female 30-39", "field": BETA, "n": 3, "reason": "field_below_k"} in held
    assert [g["sex"] for g in groups] == ["female", "male"]              # no "all" row next to them
    assert A.one_partition(groups, 12)


def test_an_item_nobody_has_is_neither_given_nor_listed_as_held():
    groups, held = A.aggregate_params(_people({("male", "30-39"): 6}), 5)
    assert all(groups[0][f] is None for f in A.fields()[3:])
    assert not any(h.get("field", "").startswith(A.CALIB_PREFIX) for h in held)


def test_the_partition_check_catches_overlapping_cuts():
    def g(sex, band, n):
        return {"sex": sex, "age_band": band, "n": n}
    assert A.one_partition([g("all", "all", 11)], 11)
    assert A.one_partition([g("male", "30-39", 6), g("male", "50+", 5), g("female", "30-49", 6)], 17)
    assert not A.one_partition([g("all", "all", 11), g("male", "30-39", 8)], 11)      # all − male = female
    assert not A.one_partition([g("male", "30-49", 8), g("male", "40-49", 5)], 13)    # 30-49 − 40-49 = 30-39
    assert not A.one_partition([g("male", "30-39", 6)], 7)                           # someone left out
    assert not A.one_partition([g("male", "nonsense", 6)], 6)
    assert A._bands_of("50+") == ["50-59", "60+"] and A._bands_of("18-39") == ["18-29", "30-39"]


def test_the_report_lists_each_new_item_and_what_was_held_and_no_persons_value():
    params = _people({("female", "30-39"): 6, ("male", "30-39"): 6, ("male", "40-49"): 1})
    _with_calib(params, **{BETA: [round(0.5 + 0.01 * i, 4) for i in range(13)],
                           SLOPE: [1.3, 1.4, None, None, None, None] + [None] * 7})
    inp = A.AInput(params=params)
    inp.calib_withheld["refused_source"] = 2
    rep, groups, _grid = A.evaluate(inp, 5)
    assert rep["ok"]
    assert rep["fields"][BETA] == {"people": 13, "groups_given": 2, "groups_held": 0}
    assert rep["fields"][SLOPE] == {"people": 2, "groups_given": 0, "groups_held": 1}
    assert "calib.easy_run_if" not in rep["fields"]                      # nobody has it
    assert rep["calib_withheld_people"] == {"refused_source": 2}
    given = {g["group"]: g["fields_given"] for g in rep["outputs"]["params"]}
    assert given == {"female 30-39": list(A.TRACK_FIELDS) + [BETA], "male 30-49": list(A.TRACK_FIELDS) + [BETA]}
    text = json.dumps(rep)
    assert not any(str(round(0.5 + 0.01 * i, 4)) in text for i in range(13))     # no one's own value
    for gr in groups:                                                     # a calibration median keeps its digits
        if gr[BETA]:
            assert gr[BETA]["median"] == round(gr[BETA]["median"], 3)


# ---------------------------------------------------------------------------- collect, end to end

def test_collect_adds_the_fitted_items_and_leaves_them_out_for_a_refused_source(server, tmp_path):
    TRACKS.clear()
    far = (HOME[0] + 0.2, HOME[1])
    for i in range(7):
        u = _user(f"c{i}@x.tw", f"g-c{i}")
        _consent(u.id)
        d = TODAY - dt.timedelta(days=5 + i)
        acts = [(1, d, "upload", None, None)] + ([(2, d - dt.timedelta(days=1), "strava", None, None)] if i == 6 else [])
        _tenant(u.folder(), acts, sex="male")
        _settings(u.folder(), {"aet_heat_beta": _entry(personal=1.0 + 0.1 * i)})
        start = (far[0] - 4000 * M_LAT, far[1] + 0.0002 * i)
        TRACKS[(u.id, 1)] = _track([start, (far[0] + 4000 * M_LAT, far[1])], _utc(d), climb=0.1)
        if i == 6:
            TRACKS[(u.id, 2)] = TRACKS[(u.id, 1)]
    inp = A.collect(today=TODAY, rng=np.random.default_rng(2), loader=_loader)
    assert len(inp.params) == 7 and inp.calib_withheld == {"refused_source": 1}
    assert sorted(p.get(BETA) for p in inp.params if p.get(BETA) is not None) == pytest.approx(
        [1.0 + 0.1 * i for i in range(6)])
    rep, groups, _grid = A.evaluate(inp, 5)
    assert rep["ok"] and [g["n"] for g in groups] == [7]
    assert groups[0][BETA]["n"] == 6 and groups[0][BETA]["median"] == pytest.approx(1.25)
    rows = A.write(groups, path=tmp_path / "pool.db")
    got = sqlite3.connect(tmp_path / "pool.db").execute("SELECT field, n, n_values FROM a_params").fetchall()
    assert rows == len(got) and (BETA, 7, 6) in got and all(r[1] >= 5 and r[2] >= 5 for r in got)


def test_collect_counts_a_tenant_whose_calibration_cannot_be_read(server, monkeypatch):
    TRACKS.clear()
    far = (HOME[0] + 0.2, HOME[1])
    broken = set()
    for i in range(5):
        u = _user(f"r{i}@x.tw", f"g-r{i}")
        _consent(u.id)
        d = TODAY - dt.timedelta(days=5 + i)
        _tenant(u.folder(), [(1, d, "upload", None, None)], sex="female")
        _settings(u.folder(), {"aet_heat_beta": _entry(personal=1.0 + 0.1 * i)})
        TRACKS[(u.id, 1)] = _track([(far[0] - 4000 * M_LAT, far[1] + 0.0002 * i), (far[0] + 4000 * M_LAT, far[1])],
                                   _utc(d), climb=0.1)
        if i == 0:
            broken.add(str(u.folder()))
    real = A.calib_params
    monkeypatch.setattr(A, "calib_params",
                        lambda folder, *a, **kw: None if str(folder) in broken else real(folder, *a, **kw))
    inp = A.collect(today=TODAY, rng=np.random.default_rng(3), loader=_loader)
    assert len(inp.params) == 5 and inp.calib_withheld == {"unreadable": 1}   # still counted for the tracks
    assert sum(1 for p in inp.params if p.get(BETA) is not None) == 4
    rep, _groups, _grid = A.evaluate(inp, 5)
    assert rep["ok"] and rep["calib_withheld_people"] == {"unreadable": 1}
    assert rep["fields"][BETA] == {"people": 4, "groups_given": 0, "groups_held": 1}
