"""SP-464: 設定 › 計算模式 (我的算式 / WKO5 對照模式) is an admin's testing tool in sign-in mode.

- A non-admin (membership role user / subscriber: no `admin` scope) never sees the section
  (GET /config → can_switch false; the settings page hides it), cannot switch (PUT /config →
  403 SCOPE_REQUIRED admin) and always gets 我的算式 (parity off) — whatever an older engine.json
  of theirs says, and whatever ?parity= a chart request carries.
- The admin (the owner, the only role with `admin`) switches their own engine.json only: their
  screens and calculations change, nobody else's — their Datasets / caches are their own (the
  Dataset key holds the config and the tenant's folder) and a switch drops only their Datasets.
- The owner's single-user app (no sign-in) and the demo: unchanged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend import tenancy
from backend.api import wko5views as WV
from backend.engine.wko5expr import config as C
from backend.engine.wko5expr.config import EngineConfig, parity_allowed
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    OWNER_EMAIL, _clean_env, _client, app, browser, c, csrf, env, sign_in, sign_up, started,
)

SETTINGS = (Path(__file__).resolve().parents[1] / "static" / "settings.html").read_text("utf-8")
CONFIG = "/api/v1/wko5/config"


# ---------------------------------------------------------------------------- fixtures

@pytest.fixture
def server(tmp_path, monkeypatch):
    """accounts.db with the owner (user 1) and the owner's folder, both in tmp."""
    from backend import accounts
    from backend.accounts import store, tenants as TN
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "owner"))
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    monkeypatch.setattr(C, "CONFIG_PATH", None)            # each tenant's own engine.json
    store.migrate_owner()
    return store


@pytest.fixture
def signin(server, monkeypatch):
    from backend.accounts import login as LG
    monkeypatch.setattr(LG, "_MODE", LG.SIGNIN)
    return server


def _user(store, plan: str = "free") -> tenancy.Tenant:
    from backend.accounts import tenants as TN
    u = store.create_user(email=f"{plan}{store.count_users()}@x.tw")
    if plan != "free":
        store.set_plan(u.id, plan, None)
    t = TN.tenant_of(store.get_user(u.id))
    t.root.mkdir(parents=True, exist_ok=True)
    return t


def _write(t: tenancy.Tenant, cfg: dict) -> None:
    t.root.mkdir(parents=True, exist_ok=True)
    (t.root / "engine.json").write_text(json.dumps(cfg), "utf-8")


@pytest.fixture
def no_source(monkeypatch):
    """_dataset_key without reading any data source (only the config / tenant parts matter here)."""
    monkeypatch.setattr(WV.DSRC, "current_source", lambda: "coros")
    monkeypatch.setattr(WV.DSRC, "source_stamp", lambda src, d=None: "stamp")


def _key_cfg(key: tuple) -> dict:
    return json.loads(key[0])


# ---------------------------------------------------------------------------- sign-in mode: a user

@pytest.mark.parametrize("plan", ["free", "subscriber"])
def test_a_user_cannot_see_or_switch_the_mode(signin, plan):
    t = _user(signin, plan)
    with tenancy.use(t):
        assert parity_allowed() is False
        got = WV.get_config()
        assert got["can_switch"] is False and got["config"]["parity"] is False
        with pytest.raises(HTTPException) as e:
            WV.put_config({"parity": True})
        assert e.value.status_code == 403
        assert e.value.detail["code"] == "SCOPE_REQUIRED" and e.value.detail["scope"] == "admin"
        with pytest.raises(HTTPException):                  # not even 我的算式's preset: no switching at all
            WV.put_config({**C.MOUNTAIN_PRESET.to_dict(), "parity": False})
    assert not (t.root / "engine.json").exists()              # nothing written


def test_a_users_old_parity_setting_is_ignored(signin, no_source):
    """An engine.json from before SP-464 that chose WKO5 parity: the user still gets 我的算式."""
    t = _user(signin)
    _write(t, {**C.MOUNTAIN_PRESET.to_dict(), "parity": True})
    with tenancy.use(t):
        assert EngineConfig.load().parity is False
        assert WV.get_config()["config"]["parity"] is False
        cfg = _key_cfg(WV._dataset_key())
        assert cfg["parity"] is False and cfg["elevation_tss_per_1000ft"] == 10.0   # their own knobs stay
    assert json.loads((t.root / "engine.json").read_text("utf-8"))["parity"] is True   # the file untouched


