"""A fake `garminconnect` package for the SP-91 tests: the classes and methods the app uses
(Garmin, its ActivityDownloadFormat, client.dumps / loads, login with return_on_mfa,
resume_login, get_activities, download_activity, get_heart_rate_zones) and the package's error
classes by name. No network; synthetic data only.

    server = FakeGarminServer(accounts={"me@x.tw": {"password": "pw"}}, activities=[...])
    monkeypatch.setattr(garmin_client, "_LIB", server.module())
"""
from __future__ import annotations

import io
import json
import threading
import time
import types
import zipfile
from enum import Enum
from typing import Optional


class GarminConnectConnectionError(Exception):
    response = None

    def __init__(self, msg: str = "", status: Optional[int] = None):
        super().__init__(msg)
        if status is not None:
            self.response = types.SimpleNamespace(status_code=status)


class GarminConnectNotFoundError(GarminConnectConnectionError):
    pass


class GarminConnectTooManyRequestsError(Exception):
    pass


class GarminConnectAuthenticationError(Exception):
    pass


def zip_of(fit: bytes, name: str = "123_ACTIVITY.fit") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, fit)
    return buf.getvalue()


def activity(aid: int, start, type_key: str = "running") -> dict:
    """A list item as get_activities returns it (the fields the app reads)."""
    local = start.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Taipei"))
    return {"activityId": aid, "activityName": "Run", "startTimeLocal": local.strftime("%Y-%m-%d %H:%M:%S"),
            "startTimeGMT": start.strftime("%Y-%m-%d %H:%M:%S"), "activityType": {"typeKey": type_key}}


class FakeGarminServer:
    """Garmin Connect as the tests need it. `fail` = {"login" | "list" | "download" | "zones": status
    or {activity id: status}} answered once per call while set; `calls` = [(what, monotonic t)]."""

    def __init__(self, accounts: Optional[dict] = None, activities: Optional[list] = None,
                 zips: Optional[dict] = None, zones=None):
        self.accounts = accounts or {}
        self.activities = activities or []
        self.zips = zips or {}
        self.zones = zones
        self.fail: dict = {}
        self.calls: list = []
        self.valid: set = set()
        self.issued = 0
        self._lock = threading.Lock()

    # --------------------------------------------------------------- helpers
    def _call(self, what: str, key=None):
        with self._lock:
            self.calls.append((what, time.monotonic()))
        f = self.fail.get(what)
        if isinstance(f, dict):
            f = f.get(key)
        if f is None:
            return
        if f == 429:
            raise GarminConnectTooManyRequestsError("Rate limit exceeded: API Error 429")
        if f == 404:
            raise GarminConnectNotFoundError("Download client error (404): API Error 404", 404)
        if f == 401:
            raise GarminConnectAuthenticationError("Authentication failed: API Error 401")
        raise GarminConnectConnectionError(f"API call client error ({f}): API Error {f}", f)

    def issue(self) -> str:
        self.issued += 1
        tok = f"di-token-{self.issued}-SECRET"
        self.valid.add(tok)
        return tok

    def revoke_all(self) -> None:
        self.valid.clear()

    def count(self, what: str) -> int:
        return sum(1 for w, _t in self.calls if w == what)

    # --------------------------------------------------------------- the package
    def module(self):
        server = self

        class Client:
            def __init__(self):
                self.di_token = None
                self.di_refresh_token = None
                self.di_client_id = None

            def dumps(self) -> str:
                return json.dumps({"di_token": self.di_token, "di_refresh_token": self.di_refresh_token,
                                   "di_client_id": self.di_client_id})

            def loads(self, s: str) -> None:
                d = json.loads(s)
                self.di_token, self.di_refresh_token = d.get("di_token"), d.get("di_refresh_token")
                self.di_client_id = d.get("di_client_id")

        class Garmin:
            class ActivityDownloadFormat(Enum):
                ORIGINAL = 1
                TCX = 2

            def __init__(self, email=None, password=None, is_cn=False, prompt_mfa=None, return_on_mfa=False,
                         retry_attempts=3, retry_min_wait=1.0, retry_max_wait=10.0, verify_login=True):
                assert retry_attempts == 0, "the app turns the package's retries off"
                assert return_on_mfa is True, "the MFA code comes with a second request"
                self.email, self.password = email, password
                self.client = Client()
                self._mfa = False

            def _auth(self):
                if self.client.di_token not in server.valid:
                    raise GarminConnectAuthenticationError("Authentication failed: API Error 401")

            def login(self, tokenstore=None):
                server._call("login")
                acc = server.accounts.get(self.email)
                if acc is None or acc.get("password") != self.password:
                    raise GarminConnectAuthenticationError("Authentication failed (401 Unauthorized).")
                if acc.get("mfa"):
                    self._mfa = True
                    return "needs_mfa", None
                self.client.di_token, self.client.di_refresh_token = server.issue(), "refresh-SECRET"
                self.password = None
                return None, None

            def resume_login(self, client_state, mfa_code):
                server._call("mfa")
                assert self._mfa, "no MFA login pending"
                if mfa_code != server.accounts[self.email]["mfa"]:
                    raise GarminConnectAuthenticationError("MFA verification failed")
                self._mfa = False
                self.client.di_token, self.client.di_refresh_token = server.issue(), "refresh-SECRET"
                return None, None

            def get_activities(self, start=0, limit=20, activitytype=None, activitysubtype=None):
                server._call("list")
                self._auth()
                return [dict(a) for a in server.activities[start:start + limit]]

            def download_activity(self, activity_id, dl_fmt=None):
                server._call("download", int(activity_id))
                self._auth()
                assert dl_fmt is Garmin.ActivityDownloadFormat.ORIGINAL
                blob = server.zips.get(int(activity_id))
                if blob is None:
                    raise GarminConnectNotFoundError("Download client error (404): API Error 404", 404)
                return blob

            def get_heart_rate_zones(self):
                server._call("zones")
                self._auth()
                return server.zones or []

        mod = types.ModuleType("garminconnect")
        mod.Garmin = Garmin
        mod.GarminConnectAuthenticationError = GarminConnectAuthenticationError
        mod.GarminConnectTooManyRequestsError = GarminConnectTooManyRequestsError
        mod.GarminConnectConnectionError = GarminConnectConnectionError
        mod.GarminConnectNotFoundError = GarminConnectNotFoundError
        return mod


def refusing_module():
    """conftest: every Garmin call fails as if there were no network."""
    server = FakeGarminServer()

    def refuse(*_a, **_k):
        raise GarminConnectConnectionError("no live network in tests")
    mod = server.module()

    class Refusing(mod.Garmin):
        login = resume_login = get_activities = download_activity = get_heart_rate_zones = refuse
    mod.Garmin = Refusing
    return mod
