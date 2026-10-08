"""Shared fixtures. Before anything else: the real-data guard (_guard.py) -
home is a temp folder and nothing may open, list or write the real ~/WKO5 or
~/.wko5coach. The real-data comparisons are the opt-in realdata/ suite."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.tests import _guard  # noqa: E402  (stdlib only; must run before any backend import)

_guard.install()

import pytest  # noqa: E402

collect_ignore = [] if _guard.REALDATA else ["realdata"]


@pytest.fixture(autouse=True)
def _no_real_data_folders():
    """Fails a test that opened / listed / wrote anything under the real
    ~/WKO5 or ~/.wko5coach (the audit hook raises; this catches the cases the
    code under test swallowed, e.g. a best-effort cache write)."""
    _guard.take_violations()
    yield
    got = _guard.take_violations()
    if got:
        pytest.fail("real data folder touched:\n  " + "\n  ".join(got[:10]), pytrace=False)


@pytest.fixture(autouse=True)
def _no_live_sync_calls():
    """Never a live COROS / TP call: every sync request (backend/sync/http.py)
    goes to a transport that refuses, unless the test installs its own
    MockTransport. The login check's cache (sync/session_check.py) is per test."""
    import httpx
    from backend.sync import http, session_check

    def refuse(request):
        raise httpx.ConnectError(f"no live network in tests: {request.url.host}", request=request)
    session_check.forget()
    with http.use_transport(httpx.MockTransport(refuse)):
        yield
    session_check.forget()


@pytest.fixture(autouse=True)
def _no_recheck_pause(monkeypatch):
    """The fake COROS deletes at once: no pause before the post-delete look (SP-358)."""
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "RECHECK_DELAY_S", 0)


@pytest.fixture(autouse=True)
def _no_user_wko5_views(monkeypatch):
    """Imported WKO5 views come only from a folder the user configures
    (WKO5_VIEWS_DIR / charts.wko5_views_dir); a test that wants one writes a
    synthetic .wko5chart (wko5chart_builder.py) and sets the env itself."""
    if not _guard.REALDATA:
        monkeypatch.delenv("WKO5_VIEWS_DIR", raising=False)


FAKE_TP_CLIENT =("fake-client-id", "fake-client-secret-for-tests")


@pytest.fixture(autouse=True)
def _test_secret_key(monkeypatch):
    """Tests never create or read ~/.wko5coach/secret.key."""
    from cryptography.fernet import Fernet
    from backend.settings import secrets
    monkeypatch.setenv("WKO5COACH_SECRET_KEY", Fernet.generate_key().decode())
    # never look at the repo's real sealed blob or the user's DB
    monkeypatch.setattr(secrets, "SEALED_FILES", [])
    monkeypatch.setattr(secrets, "_db_path", lambda: None)
    from backend.engine.wko5expr import datasource
    monkeypatch.setattr(datasource, "_db_path", lambda: None)
    secrets.reset_cache()
    yield
    secrets.reset_cache()


@pytest.fixture(autouse=True)
def _no_real_activity_tags(monkeypatch):
    """The engine never reads the user's activity tags (the app DB's
    activity_tags table); tests pass their own rows or a tmp DB path."""
    from backend.engine import activity_tags
    monkeypatch.delenv(activity_tags.TAGS_DB_ENV, raising=False)
    monkeypatch.setattr(activity_tags, "_default_db", lambda: None)
    activity_tags._memo.clear()
    activity_tags._rec_memo.clear()


@pytest.fixture(autouse=True)
def _no_real_event_gpx(monkeypatch, tmp_path_factory):
    """The engine never reads stored event GPX rows / files (engine/event_gpx.py)
    unless a test passes its own DB path / root."""
    from backend.engine import event_gpx
    monkeypatch.setattr(event_gpx, "_default_db", lambda: None)
    monkeypatch.setattr(event_gpx, "ROOT", tmp_path_factory.mktemp("event_gpx"))
    event_gpx._memo.clear()
    # nor a user template's route GPX files (engine/user_templates.py)
    from backend.engine import user_templates
    monkeypatch.setattr(user_templates, "ROOT", tmp_path_factory.mktemp("template_gpx"))
    # nor the race calculator's saved inputs (engine/race_calc_store.py)
    from backend.engine import race_calc_store
    monkeypatch.setattr(race_calc_store, "_default_db", lambda: None)


@pytest.fixture(autouse=True)
def _no_event_size_hooks(monkeypatch):
    """planning.event_size's calculator / climb-divisor hooks (SP-111) are unset in every test,
    also after a TestClient ran the app's lifespan (main.py installs them)."""
    from backend.engine import planning
    monkeypatch.setattr(planning, "HOURS_OF", None)
    monkeypatch.setattr(planning, "DIVISOR_OF", None)
    # nor the downhill hooks (SP-111 下坡升級, engine/downhill_recovery.install)
    monkeypatch.setattr(planning, "RACE_DOWNHILL_OF", None)
    monkeypatch.setattr(planning, "ATHLETE_DOWNHILL_OF", None)


