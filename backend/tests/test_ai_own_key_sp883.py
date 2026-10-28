"""SP-883 AI 問答 on a member's own API key (backend/ai/config.py, qa.py, api/ai.py; SP-306 Q1-1 A).

驗收: a member pastes their own key on the settings page; AI 問答 then calls the model with *their*
key and counts the calls under *their* name; the key is sealed at rest and only its last 4
characters are ever shown.
Also (SP-883 Q1): every signed-in member (free too, Q1-1 A); only while the server's
TRC_AI_MEMBER_KEYS is on (Q1-2 A, default off); a member never gets the owner's env key; the key is
never logged, exported or backed up; 「測試」 checks it without billing; a 400 for the credit
balance says so; a coach viewing a student cannot reach any of it.

No real call anywhere: the Messages API POST (qa._post) and the key test's GET (qa._get) are fakes
(conftest._no_live_ai refuses both otherwise). Every key is a fake sentinel."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from backend.ai import config as C
from backend.ai import context as X
from backend.ai import qa as Q
from backend.tests.test_ai_qa_sp785 import GOOD, STATUS, WEEKS, FakeModel
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    OWNER_EMAIL, app, browser, csrf, env, sign_in, sign_up, started)

MEMBER_KEY = "sk-ant-" + "example-SP883-member-own-key-xxxxxxxxxxxxxxMINE"   # fake, split so scanners see it is not a key
OTHER_KEY = "sk-ant-" + "example-SP883-another-member-key-xxxxxxxxxxOTHR"
OWNER_ENV_KEY = "sk-ant-" + "example-SP883-owner-env-key-xxxxxxxxxxxxxxOWNR"


class FakeModels:
    """GET /v1/models (the key test): records each request, answers `status`."""

    def __init__(self, status: int = 200, body: bytes = b'{"data": []}'):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": headers})
        return self.status, self.body


@pytest.fixture
def site(app, monkeypatch):  # noqa: F811
    """Sign-in mode, members' own keys switched on; the status / weekly totals fixed."""
    monkeypatch.setenv(C.ENV_MEMBERS, "1")
    monkeypatch.setattr(X, "_status", lambda: (json.loads(json.dumps(STATUS)), object()))
    monkeypatch.setattr(X, "_weeks", lambda ds: WEEKS)
    return app


def _member(app, sub="g-m1", email="m1@example.com", ip="198.51.100.41"):  # noqa: F811
    cl = browser(app, ip)
    sign_up(cl, sub, email)
    cl.h = {"X-TRC-CSRF": csrf(cl)}
    return cl


def _server_root() -> Path:
    from backend import accounts
    return Path(os.environ[accounts.ENV_SERVER_ROOT])


def _files(name: str) -> list[Path]:
    """Every <name> under the server root (the members' folders), not the owner's."""
    return sorted(_server_root().rglob(name))


def _model(monkeypatch, reply=GOOD, status=200) -> FakeModel:
    m = FakeModel(reply, status)
    monkeypatch.setattr(Q, "_post", m)
    return m


# ------------------------------------------------------------------------------------- 驗收 1

def test_a_member_s_own_key_is_used_counted_under_their_name_sealed_and_masked(site, monkeypatch):
    m = _model(monkeypatch)
    monkeypatch.setenv(C.ENV_KEY, OWNER_ENV_KEY)                       # the owner's key on the server
    mem = _member(site)
    s = mem.get("/api/v1/ai/settings").json()
    assert s["who"] == "member" and s["key"] == {"source": "none", "masked": None}   # never the server's key
    # the owner 2026-10-28: 「問 AI」 shows before a key too; its dialog asks for the key (own, key false)
    assert s["enabled"] is True
    assert mem.get("/api/v1/session").json()["ai"] == {"on": True, "key": False, "own": True}
    r = mem.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=mem.h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AI_NO_KEY" and not m.calls

    r = mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)
    assert r.status_code == 200 and MEMBER_KEY not in r.text
    assert r.json()["key"] == {"source": "saved", "masked": "…MINE"} and r.json()["enabled"] is True
    assert mem.get("/api/v1/session").json()["ai"] == {"on": True, "key": True, "own": True}
    # sealed in the member's own folder; the owner's folder untouched
    [f] = _files(C.FILE_NAME)
    raw = f.read_text("utf-8")
    assert MEMBER_KEY not in raw and json.loads(raw)["api_key"].startswith("enc:v1:")
    assert not C.owner_path().exists()

    a = mem.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=mem.h)
    assert a.status_code == 200, a.text
    assert a.json()["used_today"] == 1 and a.json()["parts"][0]["type"] == "rule"
    assert m.calls[0]["headers"]["x-api-key"] == MEMBER_KEY            # their key, not the owner's
    # counted under the member's name: their ai_usage.json; the owner's count still 0
    [u] = _files(C.USAGE_NAME)
    assert u.parent == f.parent and json.loads(u.read_text("utf-8"))["n"] == 1
    owner = browser(site)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    o = owner.get("/api/v1/ai/settings").json()
    assert o["who"] == "owner" and o["used_today"] == 0 and o["key"]["source"] == "env"


