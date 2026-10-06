"""SP-296: the intensity budget's and the Zone 3 cap's sources are corrected, the numbers are not
(docs/research/estimated-constants-inventory.md §5.1, §5.3)."""
from backend.engine import overview as O
from backend.engine import quality_gate as QG
from backend.engine import status as ST


def test_numbers_unchanged():
    assert QG.QUALITY_SHARE_MAX == 0.20 and QG.LOW_SHARE_MIN == 0.75 and ST.LOW_SHARE_GOOD == 0.75
    assert QG.Z3_SHARE_MAX == 0.10 and QG.Z3_SHARE_START == 0.05
    assert QG.z3_budget_min(5.0) == 30.0 and QG.z3_budget_min(5.0, first=True) == 15.0


def test_source_texts_say_the_units():
    assert "Seiler 的 80/20 是堂數，不是時間" in QG.SRC_Z3["share"]
    vol = QG.SRC_Z3["volume"]
    assert "未驗證原書" in vol and "單次" in vol and "里程" in vol and "每週" in vol
    rule = QG.OPTION_INFO["auto"]["rule"]
    assert "Seiler 的 80/20 是堂數" in rule and "未驗證原書" in rule and "（Daniels）" not in rule


def test_reserved_note_names_the_unit():
    notes = []
    O._reserved_out(notes, 30.0, 0.0, "z3")
    assert "Seiler 的 80/20 是堂數" in notes[0]["text"] and "80/20；推估" not in notes[0]["text"]
