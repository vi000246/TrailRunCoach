"""SP-889: 本次重點 names the run's 閾值段 (SP-801) and points at the 閾值 tab — the tab was only found by
looking. A card 「閾值段 n 段 · 看「閾值」分頁」 when the run has ≥ 1 stretch of ≥ 10 min in
Zone 3; none on a run without one."""
import datetime as dt

from backend.engine import workout_review as R
from backend.i18n import use_locale
from backend.tests.test_threshold_segments_sp801 import CP, _workout
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 10, 18)


def _cards(blocks):
    ds = FakeDataset([_workout(TODAY, blocks)], TODAY, settings={"runthr": 160.0, "runftp": CP})
    return {c.get("id"): c for c in R.review(ds, ds.workouts[0], "summary")["cards"]}


def test_summary_counts_the_threshold_stretches_and_points_at_the_tab():
    # 3 stretches of 12' at 95 % CP, 4' easy between
    cards = _cards([(10, 150, 130), (12, 190, 150), (4, 120, 130), (12, 190, 150), (4, 120, 130),
                    (12, 190, 150), (10, 150, 130)])
    c = cards["threshold"]
    assert c["value"] == "3" and c["label"] == "閾值段", c
    assert c["sub"] == "看「閾值」分頁", c
    assert "3 段連續 ≥ 10 分鐘的 3 區" in c["tip"] and "共 36 分、最長 12 分" in c["tip"] and "「閾值」分頁" in c["tip"], c


def test_summary_in_english():
    blocks = [(10, 150, 130), (20, 190, 150), (10, 150, 130)]
    with use_locale("en"):
        c = _cards(blocks)["threshold"]
    assert c["label"] == "Threshold stretches" and c["unit"] == "Segment", c
    assert c["sub"] == "See the Threshold tab" and "1 stretch of ≥ 10 min" in c["tip"], c


def test_no_card_without_a_threshold_stretch():
    assert "threshold" not in _cards([(45, 140, 130)])
    assert "threshold" not in _cards([(10, 120, 130), (9, 190, 140), (10, 120, 130)])     # 9' < 10'
