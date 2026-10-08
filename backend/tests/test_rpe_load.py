"""
「負荷」 entered by RPE (SP-57): the five levels → Borg CR-10, each level its own TSS per hour —
the default from the TSS definition (IF² × 100 at the level's intensity), fitted per level on the
athlete's watch / COROS RPE vs the activity's TSS and shrunk toward the default, never decreasing
from one level to the next (engine/rpe_load.py); the step in engine/workout_steps.py (normalize,
timing, view), the push still sending COROS TL, and the editor's payload
(static/workout_editor.js). Synthetic data only.
"""
import datetime as dt
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import coros_rpe as CR
from backend.engine import coros_tl as TL
from backend.engine import rpe_load as RL
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 8)
IDS = ["easy", "moderate", "hard", "very_hard", "max"]


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    """No stored fit read from any DB: the defaults unless a test says otherwise."""
    monkeypatch.setattr(RL, "current", lambda user_id=1: RL.Model())
    monkeypatch.setattr(TL, "current", lambda user_id=1: TL.Model.of())


def _doc(*items):
    return {"items": list(items)}


def _rpe_step(level="very_hard", minutes=40, **kw):
    return {"id": "L", "kind": "work", "dur": {"type": "load", "value": 1, "rpe": level, "min": minutes},
            "target": {"type": "none"}, **kw}


def _rate(level):
    return 100.0 * RL.DEFAULT_IF[level] ** 2


# ---------------------------------------------------------------------------
# the levels and their defaults (TSS definition: TSS per hour = IF² × 100)
# ---------------------------------------------------------------------------

def test_five_levels_map_to_cr10_anchors():
    assert [(k, v) for k, _l, v in RL.LEVELS] == [("easy", 2), ("moderate", 4), ("hard", 5),
                                                    ("very_hard", 7), ("max", 10)]
    assert [RL.LABEL[k] for k in RL.CR10] == ["輕鬆", "稍累", "累", "很累", "極限"]


def test_defaults_per_level_from_if_squared():
    assert list(RL.DEFAULT_IF) == IDS
    for k in IDS:
        assert RL.DEFAULT_TSS_H[k] == pytest.approx(100.0 * RL.DEFAULT_IF[k] ** 2)
        assert RL.Model().rate(k) == pytest.approx(RL.DEFAULT_TSS_H[k])
        # every default says where its intensity comes from, and that the point is an estimate
        assert RL.IF_SRC[k] and "推估" in RL.IF_SRC[k]
    # the owner's examples (2026-10-07): 輕鬆 ≈ 55, 很累 ≈ 90 TSS per hour
    assert round(RL.DEFAULT_TSS_H["easy"]) == 55 and round(RL.DEFAULT_TSS_H["very_hard"]) == 90
    v = [RL.DEFAULT_TSS_H[k] for k in IDS]
    assert all(a < b for a, b in zip(v, v[1:]))
    # the IF an RPE-entered step implies stays inside the step's clamp
    assert all(WS.RPE_IF_RANGE[0] <= RL.DEFAULT_IF[k] <= WS.RPE_IF_RANGE[1] for k in IDS)


def test_tss_is_the_level_rate_times_the_hours():
    m = RL.Model()
    assert m.tss("very_hard", 40) == round(_rate("very_hard") * 40 / 60, 1)
    assert m.tss("easy", 60) == round(_rate("easy"), 1)
    assert m.tss("nope", 40) is None and m.tss("hard", 0) is None and m.tss("hard", None) is None
    assert RL.to_tss("max", 30, m) == round(_rate("max") / 2, 1)


# ---------------------------------------------------------------------------
# samples: watch RPE (FIT, 1–10) and COROS's post-run rating (1–5, SP-231) → a level
# ---------------------------------------------------------------------------

