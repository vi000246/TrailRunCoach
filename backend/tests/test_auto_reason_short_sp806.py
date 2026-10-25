"""SP-806: the 自動 → 目前用 reason says in one short sentence why a 低 max HR is not believed; the max
HR's full source (the age formula's long one, SP-294) stays on the 最大心率 row only — the reason and the
max-HR confidence card under that row do not repeat it."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.engine import hr_profile as HP
from backend.engine import threshold_confidence as TC
from backend.i18n import _, use_locale
from backend.tests.test_threshold_confidence import TODAY, lt, run

RUNS = [run(s120=178.0, s60=180.0), run(s120=176.0, s60=178.0), run(s120=175.0, s60=177.0)]
REST = {"value": 53.0, "source_kind": "manual"}
LTHR = lt(162.0, kind="watch", date=None)
AGE_LABEL = _(HP.AGE_OVER_RUNS_SOURCE, age_v=195.0, run_v=182.0, d=13.0)


def _auto(mhr, runs=RUNS):
    return TC.assess(LTHR, mhr, REST, TODAY, runs, {}, 250.0, hr_model="auto")["auto"]


@pytest.mark.parametrize("kind, label, short", [
    ("age", AGE_LABEL, "最大心率 195 是年齡公式推估"),
    ("estimate", "推估（近 365 天跑步 10 秒平均最高）", "最大心率 195 是跑步紀錄推估"),
    ("watch", "來自手錶（COROS 帳號）；近 365 天跑步 10 秒平均最高 182（推估）",
     "最大心率 195 來自手錶，近 90 天沒有跑步 10 秒平均到 190 以上"),
])
def test_a_low_max_hr_reason_is_one_short_sentence(kind, label, short):
    a = _auto({"value": 195.0, "source_kind": kind, "label": label})
    assert a["model"] == "hrmax"
    assert a["reason"].endswith("；" + short)
    assert label not in a["reason"] and "年齡公式 195" not in a["reason"]


def test_a_low_max_hr_from_a_test_says_the_runs_disagree():
    # a tested max HR is not 低 by its source: 低 only from the runs' evidence (a 60-s hold well above it)
    runs = [run(s120=190.0, s60=195.0), run(s120=189.0, s60=194.0), run(s120=188.0, s60=193.0)]
    a = _auto({"value": 182.0, "source_kind": "test", "label": "最大心率測試 2026-09-01"}, runs)
    assert a["model"] == "hrmax" and a["reason"].endswith("；最大心率 182 跟跑步紀錄對不上")
    assert "2026-09-01" not in a["reason"]


def test_the_reason_is_short_in_english_too():
    with use_locale("en"):
        a = _auto({"value": 195.0, "source_kind": "age", "label": _(HP.AGE_OVER_RUNS_SOURCE, age_v=195.0,
                                                                     run_v=182.0, d=13.0)})
        w = _auto({"value": 195.0, "source_kind": "watch", "label": "from the watch"})
    assert a["reason"].endswith("max HR 195 is an age-formula estimate") and "182" not in a["reason"]
    assert w["reason"].endswith("max HR 195 is from the watch, and no run in the last 90 days reached a 10-s "
                                "average of 190 or more")
    assert not re.search(r"[一-鿿]", a["reason"] + w["reason"])


def test_the_max_hr_card_shows_only_the_number():
    """The max-HR confidence card under the 最大心率 row shows 「現在 195 bpm」 without the source;
    the LTHR card keeps its source (its row is elsewhere)."""
    html = (Path(__file__).resolve().parents[1] / "static" / "settings.html").read_text("utf-8")
    hr = re.search(r'box\("hr-check".*?"ST7", hm\)\);', html, re.S).group(0)
    assert re.search(r'tests\("hrmax"\), false\)', hr)
    assert 'settings.thr.chk_now_v' in html
    thr = re.search(r'box\("thr-check".*?\);', html, re.S).group(0)
    assert ", false)" not in thr
    import json
    root = Path(__file__).resolve().parents[1] / "static" / "i18n"
    zh = json.loads((root / "zh-TW" / "settings.json").read_text("utf-8"))
    en = json.loads((root / "en" / "settings.json").read_text("utf-8"))
    assert zh["thr.chk_now_v"] == "現在 {v} bpm" and en["thr.chk_now_v"] == "now {v} bpm"
