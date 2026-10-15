"""SP-363 R2: 設定 → 課表功率區間 (engine/power_zone_plan.py) — Palladino (default) / Stryd 5 zones /
the classic 7 levels / 個人化區間 (the runner's own PD model; algorithm in the core,
algorithms/wko5_levels). The 課表's power texts, the structure's auto power steps (→ the watch) and the
power charts' default follow it; each kind keeps its intent and takes the nearest level.
Synthetic only (no WKO5 folder, no ~/.trailruncoach)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from backend.engine import power_zone_plan as PZP
from backend.engine import zones as Z

ROOT = Path(__file__).resolve().parents[2]
PARAMS = [15000.0, 20.0, 250.0, 25.0, 2400.0, -30.0]      # FRC, tau1, FTP, tau2, TTE, D (test_wko5expr_functions)
CP = 250.0


def pm(system: str, levels=None) -> dict:
    return {"system": system, "setting": system, "fallback": "",
            "levels": levels if levels is not None else PZP.static_levels(system)}


def personal_pm(cp: float = CP) -> dict:
    from backend.engine.algorithms.wko5_levels import levels
    lv = levels(PARAMS)
    rows = [[str(i + 1), n, lo / cp, None if hi != hi else hi / cp] for i, ((lo, hi), n) in
            enumerate(zip(lv, PZP.PERSONAL_NAMES))]
    return pm("personal", rows)


def bands(p) -> dict:
    return {tid: PZP.target_band(tid, plo, phi, p) for tid, _n, plo, phi, *_ in Z.WORKOUT_TARGETS}


# ---------------------------------------------------------------------------
# the levels
# ---------------------------------------------------------------------------

def test_personal_levels_are_the_pd_models_ten_running_levels():
    """WKO5's running iLevels (SP-661; formulas.md §6.9 for the part above threshold): ≤ threshold fixed
    fractions of the model's FTP; above it the model's power at tC (aerobic share 0.632), 3·ln2·τ1 and
    ln2·τ1; the top open."""
    from backend.engine.algorithms import wko5_levels as L
    from backend.engine.algorithms.wko5_pdmodel import aerobic, model
    lv = L.levels(PARAMS)
    F = PARAMS[2]
    assert len(lv) == 10 and lv[0][0] == 0.0 and math.isnan(lv[-1][1])
    assert [hi for _lo, hi in lv[:6]] == pytest.approx([x * F for x in (0.65, 0.75, 0.801, 0.88, 0.95, 1.02)])
    d = L.durations(PARAMS)
    assert d["tA"] == pytest.approx(math.log(2) * 20.0) and d["tB"] == pytest.approx(3 * math.log(2) * 20.0)
    assert aerobic(PARAMS, d["tC"]) / model(PARAMS, d["tC"]) == pytest.approx(0.632, abs=1e-6)
    assert lv[6] == (pytest.approx(1.02 * F), pytest.approx(model(PARAMS, d["tC"])))
    assert lv[7][1] == pytest.approx(model(PARAMS, d["tB"])) and lv[8][1] == pytest.approx(model(PARAMS, d["tA"]))
    for (_a, hi), (lo, _b) in zip(lv, lv[1:]):
        assert hi == pytest.approx(lo)                       # contiguous, easy → hard
    assert L.levels(None) is None and L.levels([1, 0, 0, 25, 2400, 0]) is None


def test_the_evaluator_reads_the_same_table():
    """levelfrom / levelto(meanmax(power), i) — a user's own view — and the 課表 share one table."""
    from backend.engine.algorithms import wko5_levels as L
    from backend.tests.test_wko5expr_functions import _ds, _ev, model_curve
    e = _ev(_ds([]))
    c = model_curve()
    ours = L.levels(e._fit(c)["params"])
    got = e._ilevels(c)
    assert [g[0] for g in got] == ["Recovery", "Warm-up", "Easy Aerobic", "Endurance", "Extensive Threshold",
                                   "Intensive Threshold", "FRC/FTP", "FRC", "Pmax/FRC", "Pmax"]
    for (_n, lo, hi), (a, b) in zip(got, ours):
        assert lo == pytest.approx(a) and (hi == pytest.approx(b) or (math.isnan(hi) and math.isnan(b)))


def test_the_classic_levels_are_the_published_seven():
    """< 55 recovery, 56–75 endurance, 76–90 tempo, 91–105 threshold, 106–120 VO2max, 121–150
    anaerobic, > 150 neuromuscular (% threshold power): the edges are each level's lower whole percent."""
    assert [(z, lo, hi) for z, _n, lo, hi in PZP.CLASSIC_LEVELS] == [
        ("1", 0.0, 0.56), ("2", 0.56, 0.76), ("3", 0.76, 0.91), ("4", 0.91, 1.06), ("5", 1.06, 1.21),
        ("6", 1.21, 1.51), ("7", 1.51, None)]
    assert "Coggan" in PZP.CLASSIC_SOURCE


