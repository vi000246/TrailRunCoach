"""SP-320 ①: code-version hashes cover only the code a cache's value comes from
(engine/codehash.py): an unrelated edit keeps the hash, an edit to a reached function or
constant changes it. Fixture modules are written to tmp_path and imported under `backend.`."""
import importlib.util
import sys

from backend.engine import codehash as C

LIB = '''
LIMIT = 10
OTHER = 5


def helper(x):
    return min(x, LIMIT)


def unrelated(x):
    return x * OTHER


class Box:
    def __init__(self, v):
        self.v = v

    def size(self):
        return helper(self.v)

    def color(self):
        return "red"
'''

MAIN = '''
from backend._ch_lib_{tag} import Box


def root(x):
    from backend import _ch_lib_{tag} as L
    return L.helper(x) + Box(x).size() + (lambda y: y + 1)(x)
'''


def _load(tmp_path, variant, lib_src):
    tag = "x"                                  # same module names for every variant
    d = tmp_path / variant
    d.mkdir()
    for name, src in ((f"_ch_lib_{tag}", lib_src), (f"_ch_main_{tag}", MAIN.format(tag=tag))):
        p = d / f"{name}.py"
        p.write_text(src)
        spec = importlib.util.spec_from_file_location(f"backend.{name}", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules[f"backend.{name}"] = m
        spec.loader.exec_module(m)
    C._ASSIGN.clear()
    C._MEMO.clear()
    return sys.modules[f"backend._ch_main_{tag}"]


def _h(tmp_path, tag, src):
    m = _load(tmp_path, tag, src)
    try:
        return C.code_hash(m.root)
    finally:
        for k in [k for k in sys.modules if k.startswith(f"backend._ch_")]:
            del sys.modules[k]


def test_reached_code_and_constants_change_the_hash(tmp_path):
    base = _h(tmp_path, "a", LIB)
    assert _h(tmp_path, "b", LIB) == base                                              # same code
    assert _h(tmp_path, "c", LIB + "\n# a comment\n\ndef added():\n    return 1\n") == base  # unrelated
    assert _h(tmp_path, "d", LIB.replace("return x * OTHER", "return x * OTHER * 2")) == base
    assert _h(tmp_path, "e", LIB.replace("OTHER = 5", "OTHER = 6")) == base            # unread constant
    assert _h(tmp_path, "f", LIB.replace('return "red"', 'return "blue"')) == base     # uncalled method
    assert _h(tmp_path, "g", LIB.replace("min(x, LIMIT)", "max(x, LIMIT)")) != base    # reached function
    assert _h(tmp_path, "h", LIB.replace("LIMIT = 10", "LIMIT = 11")) != base          # reached constant
    assert _h(tmp_path, "i", LIB.replace("self.v = v", "self.v = v + 1")) != base      # constructor


def test_real_cache_roots_skip_unrelated_code():
    """The caches' roots reach their computations but not unrelated functions."""
    from backend.api import activity_auto as AA
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import Dataset
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    auto = C.closure([AA.compute_blocking], context=[Dataset, FitFolderDataset])
    est = C.closure([FitFolderDataset._estimate_settings, FitFolderDataset._estimate_cp],
                    context=[FitFolderDataset])
    assert "backend.engine.racepower.athlete.capacity_samples" in auto
    assert "backend.engine.thresholds.estimate" in est and "backend.engine.racepower.athlete.cp_as_of" in est
    assert "backend.engine.racepower.athlete.pd_model" in est
    for parts in (auto, est):
        assert "backend.engine.racepower.athlete.climb_cadence_seconds" not in parts
    a_fns = [n for n, v in vars(A).items() if callable(v) and getattr(v, "__module__", "") == A.__name__]
    assert sum(f"{A.__name__}.{n}" in est for n in a_fns) < len(a_fns) / 2


def test_signatures_are_stable_within_a_process():
    from backend.api import activity_auto as AA
    from backend.engine.wko5expr import fitdataset as FD
    assert FD.estimate_code() == FD.estimate_code() and FD.pd_code() == FD.pd_code()
    AA._CODE.clear()
    a = AA._code_sig()
    AA._CODE.clear()
    assert AA._code_sig() == a and a.startswith(f"{AA.CACHE_V}:")


def test_render_signature_ignores_mtime(tmp_path, monkeypatch):
    import os
    from backend.engine.wko5expr import render_cache as RC
    f = tmp_path / "wko5views.py"
    f.write_text("x = 1\n")
    monkeypatch.setattr(RC, "_ENGINE_GLOBS", [(tmp_path, "wko5views.py")])
    a = RC._compute_code_signature()
    os.utime(f, (1, 1))                       # a deploy rewrites the file, same bytes
    assert RC._compute_code_signature() == a
    f.write_text("x = 2\n")
    assert RC._compute_code_signature() != a


def test_hashes_do_not_depend_on_the_hash_seed():
    """A frozenset constant / default reprs in seed order: the hash must not (two processes,
    a restart, two workers would disagree and drop every entry)."""
    import os
    import subprocess
    code = ("from backend.engine.wko5expr import fitdataset as FD; from backend.api import activity_auto as AA;"
            "print(FD.estimate_code(), FD.pd_code(), AA._code_sig())")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    outs = {subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "2")}
    assert len(outs) == 1
