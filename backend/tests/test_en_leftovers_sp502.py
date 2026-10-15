"""SP-502: the Chinese left on the English overview, 能力頁 and 課表 week view, and the 有氧／無氧訓練效果
tile cut off under its ?. The fast parts: the status in English (indicator names, the 長期訓練負荷 / 負荷平衡
verdicts the KPI tiles show) and per language in the status cache, the phase names, the power curve's
note / legend / 擬合不出 PD 模型 text, the TIS tile's `wide`, and the page literals the screenshots showed.

The browser check (zh first, then en; the tile's box at desktop and phone width) is
backend/tests/e2e/test_en_leftovers_e2e_sp502.py. Synthetic data only."""
import datetime as dt
import json
import re
from types import SimpleNamespace

from backend.api import overview as OV
from backend.engine import planning as P
from backend.engine import workout_review as R
from backend.engine.wko5expr import recentbests as RB
from backend.engine.wko5expr import render as RD
from backend.engine.wko5expr import viewi18n as VI
from backend.i18n import use_locale
from backend.scripts import i18n_extract as X
from backend.tests.test_review_tis_card import _cards, _intervals
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.wko5_fakes import FakeDataset

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
TODAY = dt.date(2026, 9, 30)


def _no_cjk(texts, where: str) -> None:
    bad = [t for t in texts if isinstance(t, str) and CJK.search(t)]
    assert not bad, f"{where}: still Chinese: {bad}"


# ---- the status (總覽's KPI tiles, indicator cards, to-do titles) -------------------------------

def _status():
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY as QT, _daily, _ds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    ds = _ds(_daily(), plan)
    return Status(ds, plan, QT, prefs=PP.Prefs()).compute().to_dict()


def test_status_in_english_names_and_load_verdicts():
    with use_locale("en"):
        st = _status()
    I = {i["id"]: i for i in st["indicators"]}
    _no_cjk([i["title"] for i in st["indicators"]], "indicator names")
    for k in ("fitness", "form"):                      # the 長期訓練負荷 / 負荷平衡 KPI tiles
        _no_cjk([I[k]["verdict"], I[k]["action"], I[k]["why"]], f"{k} verdict")
    assert "acute load" in I["form"]["why"]            # the 負荷平衡 tile's line: 「短期負荷 16」 -> "acute load 16"
    if st["phase"]:
        _no_cjk([st["phase"]["label"], I["phase"]["text"]], "phase name")
    # Chinese is unchanged
    with use_locale("zh-TW"):
        zh = {i["id"]: i for i in _status()["indicators"]}
    assert zh["long"]["title"] == "最長單次" and zh["fitness"]["title"] == "長期訓練負荷（Chronic load）"
    assert zh["form"]["why"].startswith("負荷平衡（Load balance）") and "短期負荷" in zh["form"]["why"]


def test_status_cache_key_has_the_language(monkeypatch):
    """Root cause of 「短期負荷 16」 on the English overview: the status (its words made by _()) was cached
    without the language, so an English page got the Chinese one a Chinese page had computed first."""
    from backend.engine import hr_profile as HRP
    from backend.engine import plan_prefs as PP
    from backend.engine import plan_store as PS
    monkeypatch.setattr(PP, "load", lambda: SimpleNamespace(stamp=lambda: "p1"))
    monkeypatch.setattr(PS, "test_sessions", lambda: [])
    monkeypatch.setattr(HRP, "stamp", lambda *a: "hr")
    ds = SimpleNamespace(today=20000.0)
    with use_locale("zh-TW"):
        zh, _p = OV._status_key(ds, TODAY)
    with use_locale("en"):
        en, _p = OV._status_key(ds, TODAY)
    assert zh != en and zh[OV._SK_LOCALE] == "zh-TW" and en[OV._SK_LOCALE] == "en"
    assert zh[:OV._SK_LOCALE] == en[:OV._SK_LOCALE]
    assert OV._settings_sig(zh) != OV._settings_sig(en)    # no stale answer in the other language either