# ---------------------------------------------------------------------------
# each kind → the nearest level
# ---------------------------------------------------------------------------

def test_palladino_changes_nothing():
    pal = pm("palladino")
    for tid, _n, plo, phi, *_ in Z.WORKOUT_TARGETS:
        assert PZP.target_band(tid, plo, phi, pal) == (plo, phi), tid
        assert PZP.target_band(tid, plo, phi, None) == (plo, phi), tid
    assert PZP.band(1.06, 1.12, pal) == (1.06, 1.12)


def test_stryd_bands():
    assert bands(pm("stryd")) == {
        "recovery": (None, 0.80), "z2": (0.65, 0.80), "long": (0.65, 0.80), "trail": (0.65, 0.80),
        "climb": (0.90, 1.00), "downhill": (None, None), "hill": (1.00, 1.15), "threshold": (0.90, 1.00),
        "supra": (1.00, 1.15), "vo2": (1.00, 1.15)}


def test_classic_bands():
    assert bands(pm("classic")) == {
        "recovery": (None, 0.76), "z2": (0.56, 0.76), "long": (0.56, 0.76), "trail": (0.56, 0.76),
        "climb": (0.91, 1.06), "downhill": (None, None), "hill": (0.91, 1.06), "threshold": (0.91, 1.06),
        "supra": (0.91, 1.06), "vo2": (1.06, 1.21)}


def test_personal_bands():
    p = personal_pm()
    b = bands(p)
    lv = p["levels"]
    # SP-661 (running levels): easy kinds → warm-up 65–75 %, 恢復跑 up to 75 %
    assert b["recovery"] == (None, 0.75) and b["z2"] == b["long"] == b["trail"] == (0.65, 0.75)
    assert b["climb"] == (0.88, 0.95)                         # sub-threshold
    assert b["threshold"] == b["hill"] == (0.95, 1.02)
    # supra 101–106 % overlaps above threshold (VO2max) 1.02 F – P(tC) the most (was the FTP level 95–105)
    assert b["vo2"] == b["supra"] == (1.02, round(lv[6][3], 4))


def test_a_mapped_band_maps_to_itself():
    """The push re-reads an interval's band from the session text: mapping it again changes nothing."""
    for p in (pm("stryd"), pm("classic"), personal_pm()):
        for tid, _n, plo, phi, *_ in Z.WORKOUT_TARGETS:
            lo, hi = PZP.target_band(tid, plo, phi, p)
            if lo is not None:
                assert PZP.band(lo, hi, p) == (lo, hi), (p["system"], tid)


# ---------------------------------------------------------------------------
# what follows the setting
# ---------------------------------------------------------------------------

def _variant(cls: str):
    from backend.engine import interval_library as IL
    for rung in IL.LIBRARY:
        f = IL.fit(rung, None)
        if f["variant"].cls == cls:
            return f
    raise AssertionError(cls)


def test_interval_text_follows_the_set():
    from backend.engine import interval_library as IL
    f = _variant("Z5")
    th = {"cp": 260.0, "lthr": 165.0, "aet": 140.0}
    pal = IL.session_for(f, th)["target"]
    assert IL.session_for(f, {**th, "power_model": pm("palladino")})["target"] == pal
    st = IL.session_for(f, {**th, "power_model": pm("stryd")})
    assert "功率 260–299 W（100–115% CP）" in st["target"] and "100–115% CP" in st["detail"]
    cl = IL.session_for(f, {**th, "power_model": pm("classic")})
    assert "功率 276–315 W（106–121% CP）" in cl["target"]


