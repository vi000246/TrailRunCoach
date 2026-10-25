"""SP-924: SP-897's registry guard for the series cache too.

Dataset.cached_series keys a row on series_code(compute): codehash.callable_hash of the callable the
call site hands it, a lambda or a local function made right there (`ds.cached_series(KEY, w, lambda
w=w: _per_run(ds, w))`), so there is no static list of roots to check as SP-897 does for the panel
kinds. This test finds the call sites in the source instead — `….cached_series(…)` and the local
names bound to it (`cached = getattr(ds, "cached_series", None)`, then `cached(KEY, w, …)`) — and
rebuilds each callable from its module's own code: the lambda / local def at that line, the module's
globals, a closure whose cells hold the local defs it names (others: None — values, not code; `self`
adds its class to the context, a function-level import its module). callable_roots and the same
`gaps` as SP-897 then name every module-level registry the compute reads whose functions the hash
does not reach.

What a rebuild from the source cannot see: a cell holding some other object of ours (`st` in
race_readiness._uphill's `calc`) adds its class to the hash's context at run time; here that cell
is None, so code reached only through such an object is not checked."""
from __future__ import annotations

import ast
import importlib
import pathlib
import types

import pytest

from backend.engine import codehash as CH
from backend.engine.wko5expr.fitdataset import FitFolderDataset as F
from backend.tests.test_codehash_registries_sp897 import gaps

ROOT = pathlib.Path(__file__).resolve().parents[1]          # backend/

# "module:line" → why the callable cannot be rebuilt here (it comes from the caller at run time)
DYNAMIC: dict = {}


def _is_cached_series(v: ast.AST) -> bool:
    """`x.cached_series`, `getattr(x, "cached_series", …)`, or either inside `… if … else …` / `a or b`."""
    if isinstance(v, ast.Attribute):
        return v.attr == "cached_series"
    if isinstance(v, ast.Call):
        return (isinstance(v.func, ast.Name) and v.func.id == "getattr" and len(v.args) >= 2
                and isinstance(v.args[1], ast.Constant) and v.args[1].value == "cached_series")
    if isinstance(v, ast.IfExp):
        return _is_cached_series(v.body) or _is_cached_series(v.orelse)
    if isinstance(v, ast.BoolOp):
        return any(_is_cached_series(x) for x in v.values)
    return False


def _aliases(fn: ast.AST) -> set:
    """Local names bound to a Dataset's cached_series inside one function."""
    out = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                and _is_cached_series(n.value)):
            out.add(n.targets[0].id)
    return out


def _sites(tree: ast.Module) -> list:
    """[(compute node, the enclosing function nodes outermost first, its class or None)]."""
    out = []

    def visit(node, chain, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, chain, child if not chain else cls)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = set().union(*(_aliases(f) for f in chain + [child]))
                for n in ast.walk(child):
                    if not isinstance(n, ast.Call):
                        continue
                    f = n.func
                    hit = ((isinstance(f, ast.Attribute) and f.attr == "cached_series")
                           or (isinstance(f, ast.Name) and f.id in names))
                    if not hit:
                        continue
                    comp = n.args[2] if len(n.args) >= 3 else next(
                        (k.value for k in n.keywords if k.arg == "compute"), None)
                    if comp is not None and not any(comp is s[0] for s in out):
                        out.append((comp, chain + [child], cls))
                visit(child, chain + [child], cls)
    visit(tree, [], None)
    return out


def _all_codes(co: types.CodeType):
    yield co
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            yield from _all_codes(c)


def _code_at(codes, name: str, node: ast.AST) -> types.CodeType | None:
    hits = [c for c in codes if c.co_name == name and c.co_firstlineno == node.lineno]
    if len(hits) > 1:                            # two lambdas on one line: the column tells them apart
        hits = [c for c in hits if any(p[2] == node.col_offset for p in c.co_positions())] or hits
    return hits[0] if len(hits) == 1 else None


def _local_def(chain: list, name: str):
    for f in reversed(chain):
        for n in ast.walk(f):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name and n is not f:
                return n
    return None


