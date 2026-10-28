"""SP-838 (838 back-b): the race calculator's model / accuracy / athlete texts from the server in English.

What athlete / backtest / capacity / cp / cptest / gpx / hikehr / maximal / predict / re / riegel / share /
trailhr put in the calculator's results and its 準確度 tab is English when the request is; zh-TW is unchanged.

- athlete.derive's texts are M_() (the inputs cache is shared by every language): each one shows the same
  zh-TW text as before and follows the language (i18n.relocalize).
- The stored back-test keeps the source text (backtest() runs in zh-TW whoever asks); load() shows it in the
  reader's language (backtest.shown), the file itself unchanged.
- cptest's bout reasons sit in the FIT scan cache: source text. A share's default title is stored as the
  source text and shown in the reader's language; an English share drops the REE warnings as a Chinese one.

Synthetic data only: the demo athlete (backend/demo/build.py, small mode) built into tmp_path."""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from backend.i18n import Msg, catalog, relocalize, translate, use_locale

CJK = re.compile(r"[㐀-鿿]")
FILES = [f"backend/engine/racepower/{m}.py" for m in
         ("athlete", "backtest", "capacity", "cp", "cptest", "gpx", "hikehr", "maximal", "predict", "re", "riegel",
          "share", "trailhr")]


def _texts(x, path=""):
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _texts(v, f"{path}.{k}")
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _texts(v, f"{path}[]")


# ---- the package's files: nothing unwrapped, English for every msgid ------------------------------

def test_the_model_files_have_no_unwrapped_chinese_and_english_for_every_msgid():
    from backend.scripts import i18n_extract as X
    scans = X.scan_repo()
    en = catalog("en")
    for f in FILES:
        left = [t for _l, t in scans[f].unwrapped]
        if f.endswith("/cp.py"):
            # cp.RATING_ZH: the zh-TW word the page shows beside `rating` (English already)
            assert sorted(left) == sorted(["過低", "偏低", "中等", "偏高", "過高"]), left
        else:
            assert left == [], (f, left)
        missing = [m for _l, m in scans[f].msgids if not en.get(m)]
        assert missing == [], (f, missing)


# ---- cp / riegel / maximal / re -----------------------------------------------------------------------

PTS = [{"t": 200, "p": 300, "date": "2026-01-01"}, {"t": 1000, "p": 250, "date": "2026-02-01"}]


def test_cp_checks_and_the_rwc_note_in_both_languages():
    from backend.engine.racepower import cp as CP
    zh = CP.validity(PTS, envelope=True)
    assert zh[-1]["text"] == "所有努力在 14 天內（目前跨 31 天）；曲線包絡線必然跨多天"
    assert CP.validity(PTS)[-1]["text"] == "所有努力在 14 天內（目前跨 31 天）"
    assert CP.rwc_rating(15000, 60, "male")["note"] == "無氧容量過高 → CP 可能被低估"
    with use_locale("en"):
        en = CP.validity(PTS, envelope=True)
        r = CP.rwc_rating(15000, 60, "male")
    assert en[0]["text"] == "At least one effort ≤ 6:00"
    assert en[-1]["text"] == "All efforts within 14 days (now 31 days); a curve envelope always spans several days"
    assert not any(CJK.search(c["text"]) for c in en)
    assert r["note"] == "Anaerobic capacity too high → CP may be underestimated" and r["rating"] == "Too High"
    # an M_() text: the language follows the request that shows it (the inputs cache is shared)
    assert isinstance(en[0]["text"], Msg) and relocalize(en[0]["text"], "zh-TW") == zh[0]["text"]


def test_riegel_table_warnings_and_terrain_names():
    from backend.engine.racepower import re as RE
    from backend.engine.racepower import riegel as R
    assert R.table_k(60000, 21097, 5400)["warning"] == "超出查表範圍（全馬以上）：k 取表中最慢值 −0.12"
    assert R.extrapolation_warning(20000, 3600) == "預估時間 5.6 h 超過你最長有效紀錄 1.0 h 的 1.5 倍——Riegel 在此是外插"
    with use_locale("en"):
        assert R.table_k(60000, 21097, 5400)["warning"].startswith("Beyond the table (longer than a marathon)")
        assert R.table_k(30000, 21097, 5400)["warning"].startswith("The target distance is not a standard one")
        assert R.table_k(10000, 15000, 5400)["warning"].startswith("The previous race is not a standard distance")
        w = R.extrapolation_warning(20000, 3600)
        assert w == "The predicted 5.6 h is more than 1.5× your longest valid effort (1.0 h) — Riegel is extrapolating here"
    assert [translate(lbl, "en") for lbl, _lo in RE.CVI_CATEGORIES] == [
        "Downhill", "Flat", "Slightly hilly", "Somewhat hilly", "Moderately hilly", "Very hilly", "Extremely hilly"]