def test_the_structure_and_the_watch_follow_the_set():
    """An auto band step (a library rep) resolves in the set; the watch gets the set's band through
    zones.watch_band (upper × 1.05, a Zone 5 band open to 116 %); Palladino as before."""
    from backend.engine import workout_steps as WS
    st = {"kind": "work", "dur": {"type": "time", "value": 600},
          "target": {"type": "auto", "intent": "band", "lo": 0.95, "hi": 1.01, "cls": "Z3near"}}
    c0 = WS.Ctx(cp=250.0, lthr=165.0, basis="power")
    r0 = WS.resolve(st, c0)
    assert (r0.lo, r0.hi) == (238, 252) and r0.intensity == ("power", 238, 265)
    r1 = WS.resolve(st, WS.Ctx(cp=250.0, lthr=165.0, basis="power", pzm=pm("stryd")))
    assert (r1.lo, r1.hi) == (225, 250) and r1.intensity == ("power", 225, 262)
    assert "3 Threshold" in r1.sub
    r2 = WS.resolve(st, WS.Ctx(cp=250.0, lthr=165.0, basis="power", pzm=pm("classic")))
    assert (r2.lo, r2.hi) == (228, 265) and "閾值" in r2.sub
    easy = {"kind": "work", "dur": {"type": "time", "value": 1800},
            "target": {"type": "auto", "intent": "easy", "plo": 0.65, "phi": 0.88}}
    assert WS.resolve(easy, WS.Ctx(cp=250.0, basis="power")).lo == 162                  # Palladino long 65–88
    r3 = WS.resolve(easy, WS.Ctx(cp=250.0, basis="power", pzm=pm("classic")))
    assert (r3.lo, r3.hi) == (140, 190)                                                  # endurance 56–76


def test_the_coros_push_follows_the_set():
    from backend.engine import interval_library as IL
    from backend.sync import coros_workouts as CW
    f = _variant("Z5")
    v = f["variant"]
    s = {"kind": "quality", "title": IL.title(v), "minutes": 50, "variant_key": v.key, "basis": "power"}
    th = {"cp": 260.0, "lthr": 165.0, "aet": 140.0}

    def work(t):
        steps = CW.session_steps(s, CW.Thresholds.of(t))
        for x in steps:
            if isinstance(x, CW.Step) and x.kind == CW.EX_TRAIN and x.intensity:
                return x.intensity
    lo, hi = Z.watch_band(v.lo, v.hi)
    assert work(th) == ("power", round(lo * 260), round(hi * 260))
    lo, hi = Z.watch_band(1.00, 1.15)
    assert work({**th, "power_model": pm("stryd")}) == ("power", round(lo * 260), round(hi * 260))
    easy = {"kind": "long", "title": "長跑", "minutes": 90, "basis": "power"}
    it = CW.session_steps(easy, CW.Thresholds.of({**th, "power_model": pm("stryd")}))[0].intensity
    assert it == ("power", round(0.65 * 260), round(0.80 * 260))


def test_training_targets_follow_the_setting(monkeypatch, tmp_path):
    import datetime as dt
    from datetime import datetime, timezone
    from backend.engine import overview as O
    from backend.engine.planning import Plan, Threshold
    from backend.engine.projection import target_texts
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    (d / "000.fit").write_bytes(build_run(start=datetime(2026, 9, 28, 0, tzinfo=timezone.utc), seconds=1800,
                                          power=180, stryd=True))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=False), today=dt.date(2026, 9, 30),
                          estimate_thresholds=False, tz=timezone.utc)
    ds.plan = Plan(thresholds=[Threshold("2026-09-01", lthr=165, cp=250, note="test")])
    end = int(date_to_day(dt.date(2026, 9, 30)))
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "palladino")
    pal = Z.training_targets(ds, end)
    assert pal["power_zones"] == "Palladino % CP" and pal["power_model"]["system"] == "palladino"
    thr = next(r for r in pal["rows"] if r["id"] == "threshold")["power"]
    assert thr == [pytest.approx(237.5), pytest.approx(252.5)]
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "stryd")
    st = Z.training_targets(ds, end)
    assert st["power_model"]["system"] == "stryd" and "Stryd" in st["power_zones"]
    thr = next(r for r in st["rows"] if r["id"] == "threshold")["power"]
    assert thr == [pytest.approx(225.0), pytest.approx(250.0)]
    assert O._targets(st)["threshold"].startswith("功率 225–250 W")
    assert target_texts({"cp": 250.0, "lthr": 165.0, "power_model": st["power_model"]})["threshold"] \
        .startswith("功率 225–250 W")


@pytest.mark.needs_core
def test_personal_without_a_fit_falls_back_to_palladino(monkeypatch):
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "personal")
    m = PZP.plan_model(object(), None, 250.0)                # a dataset without a PD fit
    assert m["setting"] == "personal" and m["system"] == "palladino" and m["fallback"] == PZP.NO_FIT


def test_personal_without_the_core_falls_back_to_palladino(monkeypatch):
    """The public mirror (no calculation core): the 課表 runs on Palladino and says why."""
    import backend.engine.core_stub as CS
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "personal")
    monkeypatch.setattr(CS, "core_included", lambda: False)
    m = PZP.plan_model(object(), None, 250.0)
    assert m["system"] == "palladino" and m["fallback"] == PZP.NO_CORE


