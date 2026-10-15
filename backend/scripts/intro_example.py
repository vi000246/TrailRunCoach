"""The 介紹頁's built-in race calculator examples (SP-622; SP-637: three of them): the demo athlete's
示範山徑越野 50K, 示範百岳 3 日縱走 and 示範城市半程馬拉松, each computed by the race calculator page itself
and frozen as a share snapshot (engine/racepower/share.snapshot) in backend/demo/ (share.EXAMPLES) —
served at /share/<id> (api/racepower.share_data) and, trimmed, to the 介紹頁's elevation profile
(api/intro.example) without any account, tenant or file of anyone's. The runner and the races are
fictional (backend/demo), nothing in them is a real person's.

SP-637: the same run also freezes the fictional runner the 介紹頁's small calculator computes for
(backend/demo/intro_runner.json, api/intro.estimate): the part of the demo athlete's /inputs that the
road / trail / 百岳 prediction reads — never anyone else's.

    python -m backend.scripts.intro_example                          # builds the full demo athlete (~2 min) and serves it
    python -m backend.scripts.intro_example --kind road              # just one of trail | baiyue | road
    python -m backend.scripts.intro_example --url http://127.0.0.1:8123   # a running demo server (a full build: the
                                                                           # tests' small one has a 15 km "50K")

Run it again when the calculator's result changes shape (the share page reads the snapshot as it is).
Same setup as ./shots.sh (Playwright)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "demo"
RUNNER = DEMO / "intro_runner.json"
# kind -> (the demo event, the snapshot's title, the shortest course a full build has)
KINDS = {
    "trail": ("demo-50k", "示範山徑越野 50K（虛構跑者）", 40.0),
    "baiyue": ("demo-baiyue", "示範百岳 3 日縱走（虛構跑者）", 25.0),
    "road": ("demo-half", "示範城市半程馬拉松（虛構跑者）", 18.0),
}
# the /inputs keys calc.predict reads (road / trail / 百岳): the fictional runner, nothing else
RUNNER_KEYS = ("weight", "training_conditions", "cp", "tte", "re", "auto_prior", "riegel", "hiking", "aet")


def bring_forward(plan_json: Path, today: dt.date) -> None:
    """The demo season's 百岳 3 日 and half marathon lie in the past (the calculator lists only events still
    ahead): moved to the 50K's race day for the capture — a copy of the demo, the athlete unchanged."""
    from backend.engine.planning import Plan
    plan = Plan.load(plan_json)
    ref = next((e for e in plan.events if e.id == KINDS["trail"][0]), None)
    day = ref.date if ref else (today + dt.timedelta(days=28)).isoformat()
    for e in plan.events:
        if e.id in (KINDS["baiyue"][0], KINDS["road"][0]) and e.end < today:
            e.date = day
    plan.save(plan_json)


def out_file(kind: str) -> Path:
    from backend.engine.racepower import share as SH
    return SH.EXAMPLES[SH.example_id(kind)]


# what the page asks before it computes a 百岳 trip (跟團／自己走 has no default): 自己走
CLICK = {"demo-baiyue": '#tripkind button[data-k="solo"]'}


def capture(url: str, event: str = "demo-50k") -> tuple[dict, dict]:
    """(the /plan request, its answer) as the race calculator page sends it for that event."""
    from playwright.sync_api import sync_playwright
    got: dict = {}

    def on(resp):
        # the page may plan another event first (the last one opened): only this event's course counts
        if resp.url.endswith("/api/v1/racepower/plan") and resp.request.method == "POST" and resp.ok:
            req = resp.request.post_data_json or {}
            if ((req.get("course") or {}).get("event_id")) == event:
                got["req"], got["res"] = req, resp.json()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.on("response", on)
            page.goto(f"{url.rstrip('/')}/api/v1/racepower/page?event={event}&lang=zh-TW")
            if event in CLICK:
                page.locator(CLICK[event]).click(timeout=60_000)
            for _ in range(120):
                if "res" in got:
                    break
                page.wait_for_timeout(500)
        finally:
            browser.close()
    if "res" not in got:
        raise SystemExit("the race calculator computed nothing for " + event)
    return got["req"], got["res"]


def runner(url: str) -> dict:
    """The demo athlete's /inputs, only the keys the prediction reads (RUNNER_KEYS)."""
    import urllib.request
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/v1/racepower/inputs", timeout=300) as r:
        d = json.loads(r.read().decode("utf-8"))
    return {"_about": "SP-637: the 介紹頁's fictional runner (backend/scripts/intro_example.py: the demo athlete)",
            **{k: d.get(k) for k in RUNNER_KEYS}}


def freeze(req: dict, plan: dict, title: str = KINDS["trail"][1], min_km: float = 40.0) -> dict:
    from backend.engine.racepower import share as SH
    km = (plan.get("summary") or {}).get("km") or 0
    if km < min_km:
        raise SystemExit(f"the course is {km:.1f} km: a small demo build? (use a full one)")
    now = dt.datetime.now().replace(microsecond=0)
    snap = SH.snapshot(plan, title=title, include_weight=False, expires_days=None, request=req, now=now)
    snap["example"] = True           # the share page's own: never a user's
    return undated(snap, now.date())


def undated(snap: dict, today: dt.date) -> dict:
    """SP-637 (SP-622 Q2-3): an example shows no calendar date — once it passed, the race would read as
    one already run. The race day becomes 「賽前 N 週」 (inputs.weeks_before, from the day it was computed;
    None for a race before that), the creation time goes; a clock time stays."""
    snap = dict(snap)
    inp = dict(snap.get("inputs") or {})
    weeks = None
    try:
        weeks = round((dt.date.fromisoformat(str(inp.get("date"))[:10]) - today).days / 7)
    except ValueError:
        pass
    inp["date"] = None
    inp["weeks_before"] = weeks if weeks is not None and weeks > 0 else None
    snap["inputs"] = inp
    snap["created"] = None
    return scrub_dates(snap)


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def scrub_dates(o):
    """Any other date left in a value: 'YYYY-MM-DDTHH:MM…' keeps its clock time, a bare date goes."""
    if isinstance(o, dict):
        return {k: scrub_dates(v) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub_dates(v) for v in o]
    if isinstance(o, str) and _DATE.search(o):
        m = re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2})(?::\d{2})?", o)
        return m.group(1) if m else _DATE.sub("", o).strip() or None
    return o


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.scripts.intro_example", description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", help="a running demo server built in full (run bring_forward on its plan.json first)")
    ap.add_argument("--kind", choices=[*KINDS, "all"], default="all", help="which example (default: all three)")
    ap.add_argument("--no-runner", action="store_true", help="leave backend/demo/intro_runner.json as it is")
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
            bring_forward(base / "plan.json", dt.date.today())
            srv = _server.Server(base, home / "uvicorn.log")
            url = srv.url
        for kind in (list(KINDS) if a.kind == "all" else [a.kind]):
            event, title, min_km = KINDS[kind]
            req, plan = capture(url, event)
            snap = freeze(req, plan, title, min_km)
            out = out_file(kind)
            out.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", "utf-8")
            s = snap["summary"]
            print(f"→ {out.relative_to(out.parents[2])}: {s.get('km', 0):.1f} km ↑{s.get('gain_m', 0):.0f} m, "
                  f"{len(snap['segments'])} segments")
        if not a.no_runner:
            RUNNER.write_text(json.dumps(runner(url), ensure_ascii=False, indent=1) + "\n", "utf-8")
            print(f"→ {RUNNER.relative_to(RUNNER.parents[2])}")
        return 0
    finally:
        if srv:
            srv.stop()
        if home:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
