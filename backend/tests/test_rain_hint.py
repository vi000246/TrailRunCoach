"""SP-299: the archive rain while an activity ran → 「這次活動期間下過雨（N mm），要標成濕路嗎？」
next to the 路況 choice, with a one-click 「標成濕」. Precipitation rides on the existing
activity-weather call and cache (route_weather.HOURLY, activity_weather.json); the hint never
marks anything, and there is none once a 路況 is marked, without coordinates or without weather.
Synthetic data only."""
import asyncio
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT
from backend.engine import route_weather as RW

ROOT = Path(__file__).resolve().parents[1]
DAY = "2025-07-01"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _day(rain=None, date=DAY):
    """One archive day of one point: 24 hour rows, `rain` = {hour: mm} (others 0); rain=False →
    no precipitation array at all (a day cached before SP-299)."""
    h = [f"{date}T{k:02d}:00" for k in range(24)]
    js = {"hourly": {"time": h, "temperature_2m": [25.0] * 24, "relative_humidity_2m": [70.0] * 24,
                     "dew_point_2m": [19.0] * 24}}
    if rain is not False:
        js["hourly"]["precipitation"] = [float((rain or {}).get(k, 0.0)) for k in range(24)]
    return js


def _at(hh, mm=0, date=DAY):
    return dt.datetime.fromisoformat(f"{date}T{hh:02d}:{mm:02d}")


# ---- the rain of the activity's hours --------------------------------------------------

def test_activity_rain_sums_the_hours_the_activity_ran_in():
    # Open-Meteo's hourly precipitation at T is the sum of T − 1 h … T
    d = [_day({7: 0.4, 8: 2.0, 9: 1.5, 10: 9.0})]
    # 07:10–08:40 met the 07–08 hour (row 08:00) and the 08–09 hour (row 09:00): 2.0 + 1.5
    assert RW.activity_rain(d, _at(7, 10), _at(8, 40)) == 3.5
    # 06:30–07:00 met only 06–07 (row 07:00); the 07–08 rain came after it
    assert RW.activity_rain(d, _at(6, 30), _at(7, 0)) == 0.4
    assert RW.activity_rain([_day()], _at(7), _at(9)) == 0.0           # a dry day
    # across midnight: both days' hours count
    d2 = [_day({23: 1.0}), _day({0: 0.0, 1: 2.0}, date="2025-07-02")]
    assert RW.activity_rain(d2, _at(22, 30), _at(0, 30, date="2025-07-02")) == 3.0


def test_activity_rain_is_unknown_without_precipitation_or_hours():
    assert RW.activity_rain([_day(False)], _at(7), _at(9)) is None        # cached before SP-299
    d = _day({8: 3.0})
    d["hourly"]["precipitation"][8] = None                                # a gap in an hour it ran in
    assert RW.activity_rain([d], _at(7, 30), _at(8, 30)) is None
    assert RW.activity_rain([], _at(7), _at(9)) is None
    assert RW.activity_rain([_day({8: 3.0})], _at(9), _at(8)) is None


def test_rain_hint_threshold_and_never_over_a_mark():
    assert AT.RAIN_HINT_MM == 1.0                                         # 推估 (the ticket's start)
    assert AT.rain_hint(3.2, None, True) == 3.2 and AT.rain_hint(1.0, None, True) == 1.0
    assert AT.rain_hint(0.9, None, True) is None                          # no rain to speak of
    assert AT.rain_hint(None, None, True) is None                         # no coordinates / weather
    assert AT.rain_hint(12.0, "dry", True) is None and AT.rain_hint(12.0, "wet", True) is None   # already marked
    # SP-299 follow-up (owner 2026-10-07): only 越野跑 / 登山健行 — never a road run
    assert AT.rain_hint(12.0, None, False) is None
    src = (ROOT / "engine" / "activity_tags.py").read_text(encoding="utf-8")
    assert "推估: ≥ 1 mm" in src


# ---- the activity-weather build: same call, same cache -------------------------------------

class _Track:
    """route_weather's view of a routes.Track: start, t, cm, lat / lon / e, len."""

    def __init__(self, start, secs, lat=24.5, lon=121.5, gps=True):
        self.start = start
        self.t = np.arange(0, secs + 1, 60.0)
        self.cols = {"cm": self.t.copy()}
        n = len(self.t)
        self.lat = np.full(n if gps else 0, lat)
        self.lon = np.full(n if gps else 0, lon)
        self.e = np.full(n if gps else 0, 300.0)

    def __len__(self):
        return len(self.t)


class _Archive:
    def __init__(self, rain):
        self.rain, self.calls = rain, []

    def __call__(self, url, params, timeout):
        self.calls.append(dict(params))
        assert "precipitation" in params["hourly"].split(",")             # asked in the same call
        n = len(str(params["latitude"]).split(","))
        out = [{"elevation": 300.0, "timezone": "Asia/Taipei", **_day(self.rain, params["start_date"])}
               for _ in range(n)]
        return out if n > 1 else out[0]


