"""地區與時區 (generalize-athlete plan B3): engine/localtime.py (FIT offset /
browser zone / today_local), engine/region.py (tw | intl), the region-aware
map default and event labels. Synthetic data only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import localtime as LT
from backend.engine import region as RG
from backend.settings.repository import resolve_tz

NOW = dt.datetime(2026, 7, 1, 12, tzinfo=dt.timezone.utc)      # summer: Berlin is UTC+2


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


# ---- time zone ---------------------------------------------------------------------

def test_zone_of_auto():
    assert LT.zone_of_auto(None) is None and LT.zone_of_auto({}) is None
    z = LT.zone_of_auto({"fit": {"offset_min": 120}}, NOW)
    assert z.utcoffset(None) == dt.timedelta(hours=2)
    assert getattr(LT.zone_of_auto({"fit": {"offset_min": 120}, "browser": "Europe/Berlin"}, NOW), "key", None) \
        == "Europe/Berlin"                                       # agree: the IANA zone (knows DST)
    z = LT.zone_of_auto({"fit": {"offset_min": 480}, "browser": "Europe/Berlin"}, NOW)
    assert z.utcoffset(None) == dt.timedelta(hours=8)            # disagree: the watch wins
    assert LT.zone_of_auto({"browser": "Asia/Tokyo"}).key == "Asia/Tokyo"
    assert LT.zone_of_auto({"browser": "Not/AZone"}) is None


def test_resolve_tz_order(monkeypatch):
    monkeypatch.delenv("WKO5COACH_TZ", raising=False)
    auto = {"browser": "Asia/Tokyo"}
    assert resolve_tz("Europe/London", auto=auto).key == "Europe/London"   # the setting first
    monkeypatch.setenv("WKO5COACH_TZ", "America/New_York")
    assert resolve_tz(None, auto=auto).key == "America/New_York"           # then the env
    monkeypatch.delenv("WKO5COACH_TZ")
    assert resolve_tz(None, auto=auto).key == "Asia/Tokyo"                 # then detected
    assert resolve_tz(None) is not None                                    # then the system


def test_today_local_follows_the_zone(monkeypatch):
    monkeypatch.setattr(LT, "zone", lambda user_id=1: dt.timezone(dt.timedelta(hours=14)))
    assert LT.today_local() == dt.datetime.now(dt.timezone(dt.timedelta(hours=14))).date()


def test_fit_offset_on_a_file_without_local_time(tmp_path):
    from backend.tests.fit_builder import build_run
    p = tmp_path / "a.fit"
    p.write_bytes(build_run(dt.datetime(2026, 9, 1, 6, tzinfo=dt.timezone.utc), seconds=60))
    assert LT.fit_offset_min(p) is None
    assert LT.fit_offset_min(tmp_path / "missing.fit") is None


def test_refresh_from_fits_and_browser_zone(tmp_path, monkeypatch):
    from backend.db.models import WorkoutFile
    from backend.settings.repository import SettingsRepository
    f = tmp_path / "r.fit"
    f.write_bytes(b"x")
    monkeypatch.setattr(LT, "fit_offset_min", lambda p: 60)

    async def go():
        s = await _session()
        s.add(WorkoutFile(athlete_id=1, file_path=str(f), file_format="fit",
                          start_time_utc=dt.datetime(2026, 9, 1, 6), workout_date=dt.date(2026, 9, 1)))
        await s.commit()
        cur = await LT.refresh_from_fits(s, 1)
        assert cur["fit"]["offset_min"] == 60 and cur["fit"]["file"] == "r.fit"
        cur = await LT.set_browser_zone(s, "Europe/London", 1)
        assert cur == {"fit": cur["fit"], "browser": "Europe/London"}
        with pytest.raises(ValueError):
            await LT.set_browser_zone(s, "Mars/Olympus", 1)
        tz = await SettingsRepository(s, 1).timezone()
        assert tz is not None
    _run(go())


# ---- region --------------------------------------------------------------------------

def _cells(root, *cells):
    w = root / "weather"
    w.mkdir(parents=True, exist_ok=True)
    for i, (lat, lon, n) in enumerate(cells):
        for d in range(n):
            (w / f"{lat}_{lon}_2026-01-{d + 1:02d}.json").write_text("{}", "utf-8")


def test_region_from_the_home_cell(tmp_path):
    _cells(tmp_path, ("25.00", "121.50", 5), ("46.50", "8.00", 2))
    assert RG.home_cell(tmp_path) == (25.0, 121.5)
    assert RG.detect(tmp_path) == ("tw", "住家天氣格點")
    other = tmp_path / "o"
    _cells(other, ("46.50", "8.00", 4), ("25.00", "121.50", 1))
    assert RG.detect(other)[0] == "intl"


def test_region_fallbacks(tmp_path, monkeypatch):
    assert RG.detect(tmp_path, {"browser": "Asia/Taipei"}) == ("tw", "瀏覽器時區")
    assert RG.detect(tmp_path, {"browser": "Europe/Paris"})[0] == "intl"
    assert RG.detect(tmp_path) == ("intl", "預設")
    from backend.engine.wko5expr import datasource as DS
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, u=1: "tw" if k == RG.KEY else d)
    assert RG.region(root=tmp_path) == ("tw", "設定")


def test_region_api_kinds_and_map_default(monkeypatch, tmp_path):
    from backend.api import region as RAPI, plan as PA
    monkeypatch.setattr(RG, "region", lambda user_id=1, root=None: ("intl", "預設"))
    r = RAPI.get_region()
    assert r["region"] == "intl" and r["basemap_default"] == "osm"
    assert PA._kinds()["baiyue"] == "多日登山"
    monkeypatch.setattr(RG, "region", lambda user_id=1, root=None: ("tw", "設定"))
    assert PA._kinds()["baiyue"] == "百岳" and RG.default_basemap("tw") == "rudy"

    from backend.api import sync as SY
    from backend.settings.repository import SettingsRepository
    monkeypatch.setattr(SY, "_power_source", lambda: asyncio.sleep(0, result=("none", "預設")))

    async def go():
        s = await _session()
        repo = SettingsRepository(s, 1)
        out = await SY._sync_settings(repo)
        assert out["map_basemap"] == "rudy" and out["map_basemap_stored"] is None and out["region"] == "tw"
        monkeypatch.setattr(RG, "region", lambda user_id=1, root=None: ("intl", "預設"))
        assert (await SY._sync_settings(repo))["map_basemap"] == "osm"
        await repo.set("charts.map.basemap", "google-terrain")
        assert (await SY._sync_settings(repo))["map_basemap"] == "google-terrain"     # a choice wins
        await repo.set("athlete.region", "tw")
        with pytest.raises(ValueError):
            await repo.set("athlete.region", "jp")
    _run(go())


def test_open_meteo_asks_for_the_locations_zone():
    import inspect
    from backend.engine.racepower import weather as W
    src = inspect.getsource(W)
    assert "Asia/Taipei" not in src and src.count('"timezone": "auto"') >= 3
