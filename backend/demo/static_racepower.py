"""
The static demo's race calculator: the real engine (engine/racepower/calc.py) in the
browser with Pyodide, on an athlete context exported as JSON.

The race calculator page asks the server for every result (POST /racepower/plan,
/predict, /course/event/{id}, /export/csv). The static site has no server, so
static_shim.js hands those requests to a Web Worker (static_racepower_worker.js) that
loads Pyodide from the CDN (pinned PYODIDE_VERSION), unpacks the Python files this
module bundles (BUNDLE: calc.py and every backend module it imports, no FastAPI /
SQLAlchemy / sqlite) and calls `handle()` below on the context in data/CTX_FILE.

The split, so both paths run one function:
  - export time, inside the demo app: `collect()` asks api/racepower.LiveContext (the
    same memoised Dataset / DB / file reads the API uses) for everything the athlete's
    data decides — derive() inputs, grade models, back-test flags, the heat status, the
    trail HR model, the body profile, the plan events and their stored GPX tracks, the
    calibration entries in effect — and `encode()`s it (tagged JSON: dataclasses,
    numpy values, tuples, dates and non-str dict keys come back as they were);
  - in the browser: `StaticContext` serves the same answers from that copy and calc.py
    computes. Weather is never fetched (the page's own inputs or the exported data).

backend/tests/test_static_racepower.py checks that calc on the exported context
equals the live API for road / trail-GPX / multi-day 百岳 / goal inputs.

Module level: stdlib only (it runs in Pyodide); the export-time parts import lazily.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import importlib
import json
import math
import sys
from types import SimpleNamespace
from typing import Optional

from backend.i18n import N_, _

CTX_FILE = "racepower_ctx.json"                 # under data/
# under data/: the names of the race calculator's saved answers (GET /racepower/... and the
# precomputed POSTs), so static_shim.js asks the engine without fetching a missing file first
SAVED_FILE = "racepower_saved.json"
BUNDLE = "py/trc_racepower.zip"                 # under static/
WORKER = "trc_racepower_worker.js"              # under static/
PYODIDE_VERSION = "314.0.7"
PYODIDE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
PYODIDE_PACKAGES = ("numpy", "pydantic")
CTX_VERSION = 1

# the requests the worker answers (static_shim.js RACEPOWER_POSTS is the same)
ROUTE_PREFIX = "/api/v1/racepower/"
ENGINE_FAIL_MSG = N_("示範版算不出這組輸入，請換一組數字再試")

# what the browser has: numpy / pydantic come from Pyodide; these must never be needed
FORBIDDEN_IMPORTS = ("fastapi", "starlette", "sqlalchemy", "aiosqlite", "httpx", "sqlite3", "uvicorn", "anyio")
ALLOWED_THIRD_PARTY = ("numpy", "pydantic", "pydantic_core", "typing_extensions", "annotated_types",
                       "typing_inspection")


# --------------------------------------------------------------------------- codec
def _cls_path(o) -> str:
    c = type(o)
    return f"{c.__module__}:{c.__qualname__}"


def encode(o):
    """o → JSON-able, losslessly for what the context holds (decode() inverts it)."""
    if o is None or isinstance(o, (bool, str)):
        return o
    try:
        import numpy as np
    except ImportError:                     # pragma: no cover
        np = None
    if np is not None and isinstance(o, np.generic):
        return {"__np": o.dtype.str, "v": encode(o.item())}
    if np is not None and isinstance(o, np.ndarray):
        return {"__nd": o.dtype.str, "shape": list(o.shape), "v": [encode(x) for x in o.ravel().tolist()]}
    if isinstance(o, int):
        return o
    if isinstance(o, float):
        if math.isfinite(o):
            return o
        return {"__f": "nan" if o != o else ("inf" if o > 0 else "-inf")}
    if isinstance(o, list):
        return [encode(x) for x in o]
    if isinstance(o, tuple):
        return {"__t": [encode(x) for x in o]}
    if isinstance(o, (set, frozenset)):
        return {"__set": [encode(x) for x in sorted(o, key=repr)]}
    if isinstance(o, dict):
        if all(isinstance(k, str) and not k.startswith("__") for k in o):
            return {k: encode(v) for k, v in o.items()}
        return {"__d": [[encode(k), encode(v)] for k, v in o.items()]}
    if isinstance(o, dt.datetime):
        return {"__dt": o.isoformat()}
    if isinstance(o, dt.date):
        return {"__date": o.isoformat()}
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        state = dict(o.__dict__) if hasattr(o, "__dict__") else \
            {f.name: getattr(o, f.name) for f in dataclasses.fields(o)}
        return {"__dc": _cls_path(o), "f": {k: encode(v) for k, v in state.items()}}
    if isinstance(o, SimpleNamespace):
        return {"__ns": {k: encode(v) for k, v in vars(o).items()}}
    raise TypeError(f"cannot export {type(o).__module__}.{type(o).__qualname__} in the race calculator context")


def _cls(path: str):
    mod, _, qual = path.partition(":")
    obj = importlib.import_module(mod)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return obj


def decode(o):
    if isinstance(o, list):
        return [decode(x) for x in o]
    if not isinstance(o, dict):
        return o
    if len(o) <= 3:
        if "__f" in o:
            return float(o["__f"])
        if "__t" in o:
            return tuple(decode(x) for x in o["__t"])
        if "__set" in o:
            return set(decode(x) for x in o["__set"])
        if "__d" in o:
            return {decode(k): decode(v) for k, v in o["__d"]}
        if "__date" in o:
            return dt.date.fromisoformat(o["__date"])
        if "__dt" in o:
            return dt.datetime.fromisoformat(o["__dt"])
        if "__np" in o:
            import numpy as np
            return np.dtype(o["__np"]).type(decode(o["v"]))
        if "__nd" in o:
            import numpy as np
            return np.array([decode(x) for x in o["v"]], dtype=np.dtype(o["__nd"])).reshape(o["shape"])
        if "__ns" in o:
            return SimpleNamespace(**{k: decode(v) for k, v in o["__ns"].items()})
        if "__dc" in o:
            obj = object.__new__(_cls(o["__dc"]))
            for k, v in o["f"].items():
                object.__setattr__(obj, k, decode(v))
            return obj
    return {k: decode(v) for k, v in o.items()}


# --------------------------------------------------------------------------- calibration
def calib_snapshot() -> dict:
    """The calibration entries in effect (engine/calibrate.py: stored / fitted / default)
    — read from the DB and the local files on the server, from this copy in the browser."""
    from backend.engine import calibrate as CAL
    from backend.engine import heat_calib as HC
    names = sorted(CAL._registry())
    entry, stored = {}, {}
    for n in names:
        try:
            stored[n] = CAL.stored_entry(n)
            entry[n] = CAL.entry(n)
        except Exception:                   # noqa: BLE001 — an item that cannot resolve here: not served
            pass
    heat = {}
    for n in ("hadley_hr_beta", "humidity_default", "home_temp_c", "home_rh_pct"):
        try:
            heat[n] = HC.current(n)
        except Exception:                   # noqa: BLE001
            pass
    return {"entry": entry, "stored": stored, "heat": heat}


def install_calib(snap: dict) -> None:
    """calibrate.entry / stored_entry and heat_calib.current answer from `snap` (the browser)."""
    from backend.engine import calibrate as CAL
    entry, stored, heat = snap.get("entry") or {}, snap.get("stored") or {}, snap.get("heat") or {}

    def _entry(name, user_id=1):
        if name not in entry:
            raise KeyError(name)
        return dict(entry[name])

    CAL.entry = _entry
    CAL.stored_entry = lambda name, user_id=1: stored.get(name)
    try:
        from backend.engine import heat_calib as HC
    except ImportError:                     # not bundled: nothing reads it
        return

    def _current(name):
        if name in heat:
            return dict(heat[name])
        return _entry(name)
    HC.current = _current


# --------------------------------------------------------------------------- the context
class StaticContext:
    """calc.Context on an exported context (collect() → encode() → JSON → decode())."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.today = doc["today"]
        self._events = {e["id"]: e for e in doc.get("events") or []}
        self._tracks = {}
        for e in self._events.values():
            g = e.get("gpx")
            if g:
                self._tracks[g["course_id"]] = g["track"]

    def inputs(self) -> dict:
        return self.doc["inputs"]

    def grade_models(self) -> dict:
        return self.doc["grade_models"]

    def flags(self):
        return self.doc["flags"]

    def heat_status(self, date: Optional[str]) -> dict:
        from backend.engine import heat_data as HD
        from backend.engine.racepower import calc as CALC
        return HD.project_status(self.doc["heat_base"], CALC.race_day(date))

    def hrc_test(self) -> Optional[dict]:
        return self.doc.get("hrc_test")

    def trail_hr(self) -> Optional[dict]:
        return self.doc.get("trail_hr")

    def body(self) -> Optional[dict]:
        return self.doc.get("body")

    def event(self, eid: str):
        from backend.engine.racepower.calc import CalcError
        e = self._events.get(eid)
        if e is None:
            raise CalcError(404, f"no event {eid}")
        return SimpleNamespace(**e["event"])

    def event_track(self, eid: str):
        g = (self._events.get(eid) or {}).get("gpx")
        if not g:
            return None
        return g["course_id"], g["track"], g["row"]

    def event_splits(self, row: dict, days: int) -> list[float]:
        return list((row.get("splits_by_days") or {}).get(int(days or 1)) or [])

    def event_meta(self, row: dict) -> Optional[dict]:
        return row.get("meta")

    def course_track(self, course_id: str):
        return self._tracks.get(course_id)


