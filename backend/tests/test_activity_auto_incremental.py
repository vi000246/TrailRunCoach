"""SP-334: the activity auto-classification per activity (api/activity_auto.py).

Each activity carries its own key (its file, sport, tags, title, the user's 測試 mark,
the recorded RPE, the day's thresholds and the runs of their window, the plan rows of
its day, the cross-run values of the road rule and the hash of its branch's code), so a
change recomputes only the activities whose inputs changed — and the result always
equals a full recompute on a fresh Dataset. Synthetic FIT folders only (fit_builder).
"""
import datetime as dt
from datetime import timezone

import pytest

from backend.api import activity_auto as AA
from backend.engine import activity_tags as AT
from backend.tests.fit_builder import build_run

UTC = timezone.utc
D0 = dt.datetime(2025, 6, 2, 1, 0, tzinfo=UTC)


def _acts():
    """name -> (start, build_run kwargs). Road runs with Stryd power, trail runs, a
    treadmill run and hikes over ~6 weeks, plus an old run and an old hike more than
    301 days (the thresholds' window) but less than 730 days (HRmax's) before."""
    out = {
        "old_run.fit": (dt.datetime(2024, 7, 1, 1, 0, tzinfo=UTC), dict(seconds=1800, hr=150, power=230, stryd=True)),
        "old_hike.fit": (dt.datetime(2024, 6, 28, 1, 0, tzinfo=UTC), dict(seconds=2400, hr=120, speed_m_s=1.0,
                                                                         climb_m_per_s=0.2, sport=17)),
    }
    kinds = ["road", "trail", "road", "hike", "road", "tread", "road", "trail", "road", "hike", "road", "road"]
    for i, k in enumerate(kinds):
        start = D0 + dt.timedelta(days=3 * i)
        if k == "road":
            kw = dict(seconds=1500 + 240 * (i % 4), hr=140 + 3 * (i % 5), power=240 + 5 * (i % 3), stryd=True,
                      speed_m_s=3.2)
        elif k == "trail":
            kw = dict(seconds=2700, hr=150, speed_m_s=2.2, climb_m_per_s=0.15, sub_sport=3)
        elif k == "tread":
            kw = dict(seconds=1500, hr=145, speed_m_s=3.0, sub_sport=1)
        else:
            kw = dict(seconds=3000, hr=118, speed_m_s=1.0, climb_m_per_s=0.2, sport=17)
        out[f"a{i:02d}_{k}.fit"] = (start, kw)
    return out


ACTS = _acts()
NEW = ("z_new_road.fit", (D0 + dt.timedelta(days=40), dict(seconds=1800, hr=152, power=250, stryd=True,
                                                            speed_m_s=3.3)))


class World:
    """The synthetic athlete: a FIT folder, the plan, the user tags, the recorded RPEs."""

    def __init__(self, tmp_path, monkeypatch):
        from backend.engine.planning import Event, Plan, Threshold
        self.dir = tmp_path / "fit" / "coros"
        self.dir.mkdir(parents=True)
        for name, (start, kw) in ACTS.items():
            self.write(name, start, kw)
        self.plan = Plan(events=[Event(id="r1", name="10K", date=(D0 + dt.timedelta(days=12)).date().isoformat(),
                                       kind="road", distance_km=5.0)],
                         thresholds=[Threshold(date="2024-08-01", lthr=168.0, cp=260.0)])
        self.tags: list = []
        self.recorded: list = []
        self.classes: dict = {}
        self.corr = tmp_path / "corr.json"
        self.keep: list = []                 # athlete.py memos key on id(ds): never let an id be reused
        monkeypatch.setattr(AT, "load", lambda *a, **k: self.tags)
        monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: self.recorded)

    def write(self, name, start, kw):
        (self.dir / name).write_bytes(build_run(start, **kw))

    def ds(self):
        from backend.engine.wko5expr.config import EngineConfig
        from backend.engine.wko5expr.corrections import CorrectionStore
        from backend.engine.wko5expr.fitdataset import FitFolderDataset
        d = FitFolderDataset(self.dir, config=EngineConfig(parity=False), today=dt.date(2025, 8, 1),
                             corrections=CorrectionStore(self.corr), classifications=self.classes,
                             athlete_settings=[], estimate_thresholds=False, tz=UTC)
        d.plan = self.plan
        d.plan_test_sessions = []
        self.keep.append(d)
        return d

    def by_name(self, ds):
        return {w.entry.file: w for w in ds.workouts}

    def incremental(self):
        """(the job's values keyed like the endpoint, the files the job computed)."""
        ds = self.ds()
        job = AA.job_for(ds)
        snap = AA.wait(ds)
        assert snap["state"] == "ready", snap.get("error")
        return snap["auto"], set(job.computed)

    def full(self):
        ds = self.ds()
        vals = AA.compute_blocking(ds, recorded=self.recorded)
        return {AT.key_of(w.entry.start): vals[w.idx] for w in ds.workouts}


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    first, computed = w.incremental()
    assert computed == set(ACTS)                     # a cold cache computes everything once
    assert first == w.full()
    return w


