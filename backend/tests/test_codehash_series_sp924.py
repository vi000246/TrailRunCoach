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

SP-1006: a cell holding some other object of ours (`st` in race_readiness._uphill's `calc`) adds its
class to the hash's context at run time (callable_roots); the rebuilt cell is None, so the test infers
that class from the source and adds it to the context the same way (_infer): the cached_series call
itself (its receiver is the Dataset, its `w` argument the Workout its signature names), the variable's
annotation, what it is assigned from (a class of ours, or a function / method whose return is
annotated), or — for an unannotated parameter — what the callers in the same module pass. A cell whose
type cannot be inferred fails the test unless UNTYPED says why it holds nothing of ours."""
from __future__ import annotations

import ast
import builtins
import importlib
import pathlib
import types
import typing

import pytest

from backend.engine import codehash as CH
from backend.engine.wko5expr.dataset import Dataset, Workout
from backend.engine.wko5expr.fitdataset import FitFolderDataset as F
from backend.tests.test_codehash_registries_sp897 import gaps

ROOT = pathlib.Path(__file__).resolve().parents[1]          # backend/

# "module:line" → why the callable cannot be rebuilt here (it comes from the caller at run time)
DYNAMIC: dict = {}

def _declared() -> dict:
    """SP-1006: "module.Class.function:cell" → (the classes it holds, why) for a closure cell whose type the
    source does not tell (_infer gives up): those classes go into the context as the inferred ones do; ()
    when it holds nothing of ours. Written here rather than as annotations in the engine: an annotation
    changes the code hash, and every series row behind it would recompute on the next deploy."""
    from backend.engine.wko5expr import parser as P
    from backend.engine.wko5expr.evaluator import Ctx
    ev = "backend.engine.wko5expr.evaluator.Evaluator."
    ctx = ((Ctx,), "the evaluator's Ctx: Evaluator.ev(n, ctx: Ctx) hands it down; the builtins reach these "
                   "through their dispatch table, so no direct caller tells")
    node = ((P.Node,), "a parser node (wko5expr.parser: Node and its dataclasses, no methods)")
    return {ev + "_aggregate:ctx": ctx, ev + "_builtin_workout:ctx": ctx,
            ev + "_aggregate:node": node, ev + "_builtin_workout:node": node, ev + "_workout_curve:node": node}


DECLARED = _declared()

DATASET = F                    # the receiver of cached_series: the concrete dataset (as the guard's own context)


def _receiver(v: ast.AST):
    """The object of `x.cached_series` / `getattr(x, "cached_series", …)` (in `… if … else …` / `a or b`
    the first one), else None."""
    if isinstance(v, ast.Attribute):
        return v.value if v.attr == "cached_series" else None
    if isinstance(v, ast.Call):
        if (isinstance(v.func, ast.Name) and v.func.id == "getattr" and len(v.args) >= 2
                and isinstance(v.args[1], ast.Constant) and v.args[1].value == "cached_series"):
            return v.args[0]
        return None
    if isinstance(v, ast.IfExp):
        return _receiver(v.body) or _receiver(v.orelse)
    if isinstance(v, ast.BoolOp):
        return next((r for r in map(_receiver, v.values) if r is not None), None)
    return None


def _is_cached_series(v: ast.AST) -> bool:
    """`x.cached_series`, `getattr(x, "cached_series", …)`, or either inside `… if … else …` / `a or b`."""
    return _receiver(v) is not None


def _aliases(fn: ast.AST) -> dict:
    """{local name bound to a Dataset's cached_series inside one function: the Dataset expression}."""
    out = {}
    for n in ast.walk(fn):
        if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                and _is_cached_series(n.value)):
            out[n.targets[0].id] = _receiver(n.value)
    return out


def _sites(tree: ast.Module) -> list:
    """[(compute node, the enclosing function nodes outermost first, its class or None, the call's
    {cell name: type} it tells — the receiver a Dataset, the `w` argument a Workout)]."""
    out = []

    def visit(node, chain, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, chain, child if not chain else cls)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names: dict = {}
                for f in chain + [child]:
                    names.update(_aliases(f))
                for n in ast.walk(child):
                    if not isinstance(n, ast.Call):
                        continue
                    f = n.func
                    if isinstance(f, ast.Attribute) and f.attr == "cached_series":
                        recv = f.value
                    elif isinstance(f, ast.Name) and f.id in names:
                        recv = names[f.id]
                    else:
                        continue
                    comp = n.args[2] if len(n.args) >= 3 else next(
                        (k.value for k in n.keywords if k.arg == "compute"), None)
                    w = n.args[1] if len(n.args) >= 2 else next((k.value for k in n.keywords if k.arg == "w"), None)
                    told = {}
                    if isinstance(recv, ast.Name):
                        told[recv.id] = {DATASET}
                    if isinstance(w, ast.Name):
                        told[w.id] = _classes(typing.get_type_hints(Dataset.cached_series).get("w"))
                    if comp is not None and not any(comp is s[0] for s in out):
                        out.append((comp, chain + [child], cls, told))
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


# ---------------------------------------------------------------------------- SP-1006: a cell's type

_ANY = object()                 # a type the source does not tell


def _classes(tp) -> set | None:
    """The runtime types an annotation allows ({} for None alone), None when it does not say (Any, an
    unresolvable name). list[X] / dict[…] are the container (not ours, as callable_roots sees it)."""
    if tp is None or tp is type(None):
        return set()
    if tp is typing.Any or isinstance(tp, (str, typing.ForwardRef)):
        return None
    origin = typing.get_origin(tp)
    if origin is typing.Union or isinstance(tp, types.UnionType):
        out: set = set()
        for a in typing.get_args(tp):
            c = _classes(a)
            if c is None:
                return None
            out |= c
        return out
    if origin is not None:
        return {origin} if isinstance(origin, type) else None
    return {tp} if isinstance(tp, type) else None


def _ann(node: ast.AST, ns: dict) -> set | None:
    src = node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ast.unparse(node)
    try:
        return _classes(eval(src, ns))                                   # noqa: S307 — our own source
    except Exception:                                                    # noqa: BLE001 — not resolvable here
        return None


def _returns(fn) -> set | None:
    """The annotated return of a function / method / class (a class: itself), None when not annotated."""
    if isinstance(fn, type):
        return {fn}
    fn = getattr(fn, "__func__", fn)
    if not callable(fn):
        return None
    try:
        hints = typing.get_type_hints(fn)
    except Exception:                                                    # noqa: BLE001
        return None
    return _classes(hints["return"]) if "return" in hints else None


class _Scope:
    """One module and the chain of functions a name is looked up in (innermost last)."""

    def __init__(self, mod, tree, chain, cls, told=None):
        self.mod, self.tree, self.chain, self.cls, self.told = mod, tree, chain, cls, told or {}

    def ns(self) -> dict:
        ns = dict(vars(builtins))
        ns.update(vars(self.mod))
        return ns

    def resolve(self, e: ast.AST):
        """The object an expression names (a module, a class, a function), or _ANY."""
        if isinstance(e, ast.Name):
            v = _local_import(self.chain, e.id)
            if v is not None:
                return v
            if any(_binds(f, e.id) for f in self.chain):
                return _ANY
            return self.ns().get(e.id, _ANY)
        if isinstance(e, ast.Attribute):
            base = self.resolve(e.value)
            if isinstance(base, (types.ModuleType, type)):
                return getattr(base, e.attr, _ANY)
            t = self.expr(e.value)
            if t and len(t) == 1:
                return getattr(next(iter(t)), e.attr, _ANY)
        return _ANY


def _binds(f: ast.AST, name: str) -> bool:
    a = f.args
    if any(p.arg == name for p in a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]):
        return True
    for n in ast.walk(f):
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store):
            return True
    return False


