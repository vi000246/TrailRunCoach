"""
GET /api/v1/wko5/activities/auto in the background: the AUTO activity type /
effort of every activity (the 活動列表 filters and editor) without holding a
request — or the server — for minutes.

The first computation on a cold cache reads every activity's samples (HR
effort, the run review's test detection, the as-of thresholds), several
minutes on a full COROS history. So:

  * single flight: one computation per Dataset, every request shares it;
  * background: the work is a job of the server's queue (backend/jobs.py, SP-394),
    like the dataset warm-up — `warm` when the warm-up asks, `user` once the
    活動列表 asks (a queued one is moved up); `single`, or `full` when most
    activities are to be computed (FULL_FROM: a new user, a code change). A
    request returns at once with what is known plus {state, n_done, n_total}
    the page polls;
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
    stale, while the changed ones are recomputed, newest first, in chunks;
  * in age tiers (SP-394 第 3 條): when at least FULL_FROM activities are to be
    computed (a code change after a deploy, a new user), the last 90 days go
    first — one job at the caller's priority (`sync_hot` / `deploy_hot` from the
    warm-up), stored on disk as soon as they are done — then the warm (to 365
    days) and cold (older) activities, each tier its own job at `warm` / `cold`
    (backend/jobs.py tier_of / tier_priority), newest first. The list shows the
    new hot values and the previous older ones (stale) meanwhile; the file keeps
    no signature until the last tier is done (a restart recomputes the rest). A
    change of the inputs between two tiers starts a new job (only the activities
    whose key changed again); `when_done` callbacks (the warm-up's sweep of the
    old series rows, api/wko5views._low_priority) run after the last tier.

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
# a job with at least this many activities without a stored value of the current code is a
# whole recompute: the queue's `full` class (one at a time for the server), else `single`
# (推估: ~0.05 s an activity on the NAS, SP-320 §4 — 100 ≈ 5 s, four chunks)
FULL_FROM = 100
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
        # `code`: both branches' code (_code_sig), read back by to_compute (SP-394)
        tmp.write_text(json.dumps({"v": CACHE_V, "sig": sig, "code": _code_sig(), "files": files},
                                  ensure_ascii=False), "utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.warning("activity auto: could not write the cache (%s)", type(e).__name__)
        try:
            tmp.unlink(missing_ok=True)      # never leave a half-written file behind
        except OSError:
            pass


def to_compute(ds, disk: dict) -> int:
    """At most how many activities a job will compute, known before the keys are built (the
    queue class): those without a stored value, every one after a code change (the file's
    `code`; a file written before it: each entry's run-branch code `pcode`)."""
    return sum(to_compute_tiers(ds, disk).values())


def _today(ds):
    import datetime as dt
    from backend.engine.wko5expr.dataset import day_to_date
    t = getattr(ds, "today", None)
    return dt.date.today() if t is None else day_to_date(t)


def _tier(w, today) -> str:
    from backend import jobs
    return jobs.tier_of(w.entry.start.date(), today)


def to_compute_tiers(ds, disk: dict) -> dict[str, int]:
    """to_compute by age tier ({"hot", "warm", "cold"}: backend/jobs.py tier_of on the
    activity's date, as of the Dataset's today)."""
    from backend import jobs
    files = disk.get("files") or {}
    code = disk.get("code")
    every = disk.get("v") != CACHE_V or (code is not None and code != _code_sig())
    run = _branch_code()["run"] if code is None and not every else None
    today = _today(ds)
    out = dict.fromkeys(jobs.TIERS, 0)
    for w in ds.workouts:
        if not every:
            e = files.get(w.entry.file)
            if isinstance(e, dict) and isinstance(e.get("auto"), dict) and (run is None or e.get("pcode") == run):
                continue
        out[_tier(w, today)] += 1
    return out


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------

class Job:
    """One background computation of one Dataset's auto values: a job of the server's queue,
    or one per age tier (`thread`: the jobs.Job of the step queued or running now — join /
    is_alive like the thread it replaced; `join` waits for every step)."""

    def __init__(self, ds, sig: str, recorded: list):
        from backend import jobs
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
        self.done = threading.Event()
        self.superseded = False
        self._callbacks: dict = {}                  # key -> fn, run once the last step is done
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
            self.done.set()
        n = dict.fromkeys(jobs.TIERS, 0) if same else to_compute_tiers(ds, disk)
        # SP-394 第 3 條: a whole recompute goes by age tier, the last 90 days first
        self.tiered = sum(n.values()) >= FULL_FROM
        self.step = "hot" if self.tiered else "all"
        self.cls = _cls(n["hot"] if self.tiered else sum(n.values()))
        self.priority = "warm"
        self.thread = None                  # the jobs.Job of the step queued / running now
        # the first step builds these; the later tiers reuse them
        self.keys: Optional[dict] = None
        self.kb = None
        self.files: dict[str, dict] = {}
        self.todo: set[int] = set()
        self.tier_by_idx: dict[int, str] = {}

    def start(self, priority: str = "warm") -> None:
        if self.state != "computing":
            return
        self.priority = priority
        self._submit(self.step, self.cls)

    def _submit(self, step: str, cls: str) -> None:
        from backend import jobs
        prio = self.priority if step in ("hot", "all") else _later(step, self.priority)
        # in the caller's tenant (the queue keeps its contextvars), like the warm-up
        self.thread = jobs.submit(lambda job, step=step: self._run(step), cls=cls, priority=prio,
                                  label="activity auto-classification" + ("" if step == "all" else f" ({step})"))

    def _run(self, step: str) -> None:
        from backend import jobs
        from backend.engine import activity_tags as AT
        from backend.engine.wko5expr.dataset import batched_flush
        ds = self.ds_ref()
        if ds is None or self.superseded:
            self._end()
            return
        try:
            t0 = time.monotonic()
            if self.keys is None:
                with batched_flush(ds):
                    keys, kb = activity_keys(ds, self.recorded, self.entries)
                today = _today(ds)
                self.keys, self.kb = keys, kb
                self.names = {w.idx: AT.key_of(w.entry.start) for w in ds.workouts}
                self.tier_by_idx = {w.idx: _tier(w, today) for w in ds.workouts}
                for w in ds.workouts:
                    e = self.entries.get(w.entry.file)
                    if isinstance(e, dict) and e.get("key") == keys[w.idx] and isinstance(e.get("auto"), dict):
                        # the probe follows the run branch's code even when this result did not change
                        self.files[w.entry.file] = {**e, "own": kb.own[w.idx], "pcode": kb.code["run"],
                                                    "probe": kb.probes.get(w.idx)}
                        with self.lock:
                            self.fresh[self.names[w.idx]] = e["auto"]
                    else:
                        self.todo.add(w.idx)
                with self.lock:
                    self.n_done = len(ds.workouts) - len(self.todo)
                log.info("activity auto: keys of %d activities in %.2f s, %d to compute", len(ds.workouts),
                         time.monotonic() - t0, len(self.todo))
            keys, kb, names = self.keys, self.kb, self.names
            part = set(self.todo) if step == "all" else {i for i in self.todo if self.tier_by_idx.get(i) == step}

            def progress(done, total, values):
                with self.lock:
                    for i, v in values.items():
                        self.fresh[names[i]] = v
                    self.n_done += len(values)
            out = compute_blocking(ds, progress=progress, recorded=self.recorded, only=part,
                                   context=kb.context) if part else {}
            run_code = kb.code["run"]
            for w in ds.workouts:
                if w.idx in out:
                    self.files[w.entry.file] = {"key": keys[w.idx], "own": kb.own[w.idx], "pcode": run_code,
                                                "probe": kb.probes.get(w.idx), "start": w.entry.start.isoformat(),
                                                "auto": out[w.idx]}
                    self.computed.append(w.entry.file)
            self.todo -= part
            nxt = next((t for t in jobs.TIERS[jobs.TIERS.index(step) + 1:]
                        if any(self.tier_by_idx.get(i) == t for i in self.todo)), None) if step != "all" else None
            if self.superseded:              # a newer job computes (and stores) from here
                self._end()
                return
            # what is not computed yet keeps its previous entry (served stale after a restart), and
            # the file its signature only once nothing is left
            pending = {w.entry.file: self.entries[w.entry.file] for w in ds.workouts
                       if w.idx in self.todo and isinstance(self.entries.get(w.entry.file), dict)}
            save_cache(ds, None if nxt else self.sig, {**pending, **self.files})
            log.info("activity auto (%s): %d of %d recomputed in %.2f s", step, len(part), len(ds.workouts),
                     time.monotonic() - t0)
            if nxt is not None and not self.superseded:
                with self.lock:
                    self.step = nxt
                n = sum(1 for i in self.todo if self.tier_by_idx.get(i) == nxt)
                self._submit(nxt, _cls(n))
                return
            with self.lock:
                self.state, self.n_done, self.finished = "ready", self.n_total, time.monotonic()
                self.stale.clear()
            callbacks = list(self._callbacks.values())
            self._end()
            for cb in callbacks:
                try:
                    cb()
                except Exception:            # noqa: BLE001 — a callback never fails the job
                    log.warning("activity auto: a done callback failed", exc_info=True)
        except Exception as e:               # noqa: BLE001 — reported to the page, the next request retries
            log.warning("activity auto failed: %s", e, exc_info=True)
            with self.lock:
                self.state, self.error, self.finished = "error", f"{type(e).__name__}: {e}"[:300], time.monotonic()
            self._end()

    def _end(self) -> None:
        """No step left: drop what only the steps needed and wake the waiters."""
        self.entries = {}                    # the disk copy is not needed once the job is done
        self.keys = self.kb = None
        self.files = {}
        if self.state == "computing" and self.superseded:
            self.finished = time.monotonic()
        self.done.set()

    def when_done(self, fn, key: Optional[str] = None) -> None:
        """Run `fn()` once every step is done (at once when it is; never after an error). One
        callback per `key` (the latest)."""
        with self.lock:
            if not self.done.is_set():
                self._callbacks[key or id(fn)] = fn
                return
            ok = self.state == "ready"
        if ok:
            fn()

    def supersede(self, newer: "Job") -> None:
        """The inputs changed while a later tier waited for its place: `newer` takes over (the
        waiting step does nothing) and runs this job's done callbacks."""
        with self.lock:
            self.superseded = True
            cbs, self._callbacks = self._callbacks, {}
        for k, fn in cbs.items():
            newer.when_done(fn, key=k)
        t = self.thread
        if t is None or t.state == "queued":
            self._end()

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait for every step; True when done."""
        return self.done.wait(timeout)

    def wait_for(self, tier: str, timeout: Optional[float] = None) -> None:
        """Wait (at most `timeout` s) until the steps up to `tier` are done (the race charts
        read the year's activities: the hot and warm tiers)."""
        from backend import jobs
        end = time.monotonic() + (timeout if timeout is not None else 1e9)
        order = jobs.TIERS
        while not self.done.is_set() and time.monotonic() < end:
            step = self.step
            if step != "all" and step in order and order.index(step) > order.index(tier):
                return
            t = self.thread
            if t is not None:
                t.join(min(1.0, max(0.0, end - time.monotonic())))
            else:
                self.done.wait(min(1.0, max(0.0, end - time.monotonic())))

    def can_supersede(self) -> bool:
        t = self.thread
        return self.step not in ("hot", "all") and t is not None and t.state == "queued"

    def snapshot(self) -> dict:
        with self.lock:
            auto = {**self.stale, **self.fresh}
            el = (self.finished or time.monotonic()) - self.started
            return {"state": self.state, "n_done": self.n_done, "n_total": self.n_total,
                    "stale": bool(self.stale) and self.state != "ready", "error": self.error,
                    "elapsed_s": round(el, 1), "tier": self.step if self.state == "computing" else None,
                    "auto": auto}


def _cls(n: int) -> str:
    """The queue class of a step computing `n` activities."""
    return "full" if n >= FULL_FROM else "single"


def _later(tier: str, priority: str) -> str:
    """A warm / cold tier's priority: its own, or the caller's when that is lower still."""
    from backend import jobs
    own = jobs.tier_priority(tier)
    return own if jobs.PRIORITIES[own] >= jobs.PRIORITIES.get(priority, 0) else priority


def job_for(ds, recorded: Optional[list] = None, priority: str = "warm") -> Job:
    """The Dataset's job (single flight): started on first use, restarted
    when the inputs changed (signature) and the previous one is not running —
    or only waits between two tiers (SP-394: superseded, `newer` takes over).
    `priority`: the queue's (backend/jobs.py) — the warm-up's after a sync / a
    deploy (`sync_hot` / `deploy_hot`: its hot tier; the warm and cold tiers at
    `warm` / `cold`), `user` when someone waits; a hot (or only) step still
    queued is moved up to it, a warm / cold back-fill never."""
    from backend import jobs
    from backend.engine import activity_tags as AT
    recorded = AT.load_recorded() if recorded is None else recorded
    sig = signature(ds, recorded)
    with _LOCK:
        job = _JOBS.get(ds)
        running = job is not None and job.state == "computing" and job.thread is not None and job.thread.is_alive()
        if job is not None and (job.sig == sig and job.state != "error" or running and not job.can_supersede()):
            if job.thread is not None and job.step in ("hot", "all"):
                jobs.promote(job.thread, priority)
            return job
        new = Job(ds, sig, recorded)
        if job is not None and running:
            job.supersede(new)
        _JOBS[ds] = new
        new.start(priority)
        return new


def status(ds) -> dict:
    """The endpoint's answer: {state, n_done, n_total, stale, error, elapsed_s, tier, auto: {key: values}}
    (`tier`: the age tier computing now — hot / warm / cold, or all — while computing)."""
    return job_for(ds, priority="user").snapshot()


def wait(ds, timeout: float = 600.0) -> dict:
    """Block until the job is done, every tier (tests, scripts)."""
    job = job_for(ds, priority="user")
    job.join(timeout)
    return job.snapshot()
