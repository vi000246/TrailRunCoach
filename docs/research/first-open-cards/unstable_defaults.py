"""
SP-759: functions of `backend` whose default values have a per-process repr (a function, a class
instance: `<function f at 0x…>`). engine/codehash.py hashes `_stable(fn.__defaults__)`, so every disk
cache whose code closure reaches one of these gets a new code version on every server start.

    python unstable_defaults.py      (from the repo root)
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import backend  # noqa: E402
from backend.engine.codehash import _stable  # noqa: E402

ADDR = re.compile(r" at 0x[0-9a-f]+|object at ")


def funcs(mod):
    for name, v in vars(mod).items():
        if isinstance(v, types.FunctionType) and v.__module__ == mod.__name__:
            yield f"{mod.__name__}.{name}", v
        elif isinstance(v, type) and v.__module__ == mod.__name__:
            for k, m in vars(v).items():
                m = getattr(m, "__func__", m)
                if isinstance(m, types.FunctionType):
                    yield f"{mod.__name__}.{v.__qualname__}.{k}", m


def main() -> int:
    bad = []
    for info in pkgutil.walk_packages(backend.__path__, "backend."):
        if ".tests" in info.name or info.name.endswith("__main__"):
            continue
        try:
            mod = importlib.import_module(info.name)
        except Exception:                   # noqa: BLE001 — optional deps, scripts
            continue
        for q, f in funcs(mod):
            s = _stable(f.__defaults__) + _stable(f.__kwdefaults__ or {})
            if ADDR.search(s):
                try:
                    line = inspect.getsourcelines(f)[1]
                except (OSError, TypeError):
                    line = 0
                src = Path(inspect.getsourcefile(f) or "?").resolve()
                try:
                    src = src.relative_to(REPO)
                except ValueError:
                    pass
                bad.append((q, f"{src}:{line}", ADDR.sub(" at 0x…", s)[:120]))
    for q, where, s in sorted(set(bad)):
        print(f"{q}\t{where}\t{s}")
    print(f"{len(set(bad))} functions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
