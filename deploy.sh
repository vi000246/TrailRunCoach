#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "==> Building and starting WKO5 Coach + Portainer..."
docker compose up --build -d

echo ""
echo "✓ WKO5 Coach  →  http://localhost:8000"
echo "✓ Portainer   →  http://localhost:9000  (first launch: create admin password)"
echo ""
echo "Logs: docker compose logs -f wko5coach"
echo "Stop: docker compose down"
