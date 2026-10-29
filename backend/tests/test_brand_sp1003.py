"""SP-1003: what leaves the app or shows up rarely names the product Trig (SP-971): the calendar feed's
name, a backup's suggested folder and its "not ours" error, the AI explainer's own name, an exported
GPX's creator. The names come from the one key (pages.brand / current_brand). Old backups — made in
the old 「TrailRunCoach Backups」 folder, their manifest saying "app": "TrailRunCoach" — restore as before.
Synthetic only: tmp_path folders, no WKO5 folder, no ~/.wko5coach, no model call."""
from __future__ import annotations

import datetime as dt
import json
import zipfile

import pytest

from backend import i18n
from backend.ai import qa
from backend.engine import backup as B, calendar_feed as CF
from backend.engine.racepower import gpx
from backend.tests.test_backup import count, make_db
from backend.tests.test_calendar_feed import TODAY, parse, sess

OLD = "TrailRunCoach"


def test_a_new_calendar_subscription_is_named_trig():
    rows = [(sess("u1", "2026-10-05", "閾值 3×10 分", "quality", 60), dt.datetime(2026, 10, 1, 8, 0, 0))]
    for loc, name in (("zh-TW", "課表（Trig 基石）"), ("en", "Training plan (Trig)")):
        with i18n.use_locale(loc):
            text = CF.build(rows, TODAY, "https://trc.example.org", now=dt.datetime(2026, 10, 4, 1, 2, 3))
        cal, ev = parse(text)
        assert cal["X-WR-CALNAME"] == name, loc
        assert cal["PRODID"] == "-//Trig//Plan calendar//EN"
        assert OLD not in text.replace("u1@trailruncoach", "")
        assert ev[0]["UID"] == "u1@trailruncoach"          # kept: a new UID would duplicate subscribed events


def test_a_backup_in_the_old_folder_restores(tmp_path):
    old_folder = tmp_path / "cloud" / "TrailRunCoach Backups"
    r = B.create_backup(make_db(tmp_path / "app.db", rows=4), old_folder)
    assert [b["name"] for b in B.list_backups(old_folder)] == [r["name"]]
    with zipfile.ZipFile(r["path"]) as z:
        assert json.loads(z.read(B.MANIFEST))["app"] == OLD    # the format's name, unchanged
    live = make_db(tmp_path / "live.db", rows=1)
    B.restore(r["path"], live, local_dir=tmp_path / "local")
    assert count(live) == 4


def test_the_suggested_folder_is_trig_unless_the_old_one_is_there(tmp_path):
    prof = tmp_path / "win"
    fresh, used = prof / "OneDrive", prof / "Dropbox"
    fresh.mkdir(parents=True)
    (used / B.OLD_SUBDIR).mkdir(parents=True)                # backed up there before the rename
    found = {f["id"]: f["path"] for f in B.detect_cloud_folders(env={"USERPROFILE": str(prof)}, home=prof,
                                                                platform="win32", drives=())}
    assert found["onedrive"] == str(fresh / "Trig Backups")
    assert found["dropbox"] == str(used / "TrailRunCoach Backups")
    assert not (fresh / "Trig Backups").exists()             # detection never creates anything
    with pytest.raises(B.BackupError, match=r"OneDrive\\Trig Backups"):
        B.check_folder("relative/path")


def test_a_backup_of_another_app_is_refused_in_trigs_name(tmp_path):
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr(B.MANIFEST, json.dumps({"app": "SomethingElse", "format": 1}))
        z.writestr(B.DB_ENTRY, b"")
    live = make_db(tmp_path / "live.db")
    for loc, msg in (("zh-TW", "這不是 Trig 基石的備份"), ("en", "This is not a Trig backup")):
        with i18n.use_locale(loc):
            with pytest.raises(B.BackupError) as e:
                B.restore(other, live, local_dir=tmp_path / "local")
            assert str(e.value) == msg, loc


def test_the_ai_explainer_calls_itself_trig():
    zh, en = qa.system_prompt("zh-TW"), qa.system_prompt("en")
    assert zh.startswith("You are the explainer inside Trig 基石,") and "say you are Trig 基石's explainer" in zh
    assert en.startswith("You are the explainer inside Trig,") and "say you are Trig's explainer" in en
    assert "Answer in Traditional Chinese as written in Taiwan" in zh and "Answer in English" in en
    assert qa.system_prompt("fr") == zh                      # an unknown locale: the default's
    for p in (zh, en):
        assert OLD not in p and "{brand}" not in p and "{lang}" not in p


def test_an_exported_gpx_is_made_by_trig():
    track = gpx.Track([25.0, 25.001], [121.5, 121.501], [100.0, 110.0], None, [])
    text = gpx.write_gpx(track, "test")
    assert 'creator="Trig"' in text and OLD not in text
