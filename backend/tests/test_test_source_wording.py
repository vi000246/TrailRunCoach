"""Source wording of the AeT-test texts (SP-278, SP-279; aerobic-base-readiness.md §8,
2026-10-06 verbatim check). Synthetic data only."""
import pathlib

from backend.engine import base_check as BC
from backend.engine import quality_gate as QG
from backend.i18n import use_locale
from backend.tests.test_quality_gate import TODAY, _ds

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---- SP-278: 「4–6 週重測」 is not UA's (UA: every 4–6 *months*) ----------------------

def _no_data():
    ae = {"value": 140.0, "measured": False, "validity": {"valid": False, "reason": "還不夠準"}}
    return QG.aet_test_reason(_ds([]), TODAY, ae, {"state": "unconfirmed"})


def test_no_data_text_is_an_estimate_not_ua():
    r = _no_data()
    assert r["code"] == "no_data"
    assert r["text"] == "6 週內沒有可判讀的跑步（推估 6 週）"
    assert "UA" not in r["text"]


def test_no_data_text_in_english():
    ae = {"value": 140.0, "measured": False, "validity": {"valid": False, "reason": "還不夠準"}}
    with use_locale("en"):
        r = QG.aet_test_reason(_ds([]), TODAY, ae, {"state": "unconfirmed"})
    assert r["text"] == "No interpretable run in 6 weeks (6 weeks is an estimate)"


def test_the_constants_did_not_move():
    from backend.engine import baseline_test as BT
    assert BC.NO_DATA_DAYS == 42 and BT.REPEAT_DAYS == (42, 56)


def test_no_ua_4_6_weeks_left_in_the_code():
    hits = []
    for p in (ROOT / "backend").rglob("*.py"):
        if "tests" in p.parts:
            continue
        s = p.read_text(encoding="utf-8")
        for bad in ("UA 4–6 週", "UA's 4–6-week", "Athlete's 4–6 weeks"):
            if bad in s:
                hits.append(f"{p.relative_to(ROOT)}: {bad}")
    assert not hits, hits
