// FIT parse benchmark worker (SP-313). A module worker, so the page never blocks.
// Messages in:  {id, cmd, ...args}; out: {id, ok, r | error} and {progress}.
// Parsers: "js" = Garmin FIT JavaScript SDK, "py" = Pyodide + fitdecode.
// The page can also import `handlers` and run them on its main thread (?thread=main): only for
// the desktop emulation, because DevTools CPU throttling slows a page's main thread but not its
// workers ("Operation is only supported for pages, not workers").

export const FITSDK_URL = "https://cdn.jsdelivr.net/npm/@garmin/fitsdk@21.218.0/+esm";
export const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const SEMI_TO_DEG = 180 / 2 ** 31;
const FIELDS = ["ts", "hr", "power", "speed", "dist", "alt", "lat", "lon"];
const OPFS_DIR = "fit-parse-bench";

let sdk = null;
let py = null;
let pyParse = null;

const now = () => performance.now();
const IN_WORKER = typeof WorkerGlobalScope !== "undefined" && self instanceof WorkerGlobalScope;
let progressFn = (p) => self.postMessage({ progress: p });
export function setProgress(fn) {
  progressFn = fn;
}
const postProgress = (p) => progressFn(p);

function resources(prefix) {
  // bytes over the network (transferSize 0 = served from the HTTP cache)
  const es = performance.getEntriesByType("resource").filter((e) => !prefix || e.name.startsWith(prefix));
  return {
    count: es.length,
    transfer_bytes: es.reduce((s, e) => s + (e.transferSize || 0), 0),
    body_bytes: es.reduce((s, e) => s + (e.encodedBodySize || 0), 0),
    decoded_bytes: es.reduce((s, e) => s + (e.decodedBodySize || 0), 0),
  };
}

function jsHeap() {
  const m = self.performance && performance.memory;
  return m ? m.usedJSHeapSize : null;
}

function wasmHeap() {
  try {
    return py ? py._module.HEAP8.buffer.byteLength : null;
  } catch {
    return null;
  }
}

async function initJs() {
  const t0 = now();
  sdk = await import(FITSDK_URL);
  return { ms: now() - t0, net: resources("https://cdn.jsdelivr.net/npm/@garmin") };
}

const PY_SRC = `
import io, time, fitdecode
from array import array

SEMI = 180.0 / 2 ** 31
NAN = float("nan")
last_cols = None

def _f(x):
    return NAN if x is None else float(x)

def parse(buf, keep):
    t0 = time.perf_counter()
    data = buf.to_bytes()
    t1 = time.perf_counter()
    cols = {k: array("d") for k in ("ts", "hr", "power", "speed", "dist", "alt", "lat", "lon")}
    frames = records = 0
    with fitdecode.FitReader(io.BytesIO(data)) as fr:
        for fm in fr:
            frames += 1
            if not isinstance(fm, fitdecode.FitDataMessage) or fm.name != "record":
                continue
            records += 1
            if not keep:
                continue
            v = {f.name: f.value for f in fm.fields}
            ts = v.get("timestamp")
            cols["ts"].append(NAN if ts is None else ts.timestamp())
            cols["hr"].append(_f(v.get("heart_rate")))
            cols["power"].append(_f(v.get("power")))
            sp = v.get("enhanced_speed")
            cols["speed"].append(_f(sp if sp is not None else v.get("speed")))
            cols["dist"].append(_f(v.get("distance")))
            al = v.get("enhanced_altitude")
            cols["alt"].append(_f(al if al is not None else v.get("altitude")))
            la, lo = v.get("position_lat"), v.get("position_long")
            cols["lat"].append(NAN if la is None else la * SEMI)
            cols["lon"].append(NAN if lo is None else lo * SEMI)
    t2 = time.perf_counter()
    global last_cols
    last_cols = cols if keep else None
    return {"copy_s": t1 - t0, "parse_s": t2 - t1, "frames": frames, "records": records}
`;