def _expr(scope: _Scope, e: ast.AST, depth: int) -> set | None:
    if isinstance(e, ast.Constant):
        return set() if e.value is None else {type(e.value)}
    if isinstance(e, (ast.JoinedStr, ast.BinOp, ast.UnaryOp, ast.Compare, ast.List, ast.Tuple, ast.Dict,
                      ast.Set, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
        return {object}                                  # a value or a plain container: nothing of ours
    if isinstance(e, (ast.IfExp, ast.BoolOp)):
        parts = [e.body, e.orelse] if isinstance(e, ast.IfExp) else e.values
        out: set = set()
        for p in parts:
            t = _expr(scope, p, depth)
            if t is None:
                return None
            out |= t
        return out
    if isinstance(e, ast.Name):
        return _name(scope, e.id, depth)
    if isinstance(e, ast.Call):
        fn = scope.resolve(e.func)
        if fn is _ANY:
            return None
        if isinstance(fn, type):
            return {fn}
        if isinstance(fn, types.BuiltinFunctionType):           # float(…), round(…), len(…): a value
            return {object}
        return _returns(fn)
    if isinstance(e, ast.Attribute):
        t = _expr(scope, e.value, depth)
        if t and len(t) == 1:
            try:
                hints = typing.get_type_hints(next(iter(t)))
            except Exception:                                    # noqa: BLE001
                return None
            return _classes(hints[e.attr]) if e.attr in hints else None
    return None


_Scope.expr = lambda self, e, depth=0: _expr(self, e, depth)


def _param(f: ast.AST, name: str):
    a = f.args
    plain = a.posonlyargs + a.args
    for i, p in enumerate(plain):
        if p.arg == name:
            return p, i
    for p in a.kwonlyargs:
        if p.arg == name:
            return p, None
    return None, None


def _callers(scope: _Scope, f: ast.AST, pos, name: str, depth: int) -> set | None:
    """What the callers in the same module pass for parameter `name` of `f` (a module function, or a
    method called as self.f(…)); None when there is no caller or one of them does not tell."""
    method = scope.cls is not None and scope.chain and scope.chain[0] is f
    out: set = set()
    seen = False

    def visit(node, chain, cls):
        nonlocal out, seen
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, chain, child)
                continue
            inner = chain + [child] if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else chain
            if isinstance(child, ast.Call) and out is not None:
                fn = child.func
                hit = ((not method and isinstance(fn, ast.Name) and fn.id == f.name)
                       or (method and isinstance(fn, ast.Attribute) and fn.attr == f.name
                           and isinstance(fn.value, ast.Name) and fn.value.id == "self"))
                if hit:
                    arg = None
                    i = pos - 1 if (method and pos is not None) else pos
                    if i is not None and i < len(child.args) and not any(isinstance(x, ast.Starred) for x in child.args[:i + 1]):
                        arg = child.args[i]
                    arg = next((k.value for k in child.keywords if k.arg == name), arg)
                    t = _expr(_Scope(scope.mod, scope.tree, inner, cls), arg, depth + 1) if arg is not None else None
                    seen = True
                    out = None if t is None else out | t
            visit(child, inner, cls)
    visit(scope.tree, [], None)
    return out if seen else None


def _name(scope: _Scope, name: str, depth: int) -> set | None:
    """The types a local name of the chain holds (see the module doc), None when the source does not tell."""
    if depth > 3:
        return None
    if name in scope.told:
        return scope.told[name]
    for f in reversed(scope.chain):
        if not _binds(f, name):
            continue
        key = (id(f), name)
        if key in _BUSY:                        # `x = x or …`: the name itself adds nothing new
            return set()
        _BUSY.add(key)
        try:
            return _bound(scope, f, name, depth)
        finally:
            _BUSY.discard(key)
    return None


_BUSY: set = set()


def _bound(scope: _Scope, f: ast.AST, name: str, depth: int) -> set | None:
    """The types `name`, bound in `f`, holds: its parameter's annotation (else its callers'), or the union
    of what it is assigned; None when one of them does not tell (a for / with target too)."""
    p, pos = _param(f, name)
    if p is not None:
        if p.annotation is not None:
            return _ann(p.annotation, scope.ns())
        return _callers(scope, f, pos, name, depth)
    out: set = set()
    for n in ast.walk(f):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name:
            t = _ann(n.annotation, scope.ns())
        elif isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == name for x in n.targets):
            t = _expr(scope, n.value, depth + 1)
        elif isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store):
            t = set() if _assigned(f, n) else None          # handled above / a for, with or tuple target
        else:
            continue
        if t is None:
            return None
        out |= t
    return out