ROAD = {"km": 10.0, "q4_hr": 150, "peak_hr": 170, "split": 0.95, "p_avg": 250, "longer_p": 260}


def test_maximal_reasons_keep_the_chinese_and_read_in_english():
    from backend.engine.racepower import maximal as MX
    zh = MX.road_maximal(ROAD, 160, 185)
    assert zh["reason"] == ("最後 1/4 平均心率 150 vs 1.00 × LTHR 160 = 160；30 秒最高心率 170 vs 觀測最大心率 185 − 10；"
                            "後半／前半速度 0.950（≥ 0.98 = 平均或負分段）；平均功率 250 W 低於之前 ≥ 1.5 倍時長跑過的 260 W")
    assert zh["checks"][0]["text"] == "10.00 km ≈ 10K"
    assert MX.road_maximal({**ROAD, "q4_hr": 170, "peak_hr": 180, "split": 1.0, "longer_p": 240}, 160, 185)["reason"] == \
        "自配速全力（10K）"
    assert MX.road_maximal({**ROAD, "km": 3.0}, 160, 185)["reason"] == "3.00 km 不在 5K/10K/半馬/全馬 ±10 %"
    with use_locale("en"):
        en = MX.road_maximal(ROAD, 160, 185)
        half = MX.road_maximal({**ROAD, "km": 21.1, "q4_hr": 170, "split": 1.0, "longer_p": 240}, 160, 185)
        trail = MX.trail_maximal({"km": 5, "moving_s": 3000, "hr_avg": 140}, 160, 140)
        word = MX.trail_maximal({"km": 30, "moving_s": 4 * 3600, "hr_avg": 140}, 160, 140, title="race")
    assert en["reason"] == ("Last-quarter average heart rate 150 vs 1.00 × LTHR 160 = 160; 30-s peak heart rate 170 vs "
                            "observed max heart rate 185 − 10; Second half / first half speed 0.950 (≥ 0.98 = even or "
                            "negative split); Average power 250 W is below the 260 W you held earlier for ≥ 1.5× as long")
    assert half["reason"] == "Self-paced all-out (Half marathon)"
    assert trail["reason"].startswith("5.0 km (≥ ") and "Moving 50 min (≥ 90)" in trail["reason"]
    assert word["reason"] == "The title / tags say it was a race"
    for r in (en, half, trail, word):
        assert not CJK.search(r["reason"]) and not any(CJK.search(c["text"]) for c in r["checks"])


# ---- gpx / predict --------------------------------------------------------------------------------------

def test_gpx_errors_in_the_request_language():
    from backend.engine.racepower import gpx as GPX
    with pytest.raises(GPX.GpxError, match="不是 GPX 檔"):
        GPX.parse(b"<x/>")
    with use_locale("en"):
        for data, want in ((b"<x/>", "Not a GPX file (the root element is not <gpx>)"),
                           (b'<?xml version="1.0"?><!DOCTYPE gpx><gpx/>', "The GPX has a DOCTYPE / ENTITY declaration"),
                           (b"<gpx", "Not a valid GPX: "),
                           (b"<gpx></gpx>", "The GPX has no track points (trkpt / rtept)"),
                           (b"x" * (GPX.MAX_BYTES + 1), "The file is larger than ")):
            with pytest.raises(GPX.GpxError) as e:
                GPX.parse(data)
            assert str(e.value).startswith(want) and not CJK.search(str(e.value)), str(e.value)


def test_predict_hiking_flags_and_warnings():
    from backend.engine.racepower import predict as P
    kw = dict(day_plan=[{"km": 20.0, "gain_m": 1500.0, "loss_m": 1500.0}], eph=3.0, weight=60.0,
              biggest_day={"ep": 10.0, "gain_m": 800.0, "moving_h": 4.0})
    zh = P.predict_baiyue(**kw)
    assert zh["days"][0]["flags"] == ["EP 35.0 超過你最大的一天 10.0", "爬升 1500 m 超過你最大的一天 800 m",
                                      f"移動時間 {zh['days'][0]['moving_h']:.1f} h 超過你最長的一天 4.0 h"]
    with use_locale("en"):
        en = P.predict_baiyue(**kw)
        run = P.predict_run(distance_km=50.0, cp=250.0, tte=3000.0, k=-0.07, re=1.0, weight=60.0,
                            longest_effort_s=3600.0)
    assert en["days"][0]["flags"][0] == "EP 35.0 is more than your biggest day (10.0)"
    texts = en["days"][0]["flags"] + [str(w) for w in en["warnings"]] + [str(w) for w in run["warnings"]]
    assert texts and not [t for t in texts if CJK.search(t)], texts


# ---- capacity / hikehr / trailhr / cptest ----------------------------------------------------------------

