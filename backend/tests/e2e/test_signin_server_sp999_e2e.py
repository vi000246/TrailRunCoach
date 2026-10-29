"""SP-999: the review screenshots and the e2e server in sign-in mode — "/" is the 介紹頁 a stranger sees,
with everything the server fills in (window.TRC_TRY, TRC_WHY, TRC_CAIRN), not the owner's overview; and a
shot list with "mode": "signin" takes it.

Run: ./e2e.sh -k sp999 (see conftest.py)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest

from backend.tests.e2e import _server

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def signin_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-sp999")
    srv = _server.Server(_server.build_base(root), root / "uvicorn.log", signin=True)
    yield srv
    srv.stop()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "text/html"})        # a browser's page load
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def _filled(html: str, name: str) -> dict:
    m = re.search(r"<script>window\." + name + r" = (\{.*?\});</script>", html)
    assert m, name
    return json.loads(m.group(1))


def test_slash_is_the_signed_out_intro_with_what_the_server_fills_in(signin_server):
    status, _h, html = _get(signin_server.url + "/")
    assert status == 200 and 'class="hero"' in html
    assert _filled(html, "TRC_TRY")["kind"] in ("example", "demo")
    why = _filled(html, "TRC_WHY")
    assert why["sources"] > 0 and why["methods"] > 0 and why["backtest"]
    assert "url" in _filled(html, "TRC_CAIRN")
    _s, _h, body = _get(signin_server.url + "/api/v1/session")
    assert json.loads(body)["mode"] == "signed_out"
    # the app itself asks for sign-in: not the owner's pages
    status, headers, _b = _get(signin_server.url + _server.PAGES["overview"])
    assert status in (302, 303, 307) and headers.get("location", headers.get("Location", "")).startswith("/login")


def test_a_signin_shot_list_takes_the_intro_at_slash(signin_server, e2e_browser, tmp_path):
    from backend.tests.e2e import shots as S
    spec = S.check_spec({"ticket": "SP-999", "mode": "signin",
                         "shots": [{"name": "介紹頁", "path": "/", "ready": ".hero", "clip": ".hero"}]})
    assert S.server_mode(signin_server.url) == S.MODES["signin"]
    rows = S.take(e2e_browser, signin_server.url, spec, tmp_path)
    assert rows[0]["ok"] and rows[0]["errors"] == [], rows
    assert (tmp_path / rows[0]["file"]).stat().st_size > 1000
