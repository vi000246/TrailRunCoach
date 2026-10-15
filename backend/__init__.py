# recorded in backups (engine/backup.py manifest); bump on releases
__version__ = "2026.10.0"

# the environment variables' new names (TRC_*) next to the old ones (WKO5COACH_*), before any
# module reads them (backend/env_names.py, SP-498)
from backend import env_names as _env_names  # noqa: E402

_env_names.apply()
