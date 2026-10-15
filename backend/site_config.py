"""
The server-wide settings an admin edits (SP-351): what each one is, its bounds, where its value
comes from, and the log of every change. Stored in site_settings (<server-root>/site.json), never
in a tenant's own settings.

Where a value comes from (`get`, `source`):
  1. the admin page (site.json, key as below)                  source "site"
  2. its environment variable, when it has one (TRC_MAX_USERS)  source "env"
  3. the program's default                                      source "default"
The admin page wins over the environment: the variable is the deploy's starting value, the page
is where it is changed afterwards (it shows which one is in force). Removing the page's value
(None) falls back to the variable again.

For one person (`effective`), three layers before that:
  personal setting (the tenant's own, where the item has one: retention years, region)
    -> the plan's default (site.json `plan.<free|subscriber>.<key>`, items with plan=True)
    -> the site value above (page -> env -> default)
The owner is never limited by a plan's default (the self-hosted app behaves as before).

The log (`audit`, `audit_rows`): <server-root>/site_audit.jsonl, append-only, one line per change:
when, who (user id; 1 = the owner), what (action, key or target id) and the old and new value.
Users appear by id only — never an e-mail, a name or a token.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from backend import site_settings as SS

PATH: Optional[Path] = None          # fixed audit file (tests); None = <server-root>/site_audit.jsonl
AUDIT_FILE = "site_audit.jsonl"
AUDIT_READ = 500                     # newest rows the page reads
PLANS = ("free", "subscriber")
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Item:
    key: str
    group: str                       # sync | members | quota | coros | retention | shared
    kind: str                        # int | float | time | choice | text
    default: Any
    lo: Optional[float] = None
    hi: Optional[float] = None
    choices: tuple = field(default_factory=tuple)
    env: Optional[str] = None        # the variable the code reads (its old name; the page shows the new one)
    plan: bool = False               # a plan may have its own default
    personal: Optional[str] = None   # the tenant's own setting that comes first
    max_len: int = 500


ITEMS: dict[str, Item] = {i.key: i for i in (
    # 同步 — the first back-fill of a new connection: how far back (days; 0 = everything from the
    # source's own start, 2020-01-01). A plan may have its own (a subscriber's longer history)
    Item("sync.history_days", "sync", "int", 0, 0, 36500, plan=True),
    # the free tier's nightly sync: its window (scheduler.night_slot)
    Item("sync.night_start", "sync", "time", "01:00"),
    Item("sync.night_span_min", "sync", "int", 240, 30, 720),
    # 會員封頂 (SP-58): sign-up stops at this many users (the owner and disabled users count)
    Item("members.max_users", "members", "int", 100, 1, 100000, env="WKO5COACH_MAX_USERS"),
    # the plans' limits (SP-321 / SP-330)
    Item("quota.free.charts", "quota", "int", 5, 0, 10000),
    Item("quota.free.calc", "quota", "int", 10, 0, 10000),
    Item("quota.subscriber.backtest", "quota", "int", 2, 0, 10000),
    # COROS for the whole server (SP-385, sync/coros_limits.py reads the same keys)
    Item("coros.rate_per_s", "coros", "float", 2.0, 0.1, 50),
    Item("coros.rate_per_min", "coros", "float", 60.0, 1, 3000),
    Item("coros.max_concurrent", "coros", "int", 1, 1, 50),
    # 運動紀錄保留 (SP-317): the person's own choice first, then the plan's, then the site's
    Item("archive.retention_years", "retention", "choice", 3, choices=(2, 3, 5, 10), plan=True,
         personal="archive.retention_years"),
    # 共用設定: the region a new person starts in (until their data says otherwise), the notice
    Item("defaults.region", "shared", "choice", "intl", choices=("tw", "intl"), personal="athlete.region"),
    Item("notice.zh", "shared", "text", "", max_len=500),
    Item("notice.en", "shared", "text", "", max_len=500),
)}


class ConfigError(ValueError):
    def __init__(self, key: str, code: str):
        super().__init__(f"{key}: {code}")
        self.key, self.code = key, code


def _item(key: str) -> Item:
    it = ITEMS.get(key)
    if it is None:
        raise ConfigError(key, "UNKNOWN_KEY")
    return it


def validate(key: str, v: Any) -> Any:
    """The value as stored, or ConfigError (BAD_VALUE / OUT_OF_RANGE / TOO_LONG)."""
    it = _item(key)
    if it.kind in ("int", "float"):
        if isinstance(v, bool):
            raise ConfigError(key, "BAD_VALUE")
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ConfigError(key, "BAD_VALUE") from None
        if n != n:                                            # NaN
            raise ConfigError(key, "BAD_VALUE")
        if it.kind == "int":
            if n != int(n):
                raise ConfigError(key, "BAD_VALUE")
            n = int(n)
        if (it.lo is not None and n < it.lo) or (it.hi is not None and n > it.hi):
            raise ConfigError(key, "OUT_OF_RANGE")
        return n
    if it.kind == "time":
        try:
            h, m = str(v).split(":")
            if not (len(h) == 2 and len(m) == 2 and 0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise ValueError
        except ValueError:
            raise ConfigError(key, "BAD_VALUE") from None
        return str(v)
    if it.kind == "choice":
        if isinstance(v, bool) or v not in it.choices:
            raise ConfigError(key, "BAD_VALUE")
        return v
    if not isinstance(v, str):
        raise ConfigError(key, "BAD_VALUE")
    v = v.strip()
    if len(v) > it.max_len:
        raise ConfigError(key, "TOO_LONG")
    return v


def _valid(key: str, v: Any) -> Optional[Any]:
    if v is None:
        return None
    try:
        return validate(key, v)
    except ConfigError:
        return None


def _env(it: Item) -> Optional[Any]:
    if not it.env:
        return None
    raw = (os.environ.get(it.env) or "").strip()
    return _valid(it.key, raw) if raw else None


def env_name(key: str) -> Optional[str]:
    """The variable to set (its new TRC_ name)."""
    it = _item(key)
    if not it.env:
        return None
    from backend import env_names
    return env_names.new_name(it.env)


def get(key: str) -> Any:
    """The site's value: the admin page, else the environment, else the default."""
    it = _item(key)
    v = _valid(key, SS.get(key))
    if v is not None:
        return v
    v = _env(it)
    return it.default if v is None else v


