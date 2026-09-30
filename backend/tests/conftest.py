import pytest

FAKE_TP_CLIENT = ("fake-client-id", "fake-client-secret-for-tests")


@pytest.fixture(autouse=True)
def _test_secret_key(monkeypatch):
    """Tests never create or read ~/.wko5coach/secret.key."""
    from cryptography.fernet import Fernet
    from backend.settings import secrets
    monkeypatch.setenv("WKO5COACH_SECRET_KEY", Fernet.generate_key().decode())
    # never look at the repo's real sealed blob or the user's DB
    monkeypatch.setattr(secrets, "SEALED_FILES", [])
    monkeypatch.setattr(secrets, "_db_path", lambda: None)
    from backend.engine.wko5expr import datasource
    monkeypatch.setattr(datasource, "_db_path", lambda: None)
    secrets.reset_cache()
    yield
    secrets.reset_cache()


@pytest.fixture(autouse=True)
def _no_real_tp_client(monkeypatch, tmp_path_factory):
    """Tests never read the real ~/.wko5coach/tp_client.json or TP_* env."""
    from backend.sync import tp_client
    monkeypatch.delenv("TP_CLIENT_ID", raising=False)
    monkeypatch.delenv("TP_CLIENT_SECRET", raising=False)
    d = tmp_path_factory.mktemp("tpc")
    monkeypatch.setattr(tp_client, "TP_CLIENT_FILE", d / "missing_tp_client.json")
    monkeypatch.setattr(tp_client, "SEALED_CLIENT_FILE", d / "missing_tp_client.enc")
    monkeypatch.setenv(tp_client.WKO5_EXE_ENV, str(d / "missing_WKO5.exe"))


@pytest.fixture(autouse=True)
def _fit_root_in_tmp(monkeypatch, tmp_path_factory):
    """Synced FITs go to a temp folder, never ~/.wko5coach/fit."""
    from backend.sync import storage
    root = tmp_path_factory.mktemp("fitroot")
    monkeypatch.setattr(storage, "FIT_ROOT", root)
    return root


@pytest.fixture
def tp_creds(monkeypatch):
    """Obviously fake OAuth client credentials via env."""
    monkeypatch.setenv("TP_CLIENT_ID", FAKE_TP_CLIENT[0])
    monkeypatch.setenv("TP_CLIENT_SECRET", FAKE_TP_CLIENT[1])
    return FAKE_TP_CLIENT
