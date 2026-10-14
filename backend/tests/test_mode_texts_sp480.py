"""SP-480: a user who cannot switch 設定 › 計算模式 (sign-in mode without the `admin` scope, SP-464)
reads nothing about the two modes (我的算式 / WKO5 對照模式) anywhere on the settings page.

- Every sentence on the page that names a mode is either inside the 計算模式 section (hidden for
  them, SP-464), a `.mode-note` (hidden by `html.nomode`), or has a mode-free twin chosen by the
  same rule (`tabs.advanced_note_nomode`, the nav's `page.settings.purpose_nomode`). SP-488 (ST15): 「只在
  我的算式生效」 is said once, in 計算模式 — the two `.mode-note` spans and #thr-q's mode sentence (and
  with it `thr.help_nomode`) are gone.
- The rule is decided before the first paint from the session the server inlines
  (TRC_SESSION: `login` and `membership.scopes`), so nothing flashes; GET /config's can_switch
  confirms it. The admin and the owner's single-user app / the demo (no `login`): as before.
- zh-TW and en.
The browser half (what is visible, no flash) is backend/tests/e2e/test_settings_nomode_sp480.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.i18n import pages as P
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    OWNER_EMAIL, _clean_env, _client, app, browser, c, env, sign_in, sign_up, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
SETTINGS = (STATIC / "settings.html").read_text("utf-8")
SHELL = (STATIC / "shell.js").read_text("utf-8")
I18N = STATIC / "i18n"
# what names a mode, in either language
MODE_WORDS = re.compile(r"我的算式|對照模式|計算模式|WKO5 對照|My formulas|parity mode|Calculation mode", re.I)


def _cat(loc: str, ns: str) -> dict:
    return json.loads((I18N / loc / f"{ns}.json").read_text("utf-8"))


def _markup_a_user_sees(html: str) -> str:
    """The rendered page without what a nomode user never sees: the scripts (texts there are
    checked by key below), the 計算模式 section (hidden, SP-464), the `.mode-note` spans
    (html.nomode) and #thr-q's title (swapped for thr.help_nomode)."""
    html = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style\s*>", "", html, flags=re.S | re.I)
    html = re.sub(r'<section id="mode">.*?</section>', "", html, flags=re.S)
    html = re.sub(r'<span class="mode-note"[^>]*>[^<]*</span>', "", html)
    html = re.sub(r'(<button[^>]*\bid="thr-q"[^>]*?)\stitle="[^"]*"', r"\1", html)
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


# ---------------------------------------------------------------------------- the page

@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_nothing_a_nomode_user_sees_names_a_mode(loc):
    out = _markup_a_user_sees(P.render("settings", loc, debug=False))
    assert not MODE_WORDS.findall(out), MODE_WORDS.findall(out)


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_mode_sentence_is_there_for_the_admin(loc):
    """The same rendered page keeps the mode sentence for whoever may switch (the admin / the owner's
    app) — once, in 計算模式 (SP-488, ST15: it was in 資料校正, 排除壞掉的活動檔 and #thr-q's help)."""
    html = P.render("settings", loc, debug=False)
    assert not re.findall(r'<span class="mode-note"', html)
    notes = re.findall(r'<p class="meta mode-note" data-i18n="settings\.mode\.only_mine">([^<]+)</p>', html)
    assert len(notes) == 1 and notes[0] == _cat(loc, "settings")["mode.only_mine"]
    assert MODE_WORDS.search(notes[0])
    mode = re.search(r'<section id="mode">.*?</section>', html, flags=re.S).group(0)
    assert notes[0] in mode                                       # inside 計算模式 (hidden for a nomode user)
    thr = re.search(r'<button[^>]*\bid="thr-q"[^>]*\btitle="([^"]+)"', html).group(1)
    assert not MODE_WORDS.search(thr)                             # the help no longer repeats it
    assert "html.nomode .mode-note { display: none; }" in html


