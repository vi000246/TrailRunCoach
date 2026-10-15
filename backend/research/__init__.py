"""
The de-identified research pool (SP-326 investigation, docs/research/deidentified-analytics.md;
implementation SP-585 / SP-586 / SP-587). Server-internal tools only: no API route imports this
package, nothing here sends data anywhere, and everything that writes the pool is off unless the
server's operator turns it on (ENV_ENABLED) and runs `python -m backend.scripts.research_pool`.

  sources     the source chain of every activity (workout_files.source_chain) and the replaceable
              allow-list of which chain may feed which layer (SP-585)
  deid        the de-identification rules: GPS grid cells, 1-km splits, coarse summaries, daily load
  tenant      reads one tenant folder (read-only) into what deid needs
  pool        the B layer: research/pool.db, donor ids, withdrawal, retention (SP-586)
  a_layer     the A layer: k-gated group statistics and the threshold report (SP-587)

Where things live: the pool in <server-root>/research/pool.db (data_registry `research/pool.db`);
the link user -> random donor id and the de-duplication key in accounts.db (research_links,
research_keys), never next to the pool.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_ENABLED = "TRC_RESEARCH_POOL"          # "1": the export may run (off by default)
POOL_REL = "research/pool.db"


def enabled() -> bool:
    return os.getenv(ENV_ENABLED, "").strip() == "1"


def pool_db_path() -> Path:
    from backend import accounts
    return accounts.server_root() / POOL_REL
