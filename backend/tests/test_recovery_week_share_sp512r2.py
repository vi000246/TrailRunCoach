"""SP-512 R2 (the owner's Q2 item 3, as suggested): a projected recovery week (projection.week_hours /
project_weeks, the weeks after this one) says that its share is 推估 too — the same line as this week's
recovery week (overview.recovery_share_note, reading RECOVERY_WEEK_SHARE / RECOVERY_WEEK_TSB_SHARE), last
in the week's why, in zh and en. The projected 3:1 reason is the week plan's own (translated) words.
Synthetic data only."""
from __future__ import annotations

from datetime import date

from backend.engine import overview as O
from backend.engine import projection as P
from backend.engine import specific_phase as SP
from backend.i18n import translate, use_locale
from backend.tests.test_recovery_week import LIGHT, _phases, _week
from backend.tests.test_recovery_week_share_sp512 import NOTE_WORDS

HIST = [5.0, 5.5, 6.0, 6.5]


def test_the_note_reads_the_constants(monkeypatch):
    assert all(w in O.recovery_share_note() for w in NOTE_WORDS)
    monkeypatch.setattr(O, "RECOVERY_WEEK_SHARE", 0.75)
    assert "75%／60% 是推估" in O.recovery_share_note()


def test_a_projected_3_1_recovery_week_says_its_share_is_an_estimate():
    h, mode, why = P.week_hours("base", HIST, [True, True, True], ctl0=40.0, r=50.0, cc=42.0, days_to_a=None)
    assert mode == "recovery_week"
    assert why == ["已連續 3 週加量：這週是恢復週（前 3 週平均的 70%）", O.recovery_share_note()]


def test_a_projected_calendar_recovery_week_too_and_a_normal_week_not():
    _, mode, why = P.week_hours("specific", HIST, [False] * 3, ctl0=40.0, r=50.0, cc=42.0, days_to_a=60,
                                recovery="專項期賽前第 5 週是恢復週")
    assert mode == "recovery_week" and why == ["專項期賽前第 5 週是恢復週", O.recovery_share_note()]
    _, mode, why = P.week_hours("base", HIST, [False, True, True], ctl0=40.0, r=50.0, cc=42.0, days_to_a=None)
    assert mode == "base" and not any("沒有研究可依" in w for w in why)


def test_project_weeks_recovery_weeks_end_with_the_note():
    """The 專項期 countdown's projected recovery weeks (as test_recovery_week): the reason first, the note last;
    the other weeks have none."""
    _, plan, wp = _week("2026-11-14", {4: LIGHT})
    weeks = P.project_weeks(wp, _phases(plan, date(2026, 9, 30)), date(2026, 11, 8))
    by = {SP.weeks_out(date(2026, 11, 14), date.fromisoformat(w["start"])): w for w in weeks if w["phase"] == "specific"}
    for k in (5, 3):
        assert by[k]["mode"] == "recovery_week"
        assert "賽前第" in by[k]["why"][0] and by[k]["why"][-1] == O.recovery_share_note()
    for k in (6, 4):
        assert not any("沒有研究可依" in w for w in by[k]["why"])


def test_in_english_the_projected_week_reads_english():
    with use_locale("en"):
        _, _, why = P.week_hours("base", HIST, [True, True, True], ctl0=40.0, r=50.0, cc=42.0, days_to_a=None)
    assert why[0] == "3 build weeks in a row: this is a recovery week (70% of the last 3 weeks' average)"
    assert why[1].startswith("No study says how much a recovery week should cut") and "70% / 60%" in why[1]
    assert why[1] == translate(
        "恢復週減多少沒有研究可依：查證過的 11 位教練與做法最常見減 30%（範圍減 10–60%；跑步教練多在 15–35%，"
        "Uphill Athlete 登山課表約減一半）；這裡的 {share:.0%}／{tsb_share:.0%} 是推估", "en", share=0.70, tsb_share=0.60)
