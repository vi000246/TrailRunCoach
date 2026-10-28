"""
The research pool's A layer (SP-587; docs/research/deidentified-analytics.md §3.5, §3.6 table 1,
§14.4 item 6; SP-326 Q1-2, Q1-7): group statistics only, never a row per person.

Who: everyone the A layer allows (accounts/store.research_a_allowed: the analysis consent, opt-out;
the owner) and whose profile has sex, birth year (18+) and LTHR; their activities that the source
allow-list lets into the A layer (research/sources.py), one per duplicate group.

What comes out, each with at least k distinct people (k settable: 5 for internal analysis, 10 for
anything other users would see):
  params  per-person parameters, computed in memory: the prototype's three from the tracks
          (deid.person_params) and, SP-812 (research doc §14.5 item 5), every engine/calibrate.py
          item the person's own data fitted (`calib.<name>`: the stored fit before shrinkage toward
          the default — a default, a manual value or a count without a fit is no parameter; read
          from the tenant's user_settings, nothing written). A person with an activity the source
          allow-list refuses gives no calibration item: a fit may rest on any of their activities
          (the report counts such people, and those whose stored fits could not be read).
          Then only grouped by
          sex × age band: a band under k is merged with its neighbour (50-59 + 60+ -> 50+); when
          either sex cannot reach k even merged, only the all-people row is given (publishing
          "all" next to "male" would give "female" away by subtraction — the groups given are
          always one partition, never two overlapping cuts); a field with fewer than k values in
          a group is withheld
  grid    ~500-m cells walked by >= k people (deid.aggregate_grid)

`evaluate`'s report is for the operator: per output the head count of every group / cell given, and what was
held back (merged bands, groups withheld, fields withheld, cells under k — their head counts only,
never where they are), per field how many people have it and in how many groups it was given or
held, plus `ok`: every number given rests on >= k people and the groups are one partition. Every
field is cut the same way (the groups are chosen by head count, before any field): no two cuts of
one report overlap, so none can be subtracted from another (§14.4 item 6) — a field too thin in a
group is withheld there, never given on a coarser group instead.
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
TRACK_FIELDS = ("easy_flat_speed_ms", "uphill_vam_mh", "drift_pct")     # deid.person_params (SP-587)
PARAM_FIELDS = TRACK_FIELDS                                            # kept: the prototype's three
CALIB_PREFIX = "calib."
TENANT_ATHLETE = 1                   # a tenant's own athlete id in its wko5coach.db (user_settings.user_id)


def calib_items() -> dict:
    """engine/calibrate.py's items a person's data can fit (not the manual-only 進階 C 類), by
    field name `calib.<name>`, in the registry's order."""
    from backend.engine import calibrate as C
    return {CALIB_PREFIX + n: it for n, it in C._registry().items() if not it.manual_only}


def fields() -> tuple:
    """Every parameter field of the A layer: the track ones, then the calibration items."""
    return TRACK_FIELDS + tuple(calib_items())


def _digits(f: str) -> int:
    it = calib_items().get(f)
    return 2 if it is None else max(2, int(it.digits) + 1)


def calib_params(folder, user_id: int = TENANT_ATHLETE) -> Optional[dict]:
    """{calib.<name>: the person's own fit} from the tenant's stored calibration entries
    (athlete.calib.<name>, read-only): only source "fitted" with at least the item's min_n points;
    the fit before shrinkage (`personal`), else the value. {} without a DB; None when the DB is
    there but cannot be read (collect counts it, so a broken tenant is not mistaken for one with no fit)."""
    import json
    import sqlite3
    from pathlib import Path
    from backend.engine import calibrate as C
    db = Path(folder) / "wko5coach.db"
    items = calib_items()
    if not db.exists() or not items:
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        try:
            rows = con.execute("SELECT key, value_json FROM user_settings WHERE user_id = ? AND key LIKE ?",
                               (int(user_id), C.KEY_PREFIX + "%")).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    out = {}
    for k, raw in rows:
        f = CALIB_PREFIX + k[len(C.KEY_PREFIX):]
        it = items.get(f)
        try:
            e = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            e = None
        if it is None or not isinstance(e, dict) or e.get("source") != "fitted":
            continue
        n = e.get("n")
        v = e.get("personal", e.get("value"))
        if isinstance(n, bool) or not isinstance(n, (int, float)) or n < it.min_n:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v):
            continue
        out[f] = float(v)
    return out


