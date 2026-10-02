"""The real-data guard (installed by conftest.py before any backend import).

The default run (`pytest backend/tests`) never touches the athlete's WKO5
folder (~/WKO5) or the app's data folder (~/.wko5coach):

  * home (USERPROFILE / HOME) points at an empty temp folder, so every
    `Path.home() / ...` constant (DB, caches, plan.json, routes, FIT root ...)
    lands there, and the athlete-folder env vars are cleared;
  * an audit hook refuses (PermissionError) any open / listing / write / delete
    of a path under the real ~/WKO5 or ~/.wko5coach and records it, and
    conftest fails the test even when the code under test swallowed the error.

The real-data comparisons (WKO5 is the baseline, never the test data) live in
backend/tests/realdata/ and run only with WKO5COACH_REALDATA=1 (its README).
Then home is still faked and WKO5_ATHLETE_DIR points at the real athlete
folder: reads are allowed, any write under ~/WKO5 or ~/.wko5coach is refused.

Only stdlib here: this module must not import backend code (that would build
the home-based constants before home is faked).
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

REALDATA = os.environ.get("WKO5COACH_REALDATA") == "1"
REAL_HOME = Path.home()
REAL_WKO5 = REAL_HOME / "WKO5"
REAL_APPDATA = REAL_HOME / ".wko5coach"
FAKE_HOME: Path | None = None
violations: list[str] = []

_READ_EVENTS = {"os.listdir", "os.scandir", "glob.glob"}
_WRITE_EVENTS = {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace", "os.truncate",
                 "os.chmod", "os.utime", "os.symlink", "os.link", "shutil.rmtree", "shutil.copyfile",
                 "shutil.copytree", "shutil.move", "shutil.copymode", "shutil.copystat", "sqlite3.connect"}
_TWO_PATHS = ("os.rename", "os.replace", "os.symlink", "os.link", "shutil.copy", "shutil.move")
_PATH_EVENTS = _READ_EVENTS | _WRITE_EVENTS | {"open"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(os.fsdecode(p)))


_GUARDED = [(_norm(REAL_WKO5), "~/WKO5"), (_norm(REAL_APPDATA), "~/.wko5coach")]


def guarded(p) -> str | None:
    """The label of the real folder `p` is under, or None."""
    if p is None or isinstance(p, int):
        return None
    try:
        s = _norm(p)
    except (TypeError, ValueError):
        return None
    for root, label in _GUARDED:
        if s == root or s.startswith(root + os.sep):
            return label
    return None


def _audit(event, args):
    if event not in _PATH_EVENTS:
        return
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                (isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
        paths = (path,)
    elif event.startswith(_TWO_PATHS):
        write, paths = True, tuple(args[:2])
    else:
        write, paths = event in _WRITE_EVENTS, tuple(args[:1])
    for p in paths:
        label = guarded(p)
        if label is None or (REALDATA and not write):
            continue
        msg = f"test touched the real {label}: {event} {os.fsdecode(p)!r}" + (" (write)" if write else "")
        violations.append(msg)
        raise PermissionError(msg)


def _real_athlete_dir() -> str:
    for name in ("WKO5_ATHLETE_DIR", "WKO5COACH_ATHLETE_DIR"):
        if os.environ.get(name):
            return os.environ[name]
    roots = [REAL_WKO5] + (sorted(p for p in REAL_WKO5.iterdir() if p.is_dir()) if REAL_WKO5.is_dir() else [])
    return str(next((d for d in roots if any(d.glob("*.wko5athlete"))), REAL_WKO5))


def install() -> None:
    global FAKE_HOME
    if FAKE_HOME is not None:
        return
    athlete = _real_athlete_dir() if REALDATA else None
    FAKE_HOME = Path(tempfile.mkdtemp(prefix="trc-test-home-"))
    atexit.register(shutil.rmtree, FAKE_HOME, True)
    for k in ("USERPROFILE", "HOME"):
        os.environ[k] = str(FAKE_HOME)
    for k in ("WKO5_ATHLETE_DIR", "WKO5COACH_ATHLETE_DIR", "WKO5COACH_ROUTES_DIR"):
        os.environ.pop(k, None)
    if REALDATA:
        os.environ["WKO5_ATHLETE_DIR"] = athlete
        os.environ["WKO5COACH_TEST_REAL_HOME"] = str(REAL_HOME)        # realdata/_paths.py
    assert Path.home() == FAKE_HOME
    sys.addaudithook(_audit)


def take_violations() -> list[str]:
    got = list(dict.fromkeys(violations))
    del violations[:]
    return got
