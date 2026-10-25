"""SP-878: codehash keeps what `dis` finds in each code object (`_scan`) and the sha1 of its parts
(`_code_sha`), so the second cache that reaches a function does not scan its bytecode again — the
warm-up's chart code signatures (`wko5views._warm_chart_code`) went from ~3.4 to ~1.9 s local.

What the names point to is still looked up on every walk: a module global replaced at run time (a
test's monkeypatch, which is how the other codehash tests "edit" code) is followed as it is now, so
the memo never keeps an old hash. The hashes are the same as without the memo."""
import dis
import importlib.util
import sys

from backend.engine import codehash as CH
from backend.engine.wko5expr import fitdataset as FD

LIB = '''
def helper(x):
    return x + 1


def other(x):
    return x - 1


def root(x):
    return helper(x) * 2
'''
NAME = "backend._ch878_lib"


def _cold():
    CH._SCAN.clear()
    CH._SHA.clear()
    CH._MEMO.clear()


def _load(tmp_path):
    p = tmp_path / "_ch878_lib.py"
    p.write_text(LIB)
    spec = importlib.util.spec_from_file_location(NAME, p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = m
    spec.loader.exec_module(m)
    return m


def test_the_hashes_are_the_same_with_and_without_the_memo():
    r, c = FD.estimate_roots()
    _cold()
    cold = CH.closure(r, c, FD.CODE_SKIP)
    warm = CH.closure(r, c, FD.CODE_SKIP)
    assert warm == cold
    _cold()
    assert CH.closure(r, c, FD.CODE_SKIP) == cold


def test_a_second_walk_scans_no_bytecode_again(monkeypatch):
    r, c = FD.pd_roots()
    _cold()
    CH.closure(r, c, FD.CODE_SKIP)
    calls = []
    real = dis.get_instructions
    monkeypatch.setattr(CH.dis, "get_instructions", lambda *a, **k: calls.append(1) or real(*a, **k))
    CH.closure(r, c, FD.CODE_SKIP)
    assert calls == []


def test_a_global_replaced_after_the_scan_is_followed(tmp_path, monkeypatch):
    try:
        m = _load(tmp_path)
        _cold()
        before = CH.closure([m.root])
        assert f"{NAME}.helper" in before
        monkeypatch.setattr(m, "helper", m.other)          # the name root reads now means `other`
        after = CH.closure([m.root])                       # root's scan comes from the memo
        assert f"{NAME}.other" in after and f"{NAME}.helper" not in after
        assert CH._digest(after, None) != CH._digest(before, None)
        _cold()
        assert CH.closure([m.root]) == after               # as a cold walk sees it
    finally:
        sys.modules.pop(NAME, None)


def test_a_function_given_new_code_is_scanned_again(tmp_path):
    try:
        m = _load(tmp_path)
        _cold()
        before = CH.closure([m.root])
        m.root.__code__ = m.other.__code__.replace(co_name="root", co_qualname="root")
        after = CH.closure([m.root])
        assert after[f"{NAME}.root"] != before[f"{NAME}.root"]
        assert f"{NAME}.helper" not in after
    finally:
        sys.modules.pop(NAME, None)