def test_fill_activities_stores_the_rain_without_an_extra_call(tmp_path):
    tracks = {"wet.fit": _Track(f"{DAY}T07:10:00", 90 * 60), "dry.fit": _Track(f"{DAY}T15:00:00", 60 * 60),
              "nogps.fit": _Track(f"{DAY}T18:00:00", 60 * 60, gps=False)}
    get = _Archive({8: 2.0, 9: 1.5})
    doc = RW.fill_activities(tracks, tmp_path, get, today=dt.date(2025, 8, 1))
    acts = doc["activities"]
    # both GPS activities share one (cell, day): ONE call carries temperature and rain alike
    assert len(get.calls) == 1
    assert acts["wet.fit"]["rain_mm"] == 3.5 and acts["dry.fit"]["rain_mm"] == 0.0
    assert acts["wet.fit"]["start"] == f"{DAY}T07:10:00" and acts["wet.fit"]["temp_c"] == 25.0
    assert "nogps.fit" not in acts                                        # no coordinates: no weather
    on_disk = RW.load_activity_weather(tmp_path)["activities"]
    assert on_disk["wet.fit"]["rain_mm"] == 3.5
    # a rebuild: all from the cache, no call
    RW.fill_activities(tracks, tmp_path, get, today=dt.date(2025, 8, 1))
    assert len(get.calls) == 1


def test_a_day_cached_before_precipitation_is_never_refetched(tmp_path):
    tr = _Track(f"{DAY}T07:10:00", 90 * 60)

    def old_get(url, params, timeout):                                    # the old HOURLY: no rain
        return {"elevation": 300.0, "timezone": "Asia/Taipei", **_day(False, params["start_date"])}
    RW.fill_activities({"a.fit": tr}, tmp_path, old_get, today=dt.date(2025, 8, 1))
    get = _Archive({8: 5.0})
    doc = RW.fill_activities({"a.fit": tr}, tmp_path, get, today=dt.date(2025, 8, 1))
    assert get.calls == []                                                # the cache is reused as is
    assert doc["activities"]["a.fit"]["temp_c"] == 25.0 and doc["activities"]["a.fit"]["rain_mm"] is None


# ---- the list API and the button --------------------------------------------------------

@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def _weather_file(home: Path, rows: dict):
    home.mkdir(parents=True, exist_ok=True)
    (home / RW.ACTIVITY_WX_FILE).write_text(json.dumps({"version": RW.ACTIVITY_WX_VERSION, "activities": rows}),
                                            "utf-8")


def test_list_carries_the_rain_and_the_button_marks_wet(tmp_path, no_plan, monkeypatch):
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.engine import routes as R
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path, classes=_trail_classes(tmp_path))
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    home = tmp_path / "routes"
    monkeypatch.setattr(R, "HOME", home)
    run = next(w for w in ds.workouts if w.entry.file == "2025/0.fit")
    start = run.entry.start.isoformat(timespec="seconds")
    # keyed by another source's file name: found by its start
    _weather_file(home, {"WKO5_run.wko4": {"temp_c": 25.0, "rain_mm": 3.2, "start": start}})
    lst = V.activities_list()
    a = {x["file"]: x for x in lst["activities"]}
    assert a["2025/0.fit"]["rain_mm"] == 3.2 and lst["rain_hint_mm"] == AT.RAIN_HINT_MM
    assert a["2025/1.fit"]["rain_mm"] is None                             # no weather: no hint
    assert a["2025/0.fit"]["surface"] is None                             # a hint, not a mark
    assert a["2025/0.fit"]["rain_kind"] is True                         # a trail run
    assert AT.rain_hint(a["2025/0.fit"]["rain_mm"], a["2025/0.fit"]["surface"], a["2025/0.fit"]["rain_kind"]) == 3.2
    # rain under the threshold / unknown rain: the row carries it, the hint rule says no
    _weather_file(home, {"WKO5_run.wko4": {"temp_c": 25.0, "rain_mm": 0.4, "start": start},
                         "old.wko4": {"temp_c": 20.0, "start": "2025-12-14T01:00:00"}})   # pre-SP-299 row
    a = {x["file"]: x for x in V.activities_list()["activities"]}
    assert AT.rain_hint(a["2025/0.fit"]["rain_mm"], None, True) is None and a["2025/1.fit"]["rain_mm"] is None

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        # 「標成濕」 = the 路況 choice 濕 (the page sends { surface: "wet" })
        r = await V.patch_activity(run.idx, {"surface": "wet"})
        assert r["surface"] == "wet" and r["tags"] == [AT.SURFACES["wet"]]
        await eng.dispose()
    _run(_inner())
    _weather_file(home, {"WKO5_run.wko4": {"temp_c": 25.0, "rain_mm": 3.2, "start": start}})
    a = {x["file"]: x for x in V.activities_list()["activities"]}["2025/0.fit"]
    assert a["surface"] == "wet" and AT.rain_hint(a["rain_mm"], a["surface"], a["rain_kind"]) is None   # marked: no hint


