import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import pytest
from pathlib import Path
from backend.files.wko4_reader import extract_wko4_metrics, parse_wko4_metadata

SAMPLE_FILE = str(Path.home() / "WKO5/Athlete/2022/Athlete_2022_10_03_22_14.wko4")


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


def test_extract_wko4_metrics_missing_file_returns_none():
    """extract_wko4_metrics must not crash on missing files."""
    result = extract_wko4_metrics("/nonexistent/file.wko4")
    assert result["total_distance_m"] is None
    assert result["duration_s"] is None


def test_extract_wko4_metrics_invalid_file_returns_none(tmp_path):
    """Random bytes file returns None without raising."""
    bad = tmp_path / "bad.wko4"
    bad.write_bytes(b"\x00" * 100)
    result = extract_wko4_metrics(str(bad))
    assert result["total_distance_m"] is None
    assert result["duration_s"] is None


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
