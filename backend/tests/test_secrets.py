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
    sealed = S.seal("abc")
    assert sealed.startswith(S.PREFIX) and "abc" not in sealed
    assert S.unseal(sealed) == "abc"
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
