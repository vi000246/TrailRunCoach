"""
SP-757: the 心率與功率 chart's running totals go on the wire as each plotted point's increment (whole
numbers in units of the totals' last decimal) and without `x`; the server's `expand` and the viewer's
ACT.expand (charts.html, run under node) add them back up to exactly the totals `hrpower` computed,
so the brushed-range stats are the same as before. Synthetic data only.
"""
from __future__ import annotations

import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend.engine.panels import activity_charts as A
from backend.tests.test_activity_charts import _ds, _run, _steady

PAGE = Path(__file__).resolve().parents[1] / "static" / "charts.html"
NODE = shutil.which("node")


def _cases():
    t, hr, p, v = _steady(minutes=90, seed=3)
    yield "steady", _ds(_run(t, hr, p, v)), {}
    yield "steady-300", _ds(_run(t, hr, p, v)), {"points": 300}
    t2 = np.arange(0, 1201, 1.0)
    yield "no-power", _ds(_run(t2, np.full(len(t2), 150.0))), {}
    t3 = np.concatenate([np.arange(0, 600, 1.0), np.arange(900, 1500, 1.0)])
    yield "gap", _ds(_run(t3, np.full(len(t3), 140.0), np.full(len(t3), 210.0))), {}
    # long and hard: big NP sums (4 decimals), 4 h at 1-s samples → step 10
    rng = np.random.default_rng(7)
    t4 = np.arange(0, 4 * 3600 + 1, 1.0)
    yield "long", _ds(_run(t4, 150 + rng.normal(0, 5, len(t4)), 380 + rng.normal(0, 60, len(t4)),
                           12 + rng.normal(0, 1, len(t4)))), {}


CASES = list(_cases())


@pytest.mark.parametrize("name,ds,kw", CASES, ids=[c[0] for c in CASES])
def test_wire_then_expand_gives_back_the_very_same_totals(name, ds, kw):
    res = A.hrpower(ds, ds.workouts[0], **kw)
    w = A.wire(res)
    assert "x" not in w and "cum" not in w
    assert all(v is None or all(isinstance(n, int) for n in v) for v in w["inc"].values())
    back = A.expand(json.loads(json.dumps(w)))
    assert back["x"] == res["x"]
    assert back["cum"] == res["cum"]                      # exact, not approx
    K = len(res["x"])
    for ka, kb in ((0, K), (K // 4, K // 2), (3, K - 7)):
        assert A.range_stats(back, ka, kb) == A.range_stats(res, ka, kb)


def test_render_sends_the_increments_and_is_smaller():
    t, hr, p, v = _steady(minutes=120, seed=5)
    ds = _ds(_run(t, hr, p, v))
    out = A.render(ds, ds.workouts[0], {"chart": "hrpower", "title": "t"})
    assert out["kind"] == "act_hrpower" and "inc" in out and "cum" not in out and "x" not in out
    old = {**out, **A.hrpower(ds, ds.workouts[0])}
    old.pop("inc"), old.pop("inc_dec")
    gz = lambda o: len(gzip.compress(json.dumps(o, ensure_ascii=False).encode(), 6))   # noqa: E731
    assert gz(out) < 0.6 * gz(old)


def test_expand_leaves_an_old_answer_alone():
    t, hr, p, v = _steady(minutes=30)
    ds = _ds(_run(t, hr, p, v))
    res = A.hrpower(ds, ds.workouts[0])
    assert A.expand(res) is res
    assert A.wire({"empty": "x"}) == {"empty": "x"}


# ---------------------------------------------------------------------------- the viewer (node)

def _block() -> str:
    m = re.search(r"// ---- act_hrpower totals \(SP-757\) ----.*?// ---- end act_hrpower totals ----",
                  PAGE.read_text(encoding="utf-8"), re.S)
    assert m, "act_hrpower totals block not found"
    s = re.search(r"// stats of plotted buckets ka … kb−1 \(activity_charts.range_stats\)\nACT\.stats = .*?\n};\n",
                  PAGE.read_text(encoding="utf-8"), re.S)
    assert s, "ACT.stats not found"
    return m.group(0) + "\n" + s.group(0)


def _node(expr: str, **data):
    js = r"""
const vm = require("vm");
const ctx = { DATA: JSON.parse(require("fs").readFileSync(0, "utf8")), ACT: {} };
vm.runInNewContext(process.argv[1] + "\nOUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, _block(), expr], input=json.dumps(data), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("name,ds,kw", CASES, ids=[c[0] for c in CASES])
def test_the_viewer_adds_up_the_same_totals_and_stats(name, ds, kw):
    res = A.hrpower(ds, ds.workouts[0], **kw)
    w = json.loads(json.dumps(A.wire(res)))
    got = _node("(() => { const e = ACT.expand(DATA.w); return {x: e.x, cum: e.cum, inc: e.inc === undefined,"
                " s: [ACT.stats(e, 0, e.x.length), ACT.stats(e, 3, e.x.length - 7)],"
                " o: [ACT.stats(DATA.r, 0, DATA.r.x.length), ACT.stats(DATA.r, 3, DATA.r.x.length - 7)]}; })()",
                w=w, r=json.loads(json.dumps(res)))
    assert got["x"] == res["x"]
    assert got["cum"] == json.loads(json.dumps(res["cum"]))
    assert got["inc"] is True
    assert got["s"] == got["o"]                          # the stats tiles show the same numbers


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_viewer_uses_a_cached_old_answer_as_it_is():
    t, hr, p, v = _steady(minutes=20)
    ds = _ds(_run(t, hr, p, v))
    res = json.loads(json.dumps(A.hrpower(ds, ds.workouts[0])))
    assert _node("ACT.expand(DATA.r) === DATA.r", r=res) is True
