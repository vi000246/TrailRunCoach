"""
活動類型對照表 (SP-263) — one platform-neutral activity type per activity, and
the table that maps every watch platform's own sport codes onto it.

    app type   road 路跑 / trail 越野跑 / hike 登山健行 / bike 騎車 /
               strength 肌力 / walk 走路 / other 其他
               (the same keys as overview.CATEGORIES: that module's colours,
               totals and every engine rule reading overview.category use it)
    filter     the app types + baiyue 百岳登山 (the 圖表分析 activity filter):
               a hike counts as 百岳 when its activity type
               (activity_tags: user mark wins, else the auto rule) is
               百岳跟團, or (no user mark) when the 成就 page's GPS detection
               (achievements.baiyue_summits) found it passed a 百岳 summit
               (owner 2026-10-06); every activity is in exactly one filter kind.

PLATFORMS is the one mapping table: platform -> platform code -> app type.

  * "coros": COROS `sportType` (workout_files.coros_sport_type, stored by the
    sync). Codes and names: the community COROS API client xballoy/coros-api,
    src/coros/sport-type.ts (https://github.com/xballoy/coros-api), as used by
    the COROS Training Hub web app; 1200 Hybrid Fitness from luketmoss/thrive
    issue #194. Codes seen in one account's 812 synced activities
    (2026-10-06): 100, 101, 102, 104, 105, 200, 201, 300, 301, 402 and 9904
    (not in any public list: no GPS, no distance — a custom sport, 推估;
    left unmapped, so it falls through to the FIT, which says "generic", and
    ends as other).
  * "garmin": the FIT profile `sport` / `sub_sport` enums (Garmin FIT SDK
    Profile.xlsx, Types sheet; the same enums ship in fitdecode 0.11's
    profile). Any watch's FIT file uses them, COROS's included, so this is
    also the fallback for a FIT without a platform code. Key: (sport,
    sub_sport) or (sport, None) for "any sub_sport".
  * "wko5": WKO5 / TrainingPeaks sport type names (the WKO5 athlete file and
    the Dataset's lower-case `sport_type`): the legacy rules, unchanged.

Resolution (`app_type`), first match:
  1. a trail run — algorithms.classify.is_trail: the app DB's trail / road
     classification (climb rate at import; a user override always wins) or
     WKO5's runningtrail tag. A run that is not a trail run is road whatever
     the platform says, so 越野跑 / 路跑 agree with every engine rule;
  2. the platform code (COROS sportType) when the activity has one and it is
     in the table;
  3. the FIT sport / sub_sport;
  4. the WKO5 names (sport type, tags, sport group) — the original
     overview.category rules;
  5. other.
An unmapped code never guesses: it falls through to the next source and
finally to "other".
"""
from __future__ import annotations

from typing import Iterable, Optional

from backend.i18n import N_

# ---------------------------------------------------------------------------
# the app's own types
# ---------------------------------------------------------------------------

APP_TYPES: dict[str, str] = {
    "road": N_("路跑"), "trail": N_("越野跑"), "hike": N_("登山健行"), "bike": N_("騎車"),
    "strength": N_("肌力"), "walk": N_("走路"), "other": N_("其他"),
}
BAIYUE = "baiyue"
# the filter's kinds, in the order the viewer lists them
FILTER_KINDS: dict[str, str] = {
    "trail": APP_TYPES["trail"], "road": APP_TYPES["road"], "hike": APP_TYPES["hike"],
    BAIYUE: N_("百岳登山"), "bike": APP_TYPES["bike"], "strength": APP_TYPES["strength"],
    "walk": APP_TYPES["walk"], "other": APP_TYPES["other"],
}
RUN_TYPES = ("road", "trail")

# ---------------------------------------------------------------------------
# the mapping table: platform -> platform code -> app type
# ---------------------------------------------------------------------------

