import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.engine.algorithms.validator import validate_core_formulas


def test_core_formulas_match_wko5():
    report = validate_core_formulas()
    assert report, "validator returned no checks"
    failing = [r for r in report if not r["pass"]]
    assert failing == [], f"formula mismatches: {failing}"


def test_report_entries_have_shape():
    report = validate_core_formulas()
    for r in report:
        assert set(r.keys()) >= {"name", "pass", "expected", "actual"}
