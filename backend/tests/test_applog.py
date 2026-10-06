"""App log (SP-215, backend/applog.py): redaction, the log file, request
timing / exceptions, step timing and the per-sync summary. Synthetic data,
temp folders only; nothing personal may ever reach a log line."""
import asyncio
import json
import re
import logging
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sse_starlette.sse import EventSourceResponse

from backend import applog
from backend.settings.repository import SettingsRepository
from backend.sync import coros_client, http, runner
from backend.tests.test_sync_e2e import START, FakeCoros, _coros_act, collect, make_session, run


def _fmt(record_msg: str, *args, exc=None) -> str:
    rec = logging.LogRecord("backend.x", logging.WARNING, __file__, 1, record_msg, args,
                            (type(exc), exc, exc.__traceback__) if exc else None)
    return applog.RedactingFormatter(applog.FORMAT).format(rec)


# ---- redaction ---------------------------------------------------------------

@pytest.mark.parametrize("text, secret", [
    ("login failed password=hunter2 for x", "hunter2"),
    ('body {"accessToken": "ctok-abc", "userId": 42}', "ctok-abc"),
    ("headers Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
    ("refresh_token='r-123'", "r-123"),
    ("account me@example.com failed", "me@example.com"),
    ("GET https://api.example/fit?token=abc&userId=7 failed", "token=abc"),
    ("GET https://api.example/fit?token=abc&userId=7 failed", "userId=7"),
    ("feed /share/calendar/Q2hY9a8b7c6d5e4f3g2h1i0jKlMnOpQrStUv.ics", "Q2hY9a8b7c6d5e4f3g2h1i0jKlMnOpQrStUv"),
    ("sealed gAAAAABn1x2y3z4a5b6c7d8e9f0g1h2i3j4k5l6m7n8o9p0==", "gAAAAABn1x2y3z4a5b6c7d8e9f0g1h2i3j4k5l6m7n8o9p0"),
])
def test_redact_blanks_secrets_and_identifiers(text, secret):
    out = applog.redact(text)
    assert secret not in out and "***" in out


def test_redact_keeps_what_debugging_needs():
    keep = ("sync coros (manual) ok in 12.3 s: checked 40, downloaded 3, errors 0 | download 8.1 s; "
            "slowest activity 4.2 s (480567774272323786)")
    assert applog.redact(keep) == keep
    # a long function name in a traceback has no digits: kept
    assert "test_ratchet_new_untranslated_ui_strings" in applog.redact('  File "x.py", in test_ratchet_new_untranslated_ui_strings')
    assert applog.redact("Coros login OK region=eu data_base=https://teameuapi.coros.com") \
        == "Coros login OK region=eu data_base=https://teameuapi.coros.com"


def test_redact_hides_the_home_folder():
    p = str(Path.home() / ".wko5coach" / "fit" / "x.fit")
    assert applog.redact(f"Import failed for {p}") == "Import failed for ~/.wko5coach/fit/x.fit"


def test_formatter_redacts_arguments_and_the_traceback():
    try:
        raise ValueError("bad response for me@example.com: accessToken=sekret123")
    except ValueError as e:
        line = _fmt("sync failed: %s (password=%s)", "x", "pw-plain", exc=e)
    assert "Traceback" in line and "ValueError" in line
    for secret in ("me@example.com", "sekret123", "pw-plain"):
        assert secret not in line


# ---- setup -------------------------------------------------------------------

def _drop_handlers():
    root = logging.getLogger("backend")
    for h in [h for h in root.handlers if getattr(h, "_trc", None)]:
        root.removeHandler(h)
        h.close()


@pytest.fixture
def clean_handlers():
    root = logging.getLogger("backend")
    level = root.level
    _drop_handlers()
    yield root
    _drop_handlers()
    root.setLevel(level)


def test_setup_writes_the_log_file_once_and_redacted(tmp_path, monkeypatch, clean_handlers):
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path))
    monkeypatch.delenv("WKO5COACH_LOG_FILE", raising=False)
    applog.setup()
    applog.setup()                                        # idempotent: no second pair of handlers
    kinds = sorted(getattr(h, "_trc", "") for h in clean_handlers.handlers if getattr(h, "_trc", None))
    assert kinds == ["file", "stderr"]
    logging.getLogger("backend.sync.runner").warning("sync failed for %s token=%s", "me@example.com", "t0k")
    for h in clean_handlers.handlers:
        h.flush()
    text = (tmp_path / "logs" / "app.log").read_text("utf-8")
    assert "sync failed for" in text and "me@example.com" not in text and "t0k" not in text
    # a changed data folder moves the file
    other = tmp_path / "other"
    monkeypatch.setenv("WKO5COACH_HOME", str(other))
    applog.setup()
    files = [h for h in clean_handlers.handlers if getattr(h, "_trc", None) == "file"]
    assert len(files) == 1 and Path(files[0].baseFilename) == other / "logs" / "app.log"


