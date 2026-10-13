#!/bin/bash
# Local machine only: builds the image here and runs it on localhost.
# Do not run this on the NAS (production): its image is built on the Mac and
# shipped with docker save / load, see docs/deploy/wko5coach.deploy.md
# ("正式環境：NAS").
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
