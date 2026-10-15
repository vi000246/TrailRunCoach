"""The research pool's A layer (SP-587, backend/research/a_layer.py): every group or cell given has
at least k people (k settable), smaller ones are merged or withheld, and the operator's report lists
the head counts given and what was held back. Synthetic people only."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import numpy as np
import pytest

from backend.research import a_layer as A
from backend.research import deid as D
from backend.tests.test_research_pool_sp586 import (HOME, M_LAT, TODAY, TRACKS, _consent, _loader, _north,  # noqa: F401
                                                    _tenant, _track, _user, _utc, server)


def _p(sex, band, speed=3.0, vam=600.0, drift=2.0):
    return {"sex": sex, "age_band": band, "easy_flat_speed_ms": speed, "uphill_vam_mh": vam, "drift_pct": drift}


def _people(spec: dict) -> list[dict]:
    """{(sex, band): n} -> that many synthetic parameter rows with spread values."""
    rng = np.random.default_rng(3)
    out = []
    for (sex, band), n in spec.items():
        out += [_p(sex, band, float(rng.uniform(2.5, 3.5)), float(rng.uniform(400, 900)), float(rng.uniform(0, 6)))
                for _ in range(n)]
    return out


def _given_ok(groups, k):
    return all(g["n"] >= k for g in groups) and all(
        g[f] is None or g[f]["n"] >= k for g in groups for f in A.PARAM_FIELDS)


@pytest.mark.parametrize("k", [5, 10])
def test_every_group_given_has_at_least_k_people(k):
    params = _people({("female", "18-29"): 3, ("female", "30-39"): 9, ("female", "40-49"): 4,
                      ("male", "30-39"): 14, ("male", "40-49"): 8, ("male", "50-59"): 2, ("male", "60+"): 1})
    groups, held = A.aggregate_params(params, k)
    assert groups and _given_ok(groups, k)
    assert sum(g["n"] for g in groups) == len(params)                    # one partition: nobody twice
    assert any(h["reason"] == "bands_merged" for h in held)


def test_small_bands_merge_with_a_neighbour():
    params = _people({("male", "30-39"): 6, ("male", "50-59"): 3, ("male", "60+"): 2,
                      ("female", "30-39"): 7})
    groups, held = A.aggregate_params(params, 5)
    labels = {(g["sex"], g["age_band"]): g["n"] for g in groups}
    assert labels == {("female", "30-39"): 7, ("male", "30-39"): 6, ("male", "50+"): 5}
    merged = [h for h in held if h["reason"] == "bands_merged"]
    assert merged == [{"output": "params", "group": "male 50+", "n": 5, "reason": "bands_merged",
                       "bands": ["50-59", "60+"], "band_n": [3, 2]}]


def test_a_sex_below_k_gives_only_the_all_people_row():
    """'all' next to 'male' would give 'female' away by subtraction: one cut only."""
    params = _people({("male", "30-39"): 8, ("female", "30-39"): 3})
    groups, held = A.aggregate_params(params, 5)
    assert [(g["sex"], g["age_band"], g["n"]) for g in groups] == [("all", "all", 11)]
    assert {h["group"] for h in held if h["reason"] == "sex_below_k_only_all_given"} == {"female", "male"}


def test_fewer_than_k_people_gives_nothing_and_a_thin_field_is_withheld():
    groups, held = A.aggregate_params(_people({("male", "30-39"): 4}), 5)
    assert groups == [] and held == [{"output": "params", "group": "all", "n": 4, "reason": "below_k"}]
    params = _people({("male", "30-39"): 6})
    for p in params[:3]:
        p["uphill_vam_mh"] = None                                         # only 3 people climbed
    groups, held = A.aggregate_params(params, 5)
    assert groups[0]["uphill_vam_mh"] is None and groups[0]["easy_flat_speed_ms"]["n"] == 6
    assert {"output": "params", "group": "male 30-39", "field": "uphill_vam_mh", "n": 3,
            "reason": "field_below_k"} in held


def test_the_report_lists_head_counts_given_and_held_without_places():
    inp = A.AInput(params=_people({("male", "30-39"): 6, ("female", "30-39"): 5, ("female", "40-49"): 1}))
    # grid input: 6 people share cell (1, 1), 2 share (2, 2), 1 alone in (3, 3)
    inp.cells = [{(1, 1): [3.0]} for _ in range(6)]
    inp.cells[0][(2, 2)] = [2.0]
    inp.cells[1][(2, 2)] = [2.0]
    inp.cells[2][(30000, 26000)] = [2.5]
    rep, groups, grid = A.evaluate(inp, 5)
    assert rep["ok"] and rep["k"] == 5 and rep["people"] == 12
    assert rep["outputs"]["params"] == [
        {"group": "female 30-49", "n": 6, "fields_given": list(A.PARAM_FIELDS)},
        {"group": "male 30-39", "n": 6, "fields_given": list(A.PARAM_FIELDS)}]
    assert rep["outputs"]["grid"] == {"cells": 1, "people_per_cell": {"6": 1}}
    assert rep["held"]["grid"] == {"cells": 2, "people_per_cell": {"1": 1, "2": 1}}
    assert "30000" not in json.dumps(rep) and "26000" not in json.dumps(rep)      # no place of a held cell
    assert [c["n_people"] for c in grid] == [6]
    # k = 10 (what other users would see): the same people give one row and no cell
    rep10, groups10, grid10 = A.evaluate(inp, 10)
    assert rep10["ok"] and [g["n"] for g in groups10] == [12] and grid10 == []


def test_collect_reads_a_allowed_people_only_and_writes_groups_of_k(server, tmp_path):
    from backend.accounts import store
    TRACKS.clear()
    far = (HOME[0] + 0.2, HOME[1])
    for i in range(6):
        u = _user(f"a{i}@x.tw", f"g-a{i}")
        _consent(u.id, a=(i != 5))                                         # the sixth opted out
        d = TODAY - dt.timedelta(days=5 + i)
        _tenant(u.folder(), [(1, d, "upload", None, None), (2, d, "trainingpeaks", None, None)],
                sex="male" if i % 2 else "female")
        start = (far[0] - 4000 * M_LAT, far[1] + 0.0002 * i)
        TRACKS[(u.id, 1)] = _track([start, (far[0] + 4000 * M_LAT, far[1])], _utc(d), climb=0.1)
        TRACKS[(u.id, 2)] = TRACKS[(u.id, 1)]
    inp = A.collect(today=TODAY, rng=np.random.default_rng(2), loader=_loader)
    assert len(inp.params) == 5 and inp.skipped["no_consent"] == 1
    assert inp.refused == {"trainingpeaks": 5}
    rep, groups, grid = A.evaluate(inp, 5)
    assert rep["ok"] and [g["n"] for g in groups] == [5]                  # 3 women + 2 men: only "all"
    assert grid and all(c["n_people"] == 5 for c in grid)
    pool = tmp_path / "pool.db"
    assert A.write(groups, path=pool) == sum(1 for f in A.PARAM_FIELDS if groups[0][f])
    rows = sqlite3.connect(pool).execute("SELECT sex, age_band, n, n_values FROM a_params").fetchall()
    assert rows and all(r[2] >= 5 and r[3] >= 5 for r in rows)
    assert store.research_links() == []                                   # the A layer makes no donor id


def test_the_cli_is_off_by_default_and_refuses_k_below_5(server, monkeypatch):
    from backend.scripts import research_pool as CLI
    assert CLI.main(["report-a"]) == 2
    monkeypatch.setenv("TRC_RESEARCH_POOL", "1")
    assert CLI.main(["report-a", "--k", "4"]) == 2
    assert D.K_INTERNAL == 5 and A.K_BY_AUDIENCE == {"internal": 5, "public": 10}