def test_coros_feel_maps_onto_the_levels_like_sp231():
    # COROS 1 Very Light … 5 Max Effort → workout_files.rpe 2 / 4 / 5 / 7 / 10 → the five levels in order
    for feel, k in zip(range(1, 6), IDS):
        assert RL.level_of_rpe(CR.TO_RPE[feel]) == k
    # a FIT's own RPE 1–10: two values per level, 6 with 5 (Seiler's zone 2 is session RPE 5–6)
    assert [RL.level_of_rpe(r) for r in range(1, 11)] == ["easy", "easy", "moderate", "moderate", "hard", "hard",
                                                         "very_hard", "very_hard", "max", "max"]
    assert RL.level_of_rpe(6.4) == "hard" and RL.level_of_rpe(6.5) == "very_hard"
    assert RL.level_of_rpe(None) is None and RL.level_of_rpe(0) is None and RL.level_of_rpe(11) is None


def test_samples_filters():
    rows = [{"date": "2026-10-01", "rpe": 5, "hours": 1.0, "tss": 80},       # kept: hard, 80 TSS/h
            {"date": "2026-10-01", "rpe": None, "hours": 1.0, "tss": 90},    # no RPE
            {"date": "2026-10-01", "rpe": 5, "hours": 0.1, "tss": 10},       # < 10 min
            {"date": "2026-10-01", "rpe": 5, "hours": 1.0, "tss": 0},        # no TSS
            {"date": "2026-10-01", "rpe": 2, "hours": 1.0, "tss": 400}]      # 400 TSS/h: mis-recorded
    s = RL.samples(rows)
    assert len(s) == 1 and s[0]["level"] == "hard" and s[0]["y"] == pytest.approx(80.0) and s[0]["h"] == 1.0


# ---------------------------------------------------------------------------
# the per-level fit with shrinkage
# ---------------------------------------------------------------------------

def _rows(level_rpe, n, rate, start=TODAY, thr=300.0):
    """n activities rated `level_rpe`, each at `rate` TSS per hour, one a day back from `start`."""
    out = []
    for i in range(n):
        h = 0.5 + (i % 3) * 0.5
        out.append({"date": (start - dt.timedelta(days=i)).isoformat(), "rpe": level_rpe, "hours": h,
                    "tss": rate * h, "thr": thr})
    return out


def _n_eff(n, start=TODAY):
    """Σ recency weights of _rows(…, n, …): one a day back from `start` (half-life TL.HALF_LIFE_DAYS)."""
    return sum(0.5 ** (((TODAY - start).days + i) / TL.HALF_LIFE_DAYS) for i in range(n))


def test_fit_with_enough_data_moves_toward_the_athlete():
    new, rep = RL.refit(_rows(2, 40, 70.0), TODAY)            # 40 easy runs at 70 TSS/h
    e = new["levels"]["easy"]
    w = _n_eff(40) / (_n_eff(40) + RL.SHRINK_K)                # the effective sample size (recency weights)
    assert e["n_eff"] == pytest.approx(_n_eff(40), abs=1e-3)
    want = math.exp(w * math.log(70.0) + (1 - w) * math.log(_rate("easy")))
    assert e["n"] == 40 and e["w"] == pytest.approx(w, abs=1e-4) and e["personal"] == pytest.approx(70.0)
    assert e["tss_h"] == pytest.approx(want, abs=0.01) and abs(e["tss_h"] - 70) < abs(e["tss_h"] - _rate("easy"))
    m = RL.Model.of(new)
    assert m.fitted and m.level("easy")["fitted"] and m.rate("easy") == pytest.approx(want, abs=0.01)
    # the other levels have no data: their defaults
    for k in IDS[1:]:
        assert new["levels"][k]["n"] == 0 and not m.level(k)["fitted"] and m.rate(k) == pytest.approx(_rate(k))
    assert rep["n"] == 40 and new["n"] == 40
    # more data → closer to the athlete
    big, _ = RL.refit(_rows(2, 160, 70.0), TODAY)
    assert abs(big["levels"]["easy"]["tss_h"] - 70) < abs(e["tss_h"] - 70)
    # each level its own: very hard rated runs pull only very hard
    both, _ = RL.refit(_rows(2, 30, 70.0) + _rows(7, 30, 110.0), TODAY)
    assert both["levels"]["easy"]["tss_h"] > _rate("easy") and both["levels"]["very_hard"]["tss_h"] > _rate("very_hard")
    assert both["levels"]["hard"]["tss_h"] == pytest.approx(_rate("hard"))
    # the leave-one-out error is reported
    assert new["loo"]["n"] == 40 and new["loo"]["mape"] is not None
    RL.validate(new)


