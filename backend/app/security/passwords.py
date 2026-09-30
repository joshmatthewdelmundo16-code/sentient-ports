"""Password hashing — D26.

Standard-library scrypt (no extra dependency). Stored format:

    scrypt$<N>$<r>$<p>$<salt b64>$<hash b64>

Parameters travel with the hash, so they can be raised later without breaking existing
logins (`needs_rehash` tells the login path to upgrade a hash transparently).
Verification is constant-time. Plaintext passwords are never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from backend.app.config import settings

_PREFIX = "scrypt"
_DKLEN = 32


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    # maxmem must exceed 128·N·r bytes (plus headroom) or OpenSSL refuses the parameters.
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=128 * n * r * 2 + 1024 * 1024, dklen=_DKLEN)


def hash_password(password: str) -> str:
    n, r, p = settings.AUTH_SCRYPT_N, settings.AUTH_SCRYPT_R, settings.AUTH_SCRYPT_P
    salt = secrets.token_bytes(16)
    return f"{_PREFIX}${n}${r}${p}${_b64(salt)}${_b64(_derive(password, salt, n, r, p))}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        prefix, n, r, p, salt, digest = stored.split("$")
        if prefix != _PREFIX:
            return False
        candidate = _derive(password, _unb64(salt), int(n), int(r), int(p))
        return hmac.compare_digest(candidate, _unb64(digest))
    except (ValueError, TypeError):
        return False


def needs_rehash(stored: str | None) -> bool:
    try:
        _, n, r, p, _, _ = (stored or "").split("$")
        return (int(n), int(r), int(p)) != (
            settings.AUTH_SCRYPT_N, settings.AUTH_SCRYPT_R, settings.AUTH_SCRYPT_P)
    except ValueError:
        return True


# A fixed hash verified when an email is unknown, so a failed login costs the same time
# whether or not the account exists (no user enumeration through timing).
_DUMMY: str | None = None


def burn_equivalent_time(password: str) -> None:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _DUMMY)


def check_password_policy(password: str) -> str | None:
    """None if acceptable, else a reason. Length is what matters (NIST SP 800-63B)."""
    if len(password) < settings.AUTH_MIN_PASSWORD_LENGTH:
        return f"Use at least {settings.AUTH_MIN_PASSWORD_LENGTH} characters."
    if len(password) > 256:
        return "Use at most 256 characters."
    if password.strip() != password:
        return "The password must not start or end with a space."
    return None
