"""
GET /api/v1/wko5/activities/auto in the background: the AUTO activity type /
effort of every activity (the 活動列表 filters and editor) without holding a
request — or the server — for minutes.

The first computation on a cold cache reads every activity's samples (HR
effort, the run review's test detection, the as-of thresholds), several
minutes on a full COROS history. So:

  * single flight: one computation per Dataset, every request shares it;
  * background: the work runs in a daemon thread (like the dataset warm-up);
    a request returns at once with what is known plus {state, n_done,
    n_total} the page polls;
  * per activity (SP-334): every activity has its own key (`activity_keys`):
    its file and metadata, the user's 測試 mark, the recorded RPE, the
    thresholds of its day (the plan rows up to that day and the runs of the
    window the as-of estimate reads), the plan rows of its day, the road
    rule's cross-run values (HRmax, the longer power reference) and the hash
    of the code its branch runs (outdoor run / other). Only the activities
    whose key changed are recomputed — a sync of one activity computes that
    one; deleting an old hike or editing a note computes nothing;
  * on disk: per activity file (the FIT dataset cache folder, or ~/.wko5coach
    for WKO5) with its key, so a restart answers from disk at once; after a
    change (a sync, a plan edit) the previous values are served, marked
    stale, while the changed ones are recomputed, newest first, in chunks.

The values are those of racepower.athlete.auto_tags_all (the same rules,
called per chunk: capacity_samples for outdoor runs, the HR effort rule for
the rest).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import os
import threading
import time
import weakref
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CHUNK = 25                 # activities per published step
CACHE_V = 2                # 2: per-activity keys (SP-334); 1 was one signature over the dataset
_JOBS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# the computation (the same rules as athlete.auto_tags_all)
# ---------------------------------------------------------------------------

def _recorded_stamp(recorded: list) -> tuple:
    return (len(recorded), sum(r.get("rpe") or 0 for r in recorded), sum(r.get("feel") or 0 for r in recorded))


def _other(ds, w, recorded) -> dict:
    """A workout that is not an outdoor run: auto_tags_all's second branch."""
    from backend.engine import activity_tags as AT
    from backend.engine import sport_map as SM
    from backend.engine.racepower import athlete as A
    try:
        th = A.thresholds_as_of(ds, w.entry.start.date())
        es = A.effort_stats(ds, w, th)
        eff = AT.effort_hr(es, th.get("lthr"), th.get("aet"))
    except Exception:                       # noqa: BLE001 — one bad file never breaks the list
        es, eff = {}, {"effort": None, "reason": "無法計算"}
    rec = AT.recorded_of(recorded, w.entry.start, w.entry.file)
    eff = AT.effort_from_rpe((rec or {}).get("rpe"), es.get("rest_share"), eff, rec=rec) or eff
    typ, why = AT.auto_type(test=A._test_reason(ds, w), sport=w.sport, sport_type=w.sport_type,
                            title=getattr(w.entry, "title", "") or "", trail=A.is_trail(w),
                            baiyue_event=A.baiyue_on(ds, w.entry.start.date()), app_type=SM.app_type(w))
    return {"activity_type": typ, "activity_type_reason": why, "effort": eff["effort"], "effort_reason": eff["reason"]}


def _runs(ds, runs: list, recorded, context: Optional[dict] = None) -> dict[int, dict]:
    """Outdoor runs: auto_tags_all's first branch (capacity_samples). `context` =
    athlete.run_context(ds), built once per job instead of once per chunk."""
    from backend.engine.racepower import athlete as A
    tests = {w.idx: t for w in runs if (t := A._test_reason(ds, w))}
    caps = A.capacity_samples(ds, runs, tags=[], tests=tests, recorded=recorded, context=context)
    out: dict[int, dict] = {}
    for w in runs:
        tg = caps[w.idx]["tags"]
        out[w.idx] = {"activity_type": tg["activity_type_auto"], "activity_type_reason": tg["activity_type_reason"],
                      "effort": tg["effort_auto"], "effort_reason": tg["effort_reason"]}
    return out


