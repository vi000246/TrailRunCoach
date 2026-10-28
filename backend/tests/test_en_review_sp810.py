"""SP-810: the single-activity review from the server (engine/workout_review.py, engine/interval_eval.py)
in English — the summary / drift / interval / climb / form cards, the drift refusals (drift_of's
reasons), the temperature band, the CP / AeT test bouts — and the zh-TW text unchanged.

drift_of's reasons live in the measure cache every language shares: they are written in zh-TW (other
code reads their words: short_reason, scripts/validation/gc_drift) and shown through reason_text()
(stored_text: the msgid filled in with the same values). Synthetic data only (no WKO5 folder, no
~/.wko5coach)."""
import datetime as dt
import re

import numpy as np
import pytest

from backend.engine import cp_protocols as CPP
from backend.engine import interval_eval as IE
from backend.engine import session_stimulus as SS
from backend.engine import workout_review as R
from backend.i18n import translate, use_locale
from backend.scripts import i18n_extract as X
from backend.tests.test_climb_profile import SETTINGS as TRAIL_SETTINGS
from backend.tests.test_climb_profile import _trail
from backend.tests.test_interval_eval import _cp_test_run, _ds, _v1a_run
from backend.tests.test_quality_gate import TODAY as QT
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 9, 30)
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
FILES = ("backend/engine/workout_review.py", "backend/engine/interval_eval.py")


def _strings(x, out=None, skip=("classification",)):
    out = [] if out is None else out
    if isinstance(x, dict):
        for k, v in x.items():
            if k not in skip:
                _strings(v, out, skip)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _strings(v, out, skip)
    elif isinstance(x, str):
        out.append(x)
    return out


# ---- the package's files ---------------------------------------------------------------------------

def test_package_files_have_no_unwrapped_chinese_and_every_msgid_has_english():
    scans = X.scan_repo()
    en = X.load_json(X.LOCALES / "en.json", {})
    for f in FILES:
        assert scans[f].unwrapped == [], f
        missing = sorted({m for _l, m in scans[f].msgids if not en.get(m)})
        assert not missing, f"{f}: no en for {missing[:5]}"


# ---- drift_of's reasons: zh-TW in the cache, English on screen ---------------------------------------

REASONS = [  # (msgid, sample values as drift_of fills them in)
    (R.SHORT_REASON, dict(m=34, lim=40)), (R.TOO_SHORT_REASON, dict(m=1, lim=30)), (R.NO_HR_REASON, {}),
    (R.NOT_RUN_REASON, {}), (R.HILL_REASON, {}), (R.STOP_REASON, dict(t="5:00")),
    (R.WALK_REASON, dict(t="3:20", frac=75.0, m=3)), (R.VI_REASON, dict(vi=1.0712, lim=1.04)),
    (R.INTENSITY_REASON, dict(pct=93.4)), (R.NO_DATA_REASON, {}), (R.NO_POWER_REASON, {}),
    (R.COVER_REASON, dict(pct=81.2, lim=95.0)), (R.POWER_DATA_REASON, {}),
    *((m, dict(pct=7.6, lim=5.0)) for m in R.HALVES_REASON.values()),
    *((m, dict(pct=8.1, lim=5.0)) for m in R.FINISH_REASON.values()),
]


@pytest.mark.parametrize("msgid,params", REASONS)
def test_every_drift_refusal_reads_back_in_english(msgid, params):
    zh = R._zh(msgid, **params)
    assert not CJK.search(translate(msgid, "en", **params))
    with use_locale("en"):
        assert R.reason_text(zh) == translate(msgid, "en", **params)
    with use_locale("zh-TW"):
        assert R.reason_text(zh) == zh                      # the source language: as stored


def test_reasons_are_cached_in_zh_tw_whatever_the_request_language():
    # a hilly trail run's drift is refused; computed first during an English request, the cached
    # reason is still the zh-TW text short_reason reads (and the English one only on screen)
    ds = FakeDataset([_trail(TODAY)], TODAY, settings=TRAIL_SETTINGS)
    w = ds.workouts[0]
    with use_locale("en"):
        dr = R.measure(ds, w)["drift"]
        assert dr["reason"] == "有坡（越野或每公里爬升 ≥ 20 m），飄移數字不採用"
        d, why = R.basis_drift(dr, "pace")
        assert d is None and why == "Hilly (trail, or ≥ 20 m of climbing per km): drift not used"
        assert R.short_reason(dr) == translate("有坡（越野）", "en")
    with use_locale("zh-TW"):
        assert R.basis_drift(dr, "pace")[1] == dr["reason"] and R.short_reason(dr) == "有坡（越野）"


