"""Copy and small-print fixes (SP-415). Static checks of the pages and the i18n catalogs, no server:

1. 設定 › 資料同步 says one thing about passwords, and what the code does (api/auth.py, sync/*_client.py,
   settings/secrets.py): a password is kept only with 「記住密碼」, sealed with the server's key into the
   app's database (sync_state.*_password_sealed) — on the server, for COROS, Garmin and TrainingPeaks
   alike; without it only the sealed sign-in token / cookie is kept.
2. 賽事編輯's 「山頂在第幾 km」 input is wide enough for its placeholder (it was cut to 「空白＝GP」).
3. The overview's watch state in words is SP-397's (test_overview_cards_sp397::
   test_watch_words_with_the_provider_never_the_raw_key); nothing new here.
4. The overview's 完成度 is SP-416 (test_overview_week_done_sp416)."""
import json
import re
import unicodedata
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
SETTINGS = STATIC / "settings.html"
PLAN = STATIC / "plan.html"


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _sync_section() -> str:
    html = SETTINGS.read_text(encoding="utf-8")
    m = re.search(r'<section id="sync">.*?</section>', html, re.S)
    assert m, "資料同步 section not found"
    return m.group(0)


# ---- 1. passwords ----

def test_the_sync_section_says_the_password_is_kept_only_when_remembered():
    sec = _sync_section()
    assert "不會保存" not in sec                       # contradicted the 記住密碼 boxes below it
    m = re.search(r'<span data-i18n="settings\.sync\.cred_note">([^<]*)</span>', sec)
    assert m, "the note above the sources is a translated span (keystat stays a sibling)"
    note = m.group(1)
    assert "記住密碼" in note and "伺服器" in note
    assert '<span id="keystat"></span>' in sec


def test_every_remember_password_note_says_the_same_place():
    sec = _sync_section()
    notes = re.findall(r'data-f="remember"> <span data-i18n="settings\.sync\.remember">記住密碼</span></label>\s*'
                       r'<span class="meta" data-i18n="(settings\.sync\.remember_[a-z]+)">([^<]*)</span>', sec)
    assert [k for k, _t in notes] == ["settings.sync.remember_coros", "settings.sync.remember_garmin",
                                      "settings.sync.remember_tp"]
    for _k, text in notes:
        assert "這台電腦" not in text, text
        assert "加密存在伺服器" in text, text
    html = SETTINGS.read_text(encoding="utf-8")
    assert "加密存在這台電腦" not in html                # the toast when ticking the box, too
    assert 't("settings.sync.remember_next")' in html


def test_password_notes_in_both_languages():
    zh, en = _cat("zh-TW", "settings"), _cat("en", "settings")
    keys = ["sync.cred_note", "sync.remember", "sync.remember_coros", "sync.remember_garmin",
            "sync.remember_tp", "sync.remember_next"]
    for k in keys:
        assert zh.get(k) and en.get(k), k
        assert not re.search(r"[一-鿿]", en[k]), k
    for k in keys[2:]:
        if k != "sync.remember":
            assert "server" in en[k], k
    assert "this computer" not in json.dumps(en)


# ---- 2. the summit km hint ----

def _em(text: str) -> float:
    """A generous width estimate: full-width characters 1 em, the rest 0.6 em."""
    return sum(1.0 if unicodedata.east_asian_width(c) in "WF" else 0.6 for c in text)


def test_the_summit_km_input_fits_its_placeholder():
    html = PLAN.read_text(encoding="utf-8")
    m = re.search(r"#summitwrap input\s*\{\s*width:\s*([\d.]+)em", html)
    assert m, "the 山頂在第幾 km input gets its own width (input[type=number] is 84px)"
    width = float(m.group(1)) - 1.4                    # padding 8px + 8px and the borders
    for loc in ("zh-TW", "en"):
        ph = _cat(loc, "plan")["ev.summit_ph"]
        assert _em(ph) <= width, (loc, ph, _em(ph), width)
    assert 'placeholder="空白＝GPX 最高點"' in html     # the zh text in the page is the catalog's
