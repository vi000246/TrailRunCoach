"""The app for the browser smoke tests: the synthetic demo athlete
(backend/demo/build.py, small mode) built into a temp folder, then served by a
real uvicorn on a free localhost port in OWNER mode with WKO5COACH_HOME = that
base (the demo build's own child process runs the same way), so every page —
also the settings page and the sync buttons the demo instance hides — is there.

No Playwright import here: the server part runs (and is checked) without it.
Synthetic only: never ~/WKO5 or ~/.wko5coach (the build refuses them, the
server's env points away from both), no COROS / TP account is configured.

    python -m backend.tests.e2e._server            # build, serve, GET every page once, stop
    python -m backend.tests.e2e._server --serve    # build and keep serving (look at the e2e data by hand)
"""
from __future__ import annotations

import datetime as dt
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# every page of the app (owner mode)
PAGES = {
    "overview": "/api/v1/overview/page",
    "schedule": "/api/v1/overview/plan/schedule/page",
    "templates": "/api/v1/overview/plan/templates/page",
    "compliance": "/api/v1/overview/plan/compliance/page",
    "plan": "/api/v1/plan/page",
    "racepower": "/api/v1/racepower/page",
    "settings": "/api/v1/wko5/settings",
    "activity": "/api/v1/wko5/activities/page",
    "viewer": "/api/v1/wko5/viewer",
    "routes": "/api/v1/routes/page",
}


def build_base(root: Path) -> Path:
    """The demo athlete (8 weeks, small) under root/base/<name>; returns the base."""
    from backend.demo import build as B
    return B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_env(base: Path) -> dict:
    from cryptography.fernet import Fernet
    from backend.demo import build as B
    env = B.child_env(base)           # owner mode on `base`, no WKO5 folder, no scheduler / warm-up
    env.update({"WKO5COACH_COOKIE_SECURE": "0", "TRC_DEFAULT_LOCALE": "zh-TW",
                "WKO5COACH_SECRET_KEY": Fernet.generate_key().decode()})   # never a secret.key file
    return env


def wait_up(url: str, proc: subprocess.Popen, timeout: float = 90.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited with {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return
        except OSError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"server not up after {timeout:.0f} s: {url}")


class Server:
    """uvicorn backend.main:app in a child process on 127.0.0.1:<free port>; its
    output goes to `log` (attach it to a failure report)."""

    def __init__(self, base: Path, log: Path, python: str = sys.executable):
        self.base = base
        self.log = log
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._log = open(log, "wb")
        self.proc = subprocess.Popen(
            [python, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=str(REPO), env=server_env(base), stdout=self._log, stderr=subprocess.STDOUT)
        try:
            wait_up(self.url + "/api/v1/session", self.proc)
        except Exception:
            self.stop()
            raise

    def log_tail(self, n: int = 4000) -> str:
        try:
            return self.log.read_text("utf-8", errors="replace")[-n:]
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self._log.close()


def main(argv: list[str]) -> None:
    import shutil
    import tempfile
    os.environ["WKO5COACH_TEST_REAL_HOME"] = str(Path.home())     # the build refuses roots inside it
    home = Path(tempfile.mkdtemp(prefix="trc-e2e-home-"))
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)    # nothing under the real home
    try:
        base = build_base(home / "demo-root")
        srv = Server(base, home / "uvicorn.log")
        try:
            if "--serve" in argv:
                print(f"serving {srv.url} (Ctrl+C to stop)", flush=True)
                srv.proc.wait()
                return
            for name, path in PAGES.items():
                with urllib.request.urlopen(srv.url + path, timeout=60) as r:
                    print(r.status, name, path, flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            srv.stop()
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
