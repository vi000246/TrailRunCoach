"""SP-349 on the page: static/exportimport.js sends a platform export (a ZIP) up in pieces below the
server's chunk cap, sends a failed piece again, goes on where the server says it got to (a piece sent
twice, a reload), stops on a refusal; it lists what the server skipped by reason. 設定 › 資料同步
mounts it; every text is in both catalogs; nothing personal shows. exportimport.js runs in node
with a fake fetch (skipped without node); the rest reads the repo's pages and catalogs."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.sync import archive as AR

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "exportimport.js").read_text("utf-8")
NODE = shutil.which("node")

RUN = r"""
const vm = require("vm"), fs = require("fs");
const w = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window: w, setTimeout, Promise, JSON });
const E = w.ExportImport;
// a File: name, size, slice
const file = (name, size) => ({ name, size, slice: (a, b) => ({ a, b }) });
const ok = (j) => ({ ok: true, status: 200, json: async () => j });
const no = (status, code, params) => ({ ok: false, status, json: async () => ({ detail: { code, params: params || {} } }) });
const out = {};
(async () => {
  // a 25-byte file in pieces of 10: the second piece fails once (a 502), then goes
  let calls = [], got = 0, failed = false;
  const fake = async (url, o) => {
    calls.push(o.method + " " + url.replace("/api/v1/sync/upload/archive", "") + (o.body && o.body.a !== undefined ? ` [${o.body.a},${o.body.b})` : ""));
    if (o.method === "POST") return ok({ id: "abc", received: 0, limits: { chunk_bytes: 10 } });
    if (o.body.a === 10 && !failed) { failed = true; return no(502); }
    got = o.body.b;
    return ok({ id: "abc", received: got, state: got === 25 ? "queued" : "uploading" });
  };
  const prog = [];
  const r = await E.send(file("export.zip", 25), { fetch: fake, sleep: async () => {}, onProgress: (a, b) => prog.push(a) });
  out.pieces = { calls, state: r.state, prog };
  // the server already has 20 bytes (a reload): only the rest goes
  calls = [];
  const resume = async (url, o) => {
    calls.push(o.method + (o.body && o.body.a !== undefined ? ` [${o.body.a},${o.body.b})` : ""));
    if (o.method === "POST") return ok({ id: "abc", received: 20, limits: { chunk_bytes: 10 } });
    return ok({ id: "abc", received: o.body.b, state: "queued" });
  };
  await E.send(file("export.zip", 25), { fetch: resume, sleep: async () => {} });
  out.resume = calls;
  // a piece that landed twice: the server says where it is, the upload goes on from there
  calls = [];
  let once = false;
  const offset = async (url, o) => {
    calls.push(o.method + (o.body && o.body.a !== undefined ? ` [${o.body.a},${o.body.b})` : ""));
    if (o.method === "POST") return ok({ id: "abc", received: 0, limits: { chunk_bytes: 10 } });
    if (!once) { once = true; return no(409, "ARCHIVE_OFFSET", { received: 10 }); }
    return ok({ id: "abc", received: o.body.b, state: "uploading" });
  };
  await E.send(file("export.zip", 25), { fetch: offset, sleep: async () => {} });
  out.offset = calls;
  // the network keeps failing: it stops after `tries`, saying how far it got
  let n = 0;
  const down = async (url, o) => { n += 1; if (o.method === "POST") return ok({ id: "abc", received: 0, limits: { chunk_bytes: 10 } }); throw new Error("offline"); };
  out.down = { r: await E.send(file("export.zip", 25), { fetch: down, sleep: async () => {}, tries: 3 }), n };
  // a refusal: no retry
  out.busy = await E.send(file("export.zip", 25), { fetch: async () => no(409, "ARCHIVE_BUSY"), sleep: async () => {} });
  out.notzip = await E.send(file("export.zip", 25), { fetch: async (u, o) => o.method === "POST"
    ? ok({ id: "abc", received: 0, limits: { chunk_bytes: 100 } }) : no(400, "ARCHIVE_NOT_ZIP"), sleep: async () => {} });
  // the lists: per-file items and the skipped files by reason
  out.summary = E.summary({ items: [{ name: "a.fit", status: "failed", reason: "not_fit" },
    { name: "b.fit", status: "duplicate", reason: "same_file" }, { name: "m.fit", status: "skipped", reason: "not_activity" }],
    skipped_files: { not_activity_file: { n: 3, names: ["x.json", "y.json"] }, polar_json: { n: 1, names: ["t.json"] } } });
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def ran():
    if NODE is None:
        pytest.skip("node not installed")
    p = subprocess.run([NODE, "-e", RUN, str(STATIC / "exportimport.js")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_file_goes_up_in_pieces_and_a_failed_piece_is_sent_again(ran):
    assert ran["pieces"]["calls"] == ["POST ", "PUT /abc?offset=0 [0,10)", "PUT /abc?offset=10 [10,20)",
                                      "PUT /abc?offset=10 [10,20)", "PUT /abc?offset=20 [20,25)"]
    assert ran["pieces"]["state"] == "queued" and ran["pieces"]["prog"] == [0, 10, 20, 25]


def test_an_upload_goes_on_where_the_server_got_to(ran):
    assert ran["resume"] == ["POST", "PUT [20,25)"]                    # a reload: the same file again
    assert ran["offset"] == ["POST", "PUT [0,10)", "PUT [10,20)", "PUT [20,25)"]


def test_it_stops_after_a_few_tries_or_on_a_refusal(ran):
    assert ran["down"]["r"] == {"error": "upload_stopped", "id": "abc", "received": 0} and ran["down"]["n"] == 4
    assert ran["busy"]["error"] == "ARCHIVE_BUSY"
    assert ran["notzip"]["error"] == "ARCHIVE_NOT_ZIP" and ran["notzip"]["id"] == "abc"


def test_the_lists_hold_the_files_and_the_skipped_ones_by_reason(ran):
    lists = ran["summary"]["lists"]
    assert [i["name"] for i in lists["failed"]] == ["a.fit"] and [i["name"] for i in lists["duplicate"]] == ["b.fit"]
    assert [(i["name"], i["reason"]) for i in lists["skipped"]] == [
        ("m.fit", "not_activity"), ("x.json", "not_activity_file"), ("y.json", "not_activity_file"), ("t.json", "polar_json")]
    assert ran["summary"]["more"] == {"skipped": 1}


def test_the_defaults_match_the_server():
    m = re.search(r"const DEF = \{ max_archive_bytes: (\d+) \* 1073741824, chunk_bytes: (\d+) \* 1048576, "
                  r"exts: \[([^\]]*)\] \}", JS)
    assert m, "DEF not found"
    assert int(m.group(1)) * 2**30 == AR.MAX_ARCHIVE_BYTES and int(m.group(2)) * 2**20 == AR.CHUNK_BYTES
    assert re.findall(r'"([^"]+)"', m.group(3)) == list(AR.EXTS)
    assert set(AR.limits()) == {"max_archive_bytes", "chunk_bytes", "exts", "max_activity_files"}
    # the archive's own reasons the page names (the per-file ones are fitimport's)
    own = set(re.findall(r'"(\w+)"', re.search(r"const OWN = new Set\(\[([^\]]+)\]", JS).group(1)))
    assert own == {AR.NOT_ACTIVITY_FILE, AR.UNSUPPORTED, AR.POLAR_JSON, AR.APPLE_ROUTE, AR.TOO_DEEP, AR.TOO_MANY,
                   AR.BOMB, AR.NOT_ZIP, AR.TOO_MANY_ENTRIES}


def test_settings_mounts_it_in_the_sync_section_and_the_demo_lock_finds_it():
    s = (STATIC / "settings.html").read_text("utf-8")
    sync = s[s.index('<section id="sync">'):s.index('<section id="weightsync">')]
    assert '<div id="export-import"></div>' in sync and 'data-i18n="common.expimport.open"' in sync
    assert sync.index('id="src-upload"') < sync.index('id="src-export"')       # after 匯入 FIT 檔
    assert '<script src="/api/v1/static/exportimport.js"></script>' in s
    assert 'ExportImport.mount($("export-import"), { onDone:' in s
    assert JS.count('data-cap="upload.fit"') == 2                              # the button and its input
    core = (STATIC / "offline_core.js").read_text("utf-8")
    assert '"/api/v1/static/exportimport.js"' in core


def _keys_used() -> set:
    keys = {k for k in re.findall(r'\bT\("([\w.]+)"', JS) if not k.endswith(".")}
    keys |= {f"reason.{r}" for r in re.findall(r'"(\w+)"', re.search(r"const OWN = new Set\(\[([^\]]+)\]", JS).group(1))}
    keys |= {f"kind.{k}" for k in (AR.GARMIN, AR.POLAR, AR.APPLE_HEALTH)}
    keys |= {f"failed.{k}" for k in (AR.NOT_ZIP, AR.TOO_MANY_ENTRIES, "other")}
    known = re.search(r"const known = \[([^\]]+)\]", JS).group(1)
    keys |= {f"error.{k}" for k in re.findall(r'"(\w+)"', known)} | {"error.other", "error.ARCHIVE_NOTHING_TO_RETRY"}
    return keys


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_every_text_is_in_both_catalogs(loc):
    common = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
    missing = sorted(k for k in _keys_used() if not common.get(f"expimport.{k}"))
    assert missing == [], missing
    for k in re.findall(r'\bTF\("([\w.]+)"', JS):
        if not k.endswith("."):
            assert common.get(f"fitimport.{k}"), k
    assert "{gb}" in common["expimport.limits"] and "{done}" in common["expimport.working"]
    assert "{got}" in common["expimport.uploading"] and "{all}" in common["expimport.unfinished"]
    texts = " ".join(v for k, v in common.items() if k.startswith(("expimport.", "fitimport.")))
    assert not re.search(r"WKO5|擁有者|\bowner\b|docs/|\.md\b|yichlin", texts, re.I)
