"""
The research pool's A layer (SP-587; docs/research/deidentified-analytics.md §3.5, §3.6 table 1,
§14.4 item 6; SP-326 Q1-2, Q1-7): group statistics only, never a row per person.

Who: everyone the A layer allows (accounts/store.research_a_allowed: the analysis consent, opt-out;
the owner) and whose profile has sex, birth year (18+) and LTHR; their activities that the source
allow-list lets into the A layer (research/sources.py), one per duplicate group.

What comes out, each with at least k distinct people (k settable: 5 for internal analysis, 10 for
anything other users would see):
  params  per-person parameters (deid.person_params), computed in memory, then only grouped by
          sex × age band: a band under k is merged with its neighbour (50-59 + 60+ -> 50+); when
          either sex cannot reach k even merged, only the all-people row is given (publishing
          "all" next to "male" would give "female" away by subtraction — the groups given are
          always one partition, never two overlapping cuts); a field with fewer than k values in
          a group is withheld
  grid    ~500-m cells walked by >= k people (deid.aggregate_grid)

`evaluate`'s report is for the operator: per output the head count of every group / cell given, and what was
held back (merged bands, groups withheld, fields withheld, cells under k — their head counts only,
never where they are), plus `ok`: every number given rests on >= k people.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

import numpy as np

from backend.research import deid as D
from backend.research import sources as S

K_BY_AUDIENCE = {"internal": D.K_INTERNAL, "public": D.K_PUBLIC}
PARAM_FIELDS = ("easy_flat_speed_ms", "uphill_vam_mh", "drift_pct")


@dataclass
class AInput:
    params: list = field(default_factory=list)      # one dict per person (sex, age band, numbers)
    cells: list = field(default_factory=list)       # one {cell: [speeds]} per person
    skipped: Counter = field(default_factory=Counter)
    refused: Counter = field(default_factory=Counter)


def collect(*, policy: Optional[S.SourcePolicy] = None, today: Optional[dt.date] = None,
            rng: Optional[np.random.Generator] = None, loader=None, users: Optional[list] = None) -> AInput:
    """Every A-allowed person's parameters and grid input — in memory only."""
    import sqlite3
    from backend.accounts import store
    from backend.research import tenant as T
    from backend.research.pool import _cells
    policy = policy or S.SourcePolicy()
    today = today or dt.date.today()
    rng = rng or np.random.default_rng()
    loader = loader or T.load_track
    out = AInput()
    for u in (store.users() if users is None else users):
        if not store.research_a_allowed(u):
            out.skipped["no_consent"] += 1
            continue
        try:
            td = T.read_tenant(u.id, u.folder(), owner=u.is_owner, today=today)
        except (OSError, ValueError, sqlite3.Error):
            out.skipped["unreadable"] += 1
            continue
        if td.donor is None:
            for m in td.missing:
                out.skipped[m] += 1
            continue
        rows, refused = S.pick_one_per_activity(td.rows, policy, "A", owner=td.owner)
        out.refused.update(refused)
        tracks = {r["id"]: loader(td, r, S.row_chain(r)) for r in rows}
        p = D.person_params([t for t in tracks.values() if t is not None], td.donor)
        if p is None:
            out.skipped["no_a_activity"] += 1
            continue
        out.params.append(p)
        out.cells.append(_cells(td, rows, lambda r: tracks[r["id"]], rng, policy, SimpleNamespace(attributions=set())))
    return out


# ---------------------------------------------------------------------------- k-gated groups

def _band_label(bands: list[str]) -> str:
    if len(bands) == 1:
        return bands[0]
    lo = bands[0].split("-")[0].rstrip("+")
    hi = bands[-1]
    return f"{lo}+" if hi.endswith("+") else f"{lo}-{hi.split('-')[1]}"


def _merge_bands(rows: list[dict], k: int) -> Optional[list[tuple[list, list]]]:
    """Adjacent age bands merged until each has >= k people; None when all of them together < k."""
    groups = [[[b], [r for r in rows if r["age_band"] == b]] for b in D.AGE_BANDS]
    groups = [g for g in groups if g[1]]
    if sum(len(g[1]) for g in groups) < k:
        return None
    while len(groups) > 1:
        small = [i for i, g in enumerate(groups) if len(g[1]) < k]
        if not small:
            break
        i = small[0]
        nb = [j for j in (i - 1, i + 1) if 0 <= j < len(groups)]
        j = min(nb, key=lambda x: (len(groups[x][1]), x))
        a, b = sorted((i, j))
        groups[a] = [groups[a][0] + groups[b][0], groups[a][1] + groups[b][1]]
        del groups[b]
    return [(bands, rs) for bands, rs in groups]


