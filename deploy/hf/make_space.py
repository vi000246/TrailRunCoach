"""
Assemble a Hugging Face Space repo for the demo (SDK: docker).

    python deploy/hf/make_space.py <space-dir>
    cd <space-dir> && git add -A && git commit -m "update demo" && git push

<space-dir> is a clone of https://huggingface.co/spaces/<user>/<space>. It gets
only what the demo image needs, from the files git tracks (so nothing
untracked — a local DB, FIT files, the owner's WKO5 views — can slip in):
backend/ (minus tests), views/, requirements.txt, plus deploy/hf/Dockerfile
and deploy/hf/README.md (the Space card with its metadata) at the top.
Everything else in <space-dir> except .git is replaced.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEEP = ("backend/", "views/", "requirements.txt")
SKIP = ("backend/tests/", "backend/scripts/validation/")


def tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p.startswith(KEEP) and not p.startswith(SKIP)]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    dst = Path(argv[0]).resolve()
    if dst == REPO or REPO in dst.parents:
        print("the Space folder must be outside this repo")
        return 2
    dst.mkdir(parents=True, exist_ok=True)
    for p in dst.iterdir():
        if p.name == ".git":
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    files = tracked()
    for rel in files:
        s, d = REPO / rel, dst / rel
        if not s.is_file():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
    shutil.copy2(REPO / "deploy/hf/Dockerfile", dst / "Dockerfile")
    shutil.copy2(REPO / "deploy/hf/README.md", dst / "README.md")
    (dst / ".dockerignore").write_text("**/__pycache__\n*.pyc\n.git\n", "utf-8")
    print(f"{len(files)} files -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
