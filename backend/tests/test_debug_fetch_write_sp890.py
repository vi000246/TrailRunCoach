"""SP-890: debug_fetch's write subcommands (preview / apply / undo, and write-log) over SP-802's
/write/* — one preview → apply → undo through the real API (the CLI's HTTP calls handed to the test
client), the same token header, POST with a JSON body, bad changes refused before anything is sent.
Synthetic data, in-memory DB (debug_fixtures.debug_env)."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from backend.engine import debug_write as DW
from backend.scripts import debug_fetch as F
from backend.tests.debug_fixtures import debug_env
from backend.tests.test_debug_write_sp802 import LTHR_166, _plan, _row, _writer

BASE = "https://coach.example.org"


@pytest.fixture(autouse=True)
def _fresh():
    DW.reset()
    yield
    DW.reset()


def _bridge(monkeypatch, client, calls):
    """The CLI's fetch / send answered by the app (raising HTTPError like urllib for a 4xx / 5xx)."""
    def path(url):
        u = urllib.parse.urlsplit(url)
        assert f"{u.scheme}://{u.netloc}" == BASE
        return u.path + (f"?{u.query}" if u.query else "")

    def answer(url, r):
        if r.status_code >= 400:
            raise urllib.error.HTTPError(url, r.status_code, "error", {}, io.BytesIO(r.content))
        return r.json()

    def fetch(url, hdrs, timeout=120.0):
        calls.append(("GET", url, hdrs, None))
        return answer(url, client.get(path(url), headers=hdrs))

    def send(url, hdrs, body, timeout=120.0):
        calls.append(("POST", url, hdrs, body))
        return answer(url, client.post(path(url), json=body, headers=hdrs))
    monkeypatch.setattr(F, "fetch", fetch)
    monkeypatch.setattr(F, "send", send)


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_preview_apply_undo_from_the_command_line(monkeypatch, tmp_path, capsys):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        calls: list = []
        _bridge(monkeypatch, e.c, calls)
        monkeypatch.setenv("TRC_DEBUG_URL", BASE)
        monkeypatch.setenv("TRC_DEBUG_TOKEN", tok)
        monkeypatch.delenv("TRC_PROXY_USER", raising=False)

        assert F.main(["preview", "--changes", json.dumps(LTHR_166)]) == 0
        pv = _out(capsys)
        assert pv["preview_id"] and any(d["field"] == "lthr" and d["after"] == 166.0 for d in pv["diff"])
        assert _row("2026-10-05").lthr == 155.0                               # a preview writes nothing

        assert F.main(["apply", pv["preview_id"]]) == 0
        applied = _out(capsys)
        assert applied["change_id"] and _row("2026-10-05").lthr == 166.0

        assert F.main(["write-log", "-n", "5"]) == 0
        assert _out(capsys)["writes"][0]["id"] == applied["change_id"]

        assert F.main(["undo"]) == 0
        assert _out(capsys)["undone"] == applied["change_id"] and _row("2026-10-05").lthr == 155.0

        # nothing left to undo: the server's 404 on stderr, exit 1
        assert F.main(["undo"]) == 1
        assert "404" in capsys.readouterr().err

        assert [(m, urllib.parse.urlsplit(u).path) for m, u, _h, _b in calls] == [
            ("POST", "/api/v1/debug/write/preview"), ("POST", "/api/v1/debug/write/apply"),
            ("GET", "/api/v1/debug/write/log"), ("POST", "/api/v1/debug/write/undo"),
            ("POST", "/api/v1/debug/write/undo")]
        assert calls[0][3] == {"changes": LTHR_166} and calls[1][3] == {"preview_id": pv["preview_id"]}
        assert calls[3][3] == {} and calls[2][1].endswith("?n=5")
        assert all(h["X-TRC-Debug-Token"] == tok for _m, _u, h, _b in calls)


def test_the_changes_come_from_a_file_or_stdin_and_bad_ones_are_never_sent(monkeypatch, tmp_path, capsys):
    sent = []
    monkeypatch.setattr(F, "send", lambda url, hdrs, body, timeout=120.0: sent.append((url, body)) or {"ok": 1})
    monkeypatch.setenv("TRC_DEBUG_URL", BASE)
    monkeypatch.setenv("TRC_DEBUG_TOKEN", "trcd_test")
    f = tmp_path / "changes.json"
    f.write_text(json.dumps({"changes": LTHR_166}), "utf-8")
    assert F.main(["preview", "-f", str(f)]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(LTHR_166[0])))          # one change on its own
    assert F.main(["preview", "-f", "-"]) == 0
    assert F.main(["undo", "--change-id", "7"]) == 0
    assert [b for _u, b in sent] == [{"changes": LTHR_166}, {"changes": LTHR_166}, {"change_id": 7}]
    capsys.readouterr()
    for bad in ("not json", "[]", "[1, 2]", '"lthr"'):
        assert F.main(["preview", "--changes", bad]) == 2, bad
    assert F.main(["preview", "-f", str(tmp_path / "missing.json")]) == 2
    assert len(sent) == 3                                                          # nothing more sent
    with pytest.raises(SystemExit):
        F.parser().parse_args(["preview"])                                         # --changes or -f
    with pytest.raises(SystemExit):
        F.parser().parse_args(["apply"])                                           # the preview_id
