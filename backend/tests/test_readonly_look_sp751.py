"""SP-751: read-only inputs look read-only (grey, dashed) on every page with the app's shell, light and dark (the
page's own --muted / --panel), and the 課表 dialog's own `.locked` 分鐘 look stays. The race editor's total
distance / climb are the read-only ones the ticket names (races.html: readOnly while each day's rows are filled).
Repo files only; the picture is in the ticket's screenshots."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _shell_css() -> str:
    js = (STATIC / "shell.js").read_text("utf-8")
    return re.search(r"const CSS = `(.*?)`;", js, re.S).group(1)


def test_shell_css_greys_read_only_fields_but_not_locked_ones():
    css = _shell_css()
    rule = re.search(r"input\[readonly\]:not\(\.locked\):not\(\[type=checkbox\]\):not\(\[type=radio\]\), "
                     r"textarea\[readonly\]:not\(\.locked\) \{([^}]*)\}", css)
    assert rule, "the SP-751 rule"
    body = rule.group(1)
    assert "background-color: color-mix(in srgb, var(--muted, #6a7482) 16%, var(--panel, #fff)) !important" in body
    assert "border-style: dashed !important" in body
    # built from the page's colour variables, so a dark theme greys it on its own dark panel
    for page in ("races.html", "schedule.html"):
        html = (STATIC / page).read_text("utf-8")
        assert "--muted" in html and "--panel" in html and "prefers-color-scheme: dark" in html, page


def test_the_named_fields_are_the_read_only_ones():
    races = (STATIC / "races.html").read_text("utf-8")
    assert "f.distance_km.readOnly = f.climbing_m.readOnly" in races
    sched = (STATIC / "schedule.html").read_text("utf-8")
    assert "dialog.sd input.locked {" in sched                      # its own look, excluded above
