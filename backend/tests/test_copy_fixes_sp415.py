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


# ---- SP-627: one password story on the settings page and in the manual, zh and en ----
# Not ticked: the password is sent only at login and not kept, only the sealed sign-in token is;
# ticked: the password is sealed with the server key (settings/secrets.py) into sync_state on the
# server. Never 「這台電腦」, and a 「不保存／不會保存」 only in the not-ticked sentence.

MANUAL = Path(__file__).resolve().parents[1] / "manual"
_ZH_SENT = re.compile(r"[^。；\n]+")
_EN_SENT = re.compile(r"[^.;\n]+")


def _password_texts():
    """(where, lang, text) for every place that tells the password story."""
    zh, en = _cat("zh-TW", "settings"), _cat("en", "settings")
    out = [("settings.html#sync", "zh", _sync_section())]
    for k in ("sync.cred_note", "sync.remember_coros", "sync.remember_garmin", "sync.remember_tp",
              "sync.remember_next"):
        out += [(k, "zh", zh[k]), (k, "en", en[k])]
    for page in ("start.md", "faq.md"):
        out += [(f"manual/zh-TW/{page}", "zh", (MANUAL / "zh-TW" / page).read_text("utf-8")),
                (f"manual/en/{page}", "en", (MANUAL / "en" / page).read_text("utf-8"))]
    return out


def test_sp627_never_this_computer_for_the_password():
    for where, lang, text in _password_texts():
        assert "這台電腦" not in text and "本機金鑰" not in text, where
        assert not re.search(r"this (computer|machine)|local key", text, re.I), where


def test_sp627_not_kept_only_when_not_ticked():
    """「不保存」 next to 「記住密碼」 only says what happens without it — never a flat 「不會保存」."""
    for where, lang, text in _password_texts():
        if lang == "zh":
            assert "不會保存" not in text, where
            for s in _ZH_SENT.findall(text):
                if "不保存" in s and "密碼" in s:
                    assert "沒勾" in s, (where, s)
        else:
            for s in _EN_SENT.findall(text):
                if re.search(r"password[^.;]*\bnot (kept|stored)|never (kept|stored)", s, re.I):
                    assert re.search(r"\bwithout\b|\bunticked\b", s, re.I), (where, s)


def test_sp627_ticked_means_encrypted_on_the_server_everywhere():
    zh, en = _cat("zh-TW", "settings"), _cat("en", "settings")
    note_zh, note_en = zh["sync.cred_note"], en["sync.cred_note"]
    assert "沒勾「記住密碼」" in note_zh and "不保存" in note_zh and "加密過的登入憑證" in note_zh
    assert "加密存在伺服器" in note_zh
    assert "Without “Remember password”" in note_en and "not kept" in note_en and "on the server" in note_en
    assert note_zh in _sync_section()                       # the page's zh text is the catalog's
    for loc, page in (("zh-TW", "start.md"), ("zh-TW", "faq.md")):
        text = (MANUAL / loc / page).read_text("utf-8")
        assert "沒勾" in text and "加密存在伺服器" in text and "加密過的登入憑證" in text, page
    for page in ("start.md", "faq.md"):
        text = (MANUAL / "en" / page).read_text("utf-8")
        assert "stored encrypted on the server" in text and "not kept" in text, page
        assert "remember the password" not in text, page   # the box's own label: “Remember password”


# ---- SP-629: 備份 says where backups live; the 非官方介面 consent line follows the language ----
# What the code does (api/backup.py, engine/backup.py, data_registry.py):
# - the backup folder is a path on the machine running the app — the server — checked and written
#   there (engine/backup.check_folder, create_backup); nothing is downloaded to the user's device;
# - a restore takes a backup in that folder or a file uploaded from the browser (api/backup.post_upload);
#   the current data is saved first to backups/ next to the DB on the server (api/backup._local_dir);
# - kept as they are: LOCAL_SETTINGS (backup.*, debug.*, plan.calendar, upstream.*) and sync_state's
#   local_fields — the COROS, Garmin, TrainingPeaks and intervals.icu logins;
# - FIT originals: only the missing ones are put back (engine/backup._restore_fit).
_BACKUP_KEYS = ("backup.help", "backup.restore_help", "backup.fit_help", "backup.restored",
                "backup.restored_files", "backup.restored_shares", "backup.restored_fit", "backup.restored_tail")


