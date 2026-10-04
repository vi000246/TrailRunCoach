#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")"; pwd)"

# Backend
cd "$ROOT"
/opt/homebrew/bin/python3.12 -m uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "Backend started (PID $BACKEND_PID) at http://localhost:8000"

echo ""
echo "WKO5 Coach running:"
echo "  Dashboard: http://localhost:8000"
echo "  API docs:  http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"

trap "kill $BACKEND_PID 2>/dev/null; exit" INT TERM
wait