def _chunk_values(ds, chunk: list, recorded: list, context: Optional[dict] = None) -> dict[int, dict]:
    from backend.engine.racepower import athlete as A
    runs = [w for w in chunk if A.outdoor(w)]
    out: dict[int, dict] = _runs(ds, runs, recorded, context) if runs else {}
    for w in chunk:
        if w.idx not in out:
            out[w.idx] = _other(ds, w, recorded)
    return out


def compute_blocking(ds, progress=None, recorded: Optional[list] = None, only: Optional[set] = None,
                     context: Optional[dict] = None) -> dict[int, dict]:
    """{idx: auto values} of every workout (or of the `only` idx), newest first in
    chunks; `progress(done, total, {idx: values})` after each chunk. Series writes
    are batched (dataset.batched_flush). `context` = athlete.run_context(ds) when the
    caller has it (built here otherwise, once)."""
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import batched_flush
    recorded = AT.load_recorded() if recorded is None else recorded
    order = sorted((w for w in ds.workouts if only is None or w.idx in only), key=lambda w: w.entry.start,
                   reverse=True)
    out: dict[int, dict] = {}
    with batched_flush(ds):
        if context is None and any(A.outdoor(w) for w in order):
            context = A.run_context(ds)
        for i in range(0, len(order), CHUNK):
            part = _chunk_values(ds, order[i:i + CHUNK], recorded, context)
            out.update(part)
            if progress is not None:
                progress(min(i + CHUNK, len(order)), len(order), part)
    return out


# ---------------------------------------------------------------------------
# code versions: one per branch (SP-320 ①: only the functions each one reaches)
# ---------------------------------------------------------------------------

# what each branch's values come from; the run branch reaches capacity_samples,
# run_context / run_probe (the probes kept per activity) and workout_review's test rule
BRANCH_ROOTS = {"run": lambda: [_runs], "other": lambda: [_other]}
_CODE: dict = {}


def _branch_code() -> dict[str, str]:
    """{"run": hash, "other": hash}: a changed rule invalidates only the results that ran it."""
    hit = _CODE.get("branches")
    if hit is None:
        from backend.engine.codehash import code_hash
        from backend.engine.wko5expr.dataset import Dataset
        from backend.engine.wko5expr.fitdataset import FitFolderDataset
        hit = _CODE["branches"] = {b: code_hash(*roots(), context=[Dataset, FitFolderDataset], extra=(b, CACHE_V))
                                   for b, roots in BRANCH_ROOTS.items()}
    return hit


def _code_sig() -> str:
    """The code the values come from, both branches (the job trigger, `signature`)."""
    b = _branch_code()
    return f"{CACHE_V}:{hashlib.sha1((b['run'] + b['other']).encode()).hexdigest()[:12]}"


