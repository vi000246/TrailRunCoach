"""Review screenshots: the screens a ticket changed, in a real Chromium on the
synthetic demo athlete — what the reviewer looks at next to a UI ticket's
驗收條件, instead of clicking through the app.

A shot list (JSON) says what to capture. What the demo athlete has no data for
is mocked per shot — an API answer replaced or patched, or the DOM set up by a
script — so every state a ticket talks about can be pictured.

    python -m backend.tests.e2e.shots SP-396.json --out <dir>          # builds and serves the demo athlete itself (~30 s)
    python -m backend.tests.e2e._server --serve                        # or one server for many runs:
    python -m backend.tests.e2e.shots a.json b.json --out <dir> --url http://127.0.0.1:<port>
    ./shots.sh …                                                       # the same, with the repo's venv

The shot list:

    {"ticket": "SP-396",
     "shots": [
       {"name": "課表頁 手機",                        # → <out>/SP-396-1-課表頁-手機.webp
        "page": "schedule",                          # a key of _server.PAGES, or "path": "/api/v1/…?x=1"
        "ready": "#loginForm",                       # what to wait for after loading (default: the app's
                                                     #   nav, .appnav — pages without it, like /login, need this)
        "viewport": "mobile",                        # mobile (390×844) | desktop (1400×900, default) | [w, h]
                                                     #   | [w, h, scale] (scale 2: sharp at half the pixels' size)
        "scheme": "dark",                            # the browser's colour scheme: light (default) | dark
        "caption": "第二張卡片：左側色條＝類型色",      # what the reviewer should look at
        "init": "localStorage.setItem('k', 'v')",    # runs before the page's own scripts
        "mock": [                                    # set up before the page loads
          {"url": "**/api/v1/session", "json": {}, "status": 200},             # replace the answer
          {"url": "**/api/v1/overview/plan/sessions*", "set": {"0.done": true}} # patch the real answer
        ],
        "steps": [{"click": "text=週"}, {"wait": ".sesschip"}, {"fill": ["#q", "abc"]},
                  {"hover": "…"}, {"press": "Escape"}, {"scroll": "…"}, {"eval": "…js…"},
                  {"sleep": 500}, {"goto": "/api/v1/…"}],
        "hide": [".sugbox"],                         # not in the picture (the 建議 panel covers
                                                     #   the right side of most desktop pages)
        "clip": ".week",                             # only this element (else the viewport;
        "full_page": false}                          #   "full_page": true for the whole page)
     ]}

`url` is a Playwright glob, or a regular expression after "re:". `set` keys are
dotted paths into the answer's JSON ("0.done", "items.2.label"); the real answer
is fetched first, so the rest of it stays real.

Output: the images (WebP, about half the PNG; --format png for the raw ones)
and `<ticket>-shots.json` (per shot: file, page, viewport, caption and the page
errors seen — a shot taken with errors is still saved, but says so).
Exit code 1 when a shot could not be taken at all.

Synthetic only, like the smoke tests (_server.py): no account, no real home
folder, map tiles answered with a blank tile."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from backend.tests.e2e import _server

VIEWPORTS = {
    "desktop": {"viewport": {"width": 1400, "height": 900}, "device_scale_factor": 1},
    "mobile": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
               "is_mobile": True, "has_touch": True},
}
SHOT_KEYS = {"name", "page", "path", "ready", "viewport", "caption", "init", "mock", "steps", "clip", "full_page",
             "hide", "scheme"}
MOCK_KEYS = {"url", "json", "status", "set"}
STEP_KEYS = {"click", "wait", "fill", "hover", "press", "scroll", "eval", "sleep", "goto"}
TICKET_RE = re.compile(r"^[A-Z]{2,4}-\d+$")
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\s]+')       # not in a Windows file name; spaces become "-"


class SpecError(ValueError):
    pass


def load_spec(path: Path) -> dict:
    try:
        spec = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SpecError(f"{path}: {e}") from e
    return check_spec(spec, str(path))


def check_spec(spec: dict, where: str = "spec") -> dict:
    """The shot list as written, or a SpecError naming the first problem (a typo in a
    key would otherwise silently drop a mock or a step)."""
    if not isinstance(spec, dict):
        raise SpecError(f"{where}: not a JSON object")
    ticket = spec.get("ticket", "")
    if not TICKET_RE.match(str(ticket)):
        raise SpecError(f"{where}: ticket must look like SP-123, got {ticket!r}")
    shots = spec.get("shots")
    if not isinstance(shots, list) or not shots:
        raise SpecError(f"{where}: shots must be a non-empty list")
    for i, s in enumerate(shots, 1):
        at = f"{where} shot {i}"
        if not isinstance(s, dict):
            raise SpecError(f"{at}: not an object")
        if extra := set(s) - SHOT_KEYS:
            raise SpecError(f"{at}: unknown keys {sorted(extra)}")
        if not s.get("name"):
            raise SpecError(f"{at}: no name")
        if bool(s.get("page")) == bool(s.get("path")):
            raise SpecError(f"{at}: give exactly one of page / path")
        if s.get("page") and s["page"] not in _server.PAGES:
            raise SpecError(f"{at}: page {s['page']!r} is not one of {sorted(_server.PAGES)}")
        vp = s.get("viewport", "desktop")
        if not ((isinstance(vp, str) and vp in VIEWPORTS)        # a list is not hashable: no `in` on it
                or (isinstance(vp, list) and len(vp) in (2, 3) and all(isinstance(x, int) and x > 0 for x in vp))):
            raise SpecError(f"{at}: viewport must be mobile / desktop / [w, h] / [w, h, scale]")
        if s.get("scheme", "light") not in ("light", "dark"):
            raise SpecError(f"{at}: scheme must be light / dark")
        if not (isinstance(s.get("hide", []), list) and all(isinstance(x, str) and x for x in s.get("hide", []))):
            raise SpecError(f"{at}: hide must be a list of selectors")
        for m in s.get("mock", []):
            if extra := set(m) - MOCK_KEYS:
                raise SpecError(f"{at}: unknown mock keys {sorted(extra)}")
            if not m.get("url") or ("json" in m) == ("set" in m):
                raise SpecError(f"{at}: a mock needs url and exactly one of json / set")
        for st in s.get("steps", []):
            if not isinstance(st, dict) or len(st) != 1 or next(iter(st)) not in STEP_KEYS:
                raise SpecError(f"{at}: a step is one of {sorted(STEP_KEYS)}, got {st!r}")
    return spec


def file_name(ticket: str, n: int, name: str, fmt: str = "webp") -> str:
    return f"{ticket}-{n}-{_BAD_CHARS.sub('-', name).strip('-')}.{fmt}"


def encoder(browser):
    """PNG bytes → WebP bytes, encoded by Chromium itself (canvas.toDataURL): about
    half the PNG and text stays sharp at 0.7 — the shots go into a git-synced vault,
    and this needs no image library."""
    import base64
    page = browser.new_page()

    def to_webp(png: bytes, quality: float = 0.7) -> bytes:
        url = page.evaluate(
            """async ([b64, q]) => {
                 const img = new Image();
                 img.src = 'data:image/png;base64,' + b64;
                 await img.decode();
                 const c = document.createElement('canvas');
                 c.width = img.naturalWidth; c.height = img.naturalHeight;
                 c.getContext('2d').drawImage(img, 0, 0);
                 return c.toDataURL('image/webp', q);
               }""", [base64.b64encode(png).decode(), quality])
        return base64.b64decode(url.split(",", 1)[1])
    return to_webp


def set_path(doc, dotted: str, value):
    """doc with doc[a][b]… = value for "a.b…" (list indexes as numbers); in place."""
    keys = dotted.split(".")
    cur = doc
    for k in keys[:-1]:
        cur = cur[int(k)] if isinstance(cur, list) else cur[k]
    last = keys[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value
    return doc


def _matcher(url: str):
    return re.compile(url[3:]) if url.startswith("re:") else url


def _mock(ctx, m: dict) -> None:
    if "json" in m:
        body, status = m["json"], m.get("status", 200)
        ctx.route(_matcher(m["url"]), lambda route: route.fulfill(status=status, json=body))
        return
    sets = m["set"]

    def patch(route):
        resp = route.fetch()
        doc = resp.json()
        for k, v in sets.items():
            set_path(doc, k, v)
        route.fulfill(response=resp, json=doc)
    ctx.route(_matcher(m["url"]), patch)


def _step(page, st: dict) -> None:
    (op, arg), = st.items()
    if op == "click":
        page.locator(arg).first.click()
    elif op == "wait":
        page.locator(arg).first.wait_for(state="visible")
    elif op == "fill":
        page.locator(arg[0]).first.fill(arg[1])
    elif op == "hover":
        page.locator(arg).first.hover()
    elif op == "press":
        page.keyboard.press(arg)
    elif op == "scroll":
        page.locator(arg).first.scroll_into_view_if_needed()
    elif op == "eval":
        page.evaluate(arg)
    elif op == "sleep":
        page.wait_for_timeout(int(arg))
    elif op == "goto":
        _open(page, arg)


def _open(page, path: str, ready: str | None = None) -> None:
    from backend.tests.e2e._browser import open_page
    if ready:                              # a page without the app's nav (/login …): wait for this instead
        resp = page.goto(path, wait_until="load")
        if resp is None or resp.status != 200:
            raise RuntimeError(f"{path}: {resp.status if resp else 'no response'}")
        page.locator(ready).first.wait_for(state="visible")
    else:
        open_page(page, path)
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:                      # noqa: BLE001 — a page that keeps polling is still drawn
        pass


def take(browser, base_url: str, spec: dict, out: Path, fmt: str = "webp") -> list[dict]:
    from backend.tests.e2e._browser import Watch, blank_tiles
    ticket = spec["ticket"]
    to_webp = encoder(browser) if fmt == "webp" else None
    rows = []
    for n, s in enumerate(spec["shots"], 1):
        vp = s.get("viewport", "desktop")
        opts = VIEWPORTS[vp] if isinstance(vp, str) else {"viewport": {"width": vp[0], "height": vp[1]},
                                                          "device_scale_factor": vp[2] if len(vp) > 2 else 1}
        path = _server.PAGES[s["page"]] if s.get("page") else s["path"]
        row = {"n": n, "name": s["name"], "file": file_name(ticket, n, s["name"], fmt), "path": path,
               "viewport": vp, "caption": s.get("caption", ""), "errors": []}
        ctx = browser.new_context(base_url=base_url, locale="zh-TW", timezone_id="Asia/Taipei",
                                  color_scheme=s.get("scheme", "light"), **opts)
        ctx.set_default_timeout(30_000)
        try:
            blank_tiles(ctx, base_url)
            for m in s.get("mock", []):
                _mock(ctx, m)
            if s.get("init"):
                ctx.add_init_script(s["init"])
            page = ctx.new_page()
            watch = Watch(page, base_url)
            page.on("dialog", lambda d: d.accept())
            _open(page, path, s.get("ready"))
            for st in s.get("steps", []):
                _step(page, st)
            if s.get("hide"):
                page.add_style_tag(content=",".join(s["hide"]) + "{visibility:hidden!important}")
            page.wait_for_timeout(800)                     # last renders and transitions
            if s.get("clip"):
                png = page.locator(s["clip"]).first.screenshot()
            else:
                png = page.screenshot(full_page=bool(s.get("full_page")))
            (out / row["file"]).write_bytes(to_webp(png) if to_webp else png)
            row["errors"] = watch.problems()
            row["ok"] = True
        except Exception as e:                             # noqa: BLE001 — report it, take the next shot
            row["ok"] = False
            row["errors"].append(f"{type(e).__name__}: {str(e).splitlines()[0][:300]}")
        finally:
            ctx.close()
        mark = "✓" if row["ok"] and not row["errors"] else ("!" if row["ok"] else "✗")
        print(f"{mark} {row['file']}" + (f"  ({'; '.join(row['errors'])[:300]})" if row["errors"] else ""), flush=True)
        rows.append(row)
    (out / f"{ticket}-shots.json").write_text(
        json.dumps({"ticket": ticket, "shots": rows}, ensure_ascii=False, indent=2), "utf-8")
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.tests.e2e.shots", description=__doc__.split("\n\n")[0])
    ap.add_argument("specs", nargs="+", type=Path, help="shot list(s), JSON")
    ap.add_argument("--out", type=Path, required=True, help="folder for the images")
    ap.add_argument("--url", help="a running demo server (python -m backend.tests.e2e._server --serve)")
    ap.add_argument("--format", choices=("webp", "png"), default="webp")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):                # Windows consoles default to cp950: no ✓ ✗
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        specs = [load_spec(p) for p in a.specs]
    except SpecError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    home = srv = None
    url = a.url
    try:
        if not url:                                        # same isolation as _server.main
            os.environ["WKO5COACH_TEST_REAL_HOME"] = str(Path.home())
            from backend.tests import _guard                 # Playwright's browsers live under the real home
            _guard._keep_playwright_browsers()
            home = Path(tempfile.mkdtemp(prefix="trc-shots-home-"))
            os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
            srv = _server.Server(_server.build_base(home / "demo-root"), home / "uvicorn.log")
            url = srv.url
        failed = 0
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not a.headed)
            try:
                for spec in specs:
                    failed += sum(1 for r in take(browser, url.rstrip("/"), spec, a.out, a.format) if not r["ok"])
            finally:
                browser.close()
        return 1 if failed else 0
    finally:
        if srv:
            srv.stop()
        if home:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
