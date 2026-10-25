"""
Code-version hashes that cover only the code a cache's value comes from (SP-320 ①).

A disk cache key used to hash whole module sources (`inspect.getsource(module)` of
athlete.py, workout_review.py …): any edit anywhere in those modules — a comment, an
unrelated function — threw every entry away on deploy (~2.5 NAS CPU-min per tenant).
`code_hash(*roots)` instead walks the functions the roots can reach and hashes those:

* functions / methods: the compiled code (bytecode, constants, names, defaults), not the
  source text — comments, blank lines and edits elsewhere in the file don't count;
* the same in every process (SP-773): a default that is a function, class or object is hashed
  by its module.qualname (`_stable`), never by a repr with a memory address — that changed the
  hash on every restart, and every disk cache keyed on it missed; a function / class of ours
  given as a default (`gpx=stored_course`) is followed like a name the function reads;
* reached through a global name, a module attribute (`A.pd_model`, also after a local
  `from … import … as A`), a nested function / lambda / comprehension, or a method name
  called on any object (`ds.channel(…)`) when that method belongs to a reached
  class or to one of the `context` classes;
* module-level constants a reached function reads (`WINDOWS`, `A.CP_WINDOW_DAYS`): the
  AST of their top-level assignment, so editing the value changes the hash;
* code outside `backend` (numpy, the stdlib …) is not followed.

Dynamic lookups (`getattr(m, name)`, a registry filled at import time) are not seen: every
caller keeps a manual version constant next to the hash for those, and a test pins the
roots of each cache.

Cache plumbing (PLUMBING: `Dataset.cached_series`, `flush_series` …) is never followed:
memoising a value does not change it, and an edit to the cache machinery would otherwise drop
every value cached through it (SP-339). A caller can leave out more for its own cache (`skip`,
SP-811): a method reached only through the by-name rule whose result the key already holds as a
value — the estimate / PD memos skip `EngineConfig.load` (another `.load()`, json.load or
activity_tags.load, matched it; the Dataset gets its config loaded, and `config.to_dict()` is
in the key).

This module itself is plumbing too (PLUMBING_MODULES, SP-894): the Dataset build reaches
`estimate_code()` → `code_hash`, so every chart, activity_auto, the estimate and PD memos used to
hash codehash's own functions — an edit here (SP-811, SP-819) changed all of their keys, though how a
code version is computed is not what any cached value comes from. A change to the hashing rules that
should drop the old values says so with the caches' manual versions.

What `dis` finds in a code object and the sha1 of its parts are kept per code object (`_scan`,
`_code_sha`, SP-878): many caches reach the same functions, and the warm-up's chart signatures
scanned their bytecode again for each. What a name points to is looked up on every walk, so a module
global replaced at run time is followed as it is now.

Two functions can share a qualified name: every `@dataclass`'s generated `__repr__` (its
`__wrapped__` is named `__create_fn__.<locals>.__repr__`), two lambdas in one function. The walk
keeps the last one it reaches, and which is last follows the order of a set of names — the hash
seed. `merge=True` keeps them all (sorted), so the hash is the same in every process and an
edit to any of them changes it (SP-834). Only the chart signatures use it for now: turning it on
everywhere changes every cache key once (estimate, PD, per-workout, series.db), which waits for a
change that recomputes them anyway.

`callable_hash(fn)` (SP-339) is for a callable made at the call site — `ds.cached_series(key, w,
lambda w=w: _measure(ds, w))`: its defaults are inputs, not code, so they are left out; what it
reaches through its closure cells is followed (a nested function, a local `import … as A` read
as `A.f`, the classes of the objects of ours it closes over, whose methods it calls by name).
"""
from __future__ import annotations

import ast
import dis
import functools
import hashlib
import importlib
import inspect
import re
import sys
import types
from typing import Any, Iterable, Optional

PKG = "backend"

_MEMO: dict = {}
_ASSIGN: dict = {}
_CALLABLE: dict = {}

# cache plumbing, never followed (see the module doc): qualified names of functions / classes
PLUMBING = frozenset({
    "backend.engine.wko5expr.dataset.Dataset.cached_series",
    "backend.engine.wko5expr.dataset.Dataset.flush_series",
    "backend.engine.wko5expr.fitdataset.FitFolderDataset.flush_series",
    "backend.engine.wko5expr.dataset.batched_flush",
    "backend.engine.wko5expr.dataset.flush_held",
})
# modules whose functions and constants are never followed: this one (SP-894, the module doc)
PLUMBING_MODULES = frozenset({__name__})


