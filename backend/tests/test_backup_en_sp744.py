"""SP-744: the settings page's backup section in English (title, buttons, states, list, restore dialog, error
messages) — and after a restore a page reload is enough: the server empties its in-memory caches
(api/backup.after_restore → drop_caches), so the text no longer asks to restart the app.

Repo files and the backup engine on temporary folders only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.i18n import use_locale
from backend.scripts import i18n_extract as X

STATIC = Path(__file__).resolve().parents[1] / "static"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _html() -> str:
    return (STATIC / "settings.html").read_text("utf-8")


def _part(s: str, start: str, end: str) -> str:
    a = s.index(start)
    return s[a:s.index(end, a)]


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "settings.json").read_text("utf-8"))


def test_backup_section_dialog_and_script_have_no_hardcoded_chinese():
    s = _html()
    sec = _part(s, '<section id="backup"', "</section>")
    dlg = _part(s, '<dialog id="restore-dlg"', "</dialog>")
    js = _part(s, "// ---- 備份 (api/backup.py)", "// 自動估算的參數")
    assert [m for _l, m in X.scan_html(STATIC / "x.html", sec).unwrapped] == []
    assert [m for _l, m in X.scan_html(STATIC / "x.html", dlg).unwrapped] == []
    assert [m for _l, m in X.scan_js_source("x.js", js).unwrapped] == []


def test_backup_keys_exist_in_both_languages():
    s = _html()
    keys = set(re.findall(r'settings\.(backup\.\w+)', s)) | {f"backup.{k}" for k in re.findall(r'tBk\("(\w+)"', s)}
    keys |= {f"backup.{k}" for k in ("cloud_found", "cloud_none", "pill_none", "pill_ok", "pill_failed", "dir_set",
                                     "dir_unset", "check_ok")}
    zh, en = _cat("zh-TW"), _cat("en")
    missing = sorted(k for k in keys if not zh.get(k) or not en.get(k))
    assert missing == [], missing
    def texts(v):
        return list(v.values()) if isinstance(v, dict) else [v]
    assert [k for k in keys if any(CJK.search(t) for t in texts(en[k]))] == []
    assert en["backup.confirm_word"] == "restore" and zh["backup.confirm_word"] == "還原"


def test_restore_texts_ask_for_a_reload_not_a_restart():
    zh, en = _cat("zh-TW"), _cat("en")
    for cat in (zh, en):
        for k in ("backup.restore_help", "backup.restored_tail", "backup.restored_toast"):
            assert "重新啟動 App" not in cat[k] and "Restart the app" not in cat[k], k
    assert "重新整理頁面" in zh["backup.restored_tail"] and "Reload the page" in en["backup.restored_tail"]
    assert "重新啟動 App" not in _html()


def test_after_restore_empties_the_caches():
    from backend.api import backup as AB
    from backend.api import overview as OV
    from backend.engine import activity_tags
    OV._status_cache["x"] = 1
    activity_tags._memo["x"] = 1
    assert AB.drop_caches() == []                       # every listed cache exists and was emptied
    assert "x" not in OV._status_cache and "x" not in activity_tags._memo


def test_backup_errors_in_english(tmp_path):
    from backend.engine import backup as B
    with use_locale("en"):
        with pytest.raises(B.BackupError) as e:
            B.check_folder("relative/path")
        assert str(e.value).startswith("Type the full path (for example ")
        with pytest.raises(B.BackupError) as e:
            B.open_backup(tmp_path / "none.zip", tmp_path / "w")
        assert str(e.value) == "Backup file not found"
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        with pytest.raises(B.BackupError) as e:
            B.open_backup(bad, tmp_path / "w")
        assert not CJK.search(str(e.value)), str(e.value)
    with pytest.raises(B.BackupError) as e:            # Chinese unchanged
        B.open_backup(tmp_path / "none.zip", tmp_path / "w")
    assert str(e.value) == "找不到備份檔"
