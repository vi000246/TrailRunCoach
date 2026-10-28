"""SP-810: the rest of the English overview — the 做了什麼 KPI tiles and their in-progress line, the sport
legend, the intensity bands, the indicator cards' values / verdicts / details / sources, this week's 「詳細」,
the to-do rows and the phase goal, the period buttons and ? helps; the pending-proposal banner (autoplan.js,
shared with the 課表 page), the suggestion box's test / zone / injury rows, the 下一課建議 panel.

The activity rows keep their zh-TW category / type words (compliance and plan_match read them; the 課表 inputs
are one cache for every language): GET /summary and /weekplan show them in the request's language
(api/overview._shown, engine/stored_text). Chinese is unchanged. Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.i18n import translate as T
from backend.i18n import use_locale
from backend.scripts.i18n_extract import CJK

STATIC = Path(__file__).resolve().parents[1] / "static"
# names the user gave (races, trips, routes) stay as written — the demo's are Chinese
NAME_KEYS = {"name", "event", "next_a", "race", "title_src", "event_name"}


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _cjk_leaves(o, path="", skip=frozenset()) -> list:
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k not in skip:
                out += _cjk_leaves(v, f"{path}.{k}", skip)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out += _cjk_leaves(v, f"{path}[{i}]", skip)
    elif isinstance(o, str) and CJK.search(o):
        out.append((path, o))
    return out


# ---- the files ----------------------------------------------------------------------------------------

def test_the_files_have_no_unwrapped_chinese():
    from backend.scripts import i18n_extract as X
    scans = X.scan_repo()
    for f in ("backend/static/overview.html", "backend/static/autoplan.js", "backend/engine/status.py",
              "backend/engine/panels/recommend.py", "backend/engine/suggestions.py", "backend/api/overview.py"):
        assert scans[f].unwrapped == [], (f, scans[f].unwrapped[:5])


def test_the_page_catalog_has_english_for_every_key():
    html = (STATIC / "overview.html").read_text("utf-8")
    zh, en = _cat("zh-TW", "overview"), _cat("en", "overview")
    used = set(re.findall(r'"overview\.([\w.]+)"', html)) | set(re.findall(r'\btO\("([\w.]+)"', html))
    assert sorted(k for k in used if not k.endswith((".", "_")) and (k not in zh or not en.get(k))) == []
    assert all(f"log.unit_{u}" in en for u in ("week", "month", "year"))         # tO("log.unit_" + R.unit)
    flat = lambda v: " ".join(v.values()) if isinstance(v, dict) else v     # noqa: E731
    assert [k for k, v in en.items() if CJK.search(flat(v))] == []
    assert set(zh) == set(en)
    # Chinese as it was
    assert zh["page_title"] == "訓練總覽" and zh["log.in_progress"] == "（進行中 {p}）" and zh["b2b.title"] == "B2B 連續長天"
    assert zh["gloss.cp_term"] == "CP 測試" and zh["gloss.gate_term"] == "間歇門檻"


def test_the_english_page_shows_no_chinese():
    from backend.i18n.pages import render
    html = render("overview", "en")
    body = re.sub(r"<script>window.__I18N__.*?</script>|<style>.*?</style>|<!--.*?-->", "", html, flags=re.S)
    body = re.sub(r"(?m)//[^\n]*$", "", body)            # the page script's comments
    # code that reads the server's zh-TW words (not shown): formWhy's labels, kpiWord's punctuation, the
    # 「標題：一句」 split, 換了課：
    for code in ("/^(?:TSB|負荷平衡（Load balance）|負荷平衡|狀況（Form）|狀況|Load balance|Form)[^（(]*/", '[^（(]*/, "")',
                 '/^[（(]([^]*)[）)]$/', "/[，、：（()）；,:;]/", "/：|: /", "/^換了課：/"):
        body = body.replace(code, "")
    assert CJK.findall(body) == [], sorted({ln.strip()[:120] for ln in body.splitlines() if CJK.search(ln)})[:10]
    assert "<title data-i18n=\"overview.page_title\">Training overview</title>" in html
    assert 'aria-label="Previous period"' in html and "How much each period held" in html
    zh = render("overview", "zh-TW")
    assert "<title data-i18n=\"overview.page_title\">訓練總覽</title>" in zh and 'aria-label="上一期"' in zh


# ---- the status (indicator cards, to-do, headline, phase goal) ----------------------------------------

def _status():
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY as QT, _daily, _ds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    return Status(_ds(_daily(), plan), plan, QT, prefs=PP.Prefs()).compute().to_dict()


# the 間歇門檻 card is quality_gate.indicator's (its own package); its `extra` is the gate's state
NOT_MINE = {"gate"}


def test_status_cards_in_english():
    with use_locale("en"):
        st = _status()
    for i in st["indicators"]:
        if i["id"] in NOT_MINE:
            continue
        left = _cjk_leaves({k: i[k] for k in ("title", "text", "verdict", "why", "action", "source")})
        assert left == [], (i["id"], left)
    acts = [a for a in st["actions"] if not set(a["because"]) & NOT_MINE]
    assert _cjk_leaves([{k: a[k] for k in ("title", "detail", "source")} for a in acts]) == []
    assert not CJK.search(st["headline"]) or any(i["id"] in NOT_MINE for i in st["indicators"] if i["title"] in st["headline"])
    if st["phase"]:
        assert not CJK.search(st["phase"]["goal"])


def test_status_cards_chinese_unchanged():
    with use_locale("zh-TW"):
        st = _status()
    I = {i["id"]: i for i in st["indicators"]}
    assert I["volume"]["why"].startswith("上週 ") and "前 4 週平均" in I["volume"]["why"]
    assert I["intensity"]["source"].startswith("底線 75% 推估")
    assert I["climb"]["source"] == "Koop《Training Essentials for Ultrarunning》"
    assert all("：" in a["title"] for a in st["actions"] if a["because"] != ["phase"])


def test_the_phase_goals_and_the_words_behind_the_cards():
    from backend.engine import status as S
    with use_locale("en"):
        goals = {k: T(v) for k, v in S.PHASE_GOAL.items()}
    assert goals["specific"] == "Train what the race needs: climbing, long hours, climb density close to the race"
    assert not [g for g in goals.values() if CJK.search(g)]
    assert S.PHASE_GOAL["base"] == "練有氧引擎：大量低強度、EF 往上"            # the msgid, Chinese as it was
    assert T("{n} 次", "en", n=1) == "1 run" and T("{n} 次", "en", n=3) == "3 runs" and T("{n} 次", "zh-TW", n=3) == "3 次"
    assert T("{title}：{verdict}", "en", title="Strength", verdict="Hardly any") == "Strength: Hardly any"
    assert T("近 14 天有 {n} 天熱暴露，最後一次 {d} 天前", "en", n=1, d=3) == \
        "Heat exposure on 1 day of the last 14, the last 3 days ago"
    assert T("{n:.1f} 次/週", "en", n=0.5) == "0.5/wk" and T("{n:.1f} 次/週", "zh-TW", n=0.5) == "0.5 次/週"
    for src in (S.SRC_TP_TSB, S.SRC_VOLUME, S.SRC_LOW_SHARE, S.SRC_KOOP, S.SRC_FRIEL, S.SRC_CHIANG, S.SRC_SEILER):
        assert not CJK.search(T(src, "en")), src


# ---- the API: what the overview page shows (demo athlete, synthetic) ------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import datetime as dt
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-root")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return root


@pytest.fixture
def client(built, monkeypatch):
    import socket
    from backend.tests import demo_fixtures as F
    F.demo_env(monkeypatch, built)
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):                # as test_demo_smoke: no network but the test client
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        yield c
    wko5views._dataset_cfg.cache_clear()


def test_the_overview_apis_in_english(client):
    for unit in ("week", "month", "year"):
        r = client.get(f"/api/v1/overview/summary?unit={unit}&lang=en").json()
        assert _cjk_leaves(r, skip=NAME_KEYS) == [], unit
        assert r["categories"]["road"]["label"] == "Road" and r["categories"]["hike"]["label"] == "Hike"
    w = client.get("/api/v1/overview/weekplan?lang=en").json()
    shown = {k: w.get(k) for k in ("mode_label", "why", "rules", "notes", "long_weekday")}
    shown["done"] = w["done"]
    shown["thresholds"] = {k: w["thresholds"].get(k) for k in ("cp_source", "aet_source", "easy_cap_label", "easy_cap_tip")}
    assert _cjk_leaves(shown, skip=NAME_KEYS | {"detail"}) == []
    assert w["long_weekday"] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    zh = client.get("/api/v1/overview/weekplan?lang=zh-TW").json()
    assert zh["long_weekday"] in "一二三四五六日" and zh["done"]["activities"][0]["category_label"] in ("路跑", "越野跑", "登山健行")
    s = client.get("/api/v1/overview/status?lang=en").json()
    for i in s["indicators"]:
        if i["id"] not in NOT_MINE:
            assert _cjk_leaves({k: i[k] for k in ("title", "text", "verdict", "action", "source")}) == [], i["id"]


def test_long_weekday_stays_data_for_the_projection():
    from backend.api import overview as OV
    from backend.engine import overview as O
    with use_locale("en"):
        assert OV._shown({"long_weekday": "六"})["long_weekday"] == "Sat"
    with use_locale("zh-TW"):
        assert OV._shown({"long_weekday": "六"})["long_weekday"] == "六"
    assert O.WEEKDAYS.index("六") == 5 and O.CATEGORIES["road"][0] == "路跑"


def test_easy_cap_label_in_english():
    from backend.api import overview as OV
    with use_locale("en"):
        out = OV._shown({"thresholds": {"easy_cap_label": "輕鬆跑上限 150 bpm（實測 AeT）", "cp_source": "測試"}})
    assert out["thresholds"]["easy_cap_label"] == "Easy-run cap 150 bpm (measured AeT)"


# ---- autoplan.js (the 總覽 and the 課表) ---------------------------------------------------------------

def test_autoplan_words_in_both_languages():
    zh, en = _cat("zh-TW", "common"), _cat("en", "common")
    js = (STATIC / "autoplan.js").read_text("utf-8")
    keys = set(re.findall(r'common\.(autoplan\.[\w.]+)', js))
    for grp in ("st", "act", "push"):         # main's SP-909 groups
        keys |= {k for k in zh if k.startswith(f"autoplan.{grp}.")}
    assert [k for k in keys if k not in zh or k not in en] == []
    flat = lambda v: " ".join(v.values()) if isinstance(v, dict) else v     # noqa: E731
    assert [k for k in keys if CJK.search(flat(en[k]))] == []
    # Chinese as the literals were
    assert zh["autoplan.st.superseded"] == "已被新提案取代" and zh["autoplan.push.off"] == "未推送（自動推送已關）"
    assert zh["autoplan.approve_btn"] == "同意，套用並推送" and zh["autoplan.item"] == "{day} {act}「{title}」"
    assert en["autoplan.pending_title"] == "⚠ Plan to confirm"


# ---- the suggestion box's rows, the 下一課建議 panel ------------------------------------------------------

def test_suggestion_rows_in_english():
    from backend.engine import suggestions as SG
    zone = {"suggestions": [], "events": [{"field": "aet", "date": "2026-10-01", "text": "x"}]}
    with use_locale("en"):
        rows = SG.zone_rows(zone, set(), lambda *a: False, lambda *a: [])
    assert rows[0]["title"] == "Zones updated" and not CJK.search(rows[0]["help"])
    rows = SG.zone_rows(zone, set(), lambda *a: False, lambda *a: [])
    assert rows[0]["title"] == "區間已更新"


def test_recommend_panel_in_english():
    from backend.engine.panels.recommend import recommend
    focus = {"buckets": [{"key": "high", "label": "高強度", "state": "short", "pct": 5, "target": [15, 25]}]}
    with use_locale("en"):
        r = recommend(None, focus, "base", tsb=-12.0, ctl=40.0)
        t = recommend(None, None, "taper")
    assert r["headline"].startswith("Tempo run / threshold intervals") and t["reasons"] == ["Currently in the taper"]
    assert [x for x in r["reasons"] if CJK.search(x)] == []
    assert recommend(None, None, "taper")["reasons"] == ["目前是減量期"]
