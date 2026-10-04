"""No stable-weekly-volume precondition (removed 2026-10-03: the 3-week ±15 % rule had
no source). A test after uneven weeks still confirms the base, and the AeT-test
suggestion is made right away. Synthetic data only — never the WKO5 folder, the
app DB or ~/.wko5coach."""
from backend.engine import aet_test as AT
from backend.engine import base_check as BC
from backend.engine import quality_gate as QG
from backend.tests.test_quality_gate import TODAY, _ds


def test_the_precondition_is_gone():
    assert not hasattr(BC, "volume_stable") and not hasattr(BC, "VOL_WEEKS")


def test_a_test_confirms_without_a_volume_check(monkeypatch):
    monkeypatch.setattr(BC, "xu_runs", lambda ds, today, days=182: [
        {"idx": 0, "date": "2026-08-01", "ok": True, "drift": 0.06, "hr10": 128.0, "hr90": 135.7, "why": []}])
    monkeypatch.setattr(BC, "maintenance", lambda ds, today, since, brk=None: {"ok": True, "why": ""})
    z = BC.z5_status(_ds([]), TODAY, "auto", aet_paths={"aet_ua_gap": "2026-08-01"})   # SP-39: a measured AeT
    assert z["state"] == "confirmed" and z["since"] == "2026-08-01" and "vol_skipped" not in z


def test_the_aet_test_suggestion_does_not_wait():
    ae = {"value": 140.0, "measured": False, "validity": {"valid": False, "reason": "還不夠準"}}
    r = QG.aet_test_reason(_ds([]), TODAY, ae, {"state": "unconfirmed"})
    assert r["code"] == "no_data" and "wait" not in r and "週量" not in r["text"]
    assert AT.due(TODAY, "base", None, r, None)


def test_the_card_has_no_volume_item():
    gate = {"z5": {"state": "unconfirmed", "path": None}, "mode": "auto", "aet": {}, "lthr": {}, "dose": {},
            "options": {}}
    c = QG.z5_card(gate, TODAY)
    assert "pre" not in c["base"] and "週量" not in c["next"]["text"]
