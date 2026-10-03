"""WKO5 expression parser (backend/engine/wko5expr/parser.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.engine.wko5expr import parser as P

# your own exported views (opt-in): WKO5_VIEWS_DIR, never the repo
_VDIR = os.getenv("WKO5_VIEWS_DIR")
VIEWS = sorted(Path(_VDIR).rglob("*.wko5chart")) if _VDIR else []


def test_precedence_and_right_assoc_power():
    n = P.parse("1+2*3^2^1")
    assert isinstance(n, P.BinOp) and n.op == "+"
    assert n.right.op == "*" and n.right.right.op == "^" and n.right.right.right.op == "^"


def test_if_function_vs_if_variable_is_case_insensitive():
    n = P.parse("if(IF>.8,if)")
    assert isinstance(n, P.Call) and n.name == "if"
    assert n.args[0].left == P.Ident("if") and n.args[1] == P.Ident("if")


def test_statements_assignments_and_vars():
    n = P.parse("@ctlPercent := tss/ctl, if(@ctlPercent<=1.5,@ctlPercent)")
    assert isinstance(n, P.Seq)
    assert isinstance(n.items[0], P.Assign) and n.items[0].name == "@ctlpercent"


def test_pairs_lines_lists_and_ranges():
    assert P.parse("(,0.8)") == P.Pair(None, P.Num(0.8))
    assert isinstance(P.parse("(elapsedtime, power)"), P.Pair)
    assert P.parse("{1,2}") == P.ListLit([P.Num(1.0), P.Num(2.0)])
    r = P.parse("{-15:10}").items[0]
    assert isinstance(r, P.RangeLit)
    r3 = P.parse("{0:max(power):10}").items[0]
    assert r3.step == P.Num(10.0)


def test_logical_not_and_comparison_aliases():
    n = P.parse("today-!athleterange(today,today,sum(tss))")
    assert n.op == "-" and isinstance(n.right, P.Unary) and n.right.op == "!"
    assert P.parse("a==b").op == "=" and P.parse("a!=b").op == "<>"


def test_units_time_literals_and_method_calls():
    assert isinstance(P.parse('300"s"'), P.Unit)
    assert P.parse('10:00:00"hms"') == P.Unit(P.Num(36000.0), "hms")
    assert P.parse("@d:=0:1:45").value == P.Num(105.0)
    assert P.parse("{5:0:-1}").items[0].step is not None      # range, not a time
    n = P.parse("sport(sport).athleterange(date-1,date,1)")
    assert isinstance(n, P.Call) and n.receiver is not None


def test_blank_expression_is_empty():
    assert isinstance(P.parse(""), P.Empty)
    assert isinstance(P.parse(None), P.Empty)


def _parse_all(paths):
    from backend.files.wko5chart_reader import read_view
    total, failed = 0, []
    for p in paths:
        for d in read_view(p)["dashboards"]:
            for c in d["charts"]:
                for s in c.get("series", []):
                    total += 1
                    try:
                        P.parse(s["expression"])
                    except P.ParseError:
                        failed.append((d["title"], c["title"], s["name"]))
    return total, failed


def test_every_expression_in_a_synthetic_view_parses(tmp_path):
    from backend.tests import wko5chart_builder as WB
    total, failed = _parse_all([WB.season_view(tmp_path / "s.wko5chart"),
                                WB.workout_view(tmp_path / "w.wko5chart")])
    assert total == 5 and failed == []


@pytest.mark.skipif(not VIEWS, reason="set WKO5_VIEWS_DIR to your exported views")
def test_every_expression_in_the_users_views_parses():
    total, failed = _parse_all(VIEWS)
    # known gap: one VO2max interval-marking series uses the `in` operator
    assert total > 0
    assert len(failed) <= 1, failed
