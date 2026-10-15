#!/bin/bash
# Local machine only: builds the image here and runs it on localhost.
# Do not run this on the NAS (production): its image is built on the Mac and
# shipped with docker save / load, see docs/deploy/wko5coach.deploy.md
# ("正式環境：NAS").
set -e
cd "$(dirname "$0")"

# the data folder: an install from before SP-498 keeps ~/.wko5coach (never moved)
if [ -z "${TRC_DATA_FOLDER:-}" ] && [ ! -e "$HOME/.trailruncoach" ] && [ -d "$HOME/.wko5coach" ]; then
  export TRC_DATA_FOLDER=.wko5coach
fi

echo "==> Building and starting TrailRunCoach + Portainer..."
docker compose up --build -d --remove-orphans   # the service was called wko5coach before SP-498

echo ""
echo "✓ TrailRunCoach →  http://localhost:8000"
echo "✓ Portainer   →  http://localhost:9000  (first launch: create admin password)"
echo ""
echo "Logs: docker compose logs -f trailruncoach"
echo "Stop: docker compose down"
