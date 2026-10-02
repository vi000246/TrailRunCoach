"""Real-data half of backend/tests/test_fit_to_channels.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import os
import pytest
from backend.files.fit_to_channels import fit_to_channels
from backend.tests.realdata._paths import ATHLETE_DIR


wko4_files = sorted(ATHLETE_DIR.rglob("*.wko4")) if ATHLETE_DIR.exists() else []


PARITY_STRIDE = int(os.environ.get("WKO5_PARITY_STRIDE", "8"))   # 1 = every file


def _eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-6 * max(1.0, abs(b))


@pytest.mark.skipif(not wko4_files, reason="no WKO5 athlete folder available")
def test_parity_with_wko5_imports():
    import warnings
    from backend.files.wko4_file import read_wko4
    warnings.filterwarnings("ignore")
    checked = same_len = 0
    for p in wko4_files[::PARITY_STRIDE]:
        w = read_wko4(p)
        if w.original_type != "fit" or not w.original_bytes or "elapsedtime" not in w.channels:
            continue
        checked += 1
        fc = fit_to_channels(w.original_bytes)
        wt = w.channels["elapsedtime"].values
        if len(fc.elapsedtime) != len(wt):
            continue  # known device oddities (GPSMAP clock, pool swim, table tennis)
        same_len += 1
        assert all(_eq(a, b) for a, b in zip(fc.elapsedtime, wt)), (p.name, "elapsedtime")
        for ch, vals in fc.channels.items():
            if ch == "power" or ch not in w.channels:
                continue  # power: WKO5 blanks repeated spike values (not modelled)
            wv = w.channels[ch].values
            assert all(_eq(a, b) for a, b in zip(vals, wv)), (p.name, ch)
    assert checked > 20
    assert same_len / checked >= 0.95, (same_len, checked)
