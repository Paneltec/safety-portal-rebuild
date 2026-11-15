"""Password hashing and opaque token helpers."""
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .config import get_settings

# Cheaper parameters in the automated test suite only, so tests stay fast.
_ph = (
    PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    if get_settings().app_env == "test"
    else PasswordHasher()
)
_DUMMY_HASH = _ph.hash("not-a-real-password")

_COMMON = {"password", "password1", "12345678", "123456789", "qwertyuiop", "paneltec", "letmein123"}


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-shape check: always runs a hash verification, even for unknown users."""
    try:
        return bool(_ph.verify(password_hash or _DUMMY_HASH, password)) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_policy_error(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if len(password) > 128:
        return "Password must be at most 128 characters"
    if password.strip() != password or not password.strip():
        return "Password cannot start or end with a space"
    if password.lower() in _COMMON:
        return "That password is too common. Choose something harder to guess"
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
