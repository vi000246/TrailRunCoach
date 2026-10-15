"""SP-498 (with SP-494): the product no longer uses the WKO5 name where people see it.

* The charts / activities / settings API moved from /api/v1/wko5 to /api/v1/app; the old prefix
  answers 308 to the same path (method, body and query kept), so bookmarks, installed home-screen
  apps and shared links keep working.
* The chart page file is charts.html; /api/v1/static/wko5_viewer.html answers 308 to it.
* The OpenAPI title and tags are neutral.

tmp data only (the new-user smoke fixture)."""
from __future__ import annotations

from backend.tests.test_new_user_smoke import _all_routes, client  # noqa: F401  (fixture)


def test_no_mounted_route_has_the_old_prefix_except_the_redirect():
    from backend.main import OLD_API_PREFIX, API_PREFIX
    paths = {getattr(r, "path", "") for r in _all_routes()}
    old = sorted(p for p in paths if "wko5" in p.lower())
    assert old == [OLD_API_PREFIX + "/{rest:path}"], old
    assert API_PREFIX + "/viewer" in paths and API_PREFIX + "/settings" in paths
    assert API_PREFIX + "/injuries" in paths or any(p.startswith(API_PREFIX + "/injuries") for p in paths)


def test_old_prefix_redirects_308_keeping_path_and_query(client):  # noqa: F811
    r = client.get("/api/v1/wko5/activities/page?x=1&y=two", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/api/v1/app/activities/page?x=1&y=two"
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)("/api/v1/wko5/settings", follow_redirects=False)
        assert r.status_code == 308, method
        assert r.headers["location"] == "/api/v1/app/settings"


def test_old_prefix_lands_on_the_same_page(client):  # noqa: F811
    old = client.get("/api/v1/wko5/settings")
    new = client.get("/api/v1/app/settings")
    assert old.status_code == new.status_code == 200
    assert old.url.path == "/api/v1/app/settings"


