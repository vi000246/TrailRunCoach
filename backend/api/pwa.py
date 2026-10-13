"""
The installable web app (SP-314): GET /sw.js and GET /manifest.webmanifest.

Both are public in sign-in mode (backend/accounts/login.py PUBLIC_PATHS, exact match): neither
holds anyone's data — the manifest is fetched by the browser without cookies, and the worker
script must load before anyone has signed in. The data the worker keeps is the signed-in
person's only, and goes on sign-out (static/offline_core.js). The demo mounts neither (main.py).

/sw.js is served from the root so its scope covers every page (/api/v1/...); `__TRC_BUILD__` in
static/sw.js becomes a digest of the static files' contents, so a deploy that changes any of them
is a new worker (the browser compares the bytes of /sw.js), which keeps a fresh copy of the
program and drops the old one. Cache-Control: no-cache — the browser always revalidates it.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from backend import i18n
from backend.i18n.pages import STATIC, page_catalog

router = APIRouter(tags=["pwa"])

SW_FILE = STATIC / "sw.js"
BUILD_MARK = "__TRC_BUILD__"
START_URL = "/api/v1/overview/page"
THEME = "#2563eb"                  # the pages' --accent (light)
BACKGROUND = "#f4f5f7"             # the pages' --bg (light)
ICON_DIR = "/api/v1/static/icons"
ICONS = [
    {"src": f"{ICON_DIR}/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
    {"src": f"{ICON_DIR}/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    {"src": f"{ICON_DIR}/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    {"src": f"{ICON_DIR}/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
]

_BUILD: dict = {}
_LOCK = threading.Lock()


def build_id(root: Path = STATIC) -> str:
    """A digest of every static file's path and contents (re-read only when a file's size or
    mtime changed): the same files give the same id on every start and every machine."""
    files = sorted(p for p in root.rglob("*") if p.is_file())
    sig = tuple((p.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns) for p in files for st in (p.stat(),))
    key = str(root)
    with _LOCK:
        hit = _BUILD.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    out = h.hexdigest()[:16]
    with _LOCK:
        _BUILD[key] = (sig, out)
    return out


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    js = SW_FILE.read_text("utf-8").replace(BUILD_MARK, build_id())
    return Response(js, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-cache"})


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    """In the request's language (the browser sends no cookie for it: Accept-Language decides)."""
    loc = i18n.current_locale()
    cat = page_catalog(("shell",), loc)
    name = cat.get("shell.brand") or "Training Coach"
    body = {
        "id": "/",
        "name": name,
        "short_name": name,
        "description": cat.get("shell.pwa.description") or "",
        "lang": loc,
        "start_url": START_URL,
        "scope": "/",
        "display": "standalone",
        "background_color": BACKGROUND,
        "theme_color": THEME,
        "icons": ICONS,
    }
    return JSONResponse(body, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})