def _ours(obj) -> bool:
    mod = obj.__name__ if isinstance(obj, types.ModuleType) else getattr(obj, "__module__", None)
    return isinstance(mod, str) and (mod == PKG or mod.startswith(PKG + "."))


def _assignments(mod: types.ModuleType) -> dict:
    """{top-level name: AST dump of its assignment (or 'import' origin)} of one module."""
    hit = _ASSIGN.get(mod.__name__)
    if hit is not None:
        return hit
    out: dict = {}
    try:
        tree = ast.parse(inspect.getsource(mod))
    except (OSError, TypeError, SyntaxError):
        tree = None
    for node in (tree.body if tree else []):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            if node.level:
                parts = mod.__name__.split(".")[: -node.level]
                base = ".".join(parts + [node.module])
            for a in node.names:
                out[a.asname or a.name] = ("from", base, a.name)
            continue
        if not targets:
            continue
        dump = ast.dump(node.value if node.value is not None else node, annotate_fields=False)
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    prev = out.get(n.id)
                    out[n.id] = (prev + "|" + dump) if isinstance(prev, str) else dump
    _ASSIGN[mod.__name__] = out
    return out


def _constant(mod: types.ModuleType, name: str, depth: int = 0) -> Optional[str]:
    a = _assignments(mod).get(name)
    if isinstance(a, tuple) and depth < 5:
        try:
            src = importlib.import_module(a[1])
        except Exception:                       # noqa: BLE001
            return f"{a[1]}.{a[2]}"
        return _constant(src, a[2], depth + 1) if _ours(src) else f"{a[1]}.{a[2]}"
    return a


_ADDR = re.compile(r" at 0x[0-9a-fA-F]+")


def _qualname(x) -> str:
    return f"{getattr(x, '__module__', None) or '?'}.{getattr(x, '__qualname__', None) or getattr(x, '__name__', '?')}"


_SHA: dict = {}


def _code_sha(co: types.CodeType):
    """A fresh sha1 fed with a code object's parts (_code_parts), kept per code object like _scan
    (SP-878): the caller may go on updating the copy it gets."""
    hit = _SHA.get(id(co))
    if hit is None or hit[0] is not co:
        h = hashlib.sha1()
        for p in _code_parts(co):
            h.update(p if isinstance(p, bytes) else _stable(p).encode("utf-8"))
            h.update(b"\x00")
        hit = _SHA[id(co)] = (co, h)
    return hit[1].copy()


def _own_hash(co: types.CodeType) -> str:
    return _code_sha(co).hexdigest()[:12]


def _stable(x) -> str:
    """repr that is the same in every process (SP-773):
    * set / dict order fixed: a frozenset's repr follows the per-process hash seed
      (PYTHONHASHSEED), and `x in {"a", "b"}` compiles to a frozenset constant;
    * no memory address: a function, class or bound method (a default `gpx=stored_course`) is
      written as module.qualname (a function also with its own compiled code, so two lambdas of
      one scope differ and editing one counts); anything else whose repr carries
      `at 0x…` (an `object()` sentinel, FastAPI's `Depends(get_db)`) loses the address."""
    if isinstance(x, (set, frozenset)):
        return f"{type(x).__name__}({sorted(_stable(v) for v in x)})"
    if isinstance(x, dict):
        return "{" + ",".join(sorted(f"{_stable(k)}:{_stable(v)}" for k, v in x.items())) + "}"
    if isinstance(x, (list, tuple)):
        return f"{type(x).__name__}[" + ",".join(_stable(v) for v in x) + "]"
    if isinstance(x, types.FunctionType):
        return f"<function {_qualname(x)} {_own_hash(x.__code__)}>"
    if isinstance(x, types.MethodType):
        return f"<method {_stable(x.__func__)} of {_qualname(type(x.__self__))}>"
    if isinstance(x, type):
        return f"<class {_qualname(x)}>"
    if isinstance(x, (types.BuiltinFunctionType, types.BuiltinMethodType)):
        return f"<built-in {_qualname(x)}>"
    return _ADDR.sub("", repr(x))


def _code_parts(co: types.CodeType) -> Iterable:
    yield co.co_code
    yield co.co_names
    yield co.co_varnames
    yield co.co_freevars
    yield co.co_cellvars
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            yield from _code_parts(c)
        else:
            yield _stable(c)