COROS: dict[int, str] = {
    100: "road",        # run
    101: "road",        # indoorRun (treadmill / indoor; a road run here, as overview.category always had it)
    102: "trail",       # trailRun
    103: "road",        # trackRun
    104: "hike",        # hike
    105: "hike",        # mtnClimb (登山)
    106: "other",       # climb (outdoor climbing)
    200: "bike",        # bike
    201: "bike",        # indoorBike
    202: "bike",        # roadEbike
    203: "bike",        # gravelRoadBike
    204: "bike",        # mountainRiding
    205: "bike",        # mountainEbike
    299: "bike",        # helmetBike
    300: "other",       # poolSwim (the app has no swim type)
    301: "other",       # openWater
    400: "other",       # gymCardio (cardio, not strength)
    401: "other",       # gpsCardio
    402: "strength",    # strength (its FIT says training/strength_training, sometimes cardio_training)
    500: "other",       # ski
    501: "other",       # snowboard
    502: "other",       # xcSki
    503: "other",       # skiTouring
    700: "other",       # row
    701: "other",       # indoorRow
    702: "other",       # whitewater
    704: "other",       # flatwater
    705: "other",       # windsurfing
    706: "other",       # speedsurfing
    800: "other",       # indoorClimb
    801: "other",       # bouldering
    900: "walk",        # walk
    901: "other",       # jumpRope
    902: "other",       # climbStairs
    1200: "strength",   # Hybrid Fitness (thrive #194 treats it as strength; 推估)
    98: "other",        # customSport
    10000: "other",     # triathlon
    10001: "other",     # multiSport
    10002: "other",     # skiTouringOld
    10003: "other",     # multiPitch
}

# FIT profile enums (Garmin FIT SDK Profile.xlsx, Types: sport / sub_sport) —
# the numbers, so a raw enum value (an older parser profile) maps too.
FIT_SPORT: dict[int, str] = {
    0: "generic", 1: "running", 2: "cycling", 3: "transition", 4: "fitness_equipment", 5: "swimming",
    6: "basketball", 7: "soccer", 8: "tennis", 9: "american_football", 10: "training", 11: "walking",
    12: "cross_country_skiing", 13: "alpine_skiing", 14: "snowboarding", 15: "rowing", 16: "mountaineering",
    17: "hiking", 18: "multisport", 19: "paddling", 20: "flying", 21: "e_biking", 22: "motorcycling",
    23: "boating", 24: "driving", 25: "golf", 26: "hang_gliding", 27: "horseback_riding", 28: "hunting",
    29: "fishing", 30: "inline_skating", 31: "rock_climbing", 32: "sailing", 33: "ice_skating",
    34: "sky_diving", 35: "snowshoeing", 36: "snowmobiling", 37: "stand_up_paddleboarding", 38: "surfing",
    39: "wakeboarding", 40: "water_skiing", 41: "kayaking", 42: "rafting", 43: "windsurfing",
    44: "kitesurfing", 45: "tactical", 46: "jumpmaster", 47: "boxing", 48: "floor_climbing", 49: "baseball",
    53: "diving", 62: "hiit", 64: "racket", 65: "wheelchair_push_walk", 66: "wheelchair_push_run",
    67: "meditation", 69: "disc_golf", 71: "cricket", 72: "rugby", 73: "hockey", 74: "lacrosse",
    75: "volleyball", 76: "water_tubing", 77: "wakesurfing", 80: "mixed_martial_arts", 82: "snorkeling",
    83: "dance", 84: "jump_rope", 254: "all",
}
FIT_SUB_SPORT: dict[int, str] = {
    0: "generic", 1: "treadmill", 2: "street", 3: "trail", 4: "track", 5: "spin", 6: "indoor_cycling",
    7: "road", 8: "mountain", 9: "downhill", 10: "recumbent", 11: "cyclocross", 12: "hand_cycling",
    13: "track_cycling", 14: "indoor_rowing", 15: "elliptical", 16: "stair_climbing", 17: "lap_swimming",
    18: "open_water", 19: "flexibility_training", 20: "strength_training", 21: "warm_up", 22: "match",
    23: "exercise", 24: "challenge", 25: "indoor_skiing", 26: "cardio_training", 27: "indoor_walking",
    28: "e_bike_fitness", 29: "bmx", 30: "casual_walking", 31: "speed_walking", 43: "yoga", 44: "pilates",
    45: "indoor_running", 46: "gravel_cycling", 47: "e_bike_mountain", 48: "commuting", 49: "mixed_surface",
    58: "virtual_activity", 59: "obstacle", 62: "breathing", 67: "ultra", 68: "indoor_climbing",
    69: "bouldering", 70: "hiit", 254: "all",
}
# no information: the next source decides
FIT_NONE = {"", "generic", "all", "unknown", "invalid", "none"}

