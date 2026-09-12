"""Secret storage for tenant credentials (SIP passwords, OAuth refresh tokens).

`Vault` is the seam: `LocalVault` encrypts with Fernet (AES-128-CBC + HMAC) under a key derived
from `PARLIO_VAULT_KEY`, so ciphertext can live in Postgres alongside the record. A KMS/HashiCorp
backend implements the same two methods later. Plaintext never goes into a document or a log;
API responses only ever see `has_secret`/`secret_ref`.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


class Vault(Protocol):
    def seal(self, plaintext: str) -> str: ...
    def open(self, sealed: str) -> str: ...


class LocalVault:
    def __init__(self, key: str) -> None:
        digest = hashlib.sha256(key.encode()).digest()
        self._f = Fernet(base64.urlsafe_b64encode(digest))

    def seal(self, plaintext: str) -> str:
        return PREFIX + self._f.encrypt(plaintext.encode()).decode()

    def open(self, sealed: str) -> str:
        if not sealed.startswith(PREFIX):
            raise ValueError("not a sealed value")
        try:
            return self._f.decrypt(sealed.removeprefix(PREFIX).encode()).decode()
        except InvalidToken as e:
            raise ValueError("vault key mismatch or corrupt value") from e


def is_sealed(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)
