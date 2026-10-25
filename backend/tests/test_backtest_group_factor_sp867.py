"""
SP-867: the calculator's 回測 page shows SP-724's 跟團係數 back-test (hike_capacity.group_factor of the
stored back-test, backtest.group_factor_backtest) instead of leaving it for an AI to read from the JSON:
the old group EP/h against capacity × 跟團係數 (n, median |error|, bias, 10–90 %), which one the 百岳
calculator uses, the factor, the altitude / pack residual slopes ± SE, the solo days' capacity error and
each group day. The page's own function (racepower.html groupFactorHtml) run in node with the page's
formatters.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def _fn_src() -> str:
    html = (STATIC / "racepower.html").read_text("utf-8")
    m = re.search(r"\nfunction groupFactorHtml\(G, st4, hd4\) \{.*?\n\}\n", html, re.S)
    assert m, "groupFactorHtml not found"
    return m.group(0)


def _helpers() -> str:
    html = (STATIC / "racepower.html").read_text("utf-8")
    out = []
    for name in ("ok", "pct1", "spct", "f1", "n0"):
        m = re.search(rf"\nconst {name} = .*?;\n", html)
        assert m, name
        out.append(m.group(0))
    return "".join(out)


def _render(G) -> str:
    js = r"""
const vm = require("vm");
const ctx = { console, isFinite, Math, JSON, String, Number, DATA: JSON.parse(process.argv[3]) };
vm.createContext(ctx);
vm.runInContext(`
const esc = (s) => String(s ?? "");
const TR = (k, p) => p ? k + JSON.stringify(p) : k;
const hm = (s) => "hm(" + s + ")";
` + process.argv[2] + process.argv[1] + `
const st4 = (s) => "<td>" + s.n + "|" + pct1(s.median_abs) + "|" + spct(s.bias) + "</td>";
OUT = groupFactorHtml(DATA, st4, "<th>hd</th>");`, ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, _fn_src(), _helpers(), json.dumps(G)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _bt(better=False):
    return {"n": 14, "trips": 14, "better": better,
            "eph": {"n": 14, "median_abs": 0.062, "bias": 0.01, "p10": -0.1, "p90": 0.12},
            "factor": {"n": 14, "median_abs": 0.137, "bias": -0.05, "p10": -0.2, "p90": 0.18},
            "factor_full": {"median": 1.37, "p25": 1.21, "p75": 1.47, "n": 14, "trips": 14},
            "residual": {"n": 14, "alt_pct_per_km": -6.2, "alt_se_pct": 3.5, "pack_pct_per_kg": None, "pack_se_pct": None},
            "solo_capacity_err": {"n": 3, "median_abs": 0.08, "bias": -0.04, "p10": None, "p90": None},
            "rows": [{"trip": "a", "date": "2026-08-14", "day": 2, "label": "百岳 3 天", "km": 12.3, "gain_m": 1100,
                      "z_mean": 3200, "pack_kg": 11.8, "t_act": 25000, "err_eph": 0.05, "err_factor": -0.12}]}


@needs_node
def test_the_two_methods_their_errors_and_which_is_used():
    h = _render(_bt(better=False))
    assert "bt.gf.title" in h and "bt.gf.eph" in h and "bt.gf.factor" in h
    assert "<td>14|6.2%|+1.0%</td>" in h and "<td>14|13.7%|−5.0%</td>" in h      # old 6.2 %, new 13.7 % (SP-724's demo)
    assert "bt.gf.not_used" in h and "bt.gf.used\"" not in h and 'class="no"' in h
    assert "bt.gf.used" in _render(_bt(better=True))


@needs_node
def test_the_factor_the_residual_slopes_and_the_solo_days():
    h = _render(_bt())
    assert 'bt.gf.factor_line{"m":"1.37","a":"1.21","b":"1.47","n":14,"trips":14}' in h
    assert "−6.2 % / 1000 m" in h and "(± 3.5)" in h                              # altitude slope ± SE
    assert "bt.gf.pack" in h and "bt.gf.no_spread" in h                             # pack: no spread → can't say
    assert 'bt.gf.solo{"n":3,"e":"8.0%","b":"−4.0%"}' in h
    # each group day
    assert "2026-08-14" in h and "D2" in h and "百岳 3 天" in h and "3,200 m" in h.replace("3200", "3,200") and "11.8 kg" in h
    assert "+5.0%" in h and "−12.0%" in h


@needs_node
def test_error_none_and_missing():
    assert _render(None) == ""
    assert "boom" in _render({"error": "ValueError: boom"})
    h = _render({"n": 0, "trips": 0, "better": None, "eph": {}, "factor": {}, "factor_full": None, "residual": None,
                 "solo_capacity_err": {"n": 0}, "rows": []})
    assert "bt.gf.none" in h and "bt.gf.factor_none" in h and "bt.gf.resid_few" in h and "bt.gf.solo_none" in h


def test_the_page_calls_it_and_the_texts_are_in_both_catalogs():
    html = (STATIC / "racepower.html").read_text("utf-8")
    assert "groupFactorHtml((R.hike_capacity || {}).group_factor" in html
    keys = set(re.findall(r'TR\("(bt\.gf\.[a-z_]+)"', _fn_src()))
    assert len(keys) >= 15
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        for k in keys | {"bt.gf.used", "bt.gf.not_used"}:
            assert cat.get(k), (loc, k)
