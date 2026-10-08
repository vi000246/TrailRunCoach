"""backend/settings/secrets.py and tokens sealed at rest."""
from datetime import datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from backend.settings import secrets as S
from backend.sync import coros_client, http, tp_client
from backend.tests.test_sync_e2e import FakeCoros, FakeTP, make_session, run


def test_roundtrip_and_legacy_plaintext():
    # ":" is not in the base64 alphabet: a bare "abc" turns up in ~1 of 2500 sealed values
    sealed = S.seal("pw:abc")
    assert sealed.startswith(S.PREFIX) and "pw:abc" not in sealed
    assert S.unseal(sealed) == "pw:abc"
    assert S.seal(sealed) == sealed                 # idempotent
    assert S.unseal("legacy-plain") == "legacy-plain"
    assert S.seal(None) is None and S.unseal(None) is None


def test_wrong_key_is_a_clear_error(monkeypatch):
    sealed = S.seal("abc")
    monkeypatch.setenv("WKO5COACH_SECRET_KEY", Fernet.generate_key().decode())
    S.reset_cache()
    with pytest.raises(S.SecretError):
        S.unseal(sealed)


def test_generated_key_file_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("WKO5COACH_SECRET_KEY")
    monkeypatch.setattr(S, "KEY_FILE", tmp_path / "secret.key")
    S.reset_cache()
    v = S.seal("x")
    assert (tmp_path / "secret.key").exists()
    S.reset_cache()
    assert S.unseal(v) == "x"                       # same key read back


def test_missing_key_with_sealed_file_refuses_to_generate(monkeypatch, tmp_path):
    enc = tmp_path / "tp_client.enc"
    enc.write_text("enc:v1:whatever", "ascii")
    monkeypatch.delenv("WKO5COACH_SECRET_KEY")
    monkeypatch.setattr(S, "KEY_FILE", tmp_path / "secret.key")
    monkeypatch.setattr(S, "SEALED_FILES", [enc])
    S.reset_cache()
    assert S.key_status() == "missing"
    with pytest.raises(S.SecretKeyMissing) as ei:
        S.seal("x")
    assert "SECRET_KEY_MISSING" in str(ei.value) and "docs/secrets-and-keys.md" in str(ei.value)
    assert "log out of COROS" in str(ei.value)          # SP-355 L5: the way back without the key
    assert not (tmp_path / "secret.key").exists()


def test_missing_key_with_sealed_db_column_refuses(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sync_state (athlete_id INT, tp_access_token TEXT, tp_refresh_token TEXT,"
                " tp_web_cookie TEXT, coros_access_token TEXT)")
    con.execute("INSERT INTO sync_state VALUES (1, NULL, NULL, 'enc:v1:abc', NULL)")
    con.commit(); con.close()
    monkeypatch.delenv("WKO5COACH_SECRET_KEY")
    monkeypatch.setattr(S, "KEY_FILE", tmp_path / "secret.key")
    monkeypatch.setattr(S, "_db_path", lambda: db)
    S.reset_cache()
    with pytest.raises(S.SecretKeyMissing):
        S.unseal("enc:v1:abc")
    assert not (tmp_path / "secret.key").exists()


def test_sealed_columns_cover_every_credential_column_and_the_sealed_passwords(monkeypatch, tmp_path):
    """SP-355: the 「記住密碼」 columns were missing, so a DB whose only ciphertext was a remembered
    password (tokens expired / logged out) let a lost key be silently regenerated — and the password
    could never be unsealed again. Legacy plaintext (pre-2026-09-30 rows) is not ciphertext."""
    import sqlite3
    from backend import data_registry as R
    cols = {c for t, c in S.SEALED_DB_COLUMNS if t == "sync_state"}
    assert {"coros_password_sealed", "tp_password_sealed"} <= cols
    assert cols == set(R.table("sync_state").secret_fields)
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sync_state (athlete_id INT, tp_access_token TEXT, tp_refresh_token TEXT,"
                " tp_web_cookie TEXT, coros_access_token TEXT, coros_password_sealed TEXT,"
                " tp_password_sealed TEXT)")
    con.execute("INSERT INTO sync_state VALUES (1, 'legacy-plain', NULL, NULL, 'legacy-plain', NULL, NULL)")
    con.commit()
    monkeypatch.setattr(S, "_db_path", lambda: db)
    assert S.ciphertext_exists() is False              # plaintext is readable with any key
    for col in ("coros_password_sealed", "tp_password_sealed"):
        con.execute(f"UPDATE sync_state SET {col} = 'enc:v1:abc'")
        con.commit()
        assert S.ciphertext_exists() is True, col
        con.execute(f"UPDATE sync_state SET {col} = NULL")
        con.commit()
    con.close()


def test_key_generated_only_without_ciphertext_and_env_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "KEY_FILE", tmp_path / "secret.key")
    assert S.key_status() == "env"                      # conftest sets the env key
    monkeypatch.delenv("WKO5COACH_SECRET_KEY")
    assert S.key_status() == "none"
    S.reset_cache()
    S.seal("x")
    assert (tmp_path / "secret.key").exists() and S.key_status() == "file"


def test_settings_api_and_login_surface_missing_key(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.api.auth import CorosLoginRequest, coros_login
    from backend.api.sync import get_sync_settings
    from backend.tests.test_sync_e2e import FakeCoros, make_session, run

    async def go():
        s = await make_session(tmp_path)
        enc = tmp_path / "x.enc"
        enc.write_text("enc:v1:x", "ascii")
        monkeypatch.delenv("WKO5COACH_SECRET_KEY")
        monkeypatch.setattr(S, "KEY_FILE", tmp_path / "secret.key")
        monkeypatch.setattr(S, "SEALED_FILES", [enc])
        S.reset_cache()
        assert (await get_sync_settings(1, s))["secret_key_status"] == "missing"
        with http.use_transport(httpx.MockTransport(FakeCoros([]))):
            with pytest.raises(HTTPException) as ei:
                await coros_login(CorosLoginRequest(email="me@example.com", password="pw"), s)
        assert ei.value.status_code == 503 and ei.value.detail.startswith("SECRET_KEY_MISSING")
    run(go())


def test_tokens_are_not_stored_in_plaintext(tmp_path, tp_creds):
    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(FakeCoros([]))):
            await coros_client.login("me@example.com", "pw", s, 1)
        raw = (await s.execute(text("SELECT coros_access_token FROM sync_state"))).scalar()
        assert raw.startswith(S.PREFIX) and "ctok" not in raw
        token, _base, _uid = await coros_client._get_token_and_base(s, 1)
        assert token == "ctok"

        # TP refresh re-seals and hands back the plain token
        from backend.db.models import SyncState
        st = await s.get(SyncState, 1)
        st.tp_access_token, st.tp_refresh_token = S.seal("old"), S.seal("r")
        st.tp_token_expires = datetime.utcnow() - timedelta(minutes=1)
        await s.commit()
        with http.use_transport(httpx.MockTransport(FakeTP([], {}, {}))):
            assert await tp_client._get_valid_token(s, 1) == "new"
        raw = (await s.execute(text("SELECT tp_access_token, tp_refresh_token FROM sync_state"))).one()
        assert all(v.startswith(S.PREFIX) for v in raw)
    run(go())
