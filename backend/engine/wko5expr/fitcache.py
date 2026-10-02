"""
Persistent per-file cache for FIT folder datasets (fitdataset.FitFolderDataset).

Parsing a FIT file (fitdecode, pure Python) is the slow part of a COROS / TP
Dataset build: ~808 files took minutes. Everything the build derives from one
file is a pure function of that file's bytes (plus a small, named input), so
it is stored here and reused until the file or the code behind that one
field changes.

Layout (one folder per FIT folder, keyed by the folder's absolute path):

    <root>/<sha1(dir)[:12]>/index.json        per-file entries (below)
    <root>/<sha1(dir)[:12]>/ch/<sha1(rel)>.npz the parsed channels (float64, NaN = no data)

    root = $WKO5COACH_FIT_CACHE, else ~/.wko5coach/cache/fit

An entry is valid only for the file's (size, mtime_ns). Each derived field
carries its own version, so a code change re-derives only that field:

    field    derived by                                    extra key          version from
    parse    fit_to_channels (channels, start, sport,      -                  PARSE_V + source of
             sub_sport, stryd device, duration)                               fit_to_channels.py,
                                                                              power_source.fit_stryd_device,
                                                                              fitdecode version
    power    power_source.classify                          -                  POWER_V + classify source
    bad      bad_activity.features                          power-corrections  BAD_V + features source
                                                            signature          + its constants
    fields   fitdataset.workout_fields(lthr=None)          sport group        FIELDS_V + workout_fields /
             (duration, moving, distance, climbing, NP,                       minetti source + moving
             work, NGP)                                                       speed table
    hr       wko5_hr.hr_tss (hrTSS / hrIF)                  LTHR               HR_V + wko5_hr source
    mhr      moving-time-only hrTSS                         LTHR + moving      HR_V + wko5_hr source
                                                            speed

A version string = the constant here + a hash of the code that computes the
field (inspect.getsource), so editing that code invalidates exactly that
field; bump the constant for a change the hash cannot see (a dependency).

The parse step runs in a process pool when many files are new (a cold cache:
first run, a parser change), so the web server's event loop keeps the GIL;
a handful of new files (a sync) are parsed inline. WKO5COACH_FIT_WORKERS=0
turns the pool off, =N sets its size.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

ENV_ROOT = "WKO5COACH_FIT_CACHE"
ENV_WORKERS = "WKO5COACH_FIT_WORKERS"
POOL_MIN_FILES = 12           # fewer stale files than this: parse inline (no pool start-up)

PARSE_V, POWER_V, BAD_V, FIELDS_V, HR_V, AVG_V = 1, 1, 1, 1, 1, 1


def root() -> Path:
    v = os.getenv(ENV_ROOT)
    return Path(v) if v else Path.home() / ".wko5coach" / "cache" / "fit"


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

def _h(*parts) -> str:
    m = hashlib.sha1()
    for p in parts:
        if callable(p) or inspect.ismodule(p):
            try:
                p = inspect.getsource(p)
            except (OSError, TypeError):
                p = repr(p)
        m.update(str(p).encode("utf-8"))
    return m.hexdigest()[:10]


_VERSIONS: dict[str, str] = {}


def versions() -> dict[str, str]:
    if not _VERSIONS:
        import fitdecode
        from backend.engine import bad_activity as BA
        from backend.engine import power_source as PS
        from backend.engine.algorithms import minetti, wko5_hr
        from backend.engine.algorithms.wko5_time import MOVING_SPEED_KMH
        from backend.engine.wko5expr import fitdataset as FD
        from backend.files import fit_to_channels as FTC
        _VERSIONS.update({
            "parse": f"{PARSE_V}:{_h(FTC, PS.fit_stryd_device, PS.is_stryd_device, getattr(fitdecode, '__version__', ''))}",
            "power": f"{POWER_V}:{_h(PS.classify, PS._any_positive, PS.STRYD_CHANNELS)}",
            "bad": f"{BAD_V}:{_h(BA.features, BA._arr, (BA.SPIKE_KMH, BA.MOVING_KMH, BA.WINDOWS_S))}",
            "fields": f"{FIELDS_V}:{_h(FD.workout_fields, FD._rolling, FD._smooth, FD._arr, minetti, MOVING_SPEED_KMH)}",
            "hr": f"{HR_V}:{_h(wko5_hr)}",
            # the 活動列表 avg HR / avg power columns (fitdataset.averages_of)
            "avg": f"{AVG_V}:{_h(FD.averages_of)}",
        })
    return _VERSIONS


# ---------------------------------------------------------------------------
# pure per-file work (runs in pool workers too)
# ---------------------------------------------------------------------------

def _np(vals) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in vals], dtype=float)


def to_list(a: np.ndarray) -> list:
    """float64 array -> list of float with None for NaN (Channel.values)."""
    nan = np.isnan(a)
    if not nan.any():
        return a.tolist()
    o = a.astype(object)
    o[nan] = None
    return o.tolist()


def group_of(sport, sub_sport) -> str:
    from backend.engine.wko5expr.fitdataset import sport_of
    sport_raw, sub = (sport or "", sub_sport)
    if isinstance(sport_raw, str) and "/" in sport_raw:
        sport_raw, sub = sport_raw.split("/", 1)
    return sport_of(sport_raw, sub)[0]


def parse_file(path: str, npz_path: str) -> dict:
    """Parse one FIT, write its channels to `npz_path`, return the entry's
    derived fields (meta, power, bad, fields). Never raises: an unreadable file
    gives {"meta": {"error": ...}}."""
    from backend.engine import bad_activity as BA
    from backend.engine.wko5expr.fitdataset import workout_fields
    from backend.files.fit_to_channels import fit_to_channels
    v = versions()
    try:
        fc = fit_to_channels(Path(path).read_bytes())
    except Exception as e:                       # noqa: BLE001 — recorded, the build skips it
        return {"meta": {"error": type(e).__name__}, "parse": v["parse"]}
    start = fc.start_time
    meta = {"start": start.isoformat() if start is not None else None, "sport": fc.sport,
            "sub_sport": fc.sub_sport, "stryd_device": bool(fc.stryd_device), "one_second": bool(fc.one_second),
            "n": len(fc.elapsedtime), "duration": float(fc.elapsedtime[-1]) if fc.elapsedtime else None,
            "channels": sorted(fc.channels)}
    out = {"meta": meta, "parse": v["parse"]}
    if start is None or not fc.elapsedtime:
        return out                                # the build skips it (no start / no samples)
    t = np.asarray(fc.elapsedtime, dtype=float)
    ch = {k: _np(vals) for k, vals in fc.channels.items()}
    tmp = Path(npz_path).with_name(Path(npz_path).stem + f".{os.getpid()}.{threading.get_ident()}.tmp.npz")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(tmp, t=t, **{f"c_{k}": a for k, a in ch.items()})
    os.replace(tmp, npz_path)
    out["power"] = [v["power"], fc.power_source]
    out["bad"] = [v["bad"], {"": BA.features(fc.elapsedtime, fc.channels.get("elapseddistance"),
                                             fc.channels.get("power"))}]
    grp = group_of(fc.sport, fc.sub_sport)
    out["fields"] = [v["fields"], grp, {str(k): x for k, x in workout_fields(t, ch, grp, None).items()}]
    return out


def _worker_init() -> None:
    import logging as _l
    _l.disable(_l.WARNING)


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_LOCK = threading.Lock()


def _lock_for(key: str) -> threading.RLock:
    with _STORE_LOCKS_LOCK:
        lk = _STORE_LOCKS.get(key)
        if lk is None:
            lk = _STORE_LOCKS[key] = threading.RLock()
        return lk


def stamp_of(p: Path) -> list:
    st = p.stat()
    return [st.st_size, st.st_mtime_ns]


def home_of(fit_dir: Path, base: Optional[Path] = None) -> Path:
    """The cache folder of one FIT folder."""
    key = hashlib.sha1(os.path.normcase(os.path.abspath(str(Path(fit_dir)))).encode()).hexdigest()[:12]
    return (base or root()) / key


def index_path_of(fit_dir: Path, base: Optional[Path] = None) -> Path:
    return home_of(fit_dir, base) / "index.json"


class FitStore:
    """The cache of one FIT folder."""

    def __init__(self, fit_dir: Path, base: Optional[Path] = None):
        self.dir = Path(fit_dir)
        self.home = home_of(self.dir, base)
        self.index_path = self.home / "index.json"
        self.lock = _lock_for(str(self.home))
        self.v = versions()
        self.files: dict[str, dict] = {}
        self.dirty = False
        try:
            raw = json.loads(self.index_path.read_text("utf-8"))
            if raw.get("dir") == str(self.dir):
                self.files = raw.get("files", {})
        except (OSError, ValueError):
            pass

    # ---- entries ------------------------------------------------------------
    def rel(self, p: Path) -> str:
        return p.relative_to(self.dir).as_posix()

    def npz(self, rel: str) -> Path:
        return self.home / "ch" / (hashlib.sha1(rel.encode()).hexdigest()[:20] + ".npz")

    def _valid(self, rel: str, stamp: list) -> Optional[dict]:
        e = self.files.get(rel)
        if not e or e.get("stamp") != stamp or e.get("parse") != self.v["parse"]:
            return None
        m = e["meta"]
        if not m.get("error") and m.get("start") and m.get("n") and not self.npz(rel).exists():
            return None
        return e

    def stale(self, paths: list[Path]) -> list[Path]:
        with self.lock:
            return [p for p in paths if self._valid(self.rel(p), stamp_of(p)) is None]

    def ensure(self, paths: list[Path], progress=None, workers: Optional[int] = None) -> int:
        """Parse every file whose entry is missing / stale; returns how many."""
        self._prune(paths)
        todo = self.stale(paths)
        if progress is not None:
            progress.phase("parse", total=len(todo))
        if not todo:
            self.save()
            return 0
        n = _pool_size(len(todo)) if workers is None else workers
        done = 0
        if n > 1:
            try:
                done = self._parse_pool(todo, n, progress)
            except Exception as e:               # noqa: BLE001 — a pool failure falls back to inline
                log.warning("FIT cache: process pool failed (%s); parsing inline", type(e).__name__)
        rest = self.stale(todo) if done else todo
        for p in rest:
            self._store(p, parse_file(str(p), str(self.npz(self.rel(p)))))
            if progress is not None:
                progress.tick()
        self.save()
        return len(todo)

    def _prune(self, paths: list[Path]) -> None:
        """Forget files that left the folder (deleted, renamed) and their channels."""
        keep = {self.rel(p) for p in paths}
        with self.lock:
            gone = [r for r in self.files if r not in keep]
            for r in gone:
                del self.files[r]
            if gone:
                self.dirty = True
        for r in gone:
            try:
                self.npz(r).unlink()
            except OSError:
                pass

    def _parse_pool(self, todo: list[Path], n: int, progress) -> int:
        import multiprocessing as mp
        done = 0
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=n, mp_context=ctx, initializer=_worker_init) as ex:
            futs = {ex.submit(parse_file, str(p), str(self.npz(self.rel(p)))): p for p in todo}
            for i, f in enumerate(as_completed(futs)):
                self._store(futs[f], f.result())
                done += 1
                if progress is not None:
                    progress.tick()
                if i % 100 == 99:
                    self.save()                  # a killed build keeps what it parsed
        return done

    def _store(self, p: Path, res: dict) -> None:
        rel = self.rel(p)
        with self.lock:
            self.files[rel] = {"stamp": stamp_of(p), **res}
            self.dirty = True

    def entry(self, p: Path) -> Optional[dict]:
        with self.lock:
            return self._valid(self.rel(p), stamp_of(p))

    def save(self) -> None:
        with self.lock:
            if not self.dirty:
                return
            data = json.dumps({"dir": str(self.dir), "files": self.files})
            self.dirty = False
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_name(f"index.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(data, "utf-8")
            os.replace(tmp, self.index_path)
        except OSError as e:
            log.warning("FIT cache: could not write the index (%s)", type(e).__name__)

    # ---- channels -----------------------------------------------------------
    def arrays(self, rel: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        with np.load(self.npz(rel)) as z:
            t = z["t"]
            ch = {k[2:]: z[k] for k in z.files if k.startswith("c_")}
        return t, ch

    # ---- derived fields -----------------------------------------------------
    def derived(self, rel: str, field: str, key: str, compute: Callable):
        """The cached value of `field` (under `key`) for one file, computed
        and stored on a miss. `field` versions: versions()[field]."""
        ver = self.v["hr" if field == "mhr" else field]
        with self.lock:
            e = self.files.get(rel)
            slot = e.get(field) if e else None
            if slot and slot[0] == ver and key in slot[1]:
                return slot[1][key]
        val = compute()
        with self.lock:
            e = self.files.get(rel)
            if e is not None:
                slot = e.get(field)
                if not slot or slot[0] != ver:
                    slot = e[field] = [ver, {}]
                slot[1][key] = val
                self.dirty = True
        return val

    def fields(self, rel: str, group: str, compute: Callable) -> dict:
        with self.lock:
            e = self.files.get(rel)
            slot = e.get("fields") if e else None
            if slot and slot[0] == self.v["fields"] and slot[1] == group:
                return {int(k): x for k, x in slot[2].items()}
        val = compute()
        with self.lock:
            e = self.files.get(rel)
            if e is not None:
                e["fields"] = [self.v["fields"], group, {str(k): x for k, x in val.items()}]
                self.dirty = True
        return dict(val)

    def power(self, rel: str, compute: Callable) -> str:
        with self.lock:
            e = self.files.get(rel)
            slot = e.get("power") if e else None
            if slot and slot[0] == self.v["power"]:
                return slot[1]
        val = compute()
        with self.lock:
            e = self.files.get(rel)
            if e is not None:
                e["power"] = [self.v["power"], val]
                self.dirty = True
        return val


class _MultiFiles:
    """`store.files.get(rel)` over a MultiFitStore."""

    def __init__(self, ms: "MultiFitStore"):
        self.ms = ms

    def get(self, rel: str, default=None):
        s, r = self.ms.split(rel)
        return s.files.get(r, default) if s is not None else default


class MultiFitStore:
    """The caches of several FIT folders under one root (the merged
    "synced" Dataset: <root>/coros, <root>/tp). A file's rel path is
    "<folder>/<rel in that folder>"; every per-file call goes to that
    folder's own FitStore, so the merged Dataset reuses the caches the
    single-source Datasets already built (no re-parse). `home` (the merged
    Dataset's own memos: estimates, PD fits, series) is separate."""

    def __init__(self, root_dir: Path, folders, base: Optional[Path] = None):
        self.dir = Path(root_dir)
        self.stores = {f: FitStore(self.dir / f, base) for f in folders}
        key = hashlib.sha1(("merged:" + os.path.normcase(os.path.abspath(str(self.dir))) + ":"
                            + ",".join(folders)).encode()).hexdigest()[:12]
        self.home = (base or root()) / f"m{key}"
        self.files = _MultiFiles(self)

    def split(self, rel: str):
        head, _, tail = rel.partition("/")
        return self.stores.get(head), tail

    def _store_of(self, p: Path) -> tuple[Optional[FitStore], str]:
        rel = Path(p).relative_to(self.dir).as_posix()
        return self.split(rel)

    def ensure(self, paths: list[Path], progress=None, workers: Optional[int] = None) -> int:
        by: dict[str, list[Path]] = {f: [] for f in self.stores}
        for p in paths:
            head = Path(p).relative_to(self.dir).parts[0]
            if head in by:
                by[head].append(p)
        return sum(self.stores[f].ensure(ps, progress=progress, workers=workers) for f, ps in by.items())

    def entry(self, p: Path) -> Optional[dict]:
        s, _ = self._store_of(p)
        return s.entry(p) if s is not None else None

    def save(self) -> None:
        for s in self.stores.values():
            s.save()

    def arrays(self, rel: str):
        s, r = self.split(rel)
        return s.arrays(r)

    def derived(self, rel: str, field: str, key: str, compute: Callable):
        s, r = self.split(rel)
        return s.derived(r, field, key, compute)

    def fields(self, rel: str, group: str, compute: Callable) -> dict:
        s, r = self.split(rel)
        return s.fields(r, group, compute)

    def power(self, rel: str, compute: Callable) -> str:
        s, r = self.split(rel)
        return s.power(r, compute)


def _pool_size(n_files: int) -> int:
    v = os.getenv(ENV_WORKERS)
    if v is not None:
        try:
            return max(0, int(v))
        except ValueError:
            pass
    if n_files < POOL_MIN_FILES:
        return 0
    cpu = os.cpu_count() or 2
    return max(0, min(8, cpu - 2, n_files // 4))


# ---------------------------------------------------------------------------
# lazily loaded channels
# ---------------------------------------------------------------------------

class LazyFiles:
    """{workout idx -> Wko4File} whose channels are read from the cache on
    first use, keeping at most `max_open` files in memory (LRU). A warm
    build reads no channels at all: every per-file number it needs is in the
    index, so a dataset over 800 files starts in seconds and holds only what
    the charts look at."""

    def __init__(self, store: FitStore, max_open: Optional[int] = None):
        self.store = store
        self.meta: dict[int, tuple] = {}
        self.max_open = max_open or int(os.getenv("WKO5COACH_FIT_OPEN", "256"))
        self._open: "OrderedDict[int, object]" = OrderedDict()
        self._lock = threading.Lock()

    def add(self, idx: int, path: Path, rel: str, sport_type: str, start_iso: str) -> None:
        self.meta[idx] = (path, rel, sport_type, start_iso)

    def __contains__(self, idx) -> bool:
        return idx in self.meta

    def __len__(self) -> int:
        return len(self.meta)

    def __getitem__(self, idx):
        f = self.get(idx)
        if f is None:
            raise KeyError(idx)
        return f

    def get(self, idx, default=None):
        m = self.meta.get(idx)
        if m is None:
            return default
        with self._lock:
            f = self._open.get(idx)
            if f is not None:
                self._open.move_to_end(idx)
                return f
        f = self._load(*m)
        with self._lock:
            self._open[idx] = f
            self._open.move_to_end(idx)
            while len(self._open) > self.max_open:
                self._open.popitem(last=False)
        return f

    def _load(self, path: Path, rel: str, sport_type: str, start_iso: str):
        from backend.files.wko4_file import Channel, Wko4File
        t, ch = self.store.arrays(rel)
        chans = {"elapsedtime": Channel("elapsedtime", t.tolist(), 1.0, base=0.0)}
        for name, a in ch.items():
            chans[name] = Channel(name, to_list(a), 1.0)
        return Wko4File(path=str(path), sport=sport_type, start_time=start_iso, device=None, weight_kg=None,
                        original_type="fit", original_bytes=None, channels=chans, ranges=[], info=None)
