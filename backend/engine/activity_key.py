"""
One activity, whichever file it was read from (2026-10-02).

The same activity has a different file in every data source — a WKO5
`.wko4`, a COROS FIT, a TP FIT, and in the merged 同步資料 source a
"coros/…" or "tp/…" path — and a different dataset index in each. Anything
the user stored about an activity (pack weight, solo hike, peak name, the
plan session it completed) must find it again after a source switch, so it
is resolved by the activity's LOCAL START TIME:

  1. the same file (exactly, or the same path without the merged source's
     "coros/" / "tp/" folder prefix);
  2. else the stored start (a record's "start", or the start in a WKO5 file
     name, `Athlete_2021_03_03_22_32.wko4` = local 22:32) within
     MATCH_TOL_MIN minutes of the activity's start — the nearest one.

COROS and TP starts of one activity differ by seconds (sync/dedup.py groups
them within 2 min); activity_tags uses the same ±3 min (MATCH_TOL_MIN).

Stores keyed by file stay keyed by file (nothing is rewritten); new records
also carry "start", and old ones are found through the WKO5 name or the
dataset that wrote them ("migrated additively").
"""
from __future__ import annotations

import bisect
import datetime as dt
import re
import threading
from typing import Callable, Iterable, Optional

MATCH_TOL_MIN = 3            # activity_tags.MATCH_TOL_MIN
SOURCE_PREFIXES = ("coros/", "tp/")
_WKO5_NAME = re.compile(r"(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})(?:_(\d{2}))?\.wko4$", re.I)


def key_of(start) -> Optional[str]:
    """'YYYY-MM-DDTHH:MM' (activity_tags.key_of)."""
    s = to_dt(start)
    return s.strftime("%Y-%m-%dT%H:%M") if s is not None else None


def to_dt(x) -> Optional[dt.datetime]:
    if x is None:
        return None
    if isinstance(x, dt.datetime):
        return x.replace(tzinfo=None) if x.tzinfo else x
    try:
        v = dt.datetime.fromisoformat(str(x))
    except ValueError:
        return None
    return v.replace(tzinfo=None) if v.tzinfo else v


def bare(file: Optional[str]) -> Optional[str]:
    """The path without the merged source's folder prefix ("coros/2025/a.fit" -> "2025/a.fit")."""
    if not file:
        return file
    f = str(file).replace("\\", "/")
    for p in SOURCE_PREFIXES:
        if f.startswith(p):
            return f[len(p):]
    return f


def same_file(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a) and bool(b) and bare(a) == bare(b)


def start_from_name(file: Optional[str]) -> Optional[dt.datetime]:
    """The local start a WKO5 file name carries (…_YYYY_MM_DD_HH_MM.wko4); None otherwise."""
    m = _WKO5_NAME.search(str(file or ""))
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    except ValueError:
        return None


class StartIndex:
    """Nearest-start lookup over (start, value) pairs."""

    def __init__(self, items: Iterable[tuple]):
        rows = sorted(((t, v) for t, v in ((to_dt(t), v) for t, v in items) if t is not None),
                      key=lambda x: x[0])
        self.ts = [t.timestamp() if t.tzinfo else t.replace(tzinfo=dt.timezone.utc).timestamp() for t, _ in rows]
        self.vals = [v for _, v in rows]

    def find(self, start, tol_min: float = MATCH_TOL_MIN):
        s = to_dt(start)
        if s is None or not self.ts:
            return None
        x = s.replace(tzinfo=dt.timezone.utc).timestamp()
        i = bisect.bisect_left(self.ts, x)
        best, bd = None, None
        for j in (i - 1, i):
            if 0 <= j < len(self.ts):
                d = abs(self.ts[j] - x)
                if d <= tol_min * 60 and (bd is None or d < bd):
                    best, bd = self.vals[j], d
        return best