def test_a_user_cannot_ask_a_chart_for_parity(signin, no_source):
    """?parity=true on a chart / athlete / workout request (the viewer's S.parity) → 我的算式."""
    t = _user(signin)
    with tenancy.use(t):
        assert _key_cfg(WV._dataset_key(parity=True))["parity"] is False
        assert WV._dataset_key(parity=True) == WV._dataset_key(parity=False) == WV._dataset_key()


def test_signed_out_is_not_an_admin(signin):
    with tenancy.use(tenancy.anonymous()):
        assert parity_allowed() is False


# ---------------------------------------------------------------------------- sign-in mode: the admin

def test_the_admin_switches_only_their_own_mode(signin, no_source):
    admin, other = tenancy.owner(), _user(signin, "subscriber")
    _write(other, {**C.MOUNTAIN_PRESET.to_dict(), "parity": False})
    with tenancy.use(other):
        before = WV._dataset_key()
    with tenancy.use(admin):
        assert parity_allowed() is True
        assert WV.get_config()["can_switch"] is True
        WV.put_config({"parity": True})                       # WKO5 對照模式
        assert WV.get_config()["config"]["parity"] is True
        assert EngineConfig.load().parity is True
        akey = WV._dataset_key()
        assert _key_cfg(akey)["parity"] is True
        assert _key_cfg(WV._dataset_key(parity=False))["parity"] is False      # the admin may ask either
    assert json.loads((admin.root / "engine.json").read_text("utf-8"))["parity"] is True
    with tenancy.use(other):                                  # the other user: nothing changed
        assert WV.get_config()["config"]["parity"] is False
        assert WV._dataset_key() == before
        assert WV._dataset_key() != akey and WV._dataset_key()[3] != akey[3]   # never the admin's Dataset
    assert json.loads((other.root / "engine.json").read_text("utf-8"))["parity"] is False
    with tenancy.use(admin):                                  # and back to 我的算式
        WV.put_config({**C.MOUNTAIN_PRESET.to_dict(), "parity": False})
        assert WV.get_config()["config"] == {**C.MOUNTAIN_PRESET.to_dict(), "parity": False}


def test_the_admins_switch_drops_only_the_admins_datasets(signin, monkeypatch):
    """Other users' built Datasets stay (no rebuild for them); the admin's are rebuilt in the new mode."""
    cache = WV._DatasetCache(lambda *a: object())
    monkeypatch.setattr(WV, "_dataset_cfg", cache)
    admin, other = tenancy.owner(), _user(signin)
    mine = ("{cfg}", "coros", "s", str(admin.shared))
    theirs = ("{cfg}", "coros", "s", str(other.shared))
    cache._d[mine], cache._d[theirs] = object(), object()
    with tenancy.use(admin):
        WV.put_config({"parity": True})
    assert cache.entries() == [theirs]
    with tenancy.use(other):                                  # a refused switch drops nothing
        with pytest.raises(HTTPException):
            WV.put_config({"parity": True})
    assert cache.entries() == [theirs]


def test_render_cache_is_per_tenant():
    """The chart answers' cache lives in the tenant's own folder (render_cache.py), its key holds
    the mode (`parity` in chart_key's request) — an admin's 對照 chart is never another's."""
    from backend.engine.wko5expr import render_cache as RC
    import inspect
    assert "tenancy.shared_path('cache', 'render')" in inspect.getsource(RC)
    assert '"parity": ds.config.parity' in inspect.getsource(WV.chart)


# ---------------------------------------------------------------------------- over HTTP (SP-58's app)

