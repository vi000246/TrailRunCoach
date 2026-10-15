"""Run the FIT parse benchmark page in Playwright browsers (SP-313).

    PLAYWRIGHT_BROWSERS_PATH=<dir> .venv/bin/python docs/research/fit-parse-bench/run_playwright.py \
        [--configs chromium-desktop,chromium-desktop-main,chromium-pixel7-4x,...] \
        [--reps js,py] [--full js,py] [--n 800] [--out docs/research/fit-parse-bench/results]

These are NOT phones. "pixel7-4x" is desktop Chromium with a Pixel 7 screen and user agent
and its CPU slowed 4x through the DevTools protocol (Emulation.setCPUThrottlingRate); that
is the usual stand-in for a mid-range Android phone. That throttling never reaches a worker,
so the throttled configs run the parsers on the page's main thread (see CONFIGS). Firefox and
WebKit run only when Playwright's build of them is installed (WebKit is the closest thing to
Safari here, still not iOS).

While a run is going, the resident memory of the browser's processes is sampled from `ps`
every 0.25 s (peak of the renderer / content processes, which include the worker). Every
part (reps-js, reps-py, full-js, full-py) gets a fresh browser, so each peak is its own.
Each config writes results/<config>.json ({"parts": {part: the page's result object +
"harness"}}) and results/<config>-dump.json (per-second fields of the three files), then
compare.py checks every dump against fitdecode.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))

import serve  # noqa: E402

# DevTools CPU throttling only slows a page's main thread, never its workers, so the
# throttled configs run the parsers on the main thread (thread "main"); chromium-desktop-main
# is the unthrottled main-thread run that shows main and worker cost the same.
CONFIGS = {
    "chromium-desktop": {"browser": "chromium", "device": None, "throttle": 1, "thread": "worker"},
    "chromium-desktop-main": {"browser": "chromium", "device": None, "throttle": 1, "thread": "main"},
    "chromium-pixel7-4x": {"browser": "chromium", "device": "Pixel 7", "throttle": 4, "thread": "main"},
    "chromium-pixel7-6x": {"browser": "chromium", "device": "Pixel 7", "throttle": 6, "thread": "main"},
    "firefox-desktop": {"browser": "firefox", "device": None, "throttle": 1, "thread": "worker"},
    "webkit-iphone": {"browser": "webkit", "device": "iPhone 15", "throttle": 1, "thread": "worker"},
}


class RssSampler(threading.Thread):
    """Peak RSS of the browser's process tree (all of it, and the renderer / content processes,
    which hold the page and its workers). root_pid = the browser process."""

    def __init__(self, root_pid: int):
        super().__init__(daemon=True)
        self.root, self.stop_flag = root_pid, threading.Event()
        self.peak_total = self.peak_renderer = 0
        self.samples = 0

    @staticmethod
    def is_renderer(cmd: str) -> bool:
        return "--type=renderer" in cmd or "-contentproc" in cmd or "WebContent" in cmd

    def snapshot(self) -> tuple[int, int]:
        out = subprocess.run(["ps", "-axwwo", "pid=,ppid=,rss=,command="], capture_output=True, text=True).stdout
        procs = {}
        for line in out.splitlines():
            pid, ppid, rss, *cmd = line.split(None, 3)
            procs[int(pid)] = (int(ppid), int(rss), cmd[0] if cmd else "")
        tree, grew = {self.root}, True
        while grew:
            grew = False
            for pid, (ppid, _r, _c) in procs.items():
                if ppid in tree and pid not in tree:
                    tree.add(pid)
                    grew = True
        tot = sum(procs[p][1] for p in tree if p in procs)
        ren = sum(procs[p][1] for p in tree if p in procs and self.is_renderer(procs[p][2]))
        return tot * 1024, ren * 1024

    def run(self):
        while not self.stop_flag.is_set():
            t, r = self.snapshot()
            self.peak_total, self.peak_renderer = max(self.peak_total, t), max(self.peak_renderer, r)
            self.samples += 1
            time.sleep(0.25)


def run_part(pw, name: str, part: str, url: str, opts: dict) -> tuple[dict, dict] | None:
    """One fresh browser for one part ("reps-js", "full-py", ...), so each memory peak is its own."""
    cfg = CONFIGS[name]
    try:
        browser = getattr(pw, cfg["browser"]).launch()
    except Exception as e:  # browser not installed
        print(f"[{name}] skipped: {str(e).splitlines()[0]}")
        return None
    ctx_args = dict(pw.devices[cfg["device"]]) if cfg["device"] else {}
    ctx_args.pop("default_browser_type", None)
    context = browser.new_context(**ctx_args)
    page = context.new_page()
    page.on("console", lambda m: m.type == "error" and print(f"[{name}/{part}] console: {m.text}"))
    page.goto(url)
    page.wait_for_function("window.__bench && window.__bench.ready", timeout=60_000)
    root_pid = None
    if cfg["browser"] == "chromium":
        info = browser.new_browser_cdp_session().send("SystemInfo.getProcessInfo")["processInfo"]
        root_pid = next(p["id"] for p in info if p["type"] == "browser")
    if cfg["throttle"] != 1:
        context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": cfg["throttle"]})
    sampler = RssSampler(root_pid or os.getpid())
    base_total, base_ren = sampler.snapshot()
    sampler.start()
    kind, parser = part.split("-")
    run_opts = {"reps": [parser] if kind == "reps" else [], "full": [parser] if kind == "full" else [],
                "n": opts["n"], "repeat": opts["repeat"], "dump": kind == "reps", "thread": cfg["thread"]}
    t0 = time.time()
    res = page.evaluate("opts => window.benchRun(opts)", run_opts)
    wall = time.time() - t0
    sampler.stop_flag.set()
    sampler.join()
    dumps = page.evaluate("() => window.__bench.dumps") if kind == "reps" else {}
    version = browser.version
    browser.close()
    res["harness"] = {"config": name, "part": part, **cfg, "wall_s": wall, "rss_baseline_total": base_total,
                      "rss_baseline_renderer": base_ren, "rss_peak_total": sampler.peak_total,
                      "rss_peak_renderer": sampler.peak_renderer, "rss_samples": sampler.samples,
                      "browser_version": version}
    print(f"[{name}/{part}] {wall:.0f} s; renderer RSS peak {sampler.peak_renderer / 2**20:.0f} MB"
          f" (before {base_ren / 2**20:.0f} MB); errors: {res.get('errors')}", flush=True)
    return res, dumps


def run_config(pw, name: str, url: str, opts: dict, out: Path) -> bool:
    parts = [f"reps-{p}" for p in opts["reps"]] + [f"full-{p}" for p in opts["full"]]
    results, dumps = {}, {}
    for part in parts:
        got = run_part(pw, name, part, url, opts)
        if got is None:
            return False
        results[part], d = got
        dumps.update(d)
        (out / f"{name}.json").write_text(json.dumps({"config": name, "parts": results}, indent=1))
    if dumps:
        (out / f"{name}-dump.json").write_text(json.dumps({"config": name, "files": dumps}))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--reps", default="js,py")
    ap.add_argument("--full", default="js,py")
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--out", type=Path, default=BENCH / "results")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    if not (BENCH / "fixtures" / "manifest.json").exists():
        subprocess.run([sys.executable, str(BENCH / "make_fixtures.py")], check=True)
    args.out.mkdir(parents=True, exist_ok=True)
    srv = serve.make_server(args.port, "127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{args.port}/index.html"
    split = lambda s: [x for x in s.split(",") if x]  # noqa: E731
    opts = {"reps": split(args.reps), "full": split(args.full), "n": args.n, "repeat": args.repeat}
    ran = []
    with sync_playwright() as pw:
        for name in split(args.configs):
            if run_config(pw, name, url, opts, args.out):
                ran.append(name)
    srv.shutdown()
    if opts["reps"]:
        import compare
        return compare.main([str(args.out / f"{n}-dump.json") for n in ran])
    return 0


if __name__ == "__main__":
    sys.exit(main())
