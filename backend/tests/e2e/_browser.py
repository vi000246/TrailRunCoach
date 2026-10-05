"""Browser-side helpers of the smoke tests (needs Playwright; imported by the fixtures)."""
from __future__ import annotations

import base64

# a 1×1 transparent PNG: the answer to every map-tile request
_TILE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
CDN = "cdnjs.cloudflare.com"          # ECharts / Leaflet (the pages' only other origin)


def blank_tiles(ctx, base_url: str) -> None:
    """Answer every request that is neither the app nor the CDN (the basemap tile
    servers, basemaps.js) locally: a blank tile, so no test talks to them."""
    def external(url: str) -> bool:
        return not url.startswith(base_url) and CDN not in url

    def handle(route):
        if route.request.resource_type == "image":
            route.fulfill(status=200, content_type="image/png", body=_TILE)
        else:
            route.fulfill(status=204, body=b"")
    ctx.route(external, handle)


class Watch:
    """What went wrong on a page: uncaught exceptions (pageerror), console.error
    messages, the app's own 5xx answers and requests to the app / CDN that
    failed. `Failed to load resource` console lines are left to the response
    check: a 4xx can be a normal answer (no data yet), a 5xx never is."""

    def __init__(self, page, base_url: str):
        self.base = base_url
        self.errors: list[str] = []
        self.allowed: list[str] = []      # substrings of console errors a test expects
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", self._console)
        page.on("response", self._response)
        page.on("requestfailed", self._failed)

    def _console(self, msg) -> None:
        if msg.type != "error":
            return
        text = msg.text
        if text.startswith("Failed to load resource"):
            return
        loc = (msg.location or {}).get("url", "")
        self.errors.append(f"console.error: {text[:300]}" + (f" ({loc})" if loc else ""))

    def _response(self, resp) -> None:
        if resp.url.startswith(self.base) and resp.status >= 500:
            self.errors.append(f"HTTP {resp.status}: {resp.request.method} {resp.url[len(self.base):]}")

    def _failed(self, req) -> None:
        why = req.failure or ""
        # ERR_ABORTED: a request cut off by the next navigation / a superseded fetch
        if "ERR_ABORTED" in why or "NS_BINDING_ABORTED" in why:
            return
        if req.url.startswith(self.base):
            self.errors.append(f"request failed: {req.method} {req.url[len(self.base):]} {why}")
        elif CDN in req.url:
            self.errors.append(f"CDN unreachable (the e2e run needs the internet): {req.url} {why}")

    def allow(self, fragment: str) -> None:
        self.allowed.append(fragment)

    def problems(self) -> list[str]:
        return [e for e in self.errors if not any(a in e for a in self.allowed)]


def open_page(page, path: str):
    """Go to an app page and wait for its scripts (load: ECharts / Leaflet from the CDN)."""
    resp = page.goto(path, wait_until="load")
    assert resp is not None and resp.status == 200, f"{path}: {resp.status if resp else 'no response'}"
    # the shell (shell.js) is on every page: its nav is the first thing the page draws
    page.locator(".appnav").first.wait_for(state="visible")
    return resp


def no_loading(page, selector: str, timeout: float = 60_000) -> None:
    """Wait until `selector` holds no `.loading` placeholder any more (the page's data arrived)."""
    page.wait_for_function(
        """(sel) => { const el = document.querySelector(sel); return !!el && !el.querySelector('.loading'); }""",
        arg=selector, timeout=timeout)


def chart_drawn(page, selector: str, timeout: float = 60_000) -> None:
    """Wait until ECharts has drawn into `selector` (its <canvas> / <svg>)."""
    page.wait_for_function(
        """(sel) => { const el = document.querySelector(sel); return !!el && !!el.querySelector('canvas, svg'); }""",
        arg=selector, timeout=timeout)
