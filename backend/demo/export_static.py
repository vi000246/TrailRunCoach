"""
Static, read-only demo: export the demo instance as a folder of files any
static host serves (GitHub Pages, a Hugging Face static Space ...):

    python -m backend.demo.export_static --root <demo-root> [--out dist/static-demo]
    python -m backend.demo.export_static --root <demo-root> --update --reuse <old export> --out <new>
        (the root only gained activities since the old export: re-crawl what they change)

<demo-root> is a folder built by `python -m backend.demo.build --root ...`. It
is copied to a temporary folder first (the app writes caches next to its
data), so the source is never modified.

How it works:
  1. the app runs in demo mode in this process; a headless Chromium opens every
     page the demo exposes and clicks through its view-only controls (tabs,
     view / chart pickers, the demo activities ...). The browser never touches
     the network for the app: every request is answered in-process
     (Playwright route -> FastAPI TestClient), so no port is opened;
  2. every GET the pages make is saved as data/<fnv64(key)>.json, where key =
     the decoded path + "?" + the query sorted by name (data_key()); the
     view-only computations that are POSTs with the page's default inputs
     (race calculator ...) are saved as data/p<fnv64(method path body)>.json; the
     structure editor's /steps/check and /steps/derive are computed in the browser
     (static_shim.js Steps) from data/steps_ctx.json (static_steps.py);
  3. the pages are written flat at the root (index.html = the demo landing),
     with static/trc_static.js injected first: it maps /api/v1/... requests to
     those files, freezes the clock at the export day, keeps the schedule's
     edits in localStorage (an overlay) and refuses every other write with
     「唯讀示範：這個操作在示範版不能用」.

The output uses relative URLs only, so it works from any sub-path
(https://<user>.github.io/<repo>/). See deploy/static/README.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qsl, unquote, urlsplit

API = "/api/v1/"
HOST = "http://trc.demo"
REPO = Path(__file__).resolve().parents[2]
STATIC_SRC = REPO / "backend" / "static"
SHIM_SRC = Path(__file__).resolve().parent / "static_shim.js"
DEFAULT_OUT = REPO / "dist" / "static-demo"

# server page path -> the static file (flat at the root: relative URLs stay simple)
PAGES: dict[str, str] = {
    "/demo": "index.html",
    "/api/v1/overview/page": "overview.html",
    "/api/v1/overview/plan/schedule/page": "schedule.html",
    "/api/v1/overview/plan/compliance/page": "compliance.html",
    "/api/v1/wko5/viewer": "charts.html",
    "/api/v1/plan/page": "plan.html",
    "/api/v1/wko5/activities/page": "activities.html",
    "/api/v1/routes/page": "routes.html",
    "/api/v1/racepower/page": "racepower.html",
}
# pages that only redirect elsewhere in the app
PAGE_ALIASES: dict[str, str] = {
    "/": "index.html",
    "/api/v1/achievements/page": "activities.html#achievements",
}

# POSTs that change nothing stored (the demo's NO_SANDBOX computations): the crawl
# records their answers to the pages' default inputs; any other body is refused
COMPUTE_POSTS = re.compile(
    r"^/api/v1/(racepower/(predict|course|plan|course/event/[^/]+)|expr/evaluate"
    r"|overview/plan/(steps-preview|steps/check|steps/derive|blackouts/preview|prefs/conflicts))$")
# ... of which the structure editor's: answered in the browser (static_shim.js Steps), not recorded
STEPS_POSTS = re.compile(r"^/api/v1/overview/plan/steps/(check|derive)$")

# the schedule edits the shim keeps in the browser (static_shim.js OVERLAY_RULES);
# the crawl answers them with 403 like any other write (it never changes the data)
OVERLAY_WRITES: list[tuple[str, str]] = [
    ("PATCH", r"^/api/v1/overview/plan/sessions/[^/]+$"),
    ("DELETE", r"^/api/v1/overview/plan/sessions/[^/]+$"),
    ("POST", r"^/api/v1/overview/plan/sessions$"),
    ("POST", r"^/api/v1/overview/plan/rest-days$"),
    ("DELETE", r"^/api/v1/overview/plan/rest-days/[^/]+$"),
]

# GETs never saved: live state static_shim.js answers itself
SKIP_GET = re.compile(r"^/api/v1/(session|wko5/dataset/status)$")

# controls of writes the live demo allows but the static one can't do (shell.js locks them
# with 「唯讀示範：這個操作在示範版不能用」); the schedule's session edits stay open (the overlay)
STATIC_LOCKS = ", ".join([
    # 週期規劃: events, phases, GPX
    "#gpxpick, #evsubmit, #addphase, #savephases, #tomanual, #toauto, [data-del-event], [data-edit-event]",
    # 課表: 依實際進度重排, 課表偏好 / 不排課日期 save, 換一個 (library variants), expired delete
    "#rec-btn, #pf-save, #pf-reset, #bo-save, #bo-del, #sd-swap, #vd button[data-vk]",
    # 賽事計算機: GPX upload / attach, CSV import of aid stations
    "#gpxfile, #gpx-attach, #stop-import, input[type=file][accept*='.gpx' i]",
])

FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
MASK64 = (1 << 64) - 1


# --------------------------------------------------------------------------- keys
def fnv64(s: str) -> str:
    """FNV-1a 64 over the UTF-8 bytes, 16 hex digits (static_shim.js fnv64 is identical)."""
    h = FNV_OFFSET
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * FNV_PRIME) & MASK64
    return f"{h:016x}"


def data_key(url: str) -> str:
    """The canonical key of a GET: decoded path + '?' + the query pairs sorted by
    name (a name's values keep their order), decoded, joined with '&'. No query: the path."""
    parts = urlsplit(url)
    path = unquote(parts.path)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    pairs = sorted(pairs, key=lambda kv: kv[0])          # stable: same-name values keep their order
    if not pairs:
        return path
    return path + "?" + "&".join(f"{k}={v}" for k, v in pairs)


def data_file(url: str) -> str:
    """data/<name> of a GET's saved answer."""
    return f"{fnv64(data_key(url))}.json"


def post_key(method: str, url: str, body: str) -> str:
    return f"{method.upper()} {data_key(url)}\n{body or ''}"


def post_file(method: str, url: str, body: str) -> str:
    return f"p{fnv64(post_key(method, url, body))}.json"


def static_page_for(path: str) -> Optional[str]:
    """The static file of a server page path (None: not part of the static demo)."""
    p = path.rstrip("/") or "/"
    return PAGES.get(p) or PAGE_ALIASES.get(p)


def is_overlay_write(method: str, path: str) -> bool:
    return any(method.upper() == m and re.match(rx, path) for m, rx in OVERLAY_WRITES)


# --------------------------------------------------------------------------- scrub
def scrubber(secrets_: Iterable[str]) -> Callable[[str], str]:
    """Replace local absolute paths (repo, data folder, home) in a text with a neutral one."""
    subs = []
    for s in sorted({x for x in secrets_ if x and len(x) > 3}, key=len, reverse=True):
        for v in {s, s.replace("\\", "/"), s.replace("\\", "\\\\"), s.replace("/", "\\\\")}:
            subs.append(v)
    rx = re.compile("|".join(re.escape(s) for s in subs), re.I) if subs else None

    def run(text: str) -> str:
        return rx.sub("demo", text) if rx else text
    return run


def minify_json(raw: bytes) -> bytes:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------- the app
class DemoApp:
    """The demo instance in this process (TestClient: no port)."""

    def __init__(self, home: Path):
        os.environ["WKO5COACH_MODE"] = "demo"
        os.environ["WKO5COACH_HOME"] = str(home)
        os.environ["WKO5COACH_COOKIE_SECURE"] = "0"
        os.environ.setdefault("TRC_DEFAULT_LOCALE", "zh-TW")
        for k in ("WKO5COACH_DEMO_REBUILD", "WKO5COACH_DEMO_CTA_URL"):
            os.environ.pop(k, None)
        from fastapi.testclient import TestClient
        from backend import tenancy_mw as TM
        from backend.security import ratelimit as RL
        from backend.main import build_app
        # the crawl is one client making thousands of requests: lift the per-IP limits
        TM.REQ_BUCKET = RL.Buckets(rate=10 ** 9, per=60)
        TM.HEAVY_IP = RL.Buckets(rate=10 ** 9, per=60)
        TM.CREATE_HOUR = RL.Buckets(rate=10 ** 9, per=3600)     # computations that make a sandbox
        TM.CREATE_DAY = RL.Buckets(rate=10 ** 9, per=86400)     # (racepower/course/event…) would hit 3/h
        TM.WRITE_MIN = RL.Buckets(rate=10 ** 9, per=60)
        TM.HEAVY_GATE = RL.Gate(n=8, wait_s=600.0)
        self.app = build_app(demo=True)
        self.client = TestClient(self.app, base_url=HOST, follow_redirects=False)
        self.csrf = "static-export-csrf"

    def __enter__(self):
        self.client.__enter__()
        return self

    def __exit__(self, *exc):
        # close the pooled SQLite connections (else Windows keeps the temporary copy locked)
        try:
            from backend.db import database as DB
            for key in list(DB._POOL):
                self.client.portal.call(DB.dispose, Path(key))
        except Exception:                  # noqa: BLE001 — cleanup only
            pass
        self.client.__exit__(*exc)

    def request(self, method: str, url: str, body: Optional[bytes] = None,
                headers: Optional[dict] = None) -> tuple[int, dict, bytes]:
        self.client.cookies.clear()
        h = {"accept-language": "zh-TW"}
        if headers:
            h.update({k: v for k, v in headers.items() if k.lower() in ("content-type", "accept")})
        if method not in ("GET", "HEAD"):
            h["x-trc-csrf"] = self.csrf
            h["cookie"] = f"trc_csrf={self.csrf}"
        r = self.client.request(method, url, content=body, headers=h)
        out = {k.lower(): v for k, v in r.headers.items() if k.lower() in ("content-type", "location")}
        return r.status_code, out, r.content

    def get(self, url: str) -> tuple[int, dict, bytes]:
        return self.request("GET", url)


# --------------------------------------------------------------------------- recording
READONLY_MSG = "唯讀示範：這個操作在示範版不能用"


class Recorder:
    def __init__(self):
        self.gets: dict[str, dict] = {}          # data_key -> {url, status, ctype, body}
        self.posts: dict[str, dict] = {}         # post_file -> {method, url, body_in, status, body}
        self.writes: list[tuple[str, str]] = []  # refused writes seen during the crawl
        self.files: dict[str, bytes] = {}         # data/<name> written as is (steps_ctx.json)
        self.log: list[str] = []

    def add_get(self, url: str, status: int, ctype: str, body: bytes) -> None:
        k = data_key(url)
        if k not in self.gets or self.gets[k]["status"] != 200:
            self.gets[k] = {"url": url, "status": status, "ctype": ctype, "body": body}

    def add_post(self, method: str, url: str, body_in: str, status: int, body: bytes) -> None:
        if status == 429:                         # the demo's rate limits: not an answer to keep
            return
        self.posts[post_file(method, url, body_in)] = {"method": method, "url": url, "body_in": body_in,
                                                        "status": status, "body": body}


def api_path(url: str) -> Optional[str]:
    sp = urlsplit(url)
    if f"{sp.scheme}://{sp.netloc}" != HOST:
        return None
    return sp.path


class Crawler:
    """Headless Chromium whose requests to HOST are answered by the in-process app."""

    def __init__(self, app: DemoApp, rec: Recorder, verbose: bool = False):
        self.app, self.rec, self.verbose = app, rec, verbose
        self.pending = 0
        self.last_activity = time.time()

    # -- routing
    def handle(self, route, request) -> None:
        url = request.url
        path = api_path(url)
        if path is None:
            host = urlsplit(url).netloc
            if host == "cdnjs.cloudflare.com":        # the chart / map libraries
                route.continue_()
            else:                                      # map tiles, anything else: no network
                route.abort()
            return
        self.pending += 1
        self.last_activity = time.time()
        try:
            self._handle(route, request, url, path)
        finally:
            self.pending -= 1
            self.last_activity = time.time()

    def _handle(self, route, request, url: str, path: str) -> None:
        method = request.method.upper()
        if method in ("GET", "HEAD"):
            status, headers, body = self.app.request("GET", url, headers=dict(request.headers))
            ctype = headers.get("content-type", "")
            if path.startswith(API) and not path.startswith(API + "static/") and "json" in ctype \
                    and not SKIP_GET.match(path) and static_page_for(path) is None:
                self.rec.add_get(url, status, ctype, body)
                if self.verbose:
                    print(f"  GET {status} {len(body):>8} {data_key(url)[:160]}")
            route.fulfill(status=status, headers=headers, body=body)
            return
        body_in = request.post_data or ""
        if COMPUTE_POSTS.match(path):
            status, headers, body = self.app.request(method, url, body=body_in.encode("utf-8"),
                                                     headers=dict(request.headers))
            if "json" in headers.get("content-type", "") and not STEPS_POSTS.match(path):
                self.rec.add_post(method, url, body_in, status, body)
            if self.verbose:
                print(f"  {method} {status} {len(body):>8} {path} (precomputed)")
            route.fulfill(status=status, headers=headers, body=body)
            return
        self.rec.writes.append((method, path))
        if self.verbose:
            print(f"  {method} refused {path}")
        route.fulfill(status=403, headers={"content-type": "application/json"},
                      body=json.dumps({"code": "STATIC_READONLY",
                                       "detail": {"code": "STATIC_READONLY", "message": READONLY_MSG}},
                                      ensure_ascii=False))

    # -- waiting
    def settle(self, page, quiet_s: float = 1.2, max_s: float = 90.0) -> None:
        """Wait until no routed request has been in flight for quiet_s."""
        t0 = time.time()
        while time.time() - t0 < max_s:
            page.wait_for_timeout(150)
            if self.pending == 0 and time.time() - self.last_activity >= quiet_s:
                return

    def click(self, page, selector: str, quiet_s: float = 0.8, nth: int = 0, button: str = "left") -> bool:
        """Click the nth visible, enabled match (if any) and wait for its requests."""
        loc = page.locator(selector)
        try:
            n = loc.count()
            if nth >= n:
                return False
            el = loc.nth(nth)
            if not el.is_visible() or not el.is_enabled():
                return False
            el.click(timeout=3000, button=button)
        except Exception:                  # noqa: BLE001 — a control that moved / detached: skip it
            return False
        self.settle(page, quiet_s=quiet_s)
        return True

    def click_each(self, page, selector: str, quiet_s: float = 0.8) -> int:
        n = 0
        try:
            count = page.locator(selector).count()
        except Exception:                  # noqa: BLE001
            return 0
        for i in range(count):
            n += self.click(page, selector, quiet_s=quiet_s, nth=i)
        return n

    def escape(self, page) -> None:
        try:
            page.keyboard.press("Escape")
        except Exception:                  # noqa: BLE001
            pass
        self.settle(page, quiet_s=0.4)


# --------------------------------------------------------------------------- page tasks
# Each opens its page (served by the app) and clicks through the view-only controls; the
# requests they make are what the static page will make for the same clicks.
def task_overview(cr: Crawler, page) -> None:
    for unit, steps in (("week", 26), ("month", 12), ("year", 3)):
        cr.click(page, f'#unit button[data-u="{unit}"]')
        for _ in range(steps):
            if not cr.click(page, "#prev", quiet_s=0.5):
                break
        cr.click(page, "#now")
    cr.click(page, '#unit button[data-u="week"]')


def task_schedule(cr: Crawler, page) -> None:
    # month view: the months around today (the Python pass adds more ranges)
    for _ in range(3):
        cr.click(page, "#prev", quiet_s=0.6)
    cr.click(page, "#go-today")
    for _ in range(2):
        cr.click(page, "#next", quiet_s=0.6)
    cr.click(page, "#go-today")
    if cr.click(page, '#view-seg button[data-view="week"]'):
        for _ in range(2):
            cr.click(page, "#prev", quiet_s=0.6)
        cr.click(page, "#go-today")
        cr.click(page, '#view-seg button[data-view="month"]')
    # each session of this month: its dialog (the structure editor's derive / check, variants)
    chips = page.locator("#cal button.chip[data-uid]")
    for i in range(min(chips.count(), 60)):
        if cr.click(page, "#cal button.chip[data-uid]", nth=i, quiet_s=0.6):
            cr.escape(page)
            cr.escape(page)
    # an empty future day: the add dialog (default structure)
    cr.click(page, "#cal .can:not(:has(.chip))", quiet_s=0.6)
    cr.escape(page)
    cr.escape(page)
    # 課表偏好, the floating suggestions, the second mode card (課表統計)
    cr.click(page, "#pf-btn")
    cr.escape(page)
    cr.click_each(page, ".modesw .mcard, .modes .mcard, [data-mode-card]")
    cr.click(page, "#more-btn")
    cr.escape(page)


def task_compliance(cr: Crawler, page) -> None:
    cr.click(page, "#more-btn")


def task_viewer(cr: Crawler, page) -> None:
    # every dashboard of the season views (the default range); toggles: the Python pass
    def all_dashboards():
        for h in range(page.locator("#tree .vw:not(.open) > .vw-h").count()):
            cr.click(page, "#tree .vw:not(.open) > .vw-h", quiet_s=0.3)
        for i in range(page.locator("#tree button.db").count()):
            cr.click(page, "#tree button.db", nth=i, quiet_s=1.0)
    all_dashboards()
    cr.click(page, "#m-workout", quiet_s=1.0)
    cr.click(page, "#acts .act:not(.excl)", quiet_s=1.5)
    all_dashboards()
    cr.click(page, "#back", quiet_s=1.0)


def task_plan(cr: Crawler, page) -> None:
    cr.click(page, "#togglepast")


def task_activities(cr: Crawler, page) -> None:
    cr.click(page, "#tab-ach")
    cr.click(page, "#tab-acts")
    cr.click(page, "#tbody tr", quiet_s=1.0)
    cr.escape(page)


def task_routes(cr: Crawler, page) -> None:
    cr.click_each(page, "#f-kind button")
    cr.click(page, "#f-kind button")
    cr.click_each(page, "#f-dir button")
    cr.click(page, "#f-dir button")
    try:
        opts = page.locator("#f-sport option").evaluate_all("(os) => os.map((o) => o.value)")
    except Exception:                      # noqa: BLE001
        opts = []
    for v in opts:
        try:
            page.select_option("#f-sport", v)
        except Exception:                  # noqa: BLE001
            continue
        cr.settle(page, quiet_s=0.8)
    if opts:
        page.select_option("#f-sport", opts[0])
        cr.settle(page, quiet_s=0.8)


def task_racepower(cr: Crawler, page) -> None:
    cr.click(page, "#calc", quiet_s=1.5)
    for sel in ('#effort button[data-e="0.95"]', '#effort button[data-e="0.85"]', '#effort button[data-e="1"]'):
        cr.click(page, sel, quiet_s=1.0)
        cr.click(page, "#calc", quiet_s=1.5)
    for p in ("10k", "half", "full", "trail30", "yushan"):
        if cr.click(page, f'[data-preset="{p}"]', quiet_s=1.0):
            cr.click(page, "#calc", quiet_s=1.5)
    try:
        events = page.locator("#event option").evaluate_all("(os) => os.map((o) => o.value)")
    except Exception:                      # noqa: BLE001
        events = []
    for v in [e for e in events if e] + [""]:
        try:
            page.select_option("#event", v)
        except Exception:                  # noqa: BLE001
            continue
        cr.settle(page, quiet_s=1.5)
        cr.click(page, "#calc", quiet_s=1.5)
    cr.click(page, '#tabs button[data-tab="acc"]', quiet_s=1.0)


PAGE_TASKS: dict[str, Callable] = {
    "/api/v1/overview/page": task_overview,
    "/api/v1/overview/plan/schedule/page": task_schedule,
    "/api/v1/overview/plan/compliance/page": task_compliance,
    "/api/v1/wko5/viewer": task_viewer,
    "/api/v1/plan/page": task_plan,
    "/api/v1/wko5/activities/page": task_activities,
    "/api/v1/routes/page": task_routes,
    "/api/v1/racepower/page": task_racepower,
}


# --------------------------------------------------------------------------- Python passes
# The parameter spaces the browser pass doesn't walk: chart ranges / toggles, every demo
# activity, the calendar's other months and weeks, every route.
def _get_json(app: DemoApp, rec: Recorder, url: str, record: bool = True):
    status, headers, body = app.get(url)
    if record and "json" in headers.get("content-type", ""):
        rec.add_get(url, status, headers.get("content-type", ""), body)
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _qs(params: list[tuple[str, str]]) -> str:
    from urllib.parse import quote
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)


