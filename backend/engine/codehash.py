"""
Code-version hashes that cover only the code a cache's value comes from (SP-320 ①).

A disk cache key used to hash whole module sources (`inspect.getsource(module)` of
athlete.py, workout_review.py …): any edit anywhere in those modules — a comment, an
unrelated function — threw every entry away on deploy (~2.5 NAS CPU-min per tenant).
`code_hash(*roots)` instead walks the functions the roots can reach and hashes those:

* functions / methods: the compiled code (bytecode, constants, names, defaults), not the
  source text — comments, blank lines and edits elsewhere in the file don't count;
* reached through a global name, a module attribute (`A.pd_model`, also after a local
  `from … import … as A`), a nested function / lambda / comprehension, or a method name
  called on any object (`ds.cached_series(…)`) when that method belongs to a reached
  class or to one of the `context` classes;
* module-level constants a reached function reads (`WINDOWS`, `A.CP_WINDOW_DAYS`): the
  AST of their top-level assignment, so editing the value changes the hash;
* code outside `backend` (numpy, the stdlib …) is not followed.

Dynamic lookups (`getattr(m, name)`, a registry filled at import time) are not seen: every
caller keeps a manual version constant next to the hash for those, and a test pins the
roots of each cache.
"""
from __future__ import annotations

import ast
import dis
import functools
import hashlib
import importlib
import inspect
import sys
import types
from typing import Any, Iterable, Optional

PKG = "backend"

_MEMO: dict = {}
_ASSIGN: dict = {}


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


def _stable(x) -> str:
    """repr with set / dict order fixed: a frozenset's repr follows the per-process hash
    seed (PYTHONHASHSEED), and `x in {"a", "b"}` compiles to a frozenset constant."""
    if isinstance(x, (set, frozenset)):
        return f"{type(x).__name__}({sorted(_stable(v) for v in x)})"
    if isinstance(x, dict):
        return "{" + ",".join(sorted(f"{_stable(k)}:{_stable(v)}" for k, v in x.items())) + "}"
    if isinstance(x, (list, tuple)):
        return f"{type(x).__name__}[" + ",".join(_stable(v) for v in x) + "]"
    return repr(x)


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


def _refs(fn) -> tuple[list, set]:
    """(objects a function refers to, attribute names it reads) — globals, module attributes
    after `X.attr`, locally imported names; nested code included."""
    co = fn.__code__
    g = getattr(fn, "__globals__", {}) or {}
    objs: list = []
    attrs: set = set()
    local: dict = {}
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
                        local[nxt.argval] = _resolve_import(pending.split(".")[0], None)
            elif op == "IMPORT_FROM" and pending:
                nxt = ins[i + 1] if i + 1 < len(ins) else None
                obj = _resolve_import(pending, x.argval)
                if nxt is not None and nxt.opname.startswith("STORE"):
                    local[nxt.argval] = obj
                if obj is not None:
                    objs.append(obj)
            elif op in ("LOAD_GLOBAL", "LOAD_NAME"):
                if x.argval in g:
                    objs.append(g[x.argval])
                    if not callable(g[x.argval]) and not isinstance(g[x.argval], types.ModuleType):
                        objs.append(("const", fn.__module__, x.argval))
                else:
                    objs.append(("const", fn.__module__, x.argval))
            elif op in ("LOAD_ATTR", "LOAD_METHOD"):
                attrs.add(x.argval)
                prev = ins[i - 1] if i else None
                if prev is not None:
                    base = None
                    if prev.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                        base = g.get(prev.argval)
                    elif prev.opname in ("LOAD_FAST", "LOAD_DEREF", "LOAD_CLOSURE"):
                        base = local.get(prev.argval)
                    if isinstance(base, types.ModuleType) and _ours(base):
                        if hasattr(base, x.argval):
                            v = getattr(base, x.argval)
                            objs.append(v)
                            if not callable(v) and not isinstance(v, types.ModuleType):
                                objs.append(("const", base.__name__, x.argval))
                    elif isinstance(base, type) and _ours(base) and x.argval in vars(base):
                        objs.append(vars(base)[x.argval])
    return objs, attrs


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


def closure(roots: Iterable[Any], context: Iterable[type] = ()) -> dict:
    """{qualified name: hash part} of everything the roots reach (see the module doc)."""
    parts: dict = {}
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
                if key not in parts:
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
            h = hashlib.sha1()
            for p in _code_parts(fn.__code__):
                h.update(p if isinstance(p, bytes) else _stable(p).encode("utf-8"))
                h.update(b"\x00")
            h.update(_stable(fn.__defaults__).encode("utf-8"))
            h.update(_stable(fn.__kwdefaults__ or {}).encode("utf-8"))
            parts[f"{fn.__module__}.{fn.__qualname__}"] = h.hexdigest()
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
    return parts


def code_hash(*roots, context: Iterable[type] = (), extra: Any = None) -> str:
    """A short hash of the code the roots reach (+ `extra`, e.g. a manual version)."""
    key = (tuple(id(r) for r in roots), tuple(id(c) for c in context), repr(extra))
    hit = _MEMO.get(key)
    if hit is not None:
        return hit
    parts = closure(roots, context)
    h = hashlib.sha1()
    for k in sorted(parts):
        h.update(k.encode("utf-8"))
        h.update(b"=")
        h.update(str(parts[k]).encode("utf-8"))
        h.update(b"\n")
    h.update(_stable(extra).encode("utf-8"))
    out = h.hexdigest()[:16]
    _MEMO[key] = out
    return out
