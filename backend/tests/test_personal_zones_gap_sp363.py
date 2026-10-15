"""SP-363 completion (問答 Q3-1 (a)+(c), Q2-3, Q3-2; the user 2026-10-15):
- the 個人化區間 stay on the model's threshold power; when it is more than GAP_WARN (10 %) away from the
  課表's CP the settings page says so (engine/power_zone_plan.gap_warning, api/plan.power_zones_view);
- the level names are the ones the user settled (ten since SP-661, the running table);
- the comparison with WKO5's own numbers: the skeleton reads a fixture the user writes and skips
  without it (backend/tests/realdata/fixtures/personal_levels_wko5.json).
Synthetic only."""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pytest

from backend.engine import power_zone_plan as PZP

ROOT = Path(__file__).resolve().parents[2]
PARAMS = [15000.0, 20.0, 250.0, 25.0, 2400.0, -30.0]      # FRC, tau1, FTP, tau2, TTE, D
WKO5_FIXTURE = ROOT / "backend" / "tests" / "realdata" / "fixtures" / "personal_levels_wko5.json"


def _personal(cp: float, mftp: float = 250.0) -> dict:
    return {"system": "personal", "setting": "personal", "fallback": "", "levels": [["1", "x", 0.0, None]],
            "scale": mftp / cp, "basis": {"mftp": mftp}}


# ---------------------------------------------------------------------------
# Q3-1 (a): the model threshold power stays the base; (c): a gap over 10 % is said
# ---------------------------------------------------------------------------

def test_the_base_stays_the_model_threshold_power(monkeypatch):
    """(a): the levels are fractions of the model's FTP whatever the 課表's CP — the CP only writes them
    as % CP (the demo: model FTP 198 W, CP 260 W → the threshold level 188–202 W, not 247–265; SP-661:
    running levels, 95–102 %)."""
    from backend.engine.core_stub import core_included
    if not core_included():
        pytest.skip("needs the calculation core")

    class DS:
        def stryd_pd_params(self, day):
            p = list(PARAMS)
            p[2] = 198.0
            return {"params": p, "pmax": 700.0, "tte": 2400.0, "mftp": 198.0, "date": dt.date(2026, 9, 1)}
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "personal")
    m = PZP.plan_model(DS(), dt.date(2026, 9, 30), 260.0)
    lo, hi = m["levels"][5][2:]
    assert (round(lo * 260), round(hi * 260)) == (188, 202)
    w = PZP.gap_warning(m, 260.0)
    assert "198" in w and "260" in w and "24%" in w


def test_a_gap_over_ten_percent_is_said():
    assert PZP.GAP_WARN == 0.10
    assert PZP.gap_warning(_personal(260.0, 198.0), 260.0).startswith("模型閾值功率 198 W 比課表的 CP 260 W 低 24%")
    assert "高 15%" in PZP.gap_warning(_personal(200.0, 230.0), 200.0)
    # within 10 %: nothing; exactly 10 %: nothing (over, not at)
    assert PZP.gap_warning(_personal(250.0, 230.0), 250.0) == ""
    assert PZP.gap_warning(_personal(250.0, 225.0), 250.0) == ""
    # only the 個人化區間 in effect has a model base
    for system in ("palladino", "stryd", "classic"):
        assert PZP.gap_warning({"system": system, "scale": 0.5}, 260.0) == ""
    assert PZP.gap_warning(None, 260.0) == "" and PZP.gap_warning(_personal(260.0, 198.0), None) == ""


def test_the_settings_api_and_page_carry_the_warning(monkeypatch):
    from backend.api import plan as API
    from backend.engine import zones as Z
    monkeypatch.setattr(PZP, "plan_model", lambda ds, day, cp: {**_personal(260.0, 198.0), "levels": []})
    monkeypatch.setattr(Z, "cp_in_effect", lambda ds, end: {"value": 260.0})
    v = API.power_zones_view(object(), dt.date(2026, 10, 15))
    assert v["warning"].startswith("模型閾值功率 198 W")
    monkeypatch.setattr(PZP, "plan_model", lambda ds, day, cp: {"system": "palladino", "setting": "palladino",
                                                                "fallback": "", "levels": []})
    assert API.power_zones_view(object(), dt.date(2026, 10, 15))["warning"] == ""
    html = (ROOT / "backend" / "static" / "settings.html").read_text("utf-8")
    assert 'id="pz-model-warn"' in html and "r.warning" in html


