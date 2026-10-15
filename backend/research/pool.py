"""
The research pool's B layer (SP-586; docs/research/deidentified-analytics.md §3.6, §7, §12; SP-326
Q1, Q2): <server-root>/research/pool.db, written only by `export` — run by the server's operator
(`python -m backend.scripts.research_pool export`, refused unless TRC_RESEARCH_POOL=1). No API route
reaches it; nothing in it leaves the server.

Who is in it: only people whose last research_b consent row grants it (accounts/store.
research_b_consent — SP-58 writes that row; until it does, nobody), 18 or older, with sex, birth year
and an LTHR. Each gets a random donor id (research_links in accounts.db, with the day 0 the
relative days count from); the pool holds neither the account nor any date.

Tables:
  summaries  donor_id, activity_key (HMAC of the file, the key in accounts.db), rel_day, season,
             time of day, sport, coarse distance / climb / duration (deid.summary), intensity, TSS,
             RPE, race, source chain — one per activity: the source allow-list and the tenant's
             duplicate groups decide which copy (research/sources.py)
  splits     1-km splits, no donor / activity id, no time or order; export_year
  daily      donor_id, rel_day, TSS, minutes, climb band, injury break flag (one row a day)
  grid       ~500-m cells walked by >= k distinct people (A layer: everyone the A layer and the
             source rules allow), count and median speed — the only positions, recomputed whole
  meta       attributions the 方法與出處 page must carry (Garmin), the rules in force, the last run
No GPS track, no FIT, no file name, no platform id, no free text, no weight.

Withdrawal (§7.2, SP-386): `withdraw(uid)` deletes the donor's summaries and daily rows and the
link; splits and the grid (no id) stay. `sweep()` does it for every link whose person no longer
consents (or is gone) — the scheduler runs it once a day while pool.db exists (`tick`), so a
withdrawal is applied within 24 hours; deleting an account calls `withdraw` at once
(accounts/deletion.py). Retention (§12, Q1-12, Q2-2): `expire` deletes summaries / daily rows older
than RETENTION_YEARS (day 0 + rel_day) and whole export years of splits.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np

from backend import research
from backend.research import deid as D
from backend.research import sources as S

log = logging.getLogger(__name__)

KEY_NAME = "pool_activity_key"
SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (donor_id TEXT NOT NULL, activity_key TEXT NOT NULL UNIQUE, rel_day INTEGER,
  season TEXT, time_of_day TEXT, sport TEXT, distance_km REAL, ascent_m INTEGER, duration_min INTEGER,
  pct_lthr REAL, pct_cp REAL, drift_pct REAL, tss REAL, rpe REAL, is_race INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS summaries_donor ON summaries(donor_id);
CREATE TABLE IF NOT EXISTS splits (export_year INTEGER NOT NULL, grade REAL, grade_sd REAL, ele_band_m INTEGER,
  speed_ms REAL, pct_lthr REAL, w_per_kg REAL, cadence REAL, temp_c REAL, decile INTEGER, sex TEXT, age_band TEXT);
CREATE TABLE IF NOT EXISTS daily (donor_id TEXT NOT NULL, rel_day INTEGER NOT NULL, tss INTEGER, duration_min INTEGER,
  ascent_band_m INTEGER, injury_off INTEGER, PRIMARY KEY (donor_id, rel_day));
CREATE TABLE IF NOT EXISTS grid (cell_lat INTEGER, cell_lon INTEGER, n_people INTEGER, n_samples INTEGER,
  median_speed_ms REAL, PRIMARY KEY (cell_lat, cell_lon));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
SUMMARY_COLS = ("donor_id", "activity_key", "rel_day", "season", "time_of_day", "sport", "distance_km", "ascent_m",
                "duration_min", "pct_lthr", "pct_cp", "drift_pct", "tss", "rpe", "is_race", "source")
SPLIT_COLS = ("export_year", "grade", "grade_sd", "ele_band_m", "speed_ms", "pct_lthr", "w_per_kg", "cadence",
              "temp_c", "decile", "sex", "age_band")
DAILY_COLS = ("donor_id", "rel_day", "tss", "duration_min", "ascent_band_m", "injury_off")


class PoolDisabled(RuntimeError):
    """The export is off (TRC_RESEARCH_POOL is not 1)."""


@contextmanager
def connect(path: Optional[Path] = None, create: bool = False) -> Iterator[sqlite3.Connection]:
    p = Path(path or research.pool_db_path())
    if create:
        p.parent.mkdir(parents=True, exist_ok=True)
    elif not p.exists():
        raise FileNotFoundError(p)
    con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


@dataclass
class ExportResult:
    donors: int = 0                      # people exported this run
    summaries: int = 0                   # rows added
    splits: int = 0
    daily: int = 0
    grid_cells: int = 0
    grid_held: int = 0                   # cells under k (not written)
    skipped: Counter = field(default_factory=Counter)      # reason -> people
    refused: Counter = field(default_factory=Counter)      # source chain -> activities refused
    attributions: set = field(default_factory=set)
    withdrawn: int = 0

    def as_dict(self) -> dict:
        return {"donors": self.donors, "summaries": self.summaries, "splits": self.splits, "daily": self.daily,
                "grid_cells": self.grid_cells, "grid_held": self.grid_held, "skipped": dict(self.skipped),
                "refused": dict(self.refused), "attributions": sorted(self.attributions), "withdrawn": self.withdrawn}


Loader = Callable[["object", dict, str], Optional[D.Track]]


def _rel(day: dt.date, day0: dt.date) -> int:
    return (day - day0).days


def export(*, policy: Optional[S.SourcePolicy] = None, path: Optional[Path] = None, today: Optional[dt.date] = None,
           rng: Optional[np.random.Generator] = None, k: int = D.K_INTERNAL, loader: Optional[Loader] = None,
           users: Optional[list] = None, force: bool = False) -> ExportResult:
    """One run over every enabled user (or `users`): the B rows of the consenting people (new
    activities only — an activity already in the pool is never added twice), their daily rows
    rewritten, the grid recomputed from everyone the A layer allows. Withdrawals first (sweep).
    PoolDisabled unless TRC_RESEARCH_POOL=1 (`force`: the tests)."""
    from backend.accounts import store
    from backend.research import tenant as T
    if not (force or research.enabled()):
        raise PoolDisabled(f"set {research.ENV_ENABLED}=1 to export the research pool")
    policy = policy or S.SourcePolicy()
    today = today or dt.date.today()
    rng = rng or np.random.default_rng()
    loader = loader or T.load_track
    res = ExportResult()
    res.withdrawn = sweep(path=path, create=True)
    key = store.research_key(KEY_NAME)
    cells_per_person: list[dict] = []
    with connect(path, create=True) as con:
        have = {r[0] for r in con.execute("SELECT activity_key FROM summaries")}
        for u in (store.users() if users is None else users):
            b_ok = store.research_b_consent(u.id)
            a_ok = store.research_a_allowed(u)
            if not (a_ok or b_ok):
                res.skipped["no_consent"] += 1
                continue
            try:
                td = T.read_tenant(u.id, u.folder(), owner=u.is_owner, today=today)
            except (OSError, ValueError, sqlite3.Error) as e:
                log.info("research export: user %s unreadable (%s)", u.id, type(e).__name__)
                res.skipped["unreadable"] += 1
                continue
            if td.donor is None:
                for m in td.missing:
                    res.skipped[m] += 1
                continue
            tracks: dict[int, D.Track] = {}

            def track(row):
                if row["id"] not in tracks:
                    tracks[row["id"]] = loader(td, row, S.row_chain(row))
                return tracks[row["id"]]
            if a_ok:
                a_rows, refused = S.pick_one_per_activity(td.rows, policy, "A", owner=td.owner)
                res.refused.update(refused)
                cells_per_person.append(_cells(td, a_rows, track, rng, policy, res))
            if b_ok:
                b_rows, refused = S.pick_one_per_activity(td.rows, policy, "B", owner=td.owner)
                if not a_ok:
                    res.refused.update(refused)
                _export_donor(con, td, b_rows, track, key, have, today, rng, policy, res)
        grid, held = D.aggregate_grid([c for c in cells_per_person if c], k=k)
        con.execute("DELETE FROM grid")
        con.executemany("INSERT INTO grid VALUES (?, ?, ?, ?, ?)",
                        [(g["cell_lat"], g["cell_lon"], g["n_people"], g["n_samples"], g["median_speed_ms"])
                         for g in grid])
        res.grid_cells, res.grid_held = len(grid), len(held)
        old = json.loads((con.execute("SELECT value FROM meta WHERE key = 'attributions'").fetchone() or ["[]"])[0])
        meta = {"attributions": json.dumps(sorted(set(old) | res.attributions)),
                "rules": policy.to_json(), "rules_pending": json.dumps(S.RULES_PENDING),
                "last_export": today.isoformat(), "k": str(int(k))}
        con.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", list(meta.items()))
    return res


def _cells(td, rows, track, rng, policy, res) -> dict:
    """One person's grid input (in memory only): every A-allowed track, trimmed around the start,
    the end and the frequent places (found from all the tenant's tracks of those rows)."""
    trs = [t for t in (track(r) for r in rows) if t is not None]
    ends = []
    for tr in trs:
        f, l_ = D.first_last_fix(tr)
        if f:
            d = tr.local_start().date()
            ends += [(d, *f), (d, *l_)]
    places = D.frequent_places(ends)
    out: dict = {}
    for r, tr in zip(rows, (track(r) for r in rows)):
        if tr is None:
            continue
        for c, v in D.cells_of(tr, D.keep_mask(tr, places, D.trim_radii(rng))).items():
            out.setdefault(c, []).extend(v)
        a = policy.attribution(S.row_chain(r), r.get("device_name"))
        if a:
            res.attributions.add(a)
    return out


def _export_donor(con, td, rows, track, key: bytes, have: set, today: dt.date, rng, policy, res) -> None:
    from backend.accounts import store
    from backend.research import tenant as T
    acts = []
    for r in sorted(rows, key=lambda r: (str(r["workout_date"]), r["id"])):
        tr = track(r)
        if tr is not None:
            acts.append((dt.date.fromisoformat(str(r["workout_date"])[:10]), r, tr))
    if not acts:
        res.skipped["no_b_activity"] += 1
        return
    link = store.research_link(td.user_id)
    if link is None:
        first = acts[0][0]
        day0 = first - dt.timedelta(days=int(rng.integers(0, D.DAY0_JITTER_DAYS + 1)))
        link = store.research_link_create(td.user_id, secrets.token_hex(16), day0)
    donor_id, day0 = link["donor_id"], dt.date.fromisoformat(link["day0"])
    new_s, new_p, per_day = [], [], {}
    for day, r, tr in acts:
        rel = _rel(day, day0)
        akey = D.activity_key(key, td.user_id, T.content_id(r))
        s = D.summary(tr, td.donor, donor_id, rel, akey)
        x = per_day.setdefault(rel, {"tss": 0.0, "min": 0.0, "asc": 0.0})
        x["tss"] += s["tss"] or 0.0
        x["min"] += float(tr.t[-1]) / 60 if len(tr.t) else 0.0
        x["asc"] += D.ascent_m(tr)
        a = policy.attribution(S.row_chain(r), r.get("device_name"))
        if a:
            res.attributions.add(a)
        if akey in have:
            continue
        have.add(akey)
        new_s.append(s)
        new_p += D.splits(tr, td.donor, today.year)
    injury = set()
    for a, b in td.injury_breaks:
        if (b - a).days + 1 >= 7:
            injury |= {_rel(a, day0) + i for i in range((b - a).days + 1)}
    daily = D.daily_load(donor_id, per_day, injury)
    order = rng.permutation(len(new_p))
    new_p = [new_p[i] for i in order]                   # no order: splits cannot be chained back
    con.executemany(f"INSERT INTO summaries ({','.join(SUMMARY_COLS)}) VALUES ({','.join('?' * len(SUMMARY_COLS))})",
                    [tuple(s[c] for c in SUMMARY_COLS) for s in new_s])
    con.executemany(f"INSERT INTO splits ({','.join(SPLIT_COLS)}) VALUES ({','.join('?' * len(SPLIT_COLS))})",
                    [tuple(p[c] for c in SPLIT_COLS) for p in new_p])
    con.execute("DELETE FROM daily WHERE donor_id = ?", (donor_id,))
    con.executemany(f"INSERT INTO daily ({','.join(DAILY_COLS)}) VALUES ({','.join('?' * len(DAILY_COLS))})",
                    [tuple(d[c] for c in DAILY_COLS) for d in daily])
    res.donors += 1
    res.summaries += len(new_s)
    res.splits += len(new_p)
    res.daily += len(daily)


# ---------------------------------------------------------------------------- withdrawal, retention

def withdraw(uid: int, path: Optional[Path] = None) -> dict:
    """Delete everything of `uid` that still maps to them: the summaries and daily rows under their
    donor id, then the link. Splits and the grid (no id) stay (the privacy policy says so, §7.3).
    No link: nothing to do. {summaries, daily, link}."""
    from backend.accounts import store
    link = store.research_link(uid)
    if link is None:
        return {"summaries": 0, "daily": 0, "link": 0}
    p = Path(path or research.pool_db_path())
    n_s = n_d = 0
    if p.exists():
        with connect(p) as con:
            n_s = con.execute("DELETE FROM summaries WHERE donor_id = ?", (link["donor_id"],)).rowcount
            n_d = con.execute("DELETE FROM daily WHERE donor_id = ?", (link["donor_id"],)).rowcount
    n_l = store.research_link_delete(uid)               # only after the rows are gone
    log.info("research pool: a donor withdrawn (%d summaries, %d daily rows)", n_s, n_d)
    return {"summaries": n_s, "daily": n_d, "link": n_l}


def sweep(path: Optional[Path] = None, create: bool = False) -> int:
    """Withdraw every linked person who no longer has the B consent (or no account): the people
    withdrawn. Only deletes; runs whether the export is on or off."""
    from backend.accounts import store
    p = Path(path or research.pool_db_path())
    if not create and not p.exists():
        return 0
    n = 0
    for link in store.research_links():
        u = store.get_user(link["user_id"])
        if u is None or u.disabled or not store.research_b_consent(u.id):
            withdraw(link["user_id"], path=p)
            n += 1
    return n


def expire(today: Optional[dt.date] = None, path: Optional[Path] = None,
           years: int = D.RETENTION_YEARS) -> dict:
    """Retention: summaries / daily rows dated before today − `years` (day 0 + rel_day, the link
    table being the only place with day 0), and the splits of export years before that year."""
    from backend.accounts import store
    today = today or dt.date.today()
    p = Path(path or research.pool_db_path())
    if not p.exists():
        return {"summaries": 0, "daily": 0, "splits": 0}
    cut = today - dt.timedelta(days=round(365.25 * years))
    out = {"summaries": 0, "daily": 0, "splits": 0}
    with connect(p) as con:
        for link in store.research_links():
            limit = (cut - dt.date.fromisoformat(link["day0"])).days
            for t in ("summaries", "daily"):
                out[t] += con.execute(f"DELETE FROM {t} WHERE donor_id = ? AND rel_day < ?",
                                      (link["donor_id"], limit)).rowcount
        # rows whose link is gone are a withdrawal half done: delete them too
        donors = [x["donor_id"] for x in store.research_links()]
        q = ",".join("?" * len(donors))
        for t in ("summaries", "daily"):
            out[t] += con.execute(f"DELETE FROM {t} WHERE donor_id NOT IN ({q})" if donors else f"DELETE FROM {t}",
                                  donors).rowcount
        out["splits"] = con.execute("DELETE FROM splits WHERE export_year < ?", (today.year - years,)).rowcount
    return out


TICK_EVERY_S = 3600.0          # an hour: a withdrawal is applied well within the 24 hours (§7.2)
_last_tick: Optional[float] = None


def tick(today: Optional[dt.date] = None, path: Optional[Path] = None, now: Optional[float] = None) -> Optional[dict]:
    """The scheduler's pass (sync/scheduler.py calls it every minute; it works once an hour):
    withdrawals and retention, only when a pool exists — deleting never needs the export switch."""
    import time
    global _last_tick
    now = time.monotonic() if now is None else now
    if _last_tick is not None and now - _last_tick < TICK_EVERY_S:
        return None
    _last_tick = now
    p = Path(path or research.pool_db_path())
    if not p.exists():
        return None
    return {"withdrawn": sweep(path=p), "expired": expire(today, path=p)}
