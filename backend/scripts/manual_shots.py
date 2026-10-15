"""The 操作手冊's screenshots (SP-558): backend/manual/shots.json, each shot in zh-TW and en, on the
synthetic demo athlete (backend/tests/e2e/_server.py), saved as backend/manual/img/<name>.<locale>.webp —
the names the chapters use (![…](../img/<name>.<locale>.webp)). Run it again when a page the manual
pictures changes; backend/tests/test_manual_sp558.py checks that every picture a chapter uses is there.

The 介紹頁's pictures (SP-622) come the same way: backend/scripts/intro_shots.json → backend/static/intro/,
each also in dark ("schemes": ["light", "dark"] → <name>.<locale>.webp and <name>.<locale>.dark.webp);
backend/tests/test_intro_sp622.py checks that every picture the page uses is there.

    python -m backend.scripts.manual_shots                    # every shot, the manual's and the 介紹頁's (builds and serves the demo, ~1 min)
    python -m backend.scripts.manual_shots schedule-week      # only these
    python -m backend.scripts.manual_shots --set intro --full # only the 介紹頁's, on the full demo year (~2 min)
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
# the 介紹頁 (SP-622): its own list and folder (the manual's img/ holds only what a chapter uses)
INTRO_SPEC = Path(__file__).resolve().parent / "intro_shots.json"
INTRO_IMG = Path(__file__).resolve().parents[1] / "static" / "intro"
SETS = {"manual": (SPEC, IMG, "SP-558"), "intro": (INTRO_SPEC, INTRO_IMG, "SP-622")}
LOCALES = ("zh-TW", "en")


def load(names: list[str] | None = None, which: str = "manual") -> list[dict]:
    shots = json.loads(SETS[which][0].read_text("utf-8"))["shots"]
    if names:
        shots = [s for s in shots if s["name"] in names]
    return shots


def spec_for(shots: list[dict], locale: str, ticket: str = "SP-558") -> dict:
    """backend.tests.e2e.shots' shot list for one locale (page → path with ?lang=, plus the hash); a shot
    with "schemes" once per colour scheme (dark: <name>.<locale>.dark)."""
    from backend.tests.e2e import _server
    out = []
    for s in shots:
        s = dict(s)
        path = _server.PAGES[s.pop("page")] if "page" in s else s.pop("path")
        sep = "&" if "?" in path else "?"
        s["path"] = f"{path}{sep}lang={locale}{s.pop('hash', '')}"
        name = f"{s['name']}.{locale}"
        for scheme in s.pop("schemes", ["light"]):
            out.append({**s, "name": name + (".dark" if scheme == "dark" else ""), "scheme": scheme})
    return {"ticket": ticket, "shots": out}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.scripts.manual_shots", description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="only these shots (shots.json / intro_shots.json names)")
    ap.add_argument("--full", action="store_true", help="the full demo year (52 weeks), not the tests' small 8 weeks")
    ap.add_argument("--set", choices=("all", *SETS), default="all", help="the manual's, the 介紹頁's or both")
    ap.add_argument("--url", help="a running demo server (python -m backend.tests.e2e._server --serve)")
    ap.add_argument("--chrome", action="store_true", help="launch the installed Google Chrome")
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    from backend.tests.e2e import _server, shots as S
    sets = [w for w in SETS if a.set in ("all", w)]
    todo = {w: load(a.names, w) for w in sets}
    if a.names:
        unknown = set(a.names) - {s["name"] for v in todo.values() for s in v}
        if unknown:
            raise SystemExit(f"no such shot: {sorted(unknown)}")
    home = srv = None
    url = a.url
    tmp = Path(tempfile.mkdtemp(prefix="trc-manual-shots-"))
    failed = 0
    try:
        if not url:
            os.environ["WKO5COACH_TEST_REAL_HOME"] = str(Path.home())
            home = Path(tempfile.mkdtemp(prefix="trc-shots-home-"))
            os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
            if a.full:                     # the 介紹頁's: a year of training, the real 50K course
                import datetime as dt
                from backend.demo import build as B
                base = B.build(home / "demo-root", anchor=dt.date.today(), weeks=52, small=False, warm=False)
            else:
                base = _server.build_base(home / "demo-root")
            srv = _server.Server(base, home / "uvicorn.log")
            url = srv.url
        with sync_playwright() as p:
            browser = p.chromium.launch(**({"channel": "chrome"} if a.chrome else {}))
            try:
                for which, shots in todo.items():
                    if not shots:
                        continue
                    _spec, img, ticket = SETS[which]
                    for loc in LOCALES:
                        spec = S.check_spec(spec_for(shots, loc, ticket))
                        rows = S.take(browser, url.rstrip("/"), spec, tmp)
                        for r in rows:
                            name = spec["shots"][r["n"] - 1]["name"]
                            if not r["ok"]:
                                failed += 1
                                continue
                            img.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(tmp / r["file"]), img / f"{name}.webp")
                            print(f"  → {img.name}/{name}.webp" + (f"  ({'; '.join(r['errors'])[:200]})" if r["errors"] else ""))
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
