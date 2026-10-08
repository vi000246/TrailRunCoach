"""The 課表 page asks GET /suggestions once per load (SP-362): the floating box
(static/suggestions.js AppSuggestions.load) and the page's sugCount (static/schedule.html)
share one request; `fresh` asks again. Runs suggestions.js in node with a fake DOM / fetch."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")

JS = r"""
const vm = require("vm"), fs = require("fs");
const calls = [];
// any element: every method is a no-op returning another element, properties can be set
const el = () => new Proxy({}, { get: (t, k) => (k in t ? t[k] : k === "then" ? undefined : () => el()),
                                 set: (t, k, v) => { t[k] = v; return true; } });
const w = { dispatchEvent() {}, addEventListener() {} };
const ctx = { window: w, CustomEvent: function (t) { this.type = t; }, setTimeout() {},
  document: { body: null, head: el(), createElement: el, addEventListener() {} },
  fetch: (u) => { calls.push(u); return Promise.resolve({ ok: true, json: () => Promise.resolve({ suggestions: [{ id: "s1", type: "test" }] }) }); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  const A = w.AppSuggestions;
  const [a, b] = await Promise.all([A.load(false), A.load(false)]);   // the box + the page's first sugCount
  await A.load(false);
  const n1 = calls.length;
  await A.load(true);                                                   // after a change: asks again
  console.log(JSON.stringify({ n1, n2: calls.length, same: a === b, rows: a.length }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_box_and_page_share_one_request():
    r = subprocess.run([NODE, "-e", JS, str(STATIC / "suggestions.js")], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got == {"n1": 1, "n2": 2, "same": True, "rows": 1}


def test_schedule_page_uses_the_shared_request():
    html = (STATIC / "schedule.html").read_text(encoding="utf-8")
    body = html[html.index("async function sugCount"):]
    body = body[:body.index("const sugFits")]
    assert "box.load(again)" in body and 'addEventListener("suggestions:changed", () => sugCount(true))' in html
