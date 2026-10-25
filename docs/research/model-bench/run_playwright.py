"""Run the model benchmark page in Playwright browsers (SP-760).

    PLAYWRIGHT_BROWSERS_PATH=<dir> .venv/bin/python docs/research/model-bench/run_playwright.py \
        [--configs chromium-desktop,chromium-pixel7-4x,...] [--homes warm,fit,cold] \
        [--packages numpy,sqlalchemy] [--bundle backend] [--tag name] [--out docs/research/model-bench/results]

make_bundle.py first (fixtures/). These are NOT phones: "pixel7-4x" is desktop Chromium with a
Pixel 7 screen and user agent and its CPU slowed 4x through the DevTools protocol — the stand-in
for a mid-range Android phone that docs/research/fit-parse-bench used (SP-313). That throttling
never reaches a worker, so the throttled configs run Pyodide on the page's main thread.

Homes: warm / fit / cold (make_bundle.py), and steady: a first page (unthrottled, in a worker)
runs on warm and saves the folder with the caches this Pyodide wrote to OPFS, then a fresh page
in the same browser context is measured on that folder — the device's everyday state, with its
own caches (the server's do not match: see the write-up). Its download numbers are a repeat
visit's (HTTP cache).

Every (config, home) pair gets a fresh browser (empty HTTP cache: the download is the first
visit's, except steady), and the resident memory of the browser's renderer processes (page + worker) is sampled
from `ps` every 0.25 s, as in fit-parse-bench/run_playwright.py. Results go to
results/<tag><config>-<home>.json.
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
import threading
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
FPB = BENCH.parent / "fit-parse-bench"
sys.path.insert(1, str(FPB))


def _load(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


serve = _load("fpb_serve", FPB / "serve.py")                 # the no-cache handler
_fpb = _load("fpb_run_playwright", FPB / "run_playwright.py")  # the SP-313 configs and RSS sampler
CONFIGS, RssSampler = _fpb.CONFIGS, _fpb.RssSampler


def make_server(port: int):
    import http.server
    handler = functools.partial(serve.Handler, directory=str(BENCH))
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)


def run_one(pw, name: str, home: str, url: str, opts: dict) -> dict | None:
    cfg = CONFIGS[name]
    try:
        browser = getattr(pw, cfg["browser"]).launch()
    except Exception as e:  # browser not installed
        print(f"[{name}] skipped: {str(e).splitlines()[0]}")
        return None
    ctx_args = dict(pw.devices[cfg["device"]]) if cfg["device"] else {}
    ctx_args.pop("default_browser_type", None)
    context = browser.new_context(**ctx_args)
    prep = None
    if home == "steady":
        # the device's steady state: one page (unthrottled, in a worker) runs the model on the
        # server's folder and leaves the folder with the caches *this Pyodide* wrote in OPFS;
        # then a fresh page in the same context (same OPFS, warm HTTP cache) is measured on it
        p0 = context.new_page()
        p0.goto(url)
        p0.wait_for_function("window.__bench && window.__bench.ready", timeout=60_000)
        t0 = time.time()
        prep = p0.evaluate("o => window.benchRun(o)", {**opts, "home": "warm", "thread": "worker", "saveHome": True})
        prep = {"wall_s": time.time() - t0, "saved": prep.get("saved"), "error": prep.get("error"),
                "run_error": (prep.get("run") or {}).get("error")}
        p0.close()
        print(f"[{name}/steady] prep {prep['wall_s']:.0f} s, saved {prep['saved']}, error {prep['error'] or prep['run_error']}",
              flush=True)
    page = context.new_page()
    page.on("console", lambda m: m.type == "error" and print(f"[{name}/{home}] console: {m.text}"))
    page.goto(url)
    page.wait_for_function("window.__bench && window.__bench.ready", timeout=60_000)
    root_pid = None
    if cfg["browser"] == "chromium":
        info = browser.new_browser_cdp_session().send("SystemInfo.getProcessInfo")["processInfo"]
        root_pid = next(p["id"] for p in info if p["type"] == "browser")
    if cfg["throttle"] != 1:
        context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": cfg["throttle"]})
    sampler = RssSampler(root_pid or 0)
    base_total, base_ren = sampler.snapshot() if root_pid else (0, 0)
    if root_pid:
        sampler.start()
    t0 = time.time()
    res = page.evaluate("o => window.benchRun(o)", {**opts, "home": "opfs" if home == "steady" else home,
                                                     "thread": cfg["thread"]})
    wall = time.time() - t0
    if prep is not None:
        res["prep"] = prep
    if root_pid:
        sampler.stop_flag.set()
        sampler.join()
    version = browser.version
    browser.close()
    res["harness"] = {"config": name, "home": home, **cfg, "wall_s": wall, "browser_version": version,
                      "rss_baseline_renderer": base_ren, "rss_peak_renderer": sampler.peak_renderer,
                      "rss_peak_total": sampler.peak_total, "rss_samples": sampler.samples}
    run = res.get("run") or {}
    print(f"[{name}/{home}] {wall:.0f} s; renderer RSS peak {sampler.peak_renderer / 2**20:.0f} MB; "
          f"error: {res.get('error') or run.get('error')}", flush=True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", default="chromium-desktop,chromium-desktop-main,chromium-pixel7-4x,chromium-pixel7-6x")
    ap.add_argument("--homes", default="warm,fit")
    ap.add_argument("--packages", default="numpy,sqlalchemy")
    ap.add_argument("--bundle", default="backend")
    ap.add_argument("--who", default="")
    ap.add_argument("--probe", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--out", type=Path, default=BENCH / "results")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    if not (BENCH / "fixtures" / "manifest.json").exists():
        print("fixtures/ missing: run make_bundle.py --demo <demo base> first")
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    srv = make_server(a.port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{a.port}/index.html"
    split = lambda s: [x for x in s.split(",") if x]  # noqa: E731
    opts = {"packages": split(a.packages), "bundle": a.bundle, "who": split(a.who), "probe": split(a.probe)}
    with sync_playwright() as pw:
        for name in split(a.configs):
            for home in split(a.homes):
                res = run_one(pw, name, home, url, opts)
                if res is not None:
                    (a.out / f"{a.tag}{name}-{home}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