def test_old_chart_page_file_redirects_to_charts_html(client):  # noqa: F811
    r = client.get("/api/v1/static/wko5_viewer.html?view=Training&dash=0", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/api/v1/static/charts.html?view=Training&dash=0"
    assert client.get("/api/v1/static/charts.html").status_code == 200


def test_openapi_title_and_tags_are_neutral(client):  # noqa: F811
    spec = client.get("/openapi.json").json()
    assert "wko" not in spec["info"]["title"].lower()
    tags = {t for op in spec["paths"].values() for v in op.values() if isinstance(v, dict) for t in v.get("tags", [])}
    assert not [t for t in tags if "wko" in t.lower()]
    assert not [p for p in spec["paths"] if "wko" in p.lower()]


# ------------------------------------------------------------------ browser storage keys

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "static"


def _run_i18n_js(stored: dict) -> dict:
    """i18n.js under node with a fake localStorage holding `stored`; returns what is stored after."""
    src = (STATIC / "i18n" / "i18n.js").read_text("utf-8")
    js = ("const m = new Map(Object.entries(%s));\n"
          "globalThis.window = globalThis;\n"
          "globalThis.document = {documentElement: {getAttribute: () => 'zh-TW', setAttribute() {}},"
          " cookie: '', addEventListener() {}, querySelectorAll: () => []};\n"
          "globalThis.localStorage = {get length() { return m.size; }, key: (i) => [...m.keys()][i] ?? null,"
          " getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),"
          " removeItem: (k) => m.delete(k)};\n"
          "%s\n"
          "console.log(JSON.stringify(Object.fromEntries(m)));\n") % (json.dumps(stored), src)
    out = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_old_storage_keys_move_once_to_the_new_names():
    after = _run_i18n_js({"wko5viewer": '{"mode":"season"}', "wko5viewer.period": "90",
                          "wko5coach.autosync.last": "123", "charts.window": "keep",
                          "wko5viewer.window": "old", "schedule.view": "month"})
    assert after == {"charts": '{"mode":"season"}', "charts.period": "90", "trc.autosync.last": "123",
                     "charts.window": "keep", "schedule.view": "month"}


def test_pages_use_the_new_storage_keys_and_globals():
    hits = []
    for p in sorted(STATIC.glob("*.html")) + sorted(STATIC.glob("*.js")):
        text = p.read_text("utf-8")
        for word in ("wko5viewer", "wko5coach.autosync", "WKO5SessionBanner", "WKO5AutoSync"):
            if word in text:
                hits.append(f"{p.name}: {word}")
    assert not hits, hits


# ------------------------------------------------------------------ what people read

import re

WKO_WORD = re.compile(r"WKO\d*(?!\w)")          # the name as a word; not identifiers like WKO5_RED (B3)


def test_catalogs_and_views_never_say_wko5():
    from backend.scripts import i18n_extract as X
    bad = [f"{loc}/{ns}.{k}" for loc, nss in X.frontend_catalogs().items() for ns, d in nss.items()
           for k, v in d.items() if WKO_WORD.search(json.dumps(v, ensure_ascii=False))]
    en = json.loads((X.LOCALES / "en.json").read_text("utf-8"))
    bad += [f"en.json: {k[:50]!r}" for k, v in en.items() if WKO_WORD.search(k) or WKO_WORD.search(str(v))]
    for stem in ("training", "periodization", "workout"):
        text = (X.VIEWS / f"{stem}.json").read_text("utf-8")
        bad += [f"views/{stem}.json: {m}" for m in WKO_WORD.findall(text)]
    side = json.loads((X.VIEWS / "i18n" / "en.json").read_text("utf-8"))
    side.pop("_about", None)
    bad += [f"views/i18n/en.json: {m}" for m in WKO_WORD.findall(json.dumps(side, ensure_ascii=False))]
    assert bad == []


def test_backend_ui_strings_never_say_wko5():
    """Every _() / N_() msgid and every Chinese UI string not wrapped yet (Python, HTML, JS)."""
    from backend.scripts import i18n_extract as X
    scans = X.scan_repo()
    bad = [f"{p}:{ln}: {m[:60]!r}" for p, fs in scans.items() for ln, m in list(fs.msgids) + list(fs.unwrapped)
           if WKO_WORD.search(m) and "WKO5COACH_" not in m]
    assert bad == []


def test_pages_and_scripts_never_say_wko5():
    """The shipped pages and scripts (text, attributes, comments): the old name only as part of an
    internal identifier (WKO5_RED …) or a storage key moved by i18n.js."""
    hits = [f"{p.relative_to(STATIC)}: {m}" for p in sorted(STATIC.rglob("*")) if p.suffix in (".html", ".js")
            for m in WKO_WORD.findall(p.read_text("utf-8"))]
    assert hits == []


# ------------------------------------------------------------------ environment and data folder (B2)

def test_new_env_names_set_the_old_ones_and_win():
    from backend import env_names as EN
    env = {"TRC_HOME": "/new", "WKO5COACH_HOME": "/old", "TRC_ATHLETE_DIR": "/cmp",
           "TRC_SECRET_KEY_FILE": "/run/secrets/k", "TRC_DEBUG_PIN": "1234", "WKO5COACH_TZ": "Asia/Taipei"}
    old_only = EN.apply(env)
    assert env["WKO5COACH_HOME"] == "/new"                                 # the new name is read first
    assert env["WKO5_ATHLETE_DIR"] == env["WKO5COACH_ATHLETE_DIR"] == "/cmp"
    assert env["WKO5COACH_SECRET_KEY_FILE"] == "/run/secrets/k"
    assert "TRC_HOME" not in env and "TRC_ATHLETE_DIR" not in env           # only the names the code reads
    assert env["TRC_DEBUG_PIN"] == "1234" and "WKO5COACH_DEBUG_PIN" not in env   # a TRC_ name of its own
    assert env["WKO5COACH_TZ"] == "Asia/Taipei" and old_only == ["WKO5COACH_TZ"]


def test_a_child_environment_copied_from_this_one_keeps_its_override():
    """backend/demo/build.py child_env: a copy of os.environ with WKO5COACH_HOME overridden. The parent
    took TRC_HOME out after copying it, so the child's override is not undone by the parent's value."""
    from backend import env_names as EN
    parent = {"TRC_HOME": "/owner"}
    EN.apply(parent)
    child = {**parent, "WKO5COACH_HOME": "/demo"}
    EN.apply(child)
    assert child["WKO5COACH_HOME"] == "/demo"


def test_every_old_env_name_the_code_reads_has_a_new_one():
    """A WKO5COACH_* name added later (another branch) must be listed in env_names.OLD, or setting its
    TRC_* name would do nothing."""
    from backend import env_names as EN
    root = Path(__file__).resolve().parents[1]
    names = set()
    for p in root.rglob("*.py"):
        if "tests" in p.relative_to(root).parts:
            continue
        names |= set(re.findall(r"\bWKO5COACH_[A-Z0-9_]*[A-Z0-9]\b", p.read_text("utf-8")))
    missing = [n for n in names if n not in EN.OLD and not (n.endswith("_FILE") and n[:-5] in EN.OLD)]
    assert sorted(missing) == []


def test_data_folder_defaults_to_the_new_name_and_keeps_an_old_one(tmp_path, monkeypatch):
    from backend import tenancy
    monkeypatch.delenv(tenancy.ENV_HOME, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert tenancy.home_root() == tmp_path / ".trailruncoach"            # a new install
    (tmp_path / ".wko5coach").mkdir()
    assert tenancy.home_root() == tmp_path / ".wko5coach"                # only the old one: used in place
    assert (tmp_path / ".wko5coach").is_dir() and not (tmp_path / ".trailruncoach").exists()   # not moved
    (tmp_path / ".trailruncoach").mkdir()
    assert tenancy.home_root() == tmp_path / ".trailruncoach"            # both: the new one
    monkeypatch.setenv(tenancy.ENV_HOME, str(tmp_path / "elsewhere"))
    assert tenancy.home_root() == tmp_path / "elsewhere"


def test_docs_and_deploy_files_use_the_new_names():
    repo = Path(__file__).resolve().parents[2]
    for rel in (".env.example", "docker-compose.yml", "deploy/hf/Dockerfile", "README.md"):
        text = (repo / rel).read_text("utf-8")
        lines = [ln for ln in text.splitlines() if re.search(r"\bWKO5COACH_[A-Z]", ln)
                 and "Before SP-498" not in ln and "舊名" not in ln]
        assert lines == [], (rel, lines)
    compose = (repo / "docker-compose.yml").read_text("utf-8")
    assert "container_name: trailruncoach" in compose and "TRC_DATA_FOLDER:-.trailruncoach" in compose
