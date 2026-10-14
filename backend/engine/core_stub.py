"""
Stand-ins for the calculation core in the public mirror (SP-500).

The full program lives in the private repo. The public repo is a mirror of it
without the calculation core (deploy/public-mirror/exclude.txt, in the private
repo only): every core module that is left out is replaced by a small stub
module of this shape

    # PUBLIC-MIRROR-STUB …
    from backend.engine.core_stub import module_getattr as _module_getattr
    CORE_STUB = True
    __getattr__ = _module_getattr(__name__)

so `from backend.engine.wko5expr.dataset import Dataset` still imports, the app
starts and the pages open; anything that USES the core raises CoreNotIncluded,
which the API answers with 503 「核心未包含（公開版）」 (backend/main.py) and the
tests skip (backend/tests/conftest.py).

What a missing name stands for, from its spelling:
  - ...Error / ...Exception / ...Warning  → an Exception subclass (so `except X:` works);
  - CapWords                               → a class; making one raises CoreNotIncluded,
                                             subclassing it works;
  - anything else (functions, constants)   → a stand-in that raises CoreNotIncluded when
                                             called, iterated, compared or used in arithmetic.

In the private repo nothing imports this module except core_included() callers and
its tests; it changes nothing there.
"""
from __future__ import annotations

import importlib
import sys

from backend.i18n import N_

MESSAGE = N_("核心未包含（公開版）")
MARKER = "# PUBLIC-MIRROR-STUB"

# how many times a stand-in was used (the test hooks compare before / after a test)
HITS = 0


class CoreNotIncluded(RuntimeError):
    """A part of the calculation core was used in the public mirror, which does not have it."""

    def __init__(self, what: str = ""):
        self.what = what
        super().__init__(f"{MESSAGE}: {what}" if what else MESSAGE)


def _hit(what: str):
    global HITS
    HITS += 1
    raise CoreNotIncluded(what)


def core_included() -> bool:
    """True with the real core (the private repo), False in the public mirror."""
    try:
        mod = importlib.import_module("backend.engine.wko5expr")
    except ImportError:
        return False
    return not getattr(mod, "CORE_STUB", False)


def is_core_missing(exc: BaseException) -> bool:
    """`exc` (or what caused it) is a CoreNotIncluded, or an error about a stand-in
    (os.environ refusing one: "str expected, not Placeholder"; a KeyError on one)."""
    seen = 0
    while exc is not None and seen < 20:
        if isinstance(exc, CoreNotIncluded):
            return True
        try:
            text = str(exc)
        except Exception:                      # noqa: BLE001 — a broken __str__ is not ours
            text = ""
        if "Placeholder" in text or MESSAGE in text:
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


class _StubMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return Placeholder(f"{cls.__module__}.{cls.__qualname__}.{name}")


class StubClass(metaclass=_StubMeta):
    """Base of the stand-in classes: making one raises CoreNotIncluded."""

    __core_stub__ = True

    def __init__(self, *args, **kwargs):
        _hit(f"{type(self).__module__}.{type(self).__qualname__}")


def _raiser(op):
    def f(self, *args, **kwargs):
        _hit(f"{self._what} ({op})")
    f.__name__ = op
    return f


class Placeholder:
    """A missing function or constant: using it raises CoreNotIncluded."""

    __slots__ = ("_what",)

    def __init__(self, what: str):
        object.__setattr__(self, "_what", what)

    def __call__(self, *args, **kwargs):
        _hit(self._what)

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return Placeholder(f"{self._what}.{name}")

    def __mro_entries__(self, bases):
        # `class X(placeholder):` — subclass a stand-in class instead
        return (stub_class(*self._what.rsplit(".", 1)),)

    def __repr__(self):
        return f"<{MESSAGE}: {self._what}>"

    def __bool__(self):
        _hit(f"{self._what} (bool)")

    def __hash__(self):
        return hash(("core-stub", self._what))

    def __eq__(self, other):
        return isinstance(other, Placeholder) and other._what == self._what

    def __ne__(self, other):
        return not self.__eq__(other)


for _op in ("__iter__", "__len__", "__getitem__", "__setitem__", "__contains__", "__lt__", "__le__",
            "__gt__", "__ge__", "__add__", "__radd__", "__sub__", "__rsub__", "__mul__", "__rmul__",
            "__truediv__", "__rtruediv__", "__floordiv__", "__mod__", "__pow__", "__neg__", "__float__",
            "__int__", "__index__", "__enter__", "__exit__", "__fspath__"):
    setattr(Placeholder, _op, _raiser(_op))


_CLASSES: dict[tuple[str, str], type] = {}


def stub_class(module: str, name: str) -> type:
    key = (module, name)
    if key not in _CLASSES:
        _CLASSES[key] = _StubMeta(name, (StubClass,), {"__module__": module, "__qualname__": name})
    return _CLASSES[key]


def stub_attr(module: str, name: str):
    """The stand-in for `module.name`, from the name's spelling (see the module docstring)."""
    if name.endswith(("Error", "Exception", "Warning")):
        return type(name, (Exception,), {"__module__": module, "__qualname__": name})
    if name[:1].isupper() and not name.isupper() and "_" not in name:
        return stub_class(module, name)
    return Placeholder(f"{module}.{name}")


# names tools look up to see whether a module defines them (pytest: pytest_plugins, setup_module…)
_PROBED = ("pytest", "setup", "teardown", "setUp", "tearDown", "_pytest", "_ipython", "_repr_")


def module_getattr(module_name: str):
    """The module-level __getattr__ of a stub module: a package first tries its real
    submodule (the mirror keeps some, e.g. wko5expr/datasource.py), then a stand-in."""

    def __getattr__(name: str):
        if (name.startswith("__") and name.endswith("__")) or name.startswith(_PROBED):
            raise AttributeError(name)
        mod = sys.modules[module_name]
        if hasattr(mod, "__path__"):
            try:
                return importlib.import_module(f"{module_name}.{name}")
            except ModuleNotFoundError as e:
                if e.name != f"{module_name}.{name}":
                    raise
        value = stub_attr(module_name, name)
        setattr(mod, name, value)
        return value

    return __getattr__