def test_short_reason_still_reads_every_refusal():
    want = {R.HILL_REASON: "有坡（越野）", R.STOP_REASON: "停太久", R.WALK_REASON: "跑走交替",
            R.VI_REASON: "功率起伏大", R.INTENSITY_REASON: "強度太高", R.COVER_REASON: "功率不完整",
            R.NO_DATA_REASON: "資料不夠", R.NO_POWER_REASON: "沒有功率", R.POWER_DATA_REASON: "功率不夠",
            **{m: "前後半強度不同" for m in R.HALVES_REASON.values()}, **{m: "結尾加速" for m in R.FINISH_REASON.values()}}
    for msgid, params in REASONS:
        dr = {"reason": R._zh(msgid, **params), "measured_s": 1500.0}
        got = R.short_reason(dr)
        if msgid in want:
            assert got == want[msgid], msgid
    assert R.short_reason({"reason": R._zh(R.TOO_SHORT_REASON, m=12, lim=30), "measured_s": 720.0}) == "太短（12 分）"


def test_the_temperature_band_and_heat_note_in_english():
    with use_locale("en"):
        assert R.band_chip({}).lower() == "🌡 temperature unknown"
        assert R.band_chip({"temp_c": 26.0}) == "🌡 25–28 °C"
        assert not CJK.search(translate(R.HEAT_NOTE, "en"))
    with use_locale("zh-TW"):
        assert R.band_chip({}) == "🌡 溫度不明"
    # other modules (quality_gate, base_check, aet_test, status) still put the source text in their own text
    assert R.TEMP_BAND_LABEL["none"] == "溫度不明" and R.HEAT_NOTE == "熱環境，結果可能偏高"


# ---- the sections in English -------------------------------------------------------------------------

def _foreign(ds, w) -> list[str]:
    """Texts other modules put into the cards (not this package's): stripped before the CJK check."""
    m = R.measure(ds, w) or {}
    out = [R._cap_src(m), CPP.wprime_prior(None)[2], "每一趟都在目標帶"]          # hr zones / cp_protocols / quality_gate
    out += list(dict.values(SS.SRC)) + [SS.SRC[k] for k in dict.keys(SS.SRC)]  # session_stimulus' sources
    return [x for x in out if x]


