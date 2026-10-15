"""
SP-645: a Zone 5 rep under 2 minutes (30/15, Billat 30-30) is a yellow reminder, not a red
error — the library itself offers 30/15 (interval_library.NON_EQUIV, 「非同等，可手選」), so the
editor must let it be saved. Power, pace and heart-rate targets follow the same rule (before,
only power / 自動 bands were checked, so Billat 30-30 on pace never showed anything).
The other red errors (no CP, no LTHR) stay red.
"""
import pytest

from backend.engine import workout_steps as WS

FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0, "tpace": 270.0}
SHORT = "5 區每趟"          # the short-rep reminder (zh), whatever the rest of the wording


def _rep(target, sec=30):
    return WS.normalize({"items": [
        {"kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "auto", "intent": "easy"}},
        {"kind": "repeat", "times": 10, "items": [
            {"kind": "work", "dur": {"type": "time", "value": sec}, "target": target},
            {"kind": "rest", "dur": {"type": "time", "value": 30}, "target": {"type": "none"}}]},
        {"kind": "cool", "dur": {"type": "time", "value": 600}, "target": {"type": "auto", "intent": "easy"}}]})


def _short(iss):
    return [i for i in iss if SHORT in i["text"] and "休息" not in i["text"]]


Z5_TARGETS = {
    "power": {"type": "power", "mode": "pct", "lo": 1.10, "hi": 1.20},
    "pace": {"type": "pace", "mode": "pct", "lo": 0.86, "hi": 0.90},       # Billat 30-30: × threshold pace
    "hr": {"type": "hr", "mode": "pct", "lo": 1.04, "hi": 1.08},
    "auto": {"type": "auto", "intent": "band", "lo": 1.10, "hi": 1.20, "cls": "Z5"},
}


@pytest.mark.parametrize("kind", sorted(Z5_TARGETS))
@pytest.mark.parametrize("basis", ["power", "hr", "none"])
def test_short_z5_rep_is_a_warning_for_every_target_type(kind, basis):
    iss = WS.issues(_rep(Z5_TARGETS[kind]), WS.Ctx.of(FULL, basis))
    short = _short(iss)
    assert short, (kind, basis, iss)
    assert {i["level"] for i in short} == {"warn"}
    assert "0:30" in short[0]["text"]
    assert not [i for i in iss if i["level"] == "err"], iss


@pytest.mark.parametrize("kind", sorted(Z5_TARGETS))
def test_two_minute_z5_rep_says_nothing(kind):
    assert not _short(WS.issues(_rep(Z5_TARGETS[kind], sec=120), WS.Ctx.of(FULL, "power")))


def test_short_rep_below_zone_5_says_nothing():
    # 30 s at threshold pace / power / LTHR is not Zone 5: no reminder on any target type
    for tg in ({"type": "power", "mode": "pct", "lo": 0.95, "hi": 1.0},
               {"type": "pace", "mode": "pct", "lo": 1.0, "hi": 1.03},
               {"type": "hr", "mode": "pct", "lo": 0.95, "hi": 1.0}):
        assert not _short(WS.issues(_rep(tg), WS.Ctx.of(FULL, "power"))), tg


def test_personal_intervals_stay_exempt():
    tg = {**Z5_TARGETS["auto"], "opt": 4}
    assert not _short(WS.issues(_rep(tg), WS.Ctx.of(FULL, "power")))


def test_warning_wording_zh_and_en():
    from backend.i18n import use_locale
    d, c = _rep(Z5_TARGETS["power"]), WS.Ctx.of(FULL, "power")
    zh = _short(WS.issues(d, c))[0]["text"]
    assert "短間歇" in zh and "2 分鐘" in zh and "可以存檔" not in zh
    with use_locale("en"):
        en = [i["text"] for i in WS.issues(d, c) if "Short intervals" in i["text"]]
    assert en and "0:30" in en[0] and "2 min" in en[0], en


# ---- the template library, as 插入範本 inserts it --------------------------------------------

def _rows():
    return {r["key"]: r for g in WS.templates()["groups"] for r in g["rows"]}


SHORT_TEMPLATES = ("lib:ronnestad_3015", "x3015", "lib:billat_3030")


@pytest.mark.parametrize("basis", ["power", "hr", "none"])
@pytest.mark.parametrize("part", ["full", "items"])
def test_short_rep_templates_warn_and_can_be_saved(basis, part):
    rows = _rows()
    c = WS.Ctx.of(FULL, basis)
    for key in SHORT_TEMPLATES:
        d = WS.normalize({"items": rows[key][part]})
        iss = WS.issues(d, c)
        assert not [i for i in iss if i["level"] == "err"], (key, iss)
        short = _short(iss)
        assert short and {i["level"] for i in short} == {"warn"}, (key, iss)


def test_no_template_errs_with_full_data():
    """With CP, LTHR, AeT and threshold pace every built-in row inserts without a red error."""
    c_all = [WS.Ctx.of(FULL, b) for b in ("power", "hr", "none")]
    bad = []
    for key, r in _rows().items():
        for part in ("full", "items"):
            if not r.get(part):
                continue
            d = WS.normalize({"items": r[part]})
            for c in c_all:
                bad += [(key, part, c.basis, i["text"]) for i in WS.issues(d, c) if i["level"] == "err"]
    assert not bad, bad


def test_missing_cp_or_lthr_still_errs():
    rows = _rows()
    no_cp = WS.Ctx.of({"lthr": 168.0, "aet": 150.0, "tpace": 270.0}, "hr")
    iss = WS.issues(WS.normalize({"items": rows["lib:ronnestad_3015"]["full"]}), no_cp)
    assert any(i["level"] == "err" and "CP" in i["text"] for i in iss), iss
    no_lthr = WS.Ctx.of({"cp": 250.0, "tpace": 270.0}, "power")
    errs = []
    for r in rows.values():
        if r.get("full"):
            errs += [i["text"] for i in WS.issues(WS.normalize({"items": r["full"]}), no_lthr) if i["level"] == "err"]
    assert any("LTHR" in t for t in errs), errs
