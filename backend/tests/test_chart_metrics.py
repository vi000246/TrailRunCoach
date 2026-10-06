"""Charts from docs/research/competitor-charts.md §4 — formulas checked against
their sources (backend/engine/algorithms/chart_metrics.py) and, on the real
athlete, the view expressions checked against an independent recomputation.
The check results are recorded in docs/research/competitor-charts.md §7."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import json
import math
from pathlib import Path

import pytest

from backend.engine.algorithms import chart_metrics as CM

ROOT = Path(__file__).resolve().parents[2]
# the 7-day ÷ 28-day downhill load the 總覽「下坡負荷」 card computes, as an expression (the
# chart of it was dropped from 訓練量 — the weekly bars show the same spikes)
DOWNHILL_RATIO_EXPR = ('@x:=tl(sum(if(sport = "run" or sport = "walk" or hastag("hiking") or hastag("mountaineering"), '
                       + CM.DOWNHILL_EXPR + '), trunc(date)), 1), ((cumsum(@x) - shift(cumsum(@x), 7)) / 7) / '
                       '((cumsum(@x) - shift(cumsum(@x), 28)) / 28)')


def _view(name):
    return json.loads((ROOT / "views" / f"{name}.json").read_text(encoding="utf8"))


def _chart(view, title_part):
    for d in _view(view)["dashboards"]:
        for c in d["charts"]:
            if title_part in (c.get("title") or ""):
                return c
    raise KeyError(title_part)


def _series(view, title_part, name_part):
    return next(s["expression"] for s in _chart(view, title_part)["series"] if name_part in s["name"])


# ---- worked examples from the sources ---------------------------------------------

@pytest.mark.parametrize("z, pi", [
    # Treff et al. 2019, Table 1 (TID in %, PI in a.U.)
    ((80, 0, 20), 3.18), ((72, 0, 28), 3.29), ((68, 6, 26), 2.47),
    ((67.3, 30.2, 2.5), 0.75), ((80.4, 17.9, 1.8), 0.91), ((74, 11, 15), 2.00),
])
def test_polarization_index_reproduces_treff_table_1(z, pi):
    z1, z2, z3 = (v / 100 for v in z)
    assert round(CM.polarization_index(z1, z2, z3), 2) == pi


def test_polarization_index_special_cases():
    assert CM.polarization_index(0.3, 0.3, 0.4) is None      # Z3 > Z1: not valid
    assert CM.polarization_index(0.8, 0.2, 0.0) == 0.0       # Z3 = 0: zero by definition
    # percentages instead of fractions would add +2 to every value
    assert CM.polarization_index(0.68, 0.06, 0.26) == pytest.approx(math.log10(0.68 / 0.06 * 0.26 * 100))


def test_course_constant_and_itra():
    # 6 h, 12 km, 1000 m up and down: 10.8 + 3.6 + 10 + 0.6
    assert CM.course_constant(6, 12, 1000, 1000) == pytest.approx(25.0)
    # ITRA worked example (as quoted by trailia.run): 42 km, 2000 m -> 62 -> S
    assert CM.km_effort(42, 2000) == pytest.approx(62)
    assert CM.itra_category(62) == "S"
    assert [CM.itra_category(x) for x in (24.9, 25, 44.9, 45, 114.9, 115, 209.9, 210)] == \
        ["XXS", "XS", "XS", "S", "M", "L", "XL", "XXL"]


def test_form_and_ewma():
    assert CM.form_pct(50, 65) == pytest.approx(-0.30)
    assert CM.form_zone(-0.31) == "high_risk" and CM.form_zone(-0.2) == "optimal"
    assert CM.form_zone(0.0) == "grey" and CM.form_zone(0.1) == "fresh" and CM.form_zone(0.3) == "transition"
    v = CM.ewma_loads([100] * 500, 42)
    assert v[41] == pytest.approx(100 * (1 - (41 / 42) ** 42))
    assert v[-1] == pytest.approx(100, rel=1e-4)


def test_downhill_weight_follows_its_sources():
    level_3ms = 3.0 * 3.6
    assert CM.downhill_weight(0.0, level_3ms) == pytest.approx(1.0)
    # Gottschall & Kram 2005: +54% impact peak at -9 deg, 3 m/s
    assert CM.downhill_weight(-math.tan(math.radians(9)), level_3ms) == pytest.approx(1.54)
    assert CM.downhill_weight(-0.40, level_3ms) == pytest.approx(1.54)       # held beyond -9 deg
    # Keller 1996: 1.2 BW at 1.5 m/s, 2.5 BW at 6 m/s
    assert CM.keller_fz(1.5) == pytest.approx(1.2) and CM.keller_fz(6.0) == pytest.approx(2.5)
    assert CM.keller_fz(0.5) == pytest.approx(1.2) and CM.keller_fz(9.0) == pytest.approx(2.5)
    top = CM.downhill_weight(-0.5, 30.0)
    assert top == pytest.approx(1.54 * 2.5 / (1.2 + 1.3 / 4.5 * 1.5))
    assert top < 3.0      # Garmin: downhill impact up to 3x the same speed on the flat
    # samples flatter than -3% or not moving count nothing
    assert CM.downhill_load([-0.02, -0.10, 0.1], [1, 1, 1], [10.8, 0, 10.8]) == 0.0


def test_downhill_expression_constants_match_the_reference():
    assert "0.158384" in CM.DOWNHILL_EXPR and abs(math.tan(math.radians(9)) - 0.158384) < 1e-6
    assert abs(1.3 / 4.5 - 0.288889) < 1e-6 and abs(CM.keller_fz(3.0) - 1.633333) < 1e-6
    # the weekly chart uses the same per-workout sum as the overview card
    for name in ("路跑", "越野跑", "登山健行"):
        assert CM.DOWNHILL_EXPR in _series("training", "每週下坡衝擊負荷", name)


def _dh_trail(day, down_s, cad, tags=("runningtrail",), kmh=8.0, pause=False):
    """10' flat, then `down_s` s at −12 %; cadence `cad` strides/min; `pause` = a 15' gap mid-descent."""
    import datetime as dt
    import numpy as np
    from backend.tests.wko5_fakes import FakeWorkout
    n = 600 + down_s
    t = np.arange(1, n + 1, dtype=float)
    if pause:
        t[600 + down_s // 2:] += 900
    dist = np.arange(1, n + 1, dtype=float) * kmh / 3600.0
    e = np.where(np.arange(n) < 600, 500.0, 500.0 - (dist - dist[599]) * 1000 * 0.12)
    ch = {"elapsedtime": list(t), "speed": [kmh] * n, "elapseddistance": list(dist), "elevation": list(e),
          "cadence": [cad] * n, "heartrate": [140.0] * n}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=list(tags), channels=ch,
                       metrics={"duration": float(t[-1]), "movingduration": float(n), "distance": float(dist[-1])})


def test_weekly_downhill_chart_has_steep_downhill_cadence_lines():
    # SP-237: two dashed right-axis lines, colours of their bars, trail mode only, bars unchanged
    import datetime as dt
    from backend.engine.wko5expr.render import render_chart
    from backend.tests.wko5_fakes import FakeDataset
    ch = _chart("training", "每週下坡衝擊負荷")
    assert ch["sports"] == ["trail"]
    assert [a["id"] for a in ch["axes"]] == ["CUSTOM等效 km", "steps/min"]
    by = {s["name"]: s for s in ch["series"]}
    for line, bar, cat in (("越野跑 陡下坡步頻", "越野跑", 'hastag("runningtrail")'),
                           ("登山健行 陡下坡步頻", "登山健行", 'hastag("hiking") or hastag("mountaineering")')):
        s = by[line]
        assert s["type"] == "line" and s["line_style"] == "dash" and s["y_axis"] == "steps/min"
        assert s["color"] == by[bar]["color"] and s["expression"] == CM.downhill_cadence_expr(cat)
    assert "rgrade < -0.08" in CM.DH_CAD_TIME_EXPR and CM.DH_CAD_MIN_S == 600
    d = ch["description"]
    assert "Van Hooren 2024" in d and "−8%" in d and all(k in d for k in ("怎麼看：", "看什麼：", "方法："))

    ws = [_dh_trail(dt.date(2026, 9, 1), 900, 55.0), _dh_trail(dt.date(2026, 9, 2), 900, 60.0),  # one week
          _dh_trail(dt.date(2026, 9, 9), 300, 60.0),                         # 5' of steep downhill: no point
          _dh_trail(dt.date(2026, 9, 16), 1200, 58.0, tags=("hiking",)),
          _dh_trail(dt.date(2026, 9, 22), 1200, 60.0),
          _dh_trail(dt.date(2026, 9, 28), 500, 50.0, pause=True)]   # 8'20" + a 15' pause: the pause doesn't count
    ds = FakeDataset(ws, dt.date(2026, 9, 29), settings={"runftp": 250.0, "runthr": 160.0})
    out = render_chart(ch, ds, ds.today - 40, ds.today)
    pts = {s["name"]: {x[:10]: y for x, y in s["data"]["points"] if y is not None} for s in out["series"]}
    unit = {s["name"]: s["unit"]["label"] for s in out["series"]}
    assert unit["越野跑 陡下坡步頻"] == "spm"
    assert pts["越野跑 陡下坡步頻"] == {"2026-08-31": pytest.approx(115.0), "2026-09-21": pytest.approx(120.0)}
    assert pts["登山健行 陡下坡步頻"] == {"2026-09-14": pytest.approx(116.0)}
    # the bars are the same expression as before (DOWNHILL_EXPR per category) and still drawn
    assert pts["越野跑"]["2026-09-07"] > 0 and pts["登山健行"]["2026-09-14"] > 0
    # English legend and help
    from backend.engine.wko5expr.customviews import parse_view
    from backend.engine.wko5expr.viewi18n import translate_view
    v = parse_view(_view("training"), ROOT / "views" / "training.json")
    en = translate_view(v, json.loads((ROOT / "views" / "i18n" / "en.json").read_text("utf-8"))["training"])
    c = next(c for d in en["dashboards"] for c in d["charts"] if c["id"] == "weekly-downhill-load")
    assert {"Trail run steep-downhill cadence", "Hike steep-downhill cadence"} <= {s["name"] for s in c["series"]}
    assert "Van Hooren 2024" in c["description"] and "−8%" in c["description"]


def test_acute_chronic():
    assert CM.acute_chronic([1.0] * 28) == pytest.approx(1.0)
    assert CM.acute_chronic([0.0] * 21 + [4.0] * 7) == pytest.approx(4.0)
    assert CM.acute_chronic([0.0] * 28) is None


def test_climb_rate():
    g = [0.1] * 700 + [-0.1] * 700
    de = [0.1] * 700 + [-0.2] * 700
    dt_ = [1.0] * 1400
    v = [3.0] * 1400
    assert CM.climb_rate(g, de, dt_, v) == pytest.approx(360.0)
    assert CM.climb_rate(g, de, dt_, v, uphill=False) == pytest.approx(720.0)
    hr = [170.0] * 1400
    assert CM.climb_rate(g, de, dt_, v, hr, 160.0) is None          # all above LTHR
    assert CM.climb_rate(g[:500], de[:500], dt_[:500], v[:500]) is None   # < 10 min
