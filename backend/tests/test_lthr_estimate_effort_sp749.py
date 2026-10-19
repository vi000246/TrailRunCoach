"""SP-749: the automatic LTHR estimate counts runs whose best 30 min reached 95 % of the CP in effect.
With that CP too low, easy runs pass and the estimate is the top of Z2 (the owner's 155 at 175 W while
the CP was 204 W: 84 % of max HR, 77 % of HRR). The settings card no longer offers it silently: an
estimate below the usual share of the runner's own max HR / HRR carries `low_effort` and no 「套用」.
The same rule for everyone — the runner's own max / rest HR, the typical ranges of
threshold_confidence.consistency_signals."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from backend.engine import threshold_confidence as TC

ROOT = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 10, 19)


def test_a_low_estimate_against_max_hr_and_hrr_is_flagged():
    w = TC.estimate_effort_warning(155, {"value": 185, "source_kind": "watch"}, {"value": 50})
    assert w is not None and w["text"] and w["test"]["test"] == "tt30" and w["test"]["href"]
    assert len(w["signals"]) == 2                                   # 84 % HRmax and 78 % HRR
    assert any("84%" in s for s in w["signals"]) and any("78%" in s for s in w["signals"])


def test_either_share_alone_flags_it():
    # 84 % of max HR, no resting HR
    assert TC.estimate_effort_warning(155, {"value": 185, "source_kind": "test"}, {"value": None})
    # 85 % of max HR (typical), but 79 % of HRR
    w = TC.estimate_effort_warning(162, {"value": 190, "source_kind": "watch"}, {"value": 55})
    assert w and len(w["signals"]) == 1


@pytest.mark.parametrize("value,mhr,rhr", [
    (170, {"value": 185, "source_kind": "watch"}, {"value": 50}),   # 92 % HRmax, 89 % HRR: a real threshold
    (180, {"value": 185, "source_kind": "watch"}, {"value": 50}),   # high, not low: not this warning
    (155, {"value": 185, "source_kind": "age"}, {"value": 50}),     # the age formula is not the runner's data
    (155, {"value": None}, {"value": 50}),                          # no max HR: nothing to judge by
    (None, {"value": 185, "source_kind": "watch"}, {"value": 50}),  # no estimate
])
def test_no_warning(value, mhr, rhr):
    assert TC.estimate_effort_warning(value, mhr, rhr) is None


def _api(monkeypatch, lthr_value, mhr=185.0):
    from backend.api import plan as PL
    from backend.engine import hr_profile as HP
    from backend.engine import thresholds as TH
    est = {"cp": 175.0, "today": TODAY.isoformat(),
           "lthr": {"value": lthr_value, "n": 7, "reason": "r", "days": 90}, "aethr": {"value": None}}
    monkeypatch.setattr(PL, "_estimate_dataset", lambda: object())
    monkeypatch.setattr(PL, "_estimate_pending", lambda ds: None)
    monkeypatch.setattr(PL, "today_local", lambda: TODAY)
    monkeypatch.setattr(TH, "estimate", lambda ds, today: est)
    monkeypatch.setattr(HP, "account", lambda: None)
    monkeypatch.setattr(TC, "mhr_info", lambda ds, today, acc=None: {"value": mhr, "source_kind": "watch"})
    monkeypatch.setattr(TC, "rhr_info", lambda ds, today, acc=None: {"value": 50, "source_kind": "watch"})
    return PL.threshold_estimate()


def test_the_api_marks_a_low_estimate_and_keeps_its_value(monkeypatch):
    got = _api(monkeypatch, 155)
    assert got["lthr"]["value"] == 155 and got["lthr"]["low_effort"]["signals"]


def test_the_api_leaves_a_believable_estimate_as_it_was(monkeypatch):
    got = _api(monkeypatch, 170)
    assert got["lthr"]["value"] == 170 and "low_effort" not in got["lthr"]
    assert "low_effort" not in _api(monkeypatch, None)["lthr"]


def test_the_api_without_a_max_hr_gives_the_estimate_as_before(monkeypatch):
    got = _api(monkeypatch, 155, mhr=None)
    assert got["lthr"]["value"] == 155 and "low_effort" not in got["lthr"]


def test_the_settings_card_shows_the_warning_instead_of_apply():
    html = (ROOT / "backend/static/settings.html").read_text("utf-8")
    i = html.index("const low = ok && est.low_effort;")
    card = html[i:html.index("</div>`;", i)]
    # the warning branch comes first; 「套用」 only in the branch where it is not low
    assert card.index("${low ?") < card.index("data-low-effort") < card.index(": ok && !same ?") < card.index("data-apply=")


def test_the_warning_is_translated():
    import json
    en = json.loads((ROOT / "backend/i18n/locales/en.json").read_text("utf-8"))
    assert en.get(TC.EST_LOW_EFFORT)
