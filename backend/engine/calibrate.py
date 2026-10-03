"""
Per-athlete calibration (generalize-athlete plan §0.1, batch B1).

Every 「A 類」 number that used to be tuned on one runner's data becomes an
`Item`: a literature / 推估 default plus a fit on the athlete's own data,
shrunk toward the default with w = n / (n + k), as engine/drift_agg.py does
for the AeT heat β. Below `min_n` points nothing is written and the default
stands.

Storage: one settings key per item, `athlete.calib.<name>` (settings/
repository.py), value

    {"value", "se", "n", "fitted_at", "source": "default" | "fitted" | "user",
     "personal", "w"}

`source = "user"` (進階設定 → 手動指定) is never overwritten by a fit.

When: `after_sync` (sync/runner.py, next to plan_auto) starts `run_safe` in
the background after a sync that imported an activity; `calibrate()` can be
called directly (POST /api/v1/calib/run).

Reading: `value(name)` (sync, read-only) for engine code; `describe()` for
the API / settings page, with the chip text 「本人 n=…」 or 「預設（文獻／推估）」
and its hover help (static/calib_chip.js renders it).

Adding an item: `register(Item(...))` in the module that owns the number,
plus a self-consistency test (backend/tests/calib_fixtures.py): on a
synthetic fixture that mimics that runner's data, the fitted value must land
within ±1 SE of today's constant.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

KEY_PREFIX = "athlete.calib."
SOURCES = ("default", "fitted", "user")


@dataclass(frozen=True)
class Fit:
    """An item's fit on the athlete's data: the estimate, its SE, n points."""
    value: float
    se: Optional[float]
    n: int


@dataclass(frozen=True)
class Item:
    name: str                                   # athlete.calib.<name>
    label: str                                  # 「AeT 熱 β」
    unit: str                                   # 「bpm／°C」
    default: float
    default_src: str                            # where the default comes from (文獻 … / 推估)
    k: float                                    # shrinkage: w = n / (n + k) (推估 unless noted)
    min_n: int                                  # below this the default stands, nothing written
    fit: Callable[..., Optional[Fit]] = field(compare=False)   # fit(ds, today) -> Fit | None
    bounds: Optional[tuple[float, float]] = None
    digits: int = 2
    help: str = ""                              # what the number does (hover)
    default_is_literature: bool = False         # chip: 預設（文獻） vs 預設（推估）
    manual_only: bool = False                   # 進階 C 類: no fit, a default you may override by hand


REGISTRY: dict[str, Item] = {}


def register(item: Item) -> Item:
    REGISTRY[item.name] = item
    return item


def key(name: str) -> str:
    return KEY_PREFIX + name


def is_key(k: str) -> bool:
    return k.startswith(KEY_PREFIX) and k[len(KEY_PREFIX):] in _registry()


def _registry() -> dict[str, Item]:
    _load_items()
    return REGISTRY


def _load_items() -> None:
    """Import the modules that register items (idempotent)."""
    from backend.engine import drift_agg  # noqa: F401  (aet_heat_beta)
    from backend.engine import heat_calib  # noqa: F401  (hadley_hr_beta, humidity_default, home_*)
    from backend.engine import effort_calib  # noqa: F401  (trail_max_min_km, trail_max_min_min, effort_rest_max)
    from backend.engine import terrain_calib  # noqa: F401  (climb_divisor_run)
    from backend.engine import advanced_params  # noqa: F401  (進階 C 類: heat_partial_hadley, pack_daily_drop_kg)
    from backend.engine import drift_calib  # noqa: F401  (drift windows)


def validate_entry(v) -> None:
    if v is None:
        return
    if not isinstance(v, dict) or v.get("source") not in SOURCES:
        raise ValueError("calibration entry must be {value, se, n, fitted_at, source: default|fitted|user}")
    val = v.get("value")
    if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val)):
        raise ValueError("calibration value must be a number")
    if v["source"] == "user" and val is None:
        raise ValueError("a manual calibration value needs a number")


# ---------------------------------------------------------------------------
# the maths
# ---------------------------------------------------------------------------

def shrink(item: Item, fit: Optional[Fit]) -> Optional[dict]:
    """The entry to store for `fit`, or None when there is not enough data
    (n < min_n): value = w·personal + (1 − w)·default, w = n / (n + k),
    clipped to the item's bounds."""
    if fit is None or fit.value is None or not math.isfinite(fit.value) or fit.n < item.min_n:
        return None
    w = fit.n / (fit.n + item.k)
    v = w * fit.value + (1.0 - w) * item.default
    if item.bounds:
        v = min(max(v, item.bounds[0]), item.bounds[1])
    return {"value": round(v, 6), "se": None if fit.se is None else round(w * fit.se, 6), "n": int(fit.n),
            "personal": round(float(fit.value), 6), "w": round(w, 4), "source": "fitted"}


def resolve(item: Item, stored: Optional[dict]) -> dict:
    """The entry in effect: a manual or fitted one as stored, else the default."""
    if stored and stored.get("source") in ("user", "fitted") and stored.get("value") is not None:
        return {**stored}
    return {"value": item.default, "se": None, "n": int((stored or {}).get("n") or 0), "source": "default",
            "fitted_at": (stored or {}).get("fitted_at")}


