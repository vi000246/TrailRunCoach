"""The 介紹頁's built-in race calculator example (SP-622): the demo athlete's 示範山徑越野 50K, computed by
the race calculator page itself and frozen as a share snapshot (engine/racepower/share.snapshot) in
backend/demo/example_share.json — served at /share/<EXAMPLE_ID> (api/racepower.share_data) without any
account, tenant or file of anyone's. The runner and the race are fictional (backend/demo), nothing in it
is a real person's.

    python -m backend.scripts.intro_example                          # builds the full demo athlete (~2 min) and serves it
    python -m backend.scripts.intro_example --url http://127.0.0.1:8123   # a running demo server (a full build: the
                                                                           # tests' small one has a 15 km "50K")

Run it again when the calculator's result changes shape (the share page reads the snapshot as it is).
Same setup as ./shots.sh (Playwright)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "demo" / "example_share.json"
EVENT = "demo-50k"
TITLE = "示範山徑越野 50K（虛構跑者）"


def capture(url: str) -> tuple[dict, dict]:
    """(the /plan request, its answer) as the race calculator page sends it for the 50K."""
    from playwright.sync_api import sync_playwright
    got: dict = {}

    def on(resp):
        if resp.url.endswith("/api/v1/racepower/plan") and resp.request.method == "POST" and resp.ok:
            got["req"], got["res"] = resp.request.post_data_json, resp.json()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.on("response", on)
            page.goto(f"{url.rstrip('/')}/api/v1/racepower/page?event={EVENT}&lang=zh-TW")
            for _ in range(120):
                if "res" in got:
                    break
                page.wait_for_timeout(500)
        finally:
            browser.close()
    if "res" not in got:
        raise SystemExit("the race calculator computed nothing for " + EVENT)
    return got["req"], got["res"]


def freeze(req: dict, plan: dict) -> dict:
    from backend.engine.racepower import share as SH
    km = (plan.get("summary") or {}).get("km") or 0
    if km < 40:
        raise SystemExit(f"the course is {km:.1f} km: a small demo build? (use a full one)")
    now = dt.datetime.now().replace(microsecond=0)
    snap = SH.snapshot(plan, title=TITLE, include_weight=False, expires_days=None, request=req, now=now)
    snap["example"] = True           # the share page's own: never a user's
    return snap


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.scripts.intro_example", description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", help="a running demo server built in full")
    a = ap.parse_args(argv)
    home = srv = None
    try:
        url = a.url
        if not url:
            from backend.demo import build as B
            from backend.tests.e2e import _server
            os.environ["WKO5COACH_TEST_REAL_HOME"] = str(Path.home())
            home = Path(tempfile.mkdtemp(prefix="trc-intro-home-"))
            os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
            base = B.build(home / "demo-root", anchor=dt.date.today(), weeks=52, small=False, warm=False)
            srv = _server.Server(base, home / "uvicorn.log")
            url = srv.url
        req, plan = capture(url)
        snap = freeze(req, plan)
        OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", "utf-8")
        s = snap["summary"]
        print(f"→ {OUT.relative_to(OUT.parents[2])}: {s.get('km', 0):.1f} km ↑{s.get('gain_m', 0):.0f} m, "
              f"{len(snap['segments'])} segments")
        return 0
    finally:
        if srv:
            srv.stop()
        if home:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
