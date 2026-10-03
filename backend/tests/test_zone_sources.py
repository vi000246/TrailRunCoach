"""Zone anchors and where each threshold comes from
(docs/research/zones-and-thresholds.md §3.1, §3.4 change 1): an applied LTHR
estimate is labelled as one (it used to read 「你的測試」), the easy cap is a
measured AeT or 0.89 × LTHR labelled 推估, HR zones Friel % LTHR, power zones
Palladino % CP, and a CP-derived HR never becomes the LTHR. Synthetic FITs
and a temp plan only — never the WKO5 folder or the real DB."""
import datetime as dt
from datetime import datetime, timezone

import pytest

from backend.engine import planning as P
from backend.engine.planning import Plan, Threshold
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 10, 1)
UTC = timezone.utc
# a legacy plan.json row: an applied estimate, no lthr_method
LEGACY_NOTE = "LTHR 自動估算（7 次跑步的最佳 30 分鐘後 20 分鐘平均心率）；CP 204 W：CP 測試的 12 分段"


def _ds(tmp_path, plan):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    (d / "000.fit").write_bytes(build_run(start=datetime(2026, 9, 28, 0, tzinfo=UTC), seconds=1800,
                                          power=180, stryd=True))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=TODAY,
                          estimate_thresholds=False, tz=UTC)
    ds.plan = plan
    return ds


def test_legacy_rows_read_their_note():
    t = Threshold("2026-09-30", lthr=155, cp=204, note=LEGACY_NOTE)
    assert P.threshold_method(t, "lthr") == "estimate"
    assert P.threshold_method(t, "aethr") is None                     # no AeT on the row
    a = Threshold("2026-09-20", aethr=146, note="AeT 飄移測試 2026-09-20：Pw:HR 4.1%")
    assert P.threshold_method(a, "aethr") == "test"
    assert P.threshold_method(Threshold("2026-01-01", lthr=162, note="比賽心率"), "lthr") == "manual"
    assert P.threshold_method(Threshold("2026-01-01", lthr=162, lthr_method="friel30"), "lthr") == "friel30"


def test_threshold_row_labels_an_applied_estimate():
    plan = Plan(thresholds=[Threshold("2026-09-30", lthr=155, cp=204, note=LEGACY_NOTE)])
    r = P.threshold_row(plan, "lthr", TODAY)
    assert r["value"] == 155 and r["method"] == "estimate" and not r["measured"]
    assert r["label"] == "自動估算（已套用 2026-09-30）"
    assert P.threshold_row(plan, "lthr", dt.date(2026, 9, 29)) is None    # never backwards
    plan.thresholds.append(Threshold("2026-11-20", lthr=161, lthr_method="friel30"))
    r = P.threshold_row(plan, "lthr", dt.date(2026, 11, 21))
    assert r["measured"] and r["label"] == "30 分鐘測試 2026-11-20"


def test_methods_survive_save_and_load(tmp_path):
    p = tmp_path / "plan.json"
    Plan(thresholds=[Threshold("2026-09-30", lthr=155, lthr_method="estimate", aethr=140, aethr_method="test")]).save(p)
    t = Plan.load(p).thresholds[0]
    assert (t.lthr_method, t.aethr_method) == ("estimate", "test")


def test_zone_sources_say_estimate_not_your_test(tmp_path):
    from backend.engine.zones import threshold_info, training_targets, zone_table
    plan = Plan(thresholds=[Threshold("2026-09-30", lthr=155, cp=204, note=LEGACY_NOTE)])
    ds = _ds(tmp_path, plan)
    end = int(date_to_day(TODAY))
    z = zone_table(ds, "frielhr", end)
    assert z["threshold"] == 155 and z["threshold_source"] == "自動估算（已套用 2026-09-30）"
    assert "你的測試" not in z["threshold_source"]
    tt = training_targets(ds, end)
    assert tt["lthr_source"] == "自動估算（已套用 2026-09-30）" and tt["lthr_measured"] is False
    # no measured AeT: the easy cap is 0.89 × LTHR, labelled 推估
    assert tt["aet"] == pytest.approx(0.89 * 155) and "推估" in tt["aet_source"] and tt["aet_measured"] is False
    assert (tt["hr_zones"], tt["power_zones"]) == ("Friel % LTHR", "Palladino % CP")
    from backend.engine.zones import _on_day
    i = threshold_info(ds, "lthr", _on_day(ds.workouts[0], end), end)
    assert i["method"] == "estimate" and i["measured"] is False


