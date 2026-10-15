"""SP-387 第 5 條: the server's secrets come from the environment (or a Docker secret file named by
<NAME>_FILE) and never reach a log line, whatever their shape."""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet


def test_a_secret_from_a_docker_secret_file(tmp_path, monkeypatch):
    from backend.settings.env_secrets import secret_env
    f = tmp_path / "google_client_secret"
    f.write_text("  GOCSPX-abc123secret\n", "utf-8")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_FILE", str(f))
    assert secret_env("GOOGLE_CLIENT_SECRET") == "GOCSPX-abc123secret"
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "from-env-wins")
    assert secret_env("GOOGLE_CLIENT_SECRET") == "from-env-wins"
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_FILE", str(tmp_path / "missing"))
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
    assert secret_env("GOOGLE_CLIENT_SECRET") is None


def test_the_server_key_from_a_docker_secret_file(tmp_path, monkeypatch):
    from backend.settings import secrets as S
    key = Fernet.generate_key().decode()
    (tmp_path / "k").write_text(key, "utf-8")
    monkeypatch.delenv("WKO5COACH_SECRET_KEY", raising=False)
    monkeypatch.setenv("WKO5COACH_SECRET_KEY_FILE", str(tmp_path / "k"))
    S.reset_cache()
    try:
        assert S.key_status() == "env"
        assert S.unseal(S.seal("pw")) == "pw"
        assert Fernet(key.encode()).decrypt(S.seal("x")[len(S.PREFIX):].encode()) == b"x"
    finally:
        S.reset_cache()


def test_set_secrets_never_reach_a_log_line(tmp_path, monkeypatch, caplog):
    from backend import applog
    odd = {"GOOGLE_CLIENT_SECRET": "short-ish_Secret!", "TRC_POLAR_WEBHOOK_SECRET": "abcDEF1234",
           "TRC_DEBUG_PIN": "246810", "CWA_API_KEY": "CWA-0123-4567-89AB"}
    for k, v in odd.items():
        monkeypatch.setenv(k, v)
    line = "callback failed with " + " / ".join(odd.values()) + " in it"
    out = applog.redact(line)
    for v in odd.values():
        assert v not in out
    rec = logging.LogRecord("x", logging.WARNING, __file__, 1, "%s", (line,), None)
    assert all(v not in applog.RedactingFormatter("%(message)s").format(rec) for v in odd.values())


def test_short_values_are_not_redacted_everywhere(monkeypatch):
    from backend import applog
    monkeypatch.setenv("TRC_DEBUG_PIN", "123")          # too short: would blank ordinary numbers
    assert "123" in applog.redact("week 123 done")
