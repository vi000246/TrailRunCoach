"""
地形難度 (SP-725 / SP-731; the user's word 2026-10-19, not 「路線難度」): how technical a whole route's
terrain is, in three levels — 一般 / 技術路段多 / 需要手腳並用.

Why (SP-716): on 2026-10-18 the athlete was 15–25 % slower than on their usual trails at the same
grade from the first hour on (40 % on the scramble), with a high HR — the terrain's technicality, not
fatigue; OSM rarely tags it and trail popularity (Happyman) does not track it. So the user marks it:
on a past activity (activity_tags.TERRAINS, one tag), as a default on a 「我的路段」 route (this module:
routes/terrain.json, the activities on that route without a mark of their own take it) and on the
race calculator's route (PlanIn.terrain_difficulty).

What it does to a prediction: the trail HR pace model (racepower/trailhr.py, effort km = km + gain /
153, speed v₀(x) at HR level x with the durability decline) takes the level's multiplier m on the
EFFORT DISTANCE: a technical route needs m × the effort km at the same HR. That is the model's own
shape of "slower at the same grade and the same HR from the first hour on" (10/18); the durability
decline then acts on the longer moving time, as it would on a longer course. (A multiplier on the
moving time alone would skip that; on short runs the two are the same.)

The multipliers (trailhr.fit_terrain): per level, the shrunk median of the marked activities' ratio
"actual ÷ model prediction, the activity left out" (in no-decay hours, the inverse of the effort-km
multiplier), shrunk toward the prior by n / (n + k); 一般 = 1 by definition. Prior (推估, no source):
技術路段多 1.15 (the lower end of 10/18's 15–25 %), 需要手腳並用 1.35 (between 10/18's whole-route 25 %
and its scramble's 40 %). A level nobody marked yet uses its prior, labelled 推估.
"""
from __future__ import annotations

from typing import Optional

LEVELS = ("normal", "technical", "scramble")
LABELS = {"normal": "一般", "technical": "技術路段多", "scramble": "需要手腳並用"}
# 推估 (SP-716 10/18: whole route 15–25 % slower at the same grade, its scramble 40 %)
PRIOR = {"normal": 1.0, "technical": 1.15, "scramble": 1.35}
SHRINK_K = 2.0          # 推估: the prior counts as 2 marked activities
MULT_RANGE = (1.0, 2.5)  # a harder level is never faster than 一般; 2.5 guards a broken record
SOURCE = ("地形難度倍率：你標過該級的越野跑，每筆「實際 ÷ 模型預估（不含該筆）」的中位數，往先驗收縮"
          "（技術路段多 1.15、需要手腳並用 1.35，先驗當 2 筆）；一般 = 1；作用在努力距離（同心率下要走更多 effort km），推估")


def valid(level) -> bool:
    return level in LEVELS


def prior(level: Optional[str]) -> float:
    return PRIOR.get(level or "normal", 1.0)


# ---------------------------------------------------------------------------
# route defaults (「我的路段」 routes, engine/routes.py RouteStore.terrain)
# ---------------------------------------------------------------------------

def route_defaults(store=None) -> dict:
    """{route id: level} the user set (routes/terrain.json); {} when none / unreadable."""
    try:
        from backend.engine.routes import RouteStore
        d = (store or RouteStore()).terrain()
    except Exception:                       # noqa: BLE001
        return {}
    return {k: v for k, v in d.items() if valid(v)}


def route_level_map(index: Optional[dict], defaults: dict) -> tuple[dict, list]:
    """({activity file: level}, [(start 'YYYY-MM-DDTHH:MM', level)]) of the activities on routes with a
    default (route index "routes": members + efforts with file / start; either direction counts — the
    terrain is the same both ways). A sub-route's own default wins over its parent's (listed later)."""
    by_file: dict = {}
    starts: list = []
    if not index or not defaults:
        return by_file, starts
    rs = [r for r in index.get("routes") or [] if r.get("id") in defaults]
    rs.sort(key=lambda r: 1 if r.get("parent") else 0)          # parents first, sub-routes override
    for r in rs:
        lv = defaults[r["id"]]
        for f in r.get("members") or []:
            by_file[f] = lv
        for e in r.get("efforts") or []:
            if e.get("file"):
                by_file[e["file"]] = lv
            if e.get("start"):
                starts.append((str(e["start"])[:16], lv))
    return by_file, starts


def _route_level(w, by_file: dict, starts: list) -> Optional[str]:
    lv = by_file.get(w.entry.file)
    if lv:
        return lv
    if not starts:
        return None
    from backend.engine.activity_key import same_file
    for f, v in by_file.items():
        if same_file(f, w.entry.file):
            return v
    import datetime as dt
    s = w.entry.start.replace(tzinfo=None)
    best, bd = None, None
    for k, v in starts:
        try:
            d = abs((dt.datetime.fromisoformat(k) - s).total_seconds())
        except ValueError:
            continue
        if d <= 180 and (bd is None or d < bd):
            best, bd = v, d
    return best


def levels(ds, tags: Optional[list] = None, index: Optional[dict] = None,
           defaults: Optional[dict] = None) -> dict:
    """{workout idx: {"level", "src": "user" | "route"}} of every activity with a 地形難度: the user's
    own mark (activity_tags) wins, else its route's default. Memoised on the dataset by the inputs."""
    from backend.engine import activity_tags as AT
    if tags is None:
        try:
            tags = AT.load()
        except Exception:                   # noqa: BLE001
            tags = []
    if defaults is None:
        defaults = route_defaults()
    if index is None and defaults:
        try:
            from backend.engine.routes import RouteStore
            index = RouteStore().load_index()
        except Exception:                   # noqa: BLE001
            index = None
    memo = getattr(ds, "memo", None)
    mkey = ("terrain_levels", id(tags), len(tags or []), tuple(sorted(defaults.items())), id(index))
    if isinstance(memo, dict) and mkey in memo:
        return memo[mkey]
    by_file, starts = route_level_map(index, defaults)
    out: dict = {}
    for w in ds.workouts:
        u = AT.find(tags, w.entry.start, w.entry.file) if tags else None
        lv = AT.terrain_of(AT.tags_of(u))
        if lv:
            out[w.idx] = {"level": lv, "src": "user"}
            continue
        if by_file or starts:
            rl = _route_level(w, by_file, starts)
            if rl:
                out[w.idx] = {"level": rl, "src": "route"}
    if isinstance(memo, dict):
        memo[mkey] = out
    return out


def stamp() -> Optional[int]:
    """The route defaults file's mtime (a cache key part: a new default refits the model)."""
    try:
        from backend.engine.routes import RouteStore
        return RouteStore().terrain_path.stat().st_mtime_ns
    except Exception:                       # noqa: BLE001
        return None
