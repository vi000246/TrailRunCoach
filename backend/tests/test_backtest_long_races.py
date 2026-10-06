"""SP-240: the trail HR back-test lists the ≥ 8 h races apart, with and
without durability decay (docs/research/long-race-durability-shape.md §1.3,
§5 #2). Only the existing err_th_race / err_th_race_nodur are used."""
import json
from pathlib import Path

import pytest

from backend.engine.racepower import backtest as BT

STATIC = Path(__file__).resolve().parents[1] / "static"


def _row(date, hours, err, err_nodur, activity_type="race", effort_tag="max"):
    return {"category": "trail", "date": date, "label": f"run {date}", "activity_type": activity_type,
            "effort_tag": effort_tag, "th": {"moving_s": hours * 3600.0},
            "err_th_race": err, "err_th_race_nodur": err_nodur}


def test_long_race_summary_row_with_long_races():
    rows = [_row("2026-01-01", 3.0, 0.02, 0.01),
            _row("2026-02-01", 8.0, 0.30, -0.05),        # exactly 8 h counts
            _row("2026-03-01", 12.5, 0.50, -0.10),
            _row("2026-04-01", 10.0, 0.90, 0.90, activity_type="training")]   # not a race: not in the row
    s = BT.summarise_trail_hr(rows)
    lr = s["long_races"]
    assert lr["n"] == 2 and lr["min_h"] == 8.0
    assert lr["race_level"]["n"] == 2 and lr["race_level"]["bias"] == pytest.approx(0.40)
    assert lr["race_level"]["median_abs"] == pytest.approx(0.40)
    assert lr["race_level_no_durability"]["bias"] == pytest.approx(-0.075)
    assert lr["race_level_no_durability"]["median_abs"] == pytest.approx(0.075)
    assert [r["long"] for r in s["race_rows"]] == [False, True, True]
    assert s["races"]["n"] == 3                      # the races block itself is unchanged


def test_long_race_summary_row_without_long_races():
    s = BT.summarise_trail_hr([_row("2026-01-01", 3.0, 0.02, 0.01), _row("2026-02-01", 7.9, 0.1, 0.0)])
    lr = s["long_races"]
    assert lr["n"] == 0
    assert lr["race_level"]["median_abs"] is None and lr["race_level_no_durability"]["median_abs"] is None
    assert not any(r["long"] for r in s["race_rows"])
    assert BT.summarise_trail_hr([])["long_races"]["n"] == 0


def test_long_race_row_has_no_new_personal_fields():
    s = BT.summarise_trail_hr([_row("2026-02-01", 9.0, 0.3, 0.1)])
    assert set(s["long_races"]) == {"n", "min_h", "race_level", "race_level_no_durability"}
    assert set(s["race_rows"][0]) == {          # the existing row fields + the derived flag only
        "date", "label", "file", "effort_tag", "effort_overridden", "effort_reason", "rest_share", "no_power",
        "power_source", "power_unused", "err_th_given", "err_th_nodur", "err_th_race", "err_th_race_nodur",
        "err_th_race_median", "err_th_total", "err_c", "err_p", "error", "th", "long"}


def test_long_race_strings_in_both_locales_and_on_the_page():
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "racepower.json").read_text("utf-8"))
    keys = [k for k in zh if k.startswith("bt.th.")]
    assert keys and all(k in en for k in keys)
    assert zh["bt.th.none"].format(h=8) == "沒有 8 小時以上的比賽，長賽的衰減形狀無法驗證"
    assert zh["bt.th.long"].format(h=8) == "≥ 8 小時的比賽"
    page = (STATIC / "racepower.html").read_text("utf-8")
    for k in ("bt.th.none", "bt.th.long", "bt.th.moving_h", "bt.th.long_badge"):
        assert f'"{k}"' in page
