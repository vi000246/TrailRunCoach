# TrailRunCoach — the public DEMO instance (synthetic athlete only), for a
# Hugging Face Space (SDK: docker) or any container host.
#
# Build context = the repo root:
#     docker build -f deploy/hf/Dockerfile -t trailcoach-demo .
#     docker run --rm -p 7860:7860 --cpus 1 --memory 1.5g trailcoach-demo
# On Hugging Face the Space repo holds this file as ./Dockerfile (deploy/hf/make_space.py).
#
# The demo data (a year of synthetic activities, the caches, the coming weeks'
# schedule) is built INTO the image, so the first visitor never waits for a
# cold build; the running instance rebuilds it in the background once a week
# (WKO5COACH_DEMO_REBUILD=1) so the last activity stays recent.
# Nothing of the owner's (~/.wko5coach, ~/WKO5) is in the build context:
# .dockerignore + only backend/ and views/ are copied.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app TZ=Asia/Taipei

# Hugging Face runs the container as uid 1000
RUN useradd -m -u 1000 user
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=user backend/ ./backend/
COPY --chown=user views/ ./views/

USER user
ENV HOME=/home/user \
    WKO5COACH_HOME=/home/user/demo \
    WKO5COACH_FIT_WORKERS=2

# the demo data + warm caches (minutes, once per image build)
RUN python -m backend.demo.build --root "$WKO5COACH_HOME"

# HF serves the Space inside an iframe on huggingface.co: the sandbox / CSRF
# cookies must be SameSite=None; Secure; Partitioned there.
# The platform's proxy is the direct peer: trust its X-Forwarded-For / CF-Connecting-IP.
ENV WKO5COACH_MODE=demo \
    WKO5COACH_DEMO_REBUILD=1 \
    WKO5COACH_NO_SCHEDULER=1 \
    WKO5COACH_NO_AUTO_BACKUP=1 \
    WKO5COACH_THREADS=40 \
    WKO5COACH_COOKIE_SAMESITE=None \
    WKO5COACH_TRUSTED_PROXIES=127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128

EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz', timeout=4)"
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-proxy-headers"]