def test_a_measured_aet_is_the_easy_cap(tmp_path):
    from backend.engine.zones import training_targets
    plan = Plan(thresholds=[Threshold("2026-09-30", lthr=155, cp=204, aethr=142, aethr_method="test", note=LEGACY_NOTE)])
    ds = _ds(tmp_path, plan)
    tt = training_targets(ds, int(date_to_day(TODAY)))
    assert tt["aet"] == 142 and tt["aet_measured"] is True and tt["aet_source"] == "AeT 測試 2026-09-30"
    z2 = next(r for r in tt["rows"] if r["id"] == "z2")
    assert z2["hr"][1] == 142


def test_a_new_test_re_zones_from_its_day(tmp_path, monkeypatch):
    # event 1 (§2.5): a CP / AeT test applied -> the zones change at once; the
    # API's _notify(True) rebuilds the datasets (wko5views.plan_changed)
    from backend.api import wko5views as WV
    from backend.engine.zones import training_targets, zone_table
    plan = Plan(thresholds=[Threshold("2026-09-30", lthr=155, cp=204, note=LEGACY_NOTE)])
    ds = _ds(tmp_path, plan)
    end = int(date_to_day(TODAY))
    assert zone_table(ds, "palladino", end)["threshold"] == 204
    plan.thresholds.append(Threshold(TODAY.isoformat(), cp=210, cp_method="tt20", aethr=144, aethr_method="test"))
    assert zone_table(ds, "palladino", end)["threshold"] == 210
    tt = training_targets(ds, end)
    assert tt["cp"] == 210 and tt["aet"] == 144 and tt["aet_measured"]
    cleared = []
    monkeypatch.setattr(WV._dataset_cfg, "cache_clear", lambda: cleared.append(1))
    WV.plan_changed(True)
    assert cleared == [1]


def test_apply_estimate_writes_the_method(tmp_path, monkeypatch):
    from backend.api import plan as API
    path = tmp_path / "plan.json"
    load, save = Plan.load.__func__, Plan.save
    monkeypatch.setattr(Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    monkeypatch.setattr(Plan, "save", lambda self, p=path: save(self, p))
    monkeypatch.setattr(API, "_notify", lambda thresholds: None)
    API.apply_estimate(API.ApplyEstimate(lthr=155.4))
    API.apply_estimate(API.ApplyEstimate(aethr=146, date="2026-09-20", note="AeT 飄移測試 2026-09-20"))
    rows = {t.date: t for t in Plan.load(path).thresholds}
    today = dt.date.today().isoformat()
    assert rows[today].lthr == 155 and rows[today].lthr_method == "estimate"
    assert rows["2026-09-20"].aethr_method == "test"
    with pytest.raises(API.HTTPException):
        API.apply_estimate(API.ApplyEstimate(lthr=160, lthr_method="magic"))


def test_hr_at_cp_never_becomes_the_lthr():
    # a CP-derived HR (runs ≥ 10 min at CP) is a cross-check only: without
    # qualifying 30-min efforts the estimate has no value even with hr_at_cp
    from backend.engine.algorithms.threshold_estimate import RunThreshold, estimate_lthr
    from backend.engine.thresholds import HR_AT_CP_NOTE
    runs = [RunThreshold(None, None, 159.0, 900.0)]
    est = estimate_lthr(runs, 204.0)
    assert est.hr_at_cp == 159 and est.value is None
    assert "不寫進 LTHR" in HR_AT_CP_NOTE and "Micheli" in HR_AT_CP_NOTE
