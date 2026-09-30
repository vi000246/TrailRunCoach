import pytest

FAKE_TP_CLIENT = ("fake-client-id", "fake-client-secret-for-tests")


@pytest.fixture(autouse=True)
def _test_secret_key(monkeypatch):
    """Tests never create or read ~/.wko5coach/secret.key."""
    from cryptography.fernet import Fernet
    from backend.settings import secrets
    monkeypatch.setenv("WKO5COACH_SECRET_KEY", Fernet.generate_key().decode())
    secrets.reset_cache()
    yield
    secrets.reset_cache()


@pytest.fixture(autouse=True)
def _no_real_tp_client(monkeypatch, tmp_path_factory):
    """Tests never read the real ~/.wko5coach/tp_client.json or TP_* env."""
    from backend.sync import tp_client
    monkeypatch.delenv("TP_CLIENT_ID", raising=False)
    monkeypatch.delenv("TP_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(tp_client, "TP_CLIENT_FILE",
                        tmp_path_factory.mktemp("tpc") / "missing_tp_client.json")


@pytest.fixture
def tp_creds(monkeypatch):
    """Obviously fake OAuth client credentials via env."""
    monkeypatch.setenv("TP_CLIENT_ID", FAKE_TP_CLIENT[0])
    monkeypatch.setenv("TP_CLIENT_SECRET", FAKE_TP_CLIENT[1])
    return FAKE_TP_CLIENT