GARMIN: dict[tuple[str, Optional[str]], str] = {
    ("running", None): "road",                  # running/generic, street, track, treadmill, indoor_running,
    ("running", "trail"): "trail",              # virtual_activity, ultra, obstacle: road unless trail
    ("cycling", None): "bike",                  # road, mountain, gravel_cycling, indoor_cycling, spin, ...
    ("e_biking", None): "bike",
    ("hiking", None): "hike",
    ("mountaineering", None): "hike",
    ("walking", None): "walk",                  # casual_walking, speed_walking, indoor_walking
    # training = Garmin's gym sport. strength_training is the strength session; generic and
    # cardio_training stay strength (COROS writes cardio_training on some strength sessions:
    # 23 of 226 code-402 FITs in one account, 2026-10-06; and the FIT source always had
    # training -> strength); mind-body sessions are not strength.
    ("training", None): "strength",
    ("training", "strength_training"): "strength",
    ("training", "cardio_training"): "strength",
    ("training", "flexibility_training"): "other",
    ("training", "yoga"): "other",
    ("training", "pilates"): "other",
    ("training", "breathing"): "other",
    ("training", "hiit"): "other",
    ("fitness_equipment", None): "other",       # elliptical, stair_climbing, indoor_rowing, ...
    ("fitness_equipment", "strength_training"): "strength",
    ("swimming", None): "other",                # lap_swimming, open_water (no swim type)
    ("hiit", None): "other",
    ("rock_climbing", None): "other",
    ("floor_climbing", None): "other",
    ("snowshoeing", None): "other",
    ("cross_country_skiing", None): "other",
    ("multisport", None): "other",
    ("transition", None): "other",
}

# WKO5 / TrainingPeaks sport type names (lower-case, as the Dataset holds them)
WKO5: dict[str, str] = {
    "running": "road", "road running": "road", "treadmill running": "road", "indoor running": "road",
    "track running": "road", "trail running": "trail",
    "hiking": "hike", "mountaineering": "hike",
    "cycling": "bike", "road cycling": "bike", "mountain biking": "bike", "indoor cycling": "bike",
    "strength": "strength",
    "walking": "walk",
}
WKO5_GROUPS: dict[str, str] = {"run": "road", "bike": "bike", "road bike": "bike", "strength": "strength",
                               "walk": "walk"}
WKO5_HIKE_TAGS = {"hiking", "mountaineering"}

PLATFORMS: dict[str, dict] = {"coros": COROS, "garmin": GARMIN, "wko5": WKO5}


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------

def coros_type(code) -> Optional[str]:
    """The app type of a COROS sportType code; None when the code is not in
    the table (an unknown / custom code: the FIT decides)."""
    try:
        return COROS.get(int(code)) if code is not None else None
    except (TypeError, ValueError):
        return None


def _fit_name(v, enum: dict) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return enum.get(int(v), str(int(v)))
    s = str(v).strip().lower()
    if s.isdigit():
        return enum.get(int(s), s)
    return s.replace(" ", "_")


