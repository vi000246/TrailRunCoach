"""SP-391 / SP-497: the screens use our own words, not TrainingPeaks' registered trademarks (TSS®, IF®, NP®)
or the names TrainingPeaks / WKO5 promote (CTL, ATL, TSB, PMC, hrTSS, rTSS, mFTP, TIS, FRC, dFRC).

zh 「負荷（Load）」, en "Load" and so on (docs/research/trademark-terms.md §4, the owner's answers of
2026-10-13). The old names are left only where they point at the other party:

  * the two comparisons (§5.1): the TrainingPeaks source label 「TrainingPeaks 算的負荷（TSS®）」 and
    the load & fitness chart's ? help — they carry the ® and the trademark notice;
  * 「其他平台常寫成 CTL、ATL、TSB」 once, in the fitness chart's help;
  * values read from the athlete's own comparison folder (the comparison mode), named as that software names them.

Scanned: the frontend catalogs (zh-TW, en), every backend msgid in the code and its English, the bundled
views (names, titles, descriptions) and their English sidecar, and the Chinese UI strings not wrapped
yet (HTML / JS / Python). A new string with one of the old names fails here — write the new word, or,
for one of the cases above, add it to ALLOWED with the reason."""
import json
import re

import pytest

from backend.scripts import i18n_extract as X

OLD = re.compile(r"(?<![A-Za-z])(TSS|hrTSS|rTSS|CTL|ATL|TSB|PMC|mFTP|dFRC|FRC|TIS|NP|IF)(?![A-Za-z®])"
                 r"|新鮮度|Form%|刺激分數")

# SP-497 (owner 2026-10-14, after Garmin's 繁中): SP-391's words as METRIC names were replaced —
# 長期訓練負荷（Chronic load）, 短期負荷（Acute load）, 負荷平衡（Load balance）, 訓練負荷（Training load）,
# 訓練負荷圖, 長期負荷週增量, 有氧／無氧訓練效果, 無氧容量, 無氧儲備. Only those uses are caught: 「體能」
# 「疲勞」「狀況」 in their everyday sense (百岳的體能估算, 疲勞保護, 狀況卡, the zone 過度疲勞) are fine.
SUPERSEDED = re.compile(
    r"體能（Fitness）|疲勞（Fatigue）|狀況（Form）|狀況 %|負荷（Load）|體能圖|體能週增量|負荷與體能|起始體能"
    r"|疲勞 ÷ 體能|有氧／無氧效果|(?<!訓練)有氧效果|(?<!訓練)無氧效果|W′ 電池"
    r"|Fitness chart|Fitness ramp|Load & fitness|Form %|W′ balance|fatigue ÷ fitness|[Ss]tarting fitness"
    r"|(?i:aerobic effect)|(?i:anaerobic effect)")
# a whole label; not "Load" (the chip prefix 「Load 52 / 60」, the watch's 「負荷 80」 step) nor 「狀況」
# (the overview's 訓練狀況 section link)
SUPERSEDED_LABELS = {"Fitness", "Fatigue", "Form", "體能", "疲勞"}

# a string containing one of these is an allowed use of the old name
ALLOWED = (
    "TSS®",                                       # the comparison (§5.1) with the ® and the notice
    "其他平台常寫成 CTL、ATL、TSB",                 # the fitness chart's help, once
    "Other platforms often call these CTL, ATL and TSB",
    "Daily % of CTL",                             # the name of an imported chart (views/wko5_fixes.json)
    "對照資料夾的快照 CTL",                          # the comparison folder's own PMC snapshot (comparison mode only)
    "Comparison folder snapshot CTL",
    "對照資料夾的 mFTP（資料夾存的快照）",            # the mFTP stored in the comparison folder (comparison mode)
    "對照資料夾今天存的 mFTP",                      # the backtest: what it does NOT use (the folder's value)
    "對照資料夾的 mFTP",                            # the zone / threshold source label in the comparison mode
    "mFTP {w} W",                                 # panels/cp_ref.py: the line label when the source is 「對照資料夾的 mFTP」
)

VIEW_TEXT = ("name", "title", "description", "label", "section", "power_note")


def _bad(s: str) -> bool:
    if SUPERSEDED.search(s) or s.strip() in SUPERSEDED_LABELS:
        return True
    return bool(OLD.search(s)) and not any(a in s for a in ALLOWED)


def _view_texts(x, out: list):
    if isinstance(x, dict):
        for k, v in x.items():
            if isinstance(v, str):
                if k in VIEW_TEXT:
                    out.append(v)
            else:
                _view_texts(v, out)
    elif isinstance(x, list):
        for v in x:
            _view_texts(v, out)
    return out