def test_two_members_each_their_own_key_and_count(site, monkeypatch):
    monkeypatch.setenv(C.ENV_LIMIT, "1")
    m = _model(monkeypatch)
    a, b = _member(site), _member(site, "g-m2", "m2@example.com", "198.51.100.42")
    a.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=a.h)
    b.put("/api/v1/ai/settings", json={"api_key": OTHER_KEY}, headers=b.h)
    assert a.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=a.h).status_code == 200
    # a's limit reached; b still has theirs
    r = a.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}, headers=a.h)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "AI_DAILY_LIMIT"
    assert b.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=b.h).status_code == 200
    assert [c["headers"]["x-api-key"] for c in m.calls] == [MEMBER_KEY, OTHER_KEY]
    # b's settings show b's last 4, never a's
    assert b.get("/api/v1/ai/settings").json()["key"]["masked"] == "…OTHR"
    assert len(_files(C.FILE_NAME)) == 2 and len(_files(C.USAGE_NAME)) == 2


def test_a_member_s_call_is_its_own_purpose_in_the_spend_line(site, monkeypatch, tmp_path):
    from backend import ops_daily
    monkeypatch.setattr(ops_daily, "folder", lambda: tmp_path / "ops")
    _model(monkeypatch)
    mem = _member(site)
    mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)
    mem.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=mem.h)
    line = (tmp_path / "ops" / "api_calls.lp").read_text("utf-8")
    assert "purpose=ask_own_key" in line and MEMBER_KEY not in line


# ------------------------------------------------------------------------- the switch, who may

def test_the_server_switch_is_off_by_default_and_then_a_member_has_nothing(app, monkeypatch):  # noqa: F811
    monkeypatch.delenv(C.ENV_MEMBERS, raising=False)
    m = _model(monkeypatch)
    mem = _member(app)
    for r in (mem.get("/api/v1/ai/settings"),
              mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h),
              mem.post("/api/v1/ai/settings/test", json={}, headers=mem.h),
              mem.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=mem.h)):
        assert r.status_code == 403 and r.json()["detail"]["code"] == "AI_OWNER_ONLY", r.text
    assert "ai" not in mem.get("/api/v1/session").json() and not m.calls and not _files(C.FILE_NAME)
    for v in ("", "0", "off", "no"):
        monkeypatch.setenv(C.ENV_MEMBERS, v)
        assert not C.members_on()
    for v in ("1", "true", "ON", "yes"):
        monkeypatch.setenv(C.ENV_MEMBERS, v)
        assert C.members_on()


def test_a_member_never_gets_the_owner_s_key_and_the_demo_none(monkeypatch, tmp_path):
    from backend import tenancy
    monkeypatch.setenv(C.ENV_MEMBERS, "1")
    monkeypatch.setenv(C.ENV_KEY, OWNER_ENV_KEY)
    home = tmp_path / "u5"
    user = tenancy.Tenant(id="u5", kind=tenancy.USER, root=home, shared=home, caps=tenancy.USER_CAPS)
    with tenancy.use(user):
        assert C.allowed() and C.is_member()
        assert C.api_key() is None and C.key_source() == "none" and C.masked() is None
        assert C.path() == home / C.FILE_NAME and C.usage_path() == home / C.USAGE_NAME
    demo = tenancy.Tenant(id="demo-x", kind=tenancy.DEMO_SANDBOX, root=home, shared=home, caps=tenancy.DEMO_CAPS)
    no_ai = tenancy.Tenant(id="u6", kind=tenancy.USER, root=home, shared=home, caps=tenancy.USER_CAPS - {"ai"})
    for t in (demo, no_ai):
        with tenancy.use(t):
            assert not C.allowed() and C.api_key() is None
            with pytest.raises(C.OwnerOnly):
                C.save_key(MEMBER_KEY)
    # the demo instance: never
    monkeypatch.setenv("WKO5COACH_MODE", "demo")
    with tenancy.use(user):
        assert not C.allowed()


def test_a_coach_viewing_a_student_cannot_reach_ai():
    """代看 (SP-791): the middleware holds a viewing session to viewing.check — /api/v1/ai/* is
    neither a read it opens nor a write it allows, whatever the coach's switches."""
    from backend.accounts import coaching as CO, viewing as VW
    every = {p: True for p in CO.PERMS}
    for method, path in (("GET", "/api/v1/ai/settings"), ("PUT", "/api/v1/ai/settings"),
                         ("POST", "/api/v1/ai/ask"), ("POST", "/api/v1/ai/context"),
                         ("POST", "/api/v1/ai/settings/test")):
        assert not VW.check(method, path, every).ok, (method, path)
    # and the viewing tenant never has the `ai` cap: is_member() is False even past the middleware
    assert "ai" not in VW.caps_for(every)


# --------------------------------------------------------------------- never logged / exported

