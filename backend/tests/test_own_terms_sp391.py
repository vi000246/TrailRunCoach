"""SP-391: the screens use our own words, not TrainingPeaks' registered trademarks (TSS®, IF®, NP®)
or the names TrainingPeaks / WKO5 promote (CTL, ATL, TSB, PMC, hrTSS, rTSS, mFTP, TIS, FRC, dFRC).

zh 「負荷（Load）」, en "Load" and so on (docs/research/trademark-terms.md §4, the owner's answers of
2026-10-13). The old names are left only where they point at the other party:

  * the two comparisons (§5.1): the TrainingPeaks source label 「TrainingPeaks 算的負荷（TSS®）」 and
    the load & fitness chart's ? help — they carry the ® and the trademark notice;
  * 「其他平台常寫成 CTL、ATL、TSB」 once, in the fitness chart's help;
  * values read from the athlete's own WKO5 files (the WKO5 comparison mode), named as WKO5 names them.

Scanned: the frontend catalogs (zh-TW, en), every backend msgid in the code and its English, the bundled
views (names, titles, descriptions) and their English sidecar, and the Chinese UI strings not wrapped
yet (HTML / JS / Python). A new string with one of the old names fails here — write the new word, or,
for one of the cases above, add it to ALLOWED with the reason."""
import json
import re

import pytest

from backend.scripts import i18n_extract as X

OLD = re.compile(r"(?<![A-Za-z])(TSS|hrTSS|rTSS|CTL|ATL|TSB|PMC|mFTP|dFRC|FRC|TIS|NP|IF)(?![A-Za-z®])"
                 r"|新鮮度|Form%"
                 r"|刺激分數|訓練效果|[Tt]raining [Ee]ffect")   # TIS = 有氧／無氧效果 (owner 2026-10-14);
                                                             # Training Effect is Firstbeat's trademark

# a string containing one of these is an allowed use of the old name
ALLOWED = (
    "TSS®",                                       # the comparison (§5.1) with the ® and the notice
    "其他平台常寫成 CTL、ATL、TSB",                 # the fitness chart's help, once
    "Other platforms often call these CTL, ATL and TSB",
    "Daily % of CTL",                             # the name of a WKO5 chart (「類似 WKO5 的 Daily % of CTL」)
    "WKO5 快照 CTL",                               # the WKO5 file's own PMC snapshot (comparison mode only)
    "WKO5 snapshot CTL",
    "WKO5 模型 mFTP（WKO5 存的快照）",               # the mFTP stored in the WKO5 file (comparison mode)
    "WKO5 今天存的 mFTP",                           # the backtest: what it does NOT use (the WKO5 file's value)
    "WKO5 mFTP",                                  # the zone / threshold source label in the WKO5 mode
    "mFTP {w} W",                                 # panels/cp_ref.py: the line label when the source is "WKO5 mFTP"
    "WKO5 的 TIS",                                 # 有氧／無氧效果's source: 「算法參考 WKO5 的 TIS」 (owner 2026-10-14)
    "WKO5's TIS",
)

VIEW_TEXT = ("name", "title", "description", "label", "section", "power_note")


def _bad(s: str) -> bool:
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
    assert not _bad("負荷（Load）64") and not _bad("TrainingPeaks 算的負荷（TSS®）")
    assert not _bad("tss_source") and not _bad("STSS")
    assert _bad("刺激分數 3") and _bad("訓練效果 3.2") and _bad("Training Effect 3.2")
    assert not _bad("有氧／無氧效果（Aerobic / Anaerobic effect）") and not _bad("算法參考 WKO5 的 TIS")
