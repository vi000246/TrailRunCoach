/* TrailRunCoach static demo: the race calculator's engine in a Web Worker.
 *
 * static_shim.js starts this worker on the first race-calculator computation the export
 * did not precompute. It loads Pyodide from the CDN (the version pinned in
 * backend/demo/static_racepower.py, substituted below by export_static.py), unpacks the
 * bundled Python files (engine/racepower/calc.py and what it imports) and answers
 * POST /racepower/(plan|predict|course/event/<id>) with
 * static_racepower.handle() on data/racepower_ctx.json — the same functions the server runs.
 *
 * Messages in:  {type: "init", base, data}            base / data = the site's static/ and data/ URLs
 *               {type: "call", id, method, path, body}
 * Messages out: {type: "progress", text} · {type: "ready", ms} · {type: "fail", error}
 *               {type: "result", id, out}             out = handle()'s JSON text
 */
// a module worker: Pyodide ≥ 314 refuses classic workers
import { loadPyodide } from "__PYODIDE_URL__pyodide.mjs";

const PYODIDE_URL = "__PYODIDE_URL__";
const PACKAGES = __PACKAGES__;
const BUNDLE = "__BUNDLE__";
const CTX_FILE = "__CTX_FILE__";
const VERSION = "__VERSION__";
const MOD = "backend.demo.static_racepower";

let ready = null;
let handle = null;

const progress = (text) => self.postMessage({ type: "progress", text });

async function fetchOk(url, how) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return how === "text" ? r.text() : r.arrayBuffer();
}

async function boot(m) {
  const t0 = Date.now();
  progress("下載 Python 執行環境…");
  const py = await loadPyodide({ indexURL: PYODIDE_URL });
  progress("下載 numpy／pydantic…");
  const [zip, ctx] = await Promise.all([
    fetchOk(m.base + BUNDLE + "?v=" + VERSION, "bin"),
    fetchOk(m.data + CTX_FILE + "?v=" + VERSION, "text"),
    py.loadPackage(PACKAGES),
  ]);
  progress("載入計算引擎…");
  py.unpackArchive(zip, "zip", { extractDir: "/home/pyodide/trc" });
  py.runPython("import sys\nif '/home/pyodide/trc' not in sys.path: sys.path.insert(0, '/home/pyodide/trc')");
  const SR = py.pyimport(MOD);
  SR.load(ctx);
  // import the planner now: the first answer is then only the computation
  py.runPython("import backend.engine.racepower.calc, backend.engine.racepower.planner, backend.engine.racepower.fuel");
  handle = (method, path, body) => SR.handle(method, path, body);
  return Date.now() - t0;
}

self.onmessage = async (ev) => {
  const m = ev.data || {};
  if (m.type === "init") {
    if (!ready) ready = boot(m);
    try { self.postMessage({ type: "ready", ms: await ready }); }
    catch (e) { self.postMessage({ type: "fail", error: String((e && e.message) || e) }); }
    return;
  }
  if (m.type === "call") {
    let out;
    try {
      if (!ready) throw new Error("not initialised");
      await ready;
      out = handle(m.method, m.path, m.body == null ? null : String(m.body));
    } catch (e) {
      out = JSON.stringify({ status: 503, body: { detail: "load" }, error: String((e && e.message) || e) });
    }
    self.postMessage({ type: "result", id: m.id, out });
  }
};
