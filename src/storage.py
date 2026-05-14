"""
JSON-based workout storage.

Layout:
  workouts/YYYY-MM-DD_HH-MM-SS.json  — individual workout
  index.json                          — flat list of workout summaries
  athlete.json                        — athlete profile (FTP, weight)
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(os.environ.get("WKO_DATA_DIR", Path.home() / ".wko5reverse"))
WORKOUTS_DIR = BASE_DIR / "workouts"
INDEX_FILE = BASE_DIR / "index.json"
ATHLETE_FILE = BASE_DIR / "athlete.json"

SCHEMA_VERSION = 1


def init_storage() -> None:
    """Create storage directories if they don't exist."""
    WORKOUTS_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        _write_json(INDEX_FILE, {"schema_version": SCHEMA_VERSION, "workouts": []})
    if not ATHLETE_FILE.exists():
        _write_json(ATHLETE_FILE, {
            "schema_version": SCHEMA_VERSION,
            "name": "",
            "ftp_w": None,
            "weight_kg": None,
            "updated": None,
        })


def save_workout(workout_data: dict) -> Path:
    """
    Persist a workout dict to JSON.

    workout_data must have an "id" field (ISO datetime string).
    Returns path to the saved file.
    Skips silently if already exists (idempotent import).
    """
    init_storage()
    workout_id = workout_data["id"]
    filename = _id_to_filename(workout_id)
    path = WORKOUTS_DIR / filename

    if path.exists():
        return path  # already imported

    workout_data["schema_version"] = SCHEMA_VERSION
    _write_json(path, workout_data)
    _update_index(workout_data, filename)
    return path


def load_workout(workout_id: str) -> Optional[dict]:
    """Load a single workout by ID."""
    filename = _id_to_filename(workout_id)
    path = WORKOUTS_DIR / filename
    if not path.exists():
        return None
    return _read_json(path)


def load_index() -> list[dict]:
    """Return list of workout summary dicts from index."""
    if not INDEX_FILE.exists():
        return []
    data = _read_json(INDEX_FILE)
    return data.get("workouts", [])


def load_athlete() -> dict:
    """Load athlete profile."""
    if not ATHLETE_FILE.exists():
        return {}
    return _read_json(ATHLETE_FILE)


def save_athlete(profile: dict) -> None:
    """Persist athlete profile."""
    init_storage()
    profile["updated"] = datetime.now().isoformat()
    profile["schema_version"] = SCHEMA_VERSION
    _write_json(ATHLETE_FILE, profile)


def workout_exists(workout_id: str) -> bool:
    """Check if workout is already imported."""
    return (WORKOUTS_DIR / _id_to_filename(workout_id)).exists()


# ── Internal helpers ─────────────────────────────────────────────────────────

def _id_to_filename(workout_id: str) -> str:
    """Convert ISO datetime ID to safe filename."""
    safe = workout_id.replace(":", "-").replace("T", "_").replace(" ", "_")
    return f"{safe}.json"


def _update_index(workout: dict, filename: str) -> None:
    if not INDEX_FILE.exists():
        index = {"schema_version": SCHEMA_VERSION, "workouts": []}
    else:
        index = _read_json(INDEX_FILE)

    metrics = workout.get("metrics", {})
    summary = {
        "id": workout["id"],
        "date": workout.get("date"),
        "sport": workout.get("sport", "unknown"),
        "duration_s": metrics.get("duration_s"),
        "tss": metrics.get("tss"),
        "normalized_power_w": metrics.get("normalized_power_w"),
        "avg_power_w": metrics.get("avg_power_w"),
        "file": str(WORKOUTS_DIR.name + "/" + filename),
    }

    # Avoid duplicates
    existing_ids = {w["id"] for w in index.get("workouts", [])}
    if workout["id"] not in existing_ids:
        index.setdefault("workouts", []).append(summary)
        index["workouts"].sort(key=lambda w: w.get("date") or "")
        index["last_updated"] = datetime.now().isoformat()
        _write_json(INDEX_FILE, index)


def _write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=_json_default, ensure_ascii=False)


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _json_default(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    return str(obj)
