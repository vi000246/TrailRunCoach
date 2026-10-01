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
  "runningtrail" tag.
* thresholds / weight, in order: the season plan's dated rows (Dataset.setting
  / cp) -> athlete_settings in the app DB (weight, run_ftp_w, threshold pace;
  not lthr / ftp_w, see _load_db_settings) -> as-of LTHR / CP estimates from
  these FITs (_estimate_settings) -> unset ("未設定"). The WKO5 athlete file
  only when settings_dir is passed (dataset_for_source: the opt-in setting
  charts.fit_settings_from_wko5). PMC constants: 42 / 7 (WKO5's defaults).
* the app DB is opened read-only (a sync may be writing it).
* parity mode / own-formula config flags behave as for the WKO5 Dataset.
"""
from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.corrections import CorrectionStore
from backend.engine.wko5expr.dataset import (
    F_CLIMBING, F_DISTANCE, F_DURATION, F_HRIF, F_HRTSS, F_MOVING, F_NGP, F_NP,
    F_PACE_TSSDURATION, F_TSSDURATION, Dataset, Workout, date_to_day, day_to_date,
)
from backend.files.wko4_file import Channel, Wko4File
from backend.files.wko5_athlete import Athlete, WorkoutEntry, read_athlete

log = logging.getLogger(__name__)

F_DESCENDING, F_WORK = 4225, 4218

# 自組: how often the as-of LTHR / CP estimate is refreshed for a FIT source
# (a value estimated on a grid day applies until the next one). 30 days keeps
# a full-history load to ~one estimate per month; the thresholds.estimate
# windows (90 / 180 days) are much longer, so a finer grid changes little.
ESTIMATE_STEP_DAYS = 30
# Athlete.setting_on applies the earliest value backwards (WKO5's rule); a
# leading (date.min, None) entry stops that for thresholds that must not reach
# the days before they were known
NOT_BEFORE = (dt.date.min, None)
WKO5_OPT_IN_KEY = "charts.fit_settings_from_wko5"   # settings/repository.py DEFAULTS

SETTING_LABELS = {
    "wko5": "WKO5 athlete 檔（選用）",
    "db": "athlete_settings（app DB）",
    "estimate": "自動估算（當天以前的跑步，Friel 30 分鐘段）",
    "estimate_cp": "自動估算（當天以前 90 天的 PD 模型 mFTP）",
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


def _smooth(x: np.ndarray, n: int = 10) -> np.ndarray:
    k = np.ones(n) / n
    return np.convolve(np.nan_to_num(x), k, mode="same") * n


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
                 athlete_settings: Optional[list] = None, estimate_thresholds: bool = True):
        from backend.engine.planning import Plan
        from backend.engine.wko5expr.datasource import athlete_tz
        from backend.files.fit_to_channels import fit_to_channels
        self.dir = Path(fit_dir)
        self.source = source
        # same zone as the sync's local workout dates: athlete.timezone setting
        # -> WKO5COACH_TZ -> system
        self.tz = tz or athlete_tz()
        self.config = config or EngineConfig()
        self.corrections = None if self.config.parity else (corrections or CorrectionStore())
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
        self._files: dict[int, Wko4File] = {}
        self.workouts: list[Workout] = []
        entries = []
        for p in sorted(list(self.dir.rglob("*.fit")) + list(self.dir.rglob("*.fit.gz"))):
            if p.is_symlink():
                continue
            try:
                fc = fit_to_channels(p.read_bytes())
            except Exception as e:
                log.warning("FIT dataset: skipping unreadable file (%s)", type(e).__name__)
                continue
            start = fc.start_time
            if start is None or not fc.elapsedtime:
                continue
            # FIT times are UTC; WKO5 dates are the athlete's local wall clock
            if start.tzinfo is None:
                start = start.replace(tzinfo=dt.timezone.utc)
            start = start.astimezone(self.tz).replace(tzinfo=None)
            entries.append((start, p, fc))
        entries.sort(key=lambda x: x[0])
        # trail / road from the app DB (workout_files.trail_classification,
        # user overrides included), the FIT sub_sport only as a fallback
        self._classes = load_classifications() if classifications is None else classifications
        for start, p, fc in entries:
            sport_raw, sub = (fc.sport or "", getattr(fc, "sub_sport", None))
            if isinstance(sport_raw, str) and "/" in sport_raw:
                sport_raw, sub = sport_raw.split("/", 1)
            group, stype = sport_of(sport_raw, sub, classification_for(p, self._classes))
            rel = p.relative_to(self.dir).as_posix()
            idx = len(self.workouts)
            chans = {"elapsedtime": Channel("elapsedtime", list(fc.elapsedtime), 1.0, base=0.0)}
            for name, vals in fc.channels.items():
                chans[name] = Channel(name, list(vals), 1.0)
            self._files[idx] = Wko4File(path=str(p), sport=stype, start_time=start.isoformat(), device=None,
                                        weight_kg=None, original_type="fit", original_bytes=None,
                                        channels=chans, ranges=[], info=None)
            entry = WorkoutEntry(file=rel, sport=stype.title(), sport_group=group.title(), start=start,
                                 ftp=None, metrics={}, record=None)
            # WKO5 marks trail runs with the "runningtrail" tag; several
            # engine paths (thresholds, quality gate, status, achievements)
            # read only the tag, so a FIT trail run carries it too
            tags = ["runningtrail"] if stype == "trail running" else []
            w = Workout(idx=idx, entry=entry, day=date_to_day(start), sport=group, sport_type=stype, tags=tags)
            self.workouts.append(w)
            t = _arr(fc.elapsedtime)
            ch = {k: _arr(v) for k, v in fc.channels.items()}
            entry.metrics = workout_fields(t, ch, group, self.sport_setting("thr", w))
            w.metrics = self._metrics(w)
        self.first_day = int(np.floor(self.workouts[0].day)) if self.workouts else int(self.today)
        self.last_day = int(np.floor(self.workouts[-1].day)) if self.workouts else int(self.today)
        if self.settings_from == "app" and estimate_thresholds and self.workouts:
            if self._estimate_settings():
                for w in self.workouts:          # hrTSS / rTSS / power TSS with the estimated thresholds
                    self._refresh_hr_fields(w)
                    w.metrics = self._metrics(w)
        if self.config.moving_hr_tss:
            self._apply_moving_hrtss()
        self._apply_elevation_bonus()

    # FIT data: channels come from the parsed files, not .wko4
    def wko4(self, idx: int) -> Optional[Wko4File]:
        return self._files.get(idx)

    def _apply_moving_hrtss(self) -> None:
        from backend.engine.algorithms.wko5_hr import hr_tss
        from backend.engine.algorithms.wko5_time import MOVING_SPEED_KMH
        for w in self.workouts:
            if not self._is_hr_sourced(w):
                continue
            f = self._files[w.idx]
            t, hr, sp = (f.channels.get(k) for k in ("elapsedtime", "heartrate", "speed"))
            if not (t and hr):
                continue
            thr = MOVING_SPEED_KMH.get(w.sport, 0.0)
            mask = [s is not None and s > thr for s in sp.values] if sp else [True] * len(t.values)
            v = hr_tss(t.values, hr.values, self.sport_setting("thr", w), moving=mask)[0]
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
        """As-of running LTHR and CP estimated from these FITs, on a grid of
        dates every ESTIMATE_STEP_DAYS: the value estimated on a grid day
        (only runs up to that day: racepower.athlete.cp_as_of = the WKO5 PD
        model refitted on the 90-day mean-max; thresholds.estimate = Friel
        30-min-segment LTHR against that CP) applies from that day to the
        next. A plan test still wins (Dataset.setting / cp look at the plan
        first); a DB run_ftp_w row is kept. True when anything was set."""
        from backend.engine.racepower import athlete as A
        from backend.engine.thresholds import estimate
        runs = [w for w in self.workouts if w.sport == "run"]
        if not runs:
            return False
        first = runs[0].entry.start.date() + dt.timedelta(days=ESTIMATE_STEP_DAYS)
        end = min(day_to_date(self.today), runs[-1].entry.start.date())
        s = self.athlete.settings
        thr, ftp = [], []
        day = first
        while day <= end:
            try:
                cp = A.cp_as_of(self, day)
                est = estimate(self, day, cp_of=lambda d: A.cp_as_of(self, d))
            except Exception as e:           # noqa: BLE001
                log.warning("FIT dataset: threshold estimate %s failed (%s)", day, type(e).__name__)
                cp, est = None, {}
            v = (est.get("lthr") or {}).get("value")
            if v:
                thr.append((day, float(v)))
            if cp and "runftp" not in s:
                ftp.append((day, float(cp)))
            day += dt.timedelta(days=ESTIMATE_STEP_DAYS)
        if thr:
            s["runthr"] = [NOT_BEFORE] + thr
            self._setting_labels["runthr"] = SETTING_LABELS["estimate"]
        if ftp:
            s["runftp"] = [NOT_BEFORE] + ftp
            self._setting_labels["runftp"] = SETTING_LABELS["estimate_cp"]
        self.memo.clear()
        return bool(thr or ftp)

    def _refresh_hr_fields(self, w: Workout) -> None:
        """Recompute the LTHR-dependent hrTSS / hrIF after the estimates."""
        from backend.engine.algorithms.wko5_hr import hr_tss
        f = self._files[w.idx]
        t, hr = f.channels.get("elapsedtime"), f.channels.get("heartrate")
        m = w.entry.metrics
        m.pop(F_HRTSS, None)
        m.pop(F_HRIF, None)
        lthr = self.sport_setting("thr", w)
        if not (t and hr and lthr):
            return
        v, iff = hr_tss(list(t.values), [None if h is None else float(h) for h in hr.values], lthr)
        if v is not None:
            m[F_HRTSS] = v
        if iff is not None:
            m[F_HRIF] = iff

    def setting_label(self, name: str, default: str = "WKO5 設定") -> str:
        """Where a dated setting (runthr / runftp / weight ...) came from."""
        if self.settings_from == "wko5":
            return SETTING_LABELS["wko5"]
        return self._setting_labels.get(name.lower(), SETTING_LABELS["unset"])

    def curve_cache(self, expr: str) -> dict:
        return {}                         # no WKO5 Cache5 for FIT folders

    def cached_series(self, key: str, w: Workout, compute):
        """In-memory memo (no disk cache keyed on .wko4 files), keyed like the
        WKO5 Dataset's disk cache on the corrections and the thresholds in
        effect, so a threshold change never serves a stale value."""
        k = (key, w.idx, self._corr_sig(w.entry.file), self._settings_sig(w))
        store = self.__dict__.setdefault("_mem_series", {})
        if k not in store:
            store[k] = compute()
        return store[k]

    def flush_series(self) -> None:
        pass

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
    reference, not the default data path."""
    if source in ("coros", "tp"):
        from backend.sync import storage
        from backend.engine.wko5expr.datasource import read_setting
        use_wko5 = read_setting(WKO5_OPT_IN_KEY, False) is True
        return FitFolderDataset(storage.source_dir(source), settings_dir=wko5_dir if use_wko5 else None,
                                config=config, today=today, source=source)
    return Dataset(wko5_dir, today=today, config=config)
