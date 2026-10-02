"""Opt-in real-data suite: runs only with WKO5COACH_REALDATA=1 (README.md)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WKO5COACH_REALDATA") == "1":
        return
    skip = pytest.mark.skip(reason="real-data suite: set WKO5COACH_REALDATA=1 (backend/tests/realdata/README.md)")
    here = Path(__file__).resolve().parent
    for it in items:
        if here in Path(str(it.fspath)).resolve().parents:
            it.add_marker(skip)


def pytest_runtest_setup(item):
    if os.environ.get("WKO5COACH_REALDATA") != "1":
        return                    # the skip marker above
    from backend.tests.realdata._paths import ATHLETE_DIR
    if not any(ATHLETE_DIR.glob("*.wko5athlete")):
        pytest.skip(f"no WKO5 athlete folder at {ATHLETE_DIR}")