def _check(world, expect=None, within=None):
    got, computed = world.incremental()
    assert got == world.full()                       # incremental == a fresh full recompute
    if expect is not None:
        assert computed == set(expect)
    if within is not None:
        assert computed <= set(within)
    return computed


def test_nothing_changed_nothing_recomputed(world):
    _check(world, expect=[])


def test_a_new_activity_recomputes_only_itself(world):
    world.write(NEW[0], *NEW[1])
    _check(world, expect=[NEW[0]])


def test_deleting_an_old_hike_recomputes_nothing(world):
    (world.dir / "old_hike.fit").unlink()
    _check(world, expect=[])


def test_deleting_an_old_run_recomputes_at_most_the_road_runs_of_its_hrmax_window(world):
    (world.dir / "old_run.fit").unlink()
    # 301+ days before every recent activity: no thresholds window holds it; only the
    # road rule's 730-day HRmax can move, and only where its value changes
    _check(world, within=[n for n in ACTS if n.endswith("_road.fit")])


def test_a_user_effort_mark_recomputes_nothing_a_test_mark_only_that_run(world):
    ds = world.ds()
    w = world.by_name(ds)["a02_road.fit"]
    row = {"start_local": AT.key_of(w.entry.start), "file": w.entry.file, "activity_type": None,
           "activity_type_overridden": False, "effort": "max", "effort_overridden": True}
    world.tags.append(row)
    _check(world, expect=[])                         # the auto values never read the user's effort
    row.update(activity_type="test", activity_type_overridden=True)
    _check(world, expect=["a02_road.fit"])           # workout_review reads the user's 測試 mark


def test_a_recorded_rpe_recomputes_only_that_activity(world):
    ds = world.ds()
    w = world.by_name(ds)["a03_hike.fit"]
    world.recorded.append({"start_local": AT.key_of(w.entry.start), "file": "a03_hike.fit", "rpe": 9, "feel": None,
                           "coros_feel": None, "source": "watch"})
    _check(world, expect=["a03_hike.fit"])


def test_a_plan_threshold_recomputes_the_activities_from_its_day(world):
    from backend.engine.planning import Threshold
    day = (D0 + dt.timedelta(days=18)).date()
    world.plan.thresholds.append(Threshold(date=day.isoformat(), lthr=172.0))
    ds = world.ds()
    after = {w.entry.file for w in ds.workouts if w.entry.start.date() >= day}
    _check(world, expect=after)


def test_a_plan_race_recomputes_only_the_runs_of_its_day(world):
    from backend.engine.planning import Event
    ds = world.ds()
    w = world.by_name(ds)["a08_road.fit"]
    world.plan.events.append(Event(id="r2", name="5K", date=w.entry.start.date().isoformat(), kind="road",
                                   distance_km=5.0))
    _check(world, expect=["a08_road.fit"])


def test_reclassifying_a_run_recomputes_it_and_only_later_activities(world):
    ds = world.ds()
    w = world.by_name(ds)["a06_road.fit"]
    r = {"id": 1, "file_path": str(world.dir / "a06_road.fit"), "trail_classification": "trail",
         "classification_overridden": True, "duplicate_of": None, "coros_sport_type": None}
    world.classes.update({"_by_id": {1: r}, "_by_name": {"a06_road.fit": [r]}, "_dups": {}})
    later = {x.entry.file for x in ds.workouts if x.entry.start >= w.entry.start}
    computed = _check(world, within=later)
    assert "a06_road.fit" in computed


def test_a_changed_function_recomputes_only_the_results_that_used_it(world, monkeypatch):
    ds = world.ds()
    from backend.engine.racepower import athlete as A
    others = {w.entry.file for w in ds.workouts if not A.outdoor(w)}
    real = AA._branch_code()
    monkeypatch.setattr(AA, "_branch_code", lambda: {**real, "other": real["other"] + "-edited"})
    _check(world, expect=others)
    monkeypatch.setattr(AA, "_branch_code", lambda: {**real, "run": real["run"] + "-edited",
                                                     "other": real["other"] + "-edited"})
    _check(world, expect=set(ACTS) - others)


