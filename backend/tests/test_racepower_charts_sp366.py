"""
SP-366: the charts at the bottom of the race calculator (坡度 RE 曲線 + 跑走切換速度,
爬坡步頻分布) showed 「Failed to fetch」 on a marathon.

「Failed to fetch」 is a connection that ended with no HTTP answer (a gateway timeout would be
a 502 / 504 / 524 page). The cause is not confirmed: no log of the failing request exists, and
locally both requests answered 200 (slow only while the server was still building the dataset
after a start; warm, under 3 s). Candidates: the server restarting mid-request (a restart, a
redeploy or container restart, an out-of-memory kill), the machine sleeping, or the network
dropping. Mitigations here, whichever it was:
  * one computation per key (backend/singleflight.py) for GET /grade-model and
    /cadence-check, so concurrent callers (/plan, the other chart, a second tab) wait for the
    first one instead of each fitting the whole year again;
  * the page: a dropped connection or a gateway answer reads as a short message with 重試, not
    the raw error, and a chart asks once while its request is in flight. Every race type shows
    the charts (owner 2026-10-08: the road plan uses the grade curve too).

Synthetic only: a fake Dataset of flat road runs, fake inputs (no WKO5 folder, no DB).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from backend.engine.racepower import athlete as A
from backend.engine.racepower import backtest as BT
from backend.engine.racepower import grade_model as GM
from backend.tests.test_racepower_export import client  # noqa: F401  (fixture)
from backend.tests.test_racepower_runwalk import _ds, track
from backend.tests.test_racepower_v2 import RE0

from backend.api.racepower import _grade_models as REAL_GRADE_MODELS  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "racepower.html"


def flat_road_arrays():
    """Two flat road runs' samples: 10 km at 1 %, 172 spm (no climbing to speak of)."""
    return track([(10000, 0.0, 86, 3.3)])


@pytest.fixture()
def flat_road_athlete(monkeypatch):
    from backend.api import racepower as RP
    from backend.engine.wko5expr import dataset as D
    monkeypatch.setattr(D, "date_to_day", lambda d: 10)
    ds = _ds(flat_road_arrays())
    monkeypatch.setattr(RP, "_dataset", lambda: ds)
    # a road runner's grade model: no climbing windows, the curve is the prior only
    monkeypatch.setattr(A, "grade_models", lambda *a, **k: {"grade_re": GM.GradeRE(RE0),
                                                             "hike_speed": GM.fit_hike_speed([])})
    monkeypatch.setattr(A, "solo_hikes", lambda path=None: set())
    monkeypatch.setattr(BT, "class_model_flag", lambda path=None: False)
    monkeypatch.setattr(BT, "surface_split_flag", lambda path=None: False)
    monkeypatch.setattr(BT, "load", lambda path=None: None)
    for k in ("grade", "climb_cadence"):
        RP._cache.pop(k, None)
    yield RP
    for k in ("grade", "climb_cadence"):
        RP._cache.pop(k, None)


def test_flat_road_athlete_gets_both_answers(client, flat_road_athlete, monkeypatch):  # noqa: F811
    """The API side on a road runner: both answer 200 with something the page can draw."""
    monkeypatch.setattr(flat_road_athlete, "_grade_models", REAL_GRADE_MODELS)   # client's fake → the real one
    g = client.get("/api/v1/racepower/grade-model")
    assert g.status_code == 200, g.text
    gre = g.json()["grade_re"]
    assert gre["bins"] and all(b["re"] is not None for b in gre["bins"])
    c = client.get("/api/v1/racepower/cadence-check")
    assert c.status_code == 200, c.text
    cj = c.json()
    assert cj["verdict"] == "few" and cj["n_runs"] == 0 and len(cj["bins"]) == len(cj["seconds"])


def _together(fn, n=3):
    """Call fn() from n threads at once; their results."""
    out, errs = [None] * n, []
    go = threading.Barrier(n)

    def run(i):
        try:
            go.wait()
            out[i] = fn()
        except Exception as e:          # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs, errs
    return out


def test_concurrent_grade_model_callers_share_one_fit(flat_road_athlete, monkeypatch):
    """/plan, /grade-model and a second tab on a cold cache: one fit, not one each."""
    RP = flat_road_athlete
    calls = []

    def slow_fit(*a, **k):
        calls.append(1)
        time.sleep(0.4)
        return {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([])}
    monkeypatch.setattr(A, "grade_models", slow_fit)
    from backend.tests.test_racepower_v2 import fake_inputs
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    got = _together(RP._grade_models)
    assert len(calls) == 1, f"{len(calls)} fits for 3 concurrent callers"
    assert got[0] is got[1] is got[2]


def test_concurrent_cadence_checks_share_one_scan(client, flat_road_athlete, monkeypatch):  # noqa: F811
    calls = []

    def slow_scan(ds, today=None):
        calls.append(1)
        time.sleep(0.4)
        return list(np.zeros(50)), 0
    monkeypatch.setattr(A, "climb_cadence_seconds", slow_scan)
    got = _together(lambda: client.get("/api/v1/racepower/cadence-check").status_code)
    assert got == [200, 200, 200]
    assert len(calls) == 1, f"{len(calls)} scans for 3 concurrent requests"


# ---- the warm-up: the charts' fit and scan last, owner only -----------------------------

