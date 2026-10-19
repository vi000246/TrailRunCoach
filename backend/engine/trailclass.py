"""The one set of OSM path rules shared by the trail dictionary and the app (SP-709).

Pure functions, standard library only: tools/trail-dict/build.py runs them once per way when it
builds the dictionary, and the app (TrailRunCoach) can import the same module, so the two sites
never classify on their own again. inReachLiveTrack reads the classes the dictionary carries.

Three answers per OSM way, from its tags:

* ``line_class`` -- the line Rudy's map (alpha-rudy/taiwan-topo, MOI_OSM.xml / process_osm.py)
  draws: trail (thin red dash), hard (艱難路線), steps (石階), forest_road (林道), closed (已封閉),
  road, other. Membership of a named ``route=hiking`` relation (the thick red line) is a separate
  flag, because Rudy draws it over whatever the way is (SP-701: it says nothing about difficulty).
* ``surface_class`` -- paved / gravel / dirt / steps / unknown. Cairn's classifySurface as
  TrailRunCoach (racepower/surface.py, SP-543/SP-531) had it, with one fix: ``surface=unpaved`` on a
  path-like way is dirt, not gravel (on a track or a road it stays gravel).
* ``risk_flags`` -- shown to the user as a trail-condition warning, never turned into a speed
  factor (SP-716: closed / abandoned / sac_scale >= T4 / trail_visibility bad or worse).

Rules v2 (SP-735): a path / footway / bridleway with nothing else to go on (no surface, no sac_scale
T2+, no rough smoothness) is dirt when it is not in town, and stays unknown in town. "In town" is
read from the dictionary's own roads (TownGrid: road length per km2 around the way), so a rebuild
from the kept tags and coordinates needs no other data. Without a place (``town=None``) the answer
is v1's: unknown.

Bump RULES_VERSION whenever any answer for some set of tags changes: it goes into the dictionary's
manifest, so a reader knows which rules the stored classes came from.
"""
from __future__ import annotations

import math

RULES_VERSION = 2

# ---------------------------------------------------------------------------- vocabularies

LINE_CLASSES = ("trail", "hard", "steps", "forest_road", "closed", "road", "other")
SURFACE_CLASSES = ("paved", "gravel", "dirt", "steps", "unknown")
RISK_FLAGS = ("closed", "abandoned", "sac_t4plus", "poor_visibility")

# The raw tags kept next to the classes, so changed rules can be re-run without the source file.
KEPT_TAGS = ("highway", "surface", "sac_scale", "trail_visibility", "tracktype", "smoothness",
             "access", "foot", "name", "ref", "abandoned", "disused", "abandoned:highway",
             "disused:highway", "footway")

# ---------------------------------------------------------------------------- tag groups

PATH_HIGHWAYS = frozenset({"path", "footway", "bridleway"})
# laid paths for bikes / walkers in town: drawn and treated like roads
TOWN_PATH_HIGHWAYS = frozenset({"cycleway", "pedestrian"})
ROAD_HIGHWAYS = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary", "residential",
                           "unclassified", "road", "service", "living_street",
                           "motorway_link", "trunk_link", "primary_link", "secondary_link",
                           "tertiary_link", "busway"})
ABANDONED_HIGHWAYS = frozenset({"abandoned", "disused"})

PAVED_SURFACES = frozenset({"asphalt", "concrete", "paved", "sett", "cobblestone",
                            "concrete:plates", "concrete:lanes", "paving_stones", "unhewn_cobblestone",
                            "brick", "bricks", "wood", "metal", "metal_grid", "rubber", "tartan",
                            "artificial_turf", "acrylic", "grass_paver"})
GRAVEL_SURFACES = frozenset({"gravel", "fine_gravel", "compacted", "pebblestone", "unpaved"})
DIRT_SURFACES = frozenset({"dirt", "ground", "soil", "earth", "mud", "sand", "grass", "rock", "bare_rock",
                           "woodchips"})
SAC_DIRT = frozenset({"mountain_hiking", "demanding_mountain_hiking", "alpine_hiking",
                      "demanding_alpine_hiking", "difficult_alpine_hiking"})
SMOOTH_DIRT = frozenset({"very_bad", "horrible", "very_horrible", "impassable"})