def _file_stamp(ds, w):
    try:
        from backend.engine.wko5expr.fitcache import stamp_of
        return stamp_of(Path(ds.dir) / w.entry.file)
    except (OSError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# the job trigger: a cheap signature of every input (a change starts a job, which
# then recomputes only the activities whose own key changed)
# ---------------------------------------------------------------------------

def _user_marks(rows: list) -> list:
    """The part of the user's activity tags the auto values read: the 測試 type mark
    (workout_review.classify → the test rule); an effort mark or a note is never read."""
    return [(r.get("start_local"), r.get("file"), r.get("activity_type"), bool(r.get("activity_type_overridden")))
            for r in rows or []]


def _test_sessions(ds) -> list:
    from backend.engine import workout_review as WR
    try:
        return WR._plan_test_sessions(ds)
    except Exception:                        # noqa: BLE001
        return []


def _calibration() -> str:
    """The drift windows in effect (workout_review.apply_calibration): read only by the
    runs' test rule (drift.ok → a steady AeT test)."""
    from backend.engine import workout_review as WR
    try:
        return WR.apply_calibration()
    except Exception:                        # noqa: BLE001
        return ""


def _rest_max():
    """每人校正 effort_rest_max (P9, effort_calib.rest_max): read by effort_hr /
    effort_from_rpe of every activity."""
    from backend.engine import activity_tags as AT
    try:
        return AT._rest_max()
    except Exception:                        # noqa: BLE001
        return None


def _calib_part() -> tuple:
    """The per-athlete calibrations the auto values read (the job trigger)."""
    return _calibration(), _rest_max()


_PD_INPUTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _pd_inputs(ds) -> tuple:
    """(synced FIT files [(date, path, size, mtime)], global part) the PD refits behind
    cp_as_of read beyond ds.workouts (pd_model → cptest.curves): the FIT Dataset's
    PdMemo.inputs, else (a WKO5 Dataset) the same listing, once per Dataset."""
    pm = getattr(ds, "pd_memo", None)
    if pm is not None and hasattr(pm, "inputs"):
        return pm.inputs()
    try:
        hit = _PD_INPUTS.get(ds)
    except TypeError:                        # not weak-referenceable (a test double)
        hit = None
    if hit is None:
        from backend.engine import bad_activity as BA
        from backend.engine.racepower import athlete as A
        from backend.engine.wko5expr.fitdataset import synced_fit_files
        try:
            files, skip, home = synced_fit_files()
            glob = (A.CP_WINDOW_DAYS, bool(getattr(ds, "accept_watch_power", False)), BA.read_setting(True),
                    BA.overrides_stamp(), home, skip)
        except Exception:                    # noqa: BLE001
            files, glob = [], None
        hit = (files, glob)
        try:
            _PD_INPUTS[ds] = hit
        except TypeError:
            pass
    return hit


def signature(ds, recorded: list) -> str:
    """Everything the auto values read, in one cheap hash: the code, every workout
    (file stamp, sport, tags, title, start), the plan (events, tests, thresholds),
    the stored test sessions, the user's 測試 marks, the dated settings and
    corrections, the watch RPEs, the drift calibration and the power settings. A
    change starts a job; the job recomputes only the activities whose key changed."""
    from backend.engine import activity_tags as AT
    h = hashlib.sha1()

    def add(x):
        h.update(repr(x).encode("utf-8"))
        h.update(b"\x00")
    add(_code_sig())
    add(type(ds).__name__)
    add(getattr(ds, "source", None))
    add(repr(getattr(ds, "config", None)))
    try:
        add(getattr(ds, "mftp_run", None))          # WKO5's PD snapshot (Dataset.cp → _settings_sig)
    except Exception:                        # noqa: BLE001
        pass
    for w in ds.workouts:
        add((w.entry.file, _file_stamp(ds, w), w.sport, w.sport_type, sorted(w.tags or []),
             getattr(w.entry, "title", ""), w.entry.start.isoformat(), getattr(w.entry, "ftp", None),
             sorted((getattr(w, "platform", None) or {}).items())))
    add(repr(getattr(ds, "plan", None)))
    try:
        add(sorted((k, [(str(d), v) for d, v in vals]) for k, vals in ds.athlete.settings.items()))
    except Exception:                        # noqa: BLE001
        pass
    corr = getattr(ds, "corrections", None)
    add([(c.file, c.channel, c.t_start, c.t_end) for c in (corr.items if corr else [])])
    add(getattr(ds, "accept_watch_power", None))
    add(_recorded_stamp(recorded))
    add(sorted(repr(sorted((k, repr(v)) for k, v in r.items())) for r in recorded))   # the whole row (the key's)
    add(_user_marks(AT.load()))
    add(json.dumps(_test_sessions(ds), sort_keys=True, default=str))
    add(_calib_part())
    # not the synced FITs of the PD refit (_pd_inputs): listing them costs a folder walk and
    # the PD code hash on every poll; a new synced FIT is a new Dataset (source_stamp) anyway
    return h.hexdigest()


# ---------------------------------------------------------------------------
# per-activity keys (SP-334)
# ---------------------------------------------------------------------------

def _h(x) -> str:
    return hashlib.sha1(repr(x).encode("utf-8")).hexdigest()[:20]


def thr_window_days() -> int:
    """The days before an activity whose runs its thresholds read: thresholds.estimate
    takes the runs of the 180 days up to the day, each measured against cp_as_of its
    own date — a plan CP, else the PD refit of the 90 days before that date or of one
    of the 30 days before it (athlete.CP_ASOF_BACK_DAYS)."""
    from backend.engine import thresholds as TH
    from backend.engine.racepower import athlete as A
    return int(TH.WINDOWS[-1] + A.CP_ASOF_BACK_DAYS + A.CP_WINDOW_DAYS + 1)


class _Near:
    """activity_tags.find without a scan of every row: find on the rows it could return
    — every row of the same file and every row starting within MATCH_TOL_MIN (+1) of
    the activity, in their stored order — gives the same answer as on all rows (the
    first same-file row, else the first same-minute row, else the nearest within the
    tolerance, the first of equals)."""

    def __init__(self, rows: list):
        from backend.engine.activity_key import bare, to_dt
        self.rows = rows or []
        self.by_file: dict = {}
        times = []
        for i, r in enumerate(self.rows):
            if r.get("file"):
                self.by_file.setdefault(bare(str(r["file"])), []).append(i)
            t = to_dt(r.get("start_local"))
            if t is not None:
                times.append((t, i))
        times.sort()
        self.times = times
        self.keys = [t for t, _ in times]

    def find(self, start, file: Optional[str]) -> Optional[dict]:
        from backend.engine import activity_tags as AT
        return AT.find(self.near(start, file), start, file) if self.rows else None

    def near(self, start, file: Optional[str]) -> list:
        import datetime as dt
        from backend.engine import activity_tags as AT
        from backend.engine.activity_key import bare, to_dt
        hit = set(self.by_file.get(bare(str(file)), ())) if file else set()
        s = to_dt(start)
        if s is not None:
            s = s.replace(second=0, microsecond=0)
            tol = dt.timedelta(minutes=AT.MATCH_TOL_MIN + 1)
            lo, hi = bisect.bisect_left(self.keys, s - tol), bisect.bisect_right(self.keys, s + tol)
            hit.update(i for _, i in self.times[lo:hi])
        return [self.rows[i] for i in sorted(hit)]


class _Keys:
    """Builds every activity's key once per job (see the module doc)."""

    def __init__(self, ds, recorded: list, entries: dict):
        from backend.engine import activity_tags as AT
        from backend.engine import workout_review as WR
        from backend.engine.racepower import athlete as A
        from backend.engine.wko5expr.dataset import date_to_day
        self.ds, self.entries = ds, entries
        self.code = _branch_code()
        # the drift calibration is not here: only runs read it (_test_part)
        self.glob = (CACHE_V, type(ds).__name__, getattr(ds, "source", None), repr(getattr(ds, "config", None)),
                     getattr(ds, "accept_watch_power", None), _rest_max())
        self.drift = _calibration()
        self.rec = _Near(recorded)
        self.marks = _Near(AT.load())
        plan = getattr(ds, "plan", None)
        self.plan_thr = sorted((str(t.date)[:10], repr(t)) for t in getattr(plan, "thresholds", None) or [])
        self.first_plan_lthr = min((str(t.date)[:10] for t in getattr(plan, "thresholds", None) or []
                                    if getattr(t, "lthr", None) is not None), default=None)
        self.plan_thr_dates = [d for d, _ in self.plan_thr]
        self.events_on: dict = {}
        for e in getattr(plan, "events", None) or []:
            self.events_on.setdefault(str(e.date)[:10], []).append(repr(e))
        self.thr_on: dict = {}
        for d, r in self.plan_thr:
            self.thr_on.setdefault(d, []).append(r)
        self.sessions = _test_sessions(ds)
        self.races = A.plan_race_runs(ds)
        self.window = thr_window_days()
        self.date_to_day = date_to_day
        self.wdate = WR._wdate
        # own part + per-run probe of every workout (the probe reused from disk while
        # the activity's own inputs and the run branch's code are the same)
        self._stamps: dict = {}
        self.own: dict[int, str] = {}
        self.probes: dict[int, Optional[dict]] = {}
        for w in ds.workouts:
            own = self._own(w)
            self.own[w.idx] = own
            e = entries.get(w.entry.file)
            if isinstance(e, dict) and e.get("own") == own and e.get("pcode") == self.code["run"] and "probe" in e:
                self.probes[w.idx] = e["probe"]
            else:
                self.probes[w.idx] = A.run_probe(ds, w)
        self.context = A.run_context(ds, self.probes)
        # sorted by day, for the window superset _cross hands to the real functions
        self.peaks = sorted(self.context["peaks"], key=lambda x: x[0])
        self.peak_days = [d for d, _ in self.peaks]
        self.held = sorted(self.context["held"], key=lambda x: x[0])
        self.held_days = [d for d, *_ in self.held]
        # the runs the thresholds of a day read: sorted by day, one short hash each
        runs = sorted(((math.floor(w.day), w) for w in ds.workouts if w.sport == "run"), key=lambda x: x[0])
        self.run_days = [d for d, _ in runs]
        self.run_rows = [self._thr_row(w) for _, w in runs]
        files, pglob = _pd_inputs(ds)
        self.pd_files = sorted(files)
        self.pd_dates = [f[0] for f in self.pd_files]
        self.pd_glob = pglob
        self._thr_memo: dict = {}

    def _stamp(self, w):
        hit = self._stamps.get(w.idx, self)
        if hit is self:
            hit = self._stamps[w.idx] = _file_stamp(self.ds, w)
        return hit

    def _cross(self, w, moving_s) -> tuple:
        """The road rule's cross-run values of one run (capacity_samples): HRmax as of
        its day and the longer power reference — the real functions, handed only the
        runs of a window around the day that holds theirs (both are order-free)."""
        from backend.engine.racepower import athlete as A
        from backend.engine.racepower import maximal as MX
        wh = MX.MAXIMAL["hrmax_window_days"] + 1
        pk = self.peaks[bisect.bisect_left(self.peak_days, w.day - wh):bisect.bisect_right(self.peak_days, w.day + 1)]
        wr = A.RIEGEL_WINDOW_DAYS + 1
        hd = self.held[bisect.bisect_left(self.held_days, w.day - wr):bisect.bisect_right(self.held_days, w.day + 1)]
        return MX.hrmax_as_of(pk, w.day), A.longer_power(hd, w.day, moving_s)

    def _own(self, w) -> str:
        ds = self.ds
        from backend.engine.racepower import athlete as A
        corr = ds._corr_sig(w.entry.file) if hasattr(ds, "_corr_sig") else None
        sset = ds._settings_sig(w) if hasattr(ds, "_settings_sig") else None
        return _h((w.entry.file, self._stamp(w), w.sport, w.sport_type, sorted(w.tags or []),
                   getattr(w.entry, "title", "") or "", w.entry.start.isoformat(), getattr(w.entry, "ftp", None),
                   sorted((getattr(w, "platform", None) or {}).items()), corr, sset,
                   A.power_ok(ds, w), A.power_source(ds, w)))

    def _thr_row(self, w) -> str:
        ds = self.ds
        from backend.engine.racepower import athlete as A
        return _h((w.entry.file, math.floor(w.day), self._stamp(w), w.sport_type, sorted(w.tags or []), A.power_ok(ds, w),
                   A.power_source(ds, w), ds._corr_sig(w.entry.file) if hasattr(ds, "_corr_sig") else None,
                   (w.metrics or {}).get("np")))

    def thr_sig(self, day) -> str:
        """The inputs of athlete.thresholds_as_of(ds, day): the plan rows up to the day
        (tests: LTHR / AeT / CP, their labels), the runs of the window before it, the
        synced files and settings the PD refits read, and the dataset's own LTHR history."""
        hit = self._thr_memo.get(day)
        if hit is not None:
            return hit
        import datetime as dt
        iso = day.isoformat()
        tday = int(math.floor(self.date_to_day(day)))
        lo = bisect.bisect_right(self.run_days, tday - self.window)
        hi = bisect.bisect_right(self.run_days, tday)
        plan = self.plan_thr[:bisect.bisect_right(self.plan_thr_dates, iso)]
        since = day - dt.timedelta(days=self.window)
        files = self.pd_files[bisect.bisect_right(self.pd_dates, since):bisect.bisect_right(self.pd_dates, day)] \
            if self.pd_files and not isinstance(self.pd_dates[0], str) else self.pd_files
        lthr = None
        # the dataset's own LTHR history counts only on a day without a plan LTHR up to it
        # (thresholds_as_of: plan test → estimate → setting)
        if self.first_plan_lthr is None or self.first_plan_lthr > iso:
            lthr = self._own_lthr(day)
        out = self._thr_memo[day] = _h((plan, self.run_rows[lo:hi], files, self.pd_glob, lthr))
        return out

    def _own_lthr(self, day):
        import datetime as dt
        ds = self.ds
        try:
            hist = ds.athlete.settings.get("runthr") or []
            lthr = (ds.athlete.setting_on("runthr", day), bool(hist) and all(d == dt.date(1980, 1, 1) for d, _ in hist),
                    ds.setting_source("runthr", "WKO5 設定") if hasattr(ds, "setting_source") else None)
        except Exception:                    # noqa: BLE001
            lthr = None
        return lthr

    def _test_part(self, w) -> tuple:
        """What workout_review's test rule (athlete._test_reason) reads beyond the file:
        the plan rows of the day (events, thresholds), the stored test sessions of the
        day or done by an activity (whether by this one), the user's 測試 mark."""
        if w.sport != "run":
            return ()
        iso = self.wdate(w).isoformat()
        ss = []
        for s in self.sessions:
            db = s.get("done_by") or {}
            if s.get("day") == iso or (db and db.get("date", iso) == iso):
                s2 = {k: v for k, v in s.items() if k != "done_by"}
                db2 = {k: v for k, v in db.items() if k != "index"}
                ss.append((json.dumps(s2, sort_keys=True, default=str), json.dumps(db2, sort_keys=True, default=str),
                           db.get("index") == w.idx))
        from backend.engine import activity_tags as AT
        return (self.events_on.get(iso, []), self.thr_on.get(iso, []), ss,
                AT.user_type(self.marks.find(w.entry.start, w.entry.file)), self.drift)

    def key(self, w) -> tuple[str, str]:
        """(branch, key) of one workout."""
        from backend.engine import sport_map as SM
        from backend.engine.racepower import athlete as A
        from backend.engine.racepower import maximal as MX
        ds = self.ds
        day = w.entry.start.date()
        r = self.rec.find(w.entry.start, Path(str(w.entry.file).replace("\\", "/")).name)
        rec = None if r is None else sorted((k, repr(v)) for k, v in r.items())
        if A.outdoor(w):
            branch = "run"
            trail = A.is_trail(w)
            cross = None
            if not trail:
                p = self.probes.get(w.idx) or {}
                cross = self._cross(w, p.get("moving_s"))
            part = (self.races.get(w.idx), trail, cross)
        else:
            branch = "other"
            part = (A.baiyue_on(ds, day), SM.app_type(w), A.is_trail(w))
        return branch, _h((self.glob, branch, self.code[branch], self.own[w.idx], rec, self.thr_sig(day), part,
                           self._test_part(w)))


def activity_keys(ds, recorded: list, entries: Optional[dict] = None) -> tuple[dict, "_Keys"]:
    """({idx: key}, the builder: own parts, probes, the run context) of every workout."""
    k = _Keys(ds, recorded, entries or {})
    return {w.idx: k.key(w)[1] for w in ds.workouts}, k


# ---------------------------------------------------------------------------
# the disk cache
# ---------------------------------------------------------------------------

def cache_path(ds) -> Path:
    store = getattr(ds, "_store", None)
    if store is not None and getattr(store, "home", None) is not None:
        return Path(store.home) / "activity_auto.json"
    from backend.engine.wko5expr import dataset as D
    key = hashlib.sha1(os.path.normcase(str(getattr(ds, "dir", ""))).encode()).hexdigest()[:10]
    return D._cache_dir() / f"activity_auto_{key}.json"


def load_cache(ds) -> dict:
    """{"v": 2, "sig": ..., "files": {file: {"key", "own", "pcode", "probe", "start", "auto"}}}
    (empty when unreadable). A version-1 file ({"sig", "files": {file: {"stamp", "start",
    "auto"}}}) loads too: its values are shown meanwhile and recomputed once."""
    try:
        d = json.loads(cache_path(ds).read_text("utf-8"))
        if isinstance(d, dict) and isinstance(d.get("files"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"sig": None, "files": {}}


def save_cache(ds, sig: Optional[str], files: dict) -> None:
    p = cache_path(ds)
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"v": CACHE_V, "sig": sig, "files": files}, ensure_ascii=False), "utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.warning("activity auto: could not write the cache (%s)", type(e).__name__)
        try:
            tmp.unlink(missing_ok=True)      # never leave a half-written file behind
        except OSError:
            pass


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------

