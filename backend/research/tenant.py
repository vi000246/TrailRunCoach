"""
One tenant folder as the research pool's export reads it (SP-586): read-only, nothing written into
the tenant. What it takes: the workout_files rows of foot activities (the source chain, duplicate
group, file key), the person's sex, birth year, LTHR, CP and weight from plan.json (an LTHR typed in
設定 or tested; else the COROS account's), race days (the user's 比賽 marks and the season plan's
races) and injury breaks (傷病紀錄 severe = 停跑; the dates only, never the note). The tracks come
from the tenant's own FIT objects (fit/), decoded in memory.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from backend.research import deid as D

log = logging.getLogger(__name__)

FOOT = {"run": "run", "running": "run", "trail": "trail", "trail_running": "trail", "walk": "hike",
        "walking": "hike", "hike": "hike", "hiking": "hike", "mountaineering": "hike"}
COLUMNS = ("id", "duplicate_of", "source", "upstream_source", "source_chain", "device_name", "file_format",
           "file_path", "workout_date", "start_time_utc", "sport", "rpe")


@dataclass
class TenantData:
    user_id: int
    owner: bool
    folder: Path
    donor: Optional[D.Donor]
    missing: list = field(default_factory=list)        # why this person cannot be exported
    rows: list = field(default_factory=list)           # foot activities, workout_files as dicts
    race_days: set = field(default_factory=set)        # local dates
    injury_breaks: list = field(default_factory=list)  # [(first, last)] of severe injuries


def _ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _cols(con, table: str) -> set:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def read_tenant(user_id: int, folder: Path, *, owner: bool, today: dt.date) -> TenantData:
    from backend.engine.planning import Plan
    folder = Path(folder)
    out = TenantData(user_id=int(user_id), owner=owner, folder=folder, donor=None)
    plan = Plan.load(folder / "plan.json")
    db = folder / "wko5coach.db"
    lthr_app = weight_app = None
    if db.exists():
        con = _ro(db)
        try:
            have = _cols(con, "workout_files")
            sel = ", ".join(c if c in have else f"NULL AS {c}" for c in COLUMNS)
            for r in con.execute(f"SELECT {sel} FROM workout_files"):
                r = dict(r)
                sport = FOOT.get(str(r["sport"] or "").lower())
                if sport and r["workout_date"]:
                    r["sport"] = sport
                    out.rows.append(r)
            if "lthr" in _cols(con, "athlete_settings"):
                x = con.execute("SELECT lthr, weight_kg FROM athlete_settings ORDER BY effective_date DESC").fetchall()
                lthr_app = next((v[0] for v in x if v[0]), None)
                weight_app = next((v[1] for v in x if v[1]), None)
            if {"start_local", "activity_type"} <= _cols(con, "activity_tags"):
                for (s,) in con.execute("SELECT start_local FROM activity_tags WHERE activity_type = 'race'"):
                    try:
                        out.race_days.add(dt.date.fromisoformat(str(s)[:10]))
                    except ValueError:
                        pass
            if {"onset_date", "severity"} <= _cols(con, "injury_events"):
                cat = "category" if "category" in _cols(con, "injury_events") else "'injury'"
                for onset, resolved, missed in con.execute(
                        f"SELECT onset_date, resolved_date, days_missed FROM injury_events "
                        f"WHERE severity = 'severe' AND {cat} = 'injury'"):
                    try:
                        a = dt.date.fromisoformat(str(onset)[:10])
                    except ValueError:
                        continue
                    if resolved:
                        b = dt.date.fromisoformat(str(resolved)[:10]) - dt.timedelta(days=1)
                    elif missed:
                        b = a + dt.timedelta(days=int(missed) - 1)
                    else:
                        b = today
                    out.injury_breaks.append((a, max(a, b)))
        finally:
            con.close()
    for e in plan.events:
        if e.kind == "race" and e.date:
            try:
                d0 = dt.date.fromisoformat(str(e.date)[:10])
            except ValueError:
                continue
            out.race_days |= {d0 + dt.timedelta(days=k) for k in range(max(1, int(e.days or 1)))}
    sex = plan.profile.get("sex")
    by = plan.profile.get("birth_year")
    lthr = plan.threshold_on("lthr", today) or lthr_app
    if sex not in ("male", "female"):
        out.missing.append("sex")
    if not isinstance(by, int):
        out.missing.append("birth_year")
    if not lthr:
        out.missing.append("lthr")
    if not out.missing:
        age = today.year - by
        if age < D.MIN_AGE:
            out.missing.append("under_18")
        else:
            out.donor = D.Donor(sex=sex, age=age, lthr=float(lthr), cp_w=plan.threshold_on("cp", today),
                                weight_kg=plan.weight_on(today) or weight_app)
    return out


def _fit_offset_h(raw: bytes) -> Optional[float]:
    """The FIT's local_timestamp − timestamp (activity message), in hours; None without one."""
    try:
        import fitdecode
        with fitdecode.FitReader(io.BytesIO(raw), error_handling=fitdecode.ErrorHandling.IGNORE) as fit:
            for fr in fit:
                if isinstance(fr, fitdecode.FitDataMessage) and fr.name == "activity":
                    ts = fr.get_value("timestamp", fallback=None)
                    loc = fr.get_value("local_timestamp", fallback=None)
                    if ts is None or loc is None:
                        return None
                    if isinstance(ts, dt.datetime) and ts.tzinfo is not None:
                        ts = ts.astimezone(dt.timezone.utc).replace(tzinfo=None)
                    if isinstance(loc, (int, float)):
                        loc = dt.datetime(1989, 12, 31) + dt.timedelta(seconds=int(loc))
                    if isinstance(loc, dt.datetime) and loc.tzinfo is not None:
                        loc = loc.replace(tzinfo=None)
                    return round((loc - ts).total_seconds() / 900) / 4
    except Exception:                       # noqa: BLE001 — a broken file: no offset
        return None
    return None


