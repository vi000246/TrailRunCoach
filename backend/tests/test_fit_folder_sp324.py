"""SP-324: desktop Chrome / Edge remember a FIT folder (File System Access API,
`showDirectoryPicker`; the handle kept in IndexedDB) and, each time the site is opened, import the
FIT files in it that were not imported before, through SP-323's upload (static/fitimport.js ->
POST /api/v1/sync/upload/fit, the server's hash and same-activity dedup).

static/fitfolder.js runs in node (vm) with fitimport.js, a fake FormData / fetch / server, an
in-memory store in place of IndexedDB and fake directory handles in place of the browser's
(`showDirectoryPicker`, `queryPermission` / `requestPermission`, `values()`, `getFile()` are mocks);
the rest reads the repo's pages and catalogs. The browser's own permission prompt is not tested
here (a person's click in a real Chrome). Synthetic only; the node tests are skipped without node."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.sync import upload as UP

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")

RUN = r"""
const vm = require("vm"), fs = require("fs");
class FD { constructor() { this.parts = []; } append(k, v, n) { this.parts.push([k, typeof v === "string" ? v : n]); } }
const ctx = { console, setTimeout, clearTimeout, Promise, FormData: FD, Date, JSON };
ctx.window = ctx;
const cat = JSON.parse(fs.readFileSync(process.argv[1] + "/i18n/zh-TW/common.json", "utf8"));
ctx.I18N = { t: (k, p) => String(cat[k.replace(/^common\./, "")] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/fitimport.js", "utf8"), ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/fitfolder.js", "utf8"), ctx);
const F = ctx.FitFolder, U = ctx.FitImport, t = ctx.I18N.t;