@pytest.fixture(autouse=True)
def _no_registered_dataset():
    """engine/activity_key.py's registry of the last built Dataset is per
    test: a dataset built by one test never re-indexes another's plan rows."""
    from backend.engine import activity_key
    activity_key.clear_registry()
    yield
    activity_key.clear_registry()


@pytest.fixture(autouse=True)
def _no_memoised_plan():
    """Dataset.plan's memo (wko5expr/dataset.py tenant_plan) is per test: it is
    keyed on the plan.json stamp, which is the same "missing" for every test
    without a plan file, so a plan memoised by one test would otherwise be
    served to the next one and bypass its Plan.load stub."""
    from backend.engine.wko5expr import dataset
    dataset._PLAN_MEMO.clear()
    yield
    dataset._PLAN_MEMO.clear()


@pytest.fixture(autouse=True)
def _no_real_tp_client(monkeypatch, tmp_path_factory):
    """Tests never read the real ~/.wko5coach/tp_client.json or TP_* env."""
    from backend.sync import tp_client
    monkeypatch.delenv("TP_CLIENT_ID", raising=False)
    monkeypatch.delenv("TP_CLIENT_SECRET", raising=False)
    d = tmp_path_factory.mktemp("tpc")
    monkeypatch.setattr(tp_client, "TP_CLIENT_FILE", d / "missing_tp_client.json")


@pytest.fixture(autouse=True)
def _fit_root_in_tmp(monkeypatch, tmp_path_factory):
    """Synced FITs go to a temp folder, never ~/.wko5coach/fit."""
    from backend.sync import storage
    root = tmp_path_factory.mktemp("fitroot")
    monkeypatch.setattr(storage, "FIT_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def _fit_cache_in_tmp(monkeypatch, tmp_path_factory):
    """The FIT dataset cache (engine/wko5expr/fitcache.py) lives in a temp
    folder, never ~/.wko5coach/cache/fit; files are parsed inline (no
    process pool) unless a test asks for one."""
    from backend.engine.wko5expr import fitcache
    monkeypatch.setenv(fitcache.ENV_ROOT, str(tmp_path_factory.mktemp("fitcache")))
    monkeypatch.setenv(fitcache.ENV_WORKERS, "0")
    # an app started by a test (TestClient lifespan) never builds a Dataset in the background
    monkeypatch.setenv("WKO5COACH_NO_WARMUP", "1")
    # nor runs the daily automatic backup (it would read the real DB's backup folder)
    monkeypatch.setenv("WKO5COACH_NO_AUTO_BACKUP", "1")


@pytest.fixture(autouse=True)
def _no_real_heat_history(monkeypatch):
    """The heat-acclimation index never reads the real per-activity weather
    (~/.wko5coach/routes/activity_weather.json); tests pass their own rows."""
    from backend.engine import heat_data
    monkeypatch.setattr(heat_data, "exposures", lambda root=None: ([], {"missing": True}))


@pytest.fixture(autouse=True)
def _no_real_activity_weather(monkeypatch, tmp_path_factory):
    """The drift's temperature band (workout_review.activity_temp) never reads
    the real ~/.wko5coach/routes/activity_weather.json: it matches by date
    too, so a fake run on a real date would pick up the real weather. Tests
    pass `ds.activity_temps` or point routes.HOME at their own folder."""
    from backend.engine import routes, workout_review
    monkeypatch.setattr(routes, "HOME", tmp_path_factory.mktemp("routes_home"))
    workout_review._WX_CACHE.clear()
    yield
    workout_review._WX_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_auto_plan_after_sync(monkeypatch):
    """A sync test that imports an activity must not start an automatic plan
    run on the real DB (engine/plan_auto.py); test_plan_auto calls it itself."""
    from backend.engine import plan_auto
    monkeypatch.setattr(plan_auto, "after_sync", lambda *a, **k: None)
    # nor a threshold edit through the plan API (a CP change re-pushes to COROS)
    monkeypatch.setattr(plan_auto, "after_thresholds", lambda *a, **k: None)
    # nor a 進階設定 change the plan reads (api/calib.py: the Zone 3 unlock rule, SP-295)
    monkeypatch.setattr(plan_auto, "after_settings", lambda *a, **k: None)
    # nor a background calibration run (engine/calibrate.py; test_calibrate calls it itself)
    from backend.engine import calibrate
    monkeypatch.setattr(calibrate, "after_sync", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_real_hike_meta(monkeypatch, tmp_path_factory):
    """The per-activity pack (racepower_hike_meta.json; racepower/athlete.py)
    is never read from or written to ~/.wko5coach in tests."""
    from backend.engine.racepower import athlete
    monkeypatch.setattr(athlete, "HIKE_META", tmp_path_factory.mktemp("hikemeta") / "racepower_hike_meta.json")


@pytest.fixture
def tp_creds(monkeypatch):
    """Obviously fake OAuth client credentials via env."""
    monkeypatch.setenv("TP_CLIENT_ID", FAKE_TP_CLIENT[0])
    monkeypatch.setenv("TP_CLIENT_SECRET", FAKE_TP_CLIENT[1])
    return FAKE_TP_CLIENT
