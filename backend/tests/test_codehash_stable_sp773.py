"""SP-773: a cache's code hash is the same in every process. A default value that is a function, a
class, a bound method or an `object()` sentinel used to go into the hash as its repr
(`<function stored_course at 0x10ce…>`), whose address changes with every process — so after each
restart or deploy every disk cache whose code reached one (series.db measurements, chart renders)
missed and was recomputed. The hash still changes when the code does: a function's body, its
defaults, the code of a function given as a default."""
import importlib.util
import os
import subprocess
import sys

import pytest

from backend.engine import codehash as C

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LIB = '''
_MISSING = object()


class Kind:
    def label(self):
        return "k"


def target(x):
    return x + 1


def other(x):
    return x - 1


_KIND = Kind()


def root(x, f=target, sentinel=_MISSING, k=Kind, bound=_KIND.label, n=3, fs=(target, other),
         *, by={"t": target}, g=lambda y: y * 2):
    return f(x) + n + g(x)
'''

NAME = "backend._ch773_lib"


def _load(path):
    spec = importlib.util.spec_from_file_location(NAME, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = m
    spec.loader.exec_module(m)
    return m


def _h(tmp_path, variant, src):
    d = tmp_path / variant
    d.mkdir()
    p = d / "_ch773_lib.py"
    p.write_text(src)
    try:
        m = _load(p)
        C._ASSIGN.clear()
        C._MEMO.clear()
        return C.code_hash(m.root)
    finally:
        sys.modules.pop(NAME, None)
        C._ASSIGN.clear()
        C._MEMO.clear()


def _two_processes(code: str) -> set:
    """stdout of `code` run in two fresh interpreters (each one has its own addresses)."""
    outs = set()
    for _ in range(2):
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        assert p.returncode == 0, p.stderr[-2000:]
        outs.add(p.stdout.strip())
    return outs


def test_function_defaults_hash_the_same_in_two_processes(tmp_path):
    p = tmp_path / "_ch773_lib.py"
    p.write_text(LIB)
    code = ("import importlib.util, sys; from backend.engine import codehash as C;"
            f"s = importlib.util.spec_from_file_location({NAME!r}, {str(p)!r});"
            "m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m);"
            "print(C.code_hash(m.root), C._stable(m.root.__defaults__), C._stable(m.root.__kwdefaults__))")
    outs = _two_processes(code)
    assert len(outs) == 1, outs
    assert "0x" not in outs.pop()


@pytest.mark.needs_core
def test_real_cache_roots_hash_the_same_in_two_processes():
    """The roots SP-759 found unstable: the race refs a chart's signature reaches, the drift bars."""
    code = ("import backend.main; from backend.engine import codehash as C;"
            "from backend.engine.panels import race_refs as R, drift_bars as D;"
            "from backend.engine import workout_review as WR, threshold_confidence as TC;"
            "from backend.engine.wko5expr.fitdataset import FitFolderDataset as F;"
            "print(C.code_hash(R.course_of), C.code_hash(R.apply), C.code_hash(D.apply),"
            " C.code_hash(WR._measure, context=[F]), C.code_hash(TC._summary, context=[F]))")
    outs = _two_processes(code)
    assert len(outs) == 1, outs


def test_same_code_loaded_twice_hashes_the_same(tmp_path):
    """Two loads of one module in one process have different addresses too (same failure, cheaper)."""
    assert _h(tmp_path, "a", LIB) == _h(tmp_path, "b", LIB)


def test_real_code_changes_still_change_the_hash(tmp_path):
    base = _h(tmp_path, "a", LIB)
    variants = {
        "body": LIB.replace("return f(x) + n + g(x)", "return f(x) + n + g(x) + 1"),
        "default value": LIB.replace("n=3", "n=4"),
        "default function": LIB.replace("f=target", "f=other"),
        "default class": LIB.replace("k=Kind", "k=int"),
        "default in a tuple": LIB.replace("fs=(target, other)", "fs=(other, target)"),
        "kw default dict": LIB.replace('by={"t": target}', 'by={"t": other}'),
        "default lambda body": LIB.replace("g=lambda y: y * 2", "g=lambda y: y * 3"),
        "code of the default function": LIB.replace("return x + 1", "return x + 2"),
        "code of a bound-method default": LIB.replace('return "k"', 'return "j"'),
    }
    for name, src in variants.items():
        assert src != LIB, name
        assert _h(tmp_path, name.replace(" ", "_"), src) != base, name


def test_unrelated_function_change_keeps_the_hash(tmp_path):
    """Following a default's code must not pull in everything: `other` is only a default of
    `root` through the tuple, so it counts; a function nobody reaches does not."""
    base = _h(tmp_path, "a", LIB)
    assert _h(tmp_path, "b", LIB + "\n\ndef unused():\n    return 1\n") == base


@pytest.mark.needs_core
def test_no_backend_default_hashes_with_an_address():
    """Every function default of `backend` (the 28+ SP-759 listed: `gpx=stored_course`,
    `get=_http_get`, `_UNSET = object()`, FastAPI `Depends(get_db)` …) hashes without an address."""
    import importlib
    import pkgutil
    import re
    import types
    import backend
    addr = re.compile(r"0x[0-9a-fA-F]{4,}")
    bad = []
    for info in pkgutil.walk_packages(backend.__path__, "backend."):
        if ".tests" in info.name or info.name.endswith("__main__"):
            continue
        try:
            mod = importlib.import_module(info.name)
        except Exception:                       # noqa: BLE001 — optional deps, scripts
            continue
        for name, v in list(vars(mod).items()):
            fns = []
            if isinstance(v, types.FunctionType) and v.__module__ == mod.__name__:
                fns.append((name, v))
            elif isinstance(v, type) and v.__module__ == mod.__name__:
                for k, m in vars(v).items():
                    m = getattr(m, "__func__", m)
                    if isinstance(m, types.FunctionType):
                        fns.append((f"{name}.{k}", m))
            for q, f in fns:
                s = C._stable(f.__defaults__) + C._stable(f.__kwdefaults__ or {})
                if addr.search(s):
                    bad.append(f"{mod.__name__}.{q}: {s[:120]}")
    assert not bad, "\n".join(bad)