def test_capacity_texts():
    from backend.engine.racepower import capacity as CAP
    v = CAP.classifier_validation([])
    assert v["rule"] == "每類 ≥ 5 天手動標記且符合率 ≥ 80% 才自動套用"
    with use_locale("en"):
        assert CAP.classifier_validation([])["rule"] == \
            "Applied automatically only with ≥ 5 manually marked days per class and an agreement ≥ 80%"
        assert CAP.classify_day([], None, None, 10.0)["reason"] == "No steep windows or no AeT"
        assert all(not CJK.search(translate(t, "en")) for t in CAP.EVIDENCE.values())
        assert translate(CAP.GROUP_SOURCE, "en").startswith("Group factor (estimate)")
        assert translate(CAP.SIGMA_LOO_SRC, "en") == "Default 0.10 until the back-test"


def test_hike_hr_and_trail_hr_sources():
    from backend.engine.racepower import hikehr as HH
    from backend.engine.racepower import trailhr as TH
    zh = HH.summary([], None, None, 0)
    assert zh["uses"]["vam"]["evidence"] == "定義（描述統計）"
    assert TH.race_level([])[1] == "沒有之前的越野比賽：用 0.90 × LTHR（Friel Z3 下緣）"
    with use_locale("en"):
        hh = HH.summary([], None, None, 0)
        xs = TH.fit_xstar([])
        lvl = TH.race_level([0.9, 0.92])
        parts = [TH.shrink_inside(None), TH.fit([], None), TH.fade_shape([]), TH.fit_slope_cost([]),
                 TH.shrink_delta([0.2, 0.25])]
    assert hh["uses"]["vam"]["evidence"] == "Definition (descriptive statistics)"
    assert xs["label"] == "Est." and xs["source"].startswith("All-out heart-rate curve x*(T)")
    assert lvl[1] == "Median moving heart rate of 2 earlier trail races or all-out efforts"
    for p in parts:
        for path, t in _texts({k: p.get(k) for k in ("source", "label", "warning")}):
            assert not CJK.search(t), (path, t)


def test_cptest_bout_reasons_stay_the_source_text_and_the_prior_reads_in_english():
    from backend.engine.racepower import cptest as CT
    laps = [{"t": 600, "p": 150}, {"t": 180, "p": 300, "hr_max": 160}, {"t": 300, "p": 140},
            {"t": 720, "p": 310, "hr_max": 180}, {"t": 600, "p": 140}]
    with use_locale("en"):                   # the FIT scan cache keeps it: never the request's language
        t = CT.detect(laps)
        prior = CT.w_prime_prior(60, "female")
    assert t["bouts"][0]["why"] == "3′ 300 W 不高於 12′ 310 W（功率應隨時間遞減）；最高心率 160 比 12′ 的 180 低 20 bpm"
    # where a page shows one (the back-test's shown()), it reads in English
    from backend.engine.racepower import backtest as BT
    assert BT.shown({"why": t["bouts"][0]["why"]}, "en")["why"] == \
        "3′ 300 W is not above 12′ 310 W (power should fall with duration); peak heart rate 160 is 20 bpm below the 12′ bout's 180"
    assert prior["source"] == "Ruiz-Alias 2025: amateur women's anaerobic capacity in the Stryd 9/3 test 6.4 ± 2.2 kJ"
    assert CT.w_prime_prior(60, "male")["source"] == "Ruiz-Alias 2025 業餘男性 Stryd 9/3 測試無氧容量 13.1 ± 4.0 kJ"


# ---- share -------------------------------------------------------------------------------------------------

def _plan(warnings):
    return {"type": "baiyue", "summary": {"km": 10.0}, "segments": [], "fuel": {"warnings": warnings}, "days": []}


def test_share_errors_default_title_and_ree_warnings(tmp_path, monkeypatch):
    from backend.engine import hints as H
    from backend.engine.racepower import share as SH
    monkeypatch.setattr(SH, "SHARES_DIR", tmp_path)
    ree = next(m for m in catalog("en") if "基礎代謝" in m)
    with use_locale("en"):
        warn = [H.warning(H.tag("RC28", translate(ree, "en", miss="height"))), H.warning(H.tag("RC20", "Dehydration"))]
        snap = SH.snapshot(_plan(warn), title="", expires_days=None)
        with pytest.raises(SH.ShareError, match="That share link is not valid"):
            SH.load("bad id")
        with pytest.raises(SH.ShareError, match="The expiry can only be"):
            SH.snapshot(_plan([]), title="", expires_days=3)
    assert [w["text"] for w in snap["fuel"]["warnings"]] == ["Dehydration"]     # the REE one dropped
    assert snap["title"] == "賽事計畫"                                           # stored as the source text
    sid = SH.save(snap)
    assert json.loads((tmp_path / f"{sid}.json").read_text("utf-8"))["title"] == "賽事計畫"
    assert SH.load(sid)["title"] == "賽事計畫" and SH.listing()[0]["title"] == "賽事計畫"
    with use_locale("en"):
        assert SH.localized(SH.load(sid))["title"] == "Race plan" and SH.listing()[0]["title"] == "Race plan"
    assert SH.load(SH.save({**snap, "title": "我的比賽"}))["title"] == "我的比賽"


