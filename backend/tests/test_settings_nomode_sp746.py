"""SP-746: in sign-in mode a user who cannot switch 計算模式 (html.nomode, SP-480: decided in <head> from the
inlined session before the first paint, confirmed by GET /config's can_switch) sees neither the Debug API
section nor the threshold card's 「對照資料夾裡的設定」 line; the owner's app and an admin keep both.

The page's CSS and the rule's two inputs, under node like the SP-480 check. Repo files only."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")


def _html() -> str:
    return (STATIC / "settings.html").read_text("utf-8")


def test_nomode_hides_the_debug_api_and_the_comparison_folder_line():
    css = _html()
    rule = re.search(r"html\.nomode #debugapi, html\.nomode #thr-wko5, html\.nomode #thr-more-wko5 \{ display: none !important; \}", css)
    assert rule, "the SP-746 rule"
    # the elements it names exist
    assert '<section id="debugapi">' in css and 'id="thr-wko5"' in css and 'id="thr-more-wko5"' in css
    # it is in the <head> stylesheet, read before the first paint (no flash)
    assert css.index("html.nomode #debugapi") < css.index("</head>")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_who_gets_nomode():
    head = re.search(r"<script>\n// SP-480: a user who cannot switch.*?</script>", _html(), re.S).group(0)
    code = head[len("<script>"):-len("</script>")]

    def run(session) -> bool:
        prog = ("const cls = new Set(); global.document = {documentElement: {classList: {toggle: (c, on) => on ? "
                "cls.add(c) : cls.delete(c)}}}; global.window = {TRC_SESSION: " + session + "};" + code +
                ";console.log(cls.has('nomode'))")
        out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip() == "true"
    assert run("{login: true, membership: {scopes: []}}") is True              # a member: hidden
    assert run("{login: true, membership: {scopes: ['admin']}}") is False      # an admin: as before
    assert run("undefined") is False                                            # the owner's app: as before
