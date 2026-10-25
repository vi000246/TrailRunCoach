"""SP-460: the Chinese left on the English season-plan page (賽事周期: its tables, cards, timeline, phase guide)
and the English calculator share page (/share/<id>?lang=en: the result, fuelling and segment labels).

Page text goes through the plan / share catalogs (every key the page asks for exists in zh-TW and en, and the
en text has no Chinese); the plan API gives the phase names, race types and the chart to watch in the page's
language. Synthetic data and the demo athlete only."""
from __future__ import annotations

import datetime as dt
import json
import re
import socket
from pathlib import Path

import pytest

from backend.scripts import i18n_extract as X
from backend.tests import demo_fixtures as F

STATIC = Path(__file__).resolve().parents[1] / "static"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _texts(v):
    return list(v.values()) if isinstance(v, dict) else [v]


def _keys(src: str, call: str, ns: str) -> set[str]:
    """The literal keys a page asks for: t("plan.x") / JT("x") (→ share.x) / data-i18n="ns.x"."""
    found = {m for m in re.findall(rf'\b{call}\("([\w.]+)"', src) if not m.endswith(".")}   # "x." + k: listed by the test
    if call == "t":                                    # t() takes full keys: this namespace's only
        out = {k for k in found if k.startswith(ns + ".")}
    else:                                              # the page's own helper adds the namespace
        out = {f"{ns}.{k}" for k in found}
    out |= set(re.findall(rf'data-i18n="({ns}\.[\w.]+)"', src))
    return out


def _check(keys: set[str], ns: str) -> None:
    zh, en = _cat("zh-TW", ns), _cat("en", ns)
    short = sorted(k[len(ns) + 1:] for k in keys)
    assert [k for k in short if k not in zh] == [], f"{ns}: keys missing from zh-TW"
    assert [k for k in short if not en.get(k)] == [], f"{ns}: keys missing from en"
    assert [k for k in short if any(CJK.search(x) for x in _texts(en[k]))] == [], f"{ns}: Chinese in en"


# ---- the season-plan page ----------------------------------------------------------------------------------

def test_plan_page_has_no_hardcoded_chinese():
    p = STATIC / "plan.html"
    left = [s for _l, s in X.scan_html(p, p.read_text("utf-8")).unwrapped]
    # the one Chinese literal left is the periodization view's name in the chart link's URL (an id, not text)
    assert left == ["周期化訓練"], left


def test_plan_page_keys_exist_in_both_languages():
    src = (STATIC / "plan.html").read_text("utf-8")
    keys = _keys(src, "t", "plan")
    # keys built at run time: the phase guide's cards and the combined-target names
    keys |= {f"plan.guide.{k}" for k in ("recovery", "base", "specific", "taper")}
    keys |= {f"plan.guide.{k}_{i}" for k, n in {"recovery": 3, "base": 4, "specific": 4, "taper": 3}.items()
             for i in range(1, n + 1)}
    keys |= {f"plan.goal.{k}" for k in ("distance_km", "climbing_m", "climb_per_km", "est_hours", "days")}
    assert "plan.ev.th.note" in keys and "plan.ph.to_manual" in keys
    _check(keys, "plan")


def test_plan_page_plurals():
    en = _cat("en", "plan")
    assert en["ev.days_to"] == {"one": "{n} day", "other": "{n} days"}
    assert en["now.next_when"]["one"].endswith("in {n} day")


# ---- the share page ----------------------------------------------------------------------------------------

def test_share_page_has_no_hardcoded_chinese():
    p = STATIC / "share.html"
    assert X.scan_html(p, p.read_text("utf-8")).unwrapped == []


def test_share_page_keys_exist_in_both_languages():
    src = (STATIC / "share.html").read_text("utf-8")
    keys = _keys(src, "JT", "share")
    keys |= {f"share.stop.{k}" for k in ("water", "aid", "big", "medical", "self")}
    keys |= {f"share.join.{k}" for k in ("signup", "waitlist", "demo")}
    assert {"share.result", "share.fuel.carbs", "share.splits", "share.th.power"} <= keys
    _check(keys, "share")


def test_threshold_labels_follow_the_language():
    # the 「目前區間用的門檻」 card's LTHR / AeT source ("manual entry 2026-09-29"), Chinese unchanged
    from backend.engine import planning as P
    from backend.i18n import use_locale
    plan = P.Plan(thresholds=[P.Threshold(date="2026-09-29", lthr=168, lthr_method="manual"),
                              P.Threshold(date="2026-09-30", lthr=170, lthr_method="estimate")])
    day = dt.date(2026, 9, 29)
    assert P.threshold_row(plan, "lthr", day)["label"] == "手動輸入 2026-09-29"
    with use_locale("en"):
        assert P.threshold_row(plan, "lthr", day)["label"] == "manual entry 2026-09-29"
        assert P.threshold_row(plan, "lthr", day + dt.timedelta(days=1))["label"] == \
            "automatic estimate (applied 2026-09-30)"
    assert P.threshold_row(plan, "lthr", day + dt.timedelta(days=1))["label"] == "自動估算（已套用 2026-09-30）"


def test_share_errors_in_english():
    from backend.i18n import translate
    assert translate("這個分享不存在或已刪除", "en") == "This share doesn't exist or was deleted"
    assert translate("這個分享已過期", "en") == "This share has expired"


# ---- the plan API in English (demo athlete) ----------------------------------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-sp460")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return root


@pytest.fixture
def demo(built, monkeypatch):
    F.demo_env(monkeypatch, built)
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        c.get("/demo")
        yield c


def test_plan_api_labels_follow_the_language(demo):
    en = demo.get("/api/v1/plan?lang=en").json()
    zh = demo.get("/api/v1/plan?lang=zh-TW").json()
    assert not [v for v in en["phase_labels"].values() if CJK.search(v)], en["phase_labels"]
    assert not [v for v in en["kinds"].values() if CJK.search(v)], en["kinds"]
    assert not [e["kind_label"] for e in en["events"] if CJK.search(e["kind_label"])]
    assert zh["phase_labels"]["base"] == "基礎期" and zh["kinds"]["road"] == "路跑賽"
    cur = en["current_phase"]
    if cur and cur.get("dashboard"):
        assert CJK.search(cur["dashboard"]), cur          # the view's dashboard id stays as it is (the link)
        assert not CJK.search(cur["dashboard_label"]), cur


def test_plan_page_in_english_renders_the_table_heads(demo):
    html = demo.get("/api/v1/plan/page?lang=en").text
    assert "<th>Note</th>" in html or '>Note</th>' in html
    assert "備註" not in html.split("<script>")[0]