def test_setup_without_a_file(tmp_path, monkeypatch, clean_handlers):
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path))
    monkeypatch.setenv("WKO5COACH_LOG_FILE", "0")
    monkeypatch.setenv("WKO5COACH_LOG_LEVEL", "warning")
    applog.setup()
    assert [getattr(h, "_trc", None) for h in clean_handlers.handlers if getattr(h, "_trc", None)] == ["stderr"]
    assert clean_handlers.level == logging.WARNING and not (tmp_path / "logs").exists()


# ---- timing ------------------------------------------------------------------

def test_timed_info_warning_and_failure(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="backend")
    with applog.timed("dataset build", source="coros"):
        pass
    t = iter([0.0, 25.0])
    monkeypatch.setattr(applog.time, "perf_counter", lambda: next(t))
    with applog.timed("auto plan run", trigger="sync:coros"):
        pass
    monkeypatch.undo()
    with pytest.raises(RuntimeError):
        with applog.timed("calibration run"):
            raise RuntimeError("boom")
    recs = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "backend.applog"]
    assert recs[0][0] == logging.INFO and recs[0][1].startswith("dataset build took") and "source=coros" in recs[0][1]
    assert recs[1] == (logging.WARNING, "slow: auto plan run took 25.0 s trigger=sync:coros")
    assert recs[2][0] == logging.WARNING and recs[2][1].startswith("calibration run failed after")


# ---- requests ----------------------------------------------------------------

def _app(slow_s=None):
    app = FastAPI()

    @app.get("/share/calendar/{token}.ics")
    def feed(token: str):
        return {"ok": True}

    @app.get("/boom/{wid}")
    def boom(wid: int):
        raise RuntimeError(f"broken for user me@example.com wid={wid}")

    @app.get("/stream")
    def stream():
        async def gen():
            yield {"data": "x"}
        return EventSourceResponse(gen())

    app.add_middleware(applog.RequestLogMiddleware, slow_s=slow_s)
    return app


def test_request_log_uses_the_route_template_never_the_path(caplog):
    caplog.set_level(logging.INFO, logger="backend")
    c = TestClient(_app(slow_s=0.0))
    assert c.get("/share/calendar/SECRETtoken123.ics?lang=en&k=v").status_code == 200
    line = caplog.text
    assert "slow request: GET /share/calendar/{token}.ics -> 200" in line
    assert "SECRETtoken123" not in line and "k=v" not in line
    caplog.clear()
    assert c.get("/nowhere?x=1").status_code == 404
    assert "(no route)" in caplog.text and "nowhere" not in caplog.text


def test_request_log_skips_event_streams_and_fast_requests(caplog):
    caplog.set_level(logging.INFO, logger="backend")
    c = TestClient(_app(slow_s=0.0))
    with c.stream("GET", "/stream") as r:
        r.read()
    assert "slow request" not in caplog.text
    c = TestClient(_app())                                   # the default 3 s
    c.get("/share/calendar/x.ics")
    assert "slow request" not in caplog.text


def test_unhandled_exception_logged_with_traceback_and_redacted(caplog):
    caplog.set_level(logging.INFO, logger="backend")
    c = TestClient(_app(), raise_server_exceptions=False)
    assert c.get("/boom/7").status_code == 500
    recs = [r for r in caplog.records if r.name == "backend.applog" and r.levelno == logging.ERROR]
    assert len(recs) == 1 and recs[0].exc_info and "unhandled error: GET /boom/{wid}" in recs[0].getMessage()
    line = applog.RedactingFormatter(applog.FORMAT).format(recs[0])
    assert "RuntimeError" in line and "Traceback" in line and "me@example.com" not in line


def test_app_installs_the_request_log():
    from backend.main import build_app
    app = build_app(demo=False)
    assert any(m.cls is applog.RequestLogMiddleware for m in app.user_middleware)
    # outermost of the app's own middleware: tenancy and language time count too
    assert app.user_middleware[0].cls is applog.RequestLogMiddleware


# ---- sync summary ------------------------------------------------------------