@pytest.mark.needs_core
def test_personal_reads_the_pd_fit_in_effect(monkeypatch):
    import datetime as dt

    class DS:
        def stryd_pd_params(self, day):
            return {"params": PARAMS, "pmax": 700.0, "tte": 2400.0, "mftp": 250.0, "date": dt.date(2026, 9, 1)}
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "personal")
    m = PZP.plan_model(DS(), dt.date(2026, 9, 30), 260.0)
    assert m["system"] == "personal" and m["basis"]["date"] == "2026-09-01"
    assert m["levels"][1][2:] == [pytest.approx(0.65 * 250 / 260), pytest.approx(0.75 * 250 / 260)]
    assert m["levels"][-1][3] is None
    # the intent is read in the model's own frame: an easy run → warm-up (65–75 % of the model's FTP),
    # a threshold session → the threshold level (95–102 %), whatever the plan's CP — written × the CP (260)
    k = 250 / 260
    assert PZP.target_band("z2", 0.65, 0.80, m) == (round(0.65 * k, 4), round(0.75 * k, 4))
    assert PZP.target_band("threshold", 0.95, 1.01, m) == (round(0.95 * k, 4), round(1.02 * k, 4))
    assert m["scale"] == pytest.approx(k)


def test_charts_default_to_the_setting(monkeypatch):
    from backend.engine.panels import activity_charts as A
    from backend.engine.panels import period_zones as PZ
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "classic")
    assert A.default_model("power") == "classic" and A.default_model("hr") == "frielhr"
    assert PZ.model("power", None)["id"] == "classic"
    assert PZ.model("power", "stryd")["id"] == "stryd"           # a choice on the chart still wins
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "palladino")
    assert A.default_model("power") == "palladino"


def test_activity_chart_offers_classic_and_personal():
    import numpy as np
    from backend.engine.panels import activity_charts as A
    from backend.tests.test_activity_charts import _ds, _run
    p = np.repeat([150.0, 210.0, 240.0, 260.0, 300.0], 300)
    t = np.arange(len(p), dtype=float)
    ds = _ds(_run(t, np.full(len(p), 150.0), p))
    res = A.zone_times(ds, ds.workouts[0], "power")
    cl = next(m for m in res["models"] if m["id"] == "classic")
    assert cl["available"] and len(cl["rows"]) == 7 and cl["total_s"] == len(p)
    pers = next(m for m in res["models"] if m["id"] == "personal")
    assert not pers["available"] and pers["reason"]          # a fake dataset has no PD fit: says why


# ---------------------------------------------------------------------------
# the settings page and the API
# ---------------------------------------------------------------------------

def test_the_settings_page_has_the_choice_in_both_languages():
    html = (ROOT / "backend" / "static" / "settings.html").read_text("utf-8")
    assert 'id="pz-model"' in html and "/api/v1/plan/power-zones" in html and 'id="thr-pz-plan"' in html
    for lang in ("zh-TW", "en"):
        cat = json.loads((ROOT / "backend" / "static" / "i18n" / lang / "settings.json").read_text("utf-8"))
        for k in ("thr.pz_model", "thr.pz_model_help", "thr.pz_model_saved", "thr.pz_in_use", "thr.pz_personal_basis"):
            assert cat.get(k), (lang, k)
    en = json.loads((ROOT / "backend" / "i18n" / "locales" / "en.json").read_text("utf-8"))
    for msgid in (*PZP.PERSONAL_NAMES, *PZP.TITLE.values(), PZP.PERSONAL_SOURCE, PZP.NO_FIT, PZP.NO_CORE,
                  *(n for _z, n, _lo, _hi in PZP.CLASSIC_LEVELS)):
        assert en.get(msgid), msgid


def test_no_wko5_words_on_screen():
    """The user-facing words of the new sets: no WKO5, no iLevels (SP-363 Q2-2: 個人化區間)."""
    en = json.loads((ROOT / "backend" / "i18n" / "locales" / "en.json").read_text("utf-8"))
    texts = [*PZP.PERSONAL_NAMES, *PZP.TITLE.values(), PZP.PERSONAL_SOURCE, PZP.NO_FIT, PZP.NO_CORE,
             PZP.CLASSIC_SOURCE]
    texts += [en.get(x) or "" for x in texts]
    for lang in ("zh-TW", "en"):
        cat = json.loads((ROOT / "backend" / "static" / "i18n" / lang / "settings.json").read_text("utf-8"))
        texts += [v for k, v in cat.items() if k.startswith("thr.pz")]
    for x in texts:
        assert "wko" not in x.lower() and "ilevel" not in x.lower(), x