def _assigned(f: ast.AST, target: ast.Name) -> bool:
    """`target` is the whole left side of a plain / annotated assignment (handled above)."""
    for n in ast.walk(f):
        if isinstance(n, ast.Assign) and any(t is target for t in n.targets):
            return True
        if isinstance(n, ast.AnnAssign) and n.target is target:
            return True
    return False


def _qual(mod, chain, cls) -> str:
    return ".".join([mod.__name__] + ([cls.name] if cls is not None else []) + [f.name for f in chain])


def _build(code, mod, chain, codes, cls, context, scope, unresolved, depth=0):
    cells = []
    for v in code.co_freevars:
        val = _local_import(chain, v)
        d = _local_def(chain, v) if depth < 3 and val is None else None
        if d is not None:
            sub = _code_at(codes, v, d)
            if sub is not None:
                val = _build(sub, mod, chain, codes, cls, context, scope, unresolved, depth + 1)
        elif v == "self" and cls is not None:
            context.append(getattr(mod, cls.name))
        elif val is None:                       # SP-1006: an object of ours adds its class, as at run time
            t = _name(scope, v, 0)
            if t is None:                       # the source does not tell: DECLARED says, or the test fails
                q = f"{_qual(mod, chain, cls)}:{v}"
                unresolved.append(q)
                t = set(DECLARED[q][0]) if q in DECLARED else set()
            context.extend(x for x in t if isinstance(x, type) and CH._ours(x) and x not in context)
        cells.append(types.CellType(val))
    return types.FunctionType(code, vars(mod), code.co_name, None, tuple(cells) or None)


