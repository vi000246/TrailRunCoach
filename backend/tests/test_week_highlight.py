"""
SP-403: 課表統計's 這週亮點 (engine/achievements.week_highlight): one line a week, by rank —
a record (distance / climb / long-run time) > a 連續有練 milestone (4, 8, 12 … weeks) > this
week's longest / highest beating last week's > the sessions done; nothing -> None (no filler).
Synthetic activity rows only.
"""
from backend.engine import achievements as AC

MON = "2026-10-05"          # this week: Mon 10-05 … Sun 10-11


def act(day, km=8.0, climb=200.0, moving_s=3000, cat="trail", index=None, title=None):
    return {"index": index or hash((day, km, climb)) % 10_000, "date": day, "category": cat, "distance_km": km,
            "climbing_m": climb, "moving_s": moving_s, **({"title": title} if title else {})}


def history(n=12, km=20.0, climb=1200.0, moving_s=3 * 3600):
    """n earlier runs; the biggest one has km / climb / moving_s."""
    out = [act(f"2026-08-{d:02d}") for d in range(1, n)]
    return out + [act("2026-08-20", km=km, climb=climb, moving_s=moving_s)]


def test_a_record_comes_first():
    week = [act("2026-10-07", km=22.5, climb=900, moving_s=2 * 3600, title="LSD")]
    h = AC.week_highlight(week, history(), MON, streak_weeks=8, streak_this_week=True, completed=3)
    assert h["kind"] == "pr" and h["what"] == "distance" and h["value"] == 22.5 and h["prev"] == 20.0
    assert h["day"] == "2026-10-07" and h["title"] == "LSD"
    # climb record (distance not beaten)
    week = [act("2026-10-07", km=15, climb=1500)]
    h = AC.week_highlight(week, history(), MON, streak_weeks=1, streak_this_week=True, completed=1)
    assert (h["kind"], h["what"], h["value"]) == ("pr", "climb", 1500)
    # long-run time record: runs only, a hike doesn't set it
    week = [act("2026-10-08", km=15, climb=800, moving_s=4 * 3600)]
    assert AC.week_highlight(week, history(), MON, 1, True, 1)["what"] == "time"
    hike = [act("2026-10-08", km=15, climb=800, moving_s=4 * 3600, cat="hike")]
    assert AC.week_highlight(hike, history(), MON, 1, True, 1).get("kind") != "pr"


def test_no_record_with_too_little_history():
    week = [act("2026-10-07", km=40)]
    h = AC.week_highlight(week, history(n=3), MON, 0, False, 1)
    assert h["kind"] != "pr"


def test_a_streak_milestone_when_there_is_no_record():
    week = [act("2026-10-07", km=5, climb=100)]
    h = AC.week_highlight(week, history(), MON, streak_weeks=8, streak_this_week=True, completed=2)
    assert h == {"kind": "streak", "weeks": 8}
    # not a milestone, or this week not in the streak yet: no milestone
    assert AC.week_highlight(week, history(), MON, 7, True, 2)["kind"] != "streak"
    assert AC.week_highlight(week, history(), MON, 8, False, 2)["kind"] != "streak"


def test_longest_then_highest_against_last_week_then_the_count():
    last = [act("2026-09-29", km=10, climb=300, moving_s=3600)]
    before = history() + last
    week = [act("2026-10-06", km=12, climb=250, moving_s=4800, title="輕鬆跑"), act("2026-10-08", km=6, climb=100, moving_s=2000)]
    h = AC.week_highlight(week, before, MON, 2, True, 2)
    assert h == {"kind": "longest", "day": "2026-10-06", "title": "輕鬆跑", "moving_s": 4800, "more_s": 1200}
    week = [act("2026-10-06", km=8, climb=450, moving_s=3000)]
    h = AC.week_highlight(week, before, MON, 2, True, 1)
    assert (h["kind"], h["climb_m"], h["more_m"]) == ("highest", 450, 150)
    week = [act("2026-10-06", km=8, climb=200, moving_s=3000)]
    assert AC.week_highlight(week, before, MON, 2, True, 1) == {"kind": "count", "n": 1}


def test_nothing_means_none():
    assert AC.week_highlight([], history(), MON, 0, False, 0) is None
    # a bike ride isn't a run: no highlight from it
    assert AC.week_highlight([act("2026-10-06", km=60, cat="bike")], history(), MON, 0, False, 0) is None


def test_run_history_reads_runs_and_hikes_before_the_week(monkeypatch):
    import datetime as dt
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.tests.wko5_fakes import FakeDataset, FakeWorkout
    ws = [FakeWorkout(start=dt.datetime(2026, 10, 1, 7), sport="run", tags=["running", "runningtrail"],
                      sport_type="trail running", metrics={"distance": 12.0, "climbing": 600.0, "duration": 5400}),
          FakeWorkout(start=dt.datetime(2026, 10, 2, 7), sport="bike", tags=["cycling"], metrics={"distance": 40.0}),
          FakeWorkout(start=dt.datetime(2026, 10, 6, 7), sport="run", tags=["running"], metrics={"distance": 8.0})]
    monkeypatch.setattr(OV, "_dataset", lambda: FakeDataset(ws, dt.date(2026, 10, 7)))
    h = PSA._run_history(MON)
    assert [(x["date"], x["category"], x["distance_km"], x["climbing_m"]) for x in h] == \
        [("2026-10-01", "trail", 12.0, 600.0)]
