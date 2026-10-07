import base64
import os

import pytest
from pydantic import SecretStr

from backend.ibm.crypto import CredentialCipher, CredentialDecryptionError
from backend.ibm.sessions import InMemorySessionStore

API_KEY = SecretStr("ibm-api-key-123")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make_store(ttl: int = 60, clock: FakeClock | None = None) -> InMemorySessionStore:
    return InMemorySessionStore(CredentialCipher(os.urandom(32)), ttl, clock or FakeClock())


class TestCredentialCipher:
    def test_round_trip(self) -> None:
        cipher = CredentialCipher(os.urandom(32))
        blob = cipher.encrypt(API_KEY, aad=b"slot")
        assert API_KEY.get_secret_value().encode() not in blob
        assert cipher.decrypt(blob, aad=b"slot").get_secret_value() == API_KEY.get_secret_value()

    def test_uses_a_fresh_nonce_each_time(self) -> None:
        cipher = CredentialCipher(os.urandom(32))
        assert cipher.encrypt(API_KEY, b"slot") != cipher.encrypt(API_KEY, b"slot")

    def test_rejects_tampering(self) -> None:
        cipher = CredentialCipher(os.urandom(32))
        blob = bytearray(cipher.encrypt(API_KEY, aad=b"slot"))
        blob[-1] ^= 1
        with pytest.raises(CredentialDecryptionError):
            cipher.decrypt(bytes(blob), aad=b"slot")

    def test_rejects_a_ciphertext_moved_to_another_slot(self) -> None:
        cipher = CredentialCipher(os.urandom(32))
        blob = cipher.encrypt(API_KEY, aad=b"slot-a")
        with pytest.raises(CredentialDecryptionError):
            cipher.decrypt(blob, aad=b"slot-b")

    def test_rejects_another_key(self) -> None:
        blob = CredentialCipher(os.urandom(32)).encrypt(API_KEY, aad=b"slot")
        with pytest.raises(CredentialDecryptionError):
            CredentialCipher(os.urandom(32)).decrypt(blob, aad=b"slot")

    def test_reads_a_base64_setting(self) -> None:
        key = os.urandom(32)
        cipher = CredentialCipher.from_setting(SecretStr(base64.b64encode(key).decode()))
        assert cipher.decrypt(CredentialCipher(key).encrypt(API_KEY, b"x"), b"x") == API_KEY

    @pytest.mark.parametrize("value", ["not base64!!", base64.b64encode(b"short").decode()])
    def test_rejects_invalid_settings(self, value: str) -> None:
        with pytest.raises(ValueError):
            CredentialCipher.from_setting(SecretStr(value))

    def test_generates_a_key_when_unset(self) -> None:
        cipher = CredentialCipher.from_setting(None)
        assert cipher.decrypt(cipher.encrypt(API_KEY, b"x"), b"x") == API_KEY


class TestInMemorySessionStore:
    def test_creates_and_reads_a_session(self) -> None:
        store = make_store()
        session_id, session = store.create(API_KEY, "crn:instance")

        assert len(session_id) >= 43  # 32 random bytes, url-safe base64
        assert store.get(session_id) is session
        assert store.api_key(session_id, session).get_secret_value() == API_KEY.get_secret_value()

    def test_never_keeps_the_key_or_session_id_in_plain_text(self) -> None:
        store = make_store()
        session_id, session = store.create(API_KEY, None)

        assert API_KEY.get_secret_value() not in repr(session)
        assert API_KEY.get_secret_value().encode() not in session.encrypted_api_key
        assert session_id not in store._sessions  # keyed by HMAC of the id

    def test_unknown_sessions_are_absent(self) -> None:
        assert make_store().get("unknown") is None

    def test_sessions_expire(self) -> None:
        clock = FakeClock()
        store = make_store(ttl=60, clock=clock)
        session_id, _ = store.create(API_KEY, None)

        clock.now += 59
        assert store.get(session_id) is not None
        clock.now += 1
        assert store.get(session_id) is None

    def test_expired_sessions_are_purged_on_create(self) -> None:
        clock = FakeClock()
        store = make_store(ttl=60, clock=clock)
        store.create(API_KEY, None)
        clock.now += 61
        store.create(API_KEY, None)
        assert len(store._sessions) == 1

    def test_delete(self) -> None:
        store = make_store()
        session_id, _ = store.create(API_KEY, None)
        store.delete(session_id)
        assert store.get(session_id) is None

    def test_a_session_key_cannot_be_read_with_another_session_id(self) -> None:
        store = make_store()
        _, session_a = store.create(API_KEY, None)
        session_b_id, _ = store.create(SecretStr("other"), None)
        with pytest.raises(CredentialDecryptionError):
            store.api_key(session_b_id, session_a)
