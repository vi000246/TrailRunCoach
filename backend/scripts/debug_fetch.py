"""
Call the debug API (SP-371, docs/debug-api.md) without hand-building HTTP: an AI agent's CLI.

    export TRC_DEBUG_URL=https://<production address>
    export TRC_DEBUG_TOKEN=trcd_...              # made on 設定 › 進階 › Debug API; never commit it
    export TRC_PROXY_USER=... TRC_PROXY_PASSWORD=...   # the password proxy in front of the app (if any)
    python -m backend.scripts.debug_fetch day 2026-10-05
    python -m backend.scripts.debug_fetch activity --date 2026-10-05 --streams hr,speed --every 30
    python -m backend.scripts.debug_fetch activity --id 1234
    python -m backend.scripts.debug_fetch plan --from 2026-09-29 --to 2026-10-12
    python -m backend.scripts.debug_fetch thresholds 2026-10-05
    python -m backend.scripts.debug_fetch sync --lines 300
    python -m backend.scripts.debug_fetch export -o config.json
    python -m backend.scripts.debug_fetch export-plan -o plan.json

Writes (SP-802's /write/*, a token with write:config; SP-890) — preview first, read the diff, then apply:
    python -m backend.scripts.debug_fetch preview --changes '[{"target": "threshold", "date": "2026-09-30", "set": {"lthr": 166}}]'
    python -m backend.scripts.debug_fetch preview -f changes.json        # a list or {"changes": [...]}; - = stdin
    python -m backend.scripts.debug_fetch apply pv_…                      # the preview_id the preview printed
    python -m backend.scripts.debug_fetch undo                            # the newest write; --change-id N for one
    python -m backend.scripts.debug_fetch write-log -n 10                 # the newest writes, before / after

The token goes in its own header (X-TRC-Debug-Token) and is read from the environment only (never
a command-line argument: it would sit in the shell history and the process list); the proxy's
credentials, when set, go in Authorization (Basic). HTTPS only (http only to localhost); a
redirect is never followed (it could carry the headers elsewhere). Prints the JSON (indented) or
writes it to -o. Exit 1 on an HTTP error, with the server's answer on stderr (401 = token missing /
revoked / expired, 403 = scope, 404 = the debug API is off / NO_PREVIEW / NOTHING_TO_UNDO, 409 =
CHANGED since the preview, 429 = rate limit); exit 2 for a bad command line (changes that are not
JSON). GET for everything but preview / apply / undo, which POST a JSON body.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

PATHS = {"activity": "/api/v1/debug/activity", "plan": "/api/v1/debug/plan", "day": "/api/v1/debug/day",
         "thresholds": "/api/v1/debug/thresholds", "sync": "/api/v1/debug/sync",
         "export": "/api/v1/debug/export/config", "export-plan": "/api/v1/debug/export/plan",
         # SP-890: SP-802's writes
         "preview": "/api/v1/debug/write/preview", "apply": "/api/v1/debug/write/apply",
         "undo": "/api/v1/debug/write/undo", "write-log": "/api/v1/debug/write/log"}
POSTS = ("preview", "apply", "undo")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl} refused", headers, fp)


def check_base(base: str) -> str:
    """The base address: https, or http only for localhost."""
    u = urllib.parse.urlparse(base)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname in LOCAL_HOSTS):
        return base.rstrip("/")
    raise ValueError("TRC_DEBUG_URL must be https:// (http only for localhost)")


def build_url(base: str, what: str, a: argparse.Namespace) -> str:
    q: dict = {}
    if what == "activity":
        for k in ("date", "id", "label", "streams", "every"):
            v = getattr(a, k, None)
            if v not in (None, ""):
                q[k] = v
        if a.gps:
            q["gps"] = 1
    elif what == "plan":
        if a.start:
            q["from"] = a.start
        if a.end:
            q["to"] = a.end
    elif what in ("day", "thresholds") and a.date:
        q["date"] = a.date
    elif what == "sync" and a.lines:
        q["lines"] = a.lines
    elif what == "write-log" and a.n:
        q["n"] = a.n
    url = base.rstrip("/") + PATHS[what]
    return url + ("?" + urllib.parse.urlencode(q) if q else "")


def headers(token: str, proxy_user: Optional[str] = None, proxy_password: Optional[str] = None) -> dict:
    h = {"X-TRC-Debug-Token": token, "Accept": "application/json"}
    if proxy_user:
        cred = base64.b64encode(f"{proxy_user}:{proxy_password or ''}".encode("utf-8")).decode("ascii")
        h["Authorization"] = f"Basic {cred}"
    return h


def fetch(url: str, hdrs: dict, timeout: float = 120.0) -> dict:
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def send(url: str, hdrs: dict, body: dict, timeout: float = 120.0) -> dict:
    """POST `body` as JSON (the /write/* calls); the same no-redirect opener as fetch."""
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={**hdrs, "Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def write_body(a: argparse.Namespace) -> dict:
    """The JSON body of a preview / apply / undo (ValueError for changes that are not JSON or not a
    list of objects)."""
    if a.what == "apply":
        return {"preview_id": a.preview_id}
    if a.what == "undo":
        return {"change_id": a.change_id} if a.change_id is not None else {}
    if a.file is not None:
        raw = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
    else:
        raw = a.changes
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"the changes are not JSON: {e}") from None
    changes = data.get("changes", [data]) if isinstance(data, dict) else data   # a dict: {changes} or one change
    if not isinstance(changes, list) or not changes or not all(isinstance(c, dict) for c in changes):
        raise ValueError('the changes must be a list of objects, e.g. [{"target": "threshold", …}]')
    return {"changes": changes}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("TRC_DEBUG_URL"), help="base address (TRC_DEBUG_URL)")
    ap.add_argument("-o", "--out", help="write the JSON here instead of stdout")
    sub = ap.add_subparsers(dest="what", required=True)
    p = sub.add_parser("activity", help="one activity: --date / --id / --label")
    p.add_argument("--date")
    p.add_argument("--id", type=int)
    p.add_argument("--label")
    p.add_argument("--streams", help="hr,power,speed,elev,cadence,dist")
    p.add_argument("--every", type=int, help="seconds between stream points (default 10)")
    p.add_argument("--gps", action="store_true", help="include latitude / longitude (needs read:gps)")
    p = sub.add_parser("plan", help="the stored plan of a range (default this week)")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    for name in ("day", "thresholds"):
        p = sub.add_parser(name)
        p.add_argument("date", nargs="?" if name == "thresholds" else None)
    p = sub.add_parser("sync", help="sync results and the app-log tail")
    p.add_argument("--lines", type=int)
    sub.add_parser("export", help="the athlete's settings (needs export:config)")
    sub.add_parser("export-plan", help="the stored plan, every row (needs export:plan)")
    # SP-890: the writes (needs write:config) — preview, read the diff, apply; undo puts it back
    p = sub.add_parser("preview", help="the diff of some changes and a preview_id; nothing written")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--changes", help='JSON: a list of changes or {"changes": [...]}')
    g.add_argument("-f", "--file", help="a JSON file of the same (- = stdin)")
    p = sub.add_parser("apply", help="write what a preview showed (while it is still what was previewed)")
    p.add_argument("preview_id")
    p = sub.add_parser("undo", help="put back what a write replaced (default: the newest)")
    p.add_argument("--change-id", type=int)
    p = sub.add_parser("write-log", help="the newest writes with what each replaced")
    p.add_argument("-n", type=int, help="how many (1-30, default 10)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    a = parser().parse_args(argv)
    token = os.environ.get("TRC_DEBUG_TOKEN")
    if not a.url or not token:
        print("set TRC_DEBUG_URL and TRC_DEBUG_TOKEN (the token only through the environment)", file=sys.stderr)
        return 2
    try:
        base = check_base(a.url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        body = write_body(a) if a.what in POSTS else None
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    url = build_url(base, a.what, a)
    hdrs = headers(token, os.environ.get("TRC_PROXY_USER"), os.environ.get("TRC_PROXY_PASSWORD"))
    try:
        data = send(url, hdrs, body) if body is not None else fetch(url, hdrs)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:2000] if e.fp else ""
        print(f"HTTP {e.code}: {e.msg} {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"cannot reach {base}: {e.reason}", file=sys.stderr)
        return 1
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