CASES = {
    "easy": lambda: FakeDataset([_run(TODAY, minutes=52, hr=150.0, power=200.0)], TODAY, settings=SETTINGS),
    "short": lambda: FakeDataset([_run(TODAY, minutes=45, hr=135.0, power=200.0)], TODAY, settings=SETTINGS),
    "trail": lambda: FakeDataset([_trail(TODAY)], TODAY, settings=TRAIL_SETTINGS),
    "intervals": lambda: _ds([_v1a_run(QT - dt.timedelta(days=1))]),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_every_section_in_english_has_no_chinese_of_its_own(case):
    ds = CASES[case]()
    w = ds.workouts[-1]
    bad = []
    with use_locale("en"):
        foreign = _foreign(ds, w)
        for sec in R._SECTIONS:
            for basis in ("pace", "power"):
                for s in _strings(R.review(ds, w, sec, basis=basis)):
                    for f in foreign:
                        s = s.replace(f, "")
                    if CJK.search(s):
                        bad.append((sec, basis, s[:120]))
    assert not bad, bad[:8]


def test_aerobic_rows_and_drift_card_in_english():
    ds = FakeDataset([_run(TODAY, minutes=80, hr=135.0, hr_end=142.0, power=200.0)], TODAY, settings=SETTINGS)
    w = ds.workouts[0]
    with use_locale("en"):
        r = R.review(ds, w, "aerobic", basis="power")
    rows = {s["name"]: s["data"]["value"] for s in r["series"]}
    assert rows["First / second half HR"] == "137 → 140 bpm" and "Steadiness" in rows
    assert rows["Temperature"] == "No temperature data (temperature unknown; compared only with other runs of unknown temperature)"
    assert r["chip"]["text"].lower() == "🌡 temperature unknown"
    assert any(v.endswith("(First 10 min left out)") for v in rows.values())
    # the summary's drift card keeps its lines in English too (it filters them by the word for drift)
    with use_locale("en"):
        cards = {c.get("id"): c for c in R.review(ds, w, "summary")["cards"]}
    assert "HR drift" in cards["drift"]["tip"]


def test_climb_table_and_form_rows_in_english():
    ds = FakeDataset([_trail(TODAY)], TODAY, settings=TRAIL_SETTINGS)
    w = ds.workouts[0]
    with use_locale("en"):
        cl = R.review(ds, w, "climbs")
        su = R.review(ds, w, "summary")
    assert [s["name"] for s in cl["series"]][:5] == ["Segments", "Start", "From km", "Gain m", "Distance km"]
    rows = {s["name"]: s["data"]["value"] for s in su["series"]}
    assert rows["Climbing"].startswith("1 climb, 200 m in all") and rows["Time"].startswith("Moving ")


# ---- the interval judgement (interval_eval) ------------------------------------------------------------

def test_interval_reasons_in_english_and_zh_unchanged():
    ds = _ds([_v1a_run(QT - dt.timedelta(days=1))])
    w = ds.workouts[0]
    with use_locale("en"):
        en = IE.card_cached(ds, w)
    with use_locale("zh-TW"):
        zh = IE.card_cached(ds, w)
    assert en["reasons"][-1].startswith("Fade +0% (last rep vs first), Sdec 0.0% (Glaister 2008")
    assert en["reasons"][0].startswith("5 reps: 0 threshold, 5 VO2max (the intensity band")       # SP-918: detected reps by band
    assert zh["reasons"][-1] == "掉速 +0%（最後一趟比第一趟），Sdec 0.0%（Glaister 2008；只顯示）"
    assert zh["reasons"][0].startswith("5 趟：0 趟閾值、5 趟 VO2max（每趟的平均功率落在哪個強度帶")


def test_interval_evaluation_memo_is_per_language():
    ds = _ds([_v1a_run(QT - dt.timedelta(days=1))])
    w = ds.workouts[0]
    with use_locale("zh-TW"):
        zh = IE.cached(ds, w)
    with use_locale("en"):
        en = IE.cached(ds, w)
    assert zh is not en and zh["verdict_label"] != en["verdict_label"]
    assert len([k for k in ds.memo if isinstance(k, tuple) and k and k[0] == "interval_eval"]) == 2


def test_cp_test_bouts_in_english_and_the_pacing_code():
    ds = _ds([_cp_test_run(QT - dt.timedelta(days=1))])
    w = ds.workouts[0]
    with use_locale("en"):
        e = IE.card_cached(ds, w)
        v = R.review(ds, w, "interval_verdict")
        cp = R.review(ds, w, "cp_test")
    r12 = next(r for r in e["reps"] if r["duration_s"] >= 600)
    assert r12["pacing_key"] == "fast_start" and r12["pacing"] == "Faded (started too fast)" and r12["name"] == "12-min bout"
    assert e["label"] == "CP test 3 min + 12 min (all-out)"
    assert e["reasons"][0].startswith("3-min bout 330 W (132% CP; expected all-out ≈ 323 W")
    chip = next(c for c in v["chip_rows"] if c["label"] == "12-min bout")
    assert chip["verdict"] == {"text": "Faded", "level": "warn"} and chip["tip"].startswith("First half 295 → second half 265 W")
    names = [s["name"] for s in v["series"]]
    assert names[:3] == ["Verdict", "Protocol", "Bouts"] and "Anaerobic capacity used" in names
    rows = {s["name"]: s["data"]["value"] for s in cp["series"]}
    assert rows["3-min bout"] == "330 W (from 15:00, max HR 150)" and "Protocol" in rows
    # zh-TW: the words as before
    with use_locale("zh-TW"):
        e = IE.card_cached(ds, w)
    r12 = next(r for r in e["reps"] if r["duration_s"] >= 600)
    assert r12["pacing"] == "前快後掉（起步太快）" and r12["pacing_short"] == "前快後掉" and r12["name"] == "12 分段"
    assert e["label"] == "CP 測試 3 分 + 12 分（全力）"
    assert "平均＝後半和前半差 ±5% 內、最後 1 分 ≤ 該段 × 1.08（推估）" in e["reasons"]


def test_zh_tw_review_text_unchanged():
    ds = FakeDataset([_run(TODAY, minutes=80, hr=135.0, hr_end=142.0, power=200.0)], TODAY, settings=SETTINGS)
    w = ds.workouts[0]
    with use_locale("zh-TW"):
        a = R.review(ds, w, "aerobic", basis="power")
        s = R.review(ds, w, "summary")
    rows = {x["name"]: x["data"]["value"] for x in a["series"]}
    assert rows["前半／後半心率"] == "137 → 140 bpm" and rows["溫度"] == "沒有溫度資料（溫度不明，只和溫度不明的跑步比）"
    assert rows["穩定度"].startswith("VI 1.000（≤ 1.04） · 最長慢段 0:00（< 3:00） · 後半功率 +0.0%（±5% 內）")
    assert a["chip"]["text"] == "🌡 溫度不明"
    srows = {x["name"]: x["data"]["value"] for x in s["series"]}
    assert srows["時間"] == "移動 1:20:00 ／ 全程 1:20:00"
    assert srows["心率"].startswith("平均 139 bpm（AeT 142、LTHR 160）")
    assert np.isfinite(R.measure(ds, w)["drift"]["drift"])
