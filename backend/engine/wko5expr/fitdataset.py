"""
A Dataset built from a folder of FIT files (COROS / TrainingPeaks sync) so
the chart engine, overview and race power can read synced data instead of
the WKO5 athlete folder.

* samples: backend/files/fit_to_channels.py (WKO5-equivalent channels), wrapped
  in a Wko4File-shaped object so every Dataset / Evaluator code path works
  unchanged (`ds.wko4(i).channels`, `ds.channel(i, name)`)
* per-workout metrics: there is no WKO5 index for FIT sources, so the
  "Entire Workout" fields are computed here with the project's own formulas
  and fed through Dataset._metrics, which then applies the same TSS / IF
  rules (power TSS, rTSS, hrTSS fallback) as for WKO5 data:
      duration      last sample label (s)
      moving        seconds with speed above the sport's moving threshold
      distance      max elapseddistance (km)
      climbing /    sum of positive / negative deltas of a 30-sample moving
      descending    average of elevation (WKO5 smooths `_elevation` too)
      np            (mean of 30 s rolling power ^4) ^ 1/4, tssduration = power samples
      work          sum power * dt (J)
      hrTSS / hrIF  backend/engine/algorithms/wko5_hr.hr_tss with the run/bike LTHR
      ngp           grade-adjusted pace (Minetti), min/km; pace tssduration = moving
* sport type: trail / road from the app DB's workout_files.trail_classification
  (user overrides included, shared across a duplicate_of group), the FIT
  session sub_sport only as a fallback; trail runs carry WKO5's
  "runningtrail" tag, hikes "hiking" / "mountaineering" (TYPE_TAGS).
* vam (4224) = climbing / duration · 3600, as WKO5.
* CP for the charts (`cp`, cp_info): plan test -> athlete_settings
  run_ftp_w -> a Stryd-only PD fit as of the run's date (_estimate_cp,
  never watch power) -> unset; power TSS does not use the fit.
* thresholds / weight, in order: the season plan's dated rows (Dataset.setting
  / cp) -> athlete_settings in the app DB (weight, run_ftp_w, threshold pace;
  not lthr / ftp_w, see _load_db_settings) -> as-of LTHR / CP estimates from
  these FITs (_estimate_settings) -> unset ("未設定"). The WKO5 athlete file
  only when settings_dir is passed (dataset_for_source: the opt-in setting
  charts.fit_settings_from_wko5). PMC constants: 42 / 7 (WKO5's defaults).
* power source per workout (backend/engine/power_source.py): stryd (Stryd
  developer fields / a Stryd device_info row), watch (power without them) or
  none, from the parsed FIT; watch power scores no power TSS unless
  power.accept_watch_power (Dataset._apply_power_policy).
* source "synced" (merge=("coros", "tp")): both folders, one file per
  activity — the 主要資料來源's (backend/sync/primary.py; 自動 = the source
  with the most recent complete data) when both have it (starts within 2 min),
  the other only for activities the primary lacks; entry.file is
  "<folder>/<rel>", file_origin(w) says which. No per-value back-fill.
* the app DB is opened read-only (a sync may be writing it).
* parity mode / own-formula config flags behave as for the WKO5 Dataset.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.corrections import CorrectionStore
from backend.engine.wko5expr.fitcache import to_list as fitcache_list
from backend.engine.wko5expr.dataset import (
    F_CLIMBING, F_DISTANCE, F_DURATION, F_HRIF, F_HRTSS, F_MOVING, F_NGP, F_NP,
    F_PACE_TSSDURATION, F_TSSDURATION, Dataset, Workout, date_to_day, day_to_date,
)
from backend.files.wko4_file import Channel, Wko4File
from backend.files.wko5_athlete import Athlete, WorkoutEntry, read_athlete

log = logging.getLogger(__name__)

F_DESCENDING, F_WORK, F_VAM = 4225, 4218, 4224

# 推估: how often the as-of LTHR / CP estimate is refreshed for a FIT source
# (a value estimated on a grid day applies until the next one). 30 days keeps
# a full-history load to ~one estimate per month; the thresholds.estimate
# windows (90 / 180 days) are much longer, so a finer grid changes little.
ESTIMATE_STEP_DAYS = 30
# 推估: fewest Stryd runs in the 90-day window for a chart CP fit. On this
# athlete's data the first Stryd window (2025-03-22, one run) fitted 135.6 W
# against 178–203 W from the next window (11 runs) on; a single run's
# mean-max rarely holds a maximal effort at every duration.
CP_FIT_MIN_RUNS = 5
# Athlete.setting_on applies the earliest value backwards (WKO5's rule); a
# leading (date.min, None) entry stops that for thresholds that must not reach
# the days before they were known
NOT_BEFORE = (dt.date.min, None)
WKO5_OPT_IN_KEY = "charts.fit_settings_from_wko5"   # settings/repository.py DEFAULTS

SETTING_LABELS = {
    "wko5": "WKO5 athlete 檔（選用）",
    "db": "athlete_settings（app DB）",
    "estimate": "自動估算（當天以前的跑步，Friel 30 分鐘段）",
    "unset": "未設定",
}
IGNORED_WHY = ("COROS 帳號 zoneData 的值（coros_client.login 寫入），沒有記錄是哪個運動；"
               "不當跑步 LTHR／FTP 用")

# FIT sport / sub_sport -> (sport group, sport type) like WKO5 uses them
SPORTS = {
    "running": ("run", "running"), "trail": ("run", "trail running"),
    "treadmill": ("run", "indoor running"), "track": ("run", "running"),
    "cycling": ("bike", "cycling"), "walking": ("walk", "walking"),
    "hiking": ("walk", "hiking"), "mountaineering": ("walk", "mountaineering"),
    "training": ("strength", "strength"), "fitness_equipment": ("strength", "strength"),
    "swimming": ("swim", "swimming"),
}

# sport type -> the WKO5 tags the charts select by (hastag)
TYPE_TAGS = {"trail running": ["runningtrail"], "hiking": ["hiking"], "mountaineering": ["mountaineering"]}


def sport_of(sport: Optional[str], sub_sport: Optional[str],
             classification: Optional[str] = None) -> tuple[str, str]:
    """(sport group, sport type). `classification` = the app DB's
    workout_files.trail_classification of the file (incl. a user override):
    "trail" / "road" decide a run's terrain; None / "unknown" fall back to the
    FIT sub_sport (COROS FITs carry no trail sub_sport, so without the DB value
    every COROS trail run was a road run). A treadmill / indoor run stays
    indoor when the DB says "road" (the climb-rate rule cannot tell indoor)."""
    sub = str(sub_sport or "").lower()
    sp = str(sport or "").lower()
    if sp == "running" and classification == "trail":
        return SPORTS["trail"]
    if sp == "running" and classification == "road":
        return SPORTS["treadmill"] if sub == "treadmill" else SPORTS["running"]
    if sp == "running" and sub in ("trail", "treadmill", "track"):
        return SPORTS[sub]
    return SPORTS.get(sp, ("other", str(sport or "other").lower()))


# ---------------------------------------------------------------------------
# the app DB (read-only): classification, duplicates, athlete_settings
# ---------------------------------------------------------------------------

def _app_db() -> Optional[Path]:
    """The app DB (datasource._db_path; tests patch it to None)."""
    from backend.engine.wko5expr import datasource
    p = datasource._db_path()
    return p if p is not None and Path(p).exists() else None


def _ro(db: Path):
    import sqlite3
    return sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)


def _norm(p) -> str:
    import os
    return os.path.normcase(os.path.abspath(str(p)))


def load_classifications(db: Optional[Path] = None) -> dict:
    """{normalised file path: row} of every workout_files row, row = {id,
    trail_classification, classification_overridden, duplicate_of}, plus
    "_by_id" and "_by_name" indexes. {} when the DB / table is missing.
    Read-only (the sync may be writing)."""
    import sqlite3
    db = _app_db() if db is None else db
    if db is None:
        return {}
    try:
        con = _ro(db)
        try:
            cur = con.execute("SELECT id, file_path, trail_classification, classification_overridden, "
                              "duplicate_of FROM workout_files")
            raw = cur.fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    out: dict = {"_by_id": {}, "_by_name": {}, "_dups": {}}
    for i, fp, tc, ov, dup in raw:
        r = {"id": i, "file_path": fp, "trail_classification": tc, "classification_overridden": bool(ov),
             "duplicate_of": dup}
        out["_by_id"][i] = r
        if fp:
            out[_norm(fp)] = r
            out["_by_name"].setdefault(Path(str(fp).replace("\\", "/")).name, []).append(r)
        if dup:
            out["_dups"].setdefault(dup, []).append(r)
    return out


def read_athlete_settings(db: Optional[Path] = None, athlete_id: int = 1) -> list[dict]:
    """The athlete_settings rows of the app DB (read-only); [] without one."""
    import sqlite3
    db = _app_db() if db is None else db
    if db is None:
        return []
    cols = ("effective_date", "ftp_w", "weight_kg", "lthr", "threshold_pace_s_per_km", "run_ftp_w")
    try:
        con = _ro(db)
        try:
            rows = con.execute(f"SELECT {', '.join(cols)} FROM athlete_settings WHERE athlete_id=?",
                               (athlete_id,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [dict(zip(cols, r)) for r in rows]


def classification_for(path, rows: dict) -> Optional[str]:
    """The trail / road classification of one FIT file from the app DB.
    Cross-source duplicates (dedup.py: the same activity from COROS and TP,
    `duplicate_of` -> the canonical row) are one activity, so the group shares
    one classification: a user override on any row of the group wins (this
    file's row first, then the canonical row, then the other duplicates);
    otherwise this row's own auto value, else the canonical row's. None when
    the file has no row or every value is "unknown"."""
    if not rows:
        return None
    r = rows.get(_norm(path))
    if r is None:
        cand = rows["_by_name"].get(Path(str(path)).name) or []
        r = cand[0] if len(cand) == 1 else None
    if r is None:
        return None
    canon_id = r["duplicate_of"] or r["id"]
    canon = rows["_by_id"].get(canon_id)
    group = [r] + ([canon] if canon is not None and canon is not r else []) + \
        [x for x in rows["_dups"].get(canon_id, []) if x is not r]
    ok = ("trail", "road")
    for x in group:
        if x["classification_overridden"] and x["trail_classification"] in ok:
            return x["trail_classification"]
    for x in (r, canon):
        if x is not None and x["trail_classification"] in ok:
            return x["trail_classification"]
    return None


def _arr(vals) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in vals], dtype=float)


def _rolling(x: np.ndarray, n: int) -> np.ndarray:
    x = np.where(np.isfinite(x), x, 0.0)
    if len(x) < n:
        return np.array([])
    c = np.cumsum(np.concatenate([[0.0], x]))
    return (c[n:] - c[:-n]) / n


def workout_fields(t: np.ndarray, ch: dict[str, np.ndarray], group: str,
                   lthr: Optional[float]) -> dict[int, float]:
    """WKO5 'Entire Workout' field ids computed from the channels."""
    from backend.engine.algorithms import minetti as M
    from backend.engine.algorithms.wko5_hr import hr_tss
    from backend.engine.algorithms.wko5_time import MOVING_SPEED_KMH
    out: dict[int, float] = {}
    if not len(t):
        return out
    dt_s = np.diff(t, prepend=0.0)
    out[F_DURATION] = float(t[-1])
    spd = ch.get("speed")
    thr = MOVING_SPEED_KMH.get(group, 0.0)
    moving = (np.isfinite(spd) & (spd > thr)) if spd is not None else np.ones(len(t), bool)
    out[F_MOVING] = float(dt_s[moving].sum())
    d = ch.get("elapseddistance")
    if d is not None and np.isfinite(d).any():
        out[F_DISTANCE] = float(np.nanmax(d))
    e = ch.get("elevation")
    if e is not None and np.isfinite(e).sum() > 30:
        ee = e.copy()
        ok = np.isfinite(ee)
        ee[~ok] = np.interp(np.flatnonzero(~ok), np.flatnonzero(ok), ee[ok]) if (~ok).any() else ee[~ok]
        sm = _rolling(ee, 30)
        de = np.diff(sm)
        out[F_CLIMBING] = float(de[de > 0].sum())
        out[F_DESCENDING] = float(-de[de < 0].sum())
        if out[F_DURATION] > 0:
            # WKO5 4224 vam = round(climbing / range length · 3600), m/h
            # (docs/wko5-internals/workout-metrics.md); without it every FIT
            # workout's `vam` was NaN and the VAM charts were empty
            out[F_VAM] = float(round(out[F_CLIMBING] / out[F_DURATION] * 3600.0))
    p = ch.get("power")
    if p is not None and np.isfinite(p).any() and np.nanmax(p) > 0:
        roll = _rolling(p, 30)
        if len(roll):
            out[F_NP] = float(np.mean(roll ** 4) ** 0.25)
        out[F_TSSDURATION] = float(dt_s[np.isfinite(p)].sum())
        out[F_WORK] = float(np.nansum(np.where(np.isfinite(p), p, 0.0) * dt_s))
    hr = ch.get("heartrate")
    if hr is not None and lthr:
        v, iff = hr_tss(list(t), [None if not np.isfinite(h) else float(h) for h in hr], lthr)
        if v is not None:
            out[F_HRTSS] = v
        if iff is not None:
            out[F_HRIF] = iff
    if group == "run" and spd is not None and d is not None and e is not None and moving.any():
        # grade over ~10 s of distance; grade-adjusted speed averaged over moving time
        dd = np.diff(np.nan_to_num(d), prepend=0.0) * 1000.0
        de = np.diff(np.nan_to_num(e), prepend=0.0)
        gr = np.clip(np.divide(_smooth(de), _smooth(dd), out=np.zeros_like(dd), where=_smooth(dd) > 0.5),
                     -0.45, 0.45)
        s = np.where(np.isfinite(spd), spd, 0.0) / 3.6
        gap = np.array([M.grade_adjusted_speed(si, gi) for si, gi in zip(s, gr)])
        w = dt_s * moving
        v = float((gap * w).sum() / w.sum()) if w.sum() > 0 else 0.0
        if v > 0.3:
            out[F_NGP] = 1000.0 / v / 60.0          # min/km
            out[F_PACE_TSSDURATION] = out[F_MOVING]
    return out


def _json_default(o):
    """numpy scalars / arrays in a cached_series value."""
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def _smooth(x: np.ndarray, n: int = 10) -> np.ndarray:
    k = np.ones(n) / n
    return np.convolve(np.nan_to_num(x), k, mode="same") * n


class PdMemo:
    """Disk memo of racepower.athlete._pd_mftp (the as-of PD refit behind
    cp_as_of: ~670 refits for the LTHR estimates of a full COROS history,
    the slowest part of a build). One entry per day, valid while the day's
    inputs hash the same: the runs of its 90-day window (file stamp, power
    source / use, power corrections, NP), the synced FIT files cptest.curves
    adds for that window (name, size, mtime), the watch-power / bad-file
    settings and overrides, and the code of the fit. A sync adding today's
    run invalidates only the days whose window holds it."""
    MISS = object()

    def __init__(self, ds: "FitFolderDataset"):
        self.ds = ds
        self.path = ds._store.home / "pd_mftp.json"
        self._lock = threading.Lock()
        self._prep = None
        self.dirty = False
        try:
            import json
            self.store = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self.store = {}

    def _prepare(self):
        import bisect  # noqa: F401
        import hashlib
        import inspect
        from backend.engine import bad_activity as BA
        from backend.engine import power_source as PS
        from backend.engine.algorithms import wko5_meanmax, wko5_pdmodel
        from backend.engine.racepower import athlete as A
        from backend.engine.racepower import cptest as T
        from backend.engine.racepower import weather as WX
        from backend.engine.wko5expr.fitcache import stamp_of
        ds = self.ds
        code = hashlib.sha1()
        for m in (A, T, wko5_pdmodel, wko5_meanmax, PS):
            try:
                code.update(inspect.getsource(m).encode("utf-8"))
            except (OSError, TypeError):
                code.update(m.__name__.encode())
        days, rows = [], []
        for w in ds.workouts:
            if w.sport != "run":
                continue
            try:
                st = stamp_of(ds.dir / w.entry.file)
            except OSError:
                st = None
            days.append(w.day)
            rows.append((w.entry.file, st, A.power_ok(ds, w), A.power_source(ds, w),
                         ds._corr_sig(w.entry.file, "power"), w.metrics.get("np")))
        files = []
        root = Path(WX.HOME) / "fit"
        if root.exists():
            for p in root.rglob("*.fit"):
                d = T._file_date(p)
                if d is None:
                    continue
                try:
                    s = p.stat()
                except OSError:
                    continue
                files.append((d, str(p.relative_to(root)), s.st_size, int(s.st_mtime)))
        files.sort()
        from backend.engine.wko5expr.datasource import primary_info
        # cptest.curves follows the 主要資料來源 (cptest.secondary_duplicates)
        glob = (code.hexdigest(), A.CP_WINDOW_DAYS, bool(ds.accept_watch_power), BA.read_setting(True),
                BA.overrides_stamp(), str(WX.HOME), repr(primary_info()))
        self._prep = (days, rows, files, glob)

    def sig(self, day: dt.date) -> str:
        import bisect
        import hashlib
        from backend.engine.racepower import athlete as A
        if self._prep is None:
            self._prepare()
        days, rows, files, glob = self._prep
        tday = date_to_day(day)
        lo = bisect.bisect_right(days, tday - A.CP_WINDOW_DAYS)
        hi = bisect.bisect_left(days, tday + 1)
        since = day - dt.timedelta(days=A.CP_WINDOW_DAYS - 1)
        fl = [f for f in files if since <= f[0] <= day]
        return hashlib.sha1(repr((glob, rows[lo:hi], fl)).encode("utf-8")).hexdigest()

    # kinds: "" = _pd_mftp (the LTHR estimate's cp_as_of; watch power as a
    # fallback), "stryd" = the charts' Stryd-only CP fit (_estimate_cp). Each
    # kind's entry also carries a hash of the code that makes it.
    @staticmethod
    def _key(day: dt.date, kind: str) -> str:
        return f"{kind}:{day.isoformat()}" if kind else day.isoformat()

    def _kind_sig(self, day: dt.date, kind: str) -> str:
        s = self.sig(day)
        if kind == "stryd":
            import hashlib
            import inspect
            src = inspect.getsource(FitFolderDataset._estimate_cp) + repr(CP_FIT_MIN_RUNS)
            s = hashlib.sha1((s + src).encode("utf-8")).hexdigest()
        return s

    def get(self, day: dt.date, kind: str = ""):
        with self._lock:
            hit = self.store.get(self._key(day, kind))
        if hit is None:
            return self.MISS
        try:
            ok = hit[0] == self._kind_sig(day, kind)
        except Exception:                        # noqa: BLE001 — no memo, just refit
            return self.MISS
        return hit[1] if ok else self.MISS

    def put(self, day: dt.date, value, kind: str = "") -> None:
        try:
            s = self._kind_sig(day, kind)
        except Exception:                        # noqa: BLE001
            return
        with self._lock:
            self.store[self._key(day, kind)] = [s, value]
            self.dirty = True

    def flush(self) -> None:
        import json
        import os
        with self._lock:
            if not self.dirty:
                return
            text = json.dumps(self.store)
            self.dirty = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"pd_mftp.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(text, "utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("FIT dataset: could not write the PD memo (%s)", type(e).__name__)


def default_athlete() -> Athlete:
    from backend.files.wko5chart_reader import Record
    return Athlete(first_name=None, last_name=None, ctlconstant=42.0, atlconstant=7.0,
                   settings={}, pmc_snapshot={}, workouts=[], root=Record())


class FitFolderDataset(Dataset):
    """Dataset over <fit_dir>/**/*.fit (optionally .fit.gz)."""

    def __init__(self, fit_dir: str | Path, settings_dir: Optional[str | Path] = None,
                 today: Optional[dt.date] = None, config: Optional[EngineConfig] = None,
                 corrections: Optional[CorrectionStore] = None, source: str = "fit",
                 tz: Optional[dt.tzinfo] = None, classifications: Optional[dict] = None,
                 athlete_settings: Optional[list] = None, estimate_thresholds: Optional[bool] = None,
                 accept_watch_power: Optional[bool] = None, exclude_bad: Optional[bool] = None,
                 merge: Optional[tuple] = None, primary: Optional[str] = None,
                 primary_status: Optional[dict] = None):
        """merge = sub-folders of `fit_dir` to merge (("coros", "tp"): the
        "synced" source): one file per activity, the primary source's when
        both have it (sync/primary.py merge), the other only for activities
        the primary lacks. primary = "auto" | "coros" | "trainingpeaks" (or
        the folder name "tp"); None = the 主要資料來源 setting.
        primary_status = {db source: last sync status} for 自動 (None = the
        settings store)."""
        from backend.engine.planning import Plan
        from backend.engine.wko5expr import buildstate, fitcache
        from backend.engine.wko5expr.datasource import athlete_tz
        self.dir = Path(fit_dir)
        self.source = source
        self.merge = tuple(merge) if merge else None
        self._origin: dict[int, str] = {}           # workout idx -> folder (coros / tp) it was read from
        self.merge_info: Optional[dict] = None
        # same zone as the sync's local workout dates: athlete.timezone setting
        # -> WKO5COACH_TZ -> system
        self.tz = tz or athlete_tz()
        self.config = config or EngineConfig()
        self._init_power_policy(accept_watch_power)
        self.corrections = None if self.config.parity else (corrections or CorrectionStore())
        self._init_exclusion_policy(exclude_bad)
        self.plan = Plan() if self.config.parity else Plan.load()
        self.athlete = default_athlete()
        # where the thresholds / weight come from (module docstring): the plan's
        # dated rows (Dataset.setting) -> athlete_settings in the app DB -> as-of
        # estimates from these FITs -> unset. The WKO5 athlete file only when
        # asked for explicitly (settings_dir; charts.fit_settings_from_wko5).
        self.settings_from = "app"
        self._setting_labels: dict[str, str] = {}
        self.settings_ignored: list[dict] = []
        if settings_dir:
            try:
                self.athlete = read_athlete(next(Path(settings_dir).glob("*.wko5athlete")))
                self.settings_from = "wko5"
            except (StopIteration, OSError) as e:
                log.warning("FIT dataset: no WKO5 athlete settings (%s); using defaults", type(e).__name__)
        if self.settings_from == "app":
            self._load_db_settings(athlete_settings)
        self._tp_tss = {}
        self._moving_hrtss = {}
        self.today = date_to_day(today or dt.date.today())
        self.memo = {}
        self._series, self._series_dirty = {}, set()
        self._series_lock = threading.Lock()
        self.workouts: list[Workout] = []
        # every per-file result (parsed channels, power source, bad-file
        # features, workout fields, hrTSS) comes from the persistent cache
        # (fitcache.py); only new / changed files are parsed, in a process
        # pool when there are many. Channels are loaded lazily (LazyFiles).
        prog = buildstate.get(source)
        prog.phase("scan")
        if self.merge:
            self._store = fitcache.MultiFitStore(self.dir, self.merge)
            paths = []
            for f in self.merge:
                sub = self.dir / f
                paths += [p for p in sorted(list(sub.rglob("*.fit")) + list(sub.rglob("*.fit.gz")))
                          if not p.is_symlink()]
        else:
            self._store = fitcache.FitStore(self.dir)
            paths = [p for p in sorted(list(self.dir.rglob("*.fit")) + list(self.dir.rglob("*.fit.gz")))
                     if not p.is_symlink()]
        self._files = fitcache.LazyFiles(self._store)
        self._store.ensure(paths, progress=prog)
        entries = []
        for p in paths:
            e = self._store.entry(p)
            meta = (e or {}).get("meta") or {"error": "unreadable"}
            if meta.get("error"):
                log.warning("FIT dataset: skipping unreadable file (%s)", meta["error"])
                continue
            if not meta.get("start") or not meta.get("n"):
                continue
            start = dt.datetime.fromisoformat(meta["start"])
            # FIT times are UTC; WKO5 dates are the athlete's local wall clock
            if start.tzinfo is None:
                start = start.replace(tzinfo=dt.timezone.utc)
            utc = start
            start = start.astimezone(self.tz).replace(tzinfo=None)
            entries.append((start, p, meta, utc))
        if self.merge:
            entries = self._merge_entries(entries, primary, primary_status)
        entries = [(s, p, m) for s, p, m, _ in entries]
        entries.sort(key=lambda x: x[0])
        # trail / road from the app DB (workout_files.trail_classification,
        # user overrides included), the FIT sub_sport only as a fallback
        self._classes = load_classifications() if classifications is None else classifications
        prog.phase("assemble", total=len(entries))
        for start, p, meta in entries:
            prog.tick()
            sport_raw, sub = (meta.get("sport") or "", meta.get("sub_sport"))
            if isinstance(sport_raw, str) and "/" in sport_raw:
                sport_raw, sub = sport_raw.split("/", 1)
            group, stype = sport_of(sport_raw, sub, classification_for(p, self._classes))
            rel = p.relative_to(self.dir).as_posix()
            idx = len(self.workouts)
            entry = WorkoutEntry(file=rel, sport=stype.title(), sport_group=group.title(), start=start,
                                 ftp=None, metrics={}, record=None)
            # WKO5 marks trail runs with the "runningtrail" tag; several
            # engine paths (thresholds, quality gate, status, achievements)
            # read only the tag, so a FIT trail run carries it too; likewise
            # hikes carry "hiking" / "mountaineering" (the 專項期 charts
            # select hikes with hastag(), as on the user's WKO5 data)
            tags = list(TYPE_TAGS.get(stype, []))
            w = Workout(idx=idx, entry=entry, day=date_to_day(start), sport=group, sport_type=stype, tags=tags)
            # a bad file (car / bike segment, impossible power: bad_activity.py)
            # never enters ds.workouts; it is listed in ds.excluded
            if self._exclusion(w, lambda rel=rel: self._fit_bad_features(rel), duration=meta.get("duration")):
                continue
            self._files.add(idx, p, rel, stype, start.isoformat())
            self.workouts.append(w)
            if self.merge:
                self._origin[idx] = rel.split("/", 1)[0]
            # stryd / watch / none (power_source.py)
            self._power_src[idx] = self._store.power(rel, lambda rel=rel, m=meta: self._classify_power(rel, m))
            if not self.accept_watch_power and self._power_src[idx] == "watch":
                self._power_blocked.add(rel)            # no power TSS from watch power
            entry.metrics = self._fit_fields(w, group)
            self._add_hr_fields(w, self.sport_setting("thr", w))
            w.metrics = self._metrics(w)
        self.first_day = int(np.floor(self.workouts[0].day)) if self.workouts else int(self.today)
        self.last_day = int(np.floor(self.workouts[-1].day)) if self.workouts else int(self.today)
        # None = auto: only on the app's own data (an app DB exists). The
        # estimate reads the user's synced FIT folder too (pd_model ->
        # cptest.curves(~/.wko5coach)), so a bare folder (tests, a scratch
        # copy) does not estimate unless asked to.
        if estimate_thresholds is None:
            estimate_thresholds = _app_db() is not None
        self.pd_memo = PdMemo(self)               # racepower.athlete._pd_mftp on disk
        if self.settings_from == "app" and estimate_thresholds and self.workouts:
            prog.phase("estimate")
            if self._estimate_settings_memo():
                for w in self.workouts:          # hrTSS / rTSS / power TSS with the estimated thresholds
                    self._refresh_hr_fields(w)
                    w.metrics = self._metrics(w)
        prog.phase("finish")
        if self.config.moving_hr_tss:
            self._apply_moving_hrtss()
        self._apply_elevation_bonus()
        self._store.save()

    # FIT data: channels come from the parsed files, not .wko4
    def wko4(self, idx: int) -> Optional[Wko4File]:
        return self._files.get(idx)

    # ---- the merged "synced" source (backend/sync/primary.py) ---------------
    def _merge_entries(self, entries: list, primary: Optional[str], status: Optional[dict]) -> list:
        """One readable file per activity: the primary's when both folders
        have one starting within 2 min (sync/dedup.py's window), the other
        folder's only for activities the primary lacks; two files of one
        activity in the same folder keep the one with more samples. Nothing per value:
        a primary file without power, or one excluded as a bad file, is never
        replaced by the other source's (「算不出來就不要補了」). 自動 = the folder
        with the most recent complete data (primary.choose_auto)."""
        from backend.sync import primary as P
        if primary is None or status is None:
            from backend.engine.wko5expr.datasource import primary_info
            setting, st = primary_info()
            primary = setting if primary is None else primary
            status = st if status is None else status
        setting = P.normalize(P.to_db(primary))
        by_src: dict[str, list] = {}
        for s, p, m, utc in entries:
            by_src.setdefault(P.to_db(p.relative_to(self.dir).parts[0]), []).append(utc)
        stats = {s: P.stats_from_starts(v, (status or {}).get(s)) for s, v in by_src.items()}
        chosen = P.resolve(setting, stats)
        items = [(utc, P.to_db(p.relative_to(self.dir).parts[0]), (s, p, m, utc)) for s, p, m, utc in entries]
        # several files of one activity in the same source: the most samples, then the longest
        kept, dropped = P.merge(items, chosen, quality=lambda x: (x[2].get("n") or 0, x[2].get("duration") or 0))
        counts = {}
        for _, src, _ in kept:
            counts[src] = counts.get(src, 0) + 1
        self.merge_info = {"setting": setting, "primary": chosen, "auto": setting == P.AUTO,
                           "kept": counts, "replaced": sum(1 for _, s, _ in dropped if s != chosen),
                           "duplicates": sum(1 for _, s, _ in dropped if s == chosen),
                           "stats": {s: {**v, "latest": v["latest"].isoformat() if v["latest"] else None}
                                     for s, v in stats.items()}}
        return [x for _, _, x in kept]

    def file_origin(self, w: Workout) -> Optional[str]:
        """Which synced source this workout's file came from: "coros" / "tp"
        (the folder), for a single-folder dataset its source."""
        if self.merge:
            return self._origin.get(w.idx)
        return self.source if self.source in ("coros", "tp") else None

    # ---- per-file results, from the persistent cache (fitcache.py) ---------
    def _arrays(self, rel: str):
        return self._store.arrays(rel)

    def _fit_bad_features(self, rel: str) -> dict:
        """bad_activity.features of a FIT (approved power corrections applied),
        cached per file and corrections signature."""
        from backend.engine import bad_activity as BA
        sig = self._corr_sig(rel, "power")

        def compute():
            t, ch = self._arrays(rel)
            tl = t.tolist()
            pw = ch.get("power")
            pw = fitcache_list(pw) if pw is not None else None
            if pw is not None and self.corrections is not None:
                pw = self.corrections.apply(rel, "power", tl, pw)
            d = ch.get("elapseddistance")
            return BA.features(tl, fitcache_list(d) if d is not None else None, pw)
        return self._store.derived(rel, "bad", sig, compute)

    def _classify_power(self, rel: str, meta: dict) -> str:
        from backend.engine.power_source import classify
        _, ch = self._arrays(rel)
        return classify({k: fitcache_list(a) for k, a in ch.items()}, bool(meta.get("stryd_device")))

    def _fit_fields(self, w: Workout, group: str) -> dict:
        """workout_fields without the LTHR-dependent hrTSS / hrIF (cached per
        file and sport group); _add_hr_fields adds those."""
        def compute():
            t, ch = self._arrays(w.entry.file)
            return workout_fields(t, ch, group, None)
        return self._store.fields(w.entry.file, group, compute)

    def _hr_tss(self, w: Workout, lthr: float, moving_kmh: Optional[float] = None):
        """(hrTSS, hrIF) of one workout for `lthr` (moving_kmh: moving-time
        only, hrTSS alone), cached per file and inputs."""
        from backend.engine.algorithms.wko5_hr import hr_tss
        rel = w.entry.file

        def compute():
            t, ch = self._arrays(rel)
            hr = ch.get("heartrate")
            if hr is None or not len(t):
                return [None, None]
            hv = fitcache_list(hr)
            if moving_kmh is None:
                return list(hr_tss(t.tolist(), hv, lthr))
            sp = ch.get("speed")
            mask = [s is not None and s > moving_kmh for s in fitcache_list(sp)] if sp is not None \
                else [True] * len(t)
            return [hr_tss(t.tolist(), hv, lthr, moving=mask)[0], None]
        if moving_kmh is None:
            return self._store.derived(rel, "hr", f"{lthr:g}", compute)
        return self._store.derived(rel, "mhr", f"{lthr:g}|{moving_kmh:g}", compute)

    def _add_hr_fields(self, w: Workout, lthr: Optional[float]) -> None:
        m = w.entry.metrics
        m.pop(F_HRTSS, None)
        m.pop(F_HRIF, None)
        if not lthr or "heartrate" not in self._store_channels(w):
            return
        v, iff = self._hr_tss(w, float(lthr))
        if v is not None:
            m[F_HRTSS] = v
        if iff is not None:
            m[F_HRIF] = iff

    def _store_channels(self, w: Workout) -> list:
        e = self._store.files.get(w.entry.file) or {}
        return (e.get("meta") or {}).get("channels") or []

    def _apply_moving_hrtss(self) -> None:
        from backend.engine.algorithms.wko5_time import MOVING_SPEED_KMH
        for w in self.workouts:
            if not self._is_hr_sourced(w):
                continue
            lthr = self.sport_setting("thr", w)
            if not lthr or "heartrate" not in self._store_channels(w):
                continue
            v = self._hr_tss(w, float(lthr), moving_kmh=MOVING_SPEED_KMH.get(w.sport, 0.0))[0]
            if v is not None:
                w.metrics["tss"] = v
                w.metrics["hrtss_moving"] = v

    # ---- thresholds / weight without the WKO5 athlete file -------------------
    def _load_db_settings(self, rows: Optional[list] = None) -> None:
        """athlete_settings rows (app DB, read-only) -> dated settings.
        Used: weight_kg -> weight, run_ftp_w -> runftp, threshold_pace_s_per_km
        -> runtpace. NOT used: `lthr` and `ftp_w` — the only automatic writer
        is coros_client.login, which stores COROS's account zoneData.lthr /
        .ftp without saying which sport they are for (the 2026-09-30 row:
        LTHR 182, above the 171 bpm peak of that day's maximal 12′ test, so
        not this athlete's running LTHR); they stay in settings_ignored."""
        rows = read_athlete_settings() if rows is None else rows
        s = self.athlete.settings
        for r in sorted(rows, key=lambda r: str(r.get("effective_date"))):
            try:
                d = dt.date.fromisoformat(str(r["effective_date"])[:10])
            except (KeyError, ValueError):
                continue
            for col, name, f in (("weight_kg", "weight", 1.0), ("run_ftp_w", "runftp", 1.0),
                                 ("threshold_pace_s_per_km", "runtpace", 1 / 60.0)):
                v = r.get(col)
                if v:
                    # a threshold never applies before its date; weight does
                    # (as Plan.weight_on: the earliest entry before the first)
                    s.setdefault(name, [] if name == "weight" else [NOT_BEFORE]).append((d, float(v) * f))
                    self._setting_labels[name] = SETTING_LABELS["db"]
            for col in ("lthr", "ftp_w"):
                if r.get(col):
                    self.settings_ignored.append({"field": col, "value": r[col], "date": d.isoformat(),
                                                  "why": IGNORED_WHY})

    def _estimate_settings(self) -> bool:
        """As-of running LTHR estimated from these FITs, on a grid of dates
        every ESTIMATE_STEP_DAYS: the value estimated on a grid day (only runs
        up to that day: thresholds.estimate = Friel 30-min-segment LTHR, each
        run against racepower.athlete.cp_as_of its own date) applies from that
        day to the next. A plan test still wins (Dataset.setting looks at the
        plan first). True when anything was set.

        Run FTP (power TSS) is NOT filled from an estimate: cp_as_of's PD
        refit falls back to watch power and gave 362–384 W for 2024-06…12 on
        this athlete's COROS data (plan CP 204 W), which would cut every
        power TSS there ~4×. Without a plan / DB value those runs fall back to
        hrTSS (with the estimated LTHR) or stay unset.

        CP for the charts (Dataset.cp: Palladino zones, `cp` in expressions)
        is filled before the first plan CP by a Stryd-only PD fit on the same
        grid (_estimate_cp); it does not change any TSS."""
        from backend.engine.racepower import athlete as A
        from backend.engine.thresholds import estimate
        runs = [w for w in self.workouts if w.sport == "run"]
        if not runs:
            return False
        # module-level memos keyed on id(ds): a freed dataset's id can be reused
        for memo in (A._cp_memo, A._est_memo):
            for k in [k for k in memo if k[0] == id(self)]:
                del memo[k]
        first = runs[0].entry.start.date() + dt.timedelta(days=ESTIMATE_STEP_DAYS)
        end = min(day_to_date(self.today), runs[-1].entry.start.date())
        thr = []
        day = first
        while day <= end:
            try:
                est = estimate(self, day, cp_of=lambda d: A.cp_as_of(self, d))
            except Exception as e:           # noqa: BLE001
                log.warning("FIT dataset: threshold estimate %s failed (%s)", day, type(e).__name__)
                est = {}
            v = (est.get("lthr") or {}).get("value")
            if v:
                thr.append((day, float(v)))
            day += dt.timedelta(days=ESTIMATE_STEP_DAYS)
        if thr:
            self.athlete.settings["runthr"] = [NOT_BEFORE] + thr
            self._setting_labels["runthr"] = SETTING_LABELS["estimate"]
        self._estimate_cp(first, end)
        self.memo.clear()
        return bool(thr)

    # ---- the as-of estimates, memoised on disk ------------------------------
    ESTIMATE_MEMO_KEEP = 4

    def _estimate_key(self) -> str:
        """Everything _estimate_settings reads: the workouts (file stamps,
        sport, tags, power source / blocking, exclusions), the thresholds and
        weight before the estimate (plan, DB), the corrections, the engine
        config, today, and the code of the estimate (thresholds.py, race-power
        athlete / CP / PD model, mean-max)."""
        import hashlib
        import inspect
        from backend.engine import thresholds
        from backend.engine.algorithms import power_model, wko5_meanmax, wko5_pdmodel
        from backend.engine.racepower import athlete, cp
        from backend.engine.wko5expr.fitcache import stamp_of
        m = hashlib.sha1()

        def add(x):
            m.update(repr(x).encode("utf-8"))
            m.update(b"\x00")
        for mod in (thresholds, athlete, cp, power_model, wko5_pdmodel, wko5_meanmax):
            try:
                add(inspect.getsource(mod))
            except (OSError, TypeError):
                add(mod.__name__)
        add(inspect.getsource(type(self)._estimate_settings))
        add(inspect.getsource(type(self)._estimate_cp))
        add((ESTIMATE_STEP_DAYS, CP_FIT_MIN_RUNS))
        add(day_to_date(self.today).isoformat())
        add(self.config.to_dict())
        add(self.accept_watch_power)
        for w in self.workouts:
            try:
                st = stamp_of(self.dir / w.entry.file)
            except OSError:
                st = None
            add((w.entry.file, st, w.sport, w.sport_type, w.tags, w.entry.start.isoformat(),
                 self._power_src.get(w.idx), w.entry.file in self._power_blocked))
        add(sorted(x["file"] for x in self.excluded))
        add(sorted((k, [(d.isoformat(), v) for d, v in vals]) for k, vals in self.athlete.settings.items()))
        add(repr(self.plan))                     # a dataclass: events, phases, thresholds, weights, profile
        add([(c.file, c.channel, c.t_start, c.t_end) for c in (self.corrections.items if self.corrections else [])])
        return m.hexdigest()

    def _estimate_settings_memo(self) -> bool:
        """_estimate_settings, its result (the dated settings / labels it
        added) memoised on disk under _estimate_key: a restart with the same
        data does not re-run the as-of estimates (minutes on a full COROS
        history)."""
        import json
        path = self._store.home / "estimate.json"
        try:
            key = self._estimate_key()
        except Exception as e:                   # noqa: BLE001 — no memo, just estimate
            log.warning("FIT dataset: estimate memo key failed (%s)", type(e).__name__)
            return self._estimate_settings()
        try:
            memo = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            memo = {}
        hit = memo.get(key)
        if hit is not None:
            for name, vals in hit["settings"].items():
                self.athlete.settings[name] = [(dt.date.fromisoformat(d), v) for d, v in vals]
            self._setting_labels.update(hit["labels"])
            self._cp_est = [{**r, "date": dt.date.fromisoformat(r["date"])} for r in hit.get("cp_est") or []]
            self.memo.clear()
            return bool(hit["ret"])
        before = {k: list(v) for k, v in self.athlete.settings.items()}
        labels = dict(self._setting_labels)
        ret = self._estimate_settings()
        self.pd_memo.flush()
        changed = {k: [(d.isoformat(), val) for d, val in v] for k, v in self.athlete.settings.items()
                   if before.get(k) != list(v)}
        memo.pop(key, None)
        memo[key] = {"ret": bool(ret), "settings": changed,
                     "labels": {k: v for k, v in self._setting_labels.items() if labels.get(k) != v},
                     # the charts' Stryd-only CP fits (_estimate_cp), read by cp / cp_info
                     "cp_est": [{**r, "date": r["date"].isoformat()} for r in self._cp_est]}
        while len(memo) > self.ESTIMATE_MEMO_KEEP:
            memo.pop(next(iter(memo)))
        try:
            import os
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"estimate.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(memo), "utf-8")
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as e:
            log.warning("FIT dataset: could not write the estimate memo (%s)", type(e).__name__)
        return ret

    # ---- CP for the charts before the first plan CP --------------------------
    _cp_est: list = []

    def _estimate_cp(self, first: dt.date, end: dt.date) -> None:
        """Stryd-only PD fits (racepower.athlete.pd_model: WKO5's PD model on
        the 90-day mean-max up to the grid day) every ESTIMATE_STEP_DAYS. Only
        runs whose power source is Stryd: watch-estimated power never enters
        (it gave the 362–384 W fits above), and bad activity files are already
        out of ds.workouts / cptest.curves. A grid day without Stryd runs gets
        no value (no fallback to watch power). Read by `cp` / `cp_info`; a plan
        CP test on or before the date wins."""
        from backend.engine.racepower import athlete as A
        from backend.engine import power_source as PS
        out = []
        day = first
        while day <= end:
            tday = date_to_day(day)
            runs = [w for w in self.workouts if w.sport == "run" and tday - A.CP_WINDOW_DAYS < w.day < tday + 1
                    and self.power_source(w) == PS.STRYD and self.power_ok(w)]
            pdm = None
            if len(runs) >= CP_FIT_MIN_RUNS:
                # the PD-refit disk memo (PdMemo, its own key "stryd"): keyed on
                # the day's 90-day window, so a restart / a sync refits only the
                # days whose window changed
                pdm = self.pd_memo.get(day, "stryd")
                if pdm is self.pd_memo.MISS:
                    try:
                        pdm = A.pd_model(self, day, runs, None)
                        pdm = None if not pdm else {k: pdm.get(k) for k in ("mftp", "frc", "n_points")}
                        self.pd_memo.put(day, pdm, "stryd")
                    except Exception as e:           # noqa: BLE001
                        pdm = None
                        log.warning("FIT dataset: CP fit %s failed (%s)", day, type(e).__name__)
            if pdm:
                out.append({"date": day, "cp": float(pdm["mftp"]), "frc_j": float(pdm["frc"]),
                            "runs": len(runs), "n_points": pdm.get("n_points")})
            day += dt.timedelta(days=ESTIMATE_STEP_DAYS)
        self._cp_est = out

    def _cp_fit_on(self, day: dt.date) -> Optional[dict]:
        hit = None
        for r in self._cp_est:
            if r["date"] <= day:
                hit = r
        return hit

    def cp(self, w: Workout) -> Optional[float]:
        """Dataset.cp (the plan's dated CP test, else athlete_settings
        run_ftp_w), else the Stryd-only PD fit in effect on the run's date."""
        v = super().cp(w)
        if v is not None or w.sport != "run":
            return v
        fit = self._cp_fit_on(day_to_date(w.day))
        return fit["cp"] if fit else None

    def cp_info(self, w: Workout) -> dict:
        """CP / W′ in effect for `w` and where they come from (UI labels)."""
        day = day_to_date(w.day)
        rows = sorted((t for t in self.plan.thresholds if t.cp is not None and t.date[:10] <= day.isoformat()),
                      key=lambda t: t.date)
        if rows:
            t = rows[-1]
            return {"cp": float(t.cp), "source": f"你的測試 {t.date[:10]}", "date": t.date[:10],
                    "wprime": t.wprime, "wprime_source": "測試（兩點法）" if t.wprime else None}
        v = super().cp(w)
        if v is not None:
            return {"cp": v, "source": self.setting_label("runftp"), "date": None, "wprime": None,
                    "wprime_source": None}
        fit = self._cp_fit_on(day)
        if fit:
            return {"cp": fit["cp"], "date": fit["date"].isoformat(),
                    "source": f"推估：Stryd 功率 PD 模型 {fit['date'].isoformat()}（近 90 天 {fit['runs']} 次 Stryd 跑步，"
                              f"排除手錶功率）",
                    "wprime": fit["frc_j"], "wprime_source": "推估：PD 模型 FRC（≈ W′）"}
        return {"cp": None, "source": "未設定（測試日以前沒有 Stryd 功率可擬合）", "date": None,
                "wprime": None, "wprime_source": None}

    def _refresh_hr_fields(self, w: Workout) -> None:
        """Recompute the LTHR-dependent hrTSS / hrIF after the estimates."""
        self._add_hr_fields(w, self.sport_setting("thr", w))

    def setting_label(self, name: str, default: str = "WKO5 設定") -> str:
        """Where a dated setting (runthr / runftp / weight ...) came from."""
        if self.settings_from == "wko5":
            return SETTING_LABELS["wko5"]
        return self._setting_labels.get(name.lower(), SETTING_LABELS["unset"])

    def curve_cache(self, expr: str) -> dict:
        return {}                         # no WKO5 Cache5 for FIT folders

    SERIES_VARIANTS = 4     # thresholds in effect kept per file and key (before / after the estimates, ...)

    def cached_series(self, key: str, w: Workout, compute):
        """Disk memo per FIT file (fitcache folder, series_<key>.json), keyed
        like the WKO5 Dataset's on the file stamp, the corrections and the
        thresholds in effect, so a threshold change never serves a stale
        value. A few threshold variants are kept per file: a build computes
        with the plan / DB values, then again with the estimated LTHR."""
        import json
        from backend.engine.wko5expr.dataset import _cache_read, _safe
        from backend.engine.wko5expr.fitcache import stamp_of
        with self._series_lock:
            store = self._series.get(key)
            if store is None:
                store = self._series[key] = _cache_read(self._store.home / f"series_{_safe(key)}.json")
        try:
            st = stamp_of(self.dir / w.entry.file)
        except OSError:
            return compute()
        sk = json.dumps(st + [self._corr_sig(w.entry.file), self._settings_sig(w)])
        with self._series_lock:
            slot = store.get(w.entry.file)
            if isinstance(slot, dict) and sk in slot:
                return slot[sk]
        val = compute()
        with self._series_lock:
            slot = store.get(w.entry.file)
            if not isinstance(slot, dict):
                slot = store[w.entry.file] = {}
            slot.pop(sk, None)
            slot[sk] = val
            while len(slot) > self.SERIES_VARIANTS:
                slot.pop(next(iter(slot)))
            self._series_dirty.add(key)
        return val

    def flush_series(self) -> None:
        import json
        import os
        from backend.engine.wko5expr.dataset import _safe
        memo = getattr(self, "pd_memo", None)
        if memo is not None:
            memo.flush()
        with self._series_lock:
            todo = {key: {f: dict(v) for f, v in self._series[key].items()} for key in self._series_dirty}
            self._series_dirty.clear()
        for key, entries in todo.items():
            path = self._store.home / f"series_{_safe(key)}.json"
            try:
                text = json.dumps({"files": entries}, default=_json_default)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(text, "utf-8")
                os.replace(tmp, path)
            except (OSError, TypeError, ValueError) as e:
                log.warning("FIT dataset: could not write the %s cache (%s)", key, type(e).__name__)

    @property
    def mftp_run(self) -> Optional[float]:
        from backend.files.wko5_athlete import pd_snapshot
        if not hasattr(self, "_mftp_run"):
            try:
                self._mftp_run = pd_snapshot(self.athlete.root).get(("mftp", "Run"))
            except Exception:
                self._mftp_run = None
        return self._mftp_run


def dataset_for_source(source: str, wko5_dir: Path, config: Optional[EngineConfig] = None,
                       today: Optional[dt.date] = None):
    """The Dataset the charts should read for charts.data_source. A COROS /
    TP source takes its thresholds / weight from the app (plan, DB,
    estimates); the WKO5 athlete file only when the user opted in
    (setting charts.fit_settings_from_wko5 = true) — WKO5 is a cross-check
    reference, not the default data path. "synced" = both synced folders,
    the 主要資料來源's file per activity (FitFolderDataset merge)."""
    if source in ("synced", "coros", "tp"):
        from backend.sync import storage
        from backend.engine.wko5expr.datasource import read_setting
        use_wko5 = read_setting(WKO5_OPT_IN_KEY, False) is True
        if source == "synced":
            return FitFolderDataset(storage.FIT_ROOT, settings_dir=wko5_dir if use_wko5 else None,
                                    config=config, today=today, source=source, merge=tuple(storage.SOURCES))
        return FitFolderDataset(storage.source_dir(source), settings_dir=wko5_dir if use_wko5 else None,
                                config=config, today=today, source=source)
    return Dataset(wko5_dir, today=today, config=config)
