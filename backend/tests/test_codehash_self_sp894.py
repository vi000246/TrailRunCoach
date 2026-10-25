"""SP-894: codehash does not hash itself.

The Dataset build reaches `estimate_code()` → `codehash.code_hash`, so every chart's code
signature, activity_auto, the estimate and PD memos held codehash's own functions: an edit to
codehash.py (SP-811, SP-819) changed all of those keys and every tenant recomputed, though no cached
value comes from how a code version is computed. Now the module is plumbing (PLUMBING_MODULES); an
edit to the code a cache really runs still changes its hash (the other codehash tests)."""
import types

import pytest

from backend.engine import codehash as CH
from backend.engine.wko5expr import chartscope as CS
from backend.engine.wko5expr import fitdataset as FD
from backend.engine.wko5expr.dataset import Dataset
from backend.engine.wko5expr.fitdataset import FitFolderDataset as F


def _roots() -> dict:
    """{cache: (roots, context, skip, extra)} as each cache hands them to code_hash."""
    from backend.api import activity_auto as AA
    out = {}
    r, c = FD.estimate_roots()
    out["estimate"] = (r, c, FD.CODE_SKIP, ("estimate", FD.ESTIMATE_CODE_V))
    r, c = FD.pd_roots()
    out["pd"] = (r, c, FD.CODE_SKIP, ("pd", FD.ESTIMATE_CODE_V))
    for b, roots in AA.BRANCH_ROOTS.items():
        out[f"activity_auto.{b}"] = (roots(), [Dataset, F], (), (b, AA.CACHE_V))
    out["chart build"] = ([c.__init__ for c in F.__mro__ if "__init__" in vars(c) and CH._ours(c)], [F], (), None)
    for k in ("review", "z5gate", "readiness", "athlete"):
        out[f"chart {k}"] = (CS.kind_roots(k), [F], (), None)
    return out


def _digests() -> dict:
    return {k: CH._digest(CH.closure(r, c, s), e) for k, (r, c, s, e) in _roots().items()}


def test_no_cache_hashes_codehash_itself():
    for cache, (r, c, s, _) in _roots().items():
        own = [k for k in CH.closure(r, c, s) if k.startswith(("backend.engine.codehash.", "backend.engine.codehash:"))]
        assert not own, (cache, own[:5])


def _edited(fn):
    """`fn` with another docstring: its compiled code differs, what it does does not."""
    co = fn.__code__
    new = types.FunctionType(co.replace(co_consts=("an edited docstring",) + co.co_consts[1:]),
                             fn.__globals__, fn.__name__, fn.__defaults__, fn.__closure__)
    new.__kwdefaults__ = fn.__kwdefaults__
    new.__qualname__, new.__module__ = fn.__qualname__, fn.__module__
    return new


@pytest.mark.parametrize("name", ["code_hash", "closure", "_stable"])
def test_editing_codehash_keeps_every_cache_hash(monkeypatch, name):
    before = _digests()
    monkeypatch.setattr(CH, name, _edited(getattr(CH, name)))
    after = _digests()
    assert after == before


def test_the_chart_signature_and_the_memos_keep_their_keys(monkeypatch):
    """End to end: chartscope.code_signature, estimate_code(), pd_code() with code_hash edited."""
    ds = object.__new__(F)

    def now():
        CH._MEMO.clear()
        CS._CODE.clear()
        return ([CS.code_signature({"kind": k}, ds) for k in ("review", "z5gate", "athlete")],
                FD.estimate_code(), FD.pd_code())

    before = now()
    monkeypatch.setattr(CH, "code_hash", _edited(CH.code_hash))
    after = now()
    CH._MEMO.clear()
    CS._CODE.clear()
    assert after == before


def test_an_edit_to_code_a_cache_runs_still_changes_it(monkeypatch):
    """The plumbing rule is by module: the estimate's own code still counts."""
    r, c = FD.estimate_roots()
    before = CH._digest(CH.closure(r, c, FD.CODE_SKIP), None)
    monkeypatch.setattr(F, "_estimate_settings", _edited(F._estimate_settings))
    r, c = FD.estimate_roots()
    assert CH._digest(CH.closure(r, c, FD.CODE_SKIP), None) != before
