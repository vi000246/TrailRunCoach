FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
# The bundled views (training/workout/periodization, the WKO5 chart fixes, the
# views/i18n sidecar) are read at runtime from the repo root (customviews.REPO_VIEWS).
COPY views/ ./views/

ENV PYTHONPATH=/app
# the commit the image was built from: `meta.app_version` of the debug API (docs/debug-api.md);
# docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) .
ARG GIT_SHA=unknown
ENV TRC_GIT_SHA=$GIT_SHA
# glibc gives every thread that allocates its own malloc arena (up to 8 per
# core); the request thread pool then holds freed memory in dozens of arenas
# and RSS only ratchets up (the NAS container sat at its 3 GB limit). Two
# arenas keep it close to what is actually in use, for a little lock contention.
ENV MALLOC_ARENA_MAX=2
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
