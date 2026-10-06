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


# ---- SP-279: 「−5 bpm」 is 推估 (UA: "lower"; Evoke: a slower pace) ---------------------

from backend.engine import aet_test as AT  # noqa: E402


def _r(judge, band, drift):
    return {"ok": True, "judge": judge, "band": band, "drift": drift, "hr1": 140.0, "hr2": 150.0,
            "main_s": 3600.0}


def test_ua_above_lower_5_is_an_estimate():
    ln = AT.lines(_r("ua", "above", 0.06), 142.0)
    assert ln[-1] == "下次起始心率降 5 bpm（約 135；5 bpm 是推估）再測一次（目前 142）"
    assert "UA" not in ln[-1] and "Evoke" not in ln[-1]


def test_evoke_above_lower_5_is_an_estimate():
    ln = AT.lines(_r("evoke", "above", 0.06))
    assert ln[-1] == "下次起始心率降 5 bpm（約 135；5 bpm 是推估）再測一次"


def test_ua_below_plus_5_stays_uas():
    ln = AT.lines(_r("ua", "below", 0.02), 142.0)
    assert ln[-1] == "下次起始心率 +5 bpm（約 145）再測一次（目前 142）"


def test_the_numbers_did_not_move():
    assert AT.LOWER_BPM == 5
    assert AT.band_of(0.06) == "above" and AT.band_of(0.02) == "below" and AT.band_of(0.04) == "at"


def test_evoke_early_abort_keeps_evoke_rule_but_5_bpm_is_an_estimate():
    for proto in ("ua60", "ua40", "evoke60"):
        d = AT.session({}, 140.0, 250.0, protocol=proto)["detail"]
        assert "停掉改天用較慢的配速再測（Evoke）；起始心率約降 5 bpm（推估）" in d
        assert "降 5 bpm 再測（Evoke）" not in d
    assert "Evoke）；起始心率" not in AT.session({}, 140.0, 250.0, protocol="friel")["detail"]


def test_lower_line_in_english():
    with use_locale("en"):
        ln = AT.lines(_r("ua", "above", 0.06))
        d = AT.session({}, 140.0, 250.0, protocol="ua60")["detail"]
    assert ln[-1] == "Next time start 5 bpm lower (about 135; the 5 bpm is an estimate) and test again"
    assert "retest another day at a slower pace (Evoke); start about 5 bpm lower (estimate)" in d