@dataclass
class AInput:
    params: list = field(default_factory=list)      # one dict per person (sex, age band, numbers)
    cells: list = field(default_factory=list)       # one {cell: [speeds]} per person
    skipped: Counter = field(default_factory=Counter)
    refused: Counter = field(default_factory=Counter)
    calib_withheld: Counter = field(default_factory=Counter)   # people whose calibration items were left out, why


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
        cal = calib_params(td.folder)
        if cal is None:
            out.calib_withheld["unreadable"] += 1               # the track parameters still count
        elif cal and sum(refused.values()):
            out.calib_withheld["refused_source"] += 1          # a fit may rest on a refused activity
        elif cal:
            p.update(cal)
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
    for f in fields():
        xs = [r[f] for r in rows if r.get(f) is not None]
        if not xs and f not in TRACK_FIELDS:
            out[f] = None                                     # nobody has this item: nothing to hold back
            continue
        if len(xs) < k:
            out[f] = None
            held.append({"output": "params", "group": group, "field": f, "n": len(xs), "reason": "field_below_k"})
        else:
            dg = _digits(f)
            out[f] = {"n": len(xs), "median": round(float(np.median(xs)), dg),
                      "p25": round(float(np.percentile(xs, 25)), dg), "p75": round(float(np.percentile(xs, 75)), dg)}
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


def one_partition(groups: list[dict], people: int) -> bool:
    """The groups given are one partition: either the single all-people row, or sex × band groups
    that never overlap (one sex, one band each) and together hold everyone counted."""
    if not groups:
        return True
    if len(groups) == 1 and groups[0]["sex"] == "all":
        return groups[0]["n"] == people
    seen: set = set()
    for g in groups:
        if g["sex"] == "all":
            return False                                  # "all" next to a cut: subtraction
        bands = _bands_of(g["age_band"])
        if not bands or any((g["sex"], b) in seen for b in bands):
            return False
        seen |= {(g["sex"], b) for b in bands}
    return sum(g["n"] for g in groups) == people


def _bands_of(label: str) -> list:
    """The age bands a (merged) label covers: "30-39" -> [30-39], "50+" -> [50-59, 60+], "30-49" ->
    [30-39, 40-49]; [] for a label that is none."""
    bands = list(D.AGE_BANDS)
    if label in bands:
        return [label]
    lows = [b.split("-")[0].rstrip("+") for b in bands]
    lo, _, hi = label.partition("-")
    lo = lo.rstrip("+")
    if lo not in lows:
        return []
    i = lows.index(lo)
    if label.endswith("+"):
        return bands[i:]
    ends = [j for j, b in enumerate(bands) if b.endswith("-" + hi)]
    return bands[i:ends[0] + 1] if ends and ends[0] >= i else []


def _field_report(params: list[dict], groups: list[dict], held: list) -> dict:
    """Per field: how many people have it (all, before any group), in how many groups it was given /
    held back. Head counts only."""
    out = {}
    for f in fields():
        have = sum(1 for p in params if p and p.get(f) is not None)
        if not have and f not in TRACK_FIELDS:
            continue
        out[f] = {"people": have, "groups_given": sum(1 for g in groups if g.get(f)),
                  "groups_held": sum(1 for h in held if h.get("field") == f)}
    return out


def evaluate(inp: AInput, k: int) -> tuple[dict, list, list]:
    """(the operator's threshold report — no coordinates, no person —, the parameter groups given,
    the grid cells given)."""
    params, held = aggregate_params(inp.params, k)
    grid, grid_held = D.aggregate_grid([c for c in inp.cells if c], k=k)
    given_n = [g["n"] for g in params] + [c["n_people"] for c in grid]
    field_n = [v["n"] for g in params for f, v in g.items() if f in fields() and v]
    counted = len([p for p in inp.params if p])
    return {
        "k": int(k),
        "people": len(inp.params),
        "ok": all(n >= k for n in given_n + field_n) and one_partition(params, counted),
        "fields": _field_report(inp.params, params, held),
        "calib_withheld_people": dict(inp.calib_withheld),
        "outputs": {
            "params": [{"group": f"{g['sex']} {g['age_band']}", "n": g["n"],
                        "fields_given": [f for f in fields() if g.get(f)]} for g in params],
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
                for g in params for f in fields() if g.get(f)]
        con.executemany("INSERT INTO a_params VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)