def _stats(rows: list[dict], k: int, held: list, group: str) -> dict:
    out = {}
    for f in PARAM_FIELDS:
        xs = [r[f] for r in rows if r.get(f) is not None]
        if len(xs) < k:
            out[f] = None
            held.append({"output": "params", "group": group, "field": f, "n": len(xs), "reason": "field_below_k"})
        else:
            out[f] = {"n": len(xs), "median": round(float(np.median(xs)), 2),
                      "p25": round(float(np.percentile(xs, 25)), 2), "p75": round(float(np.percentile(xs, 75)), 2)}
    return out


def aggregate_params(params: list[dict], k: int) -> tuple[list[dict], list[dict]]:
    """(the groups given, what was held back). Every group given has n >= k; the groups are one
    partition of the people (sex × merged band, or all)."""
    rows = [p for p in params if p]
    held: list[dict] = []
    if len(rows) < k:
        held.append({"output": "params", "group": "all", "n": len(rows), "reason": "below_k"})
        return [], held
    by_sex = {s: [r for r in rows if r["sex"] == s] for s in sorted({r["sex"] for r in rows})}
    merged = {s: _merge_bands(rs, k) for s, rs in by_sex.items()}
    if any(m is None for m in merged.values()):
        for s, rs in by_sex.items():
            held.append({"output": "params", "group": s, "n": len(rs), "reason": "sex_below_k_only_all_given"})
        return [{"sex": "all", "age_band": "all", "n": len(rows), **_stats(rows, k, held, "all")}], held
    out = []
    for s, groups in merged.items():
        for bands, rs in groups:
            label = _band_label(bands)
            if len(bands) > 1:
                held.append({"output": "params", "group": f"{s} {label}", "n": len(rs), "reason": "bands_merged",
                             "bands": bands, "band_n": [sum(1 for r in rs if r["age_band"] == b) for b in bands]})
            out.append({"sex": s, "age_band": label, "n": len(rs), **_stats(rs, k, held, f"{s} {label}")})
    return out, held


def evaluate(inp: AInput, k: int) -> tuple[dict, list, list]:
    """(the operator's threshold report — no coordinates, no person —, the parameter groups given,
    the grid cells given)."""
    params, held = aggregate_params(inp.params, k)
    grid, grid_held = D.aggregate_grid([c for c in inp.cells if c], k=k)
    given_n = [g["n"] for g in params] + [c["n_people"] for c in grid]
    field_n = [v["n"] for g in params for f, v in g.items() if f in PARAM_FIELDS and v]
    return {
        "k": int(k),
        "people": len(inp.params),
        "ok": all(n >= k for n in given_n + field_n),
        "outputs": {
            "params": [{"group": f"{g['sex']} {g['age_band']}", "n": g["n"],
                        "fields_given": [f for f in PARAM_FIELDS if g[f]]} for g in params],
            "grid": {"cells": len(grid), "people_per_cell": _hist(c["n_people"] for c in grid)},
        },
        "held": {
            "params": held,
            "grid": {"cells": len(grid_held), "people_per_cell": _hist(c["n_people"] for c in grid_held)},
        },
        "skipped_people": dict(inp.skipped),
        "refused_activities": dict(inp.refused),
        "rules_pending": S.RULES_PENDING,
    }, params, grid


def _hist(ns) -> dict:
    return {str(n): c for n, c in sorted(Counter(ns).items())}


A_SCHEMA = """
CREATE TABLE IF NOT EXISTS a_params (sex TEXT, age_band TEXT, n INTEGER, field TEXT, n_values INTEGER,
  median REAL, p25 REAL, p75 REAL);
"""


def write(params: list[dict], path=None) -> int:
    """The given groups into pool.db's a_params (replaced whole): only rows of >= k people."""
    from backend.research.pool import connect
    with connect(path, create=True) as con:
        con.executescript(A_SCHEMA)
        con.execute("DELETE FROM a_params")
        rows = [(g["sex"], g["age_band"], g["n"], f, g[f]["n"], g[f]["median"], g[f]["p25"], g[f]["p75"])
                for g in params for f in PARAM_FIELDS if g[f]]
        con.executemany("INSERT INTO a_params VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)