def test_few_samples_stay_near_the_default():
    # below MIN_N a level keeps its default exactly (the count is still shown)
    few, _ = RL.refit(_rows(5, RL.MIN_N - 1, 130.0), TODAY)
    assert few["levels"]["hard"]["n"] == RL.MIN_N - 1 and few["levels"]["hard"]["tss_h"] == pytest.approx(_rate("hard"))
    assert not RL.Model.of(few).level("hard")["fitted"]
    # at MIN_N it moves, but only by w = n / (n + K) in log space — still near the default
    some, _ = RL.refit(_rows(5, RL.MIN_N, 130.0), TODAY)
    v = some["levels"]["hard"]["tss_h"]
    assert _rate("hard") < v < _rate("hard") + 0.35 * (130.0 - _rate("hard"))
    assert RL.Model.of(some).level("hard")["fitted"]


def test_monotonic_across_levels():
    # an athlete whose easy runs are hard work (100 TSS/h, many of them): easy can't pass the levels above
    new, _ = RL.refit(_rows(2, 40, 100.0), TODAY)
    v = [new["levels"][k]["tss_h"] for k in IDS]
    assert all(a <= b + 1e-9 for a, b in zip(v, v[1:])), v
    assert new["levels"]["easy"]["adjusted"] and new["levels"]["moderate"]["adjusted"]
    assert not new["levels"]["max"]["adjusted"]
    m = RL.Model.of(new)
    assert all(m.rate(a) <= m.rate(b) + 1e-9 for a, b in zip(IDS, IDS[1:]))
    # a stored value that breaks the order (hand-edited, older code) is put in order when read
    bad = {"levels": {"easy": {"tss_h": 95.0, "n": 50, "w": 0.83}, "very_hard": {"tss_h": 60.0, "n": 50, "w": 0.83}}, "n": 100}
    RL.validate(bad)
    mb = RL.Model.of(bad)
    assert all(mb.rate(a) <= mb.rate(b) + 1e-9 for a, b in zip(IDS, IDS[1:]))
    # monotone() itself: already in order → unchanged
    rates = dict(RL.DEFAULT_TSS_H)
    out, adj = RL.monotone(rates, {k: 1.0 for k in IDS})
    assert out == pytest.approx(rates) and not any(adj.values())


def test_stored_fit_reads_back_the_same_rates_under_pooling():
    # pooled levels with and without data of their own: reading the stored value gives every level's stored rate
    for rows in (_rows(2, 40, 100.0), _rows(2, 40, 100.0) + _rows(4, 5, 60.0), _rows(7, 30, 70.0) + _rows(10, 8, 80.0)):
        new, _ = RL.refit(rows, TODAY)
        m = RL.Model.of(new)
        for k in IDS:
            assert m.rate(k) == pytest.approx(new["levels"][k]["tss_h"], abs=0.01), (k, new["levels"])
            assert m.level(k)["adjusted"] == new["levels"][k]["adjusted"]


def test_unfitted_levels_read_the_default_not_a_stored_copy():
    stored = {"levels": {"hard": {"tss_h": 70.0, "n": 0, "w": 0.0}, "easy": {"tss_h": 54.0, "n": 1, "w": 0.0}}, "n": 1}
    m = RL.Model.of(stored)
    assert m.rate("hard") == RL.DEFAULT_TSS_H["hard"] and m.rate("easy") == RL.DEFAULT_TSS_H["easy"]


def test_old_ratings_count_less_than_recent_ones():
    recent, _ = RL.refit(_rows(5, 50, 130.0), TODAY)
    old, _ = RL.refit(_rows(5, 50, 130.0, start=TODAY - dt.timedelta(days=360)), TODAY)
    r, o = recent["levels"]["hard"], old["levels"]["hard"]
    assert r["n"] == o["n"] == 50 and o["n_eff"] < r["n_eff"] and o["w"] < r["w"]
    # the same personal value, pulled less far from the default when the ratings are old
    assert o["personal"] == pytest.approx(r["personal"]) and _rate("hard") < o["tss_h"] < r["tss_h"]


