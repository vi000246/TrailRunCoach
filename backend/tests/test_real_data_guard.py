"""The conftest guard (_guard.py): the default run never sees the real ~/WKO5 or ~/.wko5coach."""
import os
import sqlite3
from pathlib import Path

import pytest

from backend.tests import _guard as G

pytestmark = pytest.mark.skipif(G.REALDATA, reason="the real-data run may read ~/WKO5")


@pytest.fixture
def clean():
    yield
    G.take_violations()           # the probes below trip the guard on purpose


def test_home_is_a_temp_folder_and_module_paths_follow_it():
    assert G.FAKE_HOME is not None and Path.home() == G.FAKE_HOME != G.REAL_HOME
    from backend.db.database import DB_PATH
    from backend.engine.planning import PLAN_PATH
    from backend.engine.wko5expr.config import CONFIG_PATH
    from backend.engine.wko5expr.render_cache import CACHE_DIR
    from backend.settings.paths import athlete_dir
    for p in (DB_PATH, PLAN_PATH, CONFIG_PATH, CACHE_DIR, athlete_dir()):
        assert G.FAKE_HOME in Path(p).parents, p
    assert "WKO5_ATHLETE_DIR" not in os.environ


@pytest.mark.parametrize("sub", ["WKO5", ".wko5coach"])
def test_reading_listing_and_writing_the_real_folders_is_refused(sub, clean):
    root = G.REAL_HOME / sub
    with pytest.raises(PermissionError):
        open(root / "anything.json", encoding="utf-8")
    with pytest.raises(PermissionError):
        os.listdir(root)
    assert list(root.glob("*.wko5athlete")) == []        # pathlib swallows the PermissionError ...
    assert len(G.violations) == 3                        # ... the guard still saw it
    with pytest.raises(PermissionError):
        (root / "cache.json").write_text("{}", "utf-8")
    with pytest.raises(PermissionError):
        sqlite3.connect(str(root / "x.db"))
    assert len(G.violations) == 5


def test_a_swallowed_error_is_still_recorded(clean):
    try:
        (G.REAL_HOME / ".wko5coach" / "series_x.json").write_text("{}", "utf-8")
    except OSError:
        pass                      # code that hides the failure (a best-effort cache write) ...
    assert G.violations           # ... still fails the test in conftest's teardown check


def test_other_paths_are_untouched(tmp_path):
    (tmp_path / "a.json").write_text("{}", "utf-8")
    assert os.listdir(tmp_path) == ["a.json"]
    assert G.guarded(G.REAL_HOME / "WKO5x") is None
    assert G.guarded(G.REAL_HOME / "WKO5" / "a") == "~/WKO5"
