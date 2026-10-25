"""SP-897: a guard for the registries codehash cannot see.

codehash follows the names a function reads, but a module-level dict / list / tuple of functions (a
registry the code looks its callee up in, `_SECTIONS.get(section)(…)`) counts only as the AST of its
assignment — the names in it, not the code behind them. SP-819: the review card's sections were
called through `workout_review._SECTIONS`, an edit to a section left the render-cache key as it was,
and a deploy kept serving the old card. The convention is that the cache lists such a registry among
its roots (chartscope.KIND_ROOTS, expanded by kind_roots); this test finds the ones left out.

For every cached code hash (13 panel kinds and the chart parts code_signature adds, the estimate /
PD memos, activity_auto's two branches, the per-workout disk caches): each module-level registry a
reached function reads that holds functions / classes of ours the walk did not reach fails, naming
the cache, the registry and what is missing — unless EXEMPT says why it does not matter."""
import sys
import types

import pytest

from backend.engine import codehash as CH
from backend.engine.wko5expr import chartscope as CS
from backend.engine.wko5expr import dataset as D
from backend.engine.wko5expr import fitdataset as FD
from backend.engine.wko5expr.dataset import Dataset
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.fitdataset import FitFolderDataset as F

# (cache or "*", registry "module:name") → why the functions it holds that the walk misses do not
# change that cache's value. Keep it short: a registry whose functions shape the value belongs in
# the cache's roots (KIND_ROOTS), not here.
_WARM_PATH = ("reached only through building a missing Dataset: plan_sessions._compute_inputs → "
              "overview._dataset → wko5views.warm_up → prerender → wko5views.chart, which draws every "
              "chart; the value itself never reads it")
EXEMPT = {
    ("*", "backend.api.activity_auto:BRANCH_ROOTS"):
        "the lambdas only hand activity_auto's own code hash its roots (`_runs` / `_other`, which a "
        "cache reaching them reaches anyway); they compute nothing",
    ("chart review", "backend.engine.panels.activity_charts:MODELS"): _WARM_PATH,
    ("chart readiness", "backend.engine.panels.activity_charts:MODELS"): _WARM_PATH,
    ("chart readiness", "backend.engine.workout_review:_SECTIONS"): _WARM_PATH,
    ("chart post plan_projection", "backend.engine.panels.activity_charts:MODELS"): _WARM_PATH,
    ("chart post plan_projection", "backend.engine.workout_review:_SECTIONS"): _WARM_PATH,
}


def _per_workout(name):
    from backend.engine import bad_activity as BA
    first = {"channel_peaks": Dataset.channel_peaks, "workout_curves": Dataset._workout_curves,
             "power_source": Dataset._compute_power_source, "bad_activity": BA.features}[name]
    return [first, D.read_wko4, D.CorrectionStore.apply]


PER_WORKOUT = ("channel_peaks", "workout_curves", "power_source", "bad_activity")


def _caches() -> dict:
    """{cache: (roots, context, skip)} — the roots each cache hands code_hash."""
    from backend.api import activity_auto as AA
    out = {}
    r, c = FD.estimate_roots()
    out["estimate"] = (r, c, FD.CODE_SKIP)
    r, c = FD.pd_roots()
    out["pd"] = (r, c, FD.CODE_SKIP)
    for b, roots in AA.BRANCH_ROOTS.items():
        out[f"activity_auto.{b}"] = (roots(), [Dataset, F], ())
    for n in PER_WORKOUT:
        out[f"per_workout.{n}"] = (_per_workout(n), [], ())
    # chartscope.code_signature's parts: the Dataset build, the evaluator (dispatch + every fn_*),
    # each panel kind, each post-processing
    out["chart build"] = ([c.__init__ for c in F.__mro__ if "__init__" in vars(c) and CH._ours(c)], [F], ())
    out["chart eval"] = ([v for k, v in vars(Evaluator).items() if k in ("ev", "evaluate") or k.startswith(("ev_", "fn_"))],
                         [Evaluator, F], ())
    for k in CS.KIND_ROOTS:
        out[f"chart {k}"] = (CS.kind_roots(k), [F], ())
    for p, specs in CS.POST_ROOTS.items():
        out[f"chart post {p}"] = ([r for r in (CS._resolve(s) for s in specs) if r is not None], [F], ())
    return out


