"""The module specs live in the Obsidian vault (OB-441, 2026-11-13: my specs all moved out of the repo's
docs/spec). A test that checks code against a spec gets the file here; a machine without the vault
(CI, the public mirror) skips that test.

The vault: OBSIDIAN_VAULT, else <real home>/Projects/Obsidian/MainRepo. The tests fake home
(_guard.py), so the real one comes from _guard.REAL_HOME."""
import os
from pathlib import Path

import pytest

from backend.tests import _guard

SPEC_DIR = Path("650 Projects") / "越野跑教練" / "spec"


def vault_spec(name: str) -> Path:
    roots = [os.environ.get("OBSIDIAN_VAULT", "")]
    roots += [str(_guard.REAL_HOME / p / "Obsidian" / "MainRepo") for p in ("Projects", "projects")]
    for r in roots:
        if r and (Path(r) / SPEC_DIR / name).is_file():
            return Path(r) / SPEC_DIR / name
    pytest.skip(f"the spec is in the Obsidian vault ({SPEC_DIR.as_posix()}/{name}); no vault on this machine")