def source(key: str) -> str:
    """site | env | default — where get(key) came from."""
    it = _item(key)
    if _valid(key, SS.get(key)) is not None:
        return "site"
    return "env" if _env(it) is not None else "default"


def plan_key(plan: str, key: str) -> str:
    return f"plan.{plan}.{key}"


def plan_value(plan: str, key: str) -> Optional[Any]:
    """A plan's own default (None: the plan has none, the site value applies)."""
    it = _item(key)
    if not it.plan or plan not in PLANS:
        return None
    return _valid(key, SS.get(plan_key(plan, key)))


def effective(key: str, personal: Any = None, plan: Optional[str] = None, owner: bool = False) -> Any:
    """For one person: their own setting -> their plan's default -> the site value. The owner
    skips the plan layer."""
    it = _item(key)
    if it.personal and personal is not None:
        v = _valid(key, personal)
        if v is not None:
            return v
    if plan and not owner:
        v = plan_value(plan, key)
        if v is not None:
            return v
    return get(key)


def for_current(key: str, personal: Any = None) -> Any:
    """effective() for the current tenant (its plan from membership)."""
    from backend import membership as MS
    try:
        m = MS.member()
    except Exception:                        # noqa: BLE001 — no tenant: the site value
        return effective(key, personal)
    return effective(key, personal, m.plan, owner=m.role == MS.OWNER)


# ---------------------------------------------------------------------------- what the sync reads

def first_sync_day(default: dt.date, today: Optional[dt.date] = None) -> dt.date:
    """The oldest day a NEW connection's first back-fill reaches for the current tenant: the
    source's own start (`default`) or, with sync.history_days set (site / plan), that many days
    back — whichever is later."""
    days = for_current("sync.history_days")
    if not days:
        return default
    today = today or dt.date.today()
    return max(default, today - dt.timedelta(days=int(days)))


# ---------------------------------------------------------------------------- changes

def view() -> dict:
    """Everything the admin page shows: each item's value, where it came from, the plans' own."""
    items = []
    for key, it in ITEMS.items():
        row = {"key": key, "group": it.group, "kind": it.kind, "value": get(key), "source": source(key),
               "default": it.default, "plan": it.plan}
        if it.lo is not None:
            row["min"] = it.lo
        if it.hi is not None:
            row["max"] = it.hi
        if it.choices:
            row["choices"] = list(it.choices)
        if it.env:
            row["env"] = env_name(key)
            row["env_value"] = _env(it)
        if it.kind == "text":
            row["max_len"] = it.max_len
        items.append(row)
    plans = {p: {k: plan_value(p, k) for k, it in ITEMS.items() if it.plan} for p in PLANS}
    return {"items": items, "plans": plans}


def set_values(values: dict, actor: Optional[int], plans: Optional[dict] = None) -> list:
    """Validate every change first (nothing is written when one is wrong: ConfigError), then write
    and log each one that changed. `None` removes the page's value (back to env / default; for a
    plan, back to the site value). Returns the log rows written."""
    writes: dict = {}
    for k, v in (values or {}).items():
        writes[k] = None if v is None or v == "" else validate(k, v)
        if writes[k] == "":                                   # a notice cleared to blanks
            writes[k] = None
    for p, vals in (plans or {}).items():
        if p not in PLANS:
            raise ConfigError(str(p), "UNKNOWN_PLAN")
        for k, v in (vals or {}).items():
            if not _item(k).plan:
                raise ConfigError(k, "NO_PLAN_VALUE")
            writes[plan_key(p, k)] = None if v is None or v == "" else validate(k, v)
    rows = []
    with _LOCK:
        cur = SS.all()
        changed = {k: v for k, v in writes.items() if cur.get(k) != v}
        if not changed:
            return []
        SS.update(changed)
        for k, v in changed.items():
            rows.append(_append("config", k, cur.get(k), v, actor))
    return rows


# ---------------------------------------------------------------------------- the log

def audit_path() -> Path:
    if PATH is not None:
        return Path(PATH)
    from backend import accounts
    return accounts.server_root() / AUDIT_FILE


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _append(action: str, target: Any, old: Any, new: Any, actor: Optional[int]) -> dict:
    row = {"at": _now(), "by": actor, "action": action, "target": target, "old": old, "new": new}
    p = audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def audit(action: str, target: Any, old: Any, new: Any, actor: Optional[int]) -> dict:
    """One change made on the admin page (a user disabled, a plan changed, a source switched, a
    waitlisted person let in). `target`: a user id, a scope, a waitlist row id — never an e-mail."""
    with _LOCK:
        return _append(action, target, old, new, actor)


def audit_rows(limit: int = AUDIT_READ) -> list:
    """The newest `limit` rows, newest first."""
    p = audit_path()
    try:
        lines = p.read_text("utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out
