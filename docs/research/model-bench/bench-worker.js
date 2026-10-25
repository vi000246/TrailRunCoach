// Model benchmark worker (SP-760): Pyodide + the backend's own Python, running the
// single-activity review (backend/engine/workout_review.py) on the demo athlete.
// A module worker, so the page never blocks. Messages in: {id, cmd, ...args}; out: {id, ok, r | error}.
// The page can also import `handlers` and run them on its main thread (?thread=main): only for the
// desktop emulation, because DevTools CPU throttling slows a page's main thread but not its workers
// (the same reason as docs/research/fit-parse-bench/bench-worker.js).

export const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";

let py = null;
const now = () => performance.now();
const IN_WORKER = typeof WorkerGlobalScope !== "undefined" && self instanceof WorkerGlobalScope;

function resources(prefix) {
  // bytes over the network (transferSize 0 = served from the HTTP cache)
  const es = performance.getEntriesByType("resource").filter((e) => !prefix || e.name.startsWith(prefix));
  return {
    count: es.length,
    transfer_bytes: es.reduce((s, e) => s + (e.transferSize || 0), 0),
    body_bytes: es.reduce((s, e) => s + (e.encodedBodySize || 0), 0),
    decoded_bytes: es.reduce((s, e) => s + (e.decodedBodySize || 0), 0),
    files: es.map((e) => [e.name.slice(prefix ? prefix.length : 0), e.encodedBodySize || 0]),
  };
}

const jsHeap = () => (self.performance && performance.memory ? performance.memory.usedJSHeapSize : null);
const wasmHeap = () => {
  try {
    return py ? py._module.HEAP8.buffer.byteLength : null;
  } catch {
    return null;
  }
};

const OPFS_FILE = "model-bench-home.zip";

async function opfsWrite(name, bytes) {
  const root = await navigator.storage.getDirectory();
  const fh = await root.getFileHandle(name, { create: true });
  if (IN_WORKER && fh.createSyncAccessHandle) {
    const h = await fh.createSyncAccessHandle();
    h.truncate(0);
    h.write(bytes, { at: 0 });
    h.flush();
    h.close();
  } else {
    const w = await fh.createWritable();
    await w.write(bytes);
    await w.close();
  }
}

async function opfsRead(name) {
  const root = await navigator.storage.getDirectory();
  const fh = await root.getFileHandle(name);
  return new Uint8Array(await (await fh.getFile()).arrayBuffer());
}

async function saveHome() {
  // the tenant folder after a run (every cache this Pyodide wrote) -> OPFS, for a later page
  const t = now();
  py.runPython(`
import shutil
shutil.make_archive('/tmp/home-after', 'zip', '/home/pyodide/tenant')
`);
  const bytes = py.FS.readFile("/tmp/home-after.zip");
  await opfsWrite(OPFS_FILE, bytes);
  return { bytes: bytes.byteLength, ms: now() - t };
}

async function fetchBytes(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return new Uint8Array(await r.arrayBuffer());
}

async function init({ packages, bundle, home, base }) {
  // packages: Pyodide packages to load (numpy, sqlalchemy, …); bundle: "backend" | "backend-runtime";
  // home: "cold" | "fit" | "warm" (make_bundle.py)
  const out = { phases: {} };
  let t = now();
  const { loadPyodide } = await import(PYODIDE_URL + "pyodide.mjs");
  out.phases.import_mjs_ms = now() - t;
  t = now();
  py = await loadPyodide({ indexURL: PYODIDE_URL });
  out.phases.load_pyodide_ms = now() - t;
  out.wasm_after_boot = wasmHeap();
  t = now();
  if (packages && packages.length) await py.loadPackage(packages, { messageCallback: () => {} });
  out.phases.load_packages_ms = now() - t;
  out.net_pyodide = resources(PYODIDE_URL);
  t = now();
  // home "opfs": the folder a previous page left in OPFS (saveHome) — the device's own caches
  const [be, fd, hm, runner] = await Promise.all([
    fetchBytes(base + `fixtures/${bundle}.zip`),
    fetchBytes(base + "fixtures/fitdecode.zip"),
    home === "opfs" ? opfsRead(OPFS_FILE) : fetchBytes(base + `fixtures/home-${home}.zip`),
    fetch(base + "model_run.py").then((r) => r.text()),
  ]);
  out.phases.fetch_app_ms = now() - t;
  out.app_bytes = { backend: be.byteLength, fitdecode: fd.byteLength, home: hm.byteLength };
  t = now();
  py.unpackArchive(be, "zip", { extractDir: "/home/pyodide/app" });
  py.unpackArchive(fd, "zip", { extractDir: "/home/pyodide/site" });
  py.unpackArchive(hm, "zip", { extractDir: "/home/pyodide/tenant" });
  py.FS.writeFile("/home/pyodide/app/model_run.py", runner);
  py.runPython("import sys; sys.path[:0] = ['/home/pyodide/app', '/home/pyodide/site']");
  out.phases.unpack_ms = now() - t;
  out.python = py.runPython("import sys; sys.version.split()[0]");
  out.numpy = packages.includes("numpy") ? py.runPython("import numpy; numpy.__version__") : null;
  // what the server code relies on and Pyodide may not do: start a thread / a process pool, sqlite3
  out.caps = JSON.parse(py.runPython(`
import json
def _t(f):
    try:
        f(); return "ok"
    except BaseException as e:
        return f"{type(e).__name__}: {e}"[:200]
def _thread():
    import threading
    t = threading.Thread(target=lambda: None); t.start(); t.join()
def _pool():
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=2) as ex:
        ex.submit(abs, -1).result()
def _sqlite():
    import sqlite3
    sqlite3.connect(":memory:").execute("select 1").fetchone()
json.dumps({"thread": _t(_thread), "process_pool": _t(_pool), "sqlite3": _t(_sqlite)})
`));
  out.wasm_after_init = wasmHeap();
  out.js_heap = jsHeap();
  return out;
}

async function runModel({ who }) {
  const t = now();
  py.globals.set("_who", who || []);
  const text = py.runPython(`
import json, traceback, model_run
try:
    _r = model_run.run('/home/pyodide/tenant', tracemem=False, who=list(_who))
except BaseException as e:
    _r = {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-4000:]}
json.dumps(_r, ensure_ascii=False, default=str)
`);
  const r = JSON.parse(text);
  r.wall_ms = now() - t;
  r.wasm_heap_after = wasmHeap();
  r.js_heap_after = jsHeap();
  return r;
}

async function probe({ modules }) {
  // try importing each module; [name, ok, error]
  py.globals.set("_mods", modules);
  return JSON.parse(py.runPython(`
import importlib, json
_o = []
for m in _mods:
    try:
        importlib.import_module(m); _o.append([m, True, None])
    except BaseException as e:
        _o.append([m, False, f"{type(e).__name__}: {e}"[:300]])
json.dumps(_o)
`));
}

export const handlers = {
  init: async (a) => ({ result: await init(a) }),
  run: async (a) => ({ result: await runModel(a) }),
  probe: async (a) => ({ result: await probe(a) }),
  saveHome: async () => ({ result: await saveHome() }),
};

if (IN_WORKER) {
  self.onmessage = async (e) => {
    const { id, cmd, ...args } = e.data;
    try {
      const { result } = await handlers[cmd](args);
      self.postMessage({ id, ok: true, r: result });
    } catch (err) {
      self.postMessage({ id, ok: false, error: String(err && err.stack || err) });
    }
  };
}
