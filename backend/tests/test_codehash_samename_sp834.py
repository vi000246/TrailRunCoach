"""SP-834: functions sharing a qualified name (every dataclass's generated `__repr__`, module-level
lambdas) all count in a code hash with `merge=True`, whatever order the walk reaches them; the
chart signatures use it, so z5gate's no longer follows the hash seed."""
import os
import subprocess
import sys
import types

import pytest

from backend.engine import codehash as C

SRC = '''
from dataclasses import dataclass


@dataclass
class First:
    a: int


@dataclass
class Second:
    b: int


double = lambda x: x * 2
triple = lambda x: x * 3
'''


def _mod(monkeypatch, src):
    m = types.ModuleType("backend._ch_sp834")
    m.__file__ = "_ch_sp834.py"
    monkeypatch.setitem(sys.modules, m.__name__, m)
    exec(compile(src, m.__file__, "exec"), m.__dict__)
    C._MEMO.clear()
    C._ASSIGN.clear()
    return m


def _hashes(monkeypatch, src, merge):
    m = _mod(monkeypatch, src)
    return {"classes": (C.code_hash(m.First, m.Second, merge=merge), C.code_hash(m.Second, m.First, merge=merge)),
            "lambdas": (C.code_hash(m.double, m.triple, merge=merge), C.code_hash(m.triple, m.double, merge=merge))}


def test_the_walk_order_does_not_matter(monkeypatch):
    on = _hashes(monkeypatch, SRC, merge=True)
    assert on["classes"][0] == on["classes"][1] and on["lambdas"][0] == on["lambdas"][1]
    off = _hashes(monkeypatch, SRC, merge=False)          # the bug: only the last one reached counts
    assert off["lambdas"][0] != off["lambdas"][1]


@pytest.mark.parametrize("edit", [
    ("class First:\n    a: int\n", "class First:\n    a: int\n    c: int = 0\n"),
    ("class Second:\n    b: int\n", "class Second:\n    b: int\n    c: int = 0\n"),
    ("x * 2", "x * 4"),
    ("x * 3", "x * 5"),
])
def test_an_edit_to_any_of_them_changes_the_hash(monkeypatch, edit):
    kind = "lambdas" if "x *" in edit[0] else "classes"
    base = _hashes(monkeypatch, SRC, merge=True)[kind]
    after = _hashes(monkeypatch, SRC.replace(*edit), merge=True)[kind]
    assert after[0] != base[0] and after[1] != base[1]


def test_names_that_do_not_collide_keep_their_value(monkeypatch):
    m = _mod(monkeypatch, SRC)
    on, off = C.closure([m.First], merge=True), C.closure([m.First], merge=False)
    assert on == off


@pytest.mark.needs_core
def test_chart_signatures_do_not_depend_on_the_hash_seed():
    code = ("from backend.engine.wko5expr import chartscope as CS;"
            "from backend.engine.wko5expr.fitdataset import FitFolderDataset as F;"
            "ds = object.__new__(F);"
            "print(sorted((k, CS.code_signature({'kind': k}, ds)) for k in CS.KIND_ROOTS))")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    outs = {subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}).stdout
            for seed in ("1", "2", "3")}
    assert len(outs) == 1 and "z5gate" in outs.pop()
