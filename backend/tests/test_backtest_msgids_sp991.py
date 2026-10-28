"""SP-991: the stored back-test keeps its texts as msgid + params, shown in the reader's language.

- Inside backtest() / capacity_backtest() every _() text keeps its msgid (i18n.keep_msgids); save() keeps
  the texts as the Chinese strings and writes a table {zh-TW text: {"$msg": msgid, "$p": params}} beside them
  under text_version 2; load() reads such a text back as an i18n.Msg (a str with the same Chinese to every
  reader) and shows it in English from the msgid.
- A file without text_version (older) is read and shown as before (SP-838: the Chinese read back).
- The automatic effort reasons (activity_tags, f-strings until now) are msgids kept in zh-TW (i18n.S_):
  the same Chinese, English when shown — on the demo athlete nothing in the English result is Chinese.

Synthetic data only: the demo athlete (backend/demo/build.py, small mode) built into tmp_path."""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from backend.i18n import M_, Msg, S_, _, keep_msgids, relocalize, use_locale

CJK = re.compile(r"[㐀-鿿]")
KEEP = re.compile(r"\.(name|file|files|title|peaks|event|fit_files|path)\b")


def _texts(x, path=""):
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _texts(v, f"{path}.{k}")
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _texts(v, f"{path}[]")


def _plain(x):
    """The tree with plain strings (an older file: no msgids)."""
    return json.loads(json.dumps(x, ensure_ascii=False, default=float))


# ---- i18n: keep_msgids, S_ ----------------------------------------------------------------------------

def test_keep_msgids_makes_every_text_remember_its_msgid():
    t = _("賽前 {n} 天內不排間歇", n=5)
    assert not isinstance(t, Msg) and t == "賽前 5 天內不排間歇"
    with keep_msgids():
        m = _("賽前 {n} 天內不排間歇", n=5)
    assert isinstance(m, Msg) and m == t and m.msgid == "賽前 {n} 天內不排間歇" and m.params == {"n": 5}
    assert relocalize(m, "en") == "no intervals within 5 days"
    assert not isinstance(_("賽前 {n} 天內不排間歇", n=5), Msg)          # only inside the block


def test_s_keeps_the_chinese_in_an_english_request():
    with use_locale("en"):
        s = S_("{low:.0%} 時間低於 AeT", low=0.89)
    assert s == "89% 時間低於 AeT" and relocalize(s, "en") == "89% of the time below AeT"


# ---- the automatic effort reasons ----------------------------------------------------------------------

def test_effort_reasons_are_the_same_chinese_and_read_in_english():
    from backend.engine import activity_tags as AT
    easy = AT.effort_hr({"hr_avg": 138, "above_aet": 0.1, "low_share": 0.89, "rest_share": 0.0}, 170, 145)
    assert easy["effort"] == "easy" and easy["reason"] == "89% 時間低於 AeT，平均 138 bpm"
    assert relocalize(easy["reason"], "en") == "89% of the time below AeT, average 138 bpm"
    hard = AT.effort_hr({"hr_avg": 160, "above_aet": 0.9, "low_share": 0.0, "rest_share": 0.2}, 170, 145)
    assert hard["effort"] == "hard_with_rests"
    assert hard["reason"].startswith("移動心率 94% LTHR、AeT 以上 90%，但長休息 20%（> ")
    assert not CJK.search(relocalize(hard["reason"], "en"))
    mx = AT.effort_hr({"hr_avg": 160, "above_aet": 0.9, "low_share": 0.0, "rest_share": 0.02}, 170, 145)
    assert mx["reason"] == "移動心率 94% LTHR（≥ 90%）、AeT 以上 90%（≥ 67%）、長休息 2%"
    assert relocalize(mx["reason"], "en") == "moving HR 94% of LTHR (≥ 90%), 90% above AeT (≥ 67%), long rests 2%"
    road = AT.effort_road({"ok": False, "reason": "7.03 km 不在 5K/10K/半馬/全馬 ±10 %"}, {"hr_avg": 160, "low_share": 0.1}, 145)
    assert road["reason"] == "未達路跑全力條件：7.03 km 不在 5K/10K/半馬/全馬 ±10 %"
    rpe = AT.effort_from_rpe(9.5, 0.02, {"effort": "moderate"})
    assert rpe["reason"] == "手錶記錄的 RPE 9.5（運動後自評）→ 全力；心率規則會判為「一般」"
    assert relocalize(rpe["reason"], "en") == ("RPE 9.5 recorded by the watch (post-run self-rating) → all-out; "
                                               "the heart-rate rule would say “Normal”")


# ---- the store: format 2, an older file ----------------------------------------------------------------

