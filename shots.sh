#!/bin/bash
# Review screenshots (backend/tests/e2e/shots.py): the screens a UI ticket changed,
# on the synthetic demo athlete, from a JSON shot list — for the ticket's 驗收截圖.
# Same one-time setup as ./e2e.sh (Playwright + Chromium; the pages need the internet).
#   ./shots.sh SP-396.json --out <dir>                      # builds and serves the demo athlete itself
#   python -m backend.tests.e2e._server --serve             # or one server for many shot lists:
#   ./shots.sh a.json b.json --out <dir> --url http://127.0.0.1:<port>
# Paths are taken from where you call it (no cd), so relative ones work.
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python}"
if [ -z "$PYTHON" ]; then
  [ -x "$REPO/.venv/bin/python" ] && PY="$REPO/.venv/bin/python"
  [ -x "$REPO/.venv/Scripts/python.exe" ] && PY="$REPO/.venv/Scripts/python.exe"
fi
PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m backend.tests.e2e.shots "$@"
