"""The shared OSM path rules (SP-709): Rudy line class, surface class, risk flags -- synthetic tags."""
import pytest

from backend.engine import trailclass as tc


# ---------------------------------------------------------------------------- Rudy line class

@pytest.mark.parametrize("tags,want", [
    ({"highway": "path"}, "trail"),
    ({"highway": "footway", "sac_scale": "demanding_mountain_hiking"}, "trail"),   # T3 is still the thin dash
    ({"highway": "path", "sac_scale": "alpine_hiking"}, "trail"),                  # T4 too (MOI_OSM.xml)
    ({"highway": "path", "sac_scale": "demanding_alpine_hiking"}, "hard"),         # T5
    ({"highway": "path", "sac_scale": "difficult_alpine_hiking"}, "hard"),         # T6
    ({"highway": "path", "trail_visibility": "bad"}, "hard"),
    ({"highway": "path", "trail_visibility": "horrible"}, "hard"),
    ({"highway": "path", "trail_visibility": "no"}, "hard"),
    ({"highway": "path", "trail_visibility": "intermediate"}, "trail"),
    ({"highway": "steps"}, "steps"),
    ({"highway": "track"}, "forest_road"),
    ({"highway": "track", "tracktype": "grade1"}, "forest_road"),
    ({"highway": "residential"}, "road"),
    ({"highway": "service"}, "road"),
    ({"highway": "cycleway"}, "road"),
    ({"highway": "via_ferrata"}, "hard"),
    ({"highway": "corridor"}, "other"),
    ({"highway": "path", "access": "no"}, "closed"),
    ({"highway": "steps", "access": "no"}, "closed"),                              # closed beats the shape
    ({"abandoned:highway": "path"}, "closed"),
    ({"highway": "path", "abandoned": "yes"}, "closed"),
    ({}, "other"),
    (None, "other"),
])
def test_line_class(tags, want):
    assert tc.line_class(tags) == want


def test_hiking_route_membership_is_not_a_line_class():
    # the thick red line is relation membership (build.py), the way's own class stays what it is
    assert "hiking_route" not in tc.LINE_CLASSES
    assert tc.line_class({"highway": "path", "route": "hiking"}) == "trail"


# ---------------------------------------------------------------------------- surface class

@pytest.mark.parametrize("tags,want", [
    ({"highway": "path", "surface": "unpaved"}, "dirt"),          # the SP-709 fix: a trail, not gravel
    ({"highway": "footway", "surface": "unpaved"}, "dirt"),
    ({"highway": "bridleway", "surface": "unpaved"}, "dirt"),
    ({"highway": "track", "surface": "unpaved"}, "gravel"),       # on a track or a road it stays gravel
    ({"highway": "service", "surface": "unpaved"}, "gravel"),
    ({"highway": "path", "surface": "asphalt"}, "paved"),
    ({"highway": "footway", "surface": "paving_stones"}, "paved"),
    ({"highway": "footway", "surface": "wood"}, "paved"),         # 棧道
    ({"highway": "steps"}, "steps"),
    ({"highway": "steps", "surface": "concrete"}, "paved"),       # an explicit surface wins
    ({"highway": "steps", "surface": "ground"}, "steps"),
    ({"highway": "path", "surface": "gravel"}, "gravel"),
    ({"highway": "path", "surface": "ground"}, "dirt"),
    ({"highway": "path", "surface": "rock"}, "dirt"),
    ({"highway": "path", "sac_scale": "mountain_hiking"}, "dirt"),
    ({"highway": "path", "sac_scale": "hiking"}, "unknown"),      # T1 can be a paved park path
    ({"highway": "path"}, "unknown"),
    ({"highway": "footway"}, "unknown"),
    ({"highway": "primary"}, "paved"),
    ({"highway": "track"}, "gravel"),
    ({"highway": "track", "smoothness": "very_bad"}, "dirt"),
    ({"abandoned:highway": "path", "surface": "unpaved"}, "dirt"),
    (None, "unknown"),
])
def test_surface_class(tags, want):
    assert tc.surface_class(tags) == want


# ---------------------------------------------------------------------------- risk flags

@pytest.mark.parametrize("tags,want", [
    ({"highway": "path"}, []),
    ({"highway": "path", "access": "no"}, ["closed"]),
    ({"highway": "path", "access": "private"}, []),
    ({"abandoned:highway": "path"}, ["abandoned"]),
    ({"highway": "path", "disused": "yes"}, ["abandoned"]),
    ({"highway": "path", "sac_scale": "demanding_mountain_hiking"}, []),            # T3: no flag
    ({"highway": "path", "sac_scale": "alpine_hiking"}, ["sac_t4plus"]),            # T4
    ({"highway": "path", "sac_scale": "difficult_alpine_hiking"}, ["sac_t4plus"]),  # T6
    ({"highway": "path", "trail_visibility": "intermediate"}, []),
    ({"highway": "path", "trail_visibility": "bad"}, ["poor_visibility"]),
    ({"highway": "path", "access": "no", "sac_scale": "alpine_hiking", "trail_visibility": "no"},
     ["closed", "sac_t4plus", "poor_visibility"]),
])
def test_risk_flags(tags, want):
    assert tc.risk_flags(tags) == want


def test_ct103_like_way_is_closed_with_a_warning():
    # SP-716: 北插天山 CT103, OSM access=no, sac_scale T3
    c = tc.classify({"highway": "path", "access": "no", "sac_scale": "demanding_mountain_hiking",
                     "name": "CT103"})
    assert c == {"line": "closed", "surface": "dirt", "risk": ["closed"]}


# ---------------------------------------------------------------------------- what goes in

@pytest.mark.parametrize("tags,want", [
    ({"highway": "path"}, True),
    ({"highway": "residential"}, True),
    ({"abandoned:highway": "path"}, True),
    ({"highway": "abandoned"}, False),              # no old class left: nothing to draw
    ({"highway": "proposed"}, False),
    ({"highway": "construction"}, False),
    ({"highway": "platform"}, False),
    ({"highway": "pedestrian", "area": "yes"}, False),
    ({"building": "yes"}, False),
    (None, False),
])
def test_in_dictionary(tags, want):
    assert tc.in_dictionary(tags) is want


def test_kept_tags_only_keeps_the_listed_keys():
    tags = {"highway": "path", "surface": "ground", "name": "CT103", "source": "survey", "foo": "bar"}
    assert tc.kept_tags(tags) == {"highway": "path", "surface": "ground", "name": "CT103"}


def test_every_answer_is_in_its_vocabulary():
    samples = [{"highway": h, "surface": s, "sac_scale": sc, "trail_visibility": v, "access": a}
               for h in ("path", "steps", "track", "primary", "corridor")
               for s in ("", "unpaved", "asphalt", "ground")
               for sc in ("", "hiking", "alpine_hiking", "difficult_alpine_hiking")
               for v in ("", "bad")
               for a in ("", "no")]
    for t in samples:
        c = tc.classify(t)
        assert c["line"] in tc.LINE_CLASSES
        assert c["surface"] in tc.SURFACE_CLASSES
        assert set(c["risk"]) <= set(tc.RISK_FLAGS)


def test_rules_version_is_an_int():
    assert isinstance(tc.RULES_VERSION, int) and tc.RULES_VERSION >= 1
