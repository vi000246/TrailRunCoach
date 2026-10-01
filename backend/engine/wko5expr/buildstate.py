"""
Progress of the chart Dataset builds, for GET /api/v1/wko5/dataset/status
and the pages that wait on a build (shell.js shows
「正在處理第 350／808 筆（解析 FIT）…」).

One BuildState per data source (wko5 / coros / tp / a test folder). The
build code reports phases and counts; the endpoint reads a snapshot. Thread
safe: builds run in worker threads, the endpoint on the event loop.

    st = buildstate.get("coros")
    st.begin()
    st.phase("parse", total=808)
    st.tick()                   # one file done
    st.finish()                 # or st.fail(exc)
"""
from __future__ import annotations

import threading
import time
from typing import Optional

PHASE_LABELS = {
    "scan": "掃描檔案",
    "parse": "解析 FIT",
    "assemble": "整理活動",
    "estimate": "估算門檻",
    "finish": "收尾",
    "wko5": "讀取 WKO5 資料夾",
}


class BuildState:
    def __init__(self, source: str):
        self.source = source
        self._lock = threading.Lock()
        self._s = {"source": source, "state": "idle", "phase": None, "phase_label": None,
                   "n_done": 0, "n_total": 0, "started_at": None, "finished_at": None,
                   "elapsed_s": None, "last_build_s": None, "error": None}

    def begin(self) -> None:
        with self._lock:
            self._s.update(state="building", phase=None, phase_label=None, n_done=0, n_total=0,
                           started_at=time.time(), finished_at=None, elapsed_s=None, error=None)

    def phase(self, name: str, total: Optional[int] = None) -> None:
        with self._lock:
            self._s.update(phase=name, phase_label=PHASE_LABELS.get(name, name), n_done=0,
                           n_total=int(total or 0))

    def tick(self, n: int = 1) -> None:
        with self._lock:
            self._s["n_done"] += n

    def finish(self) -> None:
        with self._lock:
            now = time.time()
            took = now - (self._s["started_at"] or now)
            self._s.update(state="ready", finished_at=now, elapsed_s=round(took, 1),
                           last_build_s=round(took, 1), phase=None, phase_label=None)

    def fail(self, e: BaseException) -> None:
        with self._lock:
            self._s.update(state="error", finished_at=time.time(), error=f"{type(e).__name__}: {e}"[:300])

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._s)
        if s["state"] == "building" and s["started_at"]:
            s["elapsed_s"] = round(time.time() - s["started_at"], 1)
        s["message"] = message(s)
        return s


def message(s: dict) -> Optional[str]:
    """The text a waiting page shows."""
    if s.get("state") != "building":
        return None
    lab = s.get("phase_label") or "準備中"
    if s.get("n_total"):
        return f"正在處理第 {min(s['n_done'] + 1, s['n_total'])}／{s['n_total']} 筆（{lab}）…"
    return f"正在準備資料（{lab}）…"


_STATES: dict[str, BuildState] = {}
_LOCK = threading.Lock()


def get(source: Optional[str]) -> BuildState:
    key = source or "wko5"
    with _LOCK:
        st = _STATES.get(key)
        if st is None:
            st = _STATES[key] = BuildState(key)
        return st


def all_states() -> dict[str, dict]:
    with _LOCK:
        items = list(_STATES.items())
    return {k: v.snapshot() for k, v in items}
