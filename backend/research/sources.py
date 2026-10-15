"""
Source chain and the pool's source allow-list (SP-585; docs/research/deidentified-analytics.md §8,
§14.4 items 4–5).

The source chain of an activity says where its data came from, the upstream platform included:
`workout_files.source` only names the sync that brought the file in (coros, garmin, trainingpeaks,
intervals, upload, local), but an intervals.icu activity can be a Garmin, COROS, Huawei or Strava
one (intervals.icu's Activity.source, kept in workout_files.upstream_source since SP-346), and the
platforms' terms differ (sync-platforms.md §4.4, watch-official-apis.md):

  chain                 what it is                                    default rule (§8.2, NOT decided)
  manual                a file the user uploaded himself (SP-323/349) A, B
  coros-unofficial      COROS sync with the account's password        A, B — the owner's own data only
  coros-mcp             COROS MCP (not built)                         A only, until COROS answers
  coros-partner         COROS Partner API (not built)                 A, B (§7.4: internal use)
  garmin-unofficial     the Garmin trial (SP-91, unofficial package)  A, B — the owner's only; attributed
  intervals:<UPSTREAM>  intervals.icu + the platform it came from     A, B; GARMIN_CONNECT attributed;
                                                                      STRAVA and UNKNOWN refused
  trainingpeaks         TrainingPeaks (no database of its data)       refused
  strava, strava-export Strava (§5.4: not even de-identified)         refused
  local                 the owner's WKO5 folder scan                  A, B — the owner's only

Anything not in the table is refused (deny by default). The table is one replaceable object
(SourcePolicy(rules) or a JSON file, `load_rules`): the user has not answered the research doc's
§11 Q10 yet (RULES_PENDING), so the defaults are the document's recommendations.

`workout_files.source_chain` is written for every new row (a mapper event, db/models.py) and filled
in once for the rows from before (`backfill_sql`, run by db/database._migrate_schema: only rows
whose chain is NULL — idempotent). A future source that cannot be told from `source` alone (COROS
MCP vs. the unofficial sync) sets source_chain itself; the event never overwrites a chain it did not
derive.

Duplicates: the same run from two sources is one activity group in the tenant (sync/dedup.py,
`duplicate_of`). `pick_one_per_activity` keeps one row per group — the canonical one when the
policy allows it, else the best allowed copy (the research doc §8.3: "優先取可以進資料池的那份") —
so a run never reaches the pool twice, and a refused copy never hides an allowed one.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

# workout_files.source -> the chain (an intervals.icu row adds its upstream: chain_of)
CHAIN_OF_SOURCE = {
    "upload": "manual",
    "coros": "coros-unofficial",
    "garmin": "garmin-unofficial",
    "trainingpeaks": "trainingpeaks",
    "local": "local",
}
INTERVALS = "intervals"
UNKNOWN_UPSTREAM = "UNKNOWN"
MAX_CHAIN = 60                     # the column's width

GARMIN_ATTRIBUTION = "Insights derived in part from Garmin device-sourced data."


def chain_of(source: Optional[str], upstream_source: Optional[str] = None) -> str:
    """The chain of a workout_files row from its source (+ intervals.icu's upstream platform)."""
    src = (source or "local").strip().lower()
    if src == INTERVALS:
        up = (upstream_source or "").strip().upper() or UNKNOWN_UPSTREAM
        return f"{INTERVALS}:{up}"[:MAX_CHAIN]
    return CHAIN_OF_SOURCE.get(src, src)[:MAX_CHAIN]


def backfill_sql(columns=None) -> str:
    """One UPDATE that fills workout_files.source_chain where it is NULL (the rows from before the
    column) with chain_of(source, upstream_source); a second run changes nothing. `columns`: the
    table's columns (PRAGMA table_info) — an old or partial schema without `source` /
    `upstream_source` reads them as NULL (None: both there)."""
    cols = set(columns) if columns is not None else {"source", "upstream_source"}
    src = "source" if "source" in cols else "NULL"
    up = "upstream_source" if "upstream_source" in cols else "NULL"
    whens = " ".join(f"WHEN '{s}' THEN '{c}'" for s, c in CHAIN_OF_SOURCE.items())
    return (f"UPDATE workout_files SET source_chain = CASE lower(coalesce({src}, 'local')) "
            f"WHEN '{INTERVALS}' THEN '{INTERVALS}:' || coalesce(nullif(upper(trim({up})), ''), "
            f"'{UNKNOWN_UPSTREAM}') {whens} ELSE lower(coalesce({src}, 'local')) END "
            "WHERE source_chain IS NULL")


# ---------------------------------------------------------------------------
# the allow-list
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceRule:
    a: bool                      # may feed A-layer aggregates
    b: bool                      # may feed B-layer detail
    owner_only: bool = False     # only the owner's own data (he is the data subject)
    attribution: Optional[str] = None


# the research doc §8, §11 Q10 — NOT decided by the user yet (2026-10-13: "到時再提醒我")
RULES_PENDING = True
DEFAULT_RULES: dict[str, SourceRule] = {
    "manual": SourceRule(True, True),
    "coros-partner": SourceRule(True, True),
    "coros-mcp": SourceRule(True, False),
    "coros-unofficial": SourceRule(True, True, owner_only=True),
    "garmin-unofficial": SourceRule(True, True, owner_only=True, attribution=GARMIN_ATTRIBUTION),
    "local": SourceRule(True, True, owner_only=True),
    INTERVALS: SourceRule(True, True),                          # any upstream not listed below
    "intervals:COROS": SourceRule(True, True),
    "intervals:GARMIN_CONNECT": SourceRule(True, True, attribution=GARMIN_ATTRIBUTION),
    "intervals:STRAVA": SourceRule(False, False),
    f"intervals:{UNKNOWN_UPSTREAM}": SourceRule(False, False),
    "trainingpeaks": SourceRule(False, False),
    "strava": SourceRule(False, False),
    "strava-export": SourceRule(False, False),
}


class SourcePolicy:
    """Which chain may feed which layer. `rules` replaces the defaults whole (deny by default)."""

    def __init__(self, rules: Optional[dict[str, SourceRule]] = None):
        self.rules = dict(DEFAULT_RULES if rules is None else rules)

    def rule(self, chain: Optional[str]) -> Optional[SourceRule]:
        if not chain:
            return None
        if chain in self.rules:
            return self.rules[chain]
        if chain.startswith(f"{INTERVALS}:") and INTERVALS in self.rules:
            return self.rules[INTERVALS]
        return None

    def allows(self, chain: Optional[str], layer: str, *, owner: bool = False) -> bool:
        r = self.rule(chain)
        if r is None or (r.owner_only and not owner):
            return False
        return r.a if layer == "A" else r.b

    def attribution(self, chain: Optional[str], device_name: Optional[str] = None) -> Optional[str]:
        """The sentence the 方法與出處 page must carry when this activity feeds an analysis: the
        rule's, or Garmin's for any Garmin device (garmin_attribution.is_garmin)."""
        from backend.sync.garmin_attribution import is_garmin
        r = self.rule(chain)
        if r is not None and r.attribution:
            return r.attribution
        up = chain.split(":", 1)[1] if chain and chain.startswith(f"{INTERVALS}:") else None
        return GARMIN_ATTRIBUTION if is_garmin(device_name, up) else None

    def to_json(self) -> str:
        return json.dumps({k: asdict(v) for k, v in sorted(self.rules.items())}, indent=2, sort_keys=True)


def load_rules(path: Path) -> SourcePolicy:
    """A policy from a JSON file {chain: {a, b, owner_only?, attribution?}} — the whole table, not
    a patch on the defaults. ValueError on anything else."""
    raw = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("the rules file is a JSON object {chain: rule}")
    rules = {}
    for chain, r in raw.items():
        if not isinstance(r, dict) or not isinstance(r.get("a"), bool) or not isinstance(r.get("b"), bool):
            raise ValueError(f"rule {chain!r}: a and b are booleans")
        rules[str(chain)] = SourceRule(a=r["a"], b=r["b"], owner_only=bool(r.get("owner_only", False)),
                                       attribution=r.get("attribution") or None)
    return SourcePolicy(rules)


# ---------------------------------------------------------------------------
# one row per activity
# ---------------------------------------------------------------------------

def row_chain(r: dict) -> str:
    return r.get("source_chain") or chain_of(r.get("source"), r.get("upstream_source"))


def pick_one_per_activity(rows: Iterable[dict], policy: SourcePolicy, layer: str, *,
                          owner: bool = False) -> tuple[list[dict], Counter]:
    """rows: workout_files rows as dicts (id, duplicate_of, source / source_chain, upstream_source,
    file_format). Returns (one allowed row per duplicate group, {chain: rows refused}). A group
    with no allowed copy is left out whole; its refused copies are counted, the copies that were
    passed over because another one of the group was taken are not."""
    from backend.sync.dedup import FORMAT_RANK, not_original
    groups: dict[int, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("duplicate_of") or r["id"], []).append(r)
    kept, refused = [], Counter()
    for canon_id, members in sorted(groups.items()):
        readable = [m for m in members if (m.get("file_format") or "") != "corrupt"]
        ok = [m for m in readable if policy.allows(row_chain(m), layer, owner=owner)]
        if not ok:
            for m in members:
                if not policy.allows(row_chain(m), layer, owner=owner):
                    refused[row_chain(m)] += 1
            continue
        canon = [m for m in ok if m["id"] == canon_id]
        if canon:
            kept.append(canon[0])
            continue

        def rank(m):
            return (FORMAT_RANK.get(str(m.get("file_format") or "").lower(), 3)
                    + (1 if not_original(_Row(m)) else 0), m["id"])
        kept.append(min(ok, key=rank))
    return kept, refused


class _Row:
    def __init__(self, d: dict):
        self.upstream_source = d.get("upstream_source")
