"""SP-552: the CWA forecasts (登山 F-B0053-035 / -033, 鄉鎮 F-D0047-089 / -091) are one copy for
the whole site — downloaded once with the site's key, kept in the server root, read by every
tenant — and every tenant's race weather starts with CWA. CWA and Open-Meteo are fakes: no network."""
import datetime as dt
import threading
import time

import pytest

from backend import tenancy
from backend.engine.racepower import weather as WX

TODAY = dt.date(2026, 10, 14)
RACE = dt.date(2026, 10, 15)
# 七星山: a 登山預報 point at the race itself
PEAK_LAT, PEAK_LON = 25.17, 121.55


def _mountain_doc(day: str) -> dict:
    def at(h, key, v):
        return {"DataTime": f"{day}T{h:02d}:00:00+08:00", "ElementValue": [{key: v}]}
    wx = [{"ElementName": "溫度", "Time": [at(h, "Temperature", "14") for h in range(24)]},
          {"ElementName": "相對濕度", "Time": [at(h, "RelativeHumidity", "90") for h in range(24)]},
          {"ElementName": "露點溫度", "Time": [at(h, "DewPoint", "12") for h in range(24)]}]
    return {"cwaopendata": {"Sent": f"{day}T05:00:00+08:00", "Dataset": {"Locations": {"Location": [
        {"LocationName": "七星山", "Latitude": str(PEAK_LAT), "Longitude": str(PEAK_LON),
         "ParameterSet": {"Parameter": {"ParameterName": "id", "ParameterValue": "D001"}},
         "WeatherElement": wx}]}}}}


class Fake:
    def __init__(self, delay: float = 0.0):
        self.calls, self.delay, self.lock = [], delay, threading.Lock()

    def __call__(self, url, params, timeout):
        with self.lock:
            self.calls.append(url)
        if "opendataapi" in url:
            if self.delay:
                time.sleep(self.delay)
            return _mountain_doc(RACE.isoformat())
        raise RuntimeError("Open-Meteo must not be asked")

    def cwa(self):
        return [u for u in self.calls if "opendataapi" in u]


