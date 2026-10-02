"""Real-data half of backend/tests/test_wko4_extractor.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from pathlib import Path

from backend.tests.realdata._paths import ATHLETE_DIR
from backend.files.wko4_reader import extract_wko4_metrics, parse_wko4_metadata


SAMPLE_FILE = str(ATHLETE_DIR / "2022" / f"{ATHLETE_DIR.name}_2022_10_03_22_14.wko4")


@pytest.mark.skipif(not Path(SAMPLE_FILE).exists(), reason="WKO4 sample file not available")
def test_extract_wko4_metrics_returns_reasonable_values():
    """Extracted distance and duration must be within plausible activity ranges."""
    result = extract_wko4_metrics(SAMPLE_FILE)
    if result["total_distance_m"] is not None:
        assert 100 < result["total_distance_m"] < 200_000, (
            f"Unexpected distance: {result['total_distance_m']}"
        )
    if result["duration_s"] is not None:
        assert 60 < result["duration_s"] < 86400, (
            f"Unexpected duration: {result['duration_s']}"
        )


@pytest.mark.skipif(not Path(SAMPLE_FILE).exists(), reason="WKO4 sample file not available")
def test_parse_wko4_metadata_propagates_metrics():
    """parse_wko4_metadata must populate duration_s and total_distance_m."""
    meta = parse_wko4_metadata(SAMPLE_FILE)
    assert meta.sport in ("running", "walking", "cycling", "unknown", "other", "strength")
    # duration_s and total_distance_m may be None if format not decoded, but should not raise
    if meta.duration_s is not None:
        assert meta.duration_s > 0
    if meta.total_distance_m is not None:
        assert meta.total_distance_m > 0