def _low_priority_run(monkeypatch, demo: bool):
    from types import SimpleNamespace

    from backend.api import activity_auto as AA
    from backend.api import racepower as RP
    from backend.api import wko5views
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    order = []
    if demo:
        monkeypatch.setenv("WKO5COACH_MODE", "demo")
    else:
        monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    monkeypatch.setattr(plan_auto, "busy", lambda: False)
    monkeypatch.setattr(wko5views, "_dataset", lambda: SimpleNamespace(today=20000.0))
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(AA, "job_for", lambda d: order.append("classify"))
    monkeypatch.setattr(RP, "_grade_models", lambda: order.append("grade"))
    monkeypatch.setattr(RP, "_climb_cadence", lambda: order.append("cadence"))
    wko5views._WARM["low_again"] = False
    wko5views._low_priority("test")
    return order


def test_warm_up_fits_the_charts_last_for_the_owner(monkeypatch):
    assert _low_priority_run(monkeypatch, demo=False) == ["classify", "grade", "cadence"]


def test_warm_up_skips_the_charts_in_the_demo(monkeypatch):
    assert _low_priority_run(monkeypatch, demo=True) == ["classify"]


def test_chart_warm_up_waits_for_the_classification_job(monkeypatch):
    """One heavy job at a time: the fit starts after the classification thread ends."""
    from types import SimpleNamespace

    from backend.api import racepower as RP
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    order = []
    t = threading.Thread(target=lambda: (time.sleep(0.3), order.append("classified")))
    t.start()
    monkeypatch.setattr(RP, "_grade_models", lambda: order.append("grade"))
    monkeypatch.setattr(RP, "_climb_cadence", lambda: order.append("cadence"))
    RP.warm_charts(SimpleNamespace(thread=t))
    assert order == ["classified", "grade", "cadence"]


# ---- the page --------------------------------------------------------------------------

NODE = shutil.which("node")


def _block() -> str:
    m = re.search(r"// ---- climbing charts \(SP-366\) ----.*?// ---- end climbing charts ----",
                  PAGE.read_text("utf-8"), re.S)
    assert m, "climbing charts block not found"
    return m.group(0)


def test_every_race_type_loads_the_charts():
    """Owner 2026-10-08: road races show them too (the road plan uses the grade curve)."""
    src = PAGE.read_text("utf-8")
    assert "chartRoad" not in src and 'id="gm-road"' not in src and 'id="cad-road"' not in src
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert "charts.road" not in cat
        for k in ("charts.neterr", "charts.retry"):
            assert cat.get(k), (loc, k)
    # the type switch asks for nothing: the charts are the athlete's, not the race's
    st = re.search(r"\nfunction setType\(t\) \{\n(.*?)\n\}\n", src, re.S)
    assert st and "renderGM" not in st.group(1) and "renderCad" not in st.group(1)


JS = r"""
const vm = require("vm");
const els = {};
const el = (id) => els[id] || (els[id] = { id, innerHTML: "", btn: null,
  querySelector() { return this.btn || (this.btn = { onclick: null }); } });
let calls = 0, answer = null;
const ctx = {
  API: "/api", GM: null, CAD: null, console,
  $: el, esc: (s) => String(s), T: (k) => "<" + k + ">", css: () => "", ok: (v) => v != null && isFinite(v),
  r3: String, n0: String, f1: String, hm: String, pct: String, axisStyle: () => ({}),
  chart: () => ({ setOption() {} }),
  j: () => { calls += 1; return answer(); },
};
vm.runInNewContext(process.argv[1], ctx);
const run = async () => {
  const out = {};
  // a gateway / dropped answer → the message with 重試; another error → its own text
  for (const [name, err] of [["drop", { message: "Failed to fetch" }], ["s502", { status: 502, message: "502 Bad Gateway" }],
                             ["s524", { status: 524, message: "524 <html>" }], ["s400", { status: 400, message: "沒有資料" }]]) {
    answer = () => Promise.reject(err);
    ctx.GM = null; await vm.runInNewContext("renderGM()", ctx);
    out[name] = el("gm-detail").innerHTML;
  }
  // one request in flight: toggling twice while it loads asks once
  calls = 0; let release;
  answer = () => new Promise((res) => { release = res; });
  ctx.GM = null;
  const a = vm.runInNewContext("renderCad()", ctx), b = vm.runInNewContext("renderCad()", ctx);
  out.inflight_calls = calls;
  release({ seconds: [0, 60], bins: [100, 105], threshold_spm: 130, valley_spm: null, verdict: "few", total_s: 60, n_runs: 0 });
  await a; await b;
  out.after = el("cad-detail").innerHTML.length > 0;
  // the retry button asks again
  calls = 0;
  answer = () => Promise.reject({ message: "Failed to fetch" });
  ctx.CAD = null; await vm.runInNewContext("renderCad()", ctx);
  answer = () => Promise.reject({ message: "Failed to fetch" });
  el("cad-detail").btn.onclick(); await new Promise((r) => setTimeout(r, 0));
  out.retry_calls = calls;
  console.log(JSON.stringify(out));
};
run().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_errors_retry_and_one_request_in_flight():
    r = subprocess.run([NODE, "-e", JS, _block()], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    for k in ("drop", "s502", "s524"):
        assert "<charts.neterr>" in out[k] and "<charts.retry>" in out[k], (k, out[k])
    assert "沒有資料" in out["s400"] and "charts.neterr" not in out["s400"]
    assert out["inflight_calls"] == 1 and out["after"]
    assert out["retry_calls"] == 2