def _user(root, n):
    d = root / "users" / str(n)
    d.mkdir(parents=True)
    return tenancy.Tenant(id=f"u{n}", kind=tenancy.USER, root=d, shared=d, caps=tenancy.USER_CAPS)


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The server root holds the shared files; the key is the site's (env), no tenant's."""
    root = tmp_path / "server"
    root.mkdir()
    monkeypatch.setattr(WX, "SITE_DIR", root)
    monkeypatch.setattr(WX, "HOME", None)
    monkeypatch.setenv("CWA_API_KEY", "CWA-SITE-KEY-0001")
    return root


def _race(get, **kw):
    args = dict(date=RACE, lat=PEAK_LAT, lon=PEAK_LON, elevation_m=1120.0, name="七星山", today=TODAY, get=get)
    args.update(kw)
    return WX.race_conditions(**args)


def test_a_user_gets_cwa_weather_with_the_sites_key(site):
    g = Fake()
    with tenancy.use(_user(site, 2)):
        assert WX.load_key() is None                       # the key itself stays the owner's (SP-384)
        r = _race(g)
    assert r["provider"] == "cwa_hourly" and r["detail"]["cwa_location"] == "七星山"
    assert r["values"]["temp_c"] == pytest.approx(14)
    assert "中央氣象署" in r["detail"]["attribution"]
    assert all("open-meteo" not in u for u in g.calls)


def test_one_download_for_every_tenant_and_it_lives_in_the_server_root(site):
    g = Fake()
    a, b = _user(site, 2), _user(site, 3)
    with tenancy.use(a):
        ra = _race(g)
    with tenancy.use(b):
        rb = _race(g)
    with tenancy.use(tenancy.owner()):
        ro = _race(g)
    assert len(g.cwa()) == 1                               # 3-day file: once for the site
    assert ra["detail"]["cache"] == "miss" and rb["detail"]["cache"] == "hit" and ro["detail"]["cache"] == "hit"
    assert (site / f"cwa_{WX.CWA_HOURLY}.json").exists()
    assert not list(a.root.glob("cwa_*")) and not list(b.root.glob("cwa_*"))


def test_concurrent_requests_download_a_file_once(site):
    g = Fake(delay=0.2)
    out = []

    def run(n):
        with tenancy.use(_user(site, n)):
            out.append(WX.fetch_cwa(WX.CWA_HOURLY, "CWA-SITE-KEY-0001", get=g)["cache"])
    ts = [threading.Thread(target=run, args=(n,)) for n in range(2, 7)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(g.cwa()) == 1
    assert sorted(out) == ["hit"] * 4 + ["miss"]


def test_while_a_file_is_downloading_the_others_get_the_old_copy_at_once(site):
    old = dt.datetime(2026, 10, 14, 6, 0, tzinfo=WX.TZ)
    WX.fetch_cwa(WX.CWA_HOURLY, "k" * 12, now=old, get=Fake())
    g = Fake(delay=0.5)
    started = threading.Event()
    lock = WX._cwa_lock(site / f"cwa_{WX.CWA_HOURLY}.json")
    lock.acquire()                                         # a download in progress
    try:
        started.set()
        t0 = time.monotonic()
        r = WX.fetch_cwa(WX.CWA_HOURLY, "k" * 12, now=old + dt.timedelta(hours=5), get=g)
        assert time.monotonic() - t0 < 0.3
    finally:
        lock.release()
    assert r["cache"] == "stale" and g.cwa() == []


def test_the_parsed_file_is_reused_until_it_changes(site, monkeypatch):
    WX.fetch_cwa(WX.CWA_HOURLY, "k" * 12, get=Fake())
    reads = []
    real = WX.json.loads
    monkeypatch.setattr(WX.json, "loads", lambda s, *a, **k: reads.append(1) or real(s, *a, **k))
    for _ in range(3):
        WX.fetch_cwa(WX.CWA_HOURLY, "k" * 12, get=Fake())
    assert reads == []                                     # parsed when written, never again


def test_a_user_sees_the_site_has_a_key_but_never_the_key(site):
    with tenancy.use(_user(site, 2)):
        s = WX.key_status()
    assert s["configured"] is False and s["masked"] is None and s["shared"] is True
    assert "CWA-SITE" not in str(s)


def test_without_a_site_key_a_user_falls_through_to_the_global_forecast_as_before(site, monkeypatch):
    # SP-797: the global forecast is MET Norway's now (Open-Meteo's free API is non-commercial)
    from backend.tests.test_weather_met_sp797 import met_js
    monkeypatch.delenv("CWA_API_KEY")
    calls = []

    def get(url, params, timeout):
        calls.append(url)
        if url == WX.MET_FORECAST:
            return met_js(RACE, temp=15.0, rh=80.0)
        raise AssertionError(url)
    with tenancy.use(_user(site, 2)):
        r = _race(get)
    assert r["provider"] == "met_norway"
    assert not any("opendataapi" in u for u in calls)


# ---- the climatology (Q1 第 2 題, owner 2026-10-20): one copy for the whole site too ---------------

FAR = dt.date(2026, 12, 20)                                # past every forecast: the climatology answers


def _archive(calls, delay=0.0):
    from backend.tests.test_race_climatology import archive
    inner = archive(calls=calls)

    def get(url, params, timeout):
        if delay:
            time.sleep(delay)
        return inner(url, params, timeout)
    return get


def _far(get):
    return WX.race_conditions(date=FAR, lat=23.4712, lon=120.9571, elevation_m=1000.0, today=TODAY, get=get,
                              use_cwa=False)


def test_the_climatology_is_fetched_once_for_every_tenant_in_the_server_root(site):
    calls = []
    a, b = _user(site, 2), _user(site, 3)
    with tenancy.use(a):
        ra = _far(_archive(calls))
    with tenancy.use(b):
        rb = _far(_archive(calls))
    assert ra["provider"] == rb["provider"] == "climatology"
    assert len(calls) == WX.CLIM_YEARS                     # one archive call per year, once for the site
    assert ra["detail"]["cache"] == "miss" and rb["detail"]["cache"] == "hit"
    assert rb["values"] == ra["values"]
    assert len(list((site / "climatology").glob("*.json"))) == 1
    assert not (a.root / "climatology").exists() and not (b.root / "climatology").exists()


def test_concurrent_tenants_fetch_the_climatology_once(site):
    calls, out = [], []

    def run(n):
        with tenancy.use(_user(site, n)):
            out.append(_far(_archive(calls, delay=0.02))["detail"]["cache"])
    ts = [threading.Thread(target=run, args=(n,)) for n in range(2, 5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == WX.CLIM_YEARS
    assert sorted(out) == ["hit", "hit", "miss"]


def test_a_tenants_older_climatology_file_is_read_and_copied_to_the_site(site):
    calls = []
    u = _user(site, 2)
    with tenancy.use(u):                                   # as before SP-552 Q1: the file in the tenant folder
        first = WX.race_conditions(date=FAR, lat=23.4712, lon=120.9571, elevation_m=1000.0, today=TODAY,
                                   get=_archive(calls), use_cwa=False, cache_dir=u.shared)
    old = list((u.shared / "climatology").glob("*.json"))
    assert len(old) == 1 and len(calls) == WX.CLIM_YEARS
    with tenancy.use(u):
        r = _far(_archive(calls))
    assert len(calls) == WX.CLIM_YEARS and r["detail"]["cache"] == "hit"     # no new archive call
    assert r["values"] == first["values"]
    assert (site / "climatology" / old[0].name).exists() and old[0].exists()  # copied, the old one left
