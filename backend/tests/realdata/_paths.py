"""Where the real-data suite finds the athlete's data. backend/tests/conftest.py
sets both (home itself is a temp folder during tests)."""
import os
from pathlib import Path

# the WKO5 athlete folder (<Name>.wko5athlete + year/*.wko4): read only
ATHLETE_DIR = Path(os.environ.get("WKO5_ATHLETE_DIR") or "__no_athlete_dir__")
# the real home, for the one check that reads ~/.wko5coach (the TP secret leak guard)
REAL_HOME = Path(os.environ.get("WKO5COACH_TEST_REAL_HOME") or "__no_real_home__")