def test_status_but_hr_takes_another_language(monkeypatch):
    """The 課表's readers of the plan / phases (status_but_hr) may take the other language's Status."""
    me = OV._tenancy.current().id
    head = (me, 1, TODAY, 100.0, "prefs", ())
    other = object()
    monkeypatch.setattr(OV, "_status_cache", {(*head, "hr:hrr", None, "z3", "exp", "zh-TW"): other})
    monkeypatch.setattr(OV, "_status_key", lambda ds, today: ((*head, "hr:lthr", None, "z3", "exp", "en"), None))
    assert OV.status_but_hr(None, TODAY) is other


def test_phase_names_in_english():
    ph = P.Phase(kind="specific", start="2026-09-19", end="2026-10-30")
    with use_locale("en"):
        assert {k: P.Phase(kind=k, start="2026-01-01", end="2026-01-02").label for k in P.PHASES} == {
            "transition": "Transition", "base": "Base", "specific": "Build", "taper": "Taper", "event": "Race",
            "recovery": "Recovery", "rebuild": "Rebuild"}
    with use_locale("zh-TW"):
        assert ph.label == "專項期"


# ---- 能力頁: the running power curve ------------------------------------------------------------

def test_recent_bests_note_and_durations_in_english():
    with use_locale("en"):
        assert [RB.duration_label(s) for s in (30, 300, 330, 3600, 5400)] == ["30 s", "5 min", "5 min 30 s", "1 h", "1 h 30 min"]
    with use_locale("zh-TW"):
        assert [RB.duration_label(s) for s in (30, 300, 330, 3600, 5400)] == ["30 秒", "5 分鐘", "5 分 30 秒", "1 小時", "1 小時 30 分"]
    day = 20000.0
    ds = SimpleNamespace(today=day, workouts=[SimpleNamespace(day=day - 1)])
    with use_locale("en"):
        r = RB.summarize({"series": []}, ds, 7)
    assert r["note"].startswith("Nothing beat your earlier bests in the last 7 days (data up to ")
    with use_locale("zh-TW"):
        assert RB.summarize({"series": []}, ds, 7)["note"].startswith("近 7 天沒有超越先前的最佳（資料到 ")


def test_power_curve_legend_in_english_follows_the_window():
    view = json.loads((X.VIEWS / "training.json").read_text("utf-8"))
    tr = json.loads((X.VIEWS / "i18n" / "en.json").read_text("utf-8"))["training"]
    en = VI.translate_view(view, tr)
    ch = next(c for d in en["dashboards"] for c in d["charts"] if c.get("id") == "run-power-recent-bests")
    shown = [s["name"] for s in ch["series"] if s.get("role") != RB.GAIN_ROLE]
    _no_cjk(shown, "power curve legend")
    assert "New bests, last 7 days" in shown
    ch14, _info = RB.apply_window(ch, "14")
    assert "New bests, last 14 days" in [s["name"] for s in ch14["series"]]
    zh = next(c for d in view["dashboards"] for c in d["charts"] if c.get("id") == "run-power-recent-bests")
    assert "近 14 天新高" in [s["name"] for s in RB.apply_window(zh, "14")[0]["series"]]
    # every series of the 能力 dashboard has its English name
    cap = next(d for d in en["dashboards"] if d.get("id") == view["dashboards"][3]["id"])
    _no_cjk([s.get("name") for c in cap["charts"] for s in c.get("series", []) if s.get("role") != RB.GAIN_ROLE],
            "能力 dashboard legends")


def test_no_pd_model_text_in_english():
    ds = SimpleNamespace(corrections=None)
    with use_locale("en"):
        t = RD._pd_reason(ds, 0, 1)
    _no_cjk([t], "擬合不出 PD 模型")
    assert t.startswith("The power–duration (PD) model can't be fitted") and "values. Comparison mode" in t
    with use_locale("zh-TW"):
        assert RD._pd_reason(ds, 0, 1).startswith("這段期間擬合不出功率–時間（PD）模型")


# ---- the 有氧／無氧訓練效果 tile -------------------------------------------------------------------

