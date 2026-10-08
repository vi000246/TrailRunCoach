"""
Rendered-chart cache for the chart page.

A chart's JSON depends on: the chart definition (after views/wko5_fixes.json —
so editing the fixes or a custom view changes the key by itself), the request
(date range, sports, workout, parity, data source), the data it reads and the
code that computes it. All of that goes into one sha1 key (chart_key), so
there is nothing to invalidate explicitly: changed inputs simply miss.

Since SP-336 (SP-320 ⑤) the data and code parts are per chart (chartscope.py):
the activities of the chart's range plus its warm-up (6 × 42 days for the
PMC loads), today only for a chart that reads it or ends today, and only the
code its chart type reaches (engine/codehash.py) plus CACHE_VERSION. One new
activity, a new day or a deploy drops only the charts they touch.
data_fingerprint(ds) / code_signature() remain the whole-dataset / whole-engine
versions (a chart kind chartscope does not know falls back to them).

Entries live in memory (small LRU) and on disk under <tenant>/cache/render/
(survive restarts), capped by size with least-recently-used eviction.

Two more things keep the page responsive when several charts (or a refresh
while the previous load is still computing) ask at once:
  * identical in-flight requests are coalesced — one computation, shared;
  * at most MAX_CONCURRENT renders run at a time, so the other endpoints
    (overview, settings) still get threadpool time.

Stale-while-revalidate (SP-336, the pattern of api/plan_sessions.py, SP-362):
`serve(key, compute, slot, stale_ok)`. A *slot* names a chart request apart
from its data and code (the endpoint builds it: view, chart, parameters, the
range — "the 90 days to today" — not its dates); `cache/render/slots/<slot>.json`
points at the key last drawn for it. On a miss with stale_ok and an older
drawing of the slot still on disk, that drawing is answered at once with
`stale` = {key, at} and the new key is computed in the background
(REFRESH_POOL, 2 threads, once per key, at most REFRESH_MAX keys waiting);
`ready(keys)` says which are there (GET /render/ready, the viewer swaps the
card). A background failure is remembered: that key is then computed in the
request. Display only — the endpoint passes stale_ok only for the viewer's
?stale=1 and never for a demo tenant.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

# Bump when the rendered JSON changes shape or meaning in a way engine/codehash.py cannot see
# (a registry, a getattr). 2: SP-336 per-chart keys.
CACHE_VERSION = 2
CACHE_DIR = None      # fixed folder (tests); None = <tenant shared>/cache/render


def cache_dir() -> Path:
    if CACHE_DIR is not None:
        return Path(CACHE_DIR)
    from backend import tenancy
    return tenancy.shared_path('cache', 'render')

MAX_DISK_BYTES = 300 * 1024 * 1024
MAX_MEMORY_ENTRIES = 400
# JSON bytes kept in memory (the Python objects take a few times that): a
# count alone let per-second workout charts add up to GB on the NAS
MAX_MEMORY_BYTES = 32 * 1024 * 1024
MAX_CONCURRENT = 2
REFRESH_MAX = 64          # keys waiting for a background render; more = computed in the request
FAILED_MAX = 256
SLOTS_DIR = "slots"
# the background half of a stale answer: shared by every tenant (each job carries its own context)
REFRESH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chart-refresh")
log = logging.getLogger(__name__)

# Everything that shapes a chart's JSON (the whole-engine signature, code_signature()): wko5expr/, algorithms/, engine/*.py
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


def _content(p: Path) -> list:
    """[name, sha1 of the bytes] — SP-320 ①: a deploy rewrites every file's mtime;
    only a changed file's contents should drop the charts."""
    try:
        return [p.name, hashlib.sha1(p.read_bytes()).hexdigest()]
    except OSError:
        return [p.name, None]


def _compute_code_signature() -> str:
    parts = [CACHE_VERSION]
    for d, pat in _ENGINE_GLOBS:
        parts += [[d.name] + _content(p) for p in sorted(d.glob(pat))]
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()


# Taken ONCE, when this process imports the code: it must describe the code
# that is actually running. Re-reading mtimes per request would let a process
# that hasn't reloaded yet (or a second server sharing the disk cache) store
# old-code results under the new code's signature.
_CODE_SIGNATURE = _compute_code_signature()


def code_signature() -> str:
    """CACHE_VERSION + contents of the engine modules, as loaded by this process."""
    return _CODE_SIGNATURE


def data_fingerprint(ds) -> str:
    """The whole dataset's fingerprint: every activity, today, the weather — chartscope's
    fingerprint over the whole history (a chart of unknown scope)."""
    from backend.engine.wko5expr import chartscope as CS
    return CS.fingerprint(ds, CS.Scope.whole())