SAC_T4PLUS = frozenset({"alpine_hiking", "demanding_alpine_hiking", "difficult_alpine_hiking"})
SAC_T5PLUS = frozenset({"demanding_alpine_hiking", "difficult_alpine_hiking"})
POOR_VISIBILITY = frozenset({"bad", "horrible", "no"})

_YES = frozenset({"yes", "true", "1"})


def _t(tags: dict | None, key: str) -> str:
    return ((tags or {}).get(key) or "").strip()


def effective_highway(tags: dict | None) -> str:
    """``highway``, or the old class of an abandoned / disused way (``abandoned:highway=path``)."""
    hw = _t(tags, "highway")
    if hw and hw not in ABANDONED_HIGHWAYS:
        return hw
    return _t(tags, "abandoned:highway") or _t(tags, "disused:highway") or hw


def is_path_like(tags: dict | None) -> bool:
    return effective_highway(tags) in PATH_HIGHWAYS


# highway values that are not something you walk or run on (yet / any more / at all)
NOT_A_WAY = frozenset({"proposed", "construction", "planned", "razed", "no", "platform", "bus_stop",
                       "elevator", "rest_area", "services", "emergency_bay", "raceway", "bus_guideway",
                       "escape", "turning_circle", "traffic_island"})


def in_dictionary(tags: dict | None) -> bool:
    """Does this way go into the trail dictionary? Lines you can be on: highway=* (or an abandoned
    way that still has its old class), not plazas mapped as areas, not plans or platforms."""
    hw = effective_highway(tags)
    if not hw or hw in NOT_A_WAY or hw in ABANDONED_HIGHWAYS:
        return False
    if _t(tags, "area") in _YES:
        return False
    return True


# ---------------------------------------------------------------------------- the three answers

def risk_flags(tags: dict | None) -> list[str]:
    """Trail-condition warnings, in RISK_FLAGS order (empty = nothing to warn about)."""
    out = []
    if _t(tags, "access") == "no":
        out.append("closed")
    hw = _t(tags, "highway")
    if (hw in ABANDONED_HIGHWAYS or _t(tags, "abandoned") in _YES or _t(tags, "disused") in _YES
            or (not hw and (_t(tags, "abandoned:highway") or _t(tags, "disused:highway")))):
        out.append("abandoned")
    if _t(tags, "sac_scale") in SAC_T4PLUS:
        out.append("sac_t4plus")
    if _t(tags, "trail_visibility") in POOR_VISIBILITY:
        out.append("poor_visibility")
    return out


def line_class(tags: dict | None) -> str:
    """The line Rudy's map draws for this way (LINE_CLASSES)."""
    if _t(tags, "access") == "no" or "abandoned" in risk_flags(tags):
        return "closed"                                  # grey 已封閉 (abandoned: 推估, same grey)
    hw = effective_highway(tags)
    if hw == "steps":
        return "steps"
    if hw == "via_ferrata":
        return "hard"
    if hw in PATH_HIGHWAYS:
        if (_t(tags, "trail_visibility") in POOR_VISIBILITY
                or _t(tags, "sac_scale") in SAC_T5PLUS):
            return "hard"
        return "trail"
    if hw == "track":
        return "forest_road"
    if hw in ROAD_HIGHWAYS or hw in TOWN_PATH_HIGHWAYS:
        return "road"
    return "other"


def surface_class(tags: dict | None, town: bool | None = None) -> str:
    """paved / gravel / dirt / steps / unknown -- the one surface rule for both sites.

    ``town``: is the way in town (TownGrid.is_town)? Only decides a path with nothing else to go on
    (rules v2): False -> dirt, True or None (place not known) -> unknown."""
    hw = effective_highway(tags)
    sf = _t(tags, "surface")
    if sf in PAVED_SURFACES:                     # an explicit surface beats the highway class
        return "paved"
    if hw == "steps":
        return "steps"
    if sf == "unpaved" and hw in PATH_HIGHWAYS:  # a mountain path tagged just "unpaved" is a dirt trail
        return "dirt"
    if sf in GRAVEL_SURFACES:
        return "gravel"
    if sf in DIRT_SURFACES:
        return "dirt"
    if _t(tags, "sac_scale") in SAC_DIRT:
        return "dirt"
    if hw in ROAD_HIGHWAYS:
        return "paved"
    if _t(tags, "smoothness") in SMOOTH_DIRT:
        return "dirt"
    if hw == "track":
        return "gravel"
    if town is False and needs_place(tags):
        return "dirt"                            # rules v2: an untagged path out of town (推估)
    return "unknown"                             # path / footway / … without a surface, or not a way


