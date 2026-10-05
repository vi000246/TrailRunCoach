"""SP-88: a COROS login that dies (e.g. after a login on the COROS web Training
Hub: result 1019 "Access token is invalid") must show on the 總覽 page —
登入已過期 / 無法同步, the last successful sync, the failed auto-sync — and
stay shown. Fake COROS only (never a real account)."""
import json
import time
from pathlib import Path

import httpx

from backend.api import auth as AUTH
from backend.api import sync as SYNC_API
from backend.settings.repository import SettingsRepository
from backend.sync import http, runner, session_check as SC
from backend.tests.test_coros_login_remember import PASSWORD, US, Coros
from backend.tests.test_sync_e2e import collect, make_session, run

STATIC = Path(__file__).resolve().parents[1] / "static"


async def _session(tmp_path):
    return await make_session(tmp_path, sync__primary_source="coros")


async def _login(s, fake, remember=False):
    with http.use_transport(httpx.MockTransport(fake)):
        await AUTH.coros_login(AUTH.CorosLoginRequest(email="me@example.com", password=PASSWORD,
                                                      remember=remember), s)


async def _sync(s, fake, trigger="open"):
    with http.use_transport(httpx.MockTransport(fake)):
        return await collect(runner.stream(s, "coros", 1, trigger=trigger))


def _age_cache(source="coros"):
    """The cached answer is older than CHECK_TTL_S (the next read checks again)."""
    for k, (v, at) in list(SC._CACHE.items()):
        if k[0] == source:
            SC._CACHE[k] = (v, at - SC.CHECK_TTL_S - 1)


def test_a_web_login_kills_the_token_and_the_overview_says_so(tmp_path):
    async def go():
        s = await _session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake)
        assert (await _sync(s, fake))[-1]["status"] == "complete"
        ok_at = await runner.last_sync_at(s, "coros", 1)
        fake.valid = "web-session"                       # logged in on the Training Hub: 1019 from now on
        _age_cache()
        with http.use_transport(httpx.MockTransport(fake)):
            r = await AUTH.session_alerts(1, s)
            p = await SYNC_API.sync_primary(1, s)
        sy = r["sync"]
        assert sy["problem"] == "expired" and sy["login"] == "expired" and sy["source"] == "coros"
        assert sy["last_ok_at"] == ok_at.isoformat()
        assert [a["source"] for a in r["expired"]] == ["coros"] and "sync" in r["expired"][0]["needs"]
        assert p["logged_in"] is False and p["login"] == "expired"
        assert PASSWORD not in json.dumps(r) and "tok1" not in json.dumps(r)
    run(go())


def test_an_unreachable_recheck_keeps_a_refused_login_expired(tmp_path):
    """Root cause 1: a sync that got 1019 marked the login expired in memory only;
    after CHECK_TTL_S the cached 登入已過期 lapsed, a re-check that could not reach
    COROS (timeout) answered unknown = 已登入, and the banner vanished."""
    async def go():
        s = await _session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake)
        fake.valid = "web-session"
        assert (await _sync(s, fake))[-1]["error"] == "COROS_AUTH_REQUIRED"
        assert SC.is_expired("coros", 1)
        _age_cache()
        fake.probe_ok = False                            # the re-check times out
        with http.use_transport(httpx.MockTransport(fake)):
            assert await SC.check(s, "coros", 1) == SC.EXPIRED
            assert (await AUTH.session_alerts(1, s))["sync"]["problem"] == "expired"
            assert (await SYNC_API.sync_primary(1, s))["logged_in"] is False
        # and it survives a restart (the in-memory answer gone): the refusal is stored
        SC.forget()
        with http.use_transport(httpx.MockTransport(fake)):
            assert await SC.check(s, "coros", 1) == SC.EXPIRED
        # a fresh login clears it
        fake.probe_ok = True
        await _login(s, fake)
        assert (await AUTH.session_alerts(1, s))["sync"]["problem"] is None
    run(go())


def test_the_sticky_answer_is_rechecked_soon(tmp_path, monkeypatch):
    async def go():
        s = await _session(tmp_path)
        await _login(s, Coros(home=US))
        SC.mark_expired("coros", 1)
        _age_cache()

        async def unreachable(db, athlete_id):
            return SC.UNKNOWN
        monkeypatch.setattr(SC, "_check_coros", unreachable)
        assert await SC.check(s, "coros", 1) == SC.EXPIRED
        at = SC._CACHE[("coros", 1)][1]
        assert time.monotonic() - at >= SC.CHECK_TTL_S - SC.UNKNOWN_TTL_S - 1   # re-checked within ~UNKNOWN_TTL_S
        SC.mark_ok("coros", 1)
        _age_cache()
        assert await SC.check(s, "coros", 1) == SC.UNKNOWN                     # never refused: 已登入 kept
    run(go())


