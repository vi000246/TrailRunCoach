"""
總覽 › 體能・疲勞・狀況 (SP-122; overview.html): the range is 7 / 42 / 90 days, default 42,
remembered per browser; the chart asks /overview/pmc for one day before the range (yesterday's
CTL colours the first bar). The page's PMC block runs under node with stubs.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "overview.html"
NODE = shutil.which("node")


def _block() -> str:
    m = re.search(r"// ---------------- PMC, the last 7 / 42 / 90 days.*?(?=function renderPmc\(\))",
                  PAGE.read_text(encoding="utf-8"), re.S)
    assert m, "PMC block not found"
    return m.group(0)


def _run(saved, clicks=()) -> dict:
    js = r"""
const vm = require("vm");
const store = {}, urls = [], els = {};
const el = (id) => els[id] || (els[id] = { id, innerHTML: "", attrs: {}, handlers: {},
  setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(t, f) { this.handlers[t] = f; } });
const saved = JSON.parse(process.argv[2]);
if (saved !== null) store.pmcDays = saved;
const ctx = {
  $: el, esc: (s) => String(s), STATUS: { today: "2026-10-05" }, API: "/api/v1/overview",
  window: {}, I18N: { t: (k, p) => k + (p && p.n != null ? ":" + p.n : "") },
  ls: { get: (k, d) => (k in store ? store[k] : d), set: (k, v) => { store[k] = v; } },
  j: async (u) => { urls.push(u); return { series: [] }; }, renderPmc: () => {}, fail: () => () => {},
};
ctx.window.I18N = ctx.I18N;
vm.runInNewContext(process.argv[1], ctx);
(async () => {
  await vm.runInNewContext("loadPmc()", ctx);
  for (const n of JSON.parse(process.argv[3])) {
    els["pmc-days"].handlers.click({ target: { closest: () => ({ dataset: { n: String(n) } }) } });
    await new Promise((r) => setTimeout(r, 0));
  }
  console.log(JSON.stringify({ urls, store, seg: els["pmc-days"].innerHTML, aria: els["pmc-days"].attrs["aria-label"] }));
})();
"""
    r = subprocess.run([NODE, "-e", js, _block(), json.dumps(saved), json.dumps(list(clicks))],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_default_is_42_days():
    out = _run(None)
    assert out["urls"] == ["/api/v1/overview/pmc?begin=2026-08-24&end=2026-10-05"]      # 42 days + yesterday
    assert 'data-n="42" class="on" aria-pressed="true"' in out["seg"]
    assert [m for m in re.findall(r'data-n="(\d+)"', out["seg"])] == ["7", "42", "90"]
    assert "overview.pmc.days:7" in out["seg"] and out["aria"] == "overview.pmc.range"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_a_pick_is_remembered_and_reloads():
    out = _run(None, clicks=[7, 90])
    assert out["urls"][1:] == ["/api/v1/overview/pmc?begin=2026-09-28&end=2026-10-05",
                               "/api/v1/overview/pmc?begin=2026-07-07&end=2026-10-05"]
    assert out["store"]["pmcDays"] == 90 and 'data-n="90" class="on"' in out["seg"]
    assert _run(7)["urls"] == ["/api/v1/overview/pmc?begin=2026-09-28&end=2026-10-05"]
    assert _run(13)["urls"][0].startswith("/api/v1/overview/pmc?begin=2026-08-24")  # an old / odd value → 42


def test_texts_follow_the_range():
    page = PAGE.read_text(encoding="utf-8")
    assert 'id="pmc-days"' in page and "overview.pmc.sub" not in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "overview.json").read_text(encoding="utf-8"))
        assert "{n}" in cat["pmc.days"] and "{n}" in cat["pmc.tip"] and "{n}" in cat["pmc.aria"], loc
        assert cat["pmc.range"] and "pmc.sub" not in cat