def collect(live) -> dict:
    """The context (not yet encoded) from api/racepower.LiveContext, inside the demo app."""
    from backend.engine import event_gpx as EG
    from backend.engine.localtime import today_local
    from backend.engine.planning import Plan
    events = []
    for e in Plan.load().events:
        days = int(e.days or 1)
        item = {"id": e.id, "event": dataclasses.asdict(e), "gpx": None}
        got = live.event_track(e.id)
        if got is not None:
            cid, track, row = got
            item["gpx"] = {"course_id": cid, "track": track,
                           "row": {"filename": row.get("filename"), "sha1": row.get("sha1"),
                                   "meta": EG.meta(row),
                                   "splits_by_days": {days: EG.splits_for(row, days)} if days > 1 else {}}}
        events.append(item)
    return {"v": CTX_VERSION, "today": today_local().isoformat(),
            "inputs": live.inputs(), "grade_models": live.grade_models(), "flags": live.flags(),
            "heat_base": live.heat_status(None), "hrc_test": live.hrc_test(), "trail_hr": live.trail_hr(),
            "body": live.body(), "events": events, "calib": calib_snapshot()}


def export_json(live) -> bytes:
    return json.dumps(encode(collect(live)), ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


# --------------------------------------------------------------------------- the browser entry
_CTX: Optional[StaticContext] = None


def _install_clock(today: str) -> None:
    """today_local() is the export day (the page's clock is frozen there too)."""
    try:
        from backend.engine import localtime as LT
    except ImportError:
        return
    d = dt.date.fromisoformat(today)
    LT.today_local = lambda *a, **k: d


def load(text: str) -> StaticContext:
    """Load data/CTX_FILE (its text) once per worker."""
    global _CTX
    doc = decode(json.loads(text))
    if doc.get("v") != CTX_VERSION:
        raise ValueError(f"context version {doc.get('v')} != {CTX_VERSION}")
    install_calib(doc.get("calib") or {})
    _install_clock(doc["today"])
    _CTX = StaticContext(doc)
    return _CTX


def compute(ctx, method: str, path: str, body) -> dict:
    """One race-calculator request → {"status", "body"} (+ "csv", "filename" for /export/csv),
    as the API answers it. `body`: the parsed JSON (None = no body)."""
    from pydantic import ValidationError

    from backend.engine.racepower import calc as CALC
    sub = path[len(ROUTE_PREFIX):] if path.startswith(ROUTE_PREFIX) else None
    if method == "GET" and sub == "heat-status":         # GET /heat-status?date=: body = {"date"}
        return {"status": 200, "body": CALC.py(ctx.heat_status((body or {}).get("date")))}
    if method != "POST" or sub is None:
        return {"status": 404, "body": {"detail": "Not Found"}}
    try:
        if sub == "plan":
            return {"status": 200, "body": CALC.plan(ctx, CALC.PlanIn.model_validate(body or {}))}
        if sub == "predict":
            return {"status": 200, "body": CALC.py(CALC.predict(ctx, CALC.PredictIn.model_validate(body or {})))}
        if sub.startswith("course/event/"):
            from urllib.parse import unquote
            eid = unquote(sub[len("course/event/"):])
            b = CALC.EventCourseIn.model_validate(body) if body is not None else None
            return {"status": 200, "body": CALC.event_course(ctx, eid, b)}
        if sub == "export/csv":
            text, fname = CALC.export_csv(ctx, CALC.ExportIn.model_validate(body or {}))
            return {"status": 200, "csv": text, "filename": fname}
    except CALC.CalcError as e:
        return {"status": e.status, "body": {"detail": e.detail}}
    except ValidationError as e:
        return {"status": 422, "body": {"detail": _("輸入的數字格式不對"), "errors": e.errors(include_url=False,
                                                                                include_context=False)}}
    return {"status": 404, "body": {"detail": "Not Found"}}


def handle(method: str, path: str, body_text: Optional[str]) -> str:
    """The worker's call: JSON text in, JSON text out ({"status", "body"|"csv", "filename"});
    an unexpected failure is a 500 with a short message and the traceback for the console."""
    import traceback
    try:
        if _CTX is None:
            raise RuntimeError("context not loaded")
        body = json.loads(body_text) if body_text else None
        out = compute(_CTX, method.upper(), path, body)
    except Exception as e:                  # noqa: BLE001 — the page shows a short message
        out = {"status": 500, "body": {"detail": _(ENGINE_FAIL_MSG)},
               "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-4000:]}
    return json.dumps(out, ensure_ascii=False, allow_nan=False, default=_json_default)


def _json_default(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(type(o).__name__)


# --------------------------------------------------------------------------- the bundle
def sample_requests(ctx_doc: dict) -> list[tuple[str, str, dict]]:
    """Inputs that walk the calculator's code paths (the export's import trace and the
    parity test): road 10K / half / full, goal pace / power, every event with a GPX
    (course + plan), multi-day 百岳, the CSV."""
    base = {"effort_formula": "fitted_run", "env_from": {"altitude_m": None, "temp_c": None, "rh_pct": None},
            "env_to": {"altitude_m": None, "temp_c": None, "rh_pct": None}, "stops": [],
            "strategy": {"kind": "even", "amount": 0}, "hills": {"up": 0.05, "down": 0.1},
            "effort_target": 1, "wbal": None, "locks": [], "day_splits_km": [], "terrain": {},
            "hourly_heat": True, "hourly": None, "heat_acclimatisation": {"mode": "auto"}}
    today = dt.date.fromisoformat(ctx_doc["today"])
    soon = (today + dt.timedelta(days=30)).isoformat()

    def manual(type_, km, gain, days=1, **kw):
        b = {**base, "type": type_, "mode": "auto", "date": soon, "distance_km": km, "gain_m": gain, "loss_m": None,
             "days": days, "day_plan": None, "course": {"manual": {"km": km, "gain": gain, "loss": None, "split": "none"}}}
        b.update(kw)
        return b
    out = [
        ("road 10K", "plan", manual("road", 10, 50)),
        ("road half", "plan", manual("road", 21.0975, 120, mode="time", target_pace_s_per_km=300)),
        ("road full", "plan", manual("road", 42.195, 200, mode="power", target_power=230)),
        ("road full %CP", "plan", manual("road", 42.195, 200, mode="power", target_pct_cp=0.85,
                                         start_time="07:00", stops=[{"km": 21, "minutes": 1, "type": "aid"}])),
        ("trail 30K", "plan", manual("trail", 30, 1500, effort_target=0.9,
                                     env_to={"altitude_m": 1200, "temp_c": 24, "rh_pct": 80})),
        ("hike 3 days", "plan", manual("baiyue", 36, 3200, days=3, trip_kind="group", hr_band="aet")),
        ("hike solo power", "plan", manual("baiyue", 14, 1300, mode="power", speed_factor=1.1, trip_kind="solo")),
        ("predict road", "predict", {"type": "road", "distance_km": 10, "gain_m": 30}),
        ("predict hike", "predict", {"type": "baiyue", "distance_km": 20, "gain_m": 1800, "days": 2}),
        ("csv road", "export/csv", {**manual("road", 21.0975, 100), "name": "測試"}),
    ]
    for e in ctx_doc.get("events") or []:
        if not e.get("gpx"):
            continue
        ev = e["event"]
        kind = {"road": "road", "baiyue": "baiyue"}.get(ev.get("kind"), "trail")      # racepower.html KIND
        out.append((f"course {e['id']}", f"course/event/{e['id']}", {"split": "grade"}))
        b = {**base, "type": kind, "mode": "auto", "date": ev.get("date"), "days": ev.get("days") or 1,
             "course": {"course_id": e["gpx"]["course_id"], "event_id": e["id"], "split": "grade"}}
        out.append((f"plan {e['id']}", "plan", b))
        out.append((f"plan {e['id']} goal", "plan", {**b, "mode": "time", "target_time_s": 6 * 3600}))
    return out


def trace(ctx_path) -> dict:
    """_trace_main in a fresh interpreter (the export's process has FastAPI etc. loaded)."""
    import subprocess
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, "-m", "backend.demo.static_racepower", "--trace", str(ctx_path)],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=900)
    if r.returncode != 0:
        raise RuntimeError("racepower trace failed: " + r.stderr[-2000:])
    out = json.loads(r.stdout.strip().splitlines()[-1])
    if out["failures"]:
        raise RuntimeError("racepower trace: samples failed: " + json.dumps(out["failures"], ensure_ascii=False)[:3000])
    bad = set(out["third_party"]) - set(ALLOWED_THIRD_PARTY)
    if bad:
        raise RuntimeError(f"racepower trace: the bundle would need {sorted(bad)} (not in Pyodide's packages)")
    return out


def bundle(modules) -> bytes:
    """The zip the worker unpacks: the traced backend modules, every engine/racepower
    module (lazy imports the samples did not reach), their packages' __init__.py and this
    file. Sorted, fixed timestamps: the same sources give the same bytes."""
    import io
    import zipfile
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    files = set()
    for m in set(modules) | {"backend.demo.static_racepower"}:
        parts = m.split(".")
        p = repo.joinpath(*parts)
        f = p / "__init__.py" if p.is_dir() else p.with_suffix(".py")
        if f.is_file():
            files.add(f)
        for i in range(1, len(parts)):
            init = repo.joinpath(*parts[:i], "__init__.py")
            if init.is_file():
                files.add(init)
    files |= set((repo / "backend" / "engine" / "racepower").glob("*.py"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(files):
            info = zipfile.ZipInfo(f.relative_to(repo).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())
    return buf.getvalue()


def worker_js(version: str) -> str:
    """static_racepower_worker.js with this module's constants filled in."""
    from pathlib import Path
    t = (Path(__file__).resolve().parent / "static_racepower_worker.js").read_text("utf-8")
    for k, v in (("__PYODIDE_URL__", PYODIDE_URL), ("__PACKAGES__", json.dumps(list(PYODIDE_PACKAGES))),
                 ("__BUNDLE__", BUNDLE), ("__CTX_FILE__", CTX_FILE), ("__VERSION__", version)):
        t = t.replace(k, v)
    return t


def _trace_main(ctx_path: str) -> int:
    """`python -m backend.demo.static_racepower --trace <ctx.json>`: run sample_requests()
    with the browser's limits (no FastAPI / DB / network) and print the backend modules
    they import, as JSON — the export bundles exactly those (+ engine/racepower)."""
    import importlib.abc

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in FORBIDDEN_IMPORTS:
                raise ImportError(f"{name} is not available in the browser bundle")
            return None
    sys.meta_path.insert(0, Block())
    text = open(ctx_path, encoding="utf-8").read()
    ctx = load(text)
    raw = json.loads(text)
    failures = []
    for label, sub, body in sample_requests(decode(raw)):
        r = json.loads(handle("POST", ROUTE_PREFIX + sub, json.dumps(body)))
        if r["status"] >= 500:
            failures.append({"label": label, "error": r.get("error"), "trace": r.get("trace")})
    del ctx
    mods = sorted(m for m in sys.modules if m == "backend" or m.startswith("backend."))
    third = sorted({m.split(".")[0] for m in sys.modules
                    if m.split(".")[0] not in sys.stdlib_module_names and not m.startswith("backend")
                    and not m.startswith("_")})
    print(json.dumps({"modules": mods, "third_party": third, "failures": failures}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--trace":
        sys.exit(_trace_main(sys.argv[2]))
    print("usage: python -m backend.demo.static_racepower --trace <ctx.json>", file=sys.stderr)
    sys.exit(2)