def lookup(store: dict, file: Optional[str], start, start_of: Optional[Callable] = None):
    """The value of a {file: value} store for one activity (file, local start):
    the same file, else the record whose start (value["start"], else
    start_of(key), else the WKO5 file name) is nearest within ±3 min."""
    if not store:
        return None
    if file:
        if file in store:
            return store[file]
        b = bare(file)
        for k, v in store.items():
            if bare(k) == b:
                return v
    if start is None:
        return None
    return _index_of(store, start_of).find(start)


def lookup_key(store: dict, file: Optional[str], start, start_of: Optional[Callable] = None) -> Optional[str]:
    """Like lookup, but the matching key."""
    if not store:
        return None
    hit = lookup({k: k for k in store}, file, None)
    if hit is not None:
        return hit
    if start is None:
        return None
    idx = StartIndex((record_start(k, v, start_of), k) for k, v in store.items())
    return idx.find(start)


def record_start(key, value, start_of: Optional[Callable] = None):
    if isinstance(value, dict) and value.get("start"):
        return value["start"]
    if start_of is not None:
        s = start_of(key)
        if s is not None:
            return s
    return start_from_name(key)


def _index_of(store: dict, start_of) -> StartIndex:
    return StartIndex((record_start(k, v, start_of), v) for k, v in store.items())


def contains(keys: Iterable[str], file: Optional[str], start, starts: Optional[dict] = None) -> bool:
    """Membership of an activity in a set of file keys (e.g. the solo hikes):
    the same file, else a key whose start (`starts[key]`, else the WKO5 file
    name) is within ±3 min."""
    keys = list(keys or ())
    if not keys:
        return False
    if file and any(same_file(k, file) for k in keys):
        return True
    if start is None:
        return False
    return StartIndex(((starts or {}).get(k) or start_from_name(k), k) for k in keys).find(start) is not None


# ---------------------------------------------------------------------------
# dataset indexes (plan sessions' done_by)
# ---------------------------------------------------------------------------

def rebase_done_by(sessions: list[dict], activities: list[dict]) -> int:
    """Point every done session's done_by["index"] at the activity with the
    same start in `activities` (rows with "index" and "start"; the current
    dataset), in place. A stored index is a dataset position, which changes
    with the data source (WKO5 / COROS / TP / 同步資料) and with an older
    activity synced late; the stored "start" does not. An activity this
    source does not have gets index None (the session stays done with its
    stored numbers). A session outside the days `activities` cover (a
    calendar range) is left alone. Returns how many changed."""
    if not activities:
        return 0
    idx = StartIndex((a.get("start"), a) for a in activities if a.get("start"))
    days = sorted(str(a["start"])[:10] for a in activities if a.get("start"))
    if not days:
        return 0
    n = 0
    for s in sessions:
        d = s.get("done_by")
        if s.get("state") != "done" or not isinstance(d, dict) or not d.get("start"):
            continue
        if not days[0] <= str(d["start"])[:10] <= days[-1]:
            continue
        a = idx.find(d["start"])
        new = a.get("index") if a else None
        if new != d.get("index"):
            s["done_by"] = {**d, "index": new}
            n += 1
    return n


_REG: dict = {}
_FILES: dict = {}            # source -> {file (and its bare path): local start ISO}
_REG_LOCK = threading.Lock()


def register_dataset(ds) -> None:
    """A Dataset just built (api/wko5views._dataset_cfg): its (start, index)
    pairs, so stored plan rows read without a dataset at hand (plan_store.load,
    _plan_rows) get the current indexes (not for a parity build: it keeps
    the bad files, so its indexes differ); and every file's start, so a
    store keyed by another source's file name finds the activity
    (start_of_file)."""
    src = getattr(ds, "source", None) or "wko5"
    ws = list(getattr(ds, "workouts", []))
    files = {}
    for w in ws:
        s = w.entry.start.isoformat()
        files[w.entry.file] = s
        files.setdefault(bare(w.entry.file), s)
    for x in getattr(ds, "excluded", []) or []:
        if x.get("file") and x.get("start"):
            files.setdefault(x["file"], x["start"])
            files.setdefault(bare(x["file"]), x["start"])
    with _REG_LOCK:
        _FILES.setdefault(src, {}).update(files)
        if not getattr(getattr(ds, "config", None), "parity", False):
            _REG[src] = [{"index": w.idx, "start": w.entry.start.isoformat()} for w in ws]