def _all_strings(x, out: list):
    if isinstance(x, dict):
        for k, v in x.items():
            if k != "_about":
                out.append(k)
                _all_strings(v, out)
    elif isinstance(x, list):
        for v in x:
            _all_strings(v, out)
    elif isinstance(x, str):
        out.append(x)
    return out


@pytest.fixture(scope="module")
def scans():
    return X.scan_repo()


def test_frontend_catalogs_use_our_own_words():
    bad = [f"{loc}/{ns}.{k}: {v[:80]!r}" for loc, nss in X.frontend_catalogs().items()
           for ns, d in nss.items() for k, v in d.items() if isinstance(v, str) and _bad(v)]
    assert bad == []


def test_backend_sentences_and_their_english_use_our_own_words(scans):
    bad = [f"{p}:{ln}: {m[:80]!r}" for p, fs in scans.items() for ln, m in fs.msgids if _bad(m)]
    en = json.loads((X.LOCALES / "en.json").read_text("utf-8"))
    bad += [f"en.json: {k[:40]!r} → {v[:80]!r}" for k, v in en.items() if isinstance(v, str) and _bad(v)]
    assert bad == []


def test_bundled_views_and_their_english_use_our_own_words():
    bad = []
    for stem in ("training", "periodization", "workout"):
        texts = _view_texts(json.loads((X.VIEWS / f"{stem}.json").read_text("utf-8")), [])
        bad += [f"views/{stem}: {s[:80]!r}" for s in texts if _bad(s)]
    side = _all_strings(json.loads((X.VIEWS / "i18n" / "en.json").read_text("utf-8")), [])
    bad += [f"views/i18n/en.json: {s[:80]!r}" for s in side if _bad(s)]
    assert bad == []


def test_unwrapped_chinese_ui_strings_use_our_own_words(scans):
    bad = [f"{p}:{ln}: {s[:80]!r}" for p, fs in scans.items() for ln, s in fs.unwrapped if _bad(s)]
    assert bad == []


def test_the_comparisons_carry_the_trademark_notice():
    cat = X.frontend_catalogs()
    for loc, notice in (("zh-TW", "TSS® 是 TrainingPeaks, LLC 的註冊商標"),
                        ("en", "TSS® is a registered trademark of TrainingPeaks, LLC")):
        assert "TSS®" in cat[loc]["compare"]["tss_tp"] and notice in cat[loc]["compare"]["tss_tip"], loc
        assert "TSS®" in cat[loc]["viewer"]["load_src.trainingpeaks"] and notice in cat[loc]["viewer"]["load_src.tm"], loc
        assert notice in cat[loc]["overview"]["pmc.tip"], loc
    assert "其他平台常寫成 CTL、ATL、TSB" in cat["zh-TW"]["overview"]["pmc.tip"]
    assert "Other platforms often call these CTL, ATL and TSB" in cat["en"]["overview"]["pmc.tip"]


def test_the_guard_catches_the_old_names():
    assert _bad("TSS 64") and _bad("體能 CTL") and _bad("狀況 Form%") and _bad("mFTP 線") and _bad("Normalized NP")
    assert not _bad("負荷 64") and not _bad("TrainingPeaks 算的訓練負荷（TSS®）")
    assert not _bad("tss_source") and not _bad("STSS") and _bad("刺激分數 3")
    assert not _bad("有氧／無氧訓練效果（Aerobic / Anaerobic training effect）") and not _bad("對照資料夾的 mFTP")


def test_the_guard_catches_sp391_words_replaced_by_sp497():
    for s in ("體能（Fitness）54", "疲勞（Fatigue）", "狀況（Form）−25", "狀況 %（Form %）", "負荷（Load）完成度", "體能圖",
              "體能週增量（Fitness ramp）", "負荷與體能（Load & fitness）", "有氧／無氧效果", "有氧效果（Aerobic effect）2",
              "W′ 電池（W′ balance）", "Fitness chart", "Load ratio fatigue ÷ fitness", "Aerobic effect 2", "Fitness", "體能"):
        assert _bad(s), s
    for s in ("長期訓練負荷（Chronic load）54", "短期負荷（Acute load）", "負荷平衡（Load balance）−25", "負荷平衡 %（Load balance %）",
              "訓練負荷（Training load）完成度", "訓練負荷圖（Training load chart）", "長期負荷週增量（Chronic load ramp）",
              "有氧訓練效果（Aerobic training effect）2", "無氧儲備（Anaerobic reserve）", "無氧容量（Anaerobic capacity）",
              "百岳／多日縱走：背重、高海拔、連續多天的體能與配速估算", "疲勞保護", "狀況卡", "過度疲勞", "Load ratio (acute ÷ chronic)",
              "Load", "狀況", "負荷 80"):
        assert not _bad(s), s