# ---------------------------------------------------------------------------- place (rules v2, SP-735)

# footway=* values that follow a street: laid like the street, never a dirt trail
STREET_FOOTWAYS = frozenset({"sidewalk", "crossing", "traffic_island"})


def needs_place(tags: dict | None) -> bool:
    """Is this a way whose surface only the place can decide? A path / footway / bridleway that the
    tags alone leave unknown (no surface, no sac_scale T2+, no rough smoothness), and not a sidewalk
    or crossing."""
    if not is_path_like(tags) or _t(tags, "footway") in STREET_FOOTWAYS:
        return False
    return surface_class(tags) == "unknown"


# The roads that make a place town: streets, lanes, driveways, town paths. Not motorways / trunk roads
# (they cross the mountains too) and not tracks (forest roads, farm tracks).
TOWN_ROADS = (ROAD_HIGHWAYS - {"motorway", "motorway_link", "trunk", "trunk_link", "busway"}) | TOWN_PATH_HIGHWAYS
TOWN_CELL_DEG = 0.003            # grid cell: about 330 m north-south, 300 m east-west in Taiwan
TOWN_ROAD_KM_PER_KM2 = 8.0       # town: this much road per km2 in the 3 x 3 cells around a point (推估)
TOWN_SHARE = 0.5                 # a way is in town when at least this share of its length is
_KM_PER_DEG = 111.32


def _seg_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    dx = (b[0] - a[0]) * math.cos(lat) * _KM_PER_DEG
    dy = (b[1] - a[1]) * _KM_PER_DEG
    return math.hypot(dx, dy)


class TownGrid:
    """Road length per grid cell, from the dictionary's own ways; then "is this way in town?".

    Feed every way (add), then ask (density, is_town). A point is in town when the TOWN_ROADS in its
    3 x 3 cells (about 1 km x 1 km) come to TOWN_ROAD_KM_PER_KM2 or more; a way is in town when at
    least TOWN_SHARE of its length is."""

    def __init__(self, cell_deg: float = TOWN_CELL_DEG, threshold: float = TOWN_ROAD_KM_PER_KM2,
                 share: float = TOWN_SHARE):
        self.cell = cell_deg
        self.threshold = threshold
        self.share = share
        self.km: dict[tuple[int, int], float] = {}

    def _key(self, lon: float, lat: float) -> tuple[int, int]:
        return math.floor(lon / self.cell), math.floor(lat / self.cell)

    def add(self, tags: dict | None, coords) -> None:
        """Count a way's length into the grid when it is a town road (anything else is ignored)."""
        if _t(tags, "highway") not in TOWN_ROADS or _t(tags, "area") in _YES:
            return
        step = self.cell * _KM_PER_DEG / 2           # long segments: spread over the cells they cross
        for a, b in zip(coords, coords[1:]):
            km = _seg_km(a, b)
            n = max(1, math.ceil(km / step))
            for i in range(n):
                f = (i + 0.5) / n
                k = self._key(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                self.km[k] = self.km.get(k, 0.0) + km / n

    def density(self, lon: float, lat: float) -> float:
        """Town-road km per km2 in the 3 x 3 cells around the point."""
        x, y = self._key(lon, lat)
        km = sum(self.km.get((x + i, y + j), 0.0) for i in (-1, 0, 1) for j in (-1, 0, 1))
        side = self.cell * _KM_PER_DEG
        area = 9 * side * side * math.cos(math.radians(lat))
        return km / area

    def is_town(self, coords) -> bool:
        """At least `share` of the way's length lies where the density reaches the threshold."""
        total = town = 0.0
        for a, b in zip(coords, coords[1:]):
            km = _seg_km(a, b) or 1e-9
            total += km
            if self.density((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) >= self.threshold:
                town += km
        if total <= 0:
            return bool(coords) and self.density(coords[0][0], coords[0][1]) >= self.threshold
        return town >= self.share * total


def kept_tags(tags: dict | None) -> dict:
    return {k: tags[k] for k in KEPT_TAGS if tags and tags.get(k)}


def classify(tags: dict | None, town: bool | None = None) -> dict:
    """All three answers at once: {"line", "surface", "risk"}. `town`: see surface_class."""
    return {"line": line_class(tags), "surface": surface_class(tags, town), "risk": risk_flags(tags)}
