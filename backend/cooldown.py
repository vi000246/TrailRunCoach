"""
Cool-downs of the manual 「重算／重新整理」 buttons (SP-714): after one use, the same person waits
before the next. Each one costs seconds to minutes of the server (or calls an outside API), and in
sign-in mode one person pressing again and again slows everyone down.

    cooldown.take("calib")                    # 429 {code: COOLDOWN, key, retry_after, message} while cooling
    cooldown.check("routes"); …; cooldown.mark("routes")   # only once the work really started
    cooldown.ready("racepower_inputs")        # True / False, never raises (a silent fallback)

The waits (minutes) are site_config's `cooldown.<entry>` (group cooldown, plan=True): the free
tier's is the site value, a subscriber's its plan's own default (shorter), each editable per plan
on the admin page. The owner, an admin, the demo and the single-user app (owner mode) never wait.

State: one row per user × key in accounts.db's `usage` table (key `cooldown:<key>`, period `last`,
`used` = the last use in epoch seconds) — kept across a restart, deleted with the account. A key is
the entry, or `<entry>:<sub>` when one entry has several independent buttons (manual sync per source,
補下載 per source): they share the entry's wait, each counts on its own.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from fastapi import HTTPException

from backend.i18n import N_

# entry -> what the button does (the 429's label; zh-TW, through _() at the answer)
ENTRIES: dict[str, str] = {
    "calib": N_("重新估算自動參數"),
    "routes": N_("更新路線"),
    "check": N_("完整檢查與補下載"),
    "sync": N_("手動同步"),
    "plan_auto": N_("立即自動調整課表"),
    "dedup": N_("重建去重"),
    "racepower_inputs": N_("重新讀取計算機的個人數據"),
    "surface": N_("重新查詢路面"),
}
PERIOD = "last"
clock: Callable[[], float] = time.time           # tests replace
_LOCK = threading.Lock()


def _entry(key: str) -> str:
    entry = key.split(":", 1)[0]
    if entry not in ENTRIES:
        raise ValueError(f"unknown cooldown {key}")
    return entry


def _member():
    from backend import membership as MS
    try:
        return MS.member()
    except Exception:                         # noqa: BLE001 — no tenant (a script): nothing to cool
        return None


def exempt(m) -> bool:
    """The owner, an admin, the demo, no signed-in user: never wait."""
    from backend import membership as MS
    return m is None or m.user_id is None or m.role in (MS.OWNER, MS.DEMO) or bool(m.admin)


def wait_s(key: str, m=None) -> int:
    """The wait after one use of `key` for this person (seconds; 0 = none)."""
    from backend import site_config as SC
    from backend import membership as MS
    entry = _entry(key)
    m = m if m is not None else _member()
    if exempt(m):
        return 0
    minutes = SC.effective(f"cooldown.{entry}", plan=m.plan, owner=m.role == MS.OWNER)
    return max(0, int(round(float(minutes or 0) * 60)))


def _row_key(key: str) -> str:
    return f"cooldown:{key}"


def _last(uid: int, key: str) -> Optional[float]:
    from backend.accounts import store
    store.init()
    with store.connect() as con:
        r = con.execute("SELECT used FROM usage WHERE user_id = ? AND key = ? AND period = ?",
                        (int(uid), _row_key(key), PERIOD)).fetchone()
    return float(r[0]) if r else None


def _write(uid: int, key: str, at: float) -> None:
    from backend.accounts import store
    store.init()
    with store.connect() as con:
        con.execute("INSERT INTO usage(user_id, key, period, used) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, key, period) DO UPDATE SET used = excluded.used",
                    (int(uid), _row_key(key), PERIOD, int(at)))


def remaining(key: str, m=None) -> int:
    """Seconds until `key` may be used again by this person (0 = now)."""
    m = m if m is not None else _member()
    w = wait_s(key, m)
    if not w:
        return 0
    try:
        last = _last(m.user_id, key)
    except Exception:                         # noqa: BLE001 — accounts.db unreadable: let it run
        return 0
    if last is None:
        return 0
    return max(0, int(-(-(last + w - clock()) // 1)))        # rounded up


def message(retry_after: int) -> str:
    """「X 分鐘後可以再按」 (an hour and more: 「約 X 小時後可以再按」)."""
    from backend.i18n import _
    mins = max(1, -(-int(retry_after) // 60))
    if mins < 60:
        return _("{n} 分鐘後可以再按", n=mins)
    return _("約 {n} 小時後可以再按", n=-(-mins // 60))


def error(key: str, retry_after: int) -> HTTPException:
    from backend.i18n import _
    retry_after = max(1, int(retry_after))
    return HTTPException(429, {"code": "COOLDOWN", "key": key, "label": _(ENTRIES[_entry(key)]),
                               "retry_after": retry_after, "message": message(retry_after)},
                         headers={"Retry-After": str(retry_after)})


def check(key: str) -> None:
    """429 COOLDOWN while `key` cools down for this person; nothing is recorded."""
    left = remaining(key)
    if left > 0:
        raise error(key, left)


def mark(key: str) -> None:
    """Record one use of `key` now (no-op for those who never wait)."""
    m = _member()
    if not wait_s(key, m):
        return
    try:
        _write(m.user_id, key, clock())
    except Exception:                         # noqa: BLE001 — never fails the work that started
        pass


def take(key: str) -> None:
    """check() and mark() at once (two quick presses: the second gets the 429)."""
    m = _member()
    if not wait_s(key, m):
        return
    with _LOCK:
        left = remaining(key, m)
        if left > 0:
            raise error(key, left)
        try:
            _write(m.user_id, key, clock())
        except Exception:                     # noqa: BLE001
            pass


def ready(key: str) -> bool:
    """True when `key` may run now (a caller that falls back quietly instead of a 429)."""
    return remaining(key) <= 0


def guard(key: str) -> Callable:
    """A FastAPI dependency: take(key) before the endpoint runs."""
    _entry(key)

    def dep() -> None:
        take(key)
    dep.__name__ = f"cooldown_{key.replace(':', '_')}"
    return dep


def active(m=None) -> dict:
    """{key: seconds left} of every key cooling down for this person now (GET /api/v1/session:
    the pages disable those buttons on load)."""
    m = m if m is not None else _member()
    if exempt(m):
        return {}
    from backend.accounts import store
    try:
        store.init()
        with store.connect() as con:
            rows = con.execute("SELECT key, used FROM usage WHERE user_id = ? AND period = ? AND key LIKE 'cooldown:%'",
                               (int(m.user_id), PERIOD)).fetchall()
    except Exception:                         # noqa: BLE001
        return {}
    out = {}
    now = clock()
    for k, used in rows:
        key = k.split(":", 1)[1]
        try:
            w = wait_s(key, m)
        except ValueError:
            continue
        left = int(-(-(float(used) + w - now) // 1))
        if w and left > 0:
            out[key] = left
    return out
