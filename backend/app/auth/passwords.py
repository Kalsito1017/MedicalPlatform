"""Password hashing and verification (argon2id, docs/SECURITY.md).

Parameters: argon2-cffi `PasswordHasher` defaults — argon2id, time_cost=3,
memory_cost=65536 KiB, parallelism=4, hash_len=16, salt_len=16 (RFC 9106
low-memory profile). The seed script uses the same hasher, so seeded and
API-created accounts are interchangeable. Only hashes are ever stored
(`admin.staff_user.password_hash`); plaintext passwords exist only transiently
at login/create time and are never logged, audited, or echoed.

Verification is constant-shape: when an account does not exist the password is
verified against a module-level dummy hash so unknown-email and wrong-password
logins cost the same time class (no account enumeration via timing).
"""
from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return the argon2id hash for a plaintext password."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-shape password check. Never raises on bad input."""
    candidate = password_hash if password_hash is not None else dummy_hash()
    try:
        return _hasher.verify(candidate, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    """Hash of a random secret, used to equalize timing for unknown accounts.

    The value is random per process and never stored or compared against a real
    account; it is not a secret that guards anything.
    """
    import secrets

    return _hasher.hash(secrets.token_urlsafe(32))
