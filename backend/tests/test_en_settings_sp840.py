"""SP-840: the 設定 page in English — 計算模式 and its two cards, 主要訓練項目, 地圖, 資料校正 and the bad
activity files, 資料同步 (進階設定, the switch-data-source and delete dialogs, the COROS / Garmin / TrainingPeaks
login messages), the auto-estimated parameters' buttons and the LTHR / AeT estimate reasons
(engine/algorithms/threshold_estimate.py), the bad-activity reasons, the Garmin / COROS server states, the region
words. The import texts: a duplicate count's plural, and the auto-import folder takes FIT, TCX and GPX.
The Chinese page is unchanged.

Synthetic inputs only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.i18n import plural_words, use_locale

STATIC = Path(__file__).resolve().parents[1] / "static"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _cat(loc: str, ns: str = "settings") -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _body(html: str) -> str:
    body = re.sub(r"<script>window.__I18N__.*?</script>|<style>.*?</style>|<!--.*?-->", "", html, flags=re.S)
    return re.sub(r"(?m)//[^\n]*$", "", body)            # the page script's comments


# ---- the page ------------------------------------------------------------------------------------------

def test_the_page_and_its_modules_have_no_unwrapped_chinese():
    from backend.scripts import i18n_extract as X
    scans = X.scan_repo()
    for f in ("backend/static/settings.html", "backend/static/autosync.js", "backend/static/exportimport.js",
              "backend/static/setup_wizard.js", "backend/engine/algorithms/threshold_estimate.py",
              "backend/settings/repository.py", "backend/engine/bad_activity.py", "backend/sync/garmin_limits.py",
              "backend/sync/coros_limits.py", "backend/engine/region.py", "backend/engine/thresholds.py",
              "backend/api/auth.py", "backend/api/calib.py"):
        assert scans[f].unwrapped == [], (f, scans[f].unwrapped[:5])


def test_the_page_catalog_has_english_for_every_key():
    zh, en = _cat("zh-TW"), _cat("en")
    assert set(zh) == set(en)
    assert [k for k, v in en.items() if not v or CJK.search(json.dumps(v, ensure_ascii=False))] == []
    assert en["mode.title"] == "Calculation mode" and zh["mode.title"] == "計算模式"
    assert en["sport.title"] == "Main sport" and en["map.title"] == "Maps"


def test_the_english_page_shows_no_chinese():
    from backend.i18n.pages import render
    html = render("settings", "en")
    assert CJK.findall(_body(html)) == []
    for s in ("<title data-i18n=\"settings.page_title\">Settings</title>", "My formulas (everyday)",
              "Comparison mode", "Main sport", "Default base map", "Data corrections", "Scan for bad points",
              "Excluding broken activity files", "Chart data source", "Push the plan to", "Switch data source",
              "Delete synced files", "Re-estimate now", "Website login"):
        assert s in html, s


def test_the_chinese_page_is_unchanged():
    from backend.i18n.pages import render
    zh = render("settings", "zh-TW")
    for s in ("<title data-i18n=\"settings.page_title\">設定</title>", "我的算式（平常用）",
              "心率負荷（HR load）只算實際移動的時間", "對照模式", "主要訓練項目", "路跑／馬拉松", "魯地圖",
              "儲存地圖設定", "掃描壞點", "按下套用才會生效", "排除壞掉的活動檔", "圖表資料來源", "台灣以外",
              "課表推送到", "切換資料來源", "只刪這段期間（可留空＝全部）", "確認刪除", "現在重新估算",
              "決定圖表分析顯示哪些圖、自動課表怎麼排", "コース定数"):
        assert s in zh, s


def test_the_plurals_and_no_own_terms_in_the_new_english():
    en = _cat("en")
    assert plural_words(en["fixes.found"], {"n": 1}).format(n=1) == "1 spike found"
    assert plural_words(en["fixes.found"], {"n": 2}).format(n=2) == "2 spikes found"
    assert plural_words(en["purge.done"], {"files": 1, "rows": 2}).format(files=1, rows=2) == \
        "Deleted 1 file and 2 records"
    assert plural_words(en["sync.garmin_left"], {"n": 1}).format(n=1, auto=1) == "1 manual sync left today (1 automatic)"
    bad = re.compile(r"\b(TSS|IF|NP|CTL|ATL|TSB|PMC|mFTP|TIS|FRC)\b")
    new = [v for k, v in en.items() if k.split(".")[0] in ("mode", "sport", "map", "fixes", "excl", "srcdlg", "purge")]
    assert [v for v in new if bad.search(v)] == []


def test_the_purge_word_is_the_pages():
    """The delete dialog asks for 「刪除」 / "delete" — the word the page shows, which the input checks."""
    html = (STATIC / "settings.html").read_text("utf-8")
    assert 't("settings.purge.word")' in html and '"刪除"' not in html
    assert _cat("en")["purge.word"] == "delete" and _cat("zh-TW")["purge.word"] == "刪除"


# ---- the import texts (common.json) ---------------------------------------------------------------------

def test_a_duplicate_count_is_singular_for_one():
    en = _cat("en", "common")
    for k in ("fitimport.sum.duplicate", "fitimport.list.duplicate"):
        assert plural_words(en[k], {"n": 1}).format(n=1) == "1 duplicate"
        assert plural_words(en[k], {"n": 2}).format(n=2) == "2 duplicates"
    assert plural_words(en["fitfolder.badge.added"], {"n": 1}).format(n=1, name="x").startswith("Imported 1 new activity ")
    assert plural_words(en["expimport.queued_n"], {"n": 1}).format(n=1) == "Queued: 1 job ahead…"


def test_the_auto_import_folder_says_fit_tcx_gpx():
    js = (STATIC / "fitfolder.js").read_text("utf-8")
    assert '".tcx"' in js and '".gpx"' in js          # what the folder import really takes
    zh, en = _cat("zh-TW", "common"), _cat("en", "common")
    for cat in (zh, en):
        for k in ("fitfolder.intro", "fitfolder.first_note"):
            assert all(w in cat[k] for w in ("FIT", "TCX", "GPX")), (k, cat[k])
    assert "FIT 資料夾" not in json.dumps({k: v for k, v in zh.items() if k.startswith("fitfolder.")}, ensure_ascii=False)
    assert "FIT folder" not in json.dumps({k: v for k, v in en.items() if k.startswith("fitfolder.")})


# ---- the server's words -------------------------------------------------------------------------------

def test_the_lthr_estimate_reason_in_english_and_chinese():
    from backend.engine.algorithms import threshold_estimate as TE
    with use_locale("en"):
        assert TE.estimate_lthr([], 300).reason == "Only 0 runs with 30 minutes at ≥ 95% of CP (need ≥ 3)"
        one = TE.RunThreshold(160.0, 300.0, None, 0)
        assert TE.estimate_lthr([one], 300).reason == "Only 1 run with 30 minutes at ≥ 95% of CP (need ≥ 3)"
        ok = TE.estimate_lthr([one] * 3, 300)
        assert ok.value == 160 and ok.reason.startswith("Median of the average HR") and "in 3 runs" in ok.reason
    with use_locale("zh-TW"):
        assert TE.estimate_lthr([], 300).reason == "只有 0 次跑步有 30 分鐘 ≥ 95% CP 的強度（需要 ≥ 3 次）"


def test_the_aet_reasons_in_english():
    from backend.engine.algorithms import threshold_estimate as TE
    with use_locale("en"):
        assert TE.estimate_aet([]).reason == "Only 0 steady easy runs of ≥ 45 minutes (need ≥ 6)"
        assert TE.aet_aggregate([(140, 0.02, 0.01)]).reason == \
            "Only 1 usable steady-run drift (need ≥ 6): AeT needs a test"
        flat = TE.aet_aggregate([(140 + i * 0.1, 0.02, 0.01) for i in range(8)])
        assert flat.code == "flat" and not CJK.search(flat.reason)
        lb = TE.aet_lower_bound([(140, 0.01, 0.01, None)])
        assert lb["reason"] == "Only 1 easy run with clean enough data (HR ≤ LTHR − 3; need ≥ 6)"
    with use_locale("zh-TW"):
        assert TE.estimate_aet([]).reason == "只有 0 次 ≥ 45 分鐘的穩定輕鬆跑（需要 ≥ 6 次）"


@pytest.mark.parametrize("loc", ["en", "zh-TW"])
def test_estimate_aet_with_a_crossing_runs_in_both_languages(loc):
    """The full path (np.polyfit with cov: a local named `_` there once shadowed _() → UnboundLocalError)."""
    from backend.engine.algorithms import threshold_estimate as TE
    pts = [TE.DriftPoint(hr1=130 + 2 * i, drift=0.01 + 0.006 * i + (0.002 if i % 2 else -0.002), power=200)
           for i in range(10)]
    with use_locale(loc):
        ae = TE.estimate_aet(pts)
        lt = TE.estimate_lthr([TE.RunThreshold(160.0, 300.0, None, 0)] * 4, 300)
    assert ae.value is not None and ae.se is not None, ae.reason
    assert lt.value == 160
    if loc == "en":
        assert ae.reason.startswith("10 steady easy runs: drift rises") and not CJK.search(ae.reason + lt.reason)
    else:
        assert ae.reason.startswith("10 次穩定輕鬆跑：心率每 +10 bpm 飄移") and lt.reason.startswith("4 次跑步的最佳 30 分鐘")


def test_the_bad_activity_reason_is_stored_in_chinese_and_shown_in_english():
    from backend.engine import bad_activity as BA
    from backend.engine.stored_text import shown
    f = {"avg_kmh": 43.0, "moving_s": 1000.0, "best": {}, "avg_power": None, "power_n": 0}
    with use_locale("en"):
        auto = BA.judge(f, "run")
        ex = BA.decide(auto, None)
        assert str(auto["reason"]) == "疑似交通工具／騎車（均速 43 km/h）"     # the Dataset keeps zh-TW
        assert shown(auto["reason"]) == "Looks like a vehicle / bike (average 43 km/h)"
        assert shown(ex["label"]) == "Excluded: Looks like a vehicle / bike (average 43 km/h)"
        man = BA.decide(None, BA.EXCLUDE)
        assert shown(man["label"]) == "Excluded: Excluded by hand"
        w = BA.judge({"avg_kmh": 10.0, "moving_s": 3000.0, "best": {"60": [50.0, 600.0]}}, "run")
        assert shown(w["reason"]) == \
            "Looks like a vehicle / bike (minutes 10–11: 1 min in a row at 50 km/h, 10 km/h on average overall)"
    with use_locale("zh-TW"):
        assert shown(w["reason"]) == "疑似交通工具／騎車（第 10–11 分鐘連續 1 分鐘 50 km/h，全程均速 10 km/h）"
        assert shown(ex["label"]) == "已排除：疑似交通工具／騎車（均速 43 km/h）"
        assert BA.decide(auto, BA.EXCLUDE)["label"] == "已排除：疑似交通工具／騎車（均速 43 km/h）（手動）"


def test_the_garmin_server_state_in_english(monkeypatch):
    from backend.sync import garmin_limits as GL
    monkeypatch.setattr(GL, "available", lambda: False)
    with use_locale("en"):
        st = GL.status()
        assert st["state"] == "unavailable" and not CJK.search(st["message"]), st["message"]
    with use_locale("zh-TW"):
        assert GL.status()["message"].startswith("這台伺服器沒有安裝 Garmin 同步需要的套件")


def test_the_region_words_in_english():
    from backend.engine import region as RG
    from backend.i18n import _
    with use_locale("en"):
        assert _(RG.LABEL["intl"]) == "Outside Taiwan" and _("瀏覽器時區") == "browser time zone"
        assert _("至少要有一天可以練") == "At least one day must be open for training"
    with use_locale("zh-TW"):
        assert _(RG.LABEL["tw"]) == "台灣"
