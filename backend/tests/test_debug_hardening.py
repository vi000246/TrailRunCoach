"""
Debug API hardening after the SP-371 security review: the token in its own header next to a
password proxy's Authorization (H1); a valid token is never refused by the IP block (M1); failed
authentications aggregated, 429s never stored (M2); scrub / export allow-list (M3, the sentinel
test is test_debug_api.test_no_answer_carries_a_credential_or_a_position); per-tenant limit and the
concurrency gate (M4); same-origin settings writes, PIN logging, no OpenAPI listing, read:plan /
read:gps scopes, bounded / strict input with 400 not 500, masked audit query, the sync error count
(L1–L9). Synthetic data, in-memory DB (debug_fixtures.debug_env).
"""
import logging
from pathlib import Path

from sqlalchemy import func, select

from backend import debug_auth as DA
from backend.api import debug as debug_api
from backend.db.models import DebugAudit, DebugAuthFailure
from backend.engine import debug_view as DV
from backend.security import ratelimit as RL
from backend.settings.repository import SettingsRepository
from backend.tests.debug_fixtures import ADMIN, ALL_SCOPES, DBG, TEST_PIN, bearer, debug_env, enable, make_token
from backend.tests.test_plan_store import run

REPO = Path(__file__).resolve().parents[2]


def _count(db, model) -> int:
    return run(db.execute(select(func.count(model.id)))).scalar_one()


# ---------------------------------------------------------------- H1
def test_the_token_header_works_next_to_a_proxy_basic_auth(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ["read:sync"])
        h = {"Authorization": "Basic dXNlcjpwcm94eS1wYXNzd29yZA==", "X-TRC-Debug-Token": tok}
        assert e.c.get(f"{DBG}/sync?lines=1", headers=h).status_code == 200
        assert e.c.get(f"{DBG}/sync?lines=1", headers=bearer(tok)).status_code == 200      # still supported
        r = e.c.get(f"{DBG}/sync", headers={"Authorization": "Basic dXNlcjpwcm94eS1wYXNzd29yZA=="})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_MISSING"
        assert e.c.get(f"{DBG}/sync", headers={"X-TRC-Debug-Token": "trcd_" + "x" * 40}).status_code == 401


# ---------------------------------------------------------------- M1 / M2
def test_a_valid_token_is_never_refused_by_the_ip_block_and_failures_are_aggregated(monkeypatch, tmp_path):
    monkeypatch.setattr(DA, "FAIL_BUCKET", RL.Buckets(rate=3, per=600))
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ["read:sync"])
        bad = {"X-TRC-Debug-Token": "trcd_" + "j" * 40}
        codes = [e.c.get(f"{DBG}/sync", headers=bad).status_code for _ in range(10)]
        assert codes[:4] == [401] * 4 and set(codes[4:]) == {429}             # the IP's failures are refused
        r = e.c.get(f"{DBG}/sync?lines=1", headers=bearer(tok))               # same IP, a valid token: fine
        assert r.status_code == 200
        assert e.c.get(f"{DBG}/sync").status_code == 429                       # a missing token is a failure too
        # nothing per failed request: one aggregated row (IP, hour, code) with its count, no audit rows
        rows = run(e.db.execute(select(DebugAuthFailure))).scalars().all()
        assert [(r.code, r.count) for r in rows] == [("TOKEN_INVALID", 4)]
        assert _count(e.db, DebugAudit) == 1
        st = e.c.get(ADMIN).json()
        assert st["failures"][0]["count"] == 4 and st["dropped"]["BLOCKED"] == 7
        assert [a["status"] for a in st["audit"]] == [200]


def test_the_per_token_and_per_tenant_limits_store_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(DA, "TOKEN_BUCKET", RL.Buckets(rate=3, per=60))
    monkeypatch.setattr(DA, "TENANT_BUCKET", RL.Buckets(rate=4, per=60))
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        t1, t2 = make_token(e, ["read:sync"], name="a"), make_token(e, ["read:sync"], name="b")
        codes = [e.c.get(f"{DBG}/sync?lines=1", headers=bearer(t1)).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        assert e.c.get(f"{DBG}/sync?lines=1", headers=bearer(t2)).status_code == 200      # tenant: 4th call
        r = e.c.get(f"{DBG}/sync?lines=1", headers=bearer(t2))                            # tenant: 5th
        assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED"
        assert _count(e.db, DebugAudit) == 4 and _count(e.db, DebugAuthFailure) == 0
        assert e.c.get(ADMIN).json()["dropped"]["RATE_LIMITED"] == 2


def test_the_concurrency_gate_answers_503_when_busy(monkeypatch, tmp_path):
    class Busy:
        n, wait_s = 2, 0.0

        async def acquire(self):
            return False

        def release(self):
            raise AssertionError("never acquired")
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ["read:sync"])
        monkeypatch.setattr(DA, "CALL_GATE", Busy())
        r = e.c.get(f"{DBG}/sync", headers=bearer(tok))
        assert r.status_code == 503 and r.json()["detail"]["code"] == "BUSY"