def test_tis_tile_is_wide(monkeypatch):
    # the PD fit sees a known curve (the synthetic samples are too short to fit one), as test_review_tis_card
    from backend.engine.wko5expr import evaluator as E
    from backend.tests.test_wko5expr_functions import model_curve
    monkeypatch.setattr(E.Evaluator, "_workout_curve", lambda self, node, w: model_curve())
    ds = FakeDataset([_run(TODAY - dt.timedelta(days=2), minutes=52, power=200.0),
                      _run(TODAY, minutes=50, power=_intervals())], TODAY, settings=SETTINGS)
    with use_locale("en"):
        c = _cards(ds, 1)["tis"]
    assert c["wide"] is True and c["label"] == "Aerobic / Anaerobic training effect"
    assert "sub" not in c and "1–10, not the watch's 0–5" in c["tip"]      # SP-567: the scale in the ? only
    with use_locale("zh-TW"):
        assert _cards(ds, 1)["tis"]["wide"] is True
    # 「算不出」 (no PD model) is as wide
    monkeypatch.setattr(R, "tis_scores", lambda ds, w: (None, None))
    with use_locale("en"):
        assert _cards(ds, 1)["tis"]["wide"] is True
    src = (X.STATIC / "charts.html").read_text("utf-8")
    assert ".rv-c.w2 { grid-column: span 2; }" in src and ".rv-c.zn, .rv-c.w2 { grid-column: 1 / -1; }" in src
    assert '${c.wide ? " w2" : ""}' in src and '<span class="kt">${esc(c.label)}</span>' in src


# ---- the page literals the screenshots showed --------------------------------------------------

SHOT_LITERALS = {
    "overview.html": ("詳細</summary>", "<b>週", "今天</span>", "做了什麼", "待辦與警示", "回到現在", "課表編輯 →",
                      "項 · 全部運動一起算", "來源：${esc(i.source)}", "· 負荷 ${n0(s.done_by.tss)}"),
    "schedule.html": ("那週</span>", "調整課表偏好 →", "`實際 ", "目前 <b", ">週${", "週統計", "暫定（重排時會依進度調整）",
                      "<span>時間</span>", ">本週<", "週${wd(d)}<b>", '"沒有活動" : "休息"'),
}


def test_pages_have_no_unwrapped_shot_literals():
    for page, lits in SHOT_LITERALS.items():
        fs = X.scan_html(X.STATIC / page)
        left = [(ln, s[:80]) for ln, s in fs.unwrapped if any(lit in s for lit in lits)]
        assert not left, f"{page}: {left}"
    # the toolbar of the 課表 and the overview's section heads are marked for the server-side catalog
    sch = (X.STATIC / "schedule.html").read_text("utf-8")
    for k in ("schedule.today", "schedule.view.month", "schedule.view.week"):
        assert f'data-i18n="{k}"' in sch
    ov = (X.STATIC / "overview.html").read_text("utf-8")
    for k in ("overview.sec.log", "overview.sec.todo", "overview.to_sched", "overview.now", "overview.unit.week"):
        assert f'data-i18n="{k}"' in ov


def test_new_catalog_keys_have_english():
    cats = X.frontend_catalogs()
    new = {"overview": ("wk.load", "wk.min", "wk.today", "wk.rest", "wk.none", "sec.log", "sec.todo", "unit.week",
                        "unit.month", "unit.year", "now", "to_sched", "todo_n", "ind_n"),
           "schedule": ("today", "view.aria", "view.month", "view.week", "phase_now", "pn.week_of", "pn.prefs",
                        "chip.actual", "chip.done", "wk.sum_col", "wk.time", "wk.this_week", "wk.provisional",
                        "wk.no_plan", "day.none", "day.rest")}
    for ns, keys in new.items():
        for k in keys:
            zh, en = cats["zh-TW"][ns][k], cats["en"][ns][k]
            assert zh and en, (ns, k)
            _no_cjk(list(en.values()) if isinstance(en, dict) else [en], f"{ns}.{k}")
    assert cats["en"]["schedule"]["pn.week_of"] == "Week of {d}" and cats["zh-TW"]["schedule"]["pn.week_of"] == "{d} 那週"