def chip(item: Item, entry: dict) -> dict:
    """{"text", "tip"}: 「本人 n=24」 / 「手動」 / 「預設（文獻）」 / 「預設（推估）」 and the hover help."""
    fmt = f"{{:.{item.digits}f}}"
    src = entry.get("source")
    if src == "user":
        text = "手動"
        tip = f"{item.label} {fmt.format(entry['value'])} {item.unit}：你在進階設定手動指定，不會被自動估算覆蓋。"
    elif src == "fitted":
        text = f"本人 n={entry.get('n')}"
        tip = (f"{item.label} {fmt.format(entry['value'])} {item.unit}：本人 {entry.get('n')} 筆資料擬合 "
               f"{fmt.format(entry.get('personal', entry['value']))}，權重 {float(entry.get('w') or 0):.0%}，"
               f"其餘用預設 {fmt.format(item.default)}（{item.default_src}）。")
    else:
        text = "預設（文獻）" if item.default_is_literature else "預設（推估）"
        tip = (f"{item.label} {fmt.format(item.default)} {item.unit}：{item.default_src}。"
               + ("只有確定時才手動指定。" if item.manual_only
                  else f"本人資料 {entry.get('n') or 0} 筆，滿 {item.min_n} 筆才會自己擬合。"))
    if item.help:
        tip += " " + item.help
    return {"text": text, "tip": tip}


def describe(name: str, stored: Optional[dict]) -> dict:
    item = _registry()[name]
    e = resolve(item, stored)
    return {"name": name, "label": item.label, "unit": item.unit, "digits": item.digits,
            "default": item.default, "default_src": item.default_src, "min_n": item.min_n, "k": item.k,
            "bounds": list(item.bounds) if item.bounds else None, **e, "chip": chip(item, e)}


# ---------------------------------------------------------------------------
# reading (sync) and fitting
# ---------------------------------------------------------------------------

_READ_MEMO: dict = {}
_READ_TTL_S = 10.0          # engine loops read the same entry per activity: one DB read per 10 s


def stored_entry(name: str, user_id: int = 1) -> Optional[dict]:
    import time
    from backend.engine.wko5expr.datasource import _db_path, read_setting
    mk = (name, user_id, str(_db_path()))
    hit = _READ_MEMO.get(mk)
    if hit and time.monotonic() - hit[0] < _READ_TTL_S:
        return hit[1]
    v = read_setting(key(name), None, user_id)
    v = v if isinstance(v, dict) else None
    _READ_MEMO[mk] = (time.monotonic(), v)
    return v


def forget_reads() -> None:
    _READ_MEMO.clear()


def entry(name: str, user_id: int = 1) -> dict:
    """The entry in effect for `name` (resolve of the stored one)."""
    return resolve(_registry()[name], stored_entry(name, user_id))


def value(name: str, user_id: int = 1) -> float:
    return float(entry(name, user_id)["value"])


def run(ds, stored: dict, today: Optional[dt.date] = None) -> tuple[dict, dict]:
    """(updates {name: entry}, skipped {name: why}) for every registered item.
    A manual entry is kept; a fit below min_n writes nothing (the default /
    the last good fit stand); a fit that raises is skipped."""
    today = today or dt.date.today()
    updates, skipped = {}, {}
    for name, item in _registry().items():
        if (stored.get(name) or {}).get("source") == "user":
            skipped[name] = "user"
            continue
        try:
            f = item.fit(ds, today)
        except Exception as e:              # noqa: BLE001 — one item never stops the others
            log.warning("calibration %s failed: %s", name, type(e).__name__)
            skipped[name] = f"error:{type(e).__name__}"
            continue
        e = shrink(item, f)
        if e is None:
            skipped[name] = f"n={0 if f is None else f.n}<{item.min_n}"
            continue
        updates[name] = {**e, "fitted_at": today.isoformat()}
    return updates, skipped


async def calibrate(db, athlete_id: int = 1, ds=None, today: Optional[dt.date] = None) -> dict:
    """Fit every item on the athlete's chart Dataset and store the results."""
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    stored = {n: await repo.get(key(n)) for n in _registry()}
    if ds is None:
        from backend.api.wko5views import _dataset
        ds = await asyncio.to_thread(_dataset, False)
    updates, skipped = await asyncio.to_thread(run, ds, stored, today)
    forget_reads()
    for n, e in updates.items():
        await repo.set(key(n), e)
    await db.commit()
    return {"written": sorted(updates), "skipped": skipped}


SESSION_FACTORY: Optional[Callable] = None      # tests replace; default AsyncSessionLocal
_TASKS: set = set()


async def run_safe(athlete_id: int = 1) -> dict:
    factory = SESSION_FACTORY
    if factory is None:
        from backend.db.database import AsyncSessionLocal as factory
    try:
        async with factory() as db:
            return await calibrate(db, athlete_id)
    except Exception as e:                  # noqa: BLE001 — the sync must not break
        log.warning("calibration run failed: %s", type(e).__name__)
        return {"status": "failed", "error": type(e).__name__}


def _after_sync(source: str, result: dict, athlete_id: int = 1) -> Optional[asyncio.Task]:
    """sync/runner.py calls this when a run ends: ≥ 1 new activity -> a background fit."""
    if (result or {}).get("status") not in ("ok", "partial") or int((result or {}).get("downloaded") or 0) < 1:
        return None
    try:
        t = asyncio.get_running_loop().create_task(run_safe(athlete_id))
    except RuntimeError:
        return None
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


after_sync = _after_sync           # the hook sync/runner.py calls (tests replace it)
