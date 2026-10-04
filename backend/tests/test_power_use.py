"""「使用功率」 setting (charts.power.enabled) and which charts it hides
(wko5expr/power_use.py), plus the banded-chart option ("zoned"). Synthetic
charts and the repo's own view files only — no athlete folder."""
import pytest

from backend.engine.wko5expr import power_use as PU
from backend.engine.wko5expr.customviews import REPO_VIEWS, CustomViewError, load_custom_views, parse_view
from backend.settings.repository import DEFAULTS, validate
from backend.tests.test_sync_e2e import make_session, run


def _ch(*exprs, **kw):
    return {"kind": "athlete", "title": "t", "series": [{"name": f"s{i}", "expression": e} for i, e in enumerate(exprs)], **kw}


@pytest.mark.parametrize("exprs, power", [
    (("meanmax(runpower)", "(,cp)"), True),                       # power curve with a CP line
    (("sum(sum(if(runpower >= 0.8*cp, deltatime)), startofweek(date))",), True),
    (("if(sport=\"run\", drift(\"power\"))", "(,0.05)"), True),
    (("pwhr",), True),
    (("tisaerobic", "tisanaerobic"), True),                       # TIS built-ins read power
    (("tl((tisaerobic), ctlconstant)", "tl((tisanaerobic), atlconstant)"), True),
    (("heartrate", "(,cp)"), False),                              # HR data, power only as a reference
    (("ctl", "atl", "(,0)"), False),
    (("if(sport=\"run\", drift(\"pace\"))",), False),
    (("heartrate", "power"), False),                              # mixed: kept
    (("(,0)", "{0:0.8}", "goalclimbperkm"), False),              # reference lines only: nothing to hide
])
def test_series_classification(exprs, power):
    assert PU.chart_needs_power(_ch(*exprs)) is power


def test_basis_chart_stays_and_locks_to_pace():
    ch = _ch("drift(\"pace\")", "drift(\"power\")", basis={"default": "pace", "choices": ["pace", "power"]})
    assert not PU.chart_needs_power(ch) and PU.power_basis(ch)
    assert not PU.power_basis(_ch("heartrate"))


@pytest.mark.parametrize("ch, power", [
    ({"kind": "review", "section": "cp_test"}, True),
    ({"kind": "review", "section": "interval_verdict"}, True),
    ({"kind": "review", "section": "wprime_battery"}, True),
    ({"kind": "review", "section": "aerobic"}, False),
    ({"kind": "review", "section": "summary"}, False),
    ({"kind": "activity", "chart": "powerzones"}, True),
    ({"kind": "activity", "chart": "hrpower"}, False),
    ({"kind": "zones", "system": "palladino"}, True),
    ({"kind": "zones", "system": "frielhr"}, False),
    ({"kind": "periodzones", "view": "total"}, False),
    ({"kind": "targets"}, False),
])
def test_panel_kinds(ch, power):
    assert PU.chart_needs_power(ch) is power


def test_repo_views_hide_only_power_charts():
    views = load_custom_views([REPO_VIEWS])
    hidden = {(n, c["title"]) for n, v in views.items() for d in v["dashboards"] for c in d["charts"]
              if PU.chart_needs_power(c)}
    assert ("我的訓練", "跑步功率曲線") in hidden
    assert ("周期化訓練", "Palladino 功率區間（跑步）") in hidden
    assert ("單次活動判讀", "功率區間時間") in hidden
    assert ("單次活動判讀", "CP 測試結果（3 分／12 分）") in hidden
    assert ("我的訓練", "有氧／無氧刺激 TIS（每次活動）") in hidden
    assert ("我的訓練", "有氧／無氧刺激的長期與短期負荷（TIS）") in hidden
    for keep in (("我的訓練", "狀況 Form%（TSB ÷ CTL）"), ("我的訓練", "這段時間在各區間的時數"),
                 ("我的訓練", "有氧效率 EF（輕鬆路跑）"), ("單次活動判讀", "心率區間時間"),
                 ("單次活動判讀", "飄移判讀"), ("單次活動判讀", "心率與功率（拖曳選一段看統計）")):
        assert keep not in hidden, keep
    # every 單次活動判讀 page other than 間歇 keeps something to show
    wv = views["單次活動判讀"]
    for d in wv["dashboards"]:
        left = [c for c in d["charts"] if not PU.chart_needs_power(c)]
        assert bool(left) is (d["title"] != "間歇"), d["title"]


def test_setting_defaults_to_auto_and_is_a_bool(tmp_path):
    assert DEFAULTS[PU.SETTING_KEY] is None          # auto: from the power source (engine/athlete_profile.py)
    validate(PU.SETTING_KEY, False)
    validate(PU.SETTING_KEY, None)
    with pytest.raises(ValueError):
        validate(PU.SETTING_KEY, "off")


def test_settings_api_round_trip(tmp_path):
    from backend.api.sync import SyncSettingsBody, get_sync_settings, put_sync_settings

    async def go():
        s = await make_session(tmp_path)
        g = await get_sync_settings(1, s)
        # auto: no data and no power source entered -> no power meter -> HR only
        assert g["use_power_stored"] is None and g["use_power"] is (g["power_source"] == "stryd")
        r = await put_sync_settings(SyncSettingsBody(use_power=True), 1, s)
        assert r["use_power"] is True
        r = await put_sync_settings(SyncSettingsBody(use_power=False), 1, s)
        assert r["use_power"] is False
        assert (await get_sync_settings(1, s))["use_power"] is False
    run(go())


def test_zoned_needs_one_of_its_series():
    base = {"title": "負荷比", "series": [{"name": "正常 0.8–1.3", "expression": "{0.8:1.3}"},
                                          {"name": "ATL ÷ CTL", "expression": "atl / ctl"}]}
    v = parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [{**base, "zoned": {"line": "ATL ÷ CTL"}}]}]})
    assert v["dashboards"][0]["charts"][0]["zoned"] == {"line": "ATL ÷ CTL"}
    # an optional reference line (負荷比's 1 = as usual)
    r = parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [{**base, "zoned": {"line": "ATL ÷ CTL", "ref": {"y": 1, "label": "1 = 跟平常一樣"}}}]}]})
    assert r["dashboards"][0]["charts"][0]["zoned"]["ref"] == {"y": 1.0, "label": "1 = 跟平常一樣"}
    with pytest.raises(CustomViewError):
        parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [{**base, "zoned": {"line": "ATL ÷ CTL", "ref": {"y": "a"}}}]}]})
    with pytest.raises(CustomViewError):
        parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [{**base, "zoned": {"line": "nope"}}]}]})


def test_repo_zoned_charts_have_bands():
    views = load_custom_views([REPO_VIEWS])
    zoned = [c for v in views.values() for d in v["dashboards"] for c in d["charts"] if c.get("zoned")]
    assert {c["title"] for c in zoned} >= {"狀況 Form%（TSB ÷ CTL）", "負荷比 ATL ÷ CTL（7:42）"}
    for c in zoned:
        bands = [s for s in c["series"] if s["expression"].startswith("{")]
        assert len(bands) >= 4, c["title"]
        # band names start with a short word the latest-value label uses (「1.12 正常」)
        assert all(" " in s["name"] for s in bands)


def test_monotony_chart_is_gone():
    views = load_custom_views([REPO_VIEWS])
    titles = [c["title"] for v in views.values() for d in v["dashboards"] for c in d["charts"]]
    assert not any("單調度" in t or "Monotony" in t for t in titles)