def test_a_never_refused_login_still_reads_unknown_as_logged_in(tmp_path):
    async def go():
        s = await _session(tmp_path)
        await _login(s, Coros(home=US))
        SC.forget()                                      # conftest: every request refused
        assert await SC.check(s, "coros", 1) == SC.UNKNOWN
        assert (await AUTH.session_alerts(1, s))["sync"]["problem"] is None
    run(go())


def test_a_failed_auto_sync_is_not_a_sync_and_stays_visible(tmp_path):
    """Root cause 2: a run that hit 1019 stored last_result.at, which counted as the
    last sync: the source was 'fresh' for sync.auto_on_open.hours and 上次同步 showed
    the failed attempt; autosync.js showed it as 「已同步 COROS +0」."""
    async def go():
        s = await _session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake)
        assert (await _sync(s, fake))[-1]["status"] == "complete"
        ok_at = await runner.last_sync_at(s, "coros", 1)
        fake.valid = "web-session"
        ev = await _sync(s, fake)
        assert ev[-1]["error"] == "COROS_AUTH_REQUIRED"
        repo = SettingsRepository(s, 1)
        assert (await repo.get("sync.coros.last_result"))["status"] == "failed"
        assert (await repo.get("sync.coros.last_ok"))["at"] == ok_at.isoformat()
        assert await runner.last_sync_at(s, "coros", 1) == ok_at          # the failure is not a sync
        r = (await AUTH.session_alerts(1, s))["sync"]
        assert r["problem"] == "expired" and r["last_ok_at"] == ok_at.isoformat()
        assert r["last_run"]["status"] == "failed" and r["last_run"]["error"] == "COROS_AUTH_REQUIRED"
        # a server restart forgets the in-memory answer; COROS unreachable: still 登入已過期
        SC.forget()
        r = (await AUTH.session_alerts(1, s))["sync"]
        assert r["login"] == "expired" and r["problem"] == "expired"
        # had the check nothing to go on (unknown), the failed run itself is the banner
        SC._CACHE[("coros", 1)] = (SC.UNKNOWN, time.monotonic())
        r = (await AUTH.session_alerts(1, s))["sync"]
        assert r["login"] == "unknown" and r["problem"] == "failed"
        # logged in again: the auth failure is over (before the next run)
        fake.valid = None
        await _login(s, fake)
        assert (await AUTH.session_alerts(1, s))["sync"]["problem"] is None
    run(go())


def test_a_non_auth_failure_shows_even_with_a_good_login(tmp_path):
    async def go():
        s = await _session(tmp_path)
        await _login(s, Coros(home=US))
        await SettingsRepository(s, 1).set("sync.coros.last_result", {
            "at": "2026-10-04T01:00:00+00:00", "trigger": "open", "status": "failed",
            "downloaded": 0, "checked": 0, "errors": 0, "error": "COROS_API_ERROR"})
        r = (await AUTH.session_alerts(1, s))["sync"]
        assert r["login"] == "ok" and r["problem"] == "failed" and r["last_ok_at"] is None
    run(go())


def test_logged_out_only_after_it_synced_and_nothing_when_sync_is_off(tmp_path):
    async def go():
        s = await _session(tmp_path)
        r = (await AUTH.session_alerts(1, s))["sync"]
        assert r["login"] == "logged_out" and r["problem"] is None      # a new install: no banner
        fake = Coros(home=US)
        await _login(s, fake)
        await _sync(s, fake)
        await AUTH.coros_logout(1, s)
        assert (await AUTH.session_alerts(1, s))["sync"]["problem"] == "logged_out"
        await SettingsRepository(s, 1).set("sync.coros.enabled", False)
        assert (await AUTH.session_alerts(1, s))["sync"]["problem"] is None
    run(go())


def test_the_overview_banner_and_the_auto_sync_show_the_failure():
    js = (STATIC / "session_banner.js").read_text(encoding="utf-8")
    assert "/api/v1/auth/session-alerts" in js and "WKO5SessionBanner" in js
    assert "visibilitychange" in js and "last_ok_at" in js and "session.relogin" in js
    auto = (STATIC / "autosync.js").read_text(encoding="utf-8")
    assert '"failed"' in auto and "WKO5SessionBanner.refresh" in auto
    for page in ("overview.html", "schedule.html"):
        assert "/api/v1/static/session_banner.js" in (STATIC / page).read_text(encoding="utf-8")
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text(encoding="utf-8"))
        for k in ("session.expired_sync", "session.expired_push", "session.expired_push_only",
                  "session.logged_out", "session.sync_failed", "session.relogin", "session.see_settings",
                  "session.last_ok", "session.never_ok", "session.last_failed", "autosync.failed"):
            assert k in cat, (loc, k)