def test_a_text_stored_with_its_msgid_comes_back_as_the_same_chinese_and_shows_in_english(tmp_path, monkeypatch):
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(BT, "STORE", tmp_path / "bt.json")
    with use_locale("zh-TW"), keep_msgids():
        note = _("賽事下坡多，賽前 {n} 天內不跑長跑", n=14)
        res = {"version": 2, "notes": [note, "自己的字"], "rows": [{"label": "x", "error": _("沒有 GPS 軌跡")}],
               "nested": {"why": M_("{a}；{b}", a=_("沒有 CP"), b=_("沒有功率"))}}
    BT.save(res)
    raw_file = json.loads((tmp_path / "bt.json").read_text("utf-8"))
    assert raw_file["text_version"] == BT.TEXT_VERSION
    # the texts stay the Chinese strings; the msgids and params are in the table beside them
    assert raw_file["notes"] == ["賽事下坡多，賽前 14 天內不跑長跑", "自己的字"]
    assert raw_file["texts"]["賽事下坡多，賽前 14 天內不跑長跑"] == {"$msg": "賽事下坡多，賽前 {n} 天內不跑長跑", "$p": {"n": 14}}
    assert "自己的字" not in raw_file["texts"]                       # a plain text has no row
    assert raw_file["texts"]["沒有 CP；沒有功率"]["$p"]["a"] == {"$msg": "沒有 CP", "$p": {}, "$t": "沒有 CP"}
    raw = BT.load(raw=True)
    assert raw["notes"][0] == "賽事下坡多，賽前 14 天內不跑長跑" and isinstance(raw["notes"][0], Msg)
    assert raw["nested"]["why"] == "沒有 CP；沒有功率"
    assert BT.load() == raw                                            # zh-TW: as stored
    with use_locale("en"):
        en = BT.load()
    assert en["notes"][0] == "a race with long descents: no long run within 14 days of it"
    assert en["rows"][0]["error"] == "No GPS track"
    assert not CJK.search(en["nested"]["why"])
    assert en["notes"][1] == "自己的字"                                # not one of the app's texts: as written
    # saving what was read keeps the format (store_capacity reads and writes the file)
    BT.save(raw)
    assert json.loads((tmp_path / "bt.json").read_text("utf-8")) == raw_file
    # a result with no text made from a msgid is stored as it is (no text_version, no table)
    BT.save({"version": 2, "notes": ["自己的字"]})
    assert json.loads((tmp_path / "bt.json").read_text("utf-8")) == {"version": 2, "notes": ["自己的字"]}


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory):
    from backend.demo import build as B
    r = tmp_path_factory.mktemp("demo-sp991")
    B.build(r, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return r


@pytest.fixture()
def ds(demo_root, monkeypatch):
    from backend.tests import demo_fixtures as F
    F.demo_env(monkeypatch, demo_root)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    return wko5views._dataset()


def test_the_demo_back_test_is_english_and_an_older_file_still_reads(ds, tmp_path, monkeypatch):
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(BT, "STORE", tmp_path / "bt.json")
    with use_locale("en"):                    # an English request starts it: the file still holds zh-TW
        r = BT.backtest(ds)
    BT.save(r)
    BT.store_capacity(BT.capacity_backtest(ds, boot_reps=20))
    stored = json.loads((tmp_path / "bt.json").read_text("utf-8"))
    assert stored["text_version"] == BT.TEXT_VERSION and len(stored["texts"]) > 50
    raw = BT.load(raw=True)
    assert raw["version"] == 2 and raw["notes"][-1].startswith("心率能力（推估）")
    assert BT.flags()[0].keys() == BT.THRESHOLDS.keys()
    with use_locale("en"):
        en = BT.load()
    left = [(p, t) for p, t in _texts(en) if CJK.search(t) and not KEEP.search(p)]
    assert left == [], left[:10]
    assert en["notes"][-1].startswith("Heart-rate capacity (estimate)")
    # the effort reasons, stored with their msgids
    reasons = [x["effort_reason"] for x in raw["rows"] if x.get("effort_reason")]
    assert reasons and all(isinstance(x, Msg) for x in reasons)
    # an older file (no text_version, plain strings) reads and shows as before
    old = _plain(raw)
    old.pop("text_version")
    old.pop("texts")
    (tmp_path / "old.json").write_text(json.dumps(old, ensure_ascii=False), "utf-8")
    assert BT.load(tmp_path / "old.json", raw=True) == old
    with use_locale("en"):
        en_old = BT.load(tmp_path / "old.json")
    assert en_old["notes"][-1].startswith("Heart-rate capacity (estimate)")
    left = [(p, t) for p, t in _texts(en_old["notes"]) if CJK.search(t)]
    assert left == [], left