def test_a_member_s_key_is_never_logged_exported_or_backed_up(site, monkeypatch, caplog):
    from backend import data_registry as R
    _model(monkeypatch)
    caplog.set_level(logging.DEBUG)
    mem = _member(site)
    mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)
    mem.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=mem.h)
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert MEMBER_KEY not in logged and "訓練狀態" not in logged
    [f] = _files(C.FILE_NAME)
    # SECRET in the registry: not in 下載我的資料, not in a backup
    e = R.classify(C.FILE_NAME)
    assert e is not None and e.cls == R.SECRET and e.backup == R.NEVER
    assert C.FILE_NAME not in [x.pattern for x in R.export_entries()[1]]
    assert R.classify(C.USAGE_NAME).cls == R.DERIVED
    # cleared: gone from the file
    r = mem.put("/api/v1/ai/settings", json={"api_key": ""}, headers=mem.h)
    assert r.json()["key"] == {"source": "none", "masked": None}
    assert "api_key" not in json.loads(f.read_text("utf-8"))


# ------------------------------------------------------------------------------------ 「測試」

def test_the_key_test_checks_without_billing_and_is_capped(site, monkeypatch):
    m = _model(monkeypatch)
    g = FakeModels()
    monkeypatch.setattr(Q, "_get", g)
    mem = _member(site)
    test = lambda: mem.post("/api/v1/ai/settings/test", json={}, headers=mem.h)  # noqa: E731
    r = test()
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AI_NO_KEY" and not g.calls
    mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)
    r = test()
    assert r.status_code == 200 and r.json() == {"ok": True} and MEMBER_KEY not in r.text
    assert g.calls[0]["url"] == Q.API_MODELS and g.calls[0]["headers"]["x-api-key"] == MEMBER_KEY
    assert not m.calls                                                 # no Messages call: nothing billed
    assert mem.get("/api/v1/ai/settings").json()["used_today"] == 0     # not a question
    for status, body, code in ((401, b"{}", "AI_KEY_INVALID"), (529, b"{}", "AI_BUSY"), (500, b"{}", "AI_UPSTREAM"),
                               (400, b'{"error":{"message":"Your credit balance is too low"}}', "AI_NO_CREDIT")):
        g.status, g.body = status, body
        assert test().json()["detail"]["code"] == code, status
    g.status = 200
    for _ in range(C.KEY_TESTS_PER_DAY - 5):
        assert test().status_code == 200
    n = len(g.calls)
    r = test()
    assert r.status_code == 429 and r.json()["detail"]["code"] == "AI_TEST_LIMIT" and len(g.calls) == n


def test_the_owner_can_test_too(site, monkeypatch):
    g = FakeModels()
    monkeypatch.setattr(Q, "_get", g)
    monkeypatch.setenv(C.ENV_KEY, OWNER_ENV_KEY)
    owner = browser(site)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    r = owner.post("/api/v1/ai/settings/test", json={}, headers={"X-TRC-CSRF": csrf(owner)})
    assert r.status_code == 200 and g.calls[0]["headers"]["x-api-key"] == OWNER_ENV_KEY


def test_a_key_without_credit_says_so_and_is_not_counted(site, monkeypatch):
    m = _model(monkeypatch, status=400)
    monkeypatch.setattr(m, "reply", "x")
    mem = _member(site)
    mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)

    def no_credit(url, headers, body, timeout):
        m.calls.append(headers)
        return 400, b'{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}}'
    monkeypatch.setattr(Q, "_post", no_credit)
    r = mem.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=mem.h)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "AI_NO_CREDIT"
    assert mem.get("/api/v1/ai/settings").json()["used_today"] == 0


# ------------------------------------------------------------- the log mask reads no tenant

def test_the_log_mask_reads_no_tenant_and_still_blanks_a_member_s_key(site, monkeypatch):
    """A log line on a public path (the 首頁 estimate, test_intro_sp637) must not read any tenant:
    the mask is the owner's file and the member keys unsealed in *this* request — never a lookup of
    the requesting member's folder per log line. Inside the member's own request their key is
    masked; it does not outlive that request."""
    import contextvars
    from backend import applog, tenancy
    from backend.settings import env_secrets
    seen = []

    def model(url, headers, body, timeout):           # runs inside the member's request
        seen.append({"in_mask": MEMBER_KEY in env_secrets.values(),
                     "line": applog.redact(f"upstream said x-api-key={MEMBER_KEY}")})
        return FakeModel(GOOD)(url, headers, body, timeout)
    monkeypatch.setattr(Q, "_post", model)
    mem = _member(site)
    mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)
    assert mem.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=mem.h).status_code == 200
    assert seen == [{"in_mask": True, "line": seen[0]["line"]}] and MEMBER_KEY not in seen[0]["line"]
    # another request (a fresh context): the member's key is not carried over
    assert MEMBER_KEY not in contextvars.Context().run(env_secrets.values)

    # a public page's request: no tenant at all — the mask must not ask for one
    def no_tenant():
        pytest.fail("the log mask read a tenant")
    monkeypatch.setattr(tenancy, "current", no_tenant, raising=False)
    assert contextvars.Context().run(lambda: applog.redact("GET /api/v1/intro/estimate status=200")) \
        == "GET /api/v1/intro/estimate status=200"
    assert contextvars.Context().run(C.redact_values) == set()
