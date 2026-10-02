import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.files.wko4_reader import extract_wko4_metrics


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