class Job:
    """One background computation of one Dataset's auto values."""

    def __init__(self, ds, sig: str, recorded: list):
        from backend.engine import activity_tags as AT
        self.ds_ref = weakref.ref(ds)
        self.sig = sig
        self.recorded = recorded
        self.state = "computing"
        self.n_total = len(ds.workouts)
        self.n_done = 0
        self.error: Optional[str] = None
        self.started = time.monotonic()
        self.finished: Optional[float] = None
        self.fresh: dict[str, dict] = {}            # key -> values computed by this job
        self.stale: dict[str, dict] = {}            # key -> values from an earlier signature (shown meanwhile)
        self.computed: list[str] = []               # the files this job recomputed (tests, logs)
        self.lock = threading.Lock()
        disk = load_cache(ds)
        self.entries = by_file = disk.get("files") or {}
        same = disk.get("v") == CACHE_V and disk.get("sig") == sig
        for w in ds.workouts:
            e = by_file.get(w.entry.file)
            if not isinstance(e, dict) or not isinstance(e.get("auto"), dict):
                continue
            k = AT.key_of(w.entry.start)
            (self.fresh if same else self.stale)[k] = e["auto"]
        # by key: two activities starting in the same minute share one
        if same and len(self.fresh) == len({AT.key_of(w.entry.start) for w in ds.workouts}):
            self.state, self.n_done, self.finished = "ready", self.n_total, time.monotonic()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.state != "computing":
            return
        import contextvars
        # the request's tenant (a Thread does not carry contextvars), like the warm-up
        t = threading.Thread(target=contextvars.copy_context().run, args=(self._run,), name="activity-auto",
                             daemon=True)
        self.thread = t
        t.start()

    def _run(self) -> None:
        from backend.engine import activity_tags as AT
        from backend.engine.wko5expr.dataset import batched_flush
        ds = self.ds_ref()
        if ds is None:
            return
        try:
            t0 = time.monotonic()
            with batched_flush(ds):
                keys, kb = activity_keys(ds, self.recorded, self.entries)
            t_keys = time.monotonic() - t0
            names = {w.idx: AT.key_of(w.entry.start) for w in ds.workouts}
            files: dict[str, dict] = {}
            todo: set[int] = set()
            for w in ds.workouts:
                e = self.entries.get(w.entry.file)
                if isinstance(e, dict) and e.get("key") == keys[w.idx] and isinstance(e.get("auto"), dict):
                    # the probe follows the run branch's code even when this result did not change
                    files[w.entry.file] = {**e, "own": kb.own[w.idx], "pcode": kb.code["run"],
                                           "probe": kb.probes.get(w.idx)}
                    with self.lock:
                        self.fresh[names[w.idx]] = e["auto"]
                else:
                    todo.add(w.idx)
            kept = len(ds.workouts) - len(todo)
            with self.lock:
                self.n_done = kept

            def progress(done, total, part):
                with self.lock:
                    for i, v in part.items():
                        self.fresh[names[i]] = v
                    self.n_done = kept + done
            out = compute_blocking(ds, progress=progress, recorded=self.recorded, only=todo,
                                   context=kb.context) if todo else {}
            run_code = kb.code["run"]
            for w in ds.workouts:
                if w.idx in out:
                    files[w.entry.file] = {"key": keys[w.idx], "own": kb.own[w.idx], "pcode": run_code,
                                           "probe": kb.probes.get(w.idx), "start": w.entry.start.isoformat(),
                                           "auto": out[w.idx]}
                    self.computed.append(w.entry.file)
            save_cache(ds, self.sig, files)
            log.info("activity auto: %d of %d recomputed (keys %.2f s, total %.2f s)", len(todo), len(ds.workouts),
                     t_keys, time.monotonic() - t0)
            with self.lock:
                self.state, self.n_done, self.finished = "ready", self.n_total, time.monotonic()
                self.stale.clear()
        except Exception as e:               # noqa: BLE001 — reported to the page, the next request retries
            log.warning("activity auto failed: %s", e, exc_info=True)
            with self.lock:
                self.state, self.error, self.finished = "error", f"{type(e).__name__}: {e}"[:300], time.monotonic()
        finally:
            self.entries = {}                # the disk copy is not needed once the job is done

    def snapshot(self) -> dict:
        with self.lock:
            auto = {**self.stale, **self.fresh}
            el = (self.finished or time.monotonic()) - self.started
            return {"state": self.state, "n_done": self.n_done, "n_total": self.n_total,
                    "stale": bool(self.stale) and self.state != "ready", "error": self.error,
                    "elapsed_s": round(el, 1), "auto": auto}


def job_for(ds, recorded: Optional[list] = None) -> Job:
    """The Dataset's job (single flight): started on first use, restarted
    when the inputs changed (signature) and the previous one is not running."""
    from backend.engine import activity_tags as AT
    recorded = AT.load_recorded() if recorded is None else recorded
    sig = signature(ds, recorded)
    with _LOCK:
        job = _JOBS.get(ds)
        running = job is not None and job.state == "computing" and job.thread is not None and job.thread.is_alive()
        if job is not None and (job.sig == sig and job.state != "error" or running):
            return job
        job = Job(ds, sig, recorded)
        _JOBS[ds] = job
        job.start()
        return job


def status(ds) -> dict:
    """The endpoint's answer: {state, n_done, n_total, stale, error, elapsed_s, auto: {key: values}}."""
    return job_for(ds).snapshot()


def wait(ds, timeout: float = 600.0) -> dict:
    """Block until the job is done (tests, scripts)."""
    job = job_for(ds)
    t = job.thread
    if t is not None:
        t.join(timeout)
    return job.snapshot()