def test_the_branches_hash_only_the_code_they_reach():
    from backend.engine import codehash as C
    from backend.engine.wko5expr.dataset import Dataset
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    run = C.closure(AA.BRANCH_ROOTS["run"](), context=[Dataset, FitFolderDataset])
    other = C.closure(AA.BRANCH_ROOTS["other"](), context=[Dataset, FitFolderDataset])
    assert "backend.engine.racepower.maximal.road_maximal" in run
    assert "backend.engine.racepower.maximal.road_maximal" not in other
    assert "backend.engine.racepower.athlete.baiyue_on" in other
    assert "backend.engine.racepower.athlete.baiyue_on" not in run
    assert "backend.engine.racepower.athlete.thresholds_as_of" in run and \
        "backend.engine.racepower.athlete.thresholds_as_of" in other


def test_an_old_format_cache_is_served_meanwhile_and_rebuilt_once(tmp_path, monkeypatch):
    import json
    w = World(tmp_path, monkeypatch)
    ds = w.ds()
    p = AA.cache_path(ds)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = {"sig": "x", "files": {x.entry.file: {"stamp": None, "start": x.entry.start.isoformat(),
                                                "auto": {"activity_type": "easy", "activity_type_reason": "old",
                                                         "effort": None, "effort_reason": "old"}}
                                 for x in ds.workouts}}
    p.write_text(json.dumps(old), "utf-8")
    job = AA.Job(ds, AA.signature(ds, []), [])
    snap = job.snapshot()
    assert snap["state"] == "computing" and snap["stale"] and len(snap["auto"]) == len(ds.workouts)
    got, computed = w.incremental()
    assert computed == set(ACTS) and got == w.full()
    assert w.incremental()[1] == set()


def test_the_row_lookup_without_a_scan_answers_like_find():
    """_Near.find (the rows near one activity) == activity_tags.find (every row)."""
    import random
    rnd = random.Random(7)
    base = dt.datetime(2025, 6, 1, 7, 0)
    rows = []
    for i in range(300):
        t = base + dt.timedelta(minutes=rnd.choice([0, 1, 2, 3, 4, 5, 7, 60, 61, 1440]) + 1440 * rnd.randint(0, 20))
        f = rnd.choice([None, f"{rnd.randint(0, 40)}.fit", f"coros/2025/{rnd.randint(0, 40)}.fit"])
        rows.append({"start_local": AT.key_of(t), "file": f, "i": i})
    near = AA._Near(rows)
    for _ in range(400):
        s = base + dt.timedelta(minutes=rnd.randint(-10, 1440 * 21), seconds=rnd.randint(0, 59))
        f = rnd.choice([None, f"{rnd.randint(0, 40)}.fit", f"coros/2025/{rnd.randint(0, 40)}.fit"])
        assert near.find(s, f) is AT.find(rows, s, f)


def test_the_cross_run_values_on_a_window_equal_the_whole_history():
    """_Keys._cross hands hrmax_as_of / longer_power only a window of runs: the same values."""
    import random
    from types import SimpleNamespace
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import maximal as MX
    rnd = random.Random(3)
    peaks = [(rnd.uniform(0, 2000), rnd.uniform(150, 200)) for _ in range(500)]
    held = [(rnd.uniform(0, 2000), rnd.uniform(600, 9000), rnd.uniform(200, 300)) for _ in range(300)]
    k = AA._Keys.__new__(AA._Keys)
    k.peaks, k.held = sorted(peaks), sorted(held)
    k.peak_days, k.held_days = [d for d, _ in k.peaks], [d for d, *_ in k.held]
    for _ in range(300):
        w = SimpleNamespace(day=rnd.choice([rnd.uniform(0, 2100), float(int(rnd.uniform(0, 2100)))]))
        mv = rnd.uniform(600, 6000)
        assert k._cross(w, mv) == (MX.hrmax_as_of(peaks, w.day), A.longer_power(held, w.day, mv))


def test_the_per_run_probe_matches_capacity_samples(world):
    """capacity_samples with a context built from cached probes = without one."""
    from backend.engine.racepower import athlete as A
    ds = world.ds()
    runs = [w for w in ds.workouts if A.outdoor(w)]
    probes = {w.idx: A.run_probe(ds, w) for w in ds.workouts}
    import json
    probes = json.loads(json.dumps({str(k): v for k, v in probes.items()}))   # as kept on disk
    ctx = A.run_context(ds, {int(k): v for k, v in probes.items()})
    a = A.capacity_samples(ds, runs, tags=[], recorded=[], context=ctx)
    b = A.capacity_samples(ds, runs, tags=[], recorded=[])
    assert {i: v["tags"] for i, v in a.items()} == {i: v["tags"] for i, v in b.items()}