def test_refit_without_rpe_keeps_the_default_and_threshold_cut():
    assert RL.refit([{"date": "2026-10-01", "rpe": None, "hours": 1, "tss": 50}], TODAY)[0] is None
    # an FTP change > 5 %: only the activities after it
    old = _rows(7, 20, 60.0, start=TODAY - dt.timedelta(days=60), thr=250.0)
    new = _rows(7, 5, 110.0, start=TODAY, thr=300.0)
    fit, _ = RL.refit(old + new, TODAY)
    assert fit["levels"]["very_hard"]["n"] == 5 and fit["levels"]["very_hard"]["personal"] == pytest.approx(110.0)


def test_old_single_factor_value_reads_as_the_defaults():
    m = RL.Model.of({"factor": 0.4, "n": 10, "w": 0.5, "loo": {"mape": 0.1}})
    assert not m.fitted and m.rate("easy") == pytest.approx(_rate("easy"))


def test_validate_and_describe():
    RL.validate(None)
    for bad in ({"n": 3}, {"levels": {"easy": {"tss_h": -1}}}, {"levels": {"nope": {"tss_h": 50}}}, {"levels": []}):
        with pytest.raises(ValueError):
            RL.validate(bad)
    d = RL.describe(None)
    assert not d["fitted"] and d["err_pct"] == round(RL.DEFAULT_ERR * 100)
    assert [x["id"] for x in d["levels"]] == IDS
    lv = d["levels"][3]
    assert lv["tss_h"] == round(_rate("very_hard")) and lv["if"] == RL.DEFAULT_IF["very_hard"]
    assert lv["source"] == "default" and lv["n"] == 0 and lv["chip"]["text"] == "預設（推估）" and lv["chip"]["tip"]
    new, _ = RL.refit(_rows(2, 12, 70.0), TODAY)
    d = RL.describe(new)
    e = d["levels"][0]
    assert d["fitted"] and d["n"] == 12 and e["source"] == "fitted" and e["n"] == 12
    assert e["chip"]["text"] == "本人 n=12" and e["personal"] == pytest.approx(70.0)
    assert d["loo"]["mape"] == new["loo"]["mape"]
    # the defaults read as a general rule, personalisation only from the athlete's own rated activities
    for x in RL.describe(None)["levels"]:
        assert "你" not in x["chip"]["tip"] and "通用" in x["chip"]["tip"]
    assert all("你" not in RL.IF_SRC[k] for k in IDS)


def test_a_level_moved_only_by_the_ordering_says_so():
    new, _ = RL.refit(_rows(2, 40, 100.0), TODAY)           # easy pooled with 稍累 / 累, which have no data
    d = {x["id"]: x for x in RL.describe(new)["levels"]}
    assert d["easy"]["source"] == "fitted" and d["easy"]["chip"]["text"].startswith("本人")
    for k in ("moderate", "hard"):
        assert d[k]["source"] == "adjusted" and d[k]["chip"]["text"] == "依相鄰檔調整"
        assert d[k]["tss_h"] != round(_rate(k)) and str(d[k]["tss_h"]) in d[k]["chip"]["tip"]
    assert d["very_hard"]["source"] == "default" and d["very_hard"]["chip"]["text"] == "預設（推估）"
    lv = {x["id"]: x for x in RL.levels(RL.Model.of(new))}
    assert lv["moderate"]["adjusted"] and not lv["moderate"]["fitted"] and not lv["very_hard"]["adjusted"]


