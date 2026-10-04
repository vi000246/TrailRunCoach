"""Golden: `tisaerobic` / `tisanaerobic` (docs/wko5-internals/formulas.md §6.10) against the
per-workout scores WKO5 cached itself in the athlete's Cache5 (the two caches whose expression
starts `workoutrange(begintime,endtime,@lookback:=90`; the one with `@height` is aerobic).
Opt-in: see backend/tests/realdata/README.md.

Thresholds (2026-10-04: aerobic 359/361 equal, anaerobic 345/361 equal; of the 16 anaerobic
misses 15 are one level off and one (a 2026-09 run) two, not explained yet): the anaerobic score divides by FRC, which moves more with small PD-fit differences than
mFTP does, so a score near .5 can land one level apart. tl() of the scores is checked by the
CTL/ATL parity in test_real_wko5_pipeline.py, so these per-workout scores are the whole gap."""
import pytest

from backend.tests.realdata._paths import ATHLETE_DIR

CACHE = ATHLETE_DIR / "Cache5"
PREFIX = "workoutrange(begintime,endtime,@lookback:=90"
pytestmark = pytest.mark.golden


def _cached_scores() -> dict[str, dict[str, float]]:
    """{"aerobic" | "anaerobic": {workout file (relative): WKO5's score}} (workouts with a value)."""
    from backend.files.wko5chart_reader import decode_file, Record
    from backend.files.wko4_file import decode_channel
    out: dict[str, dict[str, float]] = {}
    for p in CACHE.glob("*.wko5cache"):
        try:
            f = decode_file(p)
        except Exception:                      # noqa: BLE001 — not a chart cache
            continue
        root = f.fields[0].value if f.fields else None
        expr = root.get(461) if isinstance(root, Record) else None
        if not isinstance(expr, str) or not expr.replace(" ", "").startswith(PREFIX):
            continue
        kind = "aerobic" if "@height" in expr else "anaerobic"
        scores = out.setdefault(kind, {})
        for e in root.get(601).all(102):
            for c in (e.get(116).all(4403) if isinstance(e.get(116), Record) else []):
                body = c.get(102)
                blk = body.get(121) if isinstance(body, Record) else None
                if c.get(101) == "y" and isinstance(blk, bytes):
                    ys = decode_channel("y", blk).values
                    if ys and ys[0] is not None:
                        scores[e.get(117).split(":", 1)[-1]] = float(ys[0])
    return out


@pytest.fixture(scope="module")
def cached():
    if not CACHE.exists():
        pytest.skip("no WKO5 athlete folder")
    got = _cached_scores()
    if set(got) != {"aerobic", "anaerobic"}:
        pytest.skip("no cached TIS charts in Cache5 (open a TIS chart in WKO5 once)")
    return got


@pytest.fixture(scope="module")
def ds():
    from backend.engine.wko5expr.dataset import Dataset
    return Dataset(ATHLETE_DIR)


@pytest.fixture(scope="module")
def ours(cached, ds):
    from backend.engine.wko5expr.evaluator import Evaluator
    assert ds.config.parity                    # WKO5's numbers: no data corrections
    ev = Evaluator(ds, ds.first_day, ds.today)
    by_file = {w.entry.file.replace("\\", "/"): w for w in ds.workouts}
    out = {}
    for kind, expr in (("aerobic", "tisaerobic"), ("anaerobic", "tisanaerobic")):
        out[kind] = {f: (ev.evaluate(expr, by_file[f]) if f in by_file else None) for f in cached[kind]}
    return out


def _compare(cached, ours, kind):
    same, off, missing = 0, [], []
    for f, want in cached[kind].items():
        got = ours[kind][f]
        if got is None or got != got:          # not in our dataset / na
            missing.append(f)
        elif got == want:
            same += 1
        else:
            off.append((f, want, got))
    return same, off, missing


@pytest.mark.parametrize("kind, min_equal", [("aerobic", 0.99), ("anaerobic", 0.95)])
def test_tis_matches_wko5_cached_scores(cached, ours, kind, min_equal):
    same, off, missing = _compare(cached, ours, kind)
    n = len(cached[kind])
    assert n >= 50, n
    assert len(missing) <= max(2, n // 100), missing
    assert same >= min_equal * n, (same, n, off[:10])
    big = [(f, want, got) for f, want, got in off if abs(want - got) > 1]
    assert len(big) <= 1 and all(abs(want - got) <= 2 for _f, want, got in big), big


def test_bundled_tis_charts_render(cached, ds):
    """我的訓練 › 負荷 PMC: the two TIS charts draw numbers over the last year, no series errors."""
    import json
    from pathlib import Path
    from backend.engine.wko5expr.customviews import parse_view
    from backend.engine.wko5expr.render import render_chart
    p = Path(__file__).resolve().parents[3] / "views" / "training.json"
    d = next(d for d in parse_view(json.loads(p.read_text("utf-8")), p)["dashboards"] if d["id"] == "load-pmc")
    for c in (c for c in d["charts"] if c["id"] in ("tis-per-workout", "tis-load")):
        res = render_chart(c, ds, ds.today - 365, ds.today)
        for s in res["series"]:
            assert s["data"]["kind"] == "points", (c["id"], s["name"], s["data"])
