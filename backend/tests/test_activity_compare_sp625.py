"""SP-625: any two activities compared — summary with B − A, kilometre splits,
the overlay on share completed / distance / time, pace by gradient group; and
two runs of one route lined up point by point (the route page's comparison)."""
import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import activity_compare as AC
from backend.engine import routes as R
from backend.tests.test_route_progress import LOOP, OUT_BACK, _builder, activity, hill_elev


def run(km=5.0, pace_s=300.0, hz=1.0, elev=None, hr=150.0, power=None, stop=None):
    """A synthetic run at a steady pace: arrays t (s), d (km), elev, hr, power.
    stop=(km, seconds) stands still there."""
    n = int(km * pace_s * hz) + 1
    t = np.arange(n) / hz
    d = t / pace_s
    if stop:
        k = int(stop[0] * pace_s * hz)
        t = np.concatenate((t[:k], t[k - 1] + 1 + np.arange(int(stop[1])), t[k:] + stop[1]))
        d = np.concatenate((d[:k], np.full(int(stop[1]), d[k - 1]), d[k:]))
    e = elev(d) if elev else np.full(len(d), 100.0)
    p = None if power is None else np.full(len(d), float(power))
    return AC.Series(t, d, e, np.full(len(d), hr), p)


def test_splits_are_the_time_of_each_kilometre_and_the_part_km():
    s = run(km=5.5, pace_s=300.0)
    sp = AC.splits(s)
    assert [x["km"] for x in sp] == [1, 2, 3, 4, 5, 6]
    assert all(x["time_s"] == pytest.approx(300.0, abs=1.0) for x in sp[:5])
    assert sp[-1]["len_km"] == pytest.approx(0.5, abs=0.01)
    assert sp[-1]["pace_min_km"] == pytest.approx(5.0, abs=0.05)
    # a 2-minute stop in km 3 is in that split (elapsed time, like a race clock)
    sp2 = AC.splits(run(km=5.0, pace_s=300.0, stop=(2.5, 120)))
    assert sp2[2]["time_s"] == pytest.approx(420.0, abs=2.0)
    assert sp2[1]["time_s"] == pytest.approx(300.0, abs=2.0)


def test_summary_and_b_minus_a():
    a, b = run(km=10, pace_s=360, hr=150, power=220), run(km=10, pace_s=330, hr=158, power=240)
    sa, sb = AC.summary(a, {"load": 80, "climbing": 120}), AC.summary(b, {"load": 90, "climbing": 120})
    assert sa["duration_s"] == pytest.approx(3600, abs=1) and sa["distance_km"] == pytest.approx(10, abs=0.01)
    assert sa["pace_min_km"] == pytest.approx(6.0, abs=0.01) and sb["pace_min_km"] == pytest.approx(5.5, abs=0.01)
    assert sa["avg_hr"] == 150 and sb["avg_power"] == 240
    d = AC.diff(sa, sb)
    assert d["duration_s"] == pytest.approx(-300, abs=2) and d["avg_hr"] == 8 and d["load"] == 10 and d["climbing_m"] == 0
    # the dataset's own totals win over the samples
    assert AC.summary(a, {"duration": 3700, "distance": 10.2})["duration_s"] == 3700


def test_overlay_by_share_distance_and_time():
    a, b = run(km=21.1, pace_s=300), run(km=21.1, pace_s=330)
    oa = AC.overlay(a)
    assert len(oa["pct"]) == 201 and oa["pct"][0] == 0 and oa["pct"][-1] == 100
    assert oa["dist_km"][-1] == pytest.approx(21.1, abs=0.01)
    assert oa["t_s"][100] == pytest.approx(21.1 / 2 * 300, abs=2)
    assert all(v == pytest.approx(5.0, abs=0.02) for v in oa["pace_min_km"])
    assert all(v == 150 for v in oa["hr"]) and oa["power"] == []
    ob = AC.overlay(b)
    # the same share completed, B later on the clock
    assert ob["t_s"][-1] - oa["t_s"][-1] == pytest.approx(21.1 * 30, abs=2)
    # a stop: no pace drawn while standing (slower than 60 min/km), the rest unchanged
    os_ = AC.overlay(run(km=2.0, pace_s=300, stop=(1.0, 3600)))
    assert any(v is None for v in os_["pace_min_km"])


def test_grade_groups_up_flat_down():
    # 2 km up at 10 %, 2 km flat, 2 km down at 10 %
    def elev(d):
        return np.where(d < 2, 100 + d * 100, np.where(d < 4, 300, 300 - (d - 4) * 100))
    s = AC.Series(*(lambda d: (d * 600, d, elev(d), np.full(len(d), 150.0), None))(np.linspace(0, 6, 3601)))
    g = AC.grade_groups(s)
    assert g["up"]["km"] == pytest.approx(2.0, abs=0.1)
    assert g["flat"]["km"] == pytest.approx(2.0, abs=0.2)
    assert g["down"]["km"] == pytest.approx(2.0, abs=0.1)
    assert g["up"]["avg_grade"] == pytest.approx(10, abs=0.5) and g["down"]["avg_grade"] == pytest.approx(-10, abs=0.5)
    assert g["up"]["pace_min_km"] == pytest.approx(10.0, abs=0.1)
    assert g["up"]["share"] + g["flat"]["share"] + g["down"]["share"] == pytest.approx(1.0, abs=0.01)
    # no elevation: no groups
    assert AC.grade_groups(AC.Series([0, 1, 2], [0, 0.5, 1.0])) is None