def start_of_file(file: Optional[str]):
    """The local start of an activity file: from any registered Dataset, else
    the WKO5 file name; None when unknown."""
    if not file:
        return None
    with _REG_LOCK:
        for m in _FILES.values():
            s = m.get(file) or m.get(bare(file))
            if s:
                return s
    return start_from_name(file)


class ByStartDict(dict):
    """A {file: record} store (pack weight, peak names …) whose lookups also
    find the record of the same activity under another source's file name:
    the same file, the same bare path, else the nearest start within ±3 min
    (record["start"], else start_of_file(key)). Iteration / writing are a
    plain dict's; `key_for` gives the key a write should update; `find(file,
    start)` takes the activity's start when the caller has it."""

    _bare = None
    _sidx = None

    def _reset(self):
        self._bare = self._sidx = None

    def __setitem__(self, k, v):
        self._reset()
        dict.__setitem__(self, k, v)

    def __delitem__(self, k):
        self._reset()
        dict.__delitem__(self, k)

    def pop(self, k, *default):
        self._reset()
        return dict.pop(self, k, *default)

    def _resolve(self, file, start=None):
        if file is not None and dict.__contains__(self, file):
            return file
        if isinstance(file, str):
            if self._bare is None:
                self._bare = {}
                for k in dict.keys(self):
                    self._bare.setdefault(bare(k), k)
            k = self._bare.get(bare(file))
            if k is not None:
                return k
        s = start if start is not None else (start_of_file(file) if isinstance(file, str) else None)
        if s is None:
            return None
        if self._sidx is None:
            self._sidx = StartIndex((record_start(k, v, start_of_file), k) for k, v in dict.items(self))
        return self._sidx.find(s)

    def key_for(self, file: str, start=None) -> str:
        return self._resolve(file, start) or file

    def find(self, file, start=None, default=None):
        k = self._resolve(file, start)
        return dict.__getitem__(self, k) if k is not None else default

    def get(self, file, default=None):
        k = self._resolve(file)
        return dict.__getitem__(self, k) if k is not None else default

    def __contains__(self, file) -> bool:
        return self._resolve(file) is not None

    def __getitem__(self, file):
        k = self._resolve(file)
        if k is None:
            raise KeyError(file)
        return dict.__getitem__(self, k)


class ByStartSet(set):
    """A set of activity file keys (the solo hikes) whose `in` also matches
    the same activity under another source's file name (by start)."""

    def __init__(self, files=(), starts: Optional[dict] = None):
        super().__init__(files)
        self.starts = dict(starts or {})

    def _start(self, k):
        return self.starts.get(k) or start_of_file(k)

    def __contains__(self, file) -> bool:
        if set.__contains__(self, file):
            return True
        if not isinstance(file, str):
            return False
        if any(same_file(k, file) for k in set.__iter__(self)):
            return True
        s = start_of_file(file)
        if s is None:
            return False
        return StartIndex((self._start(k), k) for k in set.__iter__(self)).find(s) is not None


def registered(source: Optional[str] = None) -> Optional[list]:
    if source is None:
        try:
            from backend.engine.wko5expr.datasource import current_source
            source = current_source()
        except Exception:                   # noqa: BLE001
            return None
    with _REG_LOCK:
        return _REG.get(source)


def rebase_stored(sessions: list[dict]) -> list[dict]:
    """rebase_done_by against the registered Dataset of the current source;
    unchanged when none is registered yet."""
    rows = registered()
    if rows:
        rebase_done_by(sessions, rows)
    return sessions


def clear_registry() -> None:
    with _REG_LOCK:
        _REG.clear()
        _FILES.clear()