def _trail_classes(tmp_path, cls="trail"):
    """_fit_ds's classification of 2025/0.fit, as `cls` (its default is a road run)."""
    from backend.engine.wko5expr.fitdataset import _norm
    p = tmp_path / "fit" / "coros" / "2025" / "0.fit"
    r = {"id": 7, "file_path": str(p), "trail_classification": cls, "classification_overridden": True,
         "duplicate_of": None}
    return {_norm(p): r, "_by_id": {7: r}, "_by_name": {"0.fit": [r]}, "_dups": {}}


def test_road_runs_get_no_rain_hint_trail_and_hike_do(tmp_path, no_plan, monkeypatch):
    """SP-299 follow-up (owner 2026-10-07): only 越野跑 and 登山健行 (incl. 百岳 and the user's 爬山
    mark, sport_map.kind_of) — a road run is never hinted, however much it rained."""
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.engine import routes as R
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)                                                # 0.fit: a road run
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    home = tmp_path / "routes"
    monkeypatch.setattr(R, "HOME", home)
    run = next(w for w in ds.workouts if w.entry.file == "2025/0.fit")
    start = run.entry.start.isoformat(timespec="seconds")
    _weather_file(home, {"0.fit": {"temp_c": 25.0, "rain_mm": 8.0, "start": start}})
    a = {x["file"]: x for x in V.activities_list()["activities"]}
    road, car = a["2025/0.fit"], a["2025/1.fit"]
    assert road["rain_mm"] == 8.0 and road["rain_kind"] is False
    assert AT.rain_hint(road["rain_mm"], road["surface"], road["rain_kind"]) is None
    assert car["index"] is None and car["rain_kind"] is False             # an excluded file: not a trail run
    assert AT.rain_kind(run, None) is False

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        # the user marks it 爬山: a mountain day now → the hint applies (the PATCH answer says so)
        r = await V.patch_activity(run.idx, {"activity_type": "hike"})
        assert r["rain_kind"] is True
        await eng.dispose()
    _run(_inner())
    road = {x["file"]: x for x in V.activities_list()["activities"]}["2025/0.fit"]
    assert road["rain_kind"] is True and AT.rain_hint(road["rain_mm"], road["surface"], road["rain_kind"]) == 8.0


def test_rain_kind_rule():
    from types import SimpleNamespace as NS

    def w(sport="run", sport_type="running", tags=()):
        return NS(sport=sport, sport_type=sport_type, tags=list(tags), platform=None)
    assert AT.rain_kind(w(sport_type="trail running"), None) is True       # 越野跑
    assert AT.rain_kind(w(tags=["runningtrail"]), None) is True
    assert AT.rain_kind(w(sport="other", sport_type="hiking"), None) is True   # 登山健行
    assert AT.rain_kind(w(), None) is False                               # 路跑
    assert AT.rain_kind(w(), {"activity_type": "hike", "activity_type_overridden": True}) is True
    assert AT.rain_kind(w(), {"activity_type": "baiyue_group", "activity_type_overridden": True}) is True
    assert AT.rain_kind(w(sport_type="trail running"),
                        {"activity_type": "training", "activity_type_overridden": True}) is True
    assert AT.rain_kind(w(sport="bike", sport_type="cycling"), None) is False
    # an excluded file (no workout): its sport type, or the user's 爬山 mark
    assert AT.rain_kind_excluded("trail running", None) is True
    assert AT.rain_kind_excluded("hiking", None) is True and AT.rain_kind_excluded("running", None) is False
    assert AT.rain_kind_excluded("running", {"activity_type": "hike", "activity_type_overridden": True}) is True


def test_list_without_a_weather_file_has_no_hint(tmp_path, no_plan, monkeypatch):
    from backend.api import wko5views as V
    from backend.engine import routes as R
    from backend.tests.test_activity_edit import _fit_ds
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "tags.db")
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    monkeypatch.setattr(R, "HOME", tmp_path / "nothing")
    lst = V.activities_list()
    assert lst["rain_hint_mm"] is None and all(a["rain_mm"] is None for a in lst["activities"])


def test_editor_hint_wiring_and_i18n():
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert "S.rainHintMm = r.rain_hint_mm" in page
    # shown only while 未標, with rain known and ≥ the threshold; the button is the only writer
    assert "a.rain_kind && a.rain_mm != null && S.rainHintMm != null && a.rain_mm >= S.rainHintMm && !surfaceOf(a)" in page
    # a type change in the editor moves the activity in / out of 越野跑／登山健行 (the PATCH answer)
    assert '"rain_kind" in r' in page.split("function patchLocal(", 1)[1].split("\n}", 1)[0]
    assert 'saveFields(a, { surface: "wet" })' in page and "data-rain-wet" in page
    assert page.index('T("f_surface")') < page.index("data-rain-hint") < page.index('T("f_tags")')
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text(encoding="utf-8"))
        assert "{mm}" in cat["surface.rain_hint"] and cat["surface.mark_wet"], loc
    zh = json.loads((ROOT / "static" / "i18n" / "zh-TW" / "activity.json").read_text(encoding="utf-8"))
    assert zh["surface.rain_hint"] == "這次活動期間下過雨（{mm} mm），要標成濕路嗎？"
    assert zh["surface.mark_wet"] == "標成濕"