def test_sync_clock_charges_each_gap_to_its_step(monkeypatch):
    now = iter([0.0, 1.0, 1.5, 1.6, 5.0, 5.1, 9.0, 9.5, 10.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(now))
    c = runner.SyncClock()                                                     # t0 = 0
    c.event({"status": "started"})                                             # 1.0 list
    c.event({"status": "checking", "activity_id": "A"})                        # 0.5 list
    c.event({"status": "skipped", "activity_id": "A"})                         # 0.1 check
    c.event({"status": "downloaded", "activity_id": "B", "secs": {"download": 2.4}})   # 3.4: 2.4 + 1.0
    c.event({"status": "checking", "activity_id": "C"})                        # 0.1 list
    c.event({"status": "error", "activity_id": "C", "error": "x"})             # 3.9 download (no split)
    c.event({"status": "complete"})                                            # 0.5 finish
    s = c.summary()                                                            # total at 10.0
    assert s == {"list": 1.6, "check": 0.1, "download": 6.3, "import": 1.0, "finish": 0.5,
                 "after": 0.0, "total": 10.0}
    assert c.slowest == (pytest.approx(3.9), "C")


def test_sync_run_logs_one_summary_and_stores_its_seconds(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="backend")

    async def go():
        s = await make_session(tmp_path)
        acts = [_coros_act("A1", START), _coros_act("A2", START + timedelta(days=1))]
        with http.use_transport(httpx.MockTransport(FakeCoros(acts))):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(runner.stream(s, "coros", 1))
        assert all(isinstance(e["secs"]["download"], float) for e in ev if e.get("status") == "downloaded")
        return await SettingsRepository(s, 1).get("sync.coros.last_result")
    res = run(go())
    assert res["status"] == "ok" and set(res["secs"]) == set(runner.SyncClock.STEPS) | {"total"}
    assert res["secs"]["total"] >= res["secs"]["download"] >= 0
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("sync coros")]
    assert len(lines) == 1 and lines[0].startswith("sync coros (manual) ok in ")
    assert "checked 2, downloaded 2, errors 0" in lines[0] and "slowest activity" in lines[0]


def test_sync_failure_is_logged_with_its_traceback(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="backend")

    async def broken(*a, **k):
        yield {"status": "started"}
        raise OSError("disk full at https://x.example/p?token=abc")
    monkeypatch.setattr(runner, "_client_stream", lambda *a, **k: broken())

    async def go():
        s = await make_session(tmp_path)
        return await collect(runner.stream(s, "coros", 1))
    ev = run(go())
    assert ev[-1]["error"] == "SYNC_FAILED"
    failed = [r for r in caplog.records if r.getMessage() == "coros sync failed: OSError"]
    assert failed and failed[0].exc_info
    summary = [r for r in caplog.records if r.getMessage().startswith("sync coros (manual) failed")]
    assert summary and summary[0].levelno == logging.WARNING
    text = "\n".join(applog.RedactingFormatter(applog.FORMAT).format(r) for r in caplog.records)
    assert "token=abc" not in text


# ---- nothing personal --------------------------------------------------------

class _ProfileCoros(FakeCoros):
    """A login answer with the account's body data, as COROS sends it."""

    def __call__(self, request):
        if request.url.path == "/account/login":
            return httpx.Response(200, json={"result": "0000", "data": {
                "accessToken": "ctok-9f8e7d6c5b4a39281706f5e4d3c2b1a0", "userId": 4242424242,
                "weight": 61.5, "maxHr": 193, "rhr": 47,
                "zoneData": {"ftp": 287, "lthr": 171, "maxHr": 193, "rhr": 47}}})
        if request.url.path == "/account/query":
            return httpx.Response(200, json={"result": "0000", "data": {
                "zoneData": {"maxHr": 194, "rhr": 46, "lthr": 172}}})
        return super().__call__(request)


def test_login_and_sync_log_no_personal_data(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(_ProfileCoros([_coros_act("A1", START)]))):
            await coros_client.login("runner@example.com", "pw-Secret-1", s, 1)
            await collect(runner.stream(s, "coros", 1))
    run(go())
    # the app's own messages: what the app log writes (its handlers sit on the `backend` logger;
    # caplog.text also holds source line numbers and the SQL driver's debug lines)
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("backend"))
    assert "Coros profile stored: FTP yes, LTHR yes, weight yes" in text and "Coros HR profile updated" in text
    for personal in ("runner@example.com", "pw-Secret-1", "ctok-9f8e7d6c5b4a39281706f5e4d3c2b1a0",
                     "4242424242", "61.5", "193", "194", "171", "172", "287", "47", "46"):
        assert not re.search(rf"(?<![\d.]){re.escape(personal)}(?![\d.])", text), personal
