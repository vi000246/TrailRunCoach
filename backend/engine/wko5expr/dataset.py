"""
Athlete dataset for the WKO5 expression engine, read straight from a WKO5
athlete folder (the `.wko5athlete` index + per-workout `.wko4` files).

Dates are WKO5 day numbers (days since 1901-01-01, fractional part = time of
day), so date arithmetic in expressions (`today-89`, `trunc(date)`) is plain
float math.

Workout metrics come from the athlete index "Entire Workout" range fields
(field map and tss/if rules: docs/wko5-internals/formulas.md).
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.corrections import CorrectionStore
from backend.files.wko5_athlete import AUTO, Athlete, WorkoutEntry, read_athlete, EPOCH
from backend.files.wko4_file import Wko4File, read_wko4

# Entire Workout range field ids (VERIFIED map from WKO5.exe serializer, 2026-09-29)
F_DURATION, F_DISTANCE, F_CLIMBING, F_NP = 4206, 4217, 4223, 4219
F_MOVING = 4213
F_TSSDURATION = 4248          # power tssduration (s)
F_PACE_TSSDURATION = 4249     # rTSS duration (s)
F_NGP = 4230                  # normalized graded pace, min/km
F_HRTSS, F_HRIF = 4235, 4236

# sport group -> prefix of the threshold settings (runftp, bikeftp, ...)
SPORT_SETTING_PREFIX = {"run": "run", "bike": "bike", "road bike": "bike",
                        "swim": "swim", "row": "row", "ski": "ski"}


F_TP_TSS = 4038          # .wko4 info: TSS synced from TrainingPeaks
_CACHE_DIR = Path.home() / ".wko5coach"
_TP_CACHE = _CACHE_DIR / "tp_tss.json"
_MOVING_CACHE = _CACHE_DIR / "moving_hrtss.json"


# ---------------------------------------------------------------------------
# disk caches
#
# Parsing 1000+ .wko4 files takes ~50 s, and building a mean-max curve for each
# is minutes. Both are pure functions of the file's bytes, so results are cached
# on disk keyed by (size, mtime) — plus, where the data can be edited, a
# signature of the approved corrections that apply to it.
# ---------------------------------------------------------------------------

def _cache_read(path: Path) -> dict:
    import json
    try:
        return json.loads(path.read_text("utf-8")).get("files", {})
    except (OSError, ValueError):
        return {}


def _cache_write(path: Path, entries: dict) -> None:
    import json
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"files": entries}), "utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _file_stamp(p: Path) -> list:
    st = p.stat()
    return [st.st_size, int(st.st_mtime)]


FLUSH_EVERY_S = 20.0
_HOLD_LOCK = threading.Lock()


def flush_held(ds) -> bool:
    """True while batched_flush holds this Dataset's series writes and the
    last write is younger than FLUSH_EVERY_S. A pass over every activity
    (estimate(), capacity_samples, ... each end with flush_series) used to
    rewrite the multi-MB series files once per activity, and json.dumps
    holds the GIL — the web server stalled with it."""
    import time
    if not getattr(ds, "_flush_hold", 0):
        return False
    now = time.monotonic()
    if now - getattr(ds, "_flush_last", 0.0) < FLUSH_EVERY_S:
        return True
    ds._flush_last = now                     # this call writes; the next ones wait again
    return False


class batched_flush:
    """`with batched_flush(ds):` — flush_series writes at most every
    FLUSH_EVERY_S inside (a killed process keeps what was written), and once
    at the end."""

    def __init__(self, ds):
        self.ds = ds

    def __enter__(self):
        import time
        with _HOLD_LOCK:
            n = getattr(self.ds, "_flush_hold", 0)
            try:
                if not n:
                    self.ds._flush_last = time.monotonic()
                self.ds._flush_hold = n + 1
            except AttributeError:               # a test double without attributes
                pass
        return self.ds

    def __exit__(self, *exc):
        with _HOLD_LOCK:
            n = getattr(self.ds, "_flush_hold", 0)
            try:
                self.ds._flush_hold = max(0, n - 1)
            except AttributeError:
                return False
            last = self.ds._flush_hold == 0
        f = getattr(self.ds, "flush_series", None)
        if last and f is not None:
            f()
        return False


def _safe(key: str) -> str:
    """Filesystem-safe, collision-free name for a cache keyed by an expression."""
    import hashlib
    slug = "".join(ch if ch.isalnum() else "_" for ch in key)[:40]
    return f"{slug}_{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def _load_tp_tss(athlete_dir: Path) -> dict[str, float]:
    """{relative .wko4 path -> TrainingPeaks TSS}, cached by (size, mtime)."""
    import json
    files = {str(p.relative_to(athlete_dir)).replace("\\", "/"): p
             for p in sorted(athlete_dir.rglob("*.wko4"))}
    stamp = {k: [p.stat().st_size, int(p.stat().st_mtime)] for k, p in files.items()}
    cache = {}
    try:
        cache = json.loads(_TP_CACHE.read_text("utf-8"))
    except (OSError, ValueError):
        pass
    out, entries, dirty = {}, cache.get("files", {}), False
    for key, p in files.items():
        hit = entries.get(key)
        if not hit or hit[:2] != stamp[key]:
            from backend.files.wko5chart_reader import decode, Record
            try:
                info = decode(p.read_bytes()).get(4001)
                v = info.get(F_TP_TSS) if isinstance(info, Record) else None
            except Exception:
                v = None
            hit = stamp[key] + [None if v is None or v == AUTO else v]
            entries[key] = hit
            dirty = True
        if hit[2] is not None:
            out[key] = hit[2]
    if dirty:
        try:
            _TP_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _TP_CACHE.write_text(json.dumps({"files": entries}), "utf-8")
        except OSError:
            pass
    return out


def _load_moving_hrtss(ds) -> dict[str, float]:
    """{relative .wko4 path -> hrTSS charged only while moving}, disk-cached."""
    import json
    from backend.engine.algorithms.wko5_hr import hr_tss
    from backend.engine.algorithms.wko5_time import MOVING_SPEED_KMH

    cache = {}
    try:
        cache = json.loads(_MOVING_CACHE.read_text("utf-8"))
    except (OSError, ValueError):
        pass
    entries = cache.get("files", {})
    out, dirty = {}, False
    for w in ds.workouts:
        p = ds.dir / w.entry.file
        if not p.exists():
            continue
        lthr = ds.sport_setting("thr", w)
        st = p.stat()
        stamp = [st.st_size, int(st.st_mtime), lthr]
        hit = entries.get(w.entry.file)
        if not hit or hit[:3] != stamp:
            f = read_wko4(p)
            t, hr, sp = (f.channels.get(k) for k in ("elapsedtime", "heartrate", "speed"))
            v = None
            if t and hr:
                thr = MOVING_SPEED_KMH.get(w.sport, 0.0)
                mask = ([s is not None and s > thr for s in sp.values] if sp
                        else [True] * len(t.values))
                v = hr_tss(t.values, hr.values, lthr, moving=mask)[0]
            hit = stamp + [v]
            entries[w.entry.file] = hit
            dirty = True
        if hit[3] is not None:
            out[w.entry.file] = hit[3]
    if dirty:
        try:
            _MOVING_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _MOVING_CACHE.write_text(json.dumps({"files": entries}), "utf-8")
        except OSError:
            pass
    return out


def load_wko5_curve_cache(athlete_dir: Path, expr: str) -> dict[str, tuple[list, list]]:
    """WKO5's own per-workout curves from <athlete>/Cache5/*.wko5cache, e.g.
    expr="meanmax(power)". These are bit-exact with our meanmax, so reusing them
    is both faithful and far faster than recomputing 1000+ curves."""
    from backend.files.wko5chart_reader import decode_file, Record
    from backend.files.wko4_file import decode_channel
    out: dict[str, tuple[list, list]] = {}
    cache_dir = athlete_dir / "Cache5"
    if not cache_dir.is_dir():
        return out
    for p in sorted(cache_dir.glob("*.wko5cache")):
        try:
            root = decode_file(p)
        except Exception:
            continue
        top = root.fields[0].value if root.fields else None
        if not isinstance(top, Record) or top.get(461) != expr:
            continue
        entries = top.get(601)
        for e in entries.all(102) if isinstance(entries, Record) else []:
            chans = {}
            ch = e.get(116)
            for c in ch.all(4403) if isinstance(ch, Record) else []:
                body = c.get(102)
                blk = body.get(121) if isinstance(body, Record) else None
                if isinstance(blk, bytes):
                    chans[c.get(101)] = decode_channel(c.get(101), blk).values
            xs, ys = chans.get("x"), chans.get("y")
            if xs and ys:
                out[(e.get(117) or "").split(":", 1)[-1]] = (list(xs), list(ys))
        break
    return out


def date_to_day(d: dt.date | dt.datetime) -> float:
    if isinstance(d, dt.datetime):
        base = (d.date() - EPOCH).days
        return base + (d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6) / 86400
    return float((d - EPOCH).days)


def day_to_date(n: float) -> dt.date:
    return EPOCH + dt.timedelta(days=int(np.floor(n)))


@dataclass
class Workout:
    idx: int
    entry: WorkoutEntry
    day: float                 # WKO5 day number incl. time of day
    sport: str                 # sport group, lower-case: "run", "walk", "strength"...
    sport_type: str            # e.g. "trail running"
    tags: list[str]
    metrics: dict[str, Optional[float]] = field(default_factory=dict)


class Dataset:
    def __init__(self, athlete_dir: str | Path, today: Optional[dt.date] = None,
                 config: Optional[EngineConfig] = None,
                 corrections: Optional["CorrectionStore"] = None,
                 accept_watch_power: Optional[bool] = None,
                 exclude_bad: Optional[bool] = None):
        self.dir = Path(athlete_dir)
        self.config = config or EngineConfig()
        self._init_power_policy(accept_watch_power)
        # Approved data corrections; skipped in parity mode so WKO5 comparisons
        # stay honest.
        self.corrections = None if self.config.parity else (corrections or CorrectionStore())
        self._init_exclusion_policy(exclude_bad)
        # Season plan (events, dated HR tests). Its thresholds replace WKO5's
        # settings outside parity mode.
        from backend.engine.planning import Plan
        self.plan = Plan() if self.config.parity else Plan.load()
        path = next(self.dir.glob("*.wko5athlete"), None)
        if path is None:
            # no WKO5 folder (a COROS / TP-only runner): datasource.current_source
            # sends the charts to the synced FITs; only an explicit source=wko5 gets here
            raise FileNotFoundError(f"no *.wko5athlete in {self.dir}")
        self.athlete: Athlete = read_athlete(path)
        self._tp_tss = _load_tp_tss(self.dir) if self.config.tp_tss else {}
        self._moving_hrtss: dict[str, float] = {}
        self.today = date_to_day(today or dt.date.today())
        self.memo: dict = {}  # evaluator cache: per-workout aggregate results
        self._series: dict[str, dict] = {}        # disk-memoised derived series
        self._series_dirty: set[str] = set()
        self._series_lock = threading.Lock()
        self.workouts: list[Workout] = []
        for i, e in enumerate(self.athlete.workouts):
            if e.start is None:
                continue
            tags_rec = e.record.get(3217) if e.record is not None else None
            tags = [t.lower() for t in tags_rec.all(3218)] if tags_rec is not None else []
            w = Workout(idx=len(self.workouts), entry=e, day=date_to_day(e.start),
                        sport=(e.sport_group or "").lower(),
                        sport_type=(e.sport or "").lower(), tags=tags)
            w.metrics = self._metrics(w)
            self.workouts.append(w)
        # bad activity files leave ds.workouts here, before anything keyed by
        # the workout index (wko4 / power source caches) exists
        self._apply_exclusion_policy()
        self.first_day = int(np.floor(self.workouts[0].day)) if self.workouts else int(self.today)
        self.last_day = int(np.floor(self.workouts[-1].day)) if self.workouts else int(self.today)
        self._apply_power_policy()
        if self.config.moving_hr_tss:
            self._apply_moving_hrtss()
        self._apply_elevation_bonus()

    # ---- power source (backend/engine/power_source.py) ----------------------
    def _init_power_policy(self, accept: Optional[bool]) -> None:
        """Parity mode reads every power like WKO5 does; otherwise watch-
        estimated power feeds no power-based model unless the setting
        power.accept_watch_power (or `accept`) says so."""
        from backend.engine import power_source as PS
        if self.config.parity:
            self.accept_watch_power = True
        else:
            self.accept_watch_power = PS.read_setting(False) if accept is None else bool(accept)
        self._power_blocked: set = set()
        self._power_src: dict = {}

    def _compute_power_source(self, w: Workout) -> str:
        from backend.engine import power_source as PS
        f = self.wko4(w.idx)
        if f is None:
            return PS.NONE
        return PS.classify(f.channels, PS.is_stryd_device(f.device))

    def power_source(self, w: Workout) -> str:
        """stryd / watch / none for one workout (power_source.py). WKO5 .wko4
        files: disk-cached by file stamp ("power_source_v1")."""
        src = self._power_src.get(w.idx)
        if src is None:
            src = self._power_src[w.idx] = self._cached_power_source(w)
        return src

    def _cached_power_source(self, w: Workout) -> str:
        p = self.dir / w.entry.file
        if not p.exists():
            return self._compute_power_source(w)
        path = _CACHE_DIR / "power_source_v1.json"
        store = self.__dict__.setdefault("_ps_store", None)
        if store is None:
            store = self._ps_store = _cache_read(path)
        stamp = _file_stamp(p)
        hit = store.get(w.entry.file)
        if not hit or hit[:2] != stamp:
            hit = stamp + [self._compute_power_source(w)]
            store[w.entry.file] = hit
            self._ps_dirty = True
        return hit[2]

    def _flush_power_sources(self) -> None:
        if getattr(self, "_ps_dirty", False):
            _cache_write(_CACHE_DIR / "power_source_v1.json", self._ps_store)
            self._ps_dirty = False

    def power_ok(self, w: Workout) -> bool:
        """This workout's power may feed the power-based models."""
        from backend.engine import power_source as PS
        if self.accept_watch_power:
            return True
        return PS.usable(self.power_source(w), False)

    def power_label(self, w: Workout) -> Optional[str]:
        from backend.engine import power_source as PS
        return PS.label(self.power_source(w), self.accept_watch_power)

    def _apply_power_policy(self) -> None:
        """No power TSS from watch-estimated power (default): the workouts
        that would score power TSS are classified (only those files are
        read) and the watch ones recomputed (rTSS / hrTSS)."""
        from backend.engine import power_source as PS
        if self.accept_watch_power:
            return
        for w in self.workouts:
            m = w.entry.metrics
            if not (m.get(F_TSSDURATION) and m.get(F_NP) is not None):
                continue
            if self.power_source(w) == PS.WATCH:
                self._power_blocked.add(w.entry.file)
                w.metrics = self._metrics(w)
        self._flush_power_sources()

    # ---- bad activity files (backend/engine/bad_activity.py) ----------------
    def _init_exclusion_policy(self, enabled: Optional[bool]) -> None:
        """A bad file (a run recorded in a car / on a bike, impossible power)
        leaves `self.workouts` — so it reaches no model, chart, PMC or plan
        match — and is listed in `self.excluded` (the activity list, 設定 →
        資料校正). Auto rule on unless activities.exclude_bad is false (or
        `enabled`); the user's keep / exclude override (activity_tags) wins.
        Parity mode excludes nothing (WKO5 reads every file), like the
        corrections."""
        from backend.engine import bad_activity as BA
        self.excluded: list[dict] = []
        self.exclusion_kept: list[dict] = []     # auto-flagged, the user said 這筆是正常的
        if self.config.parity:
            self.exclude_bad = False
            self._exclusion_tags = None
            return
        self.exclude_bad = BA.read_setting(True) if enabled is None else bool(enabled)
        from backend.engine import activity_tags as AT
        self._exclusion_tags = AT.load()

    def _exclusion(self, w: Workout, feats, distance: Optional[float] = None,
                   duration: Optional[float] = None) -> Optional[dict]:
        """Decide one workout (`feats()` = bad_activity.features, called only
        when needed). Records it in excluded / exclusion_kept; returns the
        exclusion (None = the workout stays)."""
        from backend.engine import activity_tags as AT
        from backend.engine import bad_activity as BA
        if self._exclusion_tags is None:                    # parity mode
            return None
        rows = self._exclusion_tags
        ov = AT.user_exclusion(AT.find(rows, w.entry.start, w.entry.file)) if rows else None
        if not self.exclude_bad and ov is None:
            return None
        auto = None
        if w.sport in BA.FOOT_GROUPS:
            auto = BA.judge(feats(), w.sport, self.setting("weight", w.day))
        ex = BA.decide(auto, ov, self.exclude_bad)
        if ex is None and not (auto and ov == BA.KEEP):
            return None
        a = auto or {}
        row = {"key": AT.key_of(w.entry.start), "start": w.entry.start.isoformat(), "file": w.entry.file,
               "sport": w.sport, "sport_type": w.sport_type,
               "distance": distance if distance is not None else a.get("distance_km"),
               "duration": duration, "moving_s": a.get("moving_s"), "avg_kmh": a.get("avg_kmh"),
               "rule": a.get("rule"), "auto": auto is not None, "override": ov,
               "reason": (ex or {}).get("reason") or a.get("reason"),
               "label": (ex or {}).get("label"), "manual": bool((ex or {}).get("manual"))}
        (self.excluded if ex else self.exclusion_kept).append(row)
        return ex

    def _bad_features(self, w: Workout) -> Optional[dict]:
        """bad_activity.features of a WKO5 .wko4 file, disk-cached by file
        stamp + its corrections ("bad_activity_v1"). Reads the file directly
        (not the per-index wko4 cache: the index is not final yet)."""
        from backend.engine import bad_activity as BA
        p = self.dir / w.entry.file
        if not p.exists():
            return None
        store = self.__dict__.get("_ba_store")
        if store is None:
            store = self._ba_store = _cache_read(_CACHE_DIR / "bad_activity_v1.json")
        stamp = _file_stamp(p) + [self._corr_sig(w.entry.file, "power")]
        hit = store.get(w.entry.file)
        if not hit or hit[:3] != stamp:
            f = read_wko4(p)
            ch = f.channels
            t, d, pw = ch.get("elapsedtime"), ch.get("elapseddistance"), ch.get("power")
            pv = pw.values if pw else None
            if pv is not None and self.corrections is not None and t is not None:
                pv = self.corrections.apply(w.entry.file, "power", t.values, pv)
            hit = stamp + [BA.features(t.values if t else None, d.values if d else None, pv)]
            store[w.entry.file] = hit
            self._ba_dirty = True
        return hit[3]

    def _apply_exclusion_policy(self) -> None:
        keep = []
        for w in self.workouts:
            ex = self._exclusion(w, lambda w=w: self._bad_features(w),
                                 distance=w.entry.metrics.get(F_DISTANCE), duration=w.entry.metrics.get(F_DURATION))
            if ex is None:
                w.idx = len(keep)
                keep.append(w)
        self.workouts = keep
        if getattr(self, "_ba_dirty", False):
            _cache_write(_CACHE_DIR / "bad_activity_v1.json", self._ba_store)
            self._ba_dirty = False

    def _is_hr_sourced(self, w: Workout) -> bool:
        m = w.metrics
        if m["tssduration"] and m["np"] is not None and not m.get("power_tss_blocked"):
            return False                                   # power TSS
        if w.sport == "run" and m["ngp"] and m["tss"] is not None and m["tss"] != m["hrtss"]:
            return False                                   # rTSS
        return True

    def _apply_elevation_bonus(self) -> None:
        """Uphill Athlete's vertical bonus — heart rate cannot see the muscular
        cost of climbing, so hrTSS under-counts vertical days."""
        if self.config.parity or not self.config.elevation_tss_per_1000ft:
            return
        for w in self.workouts:
            if not self._is_hr_sourced(w) or w.metrics["tss"] is None:
                continue
            bonus = self.config.elevation_bonus(w.metrics.get("climbing"), w.sport_type)
            if bonus:
                w.metrics["tss"] += bonus
                w.metrics["elevation_tss"] = bonus

    def _apply_moving_hrtss(self) -> None:
        """Replace hrTSS-sourced TSS with a moving-time-only hrTSS."""
        self._moving_hrtss = _load_moving_hrtss(self)
        for w in self.workouts:
            m = w.metrics
            if not self._is_hr_sourced(w):
                continue                                  # power / pace TSS unaffected
            v = self._moving_hrtss.get(w.entry.file)
            if v is not None:
                m["tss"] = v
                m["hrtss_moving"] = v

    # ---- settings ---------------------------------------------------------
    settings_from = "wko5"           # FitFolderDataset: "app" (plan / DB / estimates) unless opted in

    def setting_label(self, name: str, default: str = "WKO5 設定") -> str:
        """Where the athlete's dated setting `name` comes from (UI labels).
        Here: the WKO5 athlete file; FitFolderDataset overrides it."""
        return default

    def setting(self, name: str, day: float) -> Optional[float]:
        name = name.lower()
        if name == "weight":
            v = self.plan.weight_on(day_to_date(day))   # settings page; empty in parity mode
            if v is not None:
                return v
        for suffix, field_ in (("thr", "lthr"), ("mhr", "mhr")):
            if name.endswith(suffix):
                v = self.plan.threshold_on(field_, day_to_date(day))
                if v is not None:
                    return v
        return self.athlete.setting_on(name, day_to_date(day))

    def cp(self, w: Workout) -> Optional[float]:
        """Running critical power for the workout's date: the plan's dated CP
        test, else the FTP WKO5 stored with the workout, else the run setting."""
        v = self.plan.threshold_on("cp", day_to_date(w.day))
        if v is not None:
            return v
        if w.sport == "run" and self.mftp_run is not None:
            return self.mftp_run     # WKO5's modelled FTP (current value only)
        return w.entry.ftp or self.sport_setting("ftp", w)

    @property
    def mftp_run(self) -> Optional[float]:
        from backend.files.wko5_athlete import pd_snapshot
        if not hasattr(self, "_mftp_run"):
            self._mftp_run = pd_snapshot(self.athlete.root).get(("mftp", "Run"))
        return self._mftp_run

    def aethr(self, w: Workout) -> Optional[float]:
        """Top of the low-intensity zone: the tested aerobic threshold when the
        plan has one, else the top of WKO5's Friel HR 'Aerobic' level
        (0.89 × LTHR, docs/wko5-internals/functions.md §4 frielhr)."""
        v = self.plan.threshold_on("aethr", day_to_date(w.day))
        if v is not None:
            return v
        thr = self.sport_setting("thr", w)
        return None if thr is None else 0.89 * thr

    def sport_setting(self, kind: str, w: Workout) -> Optional[float]:
        """Sport-specific threshold (ftp / thr / mhr / tpace) for a workout."""
        prefix = SPORT_SETTING_PREFIX.get(w.sport, "other")
        return self.setting(prefix + kind, w.day)

    # ---- workout metrics --------------------------------------------------
    def _metrics(self, w: Workout) -> dict[str, Optional[float]]:
        """Workout metrics per WKO5 5.0.587 (docs/wko5-internals/formulas.md):
        tss / if are computed on the fly, never stored."""
        m = w.entry.metrics
        ftp = w.entry.ftp or self.sport_setting("ftp", w)   # index 3010 = FTP at workout time
        np_ = m.get(F_NP)
        tssdur = m.get(F_TSSDURATION)
        ngp = m.get(F_NGP)
        tss = iff = None
        # watch-estimated power gives no power TSS unless power.accept_watch_power
        # (backend/engine/power_source.py); the run falls back to rTSS / hrTSS
        blocked = w.entry.file in getattr(self, "_power_blocked", ())
        if tssdur and tssdur > 0 and np_ is not None and ftp and not blocked:
            iff = np_ / ftp
            tss = np_ * np_ * tssdur / (ftp * ftp * 36.0)       # = hours * IF^2 * 100
        elif w.sport == "run" and ngp and m.get(F_PACE_TSSDURATION):
            tpace = self.sport_setting("tpace", w)                # threshold pace, min/km
            if tpace:
                iff = tpace / ngp
                d = m[F_PACE_TSSDURATION]
                tss = (d / 60.0) ** 1.025 * iff * iff / 60.0 * 100.0   # rTSS
        # swim (cubic speed formula) not implemented yet
        if tss is None:
            # WKO5 prefers a TSS synced from TrainingPeaks over its own hrTSS
            # (workout+0x240; kept only when tssSource == 0, which isn't stored
            # on disk — so "use it when present" is the closest file-only rule).
            tp = self._tp_tss.get(w.entry.file)
            tss = tp if tp is not None else m.get(F_HRTSS)
            iff = m.get(F_HRIF) if iff is None else iff
        dur = m.get(F_DURATION)
        return {
            "duration": dur,
            "movingduration": m.get(F_MOVING, dur),
            "tssduration": tssdur,
            "ngp": ngp,
            "vam": m.get(4224),
            "grade": m.get(4226),
            "elevationchange": m.get(4227),
            "pwhr": m.get(4228),
            "pahr": m.get(4229),
            "ef": m.get(4247),
            "vi": m.get(4222),
            "hrtss": m.get(F_HRTSS),
            "hrif": m.get(F_HRIF),
            "distance": m.get(F_DISTANCE),
            "climbing": m.get(F_CLIMBING),
            "descending": m.get(4225),
            "work": (m[4218] / 1000.0) if m.get(4218) is not None else None,  # J -> kJ
            "np": np_,
            "if": iff,
            "tss": tss,
            "plannedtss": None,
            "power_tss_blocked": blocked,
        }

    # ---- disk-cached derived data -----------------------------------------
    def _corr_sig(self, file: str, channel: Optional[str] = None) -> str:
        """Signature of the approved corrections touching one file, so a cached
        result is invalidated when the athlete approves or undoes one. `channel`
        narrows it; None covers every channel (for derived series)."""
        if self.corrections is None:
            return ""
        from backend.engine.activity_key import same_file
        rules = [c for c in self.corrections.items
                 if same_file(c.file, file) and (channel is None or c.channel == channel)]
        return ";".join(sorted(f"{r.channel}:{r.t_start}-{r.t_end}" for r in rules))

    def cached_series(self, key: str, w: Workout, compute):
        """One disk-memoised derived value per workout, computed on demand.

        Unlike `_cached_per_workout` this does not walk every workout, so an
        expensive series (a mean-max curve over a derived channel) only costs
        what the current chart actually asks for. Call `flush_series()` after a
        batch to persist.
        """
        # The API serves several charts at once from one Dataset, so the store
        # and the dirty set are shared between threads. The lock is not held
        # while computing (that can take seconds); two threads may then compute
        # the same value, which is harmless — it's a pure function.
        with self._series_lock:
            store = self._series.get(key)
            if store is None:
                store = self._series[key] = _cache_read(_CACHE_DIR / f"series_{_safe(key)}.json")
        p = self.dir / w.entry.file
        if not p.exists():
            return None
        # Thresholds are inputs too (zones, IF): a changed LTHR / AeT test must
        # not serve values computed with the old one.
        stamp = _file_stamp(p) + [self._corr_sig(w.entry.file), self._settings_sig(w)]
        with self._series_lock:
            hit = store.get(w.entry.file)
        if not hit or hit[:4] != stamp:
            hit = stamp + [compute()]
            with self._series_lock:
                store[w.entry.file] = hit
                self._series_dirty.add(key)
        return hit[4]

    def _settings_sig(self, w: Workout) -> str:
        vals = [self.sport_setting(k, w) for k in ("thr", "mhr", "ftp", "tpace")] + \
            [self.aethr(w), self.cp(w), self.setting("weight", w.day)]
        return ",".join("" if v is None else f"{v:g}" for v in vals)

    def flush_series(self) -> None:
        if flush_held(self):
            return
        with self._series_lock:
            todo = {key: dict(self._series[key]) for key in self._series_dirty}
            self._series_dirty.clear()
        for key, entries in todo.items():      # write outside the lock, from snapshots
            _cache_write(_CACHE_DIR / f"series_{_safe(key)}.json", entries)

    def _cached_per_workout(self, cache_name: str, channel: str, compute):
        """{relative file -> value} for every workout, memoised on disk.

        `compute(workout, Wko4File) -> value | None`; the value must be
        JSON-round-trippable. Only workouts whose file changed (or whose
        corrections changed) are recomputed.
        """
        path = _CACHE_DIR / f"{cache_name}.json"
        entries = _cache_read(path)
        out, dirty = {}, False
        for w in self.workouts:
            p = self.dir / w.entry.file
            if not p.exists():
                continue
            key = f"{w.entry.file}|{channel}"
            stamp = _file_stamp(p) + [self._corr_sig(w.entry.file, channel)]
            hit = entries.get(key)
            if not hit or hit[:3] != stamp:
                f = self.wko4(w.idx)
                hit = stamp + [None if f is None else compute(w, f)]
                entries[key] = hit
                dirty = True
            if hit[3] is not None:
                out[w.entry.file] = hit[3]
        if dirty:
            _cache_write(path, entries)
        return out

    def channel_peaks(self, channel: str = "power") -> dict[str, float]:
        """{relative file -> highest sample of `channel`}, disk-cached."""
        def peak(w, f):
            c = f.channels.get(channel)
            if not c:
                return None
            vals = c.values
            if self.corrections is not None:
                t = f.channels.get("elapsedtime")
                if t is not None:
                    vals = self.corrections.apply(w.entry.file, channel, t.values, vals)
            good = [v for v in vals if v is not None]
            return max(good) if good else None
        return self._cached_per_workout("channel_peaks", channel, peak)

    def workout_curve(self, idx: int, channel: str):
        """Mean-max curve for one workout+channel as (xs, ys), disk-cached."""
        return self._workout_curves(channel).get(self.workouts[idx].entry.file)

    @lru_cache(maxsize=16)
    def _workout_curves(self, channel: str) -> dict[str, tuple[list, list]]:
        from backend.engine.algorithms.wko5_meanmax import meanmax_time

        def build(w, f):
            c, t = f.channels.get(channel), f.channels.get("elapsedtime")
            if not c or not t:
                return None
            vals = c.values
            if self.corrections is not None:
                vals = self.corrections.apply(w.entry.file, channel, t.values, vals)
            xs, ys = meanmax_time(list(t.values), list(vals))
            return [list(xs), list(ys)]
        raw = self._cached_per_workout("workout_curves", channel, build)
        return {k: (v[0], v[1]) for k, v in raw.items()}

    # ---- samples ----------------------------------------------------------
    @lru_cache(maxsize=8)
    def curve_cache(self, expr: str) -> dict[str, tuple[list, list]]:
        return load_wko5_curve_cache(self.dir, expr)

    @lru_cache(maxsize=4096)
    def wko4(self, idx: int) -> Optional[Wko4File]:
        w = self.workouts[idx]
        p = self.dir / w.entry.file
        return read_wko4(p) if p.exists() else None

    def channel(self, idx: int, name: str) -> Optional[np.ndarray]:
        """Samples as float array (NaN = no data); `deltatime` derived.
        Approved data corrections are applied here, never to the source files."""
        f = self.wko4(idx)
        if f is None:
            return None
        t = f.channels.get("elapsedtime")
        if name == "deltatime":
            if t is None:
                return None
            tv = np.array([np.nan if v is None else v for v in t.values], dtype=float)
            return np.diff(tv, prepend=0.0)
        c = f.channels.get(name)
        if c is None:
            return None
        vals = c.values
        if self.corrections is not None and t is not None:
            vals = self.corrections.apply(self.workouts[idx].entry.file, name, t.values, vals)
        return np.array([np.nan if v is None else v for v in vals], dtype=float)