def fit_type(sport, sub_sport=None) -> Optional[str]:
    """The app type of a FIT sport / sub_sport (names or enum numbers); None
    when the FIT says nothing (generic / missing). A valid FIT sport the table
    does not list is "other"."""
    sp, sub = _fit_name(sport, FIT_SPORT), _fit_name(sub_sport, FIT_SUB_SPORT)
    if sp in FIT_NONE:
        return None
    t = GARMIN.get((sp, sub)) if sub and sub not in FIT_NONE else None
    if t is None:
        t = GARMIN.get((sp, None))
    if t is None and sp in FIT_SPORT.values():
        t = "other"
    return t


def type_of(platform: str, code) -> Optional[str]:
    """PLATFORMS lookup: ("coros", 102) -> "trail", ("garmin", ("running",
    "trail")) -> "trail", ("wko5", "hiking") -> "hike"; None = unmapped."""
    if platform == "coros":
        return coros_type(code)
    if platform in ("garmin", "fit"):
        sp, sub = code if isinstance(code, (tuple, list)) else (code, None)
        return fit_type(sp, sub)
    if platform == "wko5":
        return WKO5.get(str(code or "").strip().lower())
    return None


def platform_type(w) -> Optional[str]:
    """What the activity's own platform data says (the COROS code, else the
    FIT sport / sub_sport), from the workout's `platform` dict that the FIT
    dataset fills; None without one."""
    p = getattr(w, "platform", None) or {}
    t = coros_type(p.get("coros")) if p.get("coros") is not None else None
    if t is None and p.get("fit"):
        t = fit_type(*p["fit"])
    return t


def _wko5_type(w) -> str:
    """The original overview.category rules on WKO5-style names."""
    tags = set(getattr(w, "tags", None) or ())
    st = (getattr(w, "sport_type", "") or "").lower()
    sport = (getattr(w, "sport", "") or "").lower()
    if sport == "run":
        return "road"
    if tags & WKO5_HIKE_TAGS or WKO5.get(st) == "hike":
        return "hike"
    if sport in ("bike", "road bike") or "cycling" in tags or "cycling" in st:
        return "bike"
    if sport == "strength" or st == "strength":
        return "strength"
    if sport == "walk":
        return "walk"
    return "other"


def app_type(w) -> str:
    """The activity's app type (module docstring, resolution order)."""
    from backend.engine.algorithms.classify import is_trail
    if is_trail(w):
        return "trail"
    t = platform_type(w) or _wko5_type(w)
    return "road" if t == "trail" else t


# ---------------------------------------------------------------------------
# the 圖表分析 filter
# ---------------------------------------------------------------------------

def kind_of(w, user: Optional[dict] = None, baiyue_event: Optional[str] = None,
            summit: bool = False) -> str:
    """The filter kind of one activity. `user` = its stored activity tag
    (activity_tags.find), `baiyue_event` = the plan's 百岳 event that day,
    `summit` = the 成就 page's GPS detection found a 百岳 summit on its track
    (achievements.baiyue_summits).
      * the user's activity type wins: 百岳跟團 -> baiyue, 爬山 -> hike
        (a run they marked as a mountain day); any other mark keeps the sport
        and is not 百岳;
      * else a hike with a 百岳 event that day is baiyue (the auto rule of
        activity_tags.auto_type), and so is a hike that summited a 百岳 (GPS,
        owner 2026-10-06 — SP-263);
      * else the app type."""
    from backend.engine import activity_tags as AT
    ut = AT.user_type(user)
    if ut == "baiyue_group":
        return BAIYUE
    if ut == "hike":
        return "hike"
    t = app_type(w)
    if ut is None and t == "hike" and (baiyue_event or summit):
        return BAIYUE
    return t


def parse_tokens(raw: Optional[str]) -> Optional[set[str]]:
    """The viewer's `sports` query value -> a token set (None = no filter)."""
    if not raw:
        return None
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