def test_the_nomode_rule_runs_before_the_body_and_follows_can_switch():
    head = SETTINGS[:SETTINGS.index("<body>")]
    # decided in <head> (before the first paint) from the inlined session: sign-in mode, no admin scope
    assert 'document.documentElement.classList.toggle("nomode", !!(s && s.login) && !scopes.includes("admin"));' in head
    assert "s.membership && s.membership.scopes" in head
    js = SETTINGS[SETTINGS.index("const SIGNIN = "):]
    js = js[:js.index('document.querySelectorAll(".mode").forEach((b) => b.onclick')]
    # then GET /config's can_switch confirms it; #thr-q's help names no mode any more (SP-488), nothing to swap
    assert 'document.documentElement.classList.toggle("nomode", can_switch === false);' in js
    assert "settings.thr.help_nomode" not in SETTINGS and "modeTexts" not in SETTINGS
    # 進階設定's tab note (SP-464) still follows the hidden section
    assert '_note${tab === "advanced" && $("mode").hidden ? "_nomode" : ""}' in SETTINGS


def test_the_nav_purpose_of_settings_follows_the_same_rule():
    assert ('const NOMODE = !!SESSION.login && !((SESSION.membership && SESSION.membership.scopes) || [])'
            '.includes("admin");') in SHELL
    assert 'p.purpose = T(`page.${p.id}.purpose${p.id === "settings" && NOMODE ? "_nomode" : ""}`);' in SHELL


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_mode_free_twins_name_no_mode(loc):
    st, sh = _cat(loc, "settings"), _cat(loc, "shell")
    for cat, k in ((st, "tabs.advanced_note"), (sh, "page.settings.purpose")):
        assert MODE_WORDS.search(cat[k]), k                        # the admin's text names it …
        assert cat[f"{k}_nomode"] and not MODE_WORDS.search(cat[f"{k}_nomode"]), k   # … the twin does not
    assert MODE_WORDS.search(st["mode.only_mine"])
    assert not MODE_WORDS.search(st["thr.help"]) and "thr.help_nomode" not in st     # SP-488: one text, no mode
    if loc == "en":
        for k in ("thr.help", "mode.only_mine"):
            assert not re.search(r"[一-鿿]", st[k]), k
        assert not re.search(r"[一-鿿]", sh["page.settings.purpose_nomode"])
    # the help is the old mode-free twin: the old help minus the mode sentence (SP-488)
    cut = {"zh-TW": "只在「我的算式」模式生效；「WKO5 對照模式」一律用 WKO5 裡的設定。",
           "en": 'Only in "My formulas" mode; "WKO5 parity mode" always uses the settings in WKO5. '}[loc]
    assert cut not in st["thr.help"] and st["thr.help"].startswith({"zh-TW": "每次測試記一列", "en": "One row per test"}[loc])


# ---------------------------------------------------------------------------- over HTTP (SP-58's app)

def _inlined_session(r) -> dict:
    m = re.search(r"<script>window\.TRC_SESSION = (\{.*?\});</script>", r.text)
    assert m, "no inlined session"
    return json.loads(m.group(1))


def _nomode(sess: dict) -> bool:
    """The page's own rule (settings.html <head>, shell.js NOMODE), in Python."""
    return bool(sess.get("login")) and "admin" not in ((sess.get("membership") or {}).get("scopes") or [])


def test_a_user_gets_nomode_and_the_admin_does_not(app, c):
    """The settings page each person gets: the inlined session says nomode exactly when GET /config
    says can_switch false (the user), and not for the admin (the owner)."""
    from backend.tests.test_login_sp58 import location
    assert location(sign_in(c, "g-owner", OWNER_EMAIL)) == "/"
    u = browser(app, "203.0.113.21")
    sign_up(u, "g-user", "runner@example.com")
    for cl, want in ((u, True), (c, False)):
        r = cl.get("/api/v1/wko5/settings")
        assert r.status_code == 200
        assert _nomode(_inlined_session(r)) is want
        assert cl.get("/api/v1/wko5/config").json()["can_switch"] is (not want)


def test_the_owners_app_and_the_demo_have_no_login():
    """No `login` in the session (the owner's single-user app, the demo): never nomode."""
    assert _nomode({"mode": "owner", "membership": {"scopes": []}}) is False
    assert _nomode({"mode": "demo", "membership": None}) is False
    assert _nomode({"mode": "user", "login": {"required": True}, "membership": {"scopes": ["plan"]}}) is True