def test_the_public_part_does_not_carry_the_algorithm():
    """power_zone_plan (public) reaches the levels only through the core module, lazily; the core
    module is one the mirror leaves out (exclude.txt: backend/engine/algorithms/wko5_*.py)."""
    src = (ROOT / "backend" / "engine" / "power_zone_plan.py").read_text("utf-8")
    assert "0.632" not in src and "ln2" not in src and "math.log(2)" not in src
    assert "from backend.engine.algorithms.wko5_levels import levels" in src
    ex = (ROOT / "deploy" / "public-mirror" / "exclude.txt").read_text("utf-8")
    assert "backend/engine/algorithms/wko5_*.py" in ex


def test_put_power_zones_saves_and_replans(monkeypatch):
    from backend.api import plan as API
    from backend.engine import plan_auto as PA
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db, run
    seen = []
    monkeypatch.setattr(PA, "after_thresholds", lambda: seen.append(1))
    monkeypatch.setattr(API, "_estimate_dataset", lambda: None)

    async def go():
        db = await make_db(logged_in=False)
        out = []
        for system in ("stryd", "stryd", "personal"):
            out.append(await API.put_power_zones(API.PowerZonesIn(system=system), db))
        return out, await SettingsRepository(db, 1).get(PZP.SETTING_KEY)
    # the view reads the setting from the store; here the stored value is checked directly
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "stryd")
    (a, b, c), stored = run(go())
    assert stored == "personal"
    assert seen == [1, 1]                                    # a switch re-plans; the same set again does not
    assert [x["id"] for x in a["choices"]] == list(PZP.SYSTEMS)
    with pytest.raises(Exception):
        run(API.put_power_zones(API.PowerZonesIn(system="nope"), None))


def test_pd_model_params_fits_the_same_points_as_pd_model():
    """The 個人化區間 refit (racepower.athlete.pd_model_params) is pd_model line for line but its
    return (pd_model stays untouched: its code is in the PD memo's key, an edit refits everything)."""
    import ast
    import inspect
    from backend.engine.racepower import athlete as A

    def body(fn):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        stmts = tree.body[0].body[1:]                          # without the docstring
        return [ast.dump(x) for x in stmts[:-1]], stmts[-1]
    a, ra = body(A.pd_model)
    b, rb = body(A.pd_model_params)
    assert a == b
    assert isinstance(ra, ast.Return) and isinstance(rb, ast.Return)
    keys = {k.value for k in rb.value.keys}
    assert {"mftp", "tte", "frc", "pmax", "params"} <= keys


def test_a_switch_of_the_set_starts_a_real_run(monkeypatch):
    """The same activities, the 課表功率區間 switched: the run does not stop at 「沒有新的活動」 — it
    re-plans (the regenerated sessions take the new set; the pushed ones go out of date by
    fingerprint and are re-pushed by push_window), and remembers the set."""
    import copy
    import datetime as dt
    from backend.engine import plan_auto as PA
    from backend.sync import coros_workouts as CW
    from backend.tests.test_coros_workouts import make_db, run as arun
    from backend.tests.test_plan_auto import Box, Push, base_inputs
    monkeypatch.setattr(CW, "real_today", lambda: dt.date(2026, 9, 30))

    def inputs(system):
        inp = base_inputs()
        for t in (inp["thresholds"], inp["cur"]["thresholds"]):
            t["power_model"] = copy.deepcopy(pm(system))
        return inp
    b = Box(monkeypatch, inputs("palladino"), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        assert (await PA.settings(db))["state"]["power_zone"] == "palladino"
        r = await PA.run(db)
        assert r["status"] == "noop" and r.get("reason") == "沒有新的活動"          # nothing new
        b.inp = inputs("stryd")
        r = await PA.run(db, trigger="cp_change")
        assert r.get("reason") != "沒有新的活動"
        assert (await PA.settings(db))["state"]["power_zone"] == "stryd"
    arun(go())


def test_the_changes_of_a_switch_say_why():
    from backend.engine import plan_auto as PA
    items = [{"action": "changed", "before": {"target": "功率 255–263 W"}, "uid": "a"},
             {"action": "changed", "before": {"day": "2026-10-16"}, "uid": "b"},              # a move: not ours
             {"action": "changed", "before": {"target": "x"}, "rule": "adapt", "uid": "c"}]  # another rule wins
    assert PA.mark_power_zone(items, "palladino", "stryd") == 1
    assert items[0]["rule"] == "power_zone" and items[0]["reason"].startswith("課表功率區間：Palladino 10 區（% CP） → Stryd 5 區")
    assert "rule" not in items[1] and items[2]["rule"] == "adapt"