def _held(v, depth=0) -> list:
    """Functions / classes of ours in a registry value (dict keys and values, list / tuple / set
    items; nested up to three levels: activity_charts.MODELS is a dict of lists of dicts)."""
    if isinstance(v, dict):
        items = list(v.keys()) + list(v.values())
    elif isinstance(v, (list, tuple, set, frozenset)):
        items = list(v)
    else:
        return []
    out = []
    for x in items:
        x = CH._unwrap(x)
        if isinstance(x, types.MethodType):
            x = x.__func__
        if isinstance(x, (types.FunctionType, type)) and CH._ours(x):
            out.append(x)
        elif depth < 2:
            out.extend(_held(x, depth + 1))
    return out


def _name(x) -> str:
    q = f"{x.__module__}.{x.__qualname__}"
    return f"class:{q}" if isinstance(x, type) else q


_LINES: dict = {}


def _written_in(m, attr, fn) -> bool:
    """A lambda written inside the registry's own assignment (`PERIOD_START = {"day": lambda d: …}`):
    the hash holds that assignment's AST, lambda body included, so editing it counts."""
    if not (isinstance(fn, types.FunctionType) and fn.__name__ == "<lambda>"):
        return False
    key = (m.__name__, attr)
    if key not in _LINES:
        import ast
        import inspect
        span = None
        try:
            for node in ast.parse(inspect.getsource(m)).body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    ts = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id == attr for t in ts):
                        span = (node.lineno, node.end_lineno)
        except (OSError, TypeError, SyntaxError):
            pass
        _LINES[key] = span
    span = _LINES[key]
    return (span is not None and fn.__code__.co_filename == getattr(m, "__file__", None)
            and span[0] <= fn.__code__.co_firstlineno <= span[1])


def gaps(cache: str, spec=None) -> dict:
    """{registry: [what it holds that the cache's walk misses]} for one cache (EXEMPT left out)."""
    roots, context, skip = spec or _caches()[cache]
    parts = CH.closure(roots, context, skip)
    out = {}
    for key in parts:
        if key.startswith("class:") or ":" not in key:
            continue
        mod, attr = key.split(":", 1)
        m = sys.modules.get(mod)
        if m is None or not hasattr(m, attr) or ("*", key) in EXEMPT or (cache, key) in EXEMPT:
            continue
        held = [x for x in _held(getattr(m, attr)) if not _written_in(m, attr, x)]
        missing = sorted({_name(x) for x in held} - set(parts) - CH.PLUMBING - set(skip))
        if missing:
            out[key] = missing
    return out


CACHES = sorted(_caches())


def test_the_per_workout_roots_here_are_the_ones_the_caches_hash():
    """_per_workout mirrors dataset.per_workout_code (kept out of the module: editing it would
    change every hash that reaches it)."""
    from backend.engine.wko5expr import fitcache
    for n, parse in zip(PER_WORKOUT, (True, True, False, False)):
        extra = (n, D.PER_WORKOUT_V, fitcache.versions()["parse"] if parse else None)
        assert CH.code_hash(*_per_workout(n), extra=extra) == D.per_workout_code(n), n


@pytest.mark.parametrize("cache", CACHES)
def test_every_registry_a_cache_reads_is_in_its_roots(cache):
    found = gaps(cache)
    assert not found, (f"{cache}: codehash reads these registries but does not reach what they hold — "
                       f"list the registry in the cache's roots (KIND_ROOTS) or say in EXEMPT why it does "
                       f"not matter: " + "; ".join(f"{k} misses {v}" for k, v in found.items()))


def test_it_catches_the_review_sections_left_out(monkeypatch):
    """SP-819's miss again: without `_SECTIONS` in KIND_ROOTS["review"] the guard fails."""
    monkeypatch.setitem(CS.KIND_ROOTS, "review", ["backend.engine.workout_review:review"])
    found = gaps("chart review", (CS.kind_roots("review"), [F], ()))
    assert "backend.engine.workout_review:_SECTIONS" in found
    assert "backend.engine.workout_review._iv_verdict" in found["backend.engine.workout_review:_SECTIONS"]


def test_the_zone_models_are_in_the_activity_and_period_zone_roots():
    """activity_charts.MODELS (a dict of lists of model dicts whose `zones` is a function) is how the
    single-activity zone card and the period-zones panel get each model's table: listed, its
    functions are hashed — editing zones.stryd_rows changes those panels' keys."""
    for kind in ("activity", "periodzones"):
        parts = CH.closure(CS.kind_roots(kind), [F])
        assert "backend.engine.zones.stryd_rows" in parts, kind