def _module_sites(name: str, mod, src: str, path: str) -> dict:
    """{"module:line": (function, extra context, cells whose type is not told) | why not rebuilt}."""
    out = {}
    tree = ast.parse(src)
    codes = list(_all_codes(compile(src, path, "exec")))
    for comp, chain, cls, told in _sites(tree):
        where = f"{name}:{comp.lineno}"
        context: list = []
        unresolved: list = []
        code = None
        if isinstance(comp, ast.Lambda):
            code = _code_at(codes, "<lambda>", comp)
        elif isinstance(comp, ast.Name):
            d = _local_def(chain, comp.id)
            code = _code_at(codes, comp.id, d) if d is not None else None
            if code is None and hasattr(mod, comp.id):
                out[where] = (getattr(mod, comp.id), context, unresolved)
                continue
        if code is None:
            out[where] = f"not a lambda / local def / module function: {ast.unparse(comp)}"
            continue
        scope = _Scope(mod, tree, chain, cls, told)
        out[where] = (_build(code, mod, chain, codes, cls, context, scope, unresolved), context, unresolved)
    return out


def _series_callables() -> dict:
    """{"module:line": (function, extra context, cells whose type is not told) | reason it could not be rebuilt}."""
    out = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        if "cached_series" not in src:
            continue
        name = ".".join(path.relative_to(ROOT.parent).with_suffix("").parts)
        mod = importlib.import_module(name)
        out.update(_module_sites(name, mod, src, getattr(mod, "__file__", str(path))))
    return out


SITES = _series_callables()


def test_every_call_site_is_found_and_rebuilt():
    """26 call sites when this was written (SP-924); every one rebuilt or in DYNAMIC with a reason."""
    assert len(SITES) >= 26, sorted(SITES)
    lost = {k: v for k, v in SITES.items() if isinstance(v, str) and k not in DYNAMIC}
    assert not lost, lost