def _codes(co: types.CodeType) -> Iterable[types.CodeType]:
    yield co
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            yield from _codes(c)


def _resolve_import(name: str, frm: Optional[str]):
    try:
        m = importlib.import_module(name)
    except Exception:                           # noqa: BLE001
        return None
    if frm is None:
        return m
    if hasattr(m, frm):
        return getattr(m, frm)
    try:
        return importlib.import_module(f"{name}.{frm}")
    except Exception:                           # noqa: BLE001
        return None


# what `dis` finds in a code object, kept per code object (SP-878): {id(code): (code, events)}. A code
# object never changes, so its scan is the same every time; what the names it reads point to is
# looked up again on every walk (_refs), so a module global replaced at run time (a test's
# monkeypatch) is followed as it is now. The code object is held so its id is never reused.
_SCAN: dict = {}
_GLOBAL, _ATTR, _IMPORT, _FROM = 0, 1, 2, 3
_LOCAL_LOADS = ("LOAD_FAST", "LOAD_DEREF", "LOAD_CLOSURE")


def _scan(co: types.CodeType) -> tuple:
    """The instructions _refs resolves, in order, for a code object and its nested code:
    (_IMPORT, top package, stored as) for `import a.b as c` / `import a`; (_FROM, module, name,
    stored as | None); (_GLOBAL, name); (_ATTR, name, "global" | "local" | None, base name)."""
    hit = _SCAN.get(id(co))
    if hit is not None and hit[0] is co:
        return hit[1]
    events: list = []
    for c in _codes(co):
        ins = list(dis.get_instructions(c))
        pending = None
        for i, x in enumerate(ins):
            op = x.opname
            if op == "IMPORT_NAME":
                pending = x.argval
                fromlist = ins[i - 1].argval if i and ins[i - 1].opname == "LOAD_CONST" else None
                if not fromlist:
                    # `import a.b as c` (3.11: IMPORT_FROM follows) or plain `import a`
                    nxt = ins[i + 1] if i + 1 < len(ins) else None
                    if nxt is not None and nxt.opname.startswith("STORE"):
                        events.append((_IMPORT, pending.split(".")[0], nxt.argval))
            elif op == "IMPORT_FROM" and pending:
                nxt = ins[i + 1] if i + 1 < len(ins) else None
                events.append((_FROM, pending, x.argval,
                               nxt.argval if nxt is not None and nxt.opname.startswith("STORE") else None))
            elif op in ("LOAD_GLOBAL", "LOAD_NAME"):
                events.append((_GLOBAL, x.argval))
            elif op in ("LOAD_ATTR", "LOAD_METHOD"):
                prev = ins[i - 1] if i else None
                where = None
                if prev is not None and prev.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                    where = "global"
                elif prev is not None and prev.opname in _LOCAL_LOADS:
                    where = "local"
                events.append((_ATTR, x.argval, where, prev.argval if where else None))
    out = tuple(events)
    _SCAN[id(co)] = (co, out)
    return out


def _refs(fn) -> tuple[list, set]:
    """(objects a function refers to, attribute names it reads) — globals, module attributes
    after `X.attr`, locally imported names; nested code included."""
    g = getattr(fn, "__globals__", {}) or {}
    objs: list = []
    attrs: set = set()
    local: dict = {}
    for e in _scan(fn.__code__):
        kind = e[0]
        if kind == _IMPORT:
            local[e[2]] = _resolve_import(e[1], None)
        elif kind == _FROM:
            obj = _resolve_import(e[1], e[2])
            if e[3] is not None:
                local[e[3]] = obj
            if obj is not None:
                objs.append(obj)
        elif kind == _GLOBAL:
            name = e[1]
            if name in g:
                objs.append(g[name])
                if not callable(g[name]) and not isinstance(g[name], types.ModuleType):
                    objs.append(("const", fn.__module__, name))
            else:
                objs.append(("const", fn.__module__, name))
        else:
            name, where, base_name = e[1], e[2], e[3]
            attrs.add(name)
            base = g.get(base_name) if where == "global" else local.get(base_name) if where == "local" else None
            if isinstance(base, types.ModuleType) and _ours(base):
                if hasattr(base, name):
                    v = getattr(base, name)
                    objs.append(v)
                    if not callable(v) and not isinstance(v, types.ModuleType):
                        objs.append(("const", base.__name__, name))
            elif isinstance(base, type) and _ours(base) and name in vars(base):
                objs.append(vars(base)[name])
    return objs, attrs


