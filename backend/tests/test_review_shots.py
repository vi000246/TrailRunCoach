"""The shot list of the review screenshots (backend/tests/e2e/shots.py), without a
browser: a typo in a key must stop the run instead of silently dropping a mock or
a step, the file names must be valid on Windows, and a `set` mock patches only
the paths it names."""
from __future__ import annotations

import pytest

from backend.tests.e2e import shots as S


def _spec(**shot):
    return {"ticket": "SP-396", "shots": [{"name": "課表頁", "page": "schedule", **shot}]}


@pytest.mark.parametrize("vp", ["mobile", "desktop", [1024, 768], [600, 900, 2]])
def test_every_documented_viewport_passes(vp):
    # [w, h] once raised TypeError (a list is not hashable) instead of passing
    assert S.check_spec(_spec(viewport=vp))


def test_a_page_without_the_nav_names_what_to_wait_for():
    assert S.check_spec({"ticket": "SP-58", "shots": [{"name": "登入", "path": "/login", "ready": "form"}]})


def test_a_plain_shot_list_passes():
    spec = _spec(viewport="mobile", caption="看色條", hide=[".sugbox"],
                 mock=[{"url": "**/api/v1/session", "json": {}}, {"url": "re:.*/sessions", "set": {"0.done": True}}],
                 steps=[{"click": "text=週"}, {"sleep": 300}])
    assert S.check_spec(spec) is spec


@pytest.mark.parametrize("bad, why", [
    ({"ticket": "396", "shots": [{"name": "x", "page": "schedule"}]}, "ticket"),
    ({"ticket": "SP-396", "shots": []}, "non-empty"),
    (_spec(viewpor="mobile"), "unknown keys"),
    (_spec(page="nope"), "is not one of"),
    ({"ticket": "SP-1", "shots": [{"name": "x", "page": "schedule", "path": "/a"}]}, "exactly one of page / path"),
    (_spec(viewport="tablet"), "viewport"),
    (_spec(viewport=[390]), "viewport"),
    (_spec(viewport=[0, 844]), "viewport"),
    (_spec(mock=[{"url": "**/x"}]), "exactly one of json / set"),
    (_spec(mock=[{"url": "**/x", "json": {}, "body": 1}]), "unknown mock keys"),
    (_spec(steps=[{"klick": "a"}]), "a step is one of"),
    (_spec(steps=[{"click": "a", "sleep": 1}]), "a step is one of"),
    (_spec(hide=".sugbox"), "hide"),
])
def test_a_typo_stops_the_run(bad, why):
    with pytest.raises(S.SpecError, match=why):
        S.check_spec(bad)


def test_file_names_are_valid_on_windows():
    assert S.file_name("SP-396", 2, '課表頁 手機: "週" / 月?') == "SP-396-2-課表頁-手機-週-月.webp"
    assert S.file_name("SP-1", 1, "a", "png") == "SP-1-1-a.png"


def test_set_patches_only_the_named_paths():
    doc = {"items": [{"done": False, "label": "a"}, {"done": False}], "total": 2}
    S.set_path(doc, "items.0.done", True)
    S.set_path(doc, "total", 3)
    assert doc == {"items": [{"done": True, "label": "a"}, {"done": False}], "total": 3}
    rows = [{"x": 1}]
    S.set_path(rows, "0.x", 2)
    assert rows == [{"x": 2}]


# ---- SP-999: a shot list taken in sign-in mode (the signed-out 介紹頁 at "/") ---------------------------------

def test_a_shot_list_names_its_servers_mode():
    assert S.check_spec({"ticket": "SP-999", "mode": "signin",
                         "shots": [{"name": "介紹頁", "path": "/", "ready": ".hero"}]})
    assert S.check_spec({**_spec(), "mode": "owner"})
    with pytest.raises(S.SpecError, match="mode"):
        S.check_spec({**_spec(), "mode": "signed_out"})


def test_the_signin_server_gets_fake_google_settings_and_its_own_accounts_folder(tmp_path, monkeypatch):
    from backend.tests.e2e import _server
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "a-real-looking-id")       # never carried over from the shell
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    owner = _server.server_env(tmp_path / "base")
    assert "GOOGLE_CLIENT_ID" not in owner and "WKO5COACH_ACCOUNTS" not in owner
    assert owner["WKO5COACH_HOME"] == str(tmp_path / "base")
    env = _server.server_env(tmp_path / "base", ("http://127.0.0.1:5000", tmp_path / "signin"))
    assert env["WKO5COACH_ACCOUNTS"] == "1" and env["WKO5COACH_PUBLIC_URL"] == "http://127.0.0.1:5000"
    assert env["GOOGLE_CLIENT_ID"] == _server.SIGNIN_CLIENT_ID and env["GOOGLE_CLIENT_SECRET"]
    assert env["WKO5COACH_OWNER_EMAIL"].endswith(".invalid")                # no one's address
    assert env["WKO5COACH_SERVER_ROOT"] == str(tmp_path / "signin")
    assert env["WKO5COACH_HOME"] == str(tmp_path / "base") and env["WKO5COACH_COOKIE_SECURE"] == "0"
