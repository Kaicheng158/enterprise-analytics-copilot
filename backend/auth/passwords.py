"""Argon2id password hashing adapter with injectable low-cost test parameters."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type


class Argon2PasswordService:
    algorithm_version = "argon2id-v1"

    def __init__(self, *, time_cost: int = 3, memory_cost: int = 65_536, parallelism: int = 4):
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_hash = self._hasher.hash("synthetic-dummy-password-not-an-account")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, stored_hash: str | None, password: str) -> bool:
        candidate = stored_hash or self._dummy_hash
        try:
            valid = self._hasher.verify(candidate, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False
        return bool(valid and stored_hash is not None)

    def needs_rehash(self, stored_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return True