def _default_refs(fn) -> list:
    """Functions, methods and classes among a function's default values (also inside a tuple, list,
    set or dict), to be followed like the names it reads."""
    out: list = []

    def walk(v, depth):
        if isinstance(v, (types.FunctionType, types.MethodType, type, staticmethod, classmethod,
                          property, functools.partial)):
            out.append(v)
        elif isinstance(v, dict) and depth < 3:
            for k, x in v.items():
                walk(k, depth + 1)
                walk(x, depth + 1)
        elif isinstance(v, (list, tuple, set, frozenset)) and depth < 3:
            for x in v:
                walk(x, depth + 1)

    walk(fn.__defaults__ or (), 0)
    walk(fn.__kwdefaults__ or {}, 0)
    return out


def _unwrap(f):
    seen = 0
    while hasattr(f, "__wrapped__") and seen < 10:
        f = f.__wrapped__
        seen += 1
    if isinstance(f, (staticmethod, classmethod)):
        f = f.__func__
    if isinstance(f, property):
        f = f.fget
    if isinstance(f, functools.partial):
        f = f.func
    return f


def closure(roots: Iterable[Any], context: Iterable[type] = (), skip: Iterable[str] = (),
            merge: bool = False) -> dict:
    """{qualified name: hash part} of everything the roots reach (see the module doc).
    `skip`: qualified names of functions this cache never follows, like PLUMBING but for one
    caller (a method reached only by name whose effect the cache key holds as a value).
    `merge`: functions sharing a qualified name all count, not just the last one reached."""
    skip = PLUMBING | frozenset(skip)
    parts: dict = {}
    same: dict = {}
    classes: list = [c for c in context]
    attrs: set = set()
    todo = list(roots)
    seen_ids: set = set()

    def class_methods(cls):
        out = []
        for k in cls.__mro__:
            if not _ours(k):
                continue
            for name, v in vars(k).items():
                if name in attrs or (name.startswith("__") and name.endswith("__")):
                    out.append(v)
        return out

    while True:
        while todo:
            o = todo.pop()
            if isinstance(o, tuple) and len(o) == 3 and o[0] == "const":
                key = f"{o[1]}:{o[2]}"
                if key not in parts and o[1] not in PLUMBING_MODULES:
                    m = sys.modules.get(o[1])
                    c = _constant(m, o[2]) if m is not None else None
                    if c is not None and not isinstance(c, tuple):
                        parts[key] = c
                continue
            o = _unwrap(o)
            if id(o) in seen_ids:
                continue
            if isinstance(o, types.ModuleType):
                continue                         # a module itself: only its used attributes count
            if isinstance(o, type):
                if _ours(o):
                    seen_ids.add(id(o))
                    if f"{o.__module__}.{o.__qualname__}" in skip:
                        continue
                    classes.append(o)
                    parts[f"class:{o.__module__}.{o.__qualname__}"] = repr([b.__qualname__ for b in o.__mro__])
                    todo.extend(class_methods(o))
                continue
            fn = o
            if not isinstance(fn, (types.FunctionType, types.MethodType)):
                continue
            if isinstance(fn, types.MethodType):
                fn = fn.__func__
            if not _ours(fn):
                continue
            seen_ids.add(id(fn))
            if f"{fn.__module__}.{fn.__qualname__}" in skip or fn.__module__ in PLUMBING_MODULES:
                continue
            h = _code_sha(fn.__code__)
            h.update(_stable(fn.__defaults__).encode("utf-8"))
            h.update(_stable(fn.__kwdefaults__ or {}).encode("utf-8"))
            key = f"{fn.__module__}.{fn.__qualname__}"
            parts[key] = h.hexdigest()
            if merge:
                same.setdefault(key, set()).add(parts[key])
            todo.extend(_default_refs(fn))       # a default `gpx=stored_course` is code it runs (SP-773)
            objs, a = _refs(fn)
            new_attrs = a - attrs
            attrs |= a
            todo.extend(objs)
            if new_attrs:
                for cls in classes:
                    for k in cls.__mro__:
                        if not _ours(k):
                            continue
                        for name in new_attrs:
                            if name in vars(k):
                                todo.append(vars(k)[name])
        break
    for key, hs in same.items():
        if len(hs) > 1:
            parts[key] = "+".join(sorted(hs))
    return parts


