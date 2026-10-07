"""Encryption of user-supplied IBM API keys at rest (AES-256-GCM)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

KEY_BYTES = 32
NONCE_BYTES = 12
_SESSION_ID_LABEL = b"qamposer/ibm-session-id/v1"


class CredentialDecryptionError(Exception):
    """The stored credential could not be decrypted (tampered, or wrong key / AAD)."""


class CredentialCipher:
    """
    Encrypts API keys with AES-256-GCM. Every value gets a fresh random nonce, and the
    caller binds it to its storage slot through associated data (AAD), so a ciphertext
    copied to another slot fails to decrypt.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"Encryption key must be {KEY_BYTES} bytes")
        self._aead = AESGCM(key)
        # Separate key for hashing session ids, so the store never holds usable ids
        self._id_key = hmac.new(key, _SESSION_ID_LABEL, hashlib.sha256).digest()

    @classmethod
    def from_setting(cls, encoded_key: SecretStr | None) -> CredentialCipher:
        """Use the configured base64 key, or a random per-process key when none is set."""
        if encoded_key is None:
            return cls(os.urandom(KEY_BYTES))
        try:
            key = base64.b64decode(encoded_key.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be base64") from e
        return cls(key)

    def session_key(self, session_id: str) -> str:
        """Storage key for a session id (HMAC-SHA256, hex)."""
        return hmac.new(self._id_key, session_id.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, plaintext: SecretStr, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, plaintext.get_secret_value().encode(), aad)

    def decrypt(self, blob: bytes, aad: bytes) -> SecretStr:
        nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        try:
            return SecretStr(self._aead.decrypt(nonce, ciphertext, aad).decode())
        except InvalidTag as e:
            raise CredentialDecryptionError("Stored credential failed authentication") from e