@pytest.mark.parametrize("site", sorted(k for k, v in SITES.items() if not isinstance(v, str)))
def test_every_registry_a_series_compute_reads_is_reached(site):
    fn, extra, _u = SITES[site]
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


# ---------------------------------------------------------------------------- SP-1006: objects in the closure

def test_every_closure_object_has_a_type_the_source_tells():
    """Each cell the rebuild leaves None either has a type told by the source (then its class, when ours,
    is in the context as at run time) or DECLARED names the classes it holds and why."""
    untold = {c for v in SITES.values() if not isinstance(v, str) for c in v[2]}
    missing = sorted(untold - set(DECLARED))
    assert not missing, ("closure objects whose type the source does not tell: annotate the variable, or add it "
                         "to DECLARED with the classes it holds (() for none of ours) and why — " + ", ".join(missing))
    stale = sorted(set(DECLARED) - untold)
    assert not stale, f"DECLARED entries no cell needs any more: {stale}"


def test_the_objects_named_in_the_ticket_add_their_class():
    """race_readiness._uphill's `st` (a Status, from its caller), the Dataset and the Workout of the call."""
    from backend.engine.status import Status
    from backend.engine.trail_dict_store import Index
    uphill = next(v for k, v in SITES.items() if k.startswith("backend.engine.race_readiness:") and not isinstance(v, str)
                  and "st" in v[0].__code__.co_freevars)
    assert Status in uphill[1] and Workout in uphill[1], uphill[1]
    kind = next(v for k, v in SITES.items() if k.startswith("backend.engine.racepower.athlete:")
                and not isinstance(v, str) and "index" in v[0].__code__.co_freevars)
    assert Index in kind[1] and F in kind[1], kind[1]


_FAKE = '''
from backend.engine import workout_review as WR


class Probe:
    def look(self):
        return WR._SECTIONS.get("x")


def make_probe() -> Probe:
    return Probe()


def series(ds, w):
    p = Probe()
    return ds.cached_series("k", w, lambda: p.look())


def series_via_caller(ds, w):
    return _inner(ds, make_probe(), w)


def _inner(ds, q, w):
    return ds.cached_series("k2", w, lambda: q.look())


def series_untold(ds, w, r):
    return ds.cached_series("k3", w, lambda: r.look())
'''


def test_it_catches_a_registry_read_through_an_object_in_the_closure(monkeypatch):
    """The registry read only through an object in the cell (`p.look()`): seen once the object's class is
    inferred — assigned from the class, or passed by the caller from an annotated function; a cell whose
    type nothing tells is reported."""
    import sys
    m = types.ModuleType("backend.sp1006_fake")
    m.__file__ = "sp1006_fake.py"
    exec(compile(_FAKE, m.__file__, "exec"), vars(m))
    monkeypatch.setitem(sys.modules, m.__name__, m)
    sites = _module_sites(m.__name__, m, _FAKE, m.__file__)
    assert len(sites) == 3, sites
    by_line = dict(sorted(sites.items(), key=lambda kv: int(kv[0].rsplit(":", 1)[1])))
    (fn1, ctx1, u1), (fn2, ctx2, u2), (fn3, ctx3, u3) = by_line.values()
    assert m.Probe in ctx1 and m.Probe in ctx2 and u1 == u2 == []
    for fn, ctx in ((fn1, ctx1), (fn2, ctx2)):
        roots, more = CH.callable_roots(fn)
        found = gaps("series test", (roots, [F] + more + ctx, ()))
        assert "backend.engine.workout_review:_SECTIONS" in found, found
        # without the inferred class (as before SP-1006) the read is not seen
        assert "backend.engine.workout_review:_SECTIONS" not in gaps("series test", (roots, [F] + more, ()))
    assert u3 == ["backend.sp1006_fake.series_untold:r"]
