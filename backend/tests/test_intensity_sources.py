"""SP-296: the intensity budget's and the Zone 3 cap's sources are corrected, the numbers are not
(docs/research/estimated-constants-inventory.md §5.1, §5.3); SP-528 corrected them again from the coach
consensus (docs/research/coach-consensus-sp518/B-intensity.md)."""
from backend.engine import overview as O
from backend.engine import quality_gate as QG
from backend.engine import status as ST


def test_numbers_unchanged():
    assert QG.QUALITY_SHARE_MAX == 0.20 and QG.LOW_SHARE_MIN == 0.75 and ST.LOW_SHARE_GOOD == 0.75
    assert QG.Z3_SHARE_MAX == 0.10 and QG.Z3_SHARE_START == 0.05
    assert QG.z3_budget_min(5.0) == 30.0 and QG.z3_budget_min(5.0, first=True) == 15.0


def test_source_texts_say_the_units():
    # SP-528 (coach-consensus-sp518 B §2–§3): the budget is the coaches' median by time (80/20 Endurance,
    # Campos 2022, UA / Koop — Koop's 10 % is hours, his 20 % sessions); the Zone 3 cap is UA / TftNA per week
    # by time, Daniels' per-session mileage cap a side note
    share = QG.SRC_Z3["share"]
    assert "80/20 Endurance" in share and "Campos 2022" in share and "Koop 是時數 10%、堂數 20%" in share
    assert "Seiler" not in share
    vol = QG.SRC_Z3["volume"]
    assert "Uphill Athlete" in vol and "TftNA" in vol and "每週、以時間算" in vol and "單次" in vol and "里程" in vol
    rule = QG.OPTION_INFO["auto"]["rule"]
    assert "教練中位數：80/20 Endurance 約 20%" in rule and "UA、TftNA：每週、以時間算" in rule
    assert "未驗證原書" not in rule and "（Daniels）" not in rule


def test_reserved_note_names_the_unit():
    notes = []
    O._reserved_out(notes, 30.0, 0.0, "z3")
    assert "教練中位數：80/20 Endurance 約 20%" in notes[0]["text"] and "80/20；推估" not in notes[0]["text"]
