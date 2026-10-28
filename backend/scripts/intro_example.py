"""The 介紹頁's built-in race calculator examples (SP-622; SP-637: three of them): the demo athlete's
示範山徑越野 50K, 示範百岳 3 日縱走 and 示範城市半程馬拉松, each computed by the race calculator page itself
and frozen as a share snapshot (engine/racepower/share.snapshot) in backend/demo/ (share.EXAMPLES) —
served at /share/<id> (api/racepower.share_data) and, trimmed, to the 介紹頁's elevation profile
(api/intro.example) without any account, tenant or file of anyone's. The runner and the races are
fictional (backend/demo), nothing in them is a real person's.

SP-969: with the trail example, its finish time taken apart factor by factor (planner.trail_breakdown: the
calculator run again with one factor more each time, the layers adding up to the example's time) is frozen
beside it (backend/demo/intro_breakdown.json, api/intro.breakdown: the 介紹頁's waterfall). --breakdown-only keeps
the frozen example and writes only its breakdown — refused unless it adds up to the example's time. SP-969 Q2:
the trail example is computed with assumed race settings (ASSUMED: effort, 地形難度, fatigue δ, aid stations)
on top of the page's own request; the 介紹頁 names them.

    python -m backend.scripts.intro_example --breakdown-only        # the waterfall for the example as it is

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
from typing import Optional

DEMO = Path(__file__).resolve().parents[1] / "demo"
RUNNER = DEMO / "intro_runner.json"
# kind -> (the demo event, the snapshot's title, the shortest course a full build has)
KINDS = {
    "trail": ("demo-50k", "示範山徑越野 50K（虛構跑者）", 40.0),
    "baiyue": ("demo-baiyue", "示範百岳 3 日縱走（虛構跑者）", 25.0),
    "road": ("demo-half", "示範城市半程馬拉松（虛構跑者）", 18.0),
}
# SP-969 (Q2): the trail example's assumed race settings — what a runner would set in the calculator for a
# typical mountain 50K, so every layer of the waterfall shows something real: a race effort (95 %, 吃力) below all-out,
# 技術路段多 (the 地形難度 prior × 1.15), the long-race fatigue at the model's prior δ (Clark 2019, 5 %/h
# after 1 h; the demo athlete's own runs measure none) and three aid stations with the minutes typed in
# (the stop model's share would put 2.5 h of stops on a 6 h race). Sent with the page's own request
# (replay); the 介紹頁 says these are the example's assumptions (intro_breakdown.json "assumed").
ASSUMED = {
    "trail": {"effort_target": 0.95, "terrain_difficulty": "technical", "assume_delta": 0.05,
              "stops": [{"km": 12.0, "type": "aid", "minutes": 3.0}, {"km": 25.0, "type": "big", "minutes": 8.0},
                        {"km": 38.0, "type": "aid", "minutes": 3.0}]},
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


def event_day(plan_json: Path, event: str) -> Optional[dt.date]:
    from backend.engine.planning import Plan
    e = next((e for e in Plan.load(plan_json).events if e.id == event), None)
    return dt.date.fromisoformat(str(e.date)[:10]) if e else None


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


PLAN = DEMO / "intro_plan.json"
BREAKDOWN = DEMO / "intro_breakdown.json"
# the factors the breakdown names, in the calculator's order (planner.trail_breakdown)
BREAKDOWN_KEYS = ("flat", "climb", "terrain", "durability", "heat", "altitude", "memory", "night")


def assumed(req: dict, kind: str) -> dict:
    """SP-969: the captured /plan request with the example's assumed settings (ASSUMED) on top."""
    body = json.loads(json.dumps(req))
    body.update(json.loads(json.dumps(ASSUMED.get(kind) or {})))
    return body


def replay(url: str, req: dict, snap: Optional[dict] = None) -> dict:
    """SP-969: the /plan request sent again — with `snap`, under the frozen example's race-day temperature and
    humidity (its inputs.temp_c / rh_pct; the course and the runner as captured): the plan whose breakdown
    goes with an example kept as it is (--breakdown-only: the forecast for a race day a few weeks out moves
    with the day the script runs)."""
    body = json.loads(json.dumps(req))
    if snap is not None:
        inp = snap.get("inputs") or {}
        body["env_to"] = {**(body.get("env_to") or {}), "temp_c": inp.get("temp_c"), "rh_pct": inp.get("rh_pct")}
    return _get(url, "/api/v1/racepower/plan", body)