def test_the_audit_log_keeps_the_newest_rows_and_masks_the_query(monkeypatch, tmp_path):
    monkeypatch.setattr(DA, "AUDIT_KEEP", 5)
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES, name="claude")
        for _ in range(8):
            e.c.get(f"{DBG}/sync?lines=1", headers=bearer(tok))
        assert _count(e.db, DebugAudit) == 5
        e.c.get(f"{DBG}/activity?label=Ab12Cd34Ef56Gh78Ij90Kl", headers=bearer(tok))
        a = e.c.get(ADMIN).json()["audit"][0]
        assert a["token"] == "claude" and a["query"] == "label=***" and a["new_ip"] is False
        assert e.c.get(ADMIN).json()["tokens"][0]["new_ip_at"]


# ---------------------------------------------------------------- L1 / L2 / L3
def test_settings_writes_refuse_a_cross_site_request(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        cross = {"Sec-Fetch-Site": "cross-site"}
        assert e.c.put(ADMIN, json={"enabled": True}, headers=cross).status_code == 403
        assert e.c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN}, headers=cross).status_code == 403
        assert e.c.post(f"{ADMIN}/tokens/revoke-all", headers=cross).status_code == 403
        assert e.c.delete(f"{ADMIN}/tokens/1", headers=cross).status_code == 403
        assert e.c.post(f"{ADMIN}/tokens/revoke-all", headers={"Origin": "https://evil.example"}).status_code == 403
        assert e.c.post(f"{ADMIN}/tokens/revoke-all", headers={"Origin": "null"}).status_code == 403
        assert e.c.put(ADMIN, json={"enabled": True}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
        assert e.c.post(f"{ADMIN}/tokens/revoke-all", headers={"Origin": "http://testserver"}).status_code == 200
        assert e.c.get(ADMIN, headers=cross).status_code == 200            # a read is not a write
        # no JSON body without a Content-Type (FastAPI strict_content_type): not parsed as JSON
        r = e.c.post(f"{ADMIN}/tokens", content=b'{"pin": "' + TEST_PIN.encode() + b'"}')
        assert r.status_code in (400, 415, 422) and "token" not in r.text


def test_requirements_pin_a_fastapi_with_strict_content_type():
    import re
    req = (REPO / "requirements.txt").read_text("utf-8")
    m = re.search(r"^fastapi>=(\d+)\.(\d+)", req, re.M)
    assert m and (int(m.group(1)), int(m.group(2))) >= (0, 142)


def test_wrong_pins_are_logged_and_counted(monkeypatch, tmp_path, caplog):
    with debug_env(monkeypatch, tmp_path) as e:
        with caplog.at_level(logging.WARNING, logger="backend.debug_auth"):
            for _ in range(DA.PIN_MAX_FAILS):
                e.c.post(f"{ADMIN}/tokens", json={"pin": "wrong-wrong"})
        text = caplog.text
        assert "wrong PIN" in text and "PIN locked" in text and "wrong-wrong" not in text and TEST_PIN not in text
        pin = e.c.get(ADMIN).json()["pin"]
        assert pin == {"wrong_total": DA.PIN_MAX_FAILS, "wrong_in_a_row": DA.PIN_MAX_FAILS, "locks": 1}


def test_the_debug_routes_are_not_in_the_openapi_schema():
    from backend.main import build_app
    paths = build_app(demo=False).openapi()["paths"]
    assert not [p for p in paths if p.startswith((DBG, ADMIN))]


# ---------------------------------------------------------------- L4 / L5 / L6 / L9
def test_gps_needs_its_own_scope(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ["read:activity", "read:plan", "read:sync"])
        r = e.c.get(f"{DBG}/activity?date=2026-10-01&gps=1", headers=bearer(tok))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE_GPS"
        assert "read:gps" not in DA.DEFAULT_SCOPES
        g = make_token(e, ["read:activity", "read:gps"])
        assert e.c.get(f"{DBG}/activity?date=2026-10-01&gps=1", headers=bearer(g)).status_code == 200


def test_bad_input_is_a_400_never_a_500_and_errors_are_audited(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES)
        for u in ("/activity?id=0", f"/activity?id={2 ** 63}", "/activity?id=abc", "/activity?date=2026-10-01&every=0",
                  "/activity?date=1900-01-01", "/activity?date=2026-02-30", "/activity?date=2026-10-01x",
                  "/activity?date=20261001", "/day?date=2101-01-01", "/plan?from=2026-10-10&to=2026-10-01",
                  "/thresholds?date=2026-1-5", "/sync?lines=0", "/sync?lines=999999", "/activity?label=" + "x" * 200):
            r = e.c.get(DBG + u, headers=bearer(tok))
            assert r.status_code == 400, (u, r.status_code, r.text[:200])
        n = _count(e.db, DebugAudit)
        assert n == 14
        # an unexpected error: a JSON 500 with the type only, audited
        monkeypatch.setattr(DV, "log_tail", lambda n: (_ for _ in ()).throw(RuntimeError("secret detail")))
        r = e.c.get(f"{DBG}/sync", headers=bearer(tok))
        assert r.status_code == 500 and r.json() == {"detail": {"code": "INTERNAL", "error": "RuntimeError"}}
        assert _count(e.db, DebugAudit) == n + 1


def test_parse_day_is_strict_and_bounded():
    assert debug_api.parse_day("2026-10-05", "d").isoformat() == "2026-10-05"
    assert debug_api.parse_day("", "d") is None
    for bad in ("2026-10-05T00:00", "2026-10-5", "05-10-2026", "1969-12-31", "2101-01-01", "２０２６-10-05"):
        try:
            debug_api.parse_day(bad, "d")
        except ValueError:
            continue
        raise AssertionError(bad)


def test_sync_failures_read_the_error_count(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ["read:sync"])
        run(SettingsRepository(e.db).set("sync.coros.last_result", {
            "at": "2026-10-05T08:00:00", "trigger": "auto", "status": "partial", "downloaded": 3, "errors": 2,
            "error": None}))
        run(SettingsRepository(e.db).set("sync.trainingpeaks.last_result", {
            "at": "2026-10-05T08:00:00", "status": "error", "errors": 0, "error": "login expired"}))
        run(e.db.commit())
        f = e.c.get(f"{DBG}/sync?lines=1", headers=bearer(tok)).json()["failures"]
        assert [(x["source"], x["error_count"], x["error"]) for x in f] == [
            ("coros", 2, None), ("trainingpeaks", 0, "login expired")]


# ---------------------------------------------------------------- M3 (unit)
def test_scrub_widened_keys_values_and_coordinates():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl"
    x = {"state": "ok", "key": "2026-10-01T06:00", "tokens": 2, "stamp": "x", "track": "z5",
         "authorization": "Basic x", "auth": "x", "id_token": "x", "api_token": "x", "feed_token": "x",
         "share_id": "x", "session_cookie": "x", "csrf": "x", "signature": "x", "private_key": "x", "otp": "x",
         "coros_email": "a@b.c", "tp_username": "u", "coros_user_id": "1", "data_dir": "d", "file_path": "f",
         "start_lng": 121.5, "end_lng": 121.6, "start_latitude": 25.0, "latlng": [25, 121], "coords": [[1, 2]],
         "points": [[1, 2]], "bounds": [1, 2, 3, 4], "center": [25, 121],
         "note": f"jwt {jwt}", "plain_value": jwt, "opaque": "Zx9" * 8, "mail": "write to me@example.com",
         "url": "https://maps.example/x?latitude=25.03&longitude=121.56", "text": "lat=25.0331 lon: 121.5654",
         "pair": "at 25.03312,121.56541", "home": str(Path.home() / "x.fit")}
    out = DV.scrub(x)
    assert set(out) == {"state", "key", "tokens", "stamp", "track", "note", "plain_value", "opaque", "mail", "url",
                        "text", "pair", "home"}
    assert out["plain_value"] == DV.REDACTED and jwt not in out["note"] and out["opaque"] == DV.REDACTED
    assert "me@example.com" not in out["mail"] and "25.03" not in out["url"] and "121.56" not in out["url"]
    assert "25.0331" not in out["text"] and "121.5654" not in out["text"] and "25.03312" not in out["pair"]
    assert str(Path.home()) not in out["home"]
    assert DV.scrub({"text": "lat=25.0331"}, gps=True)["text"] == "lat=25.0331"


def test_export_is_an_allow_list():
    assert DV.exportable("plan.prefs.runs_per_week", 5) == "plan_prefs"
    assert DV.exportable("charts.wko5_views_dir", None) is None
    assert DV.exportable("charts.map.basemap", "C:\\Users\\me\\maps") is None             # a path-like value
    assert DV.exportable("charts.map.basemap", "/home/me/maps") is None
    assert DV.exportable("some.future.setting", 1) is None                                 # not placed in a block
    assert DV.exportable("sync.coros.last_result", {}) is None and DV.exportable("athlete.calib_last_run", "x") is None
    assert DV.exportable("backup.dir", None) is None and DV.exportable("plan.calendar", None) is None