// IndexedDB stand-in: the same get / set / del as FitFolder.idbStore
function mem() { const m = new Map(); return { m, get: async (k) => m.get(k), set: async (k, v) => { m.set(k, v); }, del: async (k) => { m.delete(k); } }; }
// the browser's handles: a file has name / size / lastModified; a folder lists its children and
// answers the read permission ("granted" | "prompt" | "denied"; a request turns it into `onAsk`)
const file = (name, size, mtime) => ({ kind: "file", name, getFile: async () => ({ name, size, lastModified: mtime }) });
function dir(name, kids, o = {}) {
  const d = { kind: "directory", name, kids, perm: o.perm || "granted", asked: 0, gone: false,
    queryPermission: async (opt) => { if (o.queryThrows) throw new Error("boom"); return d.perm; },
    requestPermission: async (opt) => { d.asked += 1; d.perm = o.onAsk || "granted"; return d.perm; },
    isSameEntry: async (other) => other === d || (o.same && o.same === other),
    values: async function* () {
      if (d.gone) { const e = new Error("gone"); e.name = "NotFoundError"; throw e; }
      if (d.perm !== "granted") { const e = new Error("no"); e.name = "NotAllowedError"; throw e; }
      for (const k of d.kids) yield k;
    } };
  return d;
}
// the server: every name sent is answered from `answer` (default added); the calls are kept
function server(answer = {}) {
  const s = { calls: [] };
  s.fetch = async (url, o) => {
    const names = o.body.parts.filter(([k]) => k === "files").map(([, v]) => v);
    s.calls.push(names);
    if (answer.__status) return { ok: false, status: answer.__status, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => ({ items: names.map((n) => {
      const a = answer[n] || "added";
      const [status, reason] = a.split(":");
      return { name: n, status, reason: reason || null };
    }) }) };
  };
  s.upload = (files, opt) => U.upload(files, { ...opt, fetch: s.fetch });
  return s;
}
const sent = (s) => s.calls.flat().sort();
let OUT;
(async () => {
__BODY__
})().then(() => console.log(JSON.stringify(OUT === undefined ? null : OUT)), (e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def run(body: str):
    js = RUN.replace("__BODY__", body)
    r = subprocess.run([NODE, "-e", js, str(STATIC)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env={**os.environ, "TZ": "Asia/Taipei"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


JS = (STATIC / "fitfolder.js").read_text("utf-8") if (STATIC / "fitfolder.js").exists() else ""

CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0"
SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36"


# ---------------------------------------------------------------------------- feature detection

@needs_node
def test_only_desktop_chrome_and_edge_get_the_folder():
    out = run(f"""
const pick = async () => {{}};
const w = (ua, has, mobile) => ({{ navigator: {{ userAgent: ua, userAgentData: mobile == null ? undefined : {{ mobile }} }},
  ...(has ? {{ showDirectoryPicker: pick }} : {{}}) }});
OUT = {{
  chrome: F.supported(w({json.dumps(CHROME)}, true, false)),
  edge: F.supported(w({json.dumps(EDGE)}, true, false)),
  firefox: F.supported(w({json.dumps(FIREFOX)}, false)),
  safari: F.supported(w({json.dumps(SAFARI)}, false)),
  iphone: F.supported(w({json.dumps(IPHONE)}, false)),
  // Android Chrome 132+ has the API, but the ticket is the desktop: no remembered folder on a phone
  android: F.supported(w({json.dumps(ANDROID)}, true, true)),
  android_ua_only: F.supported(w({json.dumps(ANDROID)}, true)),
  nothing: F.supported(null),
}};
""")
    assert out == {"chrome": True, "edge": True, "firefox": False, "safari": False, "iphone": False,
                   "android": False, "android_ua_only": False, "nothing": False}


@needs_node
def test_an_unsupported_browser_never_shows_the_settings_block():
    out = run(f"""
const el = {{ hidden: false, innerHTML: "untouched", classList: {{ add() {{ throw new Error("drawn"); }} }} }};
const firefox = {{ navigator: {{ userAgent: {json.dumps(FIREFOX)} }} }};
const r = F.mount(el, {{ win: firefox, store: mem() }});
// a desktop Chrome whose account may not upload FIT files (upload.fit): hidden as well
const el2 = {{ hidden: false, innerHTML: "untouched" }};
const chrome = {{ navigator: {{ userAgent: {json.dumps(CHROME)} }}, showDirectoryPicker: async () => {{}},
  AppShell: {{ can: (c) => c !== "upload.fit", session: {{}} }} }};
const r2 = F.mount(el2, {{ win: chrome, store: mem() }});
OUT = {{ hidden: el.hidden, html: el.innerHTML, ret: r === null, hidden2: el2.hidden, html2: el2.innerHTML, ret2: r2 === null }};
""")
    assert out == {"hidden": True, "html": "untouched", "ret": True, "hidden2": True, "html2": "untouched", "ret2": True}


def test_the_shell_loads_it_only_where_the_api_exists_and_it_is_kept_offline():
    shell = (STATIC / "shell.js").read_text("utf-8")
    i = shell.index('"/api/v1/static/fitfolder.js"')
    block = shell[shell.rindex("\n\n", 0, i):shell.index("\n\n", i)]
    assert 'typeof window.showDirectoryPicker === "function"' in block and 'can("upload.fit")' in block
    assert 'dataset.auto = "1"' in block
    # never in the demo (SP-558 widened the guard to signed-out visitors too)
    assert shell.index('"/api/v1/static/fitfolder.js"') > shell.index("if (DEMO || PUBLIC) return;")
    core = (STATIC / "offline_core.js").read_text("utf-8")
    app_files = core[core.index("const APP_FILES = ["):core.index("];", core.index("const APP_FILES = ["))]
    assert '"/api/v1/static/fitfolder.js"' in app_files


# ---------------------------------------------------------------------------- only new files

@needs_node
def test_the_first_check_imports_every_fit_file_and_the_next_only_new_ones():
    out = run("""
const store = mem(), s = server();
const sub = dir("2024", [file("b.FIT", 200, 2000), file("notes.txt", 5, 2100)]);
const root = dir("Export", [file("a.fit", 100, 1000), file("._a.fit", 4, 1000), file(".DS_Store", 6, 1000), sub,
  dir(".Trash", [file("x.fit", 50, 500)]), file("c.fit.gz", 80, 3000)]);
await store.set("dir:owner", { handle: root, name: "Export", at: 1 });
const r1 = await F.run({ store, acct: "owner", upload: s.upload, now: 10 });
const first = sent(s);
s.calls.length = 0;
const r2 = await F.run({ store, acct: "owner", upload: s.upload, now: 20 });
const second = s.calls.length;
// a new run in the folder, and an old one written again (another size / time): only those two
root.kids.push(file("d.fit", 300, 4000));
sub.kids[0] = file("b.FIT", 210, 4100);
const r3 = await F.run({ store, acct: "owner", upload: s.upload, now: 30 });
OUT = { first, r1: [r1.state, r1.found, r1.fresh, r1.added], second, r2: [r2.state, r2.found, r2.fresh, r2.added],
        third: sent(s), r3: [r3.state, r3.fresh, r3.added], known: Object.keys((await store.get("seen:owner")).keys).length,
        last: (await store.get("last:owner")).at };
""")
    # paths inside the folder; hidden files (macOS ._ copies, .DS_Store) and hidden folders skipped
    assert out["first"] == ["Export/2024/b.FIT", "Export/a.fit", "Export/c.fit.gz"]
    assert out["r1"] == ["ok", 3, 3, 3]
    assert out["second"] == 0 and out["r2"] == ["ok", 3, 0, 0]                  # nothing uploaded again
    assert out["third"] == ["Export/2024/b.FIT", "Export/d.fit"] and out["r3"] == ["ok", 2, 2]
    assert out["known"] == 4 and out["last"] == 30


@needs_node
def test_what_failed_for_a_moment_is_tried_again_and_a_broken_file_is_not():
    out = run("""
const store = mem();
const root = dir("FIT", [file("ok.fit", 100, 1), file("dup.fit", 100, 2), file("bad.fit", 100, 3), file("err.fit", 100, 4),
  file("route.fit", 100, 5)]);
await store.set("dir:owner", { handle: root, name: "FIT", at: 1 });
const s1 = server({ "FIT/dup.fit": "duplicate:same_activity", "FIT/bad.fit": "failed:unreadable",
  "FIT/err.fit": "failed:error", "FIT/route.fit": "failed:not_activity" });
const r1 = await F.run({ store, acct: "owner", upload: s1.upload, now: 10 });
const s2 = server();
const r2 = await F.run({ store, acct: "owner", upload: s2.upload, now: 20 });
// the whole request refused by a proxy: none of them is remembered
const store3 = mem();
await store3.set("dir:owner", { handle: dir("FIT", [file("a.fit", 100, 1)]), name: "FIT", at: 1 });
const s3 = server({ __status: 524 });
const r3 = await F.run({ store: store3, acct: "owner", upload: s3.upload, now: 10 });
const s4 = server();
await F.run({ store: store3, acct: "owner", upload: s4.upload, now: 20 });
OUT = { r1: [r1.added, r1.duplicate, r1.failed, r1.retry], again: sent(s2), r2: r2.retry,
        r3: [r3.failed, r3.retry, r3.failures.map((f) => f.reason)], again3: sent(s4) };
""")
    assert out["r1"] == [1, 1, 3, 1]
    assert out["again"] == ["FIT/err.fit"]               # a server error: tried next time; unreadable / not a run: not
    assert out["r2"] == 0
    assert out["r3"] == [1, 1, ["timeout"]] and out["again3"] == ["FIT/a.fit"]


@needs_node
def test_more_new_files_than_one_pick_takes_go_over_several_opens():
    out = run("""
const store = mem(), s = server();
const kids = []; for (let i = 0; i < 7; i++) kids.push(file(`r${i}.fit`, 10, 1000 + i));
await store.set("dir:owner", { handle: dir("FIT", kids), name: "FIT", at: 1 });
const r1 = await F.run({ store, acct: "owner", upload: s.upload, limits: { max_select: 3 }, now: 10 });
const first = sent(s); s.calls.length = 0;
const r2 = await F.run({ store, acct: "owner", upload: s.upload, limits: { max_select: 3 }, now: 20 });
OUT = { first, r1: [r1.fresh, r1.sent, r1.pending], second: sent(s), r2: [r2.fresh, r2.sent, r2.pending] };
""")
    # the newest first; the older ones on the next open
    assert out["first"] == ["FIT/r4.fit", "FIT/r5.fit", "FIT/r6.fit"] and out["r1"] == [7, 3, 4]
    assert out["second"] == ["FIT/r1.fit", "FIT/r2.fit", "FIT/r3.fit"] and out["r2"] == [4, 3, 1]


@needs_node
def test_each_account_has_its_own_folder_and_list():
    out = run("""
const store = mem(), s = server();
await store.set("dir:u1", { handle: dir("A", [file("a.fit", 10, 1)]), name: "A", at: 1 });
const r = await F.run({ store, acct: "u2", upload: s.upload, now: 10 });
OUT = { r: r.state, calls: s.calls.length,
  acct: [F.account(null), F.account({ user: null }), F.account({ user: { id: 7 } }), F.account({ user: { id: "x" } })] };
""")
    # another account signed in on the same browser never uploads the first one's folder
    assert out == {"r": "none", "calls": 0, "acct": ["owner", "owner", "u7", "ux"]}


@needs_node
def test_opening_pages_one_after_another_checks_once_per_ten_minutes():
    out = run("""
const store = mem(), s = server();
const root = dir("FIT", [file("a.fit", 10, 1)]);
await store.set("dir:owner", { handle: root, name: "FIT", at: 1 });
const r1 = await F.auto({ store, acct: "owner", upload: s.upload, now: 0 });
root.kids.push(file("b.fit", 10, 2));
const r2 = await F.auto({ store, acct: "owner", upload: s.upload, now: 5 * 60e3 });
const r3 = await F.auto({ store, acct: "owner", upload: s.upload, now: 11 * 60e3 });
OUT = { states: [r1.state, r2.state, r3.state], calls: s.calls };
""")
    assert out["states"] == ["ok", "recent", "ok"]
    assert out["calls"] == [["FIT/a.fit"], ["FIT/b.fit"]]


# ---------------------------------------------------------------------------- the permission lapses

@needs_node
def test_a_lapsed_permission_asks_again_and_never_throws_or_uploads():
    out = run("""
const store = mem(), s = server();
const root = dir("Garmin", [file("a.fit", 10, 1)], { perm: "prompt" });
await store.set("dir:owner", { handle: root, name: "Garmin", at: 1 });
// on page open: no click, so no prompt — say so and stop
const r1 = await F.run({ store, acct: "owner", upload: s.upload, now: 10 });
const a1 = await F.auto({ store, acct: "owner", upload: s.upload, now: 20 });
const st1 = await F.status(store, "owner");
const html = F.html(st1, t);
const badge = F.badge(r1, t);
// 重新授權 (a click): the browser asks, the person allows, the import runs
const r2 = await F.run({ store, acct: "owner", upload: s.upload, ask: true, now: 30 });
// the person says no: still the same calm state
const store2 = mem();
const no = dir("Garmin", [file("a.fit", 10, 1)], { perm: "prompt", onAsk: "denied" });
await store2.set("dir:owner", { handle: no, name: "Garmin", at: 1 });
const r3 = await F.run({ store: store2, acct: "owner", upload: s.upload, ask: true, now: 40 });
// a browser whose permission query itself throws
const store3 = mem();
await store3.set("dir:owner", { handle: dir("X", [], { queryThrows: true }), name: "X", at: 1 });
const r4 = await F.run({ store: store3, acct: "owner", upload: s.upload, now: 50 });
OUT = { r1: [r1.state, r1.name], a1: a1.state, calls_before: 1, st1: st1.state,
  reauth: html.includes('data-ff="reauth"'), change: html.includes('data-ff="change"'), forget: html.includes('data-ff="forget"'),
  note: html.includes(t("common.fitfolder.permission_note")),
  badge: [badge.kind, badge.act, badge.text],
  r2: [r2.state, r2.added], asked: root.asked, calls: s.calls, r3: [r3.state, no.asked], r4: r4.state };
""")
    assert out["r1"] == ["permission", "Garmin"] and out["a1"] == "permission" and out["st1"] == "permission"
    assert out["reauth"] and out["change"] and out["forget"] and out["note"]
    assert out["badge"][0] == "warn" and out["badge"][1] == "reauth" and "Garmin" in out["badge"][2]
    assert out["r2"] == ["ok", 1] and out["asked"] == 1 and out["calls"] == [["Garmin/a.fit"]]
    assert out["r3"] == ["permission", 1]
    assert out["r4"] == "permission"


@needs_node
def test_a_folder_moved_away_or_a_permission_lost_mid_scan_is_told_not_thrown():
    out = run("""
const store = mem(), s = server();
const root = dir("FIT", [file("a.fit", 10, 1)]);
root.gone = true;
await store.set("dir:owner", { handle: root, name: "FIT", at: 1 });
const r1 = await F.run({ store, acct: "owner", upload: s.upload, now: 10 });
const st = await F.status(store, "owner");
const html = F.html(st, t);
const b = F.badge(r1, t);
// a file that cannot be read any more (the permission went while listing)
const store2 = mem();
const bad = { kind: "file", name: "b.fit", getFile: async () => { const e = new Error("x"); e.name = "NotAllowedError"; throw e; } };
await store2.set("dir:owner", { handle: dir("FIT", [bad]), name: "FIT", at: 1 });
const r2 = await F.run({ store: store2, acct: "owner", upload: s.upload, now: 10 });
OUT = { r1: r1.state, st: st.state, change: html.includes('data-ff="change"'), reauth: html.includes('data-ff="reauth"'),
  note: html.includes(t("common.fitfolder.missing_note")), badge: [b.kind, b.act], r2: r2.state, calls: s.calls.length };
""")
    assert out == {"r1": "missing", "st": "missing", "change": True, "reauth": False, "note": True,
                   "badge": ["warn", "settings"], "r2": "permission", "calls": 0}


# ---------------------------------------------------------------------------- choose, change, forget

@needs_node
def test_choose_change_and_forget_the_folder():
    out = run(f"""
const store = mem(), s = server();
const a = dir("Export", [file("a.fit", 10, 1)]);
const picks = [a];
const win = {{ navigator: {{ userAgent: {json.dumps(CHROME)} }}, showDirectoryPicker: async (o) => {{
  win.opts = o; const p = picks.shift(); if (p instanceof Error) throw p; return p; }} }};
const p1 = await F.pick({{ win, store, acct: "owner" }});
const st0 = await F.status(store, "owner");
await F.run({{ store, acct: "owner", upload: s.upload, now: 10 }});
const st1 = await F.status(store, "owner");
const html1 = F.html(st1, t);
// the picker closed without a choice: nothing changes
const abort = new Error("closed"); abort.name = "AbortError"; picks.push(abort);
const p2 = await F.pick({{ win, store, acct: "owner" }});
const after_abort = (await store.get("dir:owner")) !== undefined && (await store.get("seen:owner")) !== undefined;
// the same folder again (to re-allow it): what was imported stays known
const again = dir("Export", [file("a.fit", 10, 1)], {{ same: a }}); picks.push(again);
const p3 = await F.pick({{ win, store, acct: "owner" }});
const known3 = Object.keys((await store.get("seen:owner")).keys).length;
// another folder: a new list
picks.push(dir("Other", [file("z.fit", 10, 1)]));
const p4 = await F.pick({{ win, store, acct: "owner" }});
const seen4 = await store.get("seen:owner");
const st4 = await F.status(store, "owner");
await F.forget(store, "owner");
const st5 = await F.status(store, "owner");
const html5 = F.html(st5, t);
OUT = {{ opts: win.opts, p1: p1.state, st0: [st0.state, st0.name], st1: [st1.state, st1.known, st1.last && st1.last.added],
  granted: [html1.includes('data-ff="check"'), html1.includes('data-ff="change"'), html1.includes('data-ff="forget"'),
            html1.includes('data-ff="reauth"'), html1.includes("Export")],
  p2: p2.state, after_abort,
  p3: [p3.state, p3.same], known3, p4: [p4.state, p4.same, p4.name], seen4: seen4 ? Object.keys(seen4.keys).length : 0,
  st4: st4.name, st5: st5.state, keys: [...store.m.keys()], pick5: html5.includes('data-ff="pick"') }};
""")
    assert out["opts"] == {"id": "trc-fit-folder", "mode": "read"}
    assert out["p1"] == "picked" and out["st0"] == ["granted", "Export"]
    assert out["st1"] == ["granted", 1, 1]
    assert out["granted"] == [True, True, True, False, True]
    assert out["p2"] == "cancelled" and out["after_abort"] is True
    assert out["p3"] == ["picked", True] and out["known3"] == 1
    assert out["p4"] == ["picked", False, "Other"] and out["seen4"] == 0 and out["st4"] == "Other"
    assert out["st5"] == "none" and out["keys"] == [] and out["pick5"] is True


@needs_node
def test_the_badge_after_an_open():
    out = run("""
const ok = F.badge({ state: "ok", name: "FIT", added: 2, duplicate: 1, failed: 0, fresh: 3 }, t);
const fail = F.badge({ state: "ok", name: "FIT", added: 0, duplicate: 0, failed: 2, fresh: 2 }, t);
OUT = { ok: [ok.kind, ok.text, ok.act], fail: [fail.kind, fail.act, fail.text],
  quiet: [F.badge({ state: "ok", name: "FIT", added: 0, duplicate: 0, failed: 0, fresh: 0 }, t),
          F.badge({ state: "recent" }, t), F.badge({ state: "none" }, t), F.badge({ state: "busy" }, t)] };
""")
    assert out["ok"][0] == "ok" and "2" in out["ok"][1] and "FIT" in out["ok"][1] and out["ok"][2] is None
    assert out["fail"][0] == "warn" and out["fail"][1] == "settings" and "2" in out["fail"][2]
    assert out["quiet"] == [None, None, None, None]                  # nothing new: nothing shown


# ---------------------------------------------------------------------------- the page, the texts

def test_settings_puts_it_under_the_fit_import():
    s = (STATIC / "settings.html").read_text("utf-8")
    sync = s[s.index('<section id="sync">'):s.index('<section id="backup"')]
    up = sync[sync.index('<div class="src" id="src-upload">'):]
    assert '<div id="fit-folder" hidden></div>' in up and up.index('id="fit-import"') < up.index('id="fit-folder"')
    assert '<script src="/api/v1/static/fitfolder.js"></script>' in s
    assert 'FitFolder.mount($("fit-folder")' in s


def test_the_scan_takes_what_the_server_takes():
    m = re.search(r"const DEF = \{ max_select: (\d+), exts: \[([^\]]*)\]", JS)
    assert m, "DEF not found"
    assert int(m.group(1)) == UP.MAX_SELECT and re.findall(r'"([^"]+)"', m.group(2)) == list(UP.EXTS)


def _keys_used() -> set:
    keys = set(re.findall(r'\bT\("([\w.]+)"', JS)) | set(re.findall(r'\bT0\(t, "([\w.]+)"', JS))
    keys |= set(re.findall(r'\bbtn\("([\w.]+)"', JS))                               # the buttons
    keys |= {f"st.{s}" for s in ("granted", "permission", "missing", "error")}
    return {k for k in keys if not k.endswith(".")}


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_every_text_is_in_both_catalogs(loc):
    assert JS, "fitfolder.js missing"
    common = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
    used = _keys_used()
    assert len(used) > 15
    missing = sorted(k for k in used if not common.get(f"fitfolder.{k}"))
    assert missing == [], missing
    assert "{name}" in common["fitfolder.badge.added"] and "{n}" in common["fitfolder.badge.added"]
    assert "{name}" in common["fitfolder.badge.permission"]
    assert "{done}" in common["fitfolder.running"] and "{total}" in common["fitfolder.running"]