def chart_toggles(res: dict, kind: str) -> list[tuple[str, str]]:
    """The extra params one click on a chart card's toggles sends (wko5_viewer.html renderCard)."""
    out: list[tuple[str, str]] = []
    if not isinstance(res, dict):
        return out
    if kind == "athlete" and res.get("period_toggle") and not any(
            (s or {}).get("type") == "calendar" for s in res.get("series") or []):
        out += [("period", p) for p in ("day", "week", "month", "quarter", "year")]
    if kind == "athlete" and res.get("window_toggle"):
        out += [("window", str(w)) for w in res.get("window_choices") or []]
    if kind in ("athlete", "workout") and res.get("basis_toggle"):
        out += [("basis", str(b)) for b in res.get("basis_choices") or []]
    if res.get("variant_toggle") and len(res.get("variant_choices") or []) > 1:
        out += [("variant", str(c.get("key"))) for c in res["variant_choices"] if isinstance(c, dict)]
    return out


def viewer_ranges(snapshot: dt.date, first: Optional[str]) -> list[tuple[str, str]]:
    """(begin, end) of the viewer's default range and its presets (wko5_viewer.html preset())."""
    end = snapshot.isoformat()
    out = [((snapshot - dt.timedelta(days=365)).isoformat(), end)]          # the default
    for d in (7, 42, 90, 365):
        out.append(((snapshot - dt.timedelta(days=d - 1)).isoformat(), end))
    # 今年: local Jan 1 -> toISOString: Jan 1 west of UTC, Dec 31 east of it
    out.append((dt.date(snapshot.year, 1, 1).isoformat(), end))
    out.append((dt.date(snapshot.year - 1, 12, 31).isoformat(), end))
    if first:
        out.append((first[:10], end))
    seen, uniq = set(), []
    for r in out:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


