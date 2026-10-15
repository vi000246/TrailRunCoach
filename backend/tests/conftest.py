"""Shared fixtures. Before anything else: the real-data guard (_guard.py) -
home is a temp folder and nothing may open, list or write the real ~/WKO5 or
~/.wko5coach. The real-data comparisons are the opt-in realdata/ suite."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.tests import _guard  # noqa: E402  (stdlib only; must run before any backend import)

_guard.install()

import pytest  # noqa: E402

from backend.engine import core_stub as _core_stub  # noqa: E402

collect_ignore = [] if _guard.REALDATA else ["realdata"]

# The public mirror (SP-500) has no calculation core: its modules are stubs
# (backend/engine/core_stub.py). A test that needs the core is skipped there, not failed
# (the hooks at the end of this file); with the core (the private repo) nothing changes.
CORE_INCLUDED = _core_stub.core_included()
CORE_SKIP = "核心未包含（公開版）"


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
    session_check.clear()
    with http.use_transport(httpx.MockTransport(refuse)):
        yield
    session_check.clear()


@pytest.fixture(autouse=True)
def _no_live_google(monkeypatch):
    """Never a live Google sign-in call (SP-58): the token exchange and the JWKS fetch go to a
    transport that refuses, unless the test installs its own fake (test_login_sp58.py). The
    sign-in mode is frozen per process at start-up: every test starts undecided, with fresh
    sign-in limits."""
    import httpx
    from backend.accounts import google, login

    def refuse(request):
        raise httpx.ConnectError(f"no live network in tests: {request.url.host}", request=request)
    monkeypatch.setattr(google, "TRANSPORT", httpx.MockTransport(refuse))
    google.forget_keys()
    login.reset_mode()
    login.reset_limits()
    yield
    google.forget_keys()
    login.reset_mode()
    login.reset_limits()


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
    # nor the training routes' files (engine/route_library.py, SP-640)
    from backend.engine import route_library
    monkeypatch.setattr(route_library, "ROOT", tmp_path_factory.mktemp("route_gpx"))
    # nor the race calculator's saved inputs (engine/race_calc_store.py)
    from backend.engine import race_calc_store
    monkeypatch.setattr(race_calc_store, "_default_db", lambda: None)
    # nor a course's stored surface, and no OSM lookup leaves the machine (SP-543: tests pass a fake server)
    from backend.engine import course_surface
    monkeypatch.setattr(course_surface, "_default_db", lambda: None)
    monkeypatch.delenv("TRC_OVERPASS_URL", raising=False)


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
def _empty_job_queue():
    """The server's job queue (backend/jobs.py) is per test: the overview status and the
    classification are queue jobs (SP-394), so a job one test left running (a gate it never
    released) must not hold a place in the next one."""
    from backend import jobs
    jobs.reset()
    yield
    jobs.reset()


@pytest.fixture(autouse=True)
def _no_worker_processes(monkeypatch):
    """The back-test and a whole classification run in a worker process in the app (SP-394 第 5
    條, backend/workproc.py); a spawned process would not see the fixtures' patched modules (the
    tmp folders above), so the tests compute in their own process. test_workproc_sp394.py turns
    them on where it tests them."""
    from backend import workproc
    monkeypatch.setenv(workproc.ENV, "0")


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
    if not CORE_INCLUDED:
        yield
        return
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
    if CORE_INCLUDED:
        from backend.engine.wko5expr import fitcache
        monkeypatch.setenv(fitcache.ENV_ROOT, str(tmp_path_factory.mktemp("fitcache")))
        monkeypatch.setenv(fitcache.ENV_WORKERS, "0")
    # an app started by a test (TestClient lifespan) never builds a Dataset in the background
    monkeypatch.setenv("WKO5COACH_NO_WARMUP", "1")
    # nor runs the daily automatic backup (it would read the real DB's backup folder)
    monkeypatch.setenv("WKO5COACH_NO_AUTO_BACKUP", "1")
    # nor the one-time rain backfill (api/rain_backfill.py: Open-Meteo calls, SP-299)
    monkeypatch.setenv("WKO5COACH_NO_RAIN_BACKFILL", "1")


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
    # nor a CP that moved by itself, seen when the 課表 is opened (plan_auto.check_cp, SP-468)
    monkeypatch.setattr(plan_auto, "after_cp_moved", lambda *a, **k: None)
    # nor a switch of the 自動 課表心率區間 (plan_auto.check_hr_model, SP-493)
    monkeypatch.setattr(plan_auto, "after_hr_model", lambda *a, **k: None)
    # nor a background calibration run (engine/calibrate.py; test_calibrate calls it itself)
    from backend.engine import calibrate
    monkeypatch.setattr(calibrate, "after_sync", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _hr_zone_model_default_lthr(monkeypatch):
    """SP-493 made 自動 the 課表心率區間 a runner who never chose gets (hr_profile.DEFAULT_SETTING). The
    suite's fixtures were written for the LTHR default: they keep it unless a test sets the model
    (test_auto_zones_sp493 tests 自動 itself)."""
    from backend.engine import hr_profile
    monkeypatch.setattr(hr_profile, "DEFAULT_SETTING", "lthr")


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


@pytest.fixture(autouse=True)
def _no_live_garmin(monkeypatch):
    """Never a live Garmin call (SP-91): the garminconnect package is replaced by a fake that
    refuses every call, unless the test installs its own (test_garmin_sp91.py); no 2-s gap
    between the fake's calls; the half-done MFA logins and the limits' clocks start clean."""
    from backend.sync import garmin_client, garmin_limits
    from backend.tests.garmin_fakes import refusing_module
    monkeypatch.setattr(garmin_client, "_LIB", refusing_module())
    monkeypatch.setattr(garmin_limits, "GAP_S", 0.0)
    garmin_client._PENDING.clear()
    garmin_limits.reset()
    yield
    garmin_client._PENDING.clear()
    garmin_limits.reset()


@pytest.fixture(autouse=True)
def _intervals_clean(monkeypatch):
    """intervals.icu (SP-346): its requests go through sync/http.client, so _no_live_sync_calls
    already refuses the network; here the app-wide counts, the 0.12-s gap, the connect states and
    the OAuth app's settings start clean (a developer's TRC_INTERVALS_* never reaches a test)."""
    from backend.sync import intervals_client, intervals_limits
    monkeypatch.setattr(intervals_limits, "GAP_S", 0.0)
    for n in (intervals_client.ENV_CLIENT_ID, intervals_client.ENV_CLIENT_SECRET,
              intervals_client.ENV_WEBHOOK_SECRET):
        monkeypatch.delenv(n, raising=False)
        monkeypatch.delenv(n + "_FILE", raising=False)
    intervals_limits.reset()
    intervals_client._STATES.clear()
    yield
    intervals_limits.reset()
    intervals_client._STATES.clear()


@pytest.fixture(autouse=True)
def _site_state_per_test(monkeypatch, tmp_path_factory):
    """The server-wide state (site.json: the COROS switch, breaker, paused accounts) and the sync
    event log live in a temp folder per test, and the COROS limits start clean (SP-385): a test
    whose mock COROS answers 429 never pauses the next test's account."""
    from backend import site_settings
    from backend.sync import coros_limits, events
    d = tmp_path_factory.mktemp("site")
    monkeypatch.setattr(site_settings, "PATH", d / "site.json")
    monkeypatch.setattr(events, "PATH", d / "sync_events.jsonl")
    site_settings.reset_cache()
    coros_limits.reset()
    from backend.sync import runner
    runner._LAST_AUTO.clear()                 # SP-338: the 5-minute merge of automatic runs
    yield
    coros_limits.reset()
    site_settings.reset_cache()