def test_over_http_the_user_gets_403_and_the_admin_switches(app, c):
    from backend.tests.test_login_sp58 import location
    assert location(sign_in(c, "g-owner", OWNER_EMAIL)) == "/"          # the owner = the admin
    u = browser(app, "203.0.113.20")
    sign_up(u, "g-user", "runner@example.com")

    r = u.get(CONFIG)
    assert r.status_code == 200 and r.json()["can_switch"] is False and r.json()["config"]["parity"] is False
    r = u.put(CONFIG, json={"parity": True}, headers={"X-TRC-CSRF": csrf(u)})
    assert r.status_code == 403 and r.json()["detail"]["scope"] == "admin"
    assert u.get(CONFIG).json()["config"]["parity"] is False

    assert c.get(CONFIG).json()["can_switch"] is True
    r = c.put(CONFIG, json={"parity": True}, headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 200 and r.json()["config"]["parity"] is True
    assert c.get(CONFIG).json()["config"]["parity"] is True
    assert u.get(CONFIG).json()["config"]["parity"] is False            # the user: unchanged
    sess = u.get("/api/v1/session").json()
    assert sess["login"]["signed_in"] and "admin" not in sess["membership"]["scopes"]
    assert "admin" in c.get("/api/v1/session").json()["membership"]["scopes"]


# ---------------------------------------------------------------------------- the owner's app / demo

def test_owner_mode_is_unchanged(server, no_source, monkeypatch):
    from backend.accounts import login as LG
    assert LG.mode() is None or LG.mode() == LG.OWNER_MODE
    cache = WV._DatasetCache(lambda *a: object())
    monkeypatch.setattr(WV, "_dataset_cfg", cache)
    owner = tenancy.owner()
    with tenancy.use(owner):
        assert parity_allowed() is True
        assert WV.get_config()["can_switch"] is True
        mine = ("{cfg}", "coros", "s", str(owner.shared))
        cache._d[mine] = object()
        WV.put_config({"parity": True})
        assert WV.get_config()["config"]["parity"] is True and EngineConfig.load().parity is True
        assert cache.entries() == []                          # its Datasets rebuilt, as before
        WV.put_config({**C.MOUNTAIN_PRESET.to_dict(), "parity": False})
        assert EngineConfig.load().parity is False
        assert _key_cfg(WV._dataset_key(parity=True))["parity"] is True     # ?parity=true still honoured


def test_owner_mode_with_owner_login_setting_off_still_switches(server, monkeypatch):
    from backend.accounts import login as LG
    monkeypatch.setattr(LG, "_MODE", LG.OWNER_MODE)
    with tenancy.use(tenancy.owner()):
        assert parity_allowed() is True
        WV.put_config({"parity": True})
        assert WV.get_config()["config"]["parity"] is True


def test_the_demo_is_unchanged(monkeypatch):
    monkeypatch.setenv("WKO5COACH_MODE", "demo")
    assert parity_allowed() is True


# ---------------------------------------------------------------------------- the settings page

def test_the_settings_page_hides_the_mode_unless_the_server_says_can_switch():
    js = SETTINGS[SETTINGS.index("const SIGNIN = "):]
    js = js[:js.index("document.querySelectorAll(\".mode\").forEach((b) => b.onclick")]
    # sign-in mode: hidden before the answer (no flash for a user); then the server decides
    assert "if (SIGNIN) $(\"mode\").hidden = true;" in js
    assert "$(\"mode\").hidden = can_switch === false;" in js
    # the admin is told the section is theirs only (sign-in mode only)
    assert "$(\"mode-admin\").hidden = !SIGNIN || can_switch === false;" in js
    assert "window.TRC_SESSION.login" in js
    note = re.search(r'<p class="meta" id="mode-admin" hidden data-i18n="settings\.mode\.admin_only">([^<]+)</p>', SETTINGS)
    assert note and "管理員" in note.group(1)
    # 進階設定's note leaves out 計算模式 when it is hidden
    assert '_note${tab === "advanced" && $("mode").hidden ? "_nomode" : ""}' in SETTINGS


def test_the_new_texts_are_in_both_languages():
    root = Path(__file__).resolve().parents[1] / "static" / "i18n"
    zh = json.loads((root / "zh-TW" / "settings.json").read_text("utf-8"))
    en = json.loads((root / "en" / "settings.json").read_text("utf-8"))
    for k in ("mode.admin_only", "tabs.advanced_note_nomode"):
        assert zh[k] and en[k], k
        assert not re.search(r"[一-鿿]", en[k]), k
    assert "計算模式" not in zh["tabs.advanced_note_nomode"] and "Calculation mode" not in en["tabs.advanced_note_nomode"]
    note = re.search(r'id="mode-admin"[^>]*>([^<]+)<', SETTINGS).group(1)
    assert note == zh["mode.admin_only"]