async function initPy({ fitdecodeZipUrl }) {
  const t0 = now();
  const { loadPyodide } = await import(PYODIDE_URL + "pyodide.mjs");
  const t1 = now();
  py = await loadPyodide({ indexURL: PYODIDE_URL });
  const t2 = now();
  const zip = await (await fetch(fitdecodeZipUrl)).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/home/pyodide/fitlib" });
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide/fitlib')");
  py.runPython(PY_SRC);
  pyParse = py.globals.get("parse");
  const t3 = now();
  return {
    ms: t3 - t0,
    phases: { import_mjs_ms: t1 - t0, load_pyodide_ms: t2 - t1, fitdecode_ms: t3 - t2 },
    net: resources(PYODIDE_URL),
    wasm_heap: wasmHeap(),
    python: py.runPython("import sys; sys.version.split()[0]"),
  };
}

function parseJs(bytes, keep) {
  const t0 = now();
  const stream = sdk.Stream.fromArrayBuffer(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  const decoder = new sdk.Decoder(stream);
  const { messages, errors } = decoder.read();
  const t1 = now();
  const recs = messages.recordMesgs || [];
  let frames = 0;
  for (const k in messages) frames += messages[k].length;
  let cols = null;
  if (keep) {
    const n = recs.length;
    cols = Object.fromEntries(FIELDS.map((k) => [k, new Float64Array(n)]));
    const num = (x) => (x === undefined || x === null ? NaN : Number(x));
    for (let i = 0; i < n; i++) {
      const r = recs[i];
      cols.ts[i] = r.timestamp instanceof Date ? r.timestamp.getTime() / 1000 : NaN;
      cols.hr[i] = num(r.heartRate);
      cols.power[i] = num(r.power);
      cols.speed[i] = num(r.enhancedSpeed ?? r.speed);
      cols.dist[i] = num(r.distance);
      cols.alt[i] = num(r.enhancedAltitude ?? r.altitude);
      cols.lat[i] = r.positionLat === undefined ? NaN : r.positionLat * SEMI_TO_DEG;
      cols.lon[i] = r.positionLong === undefined ? NaN : r.positionLong * SEMI_TO_DEG;
    }
  }
  const t2 = now();
  return { parse_ms: t1 - t0, extract_ms: t2 - t1, records: recs.length, frames, errors: errors.length, cols };
}

function parsePy(bytes, keep) {
  const t0 = now();
  const res = pyParse(bytes, keep);
  const t1 = now();
  const r = res.toJs({ dict_converter: Object.fromEntries });
  res.destroy();
  let cols = null;
  if (keep) {
    const pc = py.globals.get("last_cols");
    cols = {};
    for (const k of FIELDS) {
      const arr = pc.get(k);
      const b = arr.getBuffer("f64");
      cols[k] = new Float64Array(b.data);   // copy out of the wasm heap
      b.release();
      arr.destroy();
    }
    pc.destroy();
    py.runPython("last_cols = None");
  }
  const t2 = now();
  return {
    parse_ms: t1 - t0, extract_ms: t2 - t1, py_copy_ms: r.copy_s * 1000, py_parse_ms: r.parse_s * 1000,
    records: r.records, frames: r.frames, errors: 0, cols,
  };
}

function parse(parser, bytes, keep) {
  return parser === "js" ? parseJs(bytes, keep) : parsePy(bytes, keep);
}

async function benchFile({ parser, url, repeat = 3, keep = true }) {
  const tf = now();
  const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer());
  const fetch_ms = now() - tf;
  const runs = [];
  let cols = null;
  for (let i = 0; i < repeat; i++) {
    const r = parse(parser, bytes, keep);           // every run extracts the fields (the real work)
    if (i === 0) cols = r.cols;
    delete r.cols;
    r.js_heap = jsHeap();
    r.wasm_heap = wasmHeap();
    runs.push(r);
  }
  const transfer = cols ? Object.values(cols).map((a) => a.buffer) : [];
  return { result: { size: bytes.byteLength, fetch_ms, runs, cols }, transfer };
}

// ---- full run: N files through OPFS -------------------------------------------------------------

async function opfsRoot() {
  if (!(self.navigator && navigator.storage && navigator.storage.getDirectory)) return null;
  try {
    return await navigator.storage.getDirectory();
  } catch {
    return null;
  }
}