# ---- the demo athlete: derive and the back-test --------------------------------------------------------

@pytest.fixture(scope="module")
def demo_root(tmp_path_factory):
    from backend.demo import build as B
    r = tmp_path_factory.mktemp("demo-sp838b")
    B.build(r, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return r


@pytest.fixture()
def ds(demo_root, monkeypatch):
    from backend.tests import demo_fixtures as F
    F.demo_env(monkeypatch, demo_root)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    return wko5views._dataset()


# texts that are not this package's: the athlete's own names, other modules' (power_source, cp_protocols),
# and cp.RATING_ZH (see above)
OTHERS = re.compile(r"\.name$|\.peaks|\.sex_source$|\.label_power$|\.unused_label$|\.rating_zh$")


def test_derive_in_english_and_the_same_chinese(ds, tmp_path, monkeypatch):
    from backend.engine.racepower import athlete as A
    monkeypatch.setattr(A, "TRAINING_ENV_CACHE", tmp_path / "env.json")
    today = dt.date.today()
    zh = A.derive(ds, today, fetch_weather=False)
    with use_locale("en"):
        en = A.derive(ds, today, fetch_weather=False)
    left = [(p, t) for p, t in _texts(en) if CJK.search(t) and not OTHERS.search(p)]
    assert left == [], left[:10]
    assert en["tte"]["source"] in ("The comparison folder's model TTE", "Default 50 min (the spreadsheet's default)",
                                   "TTE recomputed by the PD model")
    assert en["cp"]["sources"] and not CJK.search(en["cp"]["sources"][0]["label"])
    # the same tree; every text an M_() one shows exactly the old zh-TW text in zh-TW
    zt, et = dict(_texts(zh)), dict(_texts(en))
    assert zt.keys() == et.keys()
    n = 0
    for p, t in zt.items():
        if isinstance(et[p], Msg):
            assert relocalize(et[p], "zh-TW") == t, p
            n += 1
    assert n >= 10
    assert zh["k_default"]["source"] in ("個人擬合", "預設 −0.07（≈ Stryd 表的 k −0.069）")


def test_activity_label_and_the_archive_label():
    from backend.engine.racepower import athlete as A

    class W:                                  # the bits athlete.label reads
        sport_type = "trail running"
        metrics = {"distance": 21.0}

        class entry:
            start = dt.datetime(2026, 5, 1, 7, 0)
    assert A.label(W) == "2026-05-01 越野跑 21.0 km"
    with use_locale("en"):
        assert A.label(W) == "2026-05-01 Trail running 21.0 km"
        # main's SP-944 shape: an M_() text (the cache keeps the msgid), shown in the request's language
        assert relocalize(A._archive_label(1)).startswith("Open-Meteo historical weather: the mean temperature "
                                                          "and humidity during 1 run in the last")
    assert isinstance(A._archive_label(1), Msg) and A._archive_label(1).msgid.startswith("Open-Meteo 歷史資料")


def test_backtest_is_stored_in_the_source_language_and_shown_in_english(ds, tmp_path, monkeypatch):
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(BT, "STORE", tmp_path / "bt.json")
    with use_locale("en"):                   # an English request starts it: the file still holds zh-TW
        r = BT.backtest(ds)
    BT.save(r)
    raw = json.loads((tmp_path / "bt.json").read_text("utf-8"))
    assert raw["notes"][-1].startswith("心率能力（推估）") and raw == BT.load(raw=True)
    assert BT.load() == raw                   # zh-TW: the result as stored
    with use_locale("en"):
        en = BT.load()
    assert en["notes"][-1].startswith("Heart-rate capacity (estimate)")
    assert not [n for n in en["notes"] if CJK.search(n)], en["notes"]
    assert en["capacity"]["categories"]["road"]["label"] == "Road"
    assert not [x for x in en["capacity"]["categories"]["road"]["reasons"] if CJK.search(x)]
    assert en["terrain"]["categories"]["trail"]["groups"]["walking"]["label"].startswith("Mostly walking")
    labels = [x["label"] for x in en["rows"] if x.get("label")]
    assert labels and not [x for x in labels if CJK.search(x)], labels[:5]
    # the flags read the file as it is
    assert BT.flags(tmp_path / "bt.json")[0].keys() == BT.THRESHOLDS.keys()
    # the stored file is unchanged by a read in English
    assert json.loads((tmp_path / "bt.json").read_text("utf-8")) == raw