def breakdown(plan: dict, snap: dict) -> dict:
    """SP-969: the trail example's moving time factor by factor, as the calculator gave it with the plan
    (summary.breakdown) — refused unless its layers add up to the frozen example's time (±1 s)."""
    bd = (plan.get("summary") or {}).get("breakdown") or {}
    layers = bd.get("layers") or []
    t = float((snap.get("summary") or {}).get("time_s") or 0.0)
    if not layers or any(x.get("key") not in BREAKDOWN_KEYS for x in layers):
        raise SystemExit("the trail plan came without its breakdown (not the HR model's total?)")
    total = sum(float(x["t_s"]) for x in layers)
    if abs(total - t) > 1.0:
        s = plan.get("summary") or {}
        raise SystemExit(f"the breakdown adds up to {total:.1f} s, the example is {t:.1f} s "
                         f"(the plan: {len(plan.get('segments') or [])} segments, M̄ {s.get('M')}; the example: "
                         f"{len(snap.get('segments') or [])} segments, M̄ {(snap.get('summary') or {}).get('M')}; "
                         + ", ".join(f"{x['key']} {float(x['t_s']):.0f} s" for x in layers) + ")")
    inp = snap.get("inputs") or {}
    terr = bd.get("terrain") or {}
    return {"_about": "SP-969: the 介紹頁's waterfall (backend/scripts/intro_example.py: the trail example's plan, "
                      "planner.trail_breakdown; a fictional runner)",
            "time_s": t, "layers": [{"key": x["key"], "t_s": float(x["t_s"])} for x in layers],
            "km": bd.get("km"), "gain_m": bd.get("gain_m"), "divisor": bd.get("divisor"),
            "terrain": {"level": terr.get("level"), "mult": terr.get("mult")}, "delta": bd.get("delta"),
            "heat_beta": bd.get("heat_beta"), "temp_c": inp.get("temp_c"), "rh_pct": inp.get("rh_pct"),
            "altitude_m": inp.get("altitude_m"), "legs": len(snap.get("segments") or []),
            # the example's assumed settings as the plan took them (ASSUMED): the page names them
            "assumed": {"effort": (snap.get("effort") or {}).get("f"), "terrain": terr.get("level"),
                        "delta": bd.get("delta"),
                        "stops": len(inp.get("stops") or []),
                        "stop_min": sum(float(s.get("minutes") or 0) for s in inp.get("stops") or [])}}
PLAN_WEEKS = 4          # 賽前 4 週 … the race week (the 50K on its Saturday)