def test_compare_bundles_everything_for_two_courses():
    c = AC.compare(run(km=21.1, pace_s=300), run(km=21.3, pace_s=310))
    assert set(c) == {"summary", "splits", "overlay", "grades"}
    assert len(c["splits"]["a"]) == 22 and len(c["splits"]["b"]) == 22
    assert c["grades"]["a"]["flat"]["share"] == pytest.approx(1.0)


# --- API: the same route / segment is lined up point by point ---------------------

class FakeDS:
    """The two Dataset bits the API reads: workouts and channel(idx, name)."""

    def __init__(self, acts):
        self.workouts, self._ch = [], []
        for k, (m, ch) in enumerate(acts):
            start = dt.datetime.fromisoformat(m["start"])
            d = np.asarray(ch["dist_km"])
            self.workouts.append(SimpleNamespace(
                idx=k, sport=m["sport"], sport_type=m["sport_type"],
                entry=SimpleNamespace(file=m["file"], start=start),
                metrics={"duration": ch["t"][-1], "distance": float(d[-1]), "climbing": 10.0, "tss": 50.0}))
            self._ch.append({"elapsedtime": np.asarray(ch["t"], float), "elapseddistance": d,
                             "elevation": np.asarray(ch["elev"], float), "heartrate": np.asarray(ch["hr"], float)})

    def channel(self, idx, name):
        return self._ch[idx].get(name)


def _api(tmp_path, monkeypatch, acts, tags=()):
    from backend.api import activity_compare as API
    from backend.api import routes as RA
    from backend.engine import activity_tags as AT
    store = R.RouteStore(tmp_path / "routes")
    bld = R.Builder(store)
    monkeypatch.setattr(RA, "STORE", store)
    monkeypatch.setattr(RA, "BUILDER", bld)
    monkeypatch.setattr(RA, "_ensure_fresh", lambda: None)
    monkeypatch.setattr(RA, "_file_to_idx", lambda: {})
    monkeypatch.setattr(RA, "_phase_labels", lambda days: [None] * len(days))
    RA._CACHE.update(mtime=None, view=None)
    wl, rd = _builder(tmp_path, acts)
    bld.build(wl, rd)
    ds = FakeDS(acts)
    monkeypatch.setattr(API, "_ds", lambda: ds)
    monkeypatch.setattr(AT, "load", lambda *a, **k: list(tags))
    return API


def test_two_runs_of_one_route_are_lined_up_point_by_point(tmp_path, monkeypatch):
    acts = [activity(LOOP, start="2025-01-01T08:00", file="L0"),
            activity(LOOP, start="2025-02-01T08:00", noise=(5.0, 0.0), file="L1", speed=3.3),
            activity(list(reversed(LOOP)), start="2025-03-01T08:00", file="L2"),
            activity(OUT_BACK, elev_of=hill_elev, start="2025-04-01T08:00", file="H0")]
    API = _api(tmp_path, monkeypatch, acts)
    c = API.compare(0, 1)
    assert c["aligned"] is not None and c["aligned"]["kind"] == "route"
    from backend.api import routes as RA
    pt = RA.compare(c["aligned"]["id"], c["aligned"]["a"], c["aligned"]["b"])     # what the page asks next
    assert pt["gap_s"][-1] < 0                                                     # B (faster) ahead at the end
    # the summary / splits / overlay / grades are there too
    assert c["summary"]["a"]["distance_km"] == pytest.approx(6.0, abs=0.1) and len(c["splits"]["a"]) == 6
    # a run the other way round: the route can't be walked point by point, so different-course view + why
    c2 = API.compare(0, 2)
    assert c2["aligned"] is None and c2["aligned_note"]
    # a different course: nothing to line up
    c3 = API.compare(0, 3)
    assert c3["aligned"] is None and c3["aligned_note"] is None and c3["shared"] == []
    with pytest.raises(Exception):
        API.compare(1, 1)


def test_two_climbs_of_one_hill_line_up_on_the_segment(tmp_path, monkeypatch):
    # the same out-and-back twice: the route is the whole run; the climb and descent are shared segments
    acts = [activity(OUT_BACK, elev_of=hill_elev, start=f"2025-0{k + 1}-01T08:00", noise=(k * 4.0, 0.0),
                     speed=3.0 - 0.4 * k, file=f"g{k}") for k in range(2)]
    API = _api(tmp_path, monkeypatch, acts)
    c = API.compare(0, 1)
    assert c["aligned"] is not None
    kinds = {s["kind"] for s in c["shared"]}
    assert "climb" in kinds and all(s["a"] and s["b"] for s in c["shared"])
    climb = next(s for s in c["shared"] if s["kind"] == "climb")
    assert climb["b_s"] > climb["a_s"]                       # B is slower (2.6 vs 3.0 m/s)


def test_picker_lists_races_only_when_asked(tmp_path, monkeypatch):
    acts = [activity(LOOP, start="2025-01-01T08:00", file="L0"),
            activity(LOOP, start="2025-02-01T08:00", file="L1")]
    tags = [{"file": "L1", "start_local": "2025-02-01T08:00", "activity_type": "race",
             "activity_type_overridden": True}]
    API = _api(tmp_path, monkeypatch, acts, tags)
    every = API.activities()
    assert [a["index"] for a in every] == [1, 0] and [a["race"] for a in every] == [True, False]
    assert [a["index"] for a in API.activities(race=True)] == [1]


def test_page_is_served_in_both_languages():
    from backend.i18n.pages import render
    zh, en = render("actcompare", "zh-TW"), render("actcompare", "en")
    assert "兩次活動比較" in zh and "Compare two activities" in en
    assert "WKO5" not in zh + en and "TSS" not in zh + en