# ---- the public mirror (SP-500): a test that needs the calculation core is skipped -------------
# "Needs the core": it raised CoreNotIncluded, or it failed after a core stand-in was used
# (an API answered 503, a helper swallowed the exception). Only without the core.
_REPO = Path(__file__).resolve().parents[2]


def _left_out_file(exc) -> bool:
    """A repo file the test reads is missing: the mirror left it out (views/, docs/plans/…)."""
    name = getattr(exc, "filename", None)
    if not isinstance(exc, FileNotFoundError) or not name:
        return False
    try:
        Path(name).resolve().relative_to(_REPO)
    except ValueError:
        return False
    return True


def _core_skip(rep, path, line):
    rep.outcome = "skipped"
    rep.longrepr = (str(path), line, f"Skipped: {CORE_SKIP}")


if not CORE_INCLUDED:
    def pytest_collection_modifyitems(config, items):
        """@pytest.mark.needs_core: a test about the core that never touches a stand-in
        (it reads the core's source, a removed function's absence, a views/ file)."""
        for item in items:
            if item.get_closest_marker("needs_core"):
                item.add_marker(pytest.mark.skip(reason=CORE_SKIP))

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(item):
        item._core_hits = _core_stub.HITS

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(item, call):
        outcome = yield
        rep = outcome.get_result()
        if not rep.failed:
            return
        exc = call.excinfo.value if call.excinfo else None
        if (exc is not None and (_core_stub.is_core_missing(exc) or _left_out_file(exc))) or \
                _core_stub.HITS > getattr(item, "_core_hits", _core_stub.HITS):
            _core_skip(rep, item.path, item.location[1] or 0)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_make_collect_report(collector):
        before = _core_stub.HITS
        outcome = yield
        rep = outcome.get_result()
        if rep.failed and (_core_stub.HITS > before or "CoreNotIncluded" in str(rep.longrepr)):
            _core_skip(rep, collector.path, 0)
