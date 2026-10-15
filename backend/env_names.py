"""The environment variables' names (SP-498): TRC_* is the name to set; the old WKO5COACH_*
(and WKO5_ATHLETE_DIR / WKO5_VIEWS_DIR) keep working.

The code still reads the old names (they go with the internal rename, SP-498 B3). So `apply()`,
run once when `backend` is imported (backend/__init__.py), copies every TRC_* name it knows to
its old name and takes the TRC_* one out of the environment again:

  * the new name wins when both are set (the owner's answer: the new name is read first);
  * os.environ then holds only the old names, so a child process whose environment is a copy of
    this one with an old name overridden (backend/demo/build.py child_env, the e2e server) gets
    the override and not the parent's value back through the new name;
  * an old name set alone is used as it is, with one warning that it has a new name.

Only names listed in OLD (or their `_FILE` variant, backend/settings/env_secrets.py) are touched:
other TRC_* variables (TRC_DEBUG_PIN, TRC_DEFAULT_LOCALE …) are names of their own.
backend/tests/test_drop_wko5_name_sp498.py checks that every old name the code reads is listed."""
from __future__ import annotations

import logging
import os
from typing import MutableMapping

NEW_PREFIX = "TRC_"
OLD_PREFIX = "WKO5COACH_"

# the old names the code reads (WKO5COACH_<X> is set as TRC_<X>)
OLD = (
    "WKO5COACH_ACCOUNTS", "WKO5COACH_ATHLETE_DIR", "WKO5COACH_ATHLETE_ID", "WKO5COACH_BG_SYNC",
    "WKO5COACH_CACHE_TENANTS", "WKO5COACH_CHANGE_SYNC",
    "WKO5COACH_COOKIE_SAMESITE", "WKO5COACH_COOKIE_SECURE", "WKO5COACH_DATASET_MEM_MB",
    "WKO5COACH_DEMO_BUILD_STAGE", "WKO5COACH_DEMO_CTA_URL", "WKO5COACH_DEMO_REBUILD", "WKO5COACH_FIT_CACHE",
    "WKO5COACH_FIT_OPEN", "WKO5COACH_FIT_WORKERS", "WKO5COACH_HOME", "WKO5COACH_LOG_FILE", "WKO5COACH_LOG_LEVEL",
    "WKO5COACH_MAX_USERS", "WKO5COACH_MODE", "WKO5COACH_NO_AUTO_BACKUP", "WKO5COACH_NO_RAIN_BACKFILL",
    "WKO5COACH_NO_SCHEDULER", "WKO5COACH_NO_WARMUP", "WKO5COACH_OWNER_EMAIL", "WKO5COACH_OWNER_GOOGLE_SUB",
    "WKO5COACH_PERF_LOG", "WKO5COACH_PROCESS_JOBS", "WKO5COACH_PUBLIC_URL", "WKO5COACH_REALDATA", "WKO5COACH_ROUTES_DIR",
    "WKO5COACH_ROUTES_WEATHER", "WKO5COACH_SECRET_KEY", "WKO5COACH_SERVER_BACKUP_DIR",
    "WKO5COACH_SERVER_BACKUP_FIT", "WKO5COACH_SERVER_ROOT", "WKO5COACH_TAG_SEED", "WKO5COACH_TAGS_DB",
    "WKO5COACH_TEST_REAL_HOME", "WKO5COACH_TEST_TRUE_HOME", "WKO5COACH_THREADS", "WKO5COACH_TRUSTED_PROXIES",
    "WKO5COACH_TZ",
    # the comparison folder and its exported views (no WKO5COACH_ prefix)
    "WKO5_ATHLETE_DIR", "WKO5_VIEWS_DIR",
)
# old names whose new name is not TRC_ + the part after WKO5COACH_
SPECIAL = {"WKO5_ATHLETE_DIR": "TRC_ATHLETE_DIR", "WKO5_VIEWS_DIR": "TRC_VIEWS_DIR"}

_log = logging.getLogger(__name__)


def new_name(old: str) -> str:
    """WKO5COACH_HOME -> TRC_HOME; WKO5_ATHLETE_DIR -> TRC_ATHLETE_DIR."""
    if old in SPECIAL:
        return SPECIAL[old]
    if old.endswith("_FILE") and old[:-5] in SPECIAL:
        return SPECIAL[old[:-5]] + "_FILE"
    return NEW_PREFIX + old[len(OLD_PREFIX):] if old.startswith(OLD_PREFIX) else old


def _pairs() -> list[tuple[str, list[str]]]:
    """(new name, its old names), `_FILE` variants included. TRC_ATHLETE_DIR has two old names:
    WKO5_ATHLETE_DIR is read first (settings/paths.py), so it gets the value too."""
    by_new: dict[str, list[str]] = {}
    for old in OLD:
        for o in (old, old + "_FILE"):
            by_new.setdefault(new_name(o), []).append(o)
    return list(by_new.items())


# set by the app itself for its child processes / the tests: never worth a warning
INTERNAL = ("WKO5COACH_TEST_REAL_HOME", "WKO5COACH_TEST_TRUE_HOME", "WKO5COACH_DEMO_BUILD_STAGE")

OLD_ONLY: list[str] = []         # what apply() found set under an old name only (warn_old_names)


def apply(environ: MutableMapping[str, str] = os.environ) -> list[str]:
    """Copy the new names to the old ones (see the module doc); returns the old names that were
    set without their new name."""
    old_only = []
    for new, olds in _pairs():
        if new in environ:
            v = environ.pop(new)
            for o in olds:
                environ[o] = v
        else:
            old_only += [o for o in olds if o in environ and o not in INTERNAL]
    if environ is os.environ:
        OLD_ONLY[:] = old_only
    return old_only


def warn_old_names() -> None:
    """One warning per old name in use (the server's start-up, after the log is set up)."""
    for o in OLD_ONLY:
        _log.warning("environment variable %s has a new name: %s (the old one still works)", o, new_name(o))