def test_activity_rows_join_the_watch_rpe():
    from types import SimpleNamespace as NS
    from backend.engine.wko5expr.dataset import day_to_date
    day0 = day_to_date(0)
    start = dt.datetime(2026, 10, 1, 7, 0)
    w = NS(entry=NS(start=start, file="a.fit"), day=(dt.date(2026, 10, 1) - day0).days,
           metrics={"tss": 80.0, "movingduration": 3600, "tss_source": "power", "ftp_used": 300.0})
    w2 = NS(entry=NS(start=start + dt.timedelta(days=1), file="b.fit"), day=w.day + 1,
            metrics={"tss": 50.0, "movingduration": 3600})
    ds = NS(workouts=[w, w2], sport_setting=lambda k, x: None)
    from backend.engine import activity_tags as AT
    # a COROS post-run rating (SP-231: feel 4 → RPE 7, rpe_source coros) feeds the fit like a FIT RPE
    rec = [{"start_local": AT.key_of(start), "file": "a.fit", "rpe": 7.0, "feel": None, "coros_feel": 4, "source": "coros"}]
    rows = RL.activity_rows(ds, rec)
    assert rows == [{"date": "2026-10-01", "rpe": 7.0, "tss": 80.0, "hours": 1.0, "thr": 300.0}]
    assert RL.samples(rows)[0]["level"] == "very_hard"


# ---------------------------------------------------------------------------
# the step (engine/workout_steps.py)
# ---------------------------------------------------------------------------

def test_normalize_sets_the_tss_from_the_level_rate_and_minutes():
    d = WS.normalize(_doc(_rpe_step("very_hard", 40)))
    assert d["items"][0]["dur"] == {"type": "load", "value": round(_rate("very_hard") * 40 / 60, 1),
                                    "rpe": "very_hard", "min": 40}
    fitted = RL.Model.of({"levels": {"hard": {"tss_h": 90.0, "n": 20, "w": 0.67}}, "n": 20})
    d = WS.normalize(_doc(_rpe_step("hard", 30)), rpe_model=fitted)
    assert d["items"][0]["dur"]["value"] == 45.0
    # a typed TSS step is unchanged
    assert WS.normalize(_doc({**_rpe_step(), "dur": {"type": "load", "value": 75}}))["items"][0]["dur"] == \
        {"type": "load", "value": 75}
    for bad, msg in ((_rpe_step("so-so"), "RPE 要是"), (_rpe_step("hard", 0), "分鐘"),
                     (_rpe_step("max", 360), "超過 500 TSS")):
        with pytest.raises(WS.StepsError) as e:
            WS.normalize(_doc(bad))
        assert any(msg in x for x in e.value.errors), e.value.errors
    with pytest.raises(WS.StepsError) as e:
        WS.normalize(_doc({**_rpe_step(), "kind": "warm"}))
    assert "「負荷」只能用在主課" in e.value.errors


def test_rpe_step_is_timed_by_its_minutes_and_viewed():
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, rpe=RL.Model())
    d = WS.normalize(_doc(_rpe_step("very_hard", 40)))
    tss = round(_rate("very_hard") * 40 / 60, 1)
    st = d["items"][0]
    f = math.sqrt(tss / (100 * 40 / 60))
    # a default level's step implies that level's IF
    assert WS.load_if(st, WS.resolve(st, c)) == pytest.approx(f, abs=1e-4)
    assert f == pytest.approx(RL.DEFAULT_IF["very_hard"], abs=2e-3)
    t = WS.totals(d, c)
    assert t["sec"] == 2400 and t["tss"] == pytest.approx(tss, abs=0.1) and "RPE" in t["est_note"]
    v = WS.view(d, c)
    o = v["order"][0]
    want = TL.Model.of().tl(tss, "hr", round(f, 4))
    assert o["load"]["tss"] == tss and o["load"]["tl"] == round(want["tl"]) and o["sec"] == 2400
    assert o["load"]["rpe"] == {"level": "very_hard", "min": 40, "tss_h": round(_rate("very_hard")),
                                "fitted": False, "adjusted": False, "n": 0, "err_pct": round(RL.DEFAULT_ERR * 100)}
    assert WS.fmt_dur(st["dur"]) == f"負荷 {tss:g} TSS（很累 40 分）"
    assert v["watch"]["lines"][0]["dur"] == f"負荷 {round(want['tl'])} TL"


