"""The 操作手冊's screenshots (SP-558): backend/manual/shots.json, each shot in zh-TW and en, on the
synthetic demo athlete (backend/tests/e2e/_server.py), saved as backend/manual/img/<name>.<locale>.webp —
the names the chapters use (![…](../img/<name>.<locale>.webp)). Run it again when a page the manual
pictures changes; backend/tests/test_manual_sp558.py checks that every picture a chapter uses is there.

    python -m backend.scripts.manual_shots                    # every shot (builds and serves the demo, ~1 min)
    python -m backend.scripts.manual_shots schedule-week      # only these
    python -m backend.scripts.manual_shots --url http://127.0.0.1:8123   # a running e2e server
    python -m backend.scripts.manual_shots --chrome           # the installed Google Chrome, not Playwright's

Same setup as ./shots.sh (Playwright; the pages need the internet for ECharts)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "manual"
SPEC = ROOT / "shots.json"
IMG = ROOT / "img"
LOCALES = ("zh-TW", "en")


def load(names: list[str] | None = None) -> list[dict]:
    shots = json.loads(SPEC.read_text("utf-8"))["shots"]
    if names:
        unknown = set(names) - {s["name"] for s in shots}
        if unknown:
            raise SystemExit(f"no such shot: {sorted(unknown)}")
        shots = [s for s in shots if s["name"] in names]
    return shots


def spec_for(shots: list[dict], locale: str) -> dict:
    """backend.tests.e2e.shots' shot list for one locale (page → path with ?lang=, plus the hash)."""
    from backend.tests.e2e import _server
    out = []
    for s in shots:
        s = dict(s)
        path = _server.PAGES[s.pop("page")] if "page" in s else s.pop("path")
        sep = "&" if "?" in path else "?"
        s["path"] = f"{path}{sep}lang={locale}{s.pop('hash', '')}"
        s["name"] = f"{s['name']}.{locale}"
        out.append(s)
    return {"ticket": "SP-558", "shots": out}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.scripts.manual_shots", description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="only these shots (shots.json names)")
    ap.add_argument("--url", help="a running demo server (python -m backend.tests.e2e._server --serve)")
    ap.add_argument("--chrome", action="store_true", help="launch the installed Google Chrome")
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    from backend.tests.e2e import _server, shots as S
    shots = load(a.names)
    home = srv = None
    url = a.url
    tmp = Path(tempfile.mkdtemp(prefix="trc-manual-shots-"))
    failed = 0
    try:
        if not url:
            os.environ["WKO5COACH_TEST_REAL_HOME"] = str(Path.home())
            home = Path(tempfile.mkdtemp(prefix="trc-shots-home-"))
            os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
            srv = _server.Server(_server.build_base(home / "demo-root"), home / "uvicorn.log")
            url = srv.url
        with sync_playwright() as p:
            browser = p.chromium.launch(**({"channel": "chrome"} if a.chrome else {}))
            try:
                for loc in LOCALES:
                    spec = S.check_spec(spec_for(shots, loc))
                    rows = S.take(browser, url.rstrip("/"), spec, tmp)
                    for r in rows:
                        name = spec["shots"][r["n"] - 1]["name"]
                        if not r["ok"]:
                            failed += 1
                            continue
                        IMG.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(tmp / r["file"]), IMG / f"{name}.webp")
                        print(f"  → img/{name}.webp" + (f"  ({'; '.join(r['errors'])[:200]})" if r["errors"] else ""))
            finally:
                browser.close()
        return 1 if failed else 0
    finally:
        if srv:
            srv.stop()
        for d in (home, tmp):
            if d:
                shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