def chart_key(chart: dict, request: dict, fingerprint: str, code: Optional[str] = None) -> str:
    """`fingerprint`: the data part (chartscope.fingerprint / data_fingerprint); `code`: the
    chart's code signature (chartscope.code_signature), None = the whole engine's."""
    body = {"chart": chart, "request": request, "data": fingerprint,
            "code": code if code is not None else code_signature()}
    from backend.i18n import DEFAULT_LOCALE, current_locale
    if current_locale() != DEFAULT_LOCALE:      # the engine's text follows the request language (zh-TW keys unchanged)
        body["locale"] = current_locale()
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class RenderCache:
    @property
    def root(self) -> Path:
        """A fixed folder, else the tenant's shared cache/render (backend/tenancy.py)."""
        return self._root if self._root is not None else cache_dir()

    def __init__(self, root: Optional[Path] = None, max_bytes: int = MAX_DISK_BYTES,
                 max_memory: int = MAX_MEMORY_ENTRIES, max_concurrent: int = MAX_CONCURRENT,
                 max_memory_bytes: int = MAX_MEMORY_BYTES):
        self._root = Path(root) if root is not None else None
        self.max_bytes = max_bytes
        self.max_memory = max_memory
        self.max_memory_bytes = max_memory_bytes
        self._mem: "OrderedDict[str, tuple[Any, int]]" = OrderedDict()   # key -> (value, JSON bytes)
        self._mem_bytes = 0
        self._lock = threading.Lock()
        self._inflight: dict[str, dict] = {}
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._since_prune = 0
        self.stats = {"hit_mem": 0, "hit_disk": 0, "miss": 0, "coalesced": 0, "stale": 0}
        self._slot_keys: dict[tuple, str] = {}           # (root, slot) -> key last pointed at (spares the file)
        self._refreshing: dict[str, float] = {}      # key -> time the background render was queued
        self._failed: "OrderedDict[str, float]" = OrderedDict()   # keys whose background render failed

    # ---- storage ----------------------------------------------------------
    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def _remember(self, key: str, value: Any, size: int) -> None:
        """Keep `value` in memory; `size` = its JSON length. Capped by count
        and by bytes: a workout chart is per-second data (MB as Python
        objects), so 400 of them could hold GB; one larger than a quarter of
        the budget stays on disk only."""
        old = self._mem.pop(key, None)
        if old is not None:
            self._mem_bytes -= old[1]
        if size > self.max_memory_bytes // 4:
            return
        self._mem[key] = (value, size)
        self._mem_bytes += size
        while self._mem and (len(self._mem) > self.max_memory or self._mem_bytes > self.max_memory_bytes):
            _k, (_v, s) = self._mem.popitem(last=False)
            self._mem_bytes -= s

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
                self.stats["hit_mem"] += 1
                return self._mem[key][0]
        p = self._path(key)
        try:
            text = p.read_text("utf-8")
            value = json.loads(text)
        except (OSError, ValueError):
            return None
        try:
            os.utime(p)                      # recently used -> evicted last
        except OSError:
            pass
        with self._lock:
            self._remember(key, value, len(text))
            self.stats["hit_disk"] += 1
        return value

    def put(self, key: str, value: Any) -> None:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._remember(key, value, len(text))
        p = self._path(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(text, "utf-8")
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
            self._slot_keys.clear()
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

    # ---- stale-while-revalidate (SP-336, module doc) -------------------------
    def _slot_path(self, slot: str) -> Path:
        return self.root / SLOTS_DIR / f"{slot}.json"

    def slot_get(self, slot: str) -> Optional[dict]:
        """{key, at} last drawn for this chart slot, or None."""
        try:
            d = json.loads(self._slot_path(slot).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) and d.get("key") else None

    def slot_put(self, slot: str, key: str) -> None:
        mk = (str(self.root), slot)
        with self._lock:
            if self._slot_keys.get(mk) == key:
                return
            self._slot_keys[mk] = key
            if len(self._slot_keys) > 4 * self.max_memory:
                self._slot_keys.clear()
        p = self._slot_path(slot)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"key": key, "at": dt.datetime.now().isoformat(timespec="seconds")}), "utf-8")
            os.replace(tmp, p)
        except OSError:
            pass

    def serve(self, key: str, compute: Callable[[], Any], slot: Optional[str] = None,
              stale_ok: bool = False) -> tuple[Any, Optional[dict]]:
        """(value, stale): the cached / computed value of `key` and None — or, on a miss with
        `stale_ok` and an older drawing of `slot` on disk, that drawing and {key, at} while
        `key` is computed in the background. `slot` None: no pointer kept, never stale."""
        hit = self.get(key)
        if hit is None and slot is not None and stale_ok:
            old = self._stale(slot, key, compute)
            if old is not None:
                return old
        value = hit if hit is not None else self.get_or_compute(key, compute)
        if slot is not None:
            self.slot_put(slot, key)
        with self._lock:
            self._failed.pop(key, None)
        return value, None

    def _stale(self, slot: str, key: str, compute) -> Optional[tuple[Any, dict]]:
        with self._lock:
            if key in self._failed or (key not in self._refreshing and len(self._refreshing) >= REFRESH_MAX):
                return None
        prev = self.slot_get(slot)
        if prev is None or prev["key"] == key:
            return None
        old = self.get(prev["key"])
        if old is None:
            return None
        with self._lock:
            start = key not in self._refreshing
            if start:
                self._refreshing[key] = dt.datetime.now().timestamp()
            self.stats["stale"] += 1
        if start:
            ctx = contextvars.copy_context()         # the tenant, the locale: the request's
            REFRESH_POOL.submit(ctx.run, self._refresh, key, compute, slot)
        return old, {"key": key, "at": prev.get("at")}

    def _refresh(self, key: str, compute, slot: str) -> None:
        try:
            self.get_or_compute(key, compute)        # joins a request computing the same key
        except Exception as e:                       # noqa: BLE001 — the next request computes it itself
            log.warning("chart refresh failed: %s", type(e).__name__)
            with self._lock:
                self._failed[key] = dt.datetime.now().timestamp()
                while len(self._failed) > FAILED_MAX:
                    self._failed.popitem(last=False)
        else:
            self.slot_put(slot, key)
        finally:
            with self._lock:
                self._refreshing.pop(key, None)

    def ready(self, keys: list[str]) -> dict:
        """{ready, failed, pending}: which keys a stale answer named can be fetched now."""
        out: dict = {"ready": [], "failed": [], "pending": []}
        for k in keys:
            with self._lock:
                mem, failed = k in self._mem, k in self._failed
            if mem or self._path(k).exists():            # put(): memory first, then an atomic file
                out["ready"].append(k)
            elif failed:
                out["failed"].append(k)
            else:
                out["pending"].append(k)
        return out


CACHE = RenderCache()