def test_push_still_sends_coros_tl():
    th = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
    doc = _doc({"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "none"}},
               _rpe_step("hard", 30, target={"type": "hr", "mode": "abs", "lo": 140, "hi": 146}))
    s = {"id": "s1", "key": "u1", "kind": "quality", "title": "RPE 負荷", "minutes": 40, "day": "2026-10-06",
         "steps": doc}
    spec = CW.session_workout(s, th)
    ex = spec.payload["exercises"][1]
    tss = round(_rate("hard") * 30 / 60, 1)
    f = round(math.sqrt(tss / 50.0), 4)
    # the TSS → COROS TL conversion is the typed load step's, unchanged
    want = round(TL.Model.of().tl(tss, "hr", f)["tl"])
    assert ex["targetType"] == 6 and ex["targetValue"] == want
    assert spec.load_steps == [{"i": 1, "n": 2, "tss": tss, "tl": want, "basis": "hr", "if": f, "f": 1.0}]


def test_a_fitted_rate_above_169_clamps_the_if_used_for_tl():
    # 極限 fitted at 200 TSS/h: IF √2 = 1.41 > the step clamp 1.3 (169 TSS/h) — the TL is converted at 1.3
    fast = RL.Model.of({"levels": {"max": {"tss_h": 200.0, "n": 30, "n_eff": 30, "w": 0.75}}, "n": 30})
    assert fast.rate("max") == 200.0
    d = WS.normalize(_doc(_rpe_step("max", 30)), rpe_model=fast)
    st = d["items"][0]
    assert st["dur"]["value"] == 100.0
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, rpe=fast)
    assert WS.load_if(st, WS.resolve(st, c)) == WS.RPE_IF_RANGE[1]
    o = WS.view(d, c)["order"][0]
    assert o["load"]["tl"] == round(TL.Model.of().tl(100.0, "hr", WS.RPE_IF_RANGE[1])["tl"])


# ---------------------------------------------------------------------------
# stored sessions follow the rates in effect (no stale TSS vs the watch)
# ---------------------------------------------------------------------------

def _run(c):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(c)


async def _db():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.db.models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


def _session(uid, day, steps, tss, state="active"):
    return {"uid": uid, "week_start": "2026-10-05", "day": day, "kind": "quality", "title": "RPE 負荷", "minutes": 50,
            "target": "", "detail": "", "source": "", "tss": tss, "origin": "custom", "edited": True, "state": state,
            "steps": steps}


def test_saved_rpe_sessions_follow_the_rates_and_match_the_push(monkeypatch):
    from backend.engine import plan_store as PS
    from backend.settings.repository import SettingsRepository
    th = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
    old_factor = RL.Model.of({"levels": {k: {"tss_h": 0.3 * RL.CR10[k] * 60, "n": 99, "n_eff": 99, "w": 1.0}
                                         for k in IDS}, "n": 99})       # = the old 0.30 × CR-10 × minutes
    raw = _doc({"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "none"}},
               _rpe_step("very_hard", 40))
    saved = WS.normalize(raw, rpe_model=old_factor)
    assert saved["items"][1]["dur"]["value"] == 84.0                        # 很累 40′ under the old factor
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, rpe=old_factor)
    tss_saved = WS.totals(saved, c)["tss"]

    async def go():
        db = await _db()
        repo = SettingsRepository(db, 1)
        # the old single-factor value as it is stored before the deploy (written past the new validation)
        monkeypatch.setattr(RL, "validate", lambda v: None)
        await repo.set(RL.KEY, {"factor": 0.3, "n": 4, "w": 0.29, "loo": {"mape": 0.2}})
        await PS.save(db, [_session("up", "2026-10-10", saved, tss_saved),
                           _session("past", "2026-10-01", saved, tss_saved),
                           _session("done", "2026-10-12", saved, tss_saved, state="done")])
        r1 = await RL.sync_sessions(db, 1, "2026-10-08")
        r2 = await RL.sync_sessions(db, 1, "2026-10-08")                     # the same rates: nothing to do
        return {s["uid"]: s for s in await PS.load(db, 1)}, r1, r2, await repo.get(RL.STAMP_KEY)
    ss, r1, r2, stamp = _run(go())
    assert r1["changed"] == 1 and r2["changed"] == 0 and stamp == RL.stamp(RL.Model())
    up = ss["up"]
    pushed = CW.session_workout({**up, "key": "u", "id": "u"}, th).load_steps[0]["tss"]
    assert up["steps"]["items"][1]["dur"]["value"] == pushed == round(_rate("very_hard") * 40 / 60, 1)
    assert up["tss"] == pytest.approx(WS.totals(WS.normalize(up["steps"]), WS.Ctx(cp=300.0, lthr=170.0, aet=150.0))["tss"], abs=0.11)
    assert up["tss"] == pytest.approx(tss_saved - 84.0 + pushed, abs=0.11)
    assert up["steps"]["items"][1]["dur"]["min"] == 40 and up["origin"] == "custom"   # the structure is the user's
    for k in ("past", "done"):                                               # only upcoming, active sessions
        assert ss[k]["tss"] == tss_saved and ss[k]["steps"]["items"][1]["dur"]["value"] == 84.0