def test_the_warning_in_english_has_no_wko5_words():
    from backend.i18n import use_locale
    with use_locale("en"):
        a = PZP.gap_warning(_personal(260.0, 198.0), 260.0)
        b = PZP.gap_warning(_personal(200.0, 230.0), 200.0)
    assert a.startswith("Your modelled threshold power 198 W is 24% below the plan's CP 260 W")
    assert "15% above" in b
    for x in (a, b, PZP.GAP_LOW, PZP.GAP_HIGH):
        assert "wko" not in x.lower() and "ilevel" not in x.lower()


# ---------------------------------------------------------------------------
# Q2-3: the level names
# ---------------------------------------------------------------------------

def test_the_level_names_are_the_ones_the_user_settled():
    """SP-363 Q2-3's style, ten for the running table (SP-661)."""
    assert PZP.PERSONAL_NAMES == ("恢復", "暖身", "輕鬆有氧", "耐力", "準閾值", "閾值", "超閾值（VO2max）",
                                  "無氧容量", "無氧衝刺", "最大功率")
    en = json.loads((ROOT / "backend" / "i18n" / "locales" / "en.json").read_text("utf-8"))
    assert [en[n] for n in PZP.PERSONAL_NAMES] == ["Recovery", "Warm-up", "Easy aerobic", "Endurance",
                                                   "Sub-threshold", "Threshold", "Supra-threshold (VO2max)",
                                                   "Anaerobic capacity", "Anaerobic sprint", "Max power"]


# ---------------------------------------------------------------------------
# Q3-2: WKO5's own numbers (the user writes the fixture from WKO5's screen; skips without it)
# ---------------------------------------------------------------------------

def _wko5() -> dict:
    if not WKO5_FIXTURE.exists():
        pytest.skip(f"no WKO5 level numbers yet: write {WKO5_FIXTURE.relative_to(ROOT)} from WKO5's screen "
                    "(backend/tests/realdata/test_real_personal_levels.py's doc)")
    ref = json.loads(WKO5_FIXTURE.read_text("utf-8"))
    assert len(ref["levels"]) == 10           # WKO5's running iLevels (SP-661)
    return ref


def test_wko5_levels_up_to_threshold_are_its_threshold_power_times_the_fixed_fractions():
    """Levels 1–6 need only WKO5's modelled threshold power (its 「mftp」 on the same screen, else the
    one its own level 5 implies): 0.65 / 0.75 / 0.801 / 0.88 / 0.95 / 1.02 × it, within 1 W — the constants and
    which end is inclusive."""
    from backend.engine.core_stub import core_included
    if not core_included():
        pytest.skip("needs the calculation core")
    from backend.engine.algorithms.wko5_levels import FTP_EDGES
    ref = _wko5()
    lv = ref["levels"]
    F = float(ref.get("mftp") or lv[4][0] / FTP_EDGES[3])
    assert lv[0][0] == pytest.approx(0.0, abs=1.0)
    for i, edge in enumerate(FTP_EDGES):
        assert lv[i][1] == pytest.approx(edge * F, abs=1.0), (i + 1, "to")
        assert lv[i + 1][0] == pytest.approx(edge * F, abs=1.0), (i + 2, "from")


def test_wko5_levels_from_its_model_parameters():
    """All ten from the model's parameters, when the fixture carries them (「params」: FRC J, τ1, mFTP,
    τ2, TTE, D — e.g. from a WKO5 table chart of the model's parameters); skips without them."""
    from backend.engine.core_stub import core_included
    if not core_included():
        pytest.skip("needs the calculation core")
    from backend.engine.algorithms.wko5_levels import levels
    ref = _wko5()
    if not ref.get("params"):
        pytest.skip("the fixture has no model parameters (「params」); levels 6–9 are compared by "
                    "realdata/test_real_personal_levels.py from the athlete folder")
    got = levels(ref["params"])
    for i, ((glo, ghi), (wlo, whi)) in enumerate(zip(got, ref["levels"])):
        assert glo == pytest.approx(wlo, abs=1.0), (i + 1, "from")
        if whi is None:
            assert math.isnan(ghi)
        else:
            assert ghi == pytest.approx(whi, abs=1.0), (i + 1, "to")
