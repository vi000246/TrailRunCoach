"""SP-323 on the pages: static/fitimport.js picks FIT files or a whole folder, skips and lists what
is not FIT, fails a file over the size cap without sending it, uploads the rest in batches below
the server's caps (a failed batch fails only its own files; the last one says last=1 with what
the earlier ones added), and stops on a refusal that would refuse the rest too. 設定 › 資料同步
and the 活動列表 mount it; its texts are in the common catalog in both languages.
fitimport.js runs in node with a fake FormData / fetch (skipped without node); the rest reads
the repo's pages and catalogs."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.sync import upload as UP

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "fitimport.js").read_text("utf-8")
NODE = shutil.which("node")

RUN = r"""
const vm = require("vm"), fs = require("fs");
class FD { constructor() { this.parts = []; } append(k, v, n) { this.parts.push([k, typeof v === "string" ? v : n]); } }
const w = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window: w, FormData: FD });
const F = w.FitImport;
const file = (name, size, rel) => ({ name, size, webkitRelativePath: rel || "" });
const lim = { max_file_bytes: 500, max_files: 2, max_request_bytes: 1000, max_select: 10, exts: [".fit", ".fit.gz"] };
const out = {};
// a folder pick: paths inside the folder, a hidden file, a text file, one too big
const picked = [file("a.fit", 100, "Export/a.fit"), file("b.FIT", 100, "Export/2024/b.FIT"),
  file(".DS_Store", 6, "Export/.DS_Store"), file("notes.txt", 5, "Export/notes.txt"),
  file("big.fit", 600, "Export/big.fit"), file("c.fit.gz", 100, "Export/c.fit.gz"), file("d.fit", 450, "Export/d.fit"),
  file("e.fit", 380, "Export/e.fit")];
const p = F.plan(picked, lim);
out.plan = { send: p.send.map((b) => b.map((f) => f.webkitRelativePath)), skipped: p.skipped, tooBig: p.tooBig,
             total: p.total, overSelect: p.overSelect };
out.files = F.plan([file("x.fit", 10), file("y.txt", 1)], lim).send.map((b) => b.map((f) => f.name));
// the byte target per request (parsing time): a file bigger than it goes alone
out.bytes = F.plan([file("a.fit", 100), file("b.fit", 100), file("c.fit", 100), file("d.fit", 450)],
  { ...lim, max_files: 10, batch_bytes: 250 }).send.map((b) => b.map((f) => f.name));