def test_a_push_first_brings_stored_sessions_to_the_rates():
    from types import SimpleNamespace as NS
    from backend.engine import plan_store as PS
    from backend.settings.repository import SettingsRepository
    saved = WS.normalize(_doc(_rpe_step("very_hard", 40)), rpe_model=RL.Model.of(
        {"levels": {"very_hard": {"tss_h": 126.0, "n": 99, "n_eff": 99, "w": 1.0},
                    "max": {"tss_h": 180.0, "n": 99, "n_eff": 99, "w": 1.0}}, "n": 99}))

    async def go():
        db = await _db()
        await PS.save(db, [_session("up", "2026-10-10", saved, 84.0)])
        await CW.push_sessions(db, [], {"cp": 300.0}, "2026-10-08", hub=NS())
        return (await PS.load(db, 1))[0], await SettingsRepository(db, 1).get(RL.STAMP_KEY)
    s, stamp = _run(go())
    assert stamp == RL.stamp(RL.Model()) and s["steps"]["items"][0]["dur"]["value"] == round(_rate("very_hard") * 40 / 60, 1)
    assert s["tss"] == pytest.approx(84.0 - 84.0 + round(_rate("very_hard") * 40 / 60, 1), abs=0.11)


def test_a_refit_with_no_rated_activity_left_clears_the_old_fit(monkeypatch):
    from types import SimpleNamespace as NS
    from backend.settings.repository import SettingsRepository

    async def go():
        db = await _db()
        repo = SettingsRepository(db, 1)
        old, _ = RL.refit(_rows(2, 20, 80.0), TODAY)
        await repo.set(RL.KEY, old)
        monkeypatch.setattr(RL, "activity_rows", lambda ds: [])
        rep = await RL.refit_and_store(db, 1, NS(workouts=[]), TODAY)
        return rep, await repo.get(RL.KEY)
    rep, after = _run(go())
    assert after is None and rep["n"] == 0
    assert RL.Model.of(after).rate("easy") == RL.DEFAULT_TSS_H["easy"]


# ---------------------------------------------------------------------------
# the editor (static/workout_editor.js)
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