def code_hash(*roots, context: Iterable[type] = (), extra: Any = None, skip: Iterable[str] = (),
              merge: bool = False) -> str:
    """A short hash of the code the roots reach (+ `extra`, e.g. a manual version); `skip`
    and `merge` as in closure()."""
    skip = frozenset(skip)
    key = (tuple(id(r) for r in roots), tuple(id(c) for c in context), repr(extra), tuple(sorted(skip)),
           merge)
    hit = _MEMO.get(key)
    if hit is not None:
        return hit
    out = _digest(closure(roots, context, skip, merge), extra)
    _MEMO[key] = out
    return out


def _digest(parts: dict, extra: Any) -> str:
    h = hashlib.sha1()
    for k in sorted(parts):
        h.update(k.encode("utf-8"))
        h.update(b"=")
        h.update(str(parts[k]).encode("utf-8"))
        h.update(b"\n")
    h.update(_stable(extra).encode("utf-8"))
    return h.hexdigest()[:16]


def _cells(fn) -> dict:
    """{free variable name: value} of a function's closure (an empty cell left out)."""
    out = {}
    for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
        try:
            out[name] = cell.cell_contents
        except ValueError:
            pass
    return out


def _shape(fn, depth: int = 0):
    """What the code of a call-site callable depends on, as a memo key: its module and code and,
    per closure cell, the shape of a function, the name of a module, a class of ours or the class
    of an object of ours. Never the cells' other values or the defaults (inputs, not code)."""
    fn = _unwrap(fn)
    if isinstance(fn, types.MethodType):
        return ("m", fn.__func__.__module__, fn.__func__.__code__, type(fn.__self__))
    if not isinstance(fn, types.FunctionType):
        return ("o", type(fn))
    cells = []
    for name, v in sorted(_cells(fn).items(), key=lambda kv: kv[0]):
        if isinstance(v, (types.FunctionType, types.MethodType)):
            cells.append((name, _shape(v, depth + 1) if depth < 3 else None))
        elif isinstance(v, types.ModuleType):
            cells.append((name, v.__name__))
        elif isinstance(v, type):
            cells.append((name, v if _ours(v) else None))
        elif _ours(type(v)):
            cells.append((name, type(v)))
    return ("f", fn.__module__, fn.__code__, tuple(cells))


def callable_roots(fn) -> tuple[list, list]:
    """(roots, context) for closure() of a call-site callable (see the module doc)."""
    roots: list = []
    context: list = []

    def walk(f, depth):
        f = _unwrap(f)
        if isinstance(f, types.MethodType):
            roots.append(f.__func__)
            if _ours(type(f.__self__)):
                context.append(type(f.__self__))
            return
        if not isinstance(f, types.FunctionType):
            if _ours(type(f)):                   # a callable object of ours
                context.append(type(f))
            return
        # the same code, globals and closure without the defaults (`lambda w=w: …`)
        roots.append(types.FunctionType(f.__code__, f.__globals__, f.__name__, None, f.__closure__))
        cells = _cells(f)
        for co in _codes(f.__code__):            # `A.f` with A a module in a cell
            ins = list(dis.get_instructions(co))
            for a, b in zip(ins, ins[1:]):
                if a.opname == "LOAD_DEREF" and b.opname in ("LOAD_ATTR", "LOAD_METHOD"):
                    m = cells.get(a.argval)
                    if isinstance(m, types.ModuleType) and _ours(m) and hasattr(m, b.argval):
                        roots.append(getattr(m, b.argval))
        for v in cells.values():
            if isinstance(v, (types.FunctionType, types.MethodType)):
                if depth < 3:
                    walk(v, depth + 1)
            elif isinstance(v, type):
                if _ours(v):
                    roots.append(v)
            elif not isinstance(v, types.ModuleType) and _ours(type(v)):
                context.append(type(v))
    walk(fn, 0)
    return roots, context


def callable_hash(fn, context: Iterable[type] = (), extra: Any = None) -> str:
    """code_hash of a call-site callable (callable_roots), memoised on its shape (_shape): a
    lambda made anew for each activity hashes once per process."""
    ctx = tuple(context)
    key = (_shape(fn), ctx, _stable(extra))
    hit = _CALLABLE.get(key)
    if hit is None:
        roots, more = callable_roots(fn)
        hit = _CALLABLE[key] = _digest(closure(roots, list(ctx) + more), extra)
    return hit