def _backup_section() -> str:
    html = SETTINGS.read_text(encoding="utf-8")
    m = re.search(r'<section id="backup">.*?</section>', html, re.S)
    assert m, "備份 section not found"
    return m.group(0)


def test_sp629_backup_never_says_this_computer():
    html = SETTINGS.read_text(encoding="utf-8")
    assert "這台電腦" not in _backup_section()
    js = html[html.index("// ---- 備份 (api/backup.py)"):html.index("// 自動估算的參數")]
    assert "這台電腦" not in js
    zh, en = _cat("zh-TW", "settings"), _cat("en", "settings")
    for k in _BACKUP_KEYS:
        assert zh.get(k) and en.get(k), k
        assert "這台電腦" not in zh[k] and "電腦的" not in zh[k], k
        assert not re.search(r"this (computer|machine)", en[k], re.I), k


def test_sp629_backup_help_says_where_it_lives_and_what_a_restore_keeps():
    zh, en = _cat("zh-TW", "settings"), _cat("en", "settings")
    assert "伺服器" in zh["backup.help"] and "不會下載到你的" in zh["backup.help"]
    assert "on the server" in en["backup.help"] and "not downloaded" in en["backup.help"]
    r_zh, r_en = zh["backup.restore_help"], en["backup.restore_help"]
    assert "上傳" in r_zh and "伺服器上 app 資料夾的 backups" in r_zh and "不會被覆蓋" in r_zh
    assert "upload" in r_en and "on the server" in r_en and "Not overwritten" in r_en
    for src in ("COROS", "Garmin", "TrainingPeaks", "intervals.icu"):   # every login stays (local_fields)
        assert src in r_zh and src in r_en, src
    assert "伺服器上還沒有的 FIT 檔" in zh["backup.fit_help"]
    assert "the server does not have" in en["backup.fit_help"]
    assert "伺服器上原本的" in zh["backup.restored_tail"] and "on the server" in en["backup.restored_tail"]


def test_sp629_backup_texts_are_translated_on_the_page():
    sec = _backup_section()
    zh = _cat("zh-TW", "settings")
    for k in ("backup.help", "backup.restore_help", "backup.fit_help"):
        assert f"title:settings.{k}\"" in sec, k
        assert f'title="{zh[k]}"' in sec, k                 # the page's zh text is the catalog's
    html = SETTINGS.read_text(encoding="utf-8")
    for k in ("restored", "restored_files", "restored_shares", "restored_fit", "restored_tail"):
        assert f'tBk("{k}"' in html, k


def test_sp629_consent_line_is_translated_for_coros_and_garmin():
    html = SETTINGS.read_text(encoding="utf-8")
    assert "我已閱讀並同意" not in html                     # no hard-coded Chinese after the disclaimer
    assert html.count('tSy("consent_txt"') == 2            # COROS and Garmin
    zh, en = _cat("zh-TW", "settings"), _cat("en", "settings")
    assert zh["sync.consent_txt"] == "{text}　我已閱讀並同意。"   # unchanged in Chinese
    assert en["sync.consent_txt"] == "{text} I have read and agree."
    assert not re.search(r"[一-鿿　]", en["sync.consent_txt"])


def test_sp629_the_disclaimers_are_unchanged():
    """The consent line only: neither disclaimer's text nor version moves (no re-consent)."""
    from backend.sync import coros_limits as CL, garmin_limits as GL
    assert CL.DISCLAIMER_VERSION == "coros-unofficial-v1-2026-10-12"
    assert GL.DISCLAIMER_VERSION == "garmin-unofficial-v1-2026-10-12"
    assert GL.DISCLAIMER["en"].startswith("Garmin sync is a trial: ")
    assert not re.search(r"[一-鿿]", GL.DISCLAIMER["en"] + CL.DISCLAIMER["en"])