def test_editor_context_lists_the_level_rates(monkeypatch):
    from backend.api import plan_sessions as PSAPI
    fitted = RL.Model.of({"levels": {"easy": {"tss_h": 62.0, "n": 15, "w": 0.6}}, "n": 15})
    monkeypatch.setattr(RL, "current", lambda user_id=1: fitted)
    ctx = PSAPI._rpe_load_ctx()
    assert [x["id"] for x in ctx["levels"]] == IDS and ctx["min_range"] == list(RL.MIN_RANGE)
    assert ctx["levels"][0] == {"id": "easy", "cr10": 2, "tss_h": 62, "fitted": True, "adjusted": False, "n": 15}
    assert ctx["levels"][3] == {"id": "very_hard", "cr10": 7, "tss_h": round(_rate("very_hard")), "fitted": False,
                                "adjusted": False, "n": 0}
    assert ctx["fitted"] and ctx["n"] == 15


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_editor_payload_by_rpe():
    js = r"""
const vm = require("vm"), fs = require("fs"), w = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window: w, document: {} });
const E = w.WorkoutEditor, D = { type: "load", value: 75 };
const toRpe = E.loadDur(D, "lmode", "rpe", 42.4);
const lvl = E.loadDur(toRpe, "lrpe", "max"), mins = E.loadDur(lvl, "lmin", "25");
const levels = [["easy", 2, 55], ["moderate", 4, 67], ["hard", 5, 77], ["very_hard", 7, 90], ["max", 10, 121]]
  .map(([id, cr10, tss_h]) => ({ id, cr10, tss_h, fitted: id === "easy", adjusted: id === "moderate", n: id === "easy" ? 12 : 0 }));
const ctx = { provider: { label: "COROS", capabilities: { load_unit: "TL" } }, rpe_load: { levels } };
const r = { load: { tss: 60, tl: 78, err: 20, sec: 2400, rpe: { level: "very_hard", min: 40, tss_h: 90, fitted: false, n: 0 } } };
const rMine = { load: { tss: 37, tl: 48, err: 20, sec: 2400, rpe: { level: "easy", min: 40, tss_h: 55, fitted: true, n: 12 } } };
const rAdj = { load: { tss: 45, tl: 55, err: 20, sec: 2400, rpe: { level: "moderate", min: 40, tss_h: 67, fitted: false, adjusted: true, n: 0 } } };
console.log(JSON.stringify({
  toRpe, lvl, mins,
  badLvl: E.loadDur(toRpe, "lrpe", "meh"), badMin: E.loadDur(toRpe, "lmin", "0"), bigMin: E.loadDur(toRpe, "lmin", "400"),
  back: E.loadDur({ ...mins, value: 50 }, "lmode", "tss"),
  html: E.loadInput(ctx, { kind: "work", dur: { type: "load", value: 60, rpe: "very_hard", min: 40 } }, r),
  mine: E.loadInput(ctx, { kind: "work", dur: { type: "load", value: 37, rpe: "easy", min: 40 } }, rMine),
  adj: E.loadInput(ctx, { kind: "work", dur: { type: "load", value: 45, rpe: "moderate", min: 40 } }, rAdj),
  noCtx: E.loadInput({ provider: ctx.provider }, { kind: "work", dur: { type: "load", value: 60, rpe: "very_hard", min: 40 } }, r),
  plain: E.loadInput(ctx, { kind: "work", dur: { type: "load", value: 75 } }, null),
}));
"""
    r = subprocess.run([NODE, "-e", js, str(ROOT / "static" / "workout_editor.js")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    g = json.loads(r.stdout)
    assert g["toRpe"] == {"type": "load", "value": 75, "rpe": "hard", "min": 42}
    assert g["lvl"]["rpe"] == "max" and g["mins"] == {"type": "load", "value": 75, "rpe": "max", "min": 25}
    assert g["badLvl"] is None and g["badMin"] is None and g["bigMin"] is None
    assert g["back"] == {"type": "load", "value": 50}                     # back to TSS: the computed value
    h = g["html"]
    assert 'data-f="lmode"' in h and 'value="rpe" selected' in h and 'data-f="lrpe"' in h
    assert 'value="very_hard" selected' in h and 'data-f="lmin"' in h and 'value="40"' in h
    assert all(f'value="{k}"' in h for k in IDS)
    # each level's TSS per hour on its option (picking a level shows what it is worth)
    assert all(f"≈ {v} TSS/h" in h for v in (55, 67, 77, 90, 121))
    # the step's TSS, marked 推估 (default level) or 本人 (fitted level), then the TL
    assert "≈ 60 TSS（workout.load_rpe_src_default）≈ 78 TL" in h and 'data-f="tss"' not in h
    assert "≈ 37 TSS（workout.load_rpe_src_mine）≈ 48 TL" in g["mine"]
    # a level moved only by the ordering: its own mark; every option says where its rate comes from
    assert "≈ 45 TSS（workout.load_rpe_src_adjusted）≈ 55 TL" in g["adj"]
    assert 'value="easy" title="workout.load_rpe_src_mine"' in h and 'value="moderate" title="workout.load_rpe_src_adjusted"' in h
    assert 'value="hard" title="workout.load_rpe_src_default"' in h
    assert "TSS/h" not in g["noCtx"] and "≈ 60 TSS" in g["noCtx"]         # an old context: no rates, still works
    assert 'data-f="tss"' in g["plain"] and 'value="tss" selected' in g["plain"]