(async () => {
  // batch 1 answers, batch 2 is a server error, batch 3 answers: only batch 2's files fail
  const sent = [];
  const answers = [
    { ok: true, status: 200, json: async () => ({ items: [
      { name: "Export/a.fit", status: "added", reason: null },
      { name: "Export/2024/b.FIT", status: "duplicate", reason: "same_activity", of: "coros" }] }) },
    { ok: false, status: 500, json: async () => ({}) },
    { ok: true, status: 200, json: async () => ({ items: [{ name: "Export/e.fit", status: "added", reason: null }] }) },
  ];
  const progress = [];
  const r = await F.upload(picked, { limits: lim, onProgress: (d, t) => progress.push([d, t]),
    fetch: async (url, o) => { sent.push({ url, method: o.method, parts: o.body.parts }); return answers.shift(); } });
  out.upload = { added: r.added, duplicate: r.duplicate, failed: r.failed, skipped: r.skipped, stopped: r.stopped,
    reasons: Object.fromEntries(r.items.map((i) => [i.name, i.status + ":" + i.reason])), sent, progress };
  // another tab is importing: the first refusal stops the rest
  let n = 0;
  const busy = await F.upload(picked, { limits: lim, fetch: async () => { n += 1;
    return { ok: false, status: 409, json: async () => ({ detail: { code: "UPLOAD_BUSY" } }) }; } });
  out.busy = { calls: n, failed: busy.failed, stopped: busy.stopped, reasons: [...new Set(busy.items.filter((i) => i.status === "failed" && i.reason !== "too_large").map((i) => i.reason))] };
  // a proxy gives up waiting (Cloudflare 524): those files may be in anyway, the rest goes on
  const slow = [{ ok: false, status: 524, json: async () => { throw new Error("html"); } },
    { ok: true, status: 200, json: async () => ({ items: [] }) }, { ok: true, status: 200, json: async () => ({ items: [] }) }];
  const t = await F.upload(picked, { limits: lim, fetch: async () => slow.shift() });
  out.timeout = { reasons: t.items.filter((i) => i.name === "Export/a.fit" || i.name === "Export/2024/b.FIT").map((i) => i.reason),
                  stopped: t.stopped, left: slow.length };
  // more FIT files than one pick takes: nothing is sent
  let m = 0;
  const over = await F.upload(picked, { limits: { ...lim, max_select: 2 }, fetch: async () => { m += 1; } });
  out.over = { calls: m, stopped: over.stopped };
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def ran():
    if NODE is None:
        pytest.skip("node not installed")
    r = subprocess.run([NODE, "-e", RUN, str(STATIC / "fitimport.js")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_folder_pick_skips_non_fit_and_batches_below_the_caps(ran):
    p = ran["plan"]
    assert p["skipped"] == ["Export/.DS_Store", "Export/notes.txt"]           # listed, never sent
    assert p["tooBig"] == ["Export/big.fit"]                                    # fails here, never sent
    # ≤ 2 files and ≤ 90 % of 1000 bytes per request
    assert p["send"] == [["Export/a.fit", "Export/2024/b.FIT"], ["Export/c.fit.gz", "Export/d.fit"], ["Export/e.fit"]]
    assert p["total"] == 5 and p["overSelect"] is False
    assert ran["files"] == [["x.fit"]]                                          # a plain file pick: its own name
    assert ran["bytes"] == [["a.fit", "b.fit"], ["c.fit"], ["d.fit"]]


def test_the_batches_go_one_by_one_and_a_failed_batch_fails_only_its_files(ran):
    u = ran["upload"]
    assert (u["added"], u["duplicate"], u["failed"], u["skipped"], u["stopped"]) == (2, 1, 3, 2, None)
    assert u["reasons"] == {
        "Export/.DS_Store": "skipped:not_fit", "Export/notes.txt": "skipped:not_fit", "Export/big.fit": "failed:too_large",
        "Export/a.fit": "added:null", "Export/2024/b.FIT": "duplicate:same_activity",
        "Export/c.fit.gz": "failed:http_500", "Export/d.fit": "failed:http_500", "Export/e.fit": "added:null"}
    assert [s["url"] for s in u["sent"]] == ["/api/v1/sync/upload/fit"] * 3 and {s["method"] for s in u["sent"]} == {"POST"}
    parts = [dict((k, v) for k, v in s["parts"] if k != "files") for s in u["sent"]]
    files = [[v for k, v in s["parts"] if k == "files"] for s in u["sent"]]
    assert files == [["Export/a.fit", "Export/2024/b.FIT"], ["Export/c.fit.gz", "Export/d.fit"], ["Export/e.fit"]]
    # the server runs the post-sync steps once: on the last batch, with what the earlier ones added
    assert parts == [{"last": "0", "earlier_added": "0"}, {"last": "0", "earlier_added": "1"},
                     {"last": "1", "earlier_added": "1"}]
    assert u["progress"] == [[2, 5], [4, 5], [5, 5]]


def test_a_busy_refusal_stops_and_too_many_files_send_nothing(ran):
    assert ran["busy"] == {"calls": 1, "failed": 6, "stopped": "busy", "reasons": ["busy"]}
    assert ran["over"] == {"calls": 0, "stopped": "too_many"}


def test_a_proxy_timeout_is_told_apart_and_the_rest_goes_on(ran):
    assert ran["timeout"] == {"reasons": ["timeout", "timeout"], "stopped": None, "left": 0}


def test_the_defaults_match_the_server():
    m = re.search(r"const DEF = \{ max_file_bytes: (\d+) \* 1048576, max_files: (\d+), "
                  r"max_request_bytes: (\d+) \* 1048576, batch_bytes: (\d+) \* 1048576,\s*"
                  r"max_select: (\d+), exts: \[([^\]]*)\]", JS)
    assert m, "DEF not found"
    assert int(m.group(1)) * 1048576 == UP.MAX_FILE_BYTES and int(m.group(2)) == UP.MAX_FILES
    assert int(m.group(3)) * 1048576 == UP.MAX_REQUEST_BYTES and int(m.group(4)) * 1048576 == UP.BATCH_BYTES
    assert int(m.group(5)) == UP.MAX_SELECT and re.findall(r'"([^"]+)"', m.group(6)) == list(UP.EXTS)
    lim = UP.limits()
    assert set(lim) == {"max_file_bytes", "max_track_bytes", "max_files", "max_request_bytes", "batch_bytes",
                        "max_select", "exts"}
    m = re.search(r"max_track_bytes: (\d+) \* 1048576 \}", JS)
    assert m and int(m.group(1)) * 1048576 == UP.MAX_TRACK_BYTES


def test_the_picker_offers_files_and_a_folder_and_the_demo_lock_finds_it():
    assert '<input type="file" accept="${ACCEPT}" multiple hidden data-fi="files" data-cap="upload.fit">' in JS
    assert ('<input type="file" accept="${ACCEPT}" multiple webkitdirectory hidden data-fi="dir" '
            'data-cap="upload.fit">') in JS
    accept = re.search(r'const ACCEPT = "([^"]+)"', JS).group(1)
    assert accept.split(",") == list(UP.EXTS)
    # shell.js locks `upload.fit` controls in the demo by this selector (FIT inputs: an accept
    # without .gpx — the course GPX inputs stay open); since SP-349 the picker takes TCX / GPX too,
    # so the inputs carry data-cap themselves (the lock reads every [data-cap])
    shell = (STATIC / "shell.js").read_text("utf-8")
    sel = re.search(r'\["upload\.fit", "([^"]+)"\]', shell).group(1)
    assert sel == "input[type=file][accept*='.fit' i]:not([accept*='.gpx' i])"
    assert ".fit" in accept.lower() and ".gpx" in accept.lower() and ".tcx" in accept.lower()
    assert 'for (const el of (root.querySelectorAll ? root.querySelectorAll("[data-cap]")' in shell
    assert JS.count('data-cap="upload.fit"') == 4                              # both buttons, both inputs
    # the folder button only where the browser can pick a folder (iOS 18.4+, Android Chrome 132+)
    assert '"webkitdirectory" in document.createElement("input")' in JS and '${folderOk ? "" : " hidden"}' in JS


def test_settings_and_the_activity_list_mount_it():
    s = (STATIC / "settings.html").read_text("utf-8")
    sync = s[s.index('<section id="sync">'):s.index('<section id="backup">')]
    assert '<div id="fit-import"></div>' in sync and 'data-i18n="common.fitimport.open"' in sync
    assert '<script src="/api/v1/static/fitimport.js"></script>' in s
    assert 'FitImport.mount($("fit-import"), { listLink: true' in s
    a = (STATIC / "activity.html").read_text("utf-8")
    assert '<script src="/api/v1/static/fitimport.js"></script>' in a
    head = a[a.index('<div class="head">'):a.index('<div class="filters"')]
    assert 'id="fi-open"' in head and 'data-cap="upload.fit"' in head and 'aria-controls="fi-panel"' in head
    assert '<section id="fi-panel" class="fi-panel" data-acts-only hidden' in head
    body = a[a.index("let fiMounted = false;"):a.index("// ---- start")]
    assert "FitImport.mount(p, { onDone:" in body and "loadList()" in body           # the list reloads
    assert 'upload: T("src_upload")' in a and 'upload: T("src_upload_long")' in a     # the origin badge


def _keys_used() -> set:
    keys = {k for k in re.findall(r'\bT\("([\w.]+)"', JS) if not k.endswith(".")}     # "list." + st: below
    reasons = {UP.NOT_FIT, UP.TOO_LARGE, UP.EMPTY, UP.UNREADABLE, UP.NOT_ACTIVITY, UP.ERROR, UP.SAME_FILE,
               UP.SAME_ACTIVITY, "same_activity_any", "busy", "forbidden", "too_large_batch", "network", "timeout",
               "http"}
    keys |= {f"reason.{r}" for r in reasons}
    keys |= {f"stopped.{s}" for s in ("too_many", "busy", "forbidden")}
    keys |= {f"list.{s}" for s in ("failed", "duplicate", "skipped")}
    return keys


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_every_text_is_in_both_catalogs(loc):
    common = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
    missing = sorted(k for k in _keys_used() if not common.get(f"fitimport.{k}"))
    assert missing == [], missing
    act = json.loads((STATIC / "i18n" / loc / "activity.json").read_text("utf-8"))
    assert act.get("src_upload") and act.get("src_upload_long")
    # placeholders the module fills
    assert "{mb}" in common["fitimport.limits"] and "{n}" in common["fitimport.limits"]
    assert "{done}" in common["fitimport.progress"] and "{total}" in common["fitimport.progress"]
    assert "{src}" in common["fitimport.reason.same_activity"]
