"""Every static script parses (node --check). A syntax error stops a whole file silently in the
browser: feasibility.js declared `const src` twice in one function from SP-292 on, and the
賽事周期 page lost its feasibility card without any failing unit test. Skipped without node."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _check(path: Path) -> str:
    r = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    return "" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["?"])[-1]


@pytest.mark.parametrize("js", sorted(p.name for p in STATIC.glob("*.js")))
def test_static_script_parses(js):
    assert _check(STATIC / js) == ""


def test_inline_page_scripts_parse(tmp_path):
    bad = []
    for page in sorted(STATIC.glob("*.html")):
        html = page.read_text("utf-8")
        for i, body in enumerate(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)):
            f = tmp_path / f"{page.stem}_{i}.js"
            f.write_text(body, "utf-8")
            err = _check(f)
            if err:
                bad.append(f"{page.name} block {i}: {err}")
    assert not bad, bad
