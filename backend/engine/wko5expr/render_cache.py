"""
Rendered-chart cache for the chart page.

A chart's JSON depends on: the chart definition (after views/wko5_fixes.json —
so editing the fixes or a custom view changes the key by itself), the request
(date range, sports, workout, parity, data source), the data (the WKO5 athlete
file that sync rewrites, the season plan / thresholds, approved corrections,
the engine settings), today's date (charts use `today`) and the code that
computes it (CACHE_VERSION plus the mtimes of the engine, file-reader and
api/wko5views modules, _ENGINE_GLOBS, so a code change never serves stale
numbers).

All of that goes into one sha1 key, so there is nothing to invalidate
explicitly: changed inputs simply miss. Entries live in memory (small LRU) and
on disk under ~/.wko5coach/cache/render/ (survive restarts), capped by size
with least-recently-used eviction.

Two more things keep the page responsive when several charts (or a refresh
while the previous load is still computing) ask at once:
  * identical in-flight requests are coalesced — one computation, shared;
  * at most MAX_CONCURRENT renders run at a time, so the other endpoints
    (overview, settings) still get threadpool time.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

# Bump when the rendered JSON changes shape or meaning without a code mtime change.
CACHE_VERSION = 1
CACHE_DIR = Path.home() / ".wko5coach" / "cache" / "render"
MAX_DISK_BYTES = 300 * 1024 * 1024
MAX_MEMORY_ENTRIES = 400
MAX_CONCURRENT = 2

# Everything that shapes a chart's JSON: wko5expr/, algorithms/, engine/*.py
# (zones, thresholds, planning, workout_review feed the zones / targets / review
# panels and the plan-driven series), panels/ (workout_review uses them), files/
# (the WKO4 / WKO5 / FIT readers that turn files into channels) and
# api/wko5views.py (the endpoint that assembles the dataset and the chart
# JSON around render_chart).
_ENGINE = Path(__file__).resolve().parents[1]
_BACKEND = _ENGINE.parent
_ENGINE_GLOBS = [(_ENGINE / "wko5expr", "*.py"), (_ENGINE / "algorithms", "*.py"), (_ENGINE, "*.py"),
                 (_ENGINE / "panels", "*.py"), (_BACKEND / "files", "*.py"),
                 (_BACKEND / "api", "wko5views.py")]


def _stamp(p: Path) -> list:
    try:
        st = p.stat()
        return [p.name, st.st_size, st.st_mtime_ns]
    except OSError:
        return [p.name, None, None]


def _compute_code_signature() -> str:
    parts = [CACHE_VERSION]
    for d, pat in _ENGINE_GLOBS:
        parts += [[d.name] + _stamp(p) for p in sorted(d.glob(pat))]
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()


# Taken ONCE, when this process imports the code: it must describe the code
# that is actually running. Re-reading mtimes per request would let a process
# that hasn't reloaded yet (or a second server sharing the disk cache) store
# old-code results under the new code's signature.
_CODE_SIGNATURE = _compute_code_signature()


def code_signature() -> str:
    """CACHE_VERSION + mtimes of the engine modules, as loaded by this process."""
    return _CODE_SIGNATURE


def data_fingerprint(ds) -> str:
    """What the data looks like: athlete file (sync rewrites it), plan /
    thresholds, corrections, engine config, workout list, today."""
    from backend.engine.planning import PLAN_PATH
    from backend.engine.wko5expr.corrections import CORRECTIONS_PATH
    athlete = [_stamp(p) for p in sorted(Path(ds.dir).glob("*.wko5athlete"))]
    cfg = ds.config.to_dict() if hasattr(ds.config, "to_dict") else repr(ds.config)
    wl = ds.memo.get(("render_cache", "workouts"))
    if wl is None:
        h = hashlib.sha1()
        for w in ds.workouts:
            h.update(f"{w.entry.file}|{w.day}|{w.sport}\n".encode())
        wl = ds.memo[("render_cache", "workouts")] = f"{len(ds.workouts)}:{h.hexdigest()}"
    # chart data source (wko5 | coros | tp, datasource.py): a source-specific
    # Dataset may carry its name / file stamp; the workout list covers the rest
    src = [getattr(ds, "source", None), getattr(ds, "source_stamp", None)]
    if src[0] in ("coros", "tp") and src[1] is None:
        # FIT-folder dataset: its files' stamp, taken once per Dataset (a sync
        # that rewrites a FIT under the same name builds a new Dataset)
        st = ds.memo.get(("render_cache", "source_stamp"))
        if st is None:
            try:
                from backend.engine.wko5expr.datasource import source_stamp
                st = source_stamp(src[0], Path(ds.dir))
            except Exception:
                st = ""
            ds.memo[("render_cache", "source_stamp")] = st
        src[1] = st
    # the stored CP-test sessions: workout_review.classify recognises a test
    # from the plan (done_by) first
    from backend.engine.plan_store import test_sessions
    tests = sorted((s["uid"], s["state"], s.get("day") or "", (s.get("done_by") or {}).get("index") or -1,
                    s.get("protocol") or "") for s in test_sessions())
    # route_weather's per-activity air temperature: drift() / the review card's
    # heat rule read it (workout_review.activity_temp), and a routes build fills it
    try:
        from backend.engine import route_weather as RW
        from backend.engine.routes import HOME
        wx = _stamp(HOME / RW.ACTIVITY_WX_FILE)
    except Exception:                          # noqa: BLE001 — no routes module: no archive
        wx = None
    parts = [athlete, _stamp(PLAN_PATH), _stamp(CORRECTIONS_PATH), cfg, wl, ds.today, src, tests, wx]
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def chart_key(chart: dict, request: dict, fingerprint: str) -> str:
    body = {"chart": chart, "request": request, "data": fingerprint, "code": code_signature()}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class RenderCache:
    def __init__(self, root: Path = CACHE_DIR, max_bytes: int = MAX_DISK_BYTES,
                 max_memory: int = MAX_MEMORY_ENTRIES, max_concurrent: int = MAX_CONCURRENT):
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.max_memory = max_memory
        self._mem: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()
        self._inflight: dict[str, dict] = {}
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._since_prune = 0
        self.stats = {"hit_mem": 0, "hit_disk": 0, "miss": 0, "coalesced": 0}

    # ---- storage ----------------------------------------------------------
    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def _remember(self, key: str, value: Any) -> None:
        self._mem[key] = value
        self._mem.move_to_end(key)
        while len(self._mem) > self.max_memory:
            self._mem.popitem(last=False)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
                self.stats["hit_mem"] += 1
                return self._mem[key]
        p = self._path(key)
        try:
            value = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        try:
            os.utime(p)                      # recently used -> evicted last
        except OSError:
            pass
        with self._lock:
            self._remember(key, value)
            self.stats["hit_disk"] += 1
        return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._remember(key, value)
        p = self._path(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), "utf-8")
            os.replace(tmp, p)
        except OSError:
            return
        self._since_prune += 1
        if self._since_prune >= 50:
            self._since_prune = 0
            self.prune()

    def prune(self) -> None:
        """Drop least-recently-used files until the cache fits max_bytes."""
        try:
            files = [(p.stat().st_mtime, p.stat().st_size, p) for p in self.root.glob("*/*.json")]
        except OSError:
            return
        total = sum(s for _, s, _ in files)
        for _, size, p in sorted(files):
            if total <= self.max_bytes:
                break
            try:
                p.unlink()
                total -= size
            except OSError:
                pass

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
        for p in self.root.glob("*/*.json"):
            try:
                p.unlink()
            except OSError:
                pass

    # ---- compute ----------------------------------------------------------
    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        """Cached value, or compute it once — concurrent callers with the same
        key wait for that one computation instead of starting their own."""
        hit = self.get(key)
        if hit is not None:
            return hit
        with self._lock:
            job = self._inflight.get(key)
            owner = job is None
            if owner:
                job = self._inflight[key] = {"done": threading.Event(), "value": None, "error": None}
            else:
                self.stats["coalesced"] += 1
        if not owner:
            job["done"].wait()
            if job["error"] is not None:
                raise job["error"]
            return job["value"]
        try:
            with self._slots:                # cap concurrent renders
                hit = self.get(key)          # finished while we queued?
                if hit is None:
                    with self._lock:
                        self.stats["miss"] += 1
                    hit = compute()
                    self.put(key, hit)
            job["value"] = hit
            return hit
        except BaseException as e:
            job["error"] = e
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            job["done"].set()


CACHE = RenderCache()
