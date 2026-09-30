"""backend/settings/paths.py: where the WKO5 athlete folder is."""
from pathlib import Path

from backend.settings import paths


def _athlete(d: Path) -> Path:
    d.mkdir(parents=True)
    (d / "Someone.wko5athlete").write_bytes(b"")
    return d


def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("WKO5_ATHLETE_DIR", str(tmp_path / "x"))
    assert paths.athlete_dir() == tmp_path / "x"
    monkeypatch.delenv("WKO5_ATHLETE_DIR")
    monkeypatch.setenv("WKO5COACH_ATHLETE_DIR", str(tmp_path / "y"))
    assert paths.athlete_dir() == tmp_path / "y"


def test_found_under_a_default_root(monkeypatch, tmp_path):
    for v in paths.ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    a, b = tmp_path / "WKO5", tmp_path / "Projects" / "TrailRunCoach" / "WKO5"
    (b / "Views").mkdir(parents=True)                         # WKO5's library: only one folder is an athlete
    ath = _athlete(b / "Athlete")
    monkeypatch.setattr(paths, "default_roots", lambda: [a, b])
    assert paths.athlete_dir() == ath
    direct = _athlete(a)                                      # a root that is itself the athlete folder
    assert paths.athlete_dir() == direct


def test_fallback_is_not_machine_specific(monkeypatch, tmp_path):
    for v in paths.ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(paths, "default_roots", lambda: [tmp_path / "WKO5", tmp_path / "nope"])
    assert paths.athlete_dir() == tmp_path / "WKO5"
    src = Path(paths.__file__).read_text(encoding="utf-8")
    for f in ("api/wko5views.py", "api/achievements.py", "api/plan.py"):
        text = (Path(paths.__file__).resolve().parents[1] / f).read_text(encoding="utf-8")
        assert "user" not in text and "athlete_dir()" in text
    assert "user" not in src
