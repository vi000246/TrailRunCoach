"""The production image must contain every repo-root folder the backend reads at
runtime. The Dockerfile once copied only backend/, so /app/views was missing and
/api/v1/wko5/views returned [] in production. Reads only repo files."""
import fnmatch
from pathlib import Path

from backend.engine.wko5expr.customviews import REPO_VIEWS

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = [ROOT / "Dockerfile", ROOT / "deploy" / "hf" / "Dockerfile"]
# repo-root folders read at runtime (outside backend/)
RUNTIME_DIRS = ["backend", "views"]


def _copied(dockerfile: Path) -> set[str]:
    """The source paths of every COPY in the Dockerfile, without a trailing slash."""
    out = set()
    for line in dockerfile.read_text("utf-8").splitlines():
        parts = line.split()
        if not parts or parts[0].upper() != "COPY":
            continue
        args = [a for a in parts[1:] if not a.startswith("--")]
        out.update(a.rstrip("/").removeprefix("./") for a in args[:-1])
    return out


def test_runtime_dirs_are_copied_into_every_image():
    for df in DOCKERFILES:
        missing = [d for d in RUNTIME_DIRS if d not in _copied(df)]
        assert not missing, f"{df.relative_to(ROOT)} does not COPY {missing}"


def test_repo_views_is_a_repo_root_folder():
    # REPO_VIEWS resolves to <repo>/views, i.e. /app/views in the image
    assert REPO_VIEWS == ROOT / "views"
    assert (REPO_VIEWS / "training.json").is_file()


def test_dockerignore_keeps_the_views():
    patterns = [p.strip() for p in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
                if p.strip() and not p.startswith("#")]
    for p in sorted(REPO_VIEWS.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        parts = rel.split("/")
        # Docker matches a pattern against the path from the context root (and so,
        # implicitly, every parent folder of it); `**/` also matches at any depth.
        prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        for pat in patterns:
            pat = pat.lstrip("/")
            hit = any(fnmatch.fnmatchcase(x, pat) or
                      (pat.startswith("**/") and fnmatch.fnmatchcase(x.split("/")[-1], pat[3:]))
                      for x in prefixes)
            assert not hit, f".dockerignore pattern {pat!r} drops {rel}"
