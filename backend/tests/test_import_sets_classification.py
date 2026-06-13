import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.db.models import WorkoutFile
from backend.files.file_service import _apply_classification


def _wf(**kw):
    base = dict(athlete_id=1, file_path="/x.fit", file_format="fit")
    base.update(kw)
    return WorkoutFile(**base)


def test_apply_sets_trail_for_high_climb():
    wf = _wf(sport="running", total_distance_m=10000, elevation_gain_m=600)
    _apply_classification(wf)
    assert wf.trail_classification == "trail"


def test_apply_sets_road_for_low_climb():
    wf = _wf(sport="running", total_distance_m=10000, elevation_gain_m=30)
    _apply_classification(wf)
    assert wf.trail_classification == "road"


def test_apply_respects_override():
    wf = _wf(sport="running", total_distance_m=10000, elevation_gain_m=600,
             trail_classification="road", classification_overridden=True)
    _apply_classification(wf)
    # overridden → not recomputed
    assert wf.trail_classification == "road"


def test_apply_non_running_unknown():
    wf = _wf(sport="cycling", total_distance_m=40000, elevation_gain_m=800)
    _apply_classification(wf)
    assert wf.trail_classification == "unknown"
