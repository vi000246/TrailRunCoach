FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

ENV PYTHONPATH=/app
# glibc gives every thread that allocates its own malloc arena (up to 8 per
# core); the request thread pool then holds freed memory in dozens of arenas
# and RSS only ratchets up (the NAS container sat at its 3 GB limit). Two
# arenas keep it close to what is actually in use, for a little lock contention.
ENV MALLOC_ARENA_MAX=2
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