def track_from_fit(raw: bytes, *, sport: str, chain: str, is_race: bool, rpe: Optional[float],
                   default_offset_h: float = 8.0) -> Optional[D.Track]:
    """A FIT in memory -> Track through the app's own decoder (the per-second cache's channels)."""
    from backend.files.fit_to_channels import fit_to_channels
    fc = fit_to_channels(raw)
    n = len(fc.elapsedtime)
    if n < 2 or fc.start_time is None:
        return None

    def ch(name, k=1.0):
        v = fc.channels.get(name)
        if v is None:
            return np.full(n, np.nan)
        return np.array([np.nan if x is None else float(x) * k for x in v], dtype=float)

    dist = np.fmax.accumulate(np.nan_to_num(ch("elapseddistance", 1000.0), nan=0.0))
    start = fc.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    off = _fit_offset_h(raw)
    return D.Track(t=np.asarray(fc.elapsedtime, dtype=float), lat=ch("latitude"), lon=ch("longitude"), dist=dist,
                   speed=ch("speed", 1 / 3.6), ele=ch("elevation"), hr=ch("heartrate"), power=ch("power"),
                   cadence=ch("cadence"), temp=ch("temperature"), start_utc=start.astimezone(dt.timezone.utc),
                   sport=sport, chain=chain, is_race=is_race, rpe=rpe,
                   tz_offset_h=default_offset_h if off is None else off)


def content_id(row: dict) -> str:
    """What identifies the file inside the tenant: its sha256 (an object key), else the row id."""
    from backend.files import fitstore
    return fitstore.sha_of_key(row.get("file_path") or "") or f"row:{row['id']}"


def load_track(td: TenantData, row: dict, chain: str) -> Optional[D.Track]:
    """The row's track from the tenant's own fit/ (an object key, confined to the folder). A legacy
    absolute path is read only for the owner (his WKO5 / COROS folders)."""
    from backend.files import fitstore
    fp = row.get("file_path") or ""
    if (row.get("file_format") or "").lower() not in ("fit", "fit.gz"):
        return None
    try:
        if fitstore.is_object_key(fp):
            raw = fitstore.read_bytes(fp, fitstore.LocalDirStore(td.folder / "fit"))
        elif td.owner:
            raw = fitstore.read_bytes(fp)
        else:
            return None
        day = dt.date.fromisoformat(str(row["workout_date"])[:10])
        return track_from_fit(raw, sport=row["sport"], chain=chain, is_race=day in td.race_days, rpe=row.get("rpe"))
    except Exception as e:                  # noqa: BLE001 — one bad file never stops the export
        log.info("research export: an activity of user %s unreadable (%s)", td.user_id, type(e).__name__)
        return None
