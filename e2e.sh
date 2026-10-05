#!/bin/bash
# Browser smoke tests (backend/tests/e2e/): every page in a real Chromium on the synthetic
# demo athlete, served by a local uvicorn on a free port. One-time setup:
#   pip install -r requirements-dev.txt && python -m playwright install chromium
# Needs the internet (the pages load ECharts / Leaflet from cdnjs).
#   ./e2e.sh                         # all of them
#   ./e2e.sh -k templates            # extra pytest arguments are passed on
#   TRC_E2E_HEADED=1 ./e2e.sh -x     # watch it in a browser window
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python}"
[ -x .venv/bin/python ] && [ -z "$PYTHON" ] && PY=.venv/bin/python
exec "$PY" -m pytest backend/tests/e2e -m e2e -p no:cacheprovider -q "$@"