PZ_SPORT_SETS = ("", "road", "trail", "hike", "road,trail,hike")          # 強度's sport chips, in the chips' order


def pass_periodzones(app: DemoApp, rec: Recorder, base_url: str, view: str, ranges: list[tuple[str, str]]) -> int:
    """One 區間時數 chart (kind periodzones): what its own controls send (wko5_viewer.html pzQuery) —
    心率／功率, each zone model, each period (本週／上週／近 4 週／本期), the sport chips, 週／月 and the
    date presets — one control at a time plus 心率／功率 × the others, since every combination is
    too many to crawl. 自訂 (free dates) is not crawled."""
    b0, e0 = ranges[0]
    common = [("begin", b0), ("end", e0), ("sports", ""), ("parity", "false")]
    n0 = len(rec.gets)

    def get(extra: list[tuple[str, str]], rng: tuple[str, str] = (b0, e0)):
        params = [("begin", rng[0]), ("end", rng[1])] + common[2:] + extra
        return _get_json(app, rec, f"{base_url}?{_qs(params)}")

    first = get([("zkind", "hr")]) or {}
    models = first.get("models") or {}
    for kind in ("hr", "power"):
        k = [("zkind", kind)]
        res = get(k) or {}
        ids = [m["id"] for m in (models.get(kind) or [])]
        for mid in ids:
            get(k + [("zmodel", mid)])
        for ss in PZ_SPORT_SETS[1:]:
            get(k + [("zsports", ss)])
        if view == "weekly":
            for g in ("auto", "week", "month"):
                get(k + [("zgroup", g)])
            for rng in ranges[1:]:
                get(k, rng)
                get(k + [("zgroup", "week")], rng)
        else:
            periods = [p["id"] for p in (res.get("period_choices") or []) if p["id"] != "custom"]
            for per in periods:
                get(k + [("zperiod", per)])
                for ss in PZ_SPORT_SETS[1:]:
                    get(k + [("zperiod", per), ("zsports", ss)])
            for rng in ranges[1:]:
                get(k + [("zperiod", "range")], rng)
    return len(rec.gets) - n0