async function writeFile(dir, name, bytes) {
  const fh = await dir.getFileHandle(name, { create: true });
  if (fh.createSyncAccessHandle) {
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

async function readFile(dir, name) {
  const fh = await dir.getFileHandle(name);
  if (fh.createSyncAccessHandle) {
    const h = await fh.createSyncAccessHandle();
    const buf = new Uint8Array(h.getSize());
    h.read(buf, { at: 0 });
    h.close();
    return buf;
  }
  return new Uint8Array(await (await fh.getFile()).arrayBuffer());
}

async function fullRun({ parser, n, files, base }) {
  // files: [{file, size}] (the demo year); file i of N is files[i % files.length]
  const out = { parser, n, opfs: false };
  const tDl = now();
  const uniq = [];
  for (let i = 0; i < Math.min(n, files.length); i++) {
    uniq.push(new Uint8Array(await (await fetch(base + files[i].file)).arrayBuffer()));
    if (i % 20 === 0) postProgress({ phase: "download", i, n: Math.min(n, files.length) });
  }
  out.download_ms = now() - tDl;
  const total = Array.from({ length: n }, (_, i) => uniq[i % uniq.length].byteLength).reduce((a, b) => a + b, 0);
  out.total_bytes = total;

  const root = await opfsRoot();
  let dir = null;
  if (root) {
    try {
      try {
        await root.removeEntry(OPFS_DIR, { recursive: true });
      } catch { /* not there */ }
      dir = await root.getDirectoryHandle(OPFS_DIR, { create: true });
      const tw = now();
      for (let i = 0; i < n; i++) {
        await writeFile(dir, `f${String(i).padStart(4, "0")}.fit`, uniq[i % uniq.length]);
        if (i % 50 === 0) postProgress({ phase: "opfs-write", i, n });
      }
      out.opfs_write_ms = now() - tw;
      out.opfs = true;
      out.opfs_api = (await dir.getFileHandle("f0000.fit")).createSyncAccessHandle ? "sync-access-handle" : "writable";
    } catch (e) {
      out.opfs_error = String(e);
      dir = null;
    }
  }
  const t0 = now();
  let readMs = 0, parseMs = 0, records = 0, peakJs = jsHeap(), peakWasm = wasmHeap(), slowest = 0;
  for (let i = 0; i < n; i++) {
    let bytes;
    if (dir) {
      const tr = now();
      bytes = await readFile(dir, `f${String(i).padStart(4, "0")}.fit`);
      readMs += now() - tr;
    } else {
      bytes = uniq[i % uniq.length];
    }
    const r = parse(parser, bytes, true);           // extract the fields, then drop them
    const ms = r.parse_ms + r.extract_ms;
    parseMs += ms;
    slowest = Math.max(slowest, ms);
    records += r.records;
    const h = jsHeap();
    if (h !== null) peakJs = Math.max(peakJs, h);
    const w = wasmHeap();
    if (w !== null) peakWasm = Math.max(peakWasm, w);
    if (i % 10 === 0) postProgress({ phase: "parse", i, n, parse_ms: parseMs });
  }
  out.loop_ms = now() - t0;
  out.opfs_read_ms = dir ? readMs : null;
  out.parse_ms = parseMs;
  out.slowest_file_ms = slowest;
  out.records = records;
  out.peak_js_heap = peakJs;
  out.peak_wasm_heap = peakWasm;
  if (root && dir) {
    const tc = now();
    await root.removeEntry(OPFS_DIR, { recursive: true });
    out.opfs_cleanup_ms = now() - tc;
  }
  return { result: out, transfer: [] };
}

export const handlers = {
  async init({ parser, fitdecodeZipUrl }) {
    const r = parser === "js" ? await initJs() : await initPy({ fitdecodeZipUrl });
    r.js_heap = jsHeap();
    return { result: r, transfer: [] };
  },
  benchFile,
  fullRun,
  async env() {
    const root = await opfsRoot();
    return {
      result: {
        opfs: !!root,
        sync_access_handle: typeof FileSystemFileHandle !== "undefined" && "createSyncAccessHandle" in FileSystemFileHandle.prototype,
        performance_memory: !!(self.performance && performance.memory),
      },
      transfer: [],
    };
  },
};

if (IN_WORKER) self.onmessage = async (e) => {
  const { id, cmd, ...args } = e.data;
  try {
    const { result, transfer } = await handlers[cmd](args);
    self.postMessage({ id, ok: true, r: result }, transfer);
  } catch (err) {
    self.postMessage({ id, ok: false, error: String((err && err.stack) || err) });
  }
};
