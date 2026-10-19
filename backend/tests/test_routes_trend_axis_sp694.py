"""SP-694: the route page's trend chart — its time axis read like a time of day (8:20 … 23:20, the
user runs in the evening) and ended on an empty 23:20. Now the axis says it is how long the stretch
took, its ticks are durations in words on whole seconds / minutes, and a ? explains 經過時間 vs 移動時間.

The empty 23:20: ECharts' own nice numbers (scale: true) stepped by 100 s and rounded the axis end past
the slowest run (21:40 = 1300 s → 1400 s = 23:20) — an axis end, not a run."""
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
PAGE = (STATIC / "routes.html").read_text("utf-8")


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "routes.json").read_text("utf-8"))


def _fn(name: str) -> str:
    return re.search(rf"function {name}\(.*?\n\}}", PAGE, re.S).group(0)


def _run(js: str):
    zh = _cat("zh-TW")
    pre = ("const ZH = " + json.dumps(zh, ensure_ascii=False) + ";"
           "const T = (k, p) => ZH[k].replace(/\\{(\\w+)\\}/g, (_, x) => p[x]);"
           + re.search(r"const DUR_STEPS = \[[^\]]*\];", PAGE).group(0) + _fn("dur") + _fn("durAxis"))
    r = subprocess.run([NODE, "-e", pre + js], capture_output=True, timeout=30, check=True)
    return json.loads(r.stdout.decode("utf-8"))


@needs_node
def test_durations_in_words():
    out = _run("console.log(JSON.stringify([dur(1400), dur(1300), dur(600), dur(45), dur(3600), dur(3900), dur(3725), dur(0)]))")
    assert out == ["23 分 20 秒", "21 分 40 秒", "10 分", "45 秒", "1 小時", "1 小時 05 分", "1 小時 02 分 5 秒", "0 秒"]


@needs_node
def test_the_axis_steps_whole_minutes_and_ends_near_the_data():
    # the screenshot's runs: 8:30 … 21:40
    a = _run("console.log(JSON.stringify(durAxis([510, 600, 700, 1300])))")
    assert a["interval"] % 60 == 0 and a["min"] % a["interval"] == 0 and a["max"] % a["interval"] == 0
    assert a["min"] <= 510 and a["max"] >= 1300 and a["max"] - 1300 < a["interval"] and 510 - a["min"] < a["interval"]
    assert (a["max"] - a["min"]) / a["interval"] <= 6
    # a short climb: steps of seconds; one effort: still a range
    b = _run("console.log(JSON.stringify([durAxis([95, 120, 131]), durAxis([600])]))")
    assert b[0]["interval"] in (10, 15, 20) and b[0]["min"] <= 95 and b[0]["max"] >= 131
    assert b[1]["max"] > b[1]["min"]


def test_the_chart_uses_them_and_names_the_axis():
    f = PAGE[PAGE.index("function drawTrend"):]
    f = f[:f.index("\n}\n")]
    assert 'mode === "pace" ? pace : dur' in f and "durAxis(" in f and "...yRange" in f
    assert 'T("trend.y_time")' in f and 'T("time")' not in f
    assert "esc(dur(e[d.time_key]))" in f                       # the tooltip says a duration too
    for loc, word in (("zh-TW", "花了多久"), ("en", "Time taken")):
        assert word in _cat(loc)["trend.y_time"]


def test_a_help_explains_elapsed_and_moving_time():
    assert 'qBtn(T(tk === "elapsed_s" ? "t.elapsed_tip" : "t.moving_tip")' in PAGE
    assert ".qpop {" in PAGE and 'closest("button.q[data-tip]")' in PAGE
    for loc in ("zh-TW", "en"):
        c = _cat(loc)
        assert c["t.elapsed_tip"] and c["t.moving_tip"] and c["t.tip_label"] and c["dur.h"] and c["dur.m"] and c["dur.s"]
    zh = _cat("zh-TW")
    assert "不是幾點" in zh["t.elapsed_tip"] and "停下來" in zh["t.elapsed_tip"] and "移動時間" in zh["t.elapsed_tip"]
    assert not any(re.search(r"[一-鿿]", v) for k, v in _cat("en").items() if isinstance(v, str)
                   and k in ("t.elapsed_tip", "t.moving_tip", "trend.y_time", "dur.h", "dur.m", "dur.s"))