def update_periodzones(root: Path, out: Path, log=lambda m: print(m, flush=True)) -> dict:
    """Crawl only the 區間時數 charts' toggles into an existing export (--update-periodzones)."""
    out = out.resolve()
    if not (out / MARKER).exists() or not (out / "data").is_dir():
        raise SystemExit(f"{out} is not a static demo export")
    root = root.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="trc-static-pz-"))
    home = tmp / "demo"
    shutil.copytree(root, home, ignore=shutil.ignore_patterns("sandboxes"))
    (home / "sandboxes").mkdir(exist_ok=True)
    try:
        rec = Recorder()
        from urllib.parse import quote
        with DemoApp(home) as app:
            cal = _get_json(app, rec, "/api/v1/overview/plan/calendar?start=2000-01-03&end=2000-01-09", record=False) or {}
            today = dt.date.fromisoformat(cal.get("today") or dt.date.today().isoformat())
            snap = json.loads((out / "export.json").read_text("utf-8")).get("snapshot") if (out / "export.json").exists() else None
            if snap and snap != today.isoformat():
                log(f"  ! the export's day is {snap}, the demo's today {today}: re-export instead")
            ath = _get_json(app, rec, "/api/v1/wko5/athlete?parity=false", record=False) or {}
            ranges = viewer_ranges(today, ath.get("first"))
            for v in _get_json(app, rec, "/api/v1/wko5/views", record=False) or []:
                if v.get("error"):
                    continue
                for d in v.get("dashboards") or []:
                    for c in d.get("charts") or []:
                        if c.get("kind") == "periodzones":
                            url = f"/api/v1/wko5/views/{quote(v['name'], safe='')}/dashboards/{d['index']}/charts/{c['index']}"
                            n = pass_periodzones(app, rec, url, c.get("view") or "total", ranges)
                            log(f"  {v['name']} / {d.get('title')} / {c.get('title')}: {n} responses")
        scrub = scrubber([str(REPO), str(tmp), str(home), str(root), str(Path.home())])
        for v in rec.gets.values():
            if v["status"] != 200:
                continue
            name = data_file(v["url"])
            (out / "data" / name).write_bytes(scrub(minify_json(v["body"]).decode("utf-8")).encode("utf-8"))
        return {"written": len(rec.gets), "leaks": check_output(out, [str(REPO), str(Path.home()), Path.home().name, str(root)])[:20]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pass_viewer(app: DemoApp, rec: Recorder, snapshot: dt.date, activities: list[int], log) -> None:
    views = _get_json(app, rec, "/api/v1/wko5/views") or []
    sports = [s["sport"] for s in (_get_json(app, rec, "/api/v1/wko5/sports") or []) if s.get("sport")]
    ath = _get_json(app, rec, "/api/v1/wko5/athlete?parity=false") or {}
    ranges = viewer_ranges(snapshot, ath.get("first"))
    # sports filters one click away: each sport alone, each sport unticked
    sport_sets = [""]
    if len(sports) > 1:
        sport_sets += sports + [",".join(x for x in sports if x != s) for s in sports]
    sport_sets = list(dict.fromkeys(sport_sets))

    def season_kind(v: dict) -> bool:
        kinds = [c.get("kind") for d in v.get("dashboards") or [] for c in d.get("charts") or []]
        return kinds.count("workout") <= kinds.count("athlete")

    n0 = len(rec.gets)
    for b, e in ranges:
        for sp in (sport_sets if (b, e) == ranges[0] else [""]):
            _get_json(app, rec, f"/api/v1/wko5/workouts?{_qs([('begin', b), ('end', e), ('sports', sp), ('parity', 'false')])}")
    for v in views:
        if v.get("error"):
            continue
        from urllib.parse import quote
        vn = quote(v["name"], safe="")
        if season_kind(v):
            for d in v.get("dashboards") or []:
                for c in d.get("charts") or []:
                    kind = c.get("kind")
                    extra = [("zkind", "hr")] if kind == "periodzones" else []
                    base_url = f"/api/v1/wko5/views/{vn}/dashboards/{d['index']}/charts/{c['index']}"
                    for ri, (b, e) in enumerate(ranges):
                        for sp in (sport_sets if ri == 0 else [""]):
                            params = [("begin", b), ("end", e), ("sports", sp), ("parity", "false")] + extra
                            res = _get_json(app, rec, f"{base_url}?{_qs(params)}")
                            if ri == 0 and sp == "":
                                for t in chart_toggles(res, kind):
                                    _get_json(app, rec, f"{base_url}?{_qs(params + [t])}")
                    if kind == "periodzones":
                        pass_periodzones(app, rec, base_url, c.get("view") or "total", ranges)
        else:
            for i in activities:
                for d in v.get("dashboards") or []:
                    for c in d.get("charts") or []:
                        kind = c.get("kind")
                        base_url = f"/api/v1/wko5/views/{vn}/dashboards/{d['index']}/charts/{c['index']}"
                        params = [("workout", str(i)), ("parity", "false")]
                        res = _get_json(app, rec, f"{base_url}?{_qs(params)}")
                        for t in chart_toggles(res, kind):
                            _get_json(app, rec, f"{base_url}?{_qs(params + [t])}")
    log(f"  viewer: {len(rec.gets) - n0} responses ({len(ranges)} ranges, {len(sport_sets)} sport filters, "
        f"{len(activities)} activities)")


def pick_activities(acts: list[dict], n: int) -> list[int]:
    """The activities whose single-activity charts are exported: the latest ones, every race /
    test / hike, the longest and the hardest (all of them when n <= 0)."""
    rows = [a for a in acts if a.get("index") is not None and not a.get("excluded")]
    rows.sort(key=lambda a: a.get("start") or "", reverse=True)
    if n <= 0 or len(rows) <= n:
        return [a["index"] for a in rows]
    out: list[int] = []

    def take(xs):
        for a in xs:
            if len(out) >= n:
                return
            if a["index"] not in out:
                out.append(a["index"])
    special = [a for a in rows if (a.get("user_type") or a.get("activity_type")) in ("race", "test", "hike", "baiyue_group")
               or a.get("trail")]
    take(rows[: max(4, n // 3)])
    take(sorted(rows, key=lambda a: -(a.get("tss") or 0))[: max(3, n // 6)])
    take(sorted(rows, key=lambda a: -(a.get("duration") or 0))[: max(3, n // 6)])
    take(special)
    take(rows)
    return out


def pass_activities(app: DemoApp, rec: Recorder, log) -> list[dict]:
    acts = (_get_json(app, rec, "/api/v1/wko5/activities") or {}).get("activities") or []
    for a in acts:
        if a.get("index") is not None:
            _get_json(app, rec, f"/api/v1/wko5/workouts/{a['index']}/activity")
            _get_json(app, rec, f"/api/v1/wko5/workouts/{a['index']}/segments")
    log(f"  activities: {len(acts)} activity details")
    return acts


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def calendar_ranges(today: dt.date, months_back: int = 12, months_ahead: int = 3,
                    weeks_back: int = 26, weeks_ahead: int = 10) -> list[tuple[str, str]]:
    """schedule.html range(): a month view = Monday of the 1st .. Sunday of the last day's
    week; a week view (and every phone) = Monday .. Sunday."""
    out = []
    for k in range(-months_back, months_ahead + 1):
        y, m = today.year, today.month + k
        while m < 1:
            y, m = y - 1, m + 12
        while m > 12:
            y, m = y + 1, m - 12
        first = dt.date(y, m, 1)
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        out.append((_monday(first).isoformat(), (_monday(last) + dt.timedelta(days=6)).isoformat()))
    for k in range(-weeks_back, weeks_ahead + 1):
        a = _monday(today) + dt.timedelta(weeks=k)
        out.append((a.isoformat(), (a + dt.timedelta(days=6)).isoformat()))
    return list(dict.fromkeys(out))


def pass_schedule(app: DemoApp, rec: Recorder, today: dt.date, log) -> None:
    rs = calendar_ranges(today)
    for a, b in rs:
        _get_json(app, rec, f"/api/v1/overview/plan/calendar?start={a}&end={b}")
    # the add-session menu of the coming days: 排入測試 / templates for that day
    for k in range(0, 21):
        d = (today + dt.timedelta(days=k)).isoformat()
        _get_json(app, rec, f"/api/v1/overview/plan/test-options?day={d}")
    for u in ("/api/v1/overview/plan/test-suggestions", "/api/v1/overview/plan/test-templates",
              "/api/v1/overview/plan/steps/templates", "/api/v1/overview/plan/prefs",
              "/api/v1/overview/plan/prefs/gate", "/api/v1/overview/plan/blackouts",
              "/api/v1/overview/plan/equivalence", "/api/v1/overview/plan/reconcile",
              "/api/v1/overview/plan/suggestions"):
        _get_json(app, rec, u)
    log(f"  schedule: {len(rs)} calendar ranges")


STEPS_FILE = "steps_ctx.json"            # static_shim.js STEPS_FILE


def _saved_json(rec: Recorder, prefix: str) -> list:
    out = []
    for k, v in rec.gets.items():
        if (k == prefix or k.startswith(prefix + "?")) and v["status"] == 200:
            try:
                out.append(json.loads(v["body"]))
            except ValueError:
                pass
    return out


RECS_DAYS = 28                           # 插入範本's 推薦 precomputed for new sessions this many days ahead
RECS_KINDS = ("easy", "long", "quality", "test", "hike", "mountain")


def pass_steps(app: DemoApp, rec: Recorder, today: dt.date, log) -> None:
    """data/steps_ctx.json: what static_shim.js needs to answer the structure editor
    itself — POST /steps/check and /steps/derive (backend/demo/static_steps.py) and
    插入範本's 推薦 (GET /steps/templates/recs, whose query changes with every edit of the
    minutes): each stored session's, and per kind / day / terrain for new sessions."""
    from backend.demo import static_steps as SS
    cals = _saved_json(rec, "/api/v1/overview/plan/calendar") + _saved_json(rec, "/api/v1/overview/plan/sessions")
    seen, sessions = set(), []
    for c in cals:
        for s in c.get("sessions") or []:
            if isinstance(s, dict) and s.get("uid") not in seen:
                seen.add(s.get("uid"))
                sessions.append(s)
    sug = (_saved_json(rec, "/api/v1/overview/plan/test-suggestions") or [None])[0]
    cal = next((c for c in cals if c.get("test_templates")), {})
    data = app.client.portal.call(SS.collect, sessions + SS.dialog_tests(cal, sug))
    recs: dict = {}

    def get_recs(kind, day, ter, minutes=None, uid=None, terrain=None):
        k = SS.recs_key(kind, day, ter, minutes)
        if k in recs:
            return
        q = [("kind", kind), ("day", day)] + ([("uid", uid)] if uid else []) + \
            ([("minutes", str(minutes))] if minutes else []) + ([("terrain", terrain)] if terrain else [])
        r = _get_json(app, rec, f"/api/v1/overview/plan/steps/templates/recs?{_qs(q)}", record=False)
        if r is not None:
            recs[k] = r
    for s in sessions:
        if s.get("state") == "active" and (s.get("day") or "") >= today.isoformat() and s.get("kind") in RECS_KINDS:
            get_recs(s["kind"], s["day"], SS.recs_terrain(s["kind"], s.get("terrain")), s.get("minutes"), s.get("uid"), s.get("terrain"))
    for i in range(RECS_DAYS):
        day = (today + dt.timedelta(days=i)).isoformat()
        for kind in RECS_KINDS:
            for ter in (("trail",) if kind == "hike" else ("road", "trail")):
                get_recs(kind, day, ter, terrain=ter)
    # most days answer the same: each distinct answer once ({"keys": {key: i}, "pool": [answer]})
    pool, at = [], {}
    for k, v in recs.items():
        j = json.dumps(v, ensure_ascii=False, sort_keys=True)
        if j not in at:
            at[j] = len(pool)
            pool.append(v)
        recs[k] = at[j]
    data["recs"] = {"keys": recs, "pool": pool}
    rec.files[STEPS_FILE] = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    log(f"  steps: {len(sessions)} sessions, {len(data['derive'])} derived structures, {len(recs)} 推薦 blocks")


def update_steps(root: Path, out: Path, log=lambda m: print(m, flush=True)) -> dict:
    """Refresh only the structure editor's part of an existing export (--update-steps):
    data/steps_ctx.json and static/trc_static.js, from a copy of the demo root."""
    out = out.resolve()
    if not (out / MARKER).exists() or not (out / "data").is_dir():
        raise SystemExit(f"{out} is not a static demo export")
    root = root.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="trc-static-steps-"))
    home = tmp / "demo"
    shutil.copytree(root, home, ignore=shutil.ignore_patterns("sandboxes"))
    (home / "sandboxes").mkdir(exist_ok=True)
    try:
        rec = Recorder()
        with DemoApp(home) as app:
            cal = _get_json(app, rec, "/api/v1/overview/plan/calendar?start=2000-01-03&end=2000-01-09", record=False) or {}
            today = dt.date.fromisoformat(cal.get("today") or dt.date.today().isoformat())
            snap = json.loads((out / "export.json").read_text("utf-8")).get("snapshot") if (out / "export.json").exists() else None
            if snap and snap != today.isoformat():
                log(f"  ! the export's day is {snap}, the demo's today {today}: re-export instead")
            pass_schedule(app, rec, today, log)
            pass_steps(app, rec, today, log)
        scrub = scrubber([str(REPO), str(tmp), str(home), str(root), str(Path.home())])
        for name, raw in rec.files.items():
            (out / "data" / name).write_bytes(scrub(raw.decode("utf-8")).encode("utf-8"))
        shutil.copyfile(SHIM_SRC, out / "static" / "trc_static.js")
        return {"updated": sorted(rec.files) + ["static/trc_static.js"], "today": today.isoformat(),
                "leaks": check_output(out, [str(REPO), str(Path.home()), Path.home().name, str(root)])[:20]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def update_pages(root: Path, out: Path, log=lambda m: print(m, flush=True)) -> dict:
    """Re-render only the pages and copy the static assets into an existing export (--update-pages):
    for a change in backend/static (HTML/JS/CSS) that needs no new data. Takes about a minute."""
    out = out.resolve()
    if not (out / MARKER).exists() or not (out / "data").is_dir():
        raise SystemExit(f"{out} is not a static demo export")
    root = root.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="trc-static-pages-"))
    home = tmp / "demo"
    shutil.copytree(root, home, ignore=shutil.ignore_patterns("sandboxes"))
    (home / "sandboxes").mkdir(exist_ok=True)
    try:
        from backend.demo import sandbox as SB
        written: list[str] = []
        with DemoApp(home) as app:
            snap = json.loads((out / "export.json").read_text("utf-8")).get("snapshot")
            cfg = {"snapshot": snap, "today": snap, "pages": {**PAGES, **PAGE_ALIASES}, "compute": COMPUTE_POSTS.pattern,
                   "locks": STATIC_LOCKS}
            session = static_session(SB.current_base_name())
            scrub = scrubber([str(REPO), str(tmp), str(home), str(root), str(Path.home())])
            for path, file in PAGES.items():
                status, _h, body = app.get(path)
                if status != 200:
                    log(f"  ! page {path}: HTTP {status}")
                    continue
                (out / file).write_text(scrub(transform_page(body.decode("utf-8"), cfg, session)), "utf-8")
                written.append(file)
        # the page assets (everything except the raw page templates and the files the other
        # update modes own: the shim, the Pyodide bundle)
        keep = {"trc_static.js", "trc_racepower_worker.js", "py"}
        for src in STATIC_SRC.rglob("*"):
            rel = src.relative_to(STATIC_SRC)
            if src.is_dir() or src.suffix == ".html" or "__pycache__" in rel.parts or rel.parts[0] in keep:
                continue
            dst = out / "static" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                shutil.copyfile(src, dst)
                written.append(f"static/{rel.as_posix()}")
        shutil.copyfile(SHIM_SRC, out / "static" / "trc_static.js")
        return {"updated": written, "leaks": check_output(out, [str(REPO), str(Path.home()), Path.home().name, str(root)])[:20]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- race calculator (Pyodide)
# Any input the crawl did not precompute is computed in the browser: static_shim.js starts
# static/trc_racepower_worker.js, which runs engine/racepower/calc.py with Pyodide on
# data/racepower_ctx.json (backend/demo/static_racepower.py).
def pass_racepower(app: DemoApp, rec: Recorder, log) -> dict:
    """data/racepower_ctx.json (the athlete context, in rec.files) and the Python bundle
    (returned: {"bundle": zip bytes, "version", "modules"}) for write_racepower()."""
    import hashlib
    from backend.api import racepower as RP
    from backend.demo import static_racepower as SR
    t0 = time.time()
    raw = SR.export_json(RP.LIVE)
    tmp = Path(tempfile.mkdtemp(prefix="trc-static-rp-"))
    try:
        (tmp / SR.CTX_FILE).write_bytes(raw)
        tr = SR.trace(tmp / SR.CTX_FILE)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    zipped = SR.bundle(tr["modules"])
    rec.files[SR.CTX_FILE] = raw
    prefix = API + "racepower/"
    saved = [data_file(v["url"]) for v in rec.gets.values() if urlsplit(v["url"]).path.startswith(prefix)] + \
        [name for name, v in rec.posts.items() if urlsplit(v["url"]).path.startswith(prefix) and v["status"] == 200]
    rec.files[SR.SAVED_FILE] = json.dumps({"v": 1, "files": sorted(set(saved))}).encode("utf-8")
    version = hashlib.sha1(zipped + raw).hexdigest()[:12]
    log(f"  racepower: context {len(raw) // 1024} KB, bundle {len(zipped) // 1024} KB "
        f"({len(tr['modules'])} modules traced), {time.time() - t0:.0f} s")
    return {"bundle": zipped, "version": version, "modules": tr["modules"]}


def write_racepower(out: Path, rp: dict) -> list[str]:
    """static/py/trc_racepower.zip and static/trc_racepower_worker.js (the context is a data/ file)."""
    from backend.demo import static_racepower as SR
    z = out / "static" / SR.BUNDLE
    z.parent.mkdir(parents=True, exist_ok=True)
    z.write_bytes(rp["bundle"])
    (out / "static" / SR.WORKER).write_text(SR.worker_js(rp["version"]), "utf-8")
    return ["static/" + SR.BUNDLE, "static/" + SR.WORKER]


def _crawl_racepower(app: DemoApp, rec: Recorder, snapshot: dt.date, log) -> None:
    """crawl_pages() for the race calculator page only (its precomputed default answers)."""
    from playwright.sync_api import sync_playwright
    cr = Crawler(app, rec)
    clock = (f"window.TRC_STATIC_CFG = {json.dumps({'snapshot': snapshot.isoformat(), 'clockOnly': True})};\n"
             + SHIM_SRC.read_text("utf-8"))
    path = "/api/v1/racepower/page"
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            ctx = b.new_context(viewport={"width": 1400, "height": 900}, locale="zh-TW", timezone_id="Asia/Taipei")
            ctx.add_init_script(clock)
            ctx.route("**/*", cr.handle)
            page = ctx.new_page()
            page.goto(HOST + path, wait_until="domcontentloaded", timeout=120000)
            cr.settle(page, quiet_s=1.5)
            task_racepower(cr, page)
            page.close()
            ctx.close()
        finally:
            b.close()
    log(f"  page {path}: {len(rec.gets)} GETs, {len(rec.posts)} precomputed")


def _saved_body(status: int, body: bytes) -> bytes:
    """A recorded answer as write_site saves it (non-200: wrapped with its status)."""
    if status == 200:
        return body
    try:
        inner = json.loads(body)
    except ValueError:
        inner = None
    return json.dumps({"__trc_status": status, "body": inner}, ensure_ascii=False).encode("utf-8")


def update_racepower(root: Path, out: Path, browser: bool = True, log=lambda m: print(m, flush=True)) -> dict:
    """Refresh only the race calculator in an existing export (--update-racepower): the Pyodide
    bundle + worker, data/racepower_ctx.json, the shim, and its precomputed answers — the
    race calculator page is crawled again; precomputed answers saved as 429 (the demo's rate
    limits, before the export lifted them) are removed."""
    out = out.resolve()
    if not (out / MARKER).exists() or not (out / "data").is_dir():
        raise SystemExit(f"{out} is not a static demo export")
    root = root.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="trc-static-rp-"))
    home = tmp / "demo"
    shutil.copytree(root, home, ignore=shutil.ignore_patterns("sandboxes"))
    (home / "sandboxes").mkdir(exist_ok=True)
    try:
        rec = Recorder()
        with DemoApp(home) as app:
            cal = _get_json(app, rec, "/api/v1/overview/plan/calendar?start=2000-01-03&end=2000-01-09", record=False) or {}
            today = dt.date.fromisoformat(cal.get("today") or dt.date.today().isoformat())
            snap = json.loads((out / "export.json").read_text("utf-8")).get("snapshot") if (out / "export.json").exists() else None
            if snap and snap != today.isoformat():
                log(f"  ! the export's day is {snap}, the demo's today {today}: re-export instead")
            if browser:
                _crawl_racepower(app, rec, today, log)
            rp = pass_racepower(app, rec, log)
            # the page itself (its error display, the locks): rendered like write_site does
            from backend.demo import sandbox as SB
            day = snap or today.isoformat()
            cfg = {"snapshot": day, "today": day, "pages": {**PAGES, **PAGE_ALIASES}, "compute": COMPUTE_POSTS.pattern,
                   "locks": STATIC_LOCKS}
            status, _h, page_body = app.get("/api/v1/racepower/page")
            page_html = transform_page(page_body.decode("utf-8"), cfg, static_session(SB.current_base_name())) \
                if status == 200 else None
        scrub = scrubber([str(REPO), str(tmp), str(home), str(root), str(Path.home())])
        removed = 0
        for p in (out / "data").glob("p*.json"):
            try:
                j = json.loads(p.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(j, dict) and j.get("__trc_status") == 429:
                p.unlink()
                removed += 1
        written = []
        for v in rec.gets.values():
            name = data_file(v["url"])
            (out / "data" / name).write_bytes(scrub(minify_json(_saved_body(v["status"], v["body"])).decode("utf-8")).encode("utf-8"))
            written.append(name)
        for name, v in rec.posts.items():
            (out / "data" / name).write_bytes(scrub(minify_json(_saved_body(v["status"], v["body"])).decode("utf-8")).encode("utf-8"))
            written.append(name)
        for name, raw in rec.files.items():
            (out / "data" / name).write_bytes(scrub(raw.decode("utf-8")).encode("utf-8"))
            written.append(name)
        files = write_racepower(out, rp)
        shutil.copyfile(SHIM_SRC, out / "static" / "trc_static.js")
        if page_html is not None:
            (out / PAGES["/api/v1/racepower/page"]).write_text(scrub(page_html), "utf-8")
            files.append(PAGES["/api/v1/racepower/page"])
        try:
            info = json.loads((out / "export.json").read_text("utf-8"))
            info["racepower"] = {"version": rp["version"], "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
            (out / "export.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
        except (OSError, ValueError):
            pass
        return {"today": today.isoformat(), "version": rp["version"], "removed_429": removed,
                "precomputed": len(rec.posts), "gets": len(rec.gets),
                "updated": files + ["static/trc_static.js", "data/" + "racepower_ctx.json"],
                "leaks": check_output(out, [str(REPO), str(Path.home()), Path.home().name, str(root)])[:20]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pass_routes(app: DemoApp, rec: Recorder, log) -> None:
    ids: list[str] = []
    for k in list(rec.gets):
        if k == "/api/v1/routes" or k.startswith("/api/v1/routes?"):
            try:
                ids += [r["id"] for r in json.loads(rec.gets[k]["body"]).get("rows") or []]
            except (ValueError, KeyError, TypeError):
                pass
    if not ids:
        ids = [r["id"] for r in (_get_json(app, rec, "/api/v1/routes") or {}).get("rows") or []]
    ids = list(dict.fromkeys(ids))
    for rid in ids:
        d = _get_json(app, rec, f"/api/v1/routes/{rid}")
        if not d:
            continue
        tk = d.get("time_key")
        main = [e for e in d.get("efforts") or [] if (e.get("dir") or "same") == "same"]
        timed = sorted([e for e in main if e.get(tk) is not None], key=lambda e: e[tk])
        a = timed[0]["id"] if timed else None
        last = main[-1]["id"] if main else None
        b = last if last != a else (timed[1]["id"] if len(timed) > 1 else None)
        if a and b:
            _get_json(app, rec, f"/api/v1/routes/{d.get('id', rid)}/compare?a={a}&b={b}")
    log(f"  routes: {len(ids)} routes")


# --------------------------------------------------------------------------- output
_TRC_SESSION = re.compile(r"<script>window\.TRC_SESSION = .*?;</script>", re.S)
_HEAD = re.compile(r"<head\b[^>]*>", re.I)
_ATTR_URL = re.compile(r'(\s(?:href|src)=")(/api/v1/[^"]*|/demo|/)(")')


def static_session(base: Optional[str]) -> dict:
    return {"mode": "demo", "static": True, "caps": ["plan.write"], "user": None,
            "demo": {"sandbox": False, "base": base, "expires_at": None, "reset": False, "cta_url": None,
                     "static": True}}


def rewrite_attr_url(url: str) -> Optional[str]:
    """A page's href / src at /api/v1/... -> its relative static URL (None: leave it to the shim)."""
    sp = urlsplit(url)
    if sp.path.startswith("/api/v1/static/"):
        if sp.path.endswith(".html"):
            return None
        return "static/" + sp.path[len("/api/v1/static/"):] + (f"?{sp.query}" if sp.query else "")
    f = static_page_for(sp.path)
    if f is None:
        return None
    file, _, h = f.partition("#")
    return file + (f"?{sp.query}" if sp.query else "") + (f"#{sp.fragment}" if sp.fragment else (f"#{h}" if h else ""))


def transform_page(html_text: str, cfg: dict, session: dict) -> str:
    sess = json.dumps(session, ensure_ascii=False).replace("</", "<\\/")
    out, n = _TRC_SESSION.subn(lambda m: f"<script>window.TRC_SESSION = {sess};</script>", html_text, count=1)
    cfg_js = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    boot = f'<script>window.TRC_STATIC_CFG = {cfg_js};</script><script src="static/trc_static.js"></script>'
    if not n:
        boot += f"<script>window.TRC_SESSION = {sess};</script>"
    m = _HEAD.search(out)
    out = out[:m.end()] + boot + out[m.end():] if m else boot + out

    def attr(m: re.Match) -> str:
        new = rewrite_attr_url(m.group(2))
        return m.group(0) if new is None else m.group(1) + new + m.group(3)
    return _ATTR_URL.sub(attr, out)


MARKER = ".trc-static-demo"


def prepare_out(out: Path) -> None:
    if out.exists():
        if any(out.iterdir()) and not (out / MARKER).exists():
            raise SystemExit(f"refusing to overwrite {out}: not empty and not a static demo export")
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    (out / MARKER).write_text("written by backend/demo/export_static.py\n", "utf-8")


def write_site(app: DemoApp, rec: Recorder, out: Path, snapshot: dt.date, base_name: Optional[str],
               scrub: Callable[[str], str], log, fresh: bool = True) -> dict:
    """`fresh` False (--update): write over an existing export, keeping its other data files."""
    if fresh:
        prepare_out(out)
    # assets (not the raw page templates: the pages are rendered below)
    shutil.copytree(STATIC_SRC, out / "static", ignore=shutil.ignore_patterns("*.html", "__pycache__"),
                    dirs_exist_ok=not fresh)
    shutil.copyfile(SHIM_SRC, out / "static" / "trc_static.js")
    (out / ".nojekyll").write_text("", "utf-8")
    cfg = {"snapshot": snapshot.isoformat(), "today": snapshot.isoformat(),
           "pages": {**PAGES, **PAGE_ALIASES}, "compute": COMPUTE_POSTS.pattern, "locks": STATIC_LOCKS}
    session = static_session(base_name)
    pages = 0
    for path, file in PAGES.items():
        status, headers, body = app.get(path)
        if status != 200:
            log(f"  ! page {path}: HTTP {status}")
            continue
        html_text = scrub(transform_page(body.decode("utf-8"), cfg, session))
        (out / file).write_text(html_text, "utf-8")
        pages += 1
    n_bytes = 0
    for k, v in rec.gets.items():
        body = v["body"]
        if v["status"] != 200:
            try:
                inner = json.loads(body)
            except ValueError:
                inner = None
            body = json.dumps({"__trc_status": v["status"], "body": inner}, ensure_ascii=False).encode("utf-8")
        data = scrub(minify_json(body).decode("utf-8")).encode("utf-8")
        (out / "data" / data_file(v["url"])).write_bytes(data)
        n_bytes += len(data)
    for name, v in rec.posts.items():
        body = v["body"]
        if v["status"] != 200:
            try:
                inner = json.loads(body)
            except ValueError:
                inner = None
            body = json.dumps({"__trc_status": v["status"], "body": inner}, ensure_ascii=False).encode("utf-8")
        data = scrub(minify_json(body).decode("utf-8")).encode("utf-8")
        (out / "data" / name).write_bytes(data)
        n_bytes += len(data)
    for name, raw in rec.files.items():
        data = scrub(raw.decode("utf-8")).encode("utf-8")
        (out / "data" / name).write_bytes(data)
        n_bytes += len(data)
    info = {"snapshot": snapshot.isoformat(), "base": base_name, "pages": pages, "gets": len(rec.gets),
            "precomputed": len(rec.posts), "data_bytes": n_bytes,
            "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (out / "export.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
    return info


def check_output(out: Path, forbidden: Iterable[str]) -> list[str]:
    """Text a published demo must not contain (local paths, user name)."""
    bad = []
    pats = [f for f in {x for x in forbidden if x and len(x) > 3}]
    for p in out.rglob("*"):
        if not p.is_file() or p.suffix not in (".html", ".json", ".js"):
            continue
        t = p.read_text("utf-8", errors="replace")
        for f in pats:
            if f.lower() in t.lower():
                bad.append(f"{p.relative_to(out)}: {f!r}")
    return bad


# --------------------------------------------------------------------------- incremental
ACTIVITIES_KEY = "/api/v1/wko5/activities"


def _old_activities(reuse: Path) -> dict:
    """{index: start} of the activities in an existing export (its activity list)."""
    p = reuse / "data" / data_file(ACTIVITIES_KEY)
    try:
        acts = json.loads(p.read_text("utf-8")).get("activities") or []
    except (OSError, ValueError, AttributeError):
        raise SystemExit(f"{reuse} has no activity list ({p.name}): do a full export")
    return {a["index"]: a.get("start") for a in acts if a.get("index") is not None}


def update(root: Path, reuse: Path, out: Path, browser: bool = True, verbose: bool = False,
           log=lambda m: print(m, flush=True)) -> dict:
    """--update --reuse <old export>: an export for a demo root that only gained new
    activities after the old export was made (backend/demo/build.py --add-linked). The
    old export is copied to `out`, then only what the new activities change is
    crawled again and written over it: the new activities' details and single-activity
    charts, every page's browser pass (lists, overview, schedule, the precomputed POSTs
    and the page HTML), the calendar ranges, the structure editor's data, the routes and
    the season charts (all their ranges end today). Every older activity's own files are
    reused, which needs the older activities to keep their indexes (refused otherwise)."""
    root, reuse, out = root.resolve(), reuse.resolve(), out.resolve()
    if not (reuse / MARKER).exists():
        raise SystemExit(f"{reuse} is not a static demo export")
    if out != reuse:
        if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
            raise SystemExit(f"refusing to overwrite {out}: not empty and not a static demo export")
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(reuse, out)
    old = _old_activities(out)
    snap_old = json.loads((out / "export.json").read_text("utf-8")).get("snapshot")
    tmp = Path(tempfile.mkdtemp(prefix="trc-static-update-"))
    home = tmp / "demo"
    shutil.copytree(root, home, ignore=shutil.ignore_patterns("sandboxes"))
    (home / "sandboxes").mkdir(exist_ok=True)
    t0 = time.time()
    try:
        rec = Recorder()
        with DemoApp(home) as app:
            from backend.demo import sandbox as SB
            base_name = SB.current_base_name()
            cal = _get_json(app, rec, "/api/v1/overview/plan/calendar?start=2000-01-03&end=2000-01-09", record=False) or {}
            snapshot = dt.date.fromisoformat(cal.get("today") or dt.date.today().isoformat())
            if snap_old and snap_old != snapshot.isoformat():
                raise SystemExit(f"the old export is of {snap_old}, the demo's today is {snapshot}: do a full export")
            acts = (_get_json(app, rec, ACTIVITIES_KEY) or {}).get("activities") or []
            now = {a["index"]: a.get("start") for a in acts if a.get("index") is not None}
            moved = [i for i, s in old.items() if now.get(i) != s]
            if moved:
                raise SystemExit(f"{len(moved)} older activities changed index (e.g. {moved[:5]}): do a full export")
            new = sorted(i for i in now if i not in old)
            log(f"demo base {base_name}, today {snapshot}: {len(new)} new activities {new}")
            for i in new:
                _get_json(app, rec, f"/api/v1/wko5/workouts/{i}/activity")
                _get_json(app, rec, f"/api/v1/wko5/workouts/{i}/segments")
            t1 = time.time()
            if browser:
                crawl_pages(app, rec, snapshot, verbose, log)
            log(f"  browser pass: {time.time() - t1:.0f} s")
            t1 = time.time()
            pass_schedule(app, rec, snapshot, log)
            pass_steps(app, rec, snapshot, log)
            pass_routes(app, rec, log)
            log(f"  schedule / steps / routes: {time.time() - t1:.0f} s")
            t1 = time.time()
            pass_viewer(app, rec, snapshot, new, log)
            log(f"  viewer: {time.time() - t1:.0f} s")
            scrub = scrubber([str(REPO), str(tmp), str(home), str(root), str(Path.home())])
            info = write_site(app, rec, out, snapshot, base_name, scrub, log, fresh=False)
        info.update(seconds=round(time.time() - t0), new_activities=new, reused_from=str(reuse),
                    data_files=sum(1 for _ in (out / "data").iterdir()))
        (out / "export.json").write_text(json.dumps({k: info[k] for k in ("snapshot", "base", "pages", "gets",
                                                                         "precomputed", "data_bytes", "exported_at")}
                                                    | {"updated": True, "data_files": info["data_files"]},
                                                    ensure_ascii=False, indent=1), "utf-8")
        info["refused_writes"] = sorted({f"{m} {p}" for m, p in rec.writes})
        info["leaks"] = check_output(out, [str(REPO), str(Path.home()), Path.home().name, str(root)])[:20]
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- main
def export(root: Path, out: Path, activities: int = 40, browser: bool = True, keep_copy: bool = False,
           verbose: bool = False, log=lambda m: print(m, flush=True)) -> dict:
    root = root.resolve()
    from backend.demo.instance import _inside
    real = Path(os.environ.get("WKO5COACH_TEST_REAL_HOME") or Path.home())
    for owner in (real / ".wko5coach", real / "WKO5"):
        if _inside(root, owner):
            raise SystemExit(f"refusing to export from the owner's {owner}")
    if not (root / "base" / "current").is_file():
        raise SystemExit(f"{root} has no demo data (python -m backend.demo.build --root {root})")
    tmp = Path(tempfile.mkdtemp(prefix="trc-static-export-"))
    home = tmp / "demo"
    log(f"copying {root} -> {home}")
    shutil.copytree(root, home, ignore=shutil.ignore_patterns("sandboxes"))
    (home / "sandboxes").mkdir(exist_ok=True)
    t0 = time.time()
    try:
        rec = Recorder()
        with DemoApp(home) as app:
            from backend.demo import sandbox as SB
            base_name = SB.current_base_name()
            cal = _get_json(app, rec, "/api/v1/overview/plan/calendar?start=2000-01-03&end=2000-01-09", record=False) or {}
            snapshot = dt.date.fromisoformat(cal.get("today") or dt.date.today().isoformat())
            log(f"demo base {base_name}, today {snapshot}")
            acts = pass_activities(app, rec, log)
            if browser:
                crawl_pages(app, rec, snapshot, verbose, log)
            pass_schedule(app, rec, snapshot, log)
            pass_steps(app, rec, snapshot, log)
            rp = pass_racepower(app, rec, log)
            pass_routes(app, rec, log)
            chosen = pick_activities(acts, activities)
            pass_viewer(app, rec, snapshot, chosen, log)
            scrub = scrubber([str(REPO), str(tmp), str(home), str(root), str(Path.home())])
            info = write_site(app, rec, out, snapshot, base_name, scrub, log)
            write_racepower(out, rp)
        info["seconds"] = round(time.time() - t0)
        info["refused_writes"] = sorted({f"{m} {p}" for m, p in rec.writes})
        bad = check_output(out, [str(REPO), str(Path.home()), Path.home().name, str(root)])
        info["leaks"] = bad[:20]
        return info
    finally:
        if not keep_copy:
            shutil.rmtree(tmp, ignore_errors=True)


def crawl_pages(app: DemoApp, rec: Recorder, snapshot: dt.date, verbose: bool, log) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("  ! playwright is not installed (requirements-dev.txt): skipping the browser pass")
        return
    cr = Crawler(app, rec, verbose=verbose)
    clock = (f"window.TRC_STATIC_CFG = {json.dumps({'snapshot': snapshot.isoformat(), 'clockOnly': True})};\n"
             + SHIM_SRC.read_text("utf-8"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            for vp in ({"width": 1400, "height": 900}, {"width": 390, "height": 844}):
                ctx = b.new_context(viewport=vp, locale="zh-TW", timezone_id="Asia/Taipei")
                ctx.add_init_script(clock)
                ctx.route("**/*", cr.handle)
                for path in PAGES:
                    n0 = len(rec.gets) + len(rec.posts)
                    page = ctx.new_page()
                    try:
                        page.goto(HOST + path, wait_until="domcontentloaded", timeout=120000)
                        cr.settle(page, quiet_s=1.5)
                        task = PAGE_TASKS.get(path)
                        if task and vp["width"] > 700:
                            task(cr, page)
                    except Exception as e:     # noqa: BLE001 — keep the rest of the crawl
                        log(f"  ! {path}: {type(e).__name__}: {str(e)[:200]}")
                    finally:
                        page.close()
                    log(f"  page {path} ({vp['width']}px): +{len(rec.gets) + len(rec.posts) - n0}")
                ctx.close()
        finally:
            b.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Export the demo as a static read-only site")
    ap.add_argument("--root", required=True, type=Path, help="the demo root (python -m backend.demo.build --root ...)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
    ap.add_argument("--activities", type=int, default=40,
                    help="activities with single-activity charts (0 = all; default 40)")
    ap.add_argument("--no-browser", action="store_true", help="skip the headless-browser pass")
    ap.add_argument("--keep-copy", action="store_true", help="keep the temporary copy of the demo data")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--update-steps", action="store_true",
                    help="only refresh the structure editor's data (data/steps_ctx.json) and the shim in an existing --out")
    ap.add_argument("--update", action="store_true",
                    help="incremental: copy the export --reuse to --out and re-crawl only what the demo root's "
                         "new activities change (build.py --add-linked); older activities' files are reused")
    ap.add_argument("--reuse", type=Path, help="the existing export --update starts from (default: --out)")
    ap.add_argument("--update-racepower", action="store_true",
                    help="only refresh the race calculator (Pyodide bundle, data/racepower_ctx.json, its precomputed "
                         "answers, the shim) in an existing --out")
    ap.add_argument("--update-pages", action="store_true",
                    help="only re-render the pages and copy the static assets into an existing --out "
                         "(a change in backend/static that needs no new data; about a minute)")
    ap.add_argument("--update-periodzones", action="store_true",
                    help="only crawl the 區間時數 charts' toggles (心率／功率, models, periods, sports, 週／月) "
                         "into an existing --out")
    a = ap.parse_args(argv)
    if a.update_periodzones:
        info = update_periodzones(a.root, a.out)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 2 if info.get("leaks") else 0
    if a.update_pages:
        info = update_pages(a.root, a.out)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 2 if info.get("leaks") else 0
    if a.update:
        info = update(a.root, (a.reuse or a.out), a.out, browser=not a.no_browser, verbose=a.verbose)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 2 if info.get("leaks") else 0
    if a.update_racepower:
        info = update_racepower(a.root, a.out, browser=not a.no_browser)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 2 if info.get("leaks") else 0
    if a.update_steps:
        info = update_steps(a.root, a.out)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 2 if info.get("leaks") else 0
    info = export(a.root, a.out.resolve(), activities=a.activities, browser=not a.no_browser,
                  keep_copy=a.keep_copy, verbose=a.verbose)
    size = sum(p.stat().st_size for p in a.out.rglob("*") if p.is_file())
    print(json.dumps({**info, "total_bytes": size}, ensure_ascii=False, indent=1))
    if info.get("leaks"):
        print("!! local paths / names found in the output (see leaks); do not publish", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