def _local_import(chain: list, name: str):
    """The module or object a function-level import binds to `name` (`from … import athlete as A`)."""
    for f in reversed(chain):
        for n in ast.walk(f):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if (a.asname or a.name.split(".")[0]) == name:
                        return importlib.import_module(a.name if a.asname else a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                for a in n.names:
                    if (a.asname or a.name) == name:
                        base = importlib.import_module(n.module)
                        if hasattr(base, a.name):
                            return getattr(base, a.name)
                        return importlib.import_module(f"{n.module}.{a.name}")
    return None


def _build(code, mod, chain, codes, cls, context, depth=0):
    cells = []
    for v in code.co_freevars:
        val = _local_import(chain, v)
        d = _local_def(chain, v) if depth < 3 and val is None else None
        if d is not None:
            sub = _code_at(codes, v, d)
            if sub is not None:
                val = _build(sub, mod, chain, codes, cls, context, depth + 1)
        elif v == "self" and cls is not None:
            context.append(getattr(mod, cls.name))
        cells.append(types.CellType(val))
    return types.FunctionType(code, vars(mod), code.co_name, None, tuple(cells) or None)


def _series_callables() -> dict:
    """{"module:line": (function, extra context) | reason it could not be rebuilt}."""
    out = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        if "cached_series" not in src:
            continue
        name = ".".join(path.relative_to(ROOT.parent).with_suffix("").parts)
        mod = importlib.import_module(name)
        codes = list(_all_codes(compile(src, getattr(mod, "__file__", str(path)), "exec")))
        for comp, chain, cls in _sites(ast.parse(src)):
            where = f"{name}:{comp.lineno}"
            context: list = []
            code = None
            if isinstance(comp, ast.Lambda):
                code = _code_at(codes, "<lambda>", comp)
            elif isinstance(comp, ast.Name):
                d = _local_def(chain, comp.id)
                code = _code_at(codes, comp.id, d) if d is not None else None
                if code is None and hasattr(mod, comp.id):
                    out[where] = (getattr(mod, comp.id), context)
                    continue
            if code is None:
                out[where] = f"not a lambda / local def / module function: {ast.unparse(comp)}"
                continue
            out[where] = (_build(code, mod, chain, codes, cls, context), context)
    return out


SITES = _series_callables()


def test_every_call_site_is_found_and_rebuilt():
    """26 call sites when this was written (SP-924); every one rebuilt or in DYNAMIC with a reason."""
    assert len(SITES) >= 26, sorted(SITES)
    lost = {k: v for k, v in SITES.items() if isinstance(v, str) and k not in DYNAMIC}
    assert not lost, lost


@pytest.mark.parametrize("site", sorted(k for k, v in SITES.items() if not isinstance(v, str)))
def test_every_registry_a_series_compute_reads_is_reached(site):
    fn, extra = SITES[site]
    roots, more = CH.callable_roots(fn)
    found = gaps(f"series {site}", (roots, [F] + more + extra, ()))
    assert not found, (f"cached_series at {site}: codehash reads these registries but does not reach what "
                       f"they hold — the row would not recompute when they change: "
                       + "; ".join(f"{k} misses {v}" for k, v in found.items()))


def test_every_rebuilt_compute_reaches_code_beyond_itself():
    """The rebuilt callables resolve their names (globals, the local defs in their cells): each
    hash reaches more than the lambda itself — else the guard above would pass on nothing."""
    thin = []
    for site, v in SITES.items():
        if isinstance(v, str):
            continue
        roots, more = CH.callable_roots(v[0])
        if len(CH.closure(roots, [F] + more + v[1])) < 2:
            thin.append(site)
    assert not thin, thin


def test_it_catches_a_registry_read_through_a_lambda(monkeypatch):
    """A compute that looks its function up in a registry: the guard names it. (Written into a
    module under backend: codehash only follows our own code, and pytest names this file otherwise.)"""
    import sys
    m = types.ModuleType("backend.sp924_fake")
    m.__file__ = "sp924_fake.py"
    exec(compile("from backend.engine import workout_review as WR\nfn = lambda: WR._SECTIONS.get('x')\n",
                 m.__file__, "exec"), vars(m))
    monkeypatch.setitem(sys.modules, m.__name__, m)
    roots, more = CH.callable_roots(m.fn)
    found = gaps("series test", (roots, [F] + more, ()))
    assert "backend.engine.workout_review:_SECTIONS" in found
    assert "backend.engine.workout_review._iv_verdict" in found["backend.engine.workout_review:_SECTIONS"]