class KindFilter:
    """The 圖表分析 filter as a workout predicate. Tokens that are filter kinds
    select by kind_of; any other token is a WKO5 sport group ("run", "swim":
    links and exports from before SP-263) and selects by w.sport."""

    def __init__(self, tokens: Iterable[str], ds=None, tag_rows: Optional[list] = None):
        toks = {str(t).strip().lower() for t in tokens if str(t).strip()}
        self.kinds = toks & set(FILTER_KINDS)
        self.groups = toks - set(FILTER_KINDS)
        self.ds = ds
        if tag_rows is None:
            from backend.engine import activity_tags as AT
            tag_rows = AT.load()
        self.rows = tag_rows
        self._baiyue: dict = {}
        self._summits: Optional[dict] = None

    def _baiyue_on(self, day) -> Optional[str]:
        if self.ds is None or BAIYUE not in self.kinds and "hike" not in self.kinds:
            return None
        if day not in self._baiyue:
            try:
                from backend.engine.racepower.athlete import baiyue_on
                self._baiyue[day] = baiyue_on(self.ds, day)
            except Exception:                   # noqa: BLE001 — no plan: no 百岳 event
                self._baiyue[day] = None
        return self._baiyue[day]

    def _summit(self, file) -> bool:
        """The 成就 page's GPS 百岳 detection (achievements.baiyue_summits), read once per filter."""
        if self.ds is None or BAIYUE not in self.kinds and "hike" not in self.kinds or not file:
            return False
        if self._summits is None:
            from backend.engine.achievements import baiyue_summits
            self._summits = baiyue_summits(self.ds)
        return file in self._summits

    def kind(self, w) -> str:
        from backend.engine import activity_tags as AT
        e = getattr(w, "entry", None)
        start = getattr(e, "start", None)
        file = getattr(e, "file", None)
        user = AT.find(self.rows, start, file) if self.rows else None
        return kind_of(w, user, self._baiyue_on(start.date()) if start is not None else None,
                       self._summit(file))

    def __call__(self, w) -> bool:
        if self.groups and (getattr(w, "sport", "") or "").lower() in self.groups:
            return True
        return bool(self.kinds) and self.kind(w) in self.kinds

    def sport_hint(self) -> Optional[str]:
        """The one WKO5 sport group every selected kind belongs to (the chart
        units read it: render_units.sport_hint), else None."""
        groups = set(self.groups)
        for k in self.kinds:
            groups.add({"road": "run", "trail": "run", "bike": "bike", "strength": "strength",
                        "walk": "walk"}.get(k, "?"))
        return next(iter(groups)) if len(groups) == 1 and "?" not in groups else None


def make_filter(raw: Optional[str], ds=None, tag_rows: Optional[list] = None) -> Optional[KindFilter]:
    """A KindFilter for the query value, None when it selects everything."""
    toks = parse_tokens(raw)
    if not toks or (set(FILTER_KINDS) <= toks):
        return None
    return KindFilter(toks, ds, tag_rows)


def tags_stamp(tag_rows: Optional[list] = None) -> str:
    """What a kind filter reads from the user's marks (the render-cache key):
    every overridden activity type."""
    if tag_rows is None:
        from backend.engine import activity_tags as AT
        tag_rows = AT.load()
    return repr(sorted((r.get("start_local") or "", r.get("file") or "", r.get("activity_type") or "")
                       for r in tag_rows or () if r.get("activity_type_overridden")))


def counts(workouts: Iterable, ds=None, tag_rows: Optional[list] = None) -> dict[str, int]:
    """{filter kind: number of activities} over `workouts`."""
    f = KindFilter(FILTER_KINDS, ds, tag_rows)
    out: dict[str, int] = {}
    for w in workouts:
        k = f.kind(w)
        out[k] = out.get(k, 0) + 1
    return out


def coverage(codes: Iterable) -> dict:
    """{"mapped": {app type: n}, "unmapped": {code: n}} of COROS codes (the
    real-data check, scripts and tests)."""
    mapped: dict[str, int] = {}
    unmapped: dict = {}
    for c in codes:
        t = coros_type(c)
        if t is None:
            unmapped[c] = unmapped.get(c, 0) + 1
        else:
            mapped[t] = mapped.get(t, 0) + 1
    return {"mapped": mapped, "unmapped": unmapped}

