"""Encryption of sensitive fields at rest (HR identifiers now; integration secrets and payroll later).

The key comes from ENCRYPTION_KEY and is never stored in the database. Losing or changing it makes
the encrypted values unreadable, so it must be backed up separately from the data.
"""
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from .config import get_settings


class KeyUnavailable(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().encryption_key.strip()
    if not key:
        raise KeyUnavailable("ENCRYPTION_KEY is not set")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise KeyUnavailable("ENCRYPTION_KEY is not a valid key") from exc


def key_status() -> dict[str, Any]:
    try:
        _fernet()
        return {"configured": True, "message": None}
    except KeyUnavailable as exc:
        return {"configured": False, "message": f"{exc}. Generate one with `python -m app.seed generate-key` and add it to the server's .env file."}


def encrypt_json(value: dict[str, Any]) -> str:
    try:
        return _fernet().encrypt(json.dumps(value).encode()).decode()
    except KeyUnavailable as exc:
        raise HTTPException(503, f"Sensitive details cannot be saved: {exc}. Ask the administrator to set it on the server.") from exc


def decrypt_json(token: str | None) -> dict[str, Any]:
    """Decrypt, or raise a clear 503 when the key is missing or is not the key the data was saved with."""
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except KeyUnavailable as exc:
        raise HTTPException(503, f"Sensitive details cannot be read: {exc}.") from exc
    except InvalidToken as exc:
        raise HTTPException(503, "Sensitive details cannot be read: the server's encryption key does not match the key they were saved with.") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