def _get(url: str, path: str, body: Optional[dict] = None) -> dict:
    import urllib.request
    data = json.dumps(body).encode("utf-8") if body is not None else None
    rq = urllib.request.Request(url.rstrip("/") + path, data=data, method="POST" if body is not None else "GET",
                                headers={"Content-Type": "application/json", "Cookie": "lang=zh-TW"})
    with urllib.request.urlopen(rq, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def calendar(url: str, race_day: dt.date) -> dict:
    """SP-637 R1: the 介紹頁's 課表 calendar — the demo athlete's plan from the Monday PLAN_WEEKS weeks before
    the 50K to its race week: per week its phase and planned hours, per day its sessions (kind, the app's own
    title, minutes, km, climb, the structure as the editor derives it) and the races on it. No date: weeks
    count down to the race, days are weekdays."""
    monday = race_day - dt.timedelta(days=race_day.weekday())
    first = monday - dt.timedelta(weeks=PLAN_WEEKS - 1)
    last = monday + dt.timedelta(days=6)
    cal = _get(url, f"/api/v1/overview/plan/calendar?start={first}&end={last}")
    rows = {w["start"]: w for w in cal.get("week_rows") or []}
    races = {}
    for r in cal.get("races") or []:
        if r.get("kind") == "race" and r.get("day"):           # the trail races (not the 百岳／road A's moved here)
            races[r["day"]] = {"priority": r.get("priority"), "a": r.get("id") == KINDS["trail"][0]}
    weeks = []
    for w in range(PLAN_WEEKS):
        start = first + dt.timedelta(weeks=w)
        row = rows.get(start.isoformat()) or {}
        days = []
        for i in range(7):
            day = (start + dt.timedelta(days=i)).isoformat()
            ss = []
            for s in cal.get("sessions") or []:
                if s.get("day") != day or s.get("state") not in ("active", "done") or s.get("kind") == "race":
                    continue
                steps = (s.get("steps") or None) or _get(url, "/api/v1/overview/plan/steps/derive", {"uid": s["uid"]}).get("steps")
                ss.append({"kind": s.get("kind"), "title": s.get("title"), "min": s.get("minutes"),
                           "km": s.get("distance_km"), "gain_m": s.get("climb_m"),
                           "items": (steps or {}).get("items") or []})
            days.append({"sessions": ss, **({"race": races[day]} if day in races else {})})
        weeks.append({"before": PLAN_WEEKS - 1 - w, "phase": row.get("phase"),
                      "hours": round(row.get("planned_hours") or 0, 1), "days": days})
    # the plan is laid out day by day only to its horizon (the app plans about two weeks ahead): the days
    # after it are counted, so the page can show them as not planned yet
    try:
        planned = (dt.date.fromisoformat(str(cal.get("horizon_end"))[:10]) - first).days + 1
    except ValueError:
        planned = PLAN_WEEKS * 7
    return {"planned_days": max(0, min(PLAN_WEEKS * 7, planned)),
            "_about": "SP-637 R1: the 介紹頁's 課表 calendar (backend/scripts/intro_example.py: the demo athlete's plan "
                      "before 示範山徑越野 50K; a fictional runner, no date)", "weeks": weeks}


def freeze(req: dict, plan: dict, title: str = KINDS["trail"][1], min_km: float = 40.0) -> dict:
    from backend.engine.racepower import share as SH
    km = (plan.get("summary") or {}).get("km") or 0
    if km < min_km:
        raise SystemExit(f"the course is {km:.1f} km: a small demo build? (use a full one)")
    now = dt.datetime.now().replace(microsecond=0)
    snap = SH.snapshot(plan, title=title, include_weight=False, expires_days=None, request=req, now=now)
    snap["example"] = True           # the share page's own: never a user's
    # SP-784: an example's 分段目標 rows are derived from its own segments when it is read (share.with_rows),
    # as for the other examples — the file keeps none (SP-969: frozen again after SP-784 had added them)
    snap.pop("chart_rows", None)
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
    ap.add_argument("--no-plan", action="store_true", help="leave backend/demo/intro_plan.json as it is")
    ap.add_argument("--race-day", help="with --url: the 50K's date (YYYY-MM-DD) for the 課表 calendar")
    ap.add_argument("--breakdown-only", action="store_true",
                    help="SP-969: keep the frozen trail example, write only its breakdown (it must add up to it)")
    a = ap.parse_args(argv)
    race_day = dt.date.fromisoformat(a.race_day) if a.race_day else None
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
            race_day = race_day or event_day(base / "plan.json", KINDS["trail"][0])
            srv = _server.Server(base, home / "uvicorn.log")
            url = srv.url
        if a.breakdown_only:
            # SP-969: the trail example stays as it is; only its breakdown, from the calculator run again on
            # the captured request under the example's own race-day conditions — written when it adds up
            req, _plan = capture(url, KINDS["trail"][0])
            snap = json.loads(out_file("trail").read_text("utf-8"))
            bd = breakdown(replay(url, assumed(req, "trail"), snap), snap)
            BREAKDOWN.write_text(json.dumps(bd, ensure_ascii=False, indent=1) + "\n", "utf-8")
            print(f"→ {BREAKDOWN.relative_to(BREAKDOWN.parents[2])}: "
                  + ", ".join(f"{x['key']} {x['t_s']:.0f} s" for x in bd["layers"]))
            return 0
        for kind in (list(KINDS) if a.kind == "all" else [a.kind]):
            event, title, min_km = KINDS[kind]
            req, plan = capture(url, event)
            if ASSUMED.get(kind):
                # SP-969: the page's own request with the example's assumed settings, computed again
                req = assumed(req, kind)
                plan = replay(url, req)
            snap = freeze(req, plan, title, min_km)
            out = out_file(kind)
            out.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", "utf-8")
            s = snap["summary"]
            print(f"→ {out.relative_to(out.parents[2])}: {s.get('km', 0):.1f} km ↑{s.get('gain_m', 0):.0f} m, "
                  f"{len(snap['segments'])} segments")
            if kind == "trail":
                bd = breakdown(plan, snap)
                BREAKDOWN.write_text(json.dumps(bd, ensure_ascii=False, indent=1) + "\n", "utf-8")
                print(f"→ {BREAKDOWN.relative_to(BREAKDOWN.parents[2])}: "
                      + ", ".join(f"{x['key']} {x['t_s']:.0f} s" for x in bd["layers"]))
        if not a.no_runner:
            RUNNER.write_text(json.dumps(runner(url), ensure_ascii=False, indent=1) + "\n", "utf-8")
            print(f"→ {RUNNER.relative_to(RUNNER.parents[2])}")
        if not a.no_plan:
            if race_day is None:
                raise SystemExit("the 課表 calendar needs --race-day with --url (or --no-plan)")
            PLAN.write_text(json.dumps(calendar(url, race_day), ensure_ascii=False, indent=1) + "\n", "utf-8")
            print(f"→ {PLAN.relative_to(PLAN.parents[2])}")
        return 0
    finally:
        if srv:
            srv.stop()
        if home:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
