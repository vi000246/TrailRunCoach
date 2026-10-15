"""SP-328: subscription payments through Polar (the provider and merchant account inReachLiveTrack
uses). Polar is a fake here (FakePolar, served through httpx.MockTransport, answering like the
sandbox API: checkouts, the customer state by external id, customer sessions, subscription updates);
its webhooks are signed by the test with the Standard Webhooks scheme. Nothing reaches the network;
no real key or card is used. Sign-in is SP-58's fake Google (test_login_sp58.py)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
import time
import uuid

import httpx
import pytest

from backend.billing import entitlement as E, signature as S
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    PUBLIC_URL, _clean_env, _client, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

TOKEN = "polar_oat_FAKE_sp328_never_a_real_one"
WH_SECRET = "polar_whs_FAKE_sp328_signing_secret"
MONTH, YEAR = "prod-month-sp328", "prod-year-sp328"
OTHER_APP = "prod-inreach-basic"               # inReachLiveTrack's product in the shared organization
UTC = dt.timezone.utc


def _iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------- the fake Polar

class FakePolar:
    """The sandbox API as far as this app calls it. `subs[external_id]` = the customer's
    active_subscriptions; `fail` = a status to answer every call with (an outage)."""

    def __init__(self):
        self.calls: list = []
        self.subs: dict = {}
        self.customers: dict = {}
        self.fail = None

    def customer(self, ext: str) -> str:
        return self.customers.setdefault(ext, str(uuid.uuid4()))

    def state(self, ext: str) -> dict:
        return {"id": self.customer(ext), "external_id": ext, "email": "runner@example.com",
                "active_subscriptions": self.subs.get(ext, [])}

    def handle(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, url, body, req.headers.get("authorization")))
        assert url.startswith("https://sandbox-api.polar.sh/"), url        # never production in a test
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "invalid token"})
        if self.fail:
            return httpx.Response(self.fail, json={"detail": "upstream down"})
        path = req.url.path
        if req.method == "POST" and path == "/v1/checkouts/":
            return httpx.Response(201, json={"id": "co_1", "url": "https://sandbox.polar.sh/checkout/co_1"})
        if req.method == "GET" and path.startswith("/v1/customers/external/") and path.endswith("/state"):
            ext = req.url.path.split("/")[4]
            from urllib.parse import unquote
            ext = unquote(ext)
            if ext not in self.subs:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=self.state(ext))
        if req.method == "POST" and path == "/v1/customer-sessions/":
            return httpx.Response(201, json={"customer_portal_url": f"https://sandbox.polar.sh/portal/{body['customer_id']}"})
        if req.method == "PATCH" and path.startswith("/v1/subscriptions/"):
            sid = path.rsplit("/", 1)[1]
            for subs in self.subs.values():
                for s in subs:
                    if s["id"] == sid:
                        s.update(body)
                        return httpx.Response(200, json=s)
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(404, json={"detail": "no route"})


def sub(product=MONTH, status="active", days=30, cancel=False, ends=None, sid=None) -> dict:
    now = dt.datetime.now(UTC)
    return {"id": sid or "sub_" + secrets.token_hex(4), "status": status, "product_id": product,
            "current_period_start": _iso(now - dt.timedelta(days=1)),
            "current_period_end": _iso(now + dt.timedelta(days=days)), "cancel_at_period_end": cancel,
            "ends_at": _iso(ends) if ends else None}


@pytest.fixture
def polar(monkeypatch):
    from backend.billing import polar as P
    fake = FakePolar()
    monkeypatch.setattr(P, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def billing_env(monkeypatch, polar):
    for k, v in (("TRC_POLAR_TOKEN", TOKEN), ("TRC_POLAR_WEBHOOK_SECRET", WH_SECRET), ("TRC_POLAR_ENV", "sandbox"),
                 ("TRC_POLAR_PRODUCT_MONTH", MONTH), ("TRC_POLAR_PRODUCT_YEAR", YEAR)):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TRC_POLAR_API_BASE", raising=False)
    return polar


def _uid(sub_: str) -> int:
    from backend.accounts import store
    return store.find_identity("google", sub_).id


def runner(app, name="runner"):
    """A signed-up user in their own browser: (client, user id)."""
    cl = browser(app, "203.0.113.%d" % (20 + len(name)))
    sign_up(cl, f"g-{name}", f"{name}@example.com")
    return cl, _uid(f"g-{name}")


def hook(cl, data: dict, etype="customer.state_changed", msg_id=None, ts=None, secret=WH_SECRET, sig=None):
    """Polar posting a webhook: signed like Standard Webhooks; no cookie, no CSRF header."""
    body = json.dumps({"type": etype, "timestamp": _iso(dt.datetime.now(UTC)), "data": data}).encode()
    msg_id = msg_id or "msg_" + secrets.token_hex(8)
    ts = str(int(ts if ts is not None else time.time()))
    headers = {"webhook-id": msg_id, "webhook-timestamp": ts, "content-type": "application/json",
               "webhook-signature": sig if sig is not None else S.sign(secret, msg_id, ts, body)}
    return _client(cl.trc_app, "198.51.100.7").post("/api/v1/billing/webhook", content=body, headers=headers)


def state_of(polar: FakePolar, uid: int) -> dict:
    return polar.state(f"trc:{uid}")


def plan_of(uid: int):
    from backend.accounts import store
    from backend import membership as MS
    u = store.get_user(uid)
    return MS.role_of_user(u), u.plan_expires_at


def post(cl, path, body=None):
    return cl.post(path, json=body or {}, headers={"X-TRC-CSRF": csrf(cl)})


# ---------------------------------------------------------------------------- the signature

def test_signature_accepts_polars_scheme_and_refuses_every_doubt():
    body = b'{"type":"customer.state_changed"}'
    now = 1_760_000_000
    good = S.sign(WH_SECRET, "msg_1", str(now), body)
    assert good.startswith("v1,")
    assert S.verify(WH_SECRET, body, "msg_1", str(now), good, now=now)
    # a rotated secret: two signatures, either may match
    assert S.verify(WH_SECRET, body, "msg_1", str(now), "v1,AAAA " + good, now=now)
    assert not S.verify(WH_SECRET, body + b" ", "msg_1", str(now), good, now=now)        # body changed
    assert not S.verify(WH_SECRET, body, "msg_2", str(now), good, now=now)               # id changed
    assert not S.verify("another-secret", body, "msg_1", str(now), good, now=now)
    assert not S.verify(WH_SECRET, body, "msg_1", str(now), good, now=now + S.TOLERANCE_S + 1)   # replayed later
    assert not S.verify(WH_SECRET, body, "msg_1", str(now), good, now=now - S.TOLERANCE_S - 1)
    for bad in ("", "v1,", "v2," + good[3:], "v1,not base64!!", good[3:]):
        assert not S.verify(WH_SECRET, body, "msg_1", str(now), bad, now=now)
    assert not S.verify(None, body, "msg_1", str(now), good, now=now)                   # no secret: never open
    assert not S.verify(WH_SECRET, body, None, str(now), good, now=now)
    assert not S.verify(WH_SECRET, body, "msg_1", "soon", good, now=now)


# ---------------------------------------------------------------------------- the rules (pure)

NOW = dt.datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
PRODUCTS = {"month": MONTH, "year": YEAR}


def derive(subs, prev=None, owner=False, now=NOW):
    return E.derive(user_id=5, state={"id": "cus_1", "active_subscriptions": subs}, products=PRODUCTS, prev=prev,
                    is_owner=owner, now=now, source_ts=E.iso(now))


def _s(**kw):
    base = {"id": "sub_1", "status": "active", "product_id": MONTH, "current_period_end": "2026-11-15T12:00:00Z",
            "cancel_at_period_end": False, "ends_at": None}
    return {**base, **kw}


def test_rules_active_renewing_cancelled_and_yearly():
    o = derive([_s()])
    assert o.plan == ("subscriber", "2026-11-18T12:00:00+00:00")         # period end + 3 days of grace
    assert o.row["interval"] == "month" and o.row["customer_id"] == "cus_1" and o.row["status"] == "active"
    o = derive([_s(product_id=YEAR, current_period_end="2027-10-15T12:00:00Z")])
    assert o.row["interval"] == "year" and o.plan[1].startswith("2027-10-18")
    # cancelled at the period end: subscriber until that day exactly, then free (membership's expiry)
    o = derive([_s(cancel_at_period_end=True, ends_at="2026-11-15T12:00:00Z")])
    assert o.plan == ("subscriber", "2026-11-15T12:00:00+00:00")
    # canceled with the paid period still ahead: still counts (never take away what was paid for)
    o = derive([_s(status="canceled", ends_at="2026-10-20T00:00:00Z")])
    assert o.plan == ("subscriber", "2026-10-20T00:00:00+00:00") and E.managed(o.row, NOW)
    # …and once that day is past it doesn't
    assert derive([_s(status="canceled", ends_at="2026-10-01T00:00:00Z")]).plan is None


def test_rules_failed_renewal_gives_seven_days_then_recovers():
    o = derive([_s(status="past_due")])
    assert o.row["past_due_since"] == E.iso(NOW) and o.plan == ("subscriber", E.iso(NOW + dt.timedelta(days=7)))
    later = NOW + dt.timedelta(days=3)
    o2 = derive([_s(status="past_due")], prev=o.row, now=later)
    assert o2.row["past_due_since"] == E.iso(NOW), "the grace counts from the first failure"
    assert o2.plan[1] == E.iso(NOW + dt.timedelta(days=7))
    o3 = derive([_s()], prev=o2.row, now=later)
    assert o3.row["past_due_since"] is None and o3.plan[0] == "subscriber" and o3.plan[1].startswith("2026-11-18")


def test_rules_revoked_refunded_other_apps_products_and_the_owner():
    prev = derive([_s()]).row
    # revoked (or refunded and revoked in Polar's dashboard): nothing live -> free now
    o = derive([], prev=prev)
    assert o.plan == ("free", None) and o.row["status"] == "revoked" and not E.managed(o.row, NOW)
    # never subscribed here and nothing live: a plan the owner gave by hand is left alone
    assert derive([]) == E.Outcome(None, None)
    # the shared organization: inReachLiveTrack's product never counts here
    assert derive([_s(product_id=OTHER_APP)]) == E.Outcome(None, None)
    assert derive([_s(product_id=OTHER_APP)], prev=prev).plan == ("free", None)
    # the owner: the row is kept, the account never touched
    o = derive([_s()], owner=True)
    assert o.row is not None and o.plan is None
    assert derive([], prev=prev, owner=True).plan is None
    # the pages never see Polar's ids
    s = E.summary(derive([_s()]).row, NOW)
    assert "customer_id" not in s and "subscription_id" not in s and "cus_1" not in json.dumps(s)


# ---------------------------------------------------------------------------- not open / not here

def test_owners_single_user_app_has_no_billing(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    _clean_env(monkeypatch, tmp_path)
    with TestClient(build_app(demo=False)) as cl:
        assert cl.get("/plans").status_code == 404
        assert cl.get("/api/v1/billing").status_code == 404
        assert cl.post("/api/v1/billing/webhook", content=b"{}").status_code == 404


def test_without_the_keys_it_says_not_open_and_calls_nobody(app, polar, monkeypatch):
    for k in ("TRC_POLAR_TOKEN", "TRC_POLAR_WEBHOOK_SECRET", "TRC_POLAR_PRODUCT_MONTH", "TRC_POLAR_PRODUCT_YEAR"):
        monkeypatch.delenv(k, raising=False)
    cl, uid = runner(app)
    r = cl.get("/plans", headers={"Accept": "text/html"})
    assert r.status_code == 200 and 'id="pl-closed"' in r.text
    v = cl.get("/api/v1/billing").json()
    assert v["open"] is False and v["role"] == "user" and v["subscription"] is None
    assert v["prices"] == {"month": {"amount": 99, "currency": "TWD"}, "year": {"amount": 890, "currency": "TWD"}}
    r = post(cl, "/api/v1/billing/checkout", {"interval": "month", "agree": True})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "BILLING_NOT_OPEN"
    assert hook(cl, state_of(polar, uid)).status_code == 404
    assert polar.calls == []


# ---------------------------------------------------------------------------- checkout -> webhook -> plan

def test_checkout_opens_polar_with_this_apps_customer_key(app, billing_env):
    polar = billing_env
    cl, uid = runner(app)
    v = cl.get("/api/v1/billing").json()
    assert v["open"] is True and v["env"] == "sandbox"
    r = post(cl, "/api/v1/billing/checkout", {"interval": "year", "agree": False})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "TERMS_NOT_AGREED"
    assert cl.post("/api/v1/billing/checkout", json={"interval": "year", "agree": True}).status_code == 403  # CSRF
    r = post(cl, "/api/v1/billing/checkout", {"interval": "year", "agree": True})
    assert r.status_code == 200 and r.json()["url"] == "https://sandbox.polar.sh/checkout/co_1"
    method, url, body, auth = polar.calls[-1]
    assert (method, url) == ("POST", "https://sandbox-api.polar.sh/v1/checkouts/")
    assert body["products"] == [YEAR] and body["external_customer_id"] == f"trc:{uid}"
    assert body["customer_email"] == "runner@example.com"
    assert body["success_url"] == PUBLIC_URL + "/plans?billing=ok&checkout_id={CHECKOUT_ID}"
    assert body["return_url"] == PUBLIC_URL + "/plans?billing=cancel"
    assert post(cl, "/api/v1/billing/checkout", {"interval": "week", "agree": True}).status_code == 400
    # paid: the plan changes only through Polar's answer, never from the checkout itself
    assert plan_of(uid) == ("user", None)


def test_webhook_subscribes_renews_fails_recovers_cancels_and_revokes(app, billing_env):
    from backend import membership as MS
    polar = billing_env
    cl, uid = runner(app)
    ext = f"trc:{uid}"
    s1 = sub(sid="sub_a")
    polar.subs[ext] = [s1]
    r = hook(cl, state_of(polar, uid))
    assert r.status_code == 200 and r.json()["handled"] == 1
    role, exp = plan_of(uid)
    assert role == "subscriber" and exp > _iso(dt.datetime.now(UTC) + dt.timedelta(days=32))
    sess = cl.get("/api/v1/session").json()["membership"]
    assert sess["role"] == "subscriber" and "charts:365d" in sess["scopes"] and "calc:backtest" in sess["scopes"]
    assert sess["quotas"].get("calc") is None                                # the free tier's 10 a month is gone
    # renewed: the next period's state moves the expiry on
    s1.update(current_period_end=_iso(dt.datetime.now(UTC) + dt.timedelta(days=61)))
    assert hook(cl, state_of(polar, uid)).json()["handled"] == 1
    assert plan_of(uid)[1] > _iso(dt.datetime.now(UTC) + dt.timedelta(days=62))
    # the renewal failed: Polar retries the card; 7 days of grace, said on the page
    s1.update(status="past_due")
    hook(cl, state_of(polar, uid))
    role, exp = plan_of(uid)
    grace = dt.datetime.fromisoformat(exp) - dt.datetime.now(UTC)
    assert role == "subscriber" and dt.timedelta(days=6, hours=23) < grace <= dt.timedelta(days=7)
    v = cl.get("/api/v1/billing").json()
    assert v["subscription"]["status"] == "past_due" and v["subscription"]["grace_until"] == exp
    # past the grace without a payment: the free tier, by the expiry alone (no job)
    later = dt.datetime.fromisoformat(exp) + dt.timedelta(minutes=1)
    from backend.accounts import store
    assert MS.role_of_user(store.get_user(uid), later) == "user"
    # the card was updated: active again, grace cleared
    s1.update(status="active")
    hook(cl, state_of(polar, uid))
    assert cl.get("/api/v1/billing").json()["subscription"]["past_due_since"] is None
    # cancelled in the portal: until the period's end, then free
    end = dt.datetime.now(UTC) + dt.timedelta(days=20)
    s1.update(cancel_at_period_end=True, ends_at=_iso(end))
    hook(cl, state_of(polar, uid))
    role, exp = plan_of(uid)
    assert role == "subscriber" and exp == E.iso(end)
    assert MS.role_of_user(store.get_user(uid), end + dt.timedelta(seconds=1)) == "user"
    # revoked (refunded in Polar's dashboard and revoked, or ended): free now, data untouched
    polar.subs[ext] = []
    hook(cl, state_of(polar, uid))
    assert plan_of(uid) == ("user", None)
    assert store.get_user(uid).plan == "free"


def test_webhook_redelivery_is_applied_once_and_old_states_never_win(app, billing_env):
    from backend.accounts import store
    polar = billing_env
    cl, uid = runner(app)
    ext = f"trc:{uid}"
    polar.subs[ext] = [sub(sid="sub_b")]
    r1 = hook(cl, state_of(polar, uid), msg_id="msg_same")
    r2 = hook(cl, state_of(polar, uid), msg_id="msg_same")                 # Polar retries the same delivery
    assert r1.json()["handled"] == 1 and r2.status_code == 200 and r2.json() == {"handled": 0, "duplicate": True}
    import sqlite3
    con = sqlite3.connect(str(store.path()))
    try:
        assert con.execute("SELECT count(*) FROM billing_events WHERE id = 'msg_same'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM billing_subscriptions WHERE user_id = ?", (uid,)).fetchone()[0] == 1
    finally:
        con.close()
    # an older state delivered late (retries arrive out of order) is recorded, not applied
    polar.subs[ext] = []
    r = hook(cl, state_of(polar, uid), ts=time.time() - 120)
    assert r.json() == {"handled": 0, "stale": True}
    assert plan_of(uid)[0] == "subscriber"


def test_webhook_refuses_bad_signatures_with_no_effect(app, billing_env):
    from backend.accounts import store
    polar = billing_env
    cl, uid = runner(app)
    polar.subs[f"trc:{uid}"] = [sub()]
    data = state_of(polar, uid)
    for kw in ({"secret": "guessed"}, {"sig": ""}, {"ts": time.time() - 3600}, {"sig": "v1,AAAA"}):
        assert hook(cl, data, **kw).status_code == 401, kw
    assert plan_of(uid) == ("user", None) and store.billing_row(uid) is None
    assert not store.billing_event_seen("anything")


def test_webhook_for_another_apps_customer_or_type_changes_nothing(app, billing_env):
    from backend.accounts import store
    cl, uid = runner(app)
    # inReachLiveTrack's customer in the shared organization (its external ids are UUIDs)
    other = {"id": "cus_x", "external_id": str(uuid.uuid4()), "active_subscriptions": [sub(product=OTHER_APP)]}
    r = hook(cl, other)
    assert r.status_code == 200 and r.json()["handled"] == 0
    # this app's customer with only the other app's product: not a subscriber here
    r = hook(cl, {"id": "cus_y", "external_id": f"trc:{uid}", "active_subscriptions": [sub(product=OTHER_APP)]})
    assert r.json()["handled"] == 0 and plan_of(uid) == ("user", None) and store.billing_row(uid) is None
    r = hook(cl, {"id": "ord_1"}, etype="order.created")
    assert r.status_code == 200 and r.json()["handled"] == 0
    # not JSON (signed): 200, a retry would be the same
    msg, ts = "msg_nj", str(int(time.time()))
    r = _client(app, "198.51.100.7").post("/api/v1/billing/webhook", content=b"not json", headers={
        "webhook-id": msg, "webhook-timestamp": ts, "webhook-signature": S.sign(WH_SECRET, msg, ts, b"not json")})
    assert r.status_code == 200


# ---------------------------------------------------------------------------- sync, portal

def test_back_from_checkout_the_sync_asks_polar_and_a_failure_changes_nothing(app, billing_env):
    polar = billing_env
    cl, uid = runner(app)
    v = post(cl, "/api/v1/billing/sync").json()                          # never bought: Polar's 404
    assert v["role"] == "user" and v["subscription"] is None
    polar.subs[f"trc:{uid}"] = [sub(product=MONTH)]
    v = post(cl, "/api/v1/billing/sync").json()                          # the webhook hasn't come yet
    assert v["role"] == "subscriber" and v["subscription"]["interval"] == "month" and v["subscription"]["managed"]
    assert "customer_id" not in json.dumps(v) and polar.customers[f"trc:{uid}"] not in json.dumps(v)
    n = len(polar.calls)
    post(cl, "/api/v1/billing/sync")                                     # pressed again at once: no new call
    assert len(polar.calls) == n
    # Polar down: 502, and nobody is downgraded without Polar's answer
    from backend.accounts import store
    store.billing_mark_synced(uid, "2000-01-01T00:00:00+00:00")
    polar.fail = 503
    r = post(cl, "/api/v1/billing/sync")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "BILLING_UPSTREAM"
    assert plan_of(uid)[0] == "subscriber"
    assert cl.get("/api/v1/billing").status_code == 200                  # the page's quiet re-check: no error


def test_portal_and_a_second_checkout_while_subscribed(app, billing_env):
    polar = billing_env
    cl, uid = runner(app)
    r = post(cl, "/api/v1/billing/portal")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_SUBSCRIPTION"
    polar.subs[f"trc:{uid}"] = [sub()]
    hook(cl, state_of(polar, uid))
    r = post(cl, "/api/v1/billing/portal")
    cus = polar.customers[f"trc:{uid}"]
    assert r.status_code == 200 and r.json()["url"] == f"https://sandbox.polar.sh/portal/{cus}"
    assert polar.calls[-1][2] == {"customer_id": cus, "return_url": PUBLIC_URL + "/plans?billing=portal"}
    r = post(cl, "/api/v1/billing/checkout", {"interval": "month", "agree": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SUBSCRIPTION_EXISTS"


# ---------------------------------------------------------------------------- deleting the account (SP-386)

def test_deleting_the_account_stops_the_renewals_first(app, billing_env, monkeypatch):
    from backend.accounts import store
    from backend.api import account as A
    polar = billing_env
    cl, uid = runner(app)
    polar.subs[f"trc:{uid}"] = [sub(sid="sub_del")]
    hook(cl, state_of(polar, uid))
    monkeypatch.setattr(A, "_reauth_ok", lambda request: True)
    polar.fail = 500                                     # Polar can't be told: nothing is deleted
    r = post(cl, "/api/v1/account/delete", {"confirm": True})
    assert r.status_code == 502 and store.get_user(uid) is not None
    polar.fail = None
    r = post(cl, "/api/v1/account/delete", {"confirm": True})
    assert r.status_code == 200, r.text
    patch = [x for x in polar.calls if x[0] == "PATCH"][-1]
    assert patch[1].endswith("/v1/subscriptions/sub_del") and patch[2] == {"cancel_at_period_end": True}
    assert store.get_user(uid) is None and store.billing_row(uid) is None
    # the redelivery check still knows the events, without the person
    import sqlite3
    con = sqlite3.connect(str(store.path()))
    try:
        assert con.execute("SELECT count(*) FROM billing_events WHERE user_id IS NOT NULL").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM billing_events").fetchone()[0] >= 1
    finally:
        con.close()


# ---------------------------------------------------------------------------- secrets, the upgrade hint

def test_the_keys_never_reach_a_log_line_or_an_answer(app, billing_env, caplog):
    from backend import applog
    from backend.settings import env_secrets as ES
    polar = billing_env
    assert {"TRC_POLAR_TOKEN", "TRC_POLAR_WEBHOOK_SECRET"} <= set(ES.NAMES)
    caplog.set_level(logging.DEBUG)
    cl, uid = runner(app)
    polar.subs[f"trc:{uid}"] = [sub()]
    texts = [post(cl, "/api/v1/billing/checkout", {"interval": "month", "agree": True}).text,
             hook(cl, state_of(polar, uid)).text, post(cl, "/api/v1/billing/portal").text,
             cl.get("/api/v1/billing").text, hook(cl, {}, secret="wrong").text]
    polar.fail = 500
    texts.append(post(cl, "/api/v1/billing/portal").text)
    logged = "\n".join(applog.RedactingFormatter("%(message)s").format(r) for r in caplog.records)
    for secret in (TOKEN, WH_SECRET):
        assert secret not in logged and all(secret not in t for t in texts)
        assert secret not in applog.redact(f"oops {secret}")
    assert "runner@example.com" not in logged


def test_the_upgrade_hint_leads_to_the_plans_page():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "static"
    js = (static / "shell.js").read_text("utf-8")
    assert '{ href: "/plans", text: T("scope.see_plans") }' in js
    assert "toast(el.title, el.title === UPGRADE ? PLANS : null)" in js     # a locked control
    assert "d.upgrade && !d.paused ? PLANS : null" in js                    # a 403 / 429 from any request
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "shell.json").read_text("utf-8"))
        assert cat["scope.see_plans"] and cat["account.plans"]


def test_the_plans_page_renders_for_a_free_user(app, billing_env):
    cl, uid = runner(app)
    r = cl.get("/plans", headers={"Accept": "text/html"})
    assert r.status_code == 200 and "NT$99" in r.text and "NT$890" in r.text and 'href="/legal/terms"' in r.text
    r = cl.get("/plans?lang=en", headers={"Accept": "text/html"})
    assert "Plans and subscription" in r.text
    # signed out: the sign-in page first
    r = _client(app, "203.0.113.99").get("/plans", headers={"Accept": "text/html"})
    assert r.status_code in (302, 303, 307) and location(r).startswith("/login")
