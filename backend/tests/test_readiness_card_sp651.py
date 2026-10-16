"""SP-651: the 賽前完備程度 wide card (kind "readiness") on top of the 專項期 and 減量期 dashboards.

The board itself (race_readiness.board) is SP-650's (test_race_readiness_sp650.py); here: the view, the
chart API on the demo athlete (race_readiness.inputs on a real Dataset), the viewer's script and its
strings. The browser part: e2e/test_readiness_card_sp651_e2e.py."""
from __future__ import annotations

import datetime as dt
import json
import re
import socket
from pathlib import Path

import pytest

from backend.engine.wko5expr.customviews import REPO_VIEWS, parse_view
from backend.tests import demo_fixtures as F

ROOT = Path(__file__).resolve().parents[1]
VIEW = "周期化訓練"
STATES = ("done", "due", "miss", "fut", "tap", "unk")
IDS = {"build": "race-readiness", "taper": "race-readiness-taper"}
# 卡上不附來源：no person / seminar names in what the card shows
SOURCE_WORDS = ("Koop", "Uphill", "Friel", "Pfitzinger", "Bosquet", "Seiler", "Wang", "Hickson", "Mujika",
                "研討會", "教練）", "（UA", "Daniels", "Magness")


def _view():
    return parse_view(json.loads((REPO_VIEWS / "periodization.json").read_text(encoding="utf-8")))


def test_custom_view_accepts_the_readiness_panel():
    v = parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [{"title": "t", "kind": "readiness"}]}]})
    assert v["dashboards"][0]["charts"][0]["kind"] == "readiness"


def test_build_and_taper_open_with_the_readiness_card():
    v = _view()
    for did, cid in IDS.items():
        d = next(x for x in v["dashboards"] if x["id"] == did)
        first = d["charts"][0]
        assert first["kind"] == "readiness" and first["id"] == cid, (did, first)
        assert not any(w in first["description"] or w in first["title"] for w in SOURCE_WORDS), first
    en = json.loads((REPO_VIEWS / "i18n" / "en.json").read_text(encoding="utf-8"))["periodization"]["charts"]
    for cid in IDS.values():
        assert en[cid]["title"] and en[cid]["description"]
        assert not any(w in en[cid]["description"] for w in SOURCE_WORDS)


def test_viewer_draws_the_kind_with_its_own_script():
    viewer = (ROOT / "static" / "charts.html").read_text("utf-8")
    js = (ROOT / "static" / "readiness.js").read_text("utf-8")
    assert '<script src="/api/v1/static/readiness.js" defer></script>' in viewer
    assert 'res.kind === "readiness"' in viewer and '"readiness"' in viewer
    assert "window.Readiness" in js
    keys = set(re.findall(r'T\("([a-z_.]+)"[,)]', js)) | {"st_" + s for s in STATES} | {"lg_" + s for s in STATES}
    assert {"schedule", "week_of", "every_week", "done_of"} <= keys
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "viewer.json").read_text("utf-8"))
        miss = {k for k in keys if "readiness." + k not in cat}
        assert not miss, (loc, miss)
    assert not any(w in js for w in SOURCE_WORDS)


# ---------------------------------------------------------------------------
# the chart API on the demo athlete: race_readiness.compute → inputs() on the real Dataset
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-sp651")
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
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        c.get("/demo")
        yield c
    wko5views._dataset_cfg.cache_clear()


def _get(c, did, extra=""):
    v = next(x for x in c.get("/api/v1/app/views").json() if x["name"] == VIEW)
    di = next(i for i, d in enumerate(v["dashboards"]) if d.get("id") == did)
    ci = next(i for i, ch in enumerate(v["dashboards"][di]["charts"]) if ch.get("id") == IDS[did])
    r = c.get(f"/api/v1/app/views/{VIEW}/dashboards/{di}/charts/{ci}?begin=2026-01-01{extra}")
    assert r.status_code == 200, r.text[:500]
    return r.json()


def test_api_draws_the_board_of_the_demos_a_race(demo):
    j = _get(demo, "build")
    assert j["kind"] == "readiness", j
    R = j["readiness"]
    assert R["race"] and R["race"]["name"], R.get("text")
    assert [s["id"] for s in R["stages"]] == ["volume", "validation"]          # SP-677: 專項期 only
    assert [w["week"] for w in R["weeks"]] == list(range(10, 2, -1))
    assert j["phase"] == "build" and "dash=" in j["other"]                       # → the 減量期 card
    # the activities were read (inputs() on the real Dataset): some cell of the past weeks is judged
    cells = [c for s in R["stages"] for i in s["items"] for c in i["cells"].values()]
    if not R["preview"]:
        assert any(c["state"] in ("done", "miss", "due") for c in cells), R["text"]
    # 「安排課表」: the 課表 page on this week's Monday
    today = dt.date.fromisoformat(R["today"])
    mon = today - dt.timedelta(days=today.weekday())
    assert j["schedule"].endswith(f"?day={mon.isoformat()}"), j["schedule"]
    t = _get(demo, "taper")
    assert t["phase"] == "taper" and t["readiness"]["race"]["id"] == R["race"]["id"]
    assert [c["id"] for c in t["readiness"]["checks"]] == ["taper_volume", "climb_early", "ctl_down_days", "race_tsb"]
    assert "weeks" not in t["readiness"] and "dash=" in t["other"]


def test_api_without_a_next_a_race_says_so(demo, monkeypatch, tmp_path):
    from backend.api import wko5views as WV
    from backend.engine import specific_phase as SP
    from backend.engine.wko5expr.render_cache import RenderCache
    monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render-cache"))
    monkeypatch.setattr(SP, "race_day", lambda *a, **k: None)
    j = _get(demo, "build")
    assert j["kind"] == "readiness" and j["readiness"]["race"] is None
    assert j["readiness"]["text"] and j["readiness"]["stages"] == []
