"""Passwords, sessions, CSRF, signed media URLs, at-rest encryption."""
from __future__ import annotations

import base64
import datetime as dt
import functools
import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

# Argon2id tuned for a 1 vCPU box: still far above bcrypt cost, but a login must
# not stall the single worker. ~45ms/hash here.
_hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=1, hash_len=32, salt_len=16)

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="loyola-media")


# --- passwords --------------------------------------------------------------


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(stored: str, raw: str) -> bool:
    try:
        _hasher.verify(stored, raw)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored)
    except InvalidHashError:
        return True


PASSWORD_RULES = "At least 10 characters, with a letter and a number."


def password_problem(raw: str) -> str | None:
    if len(raw) < 10:
        return "Password must be at least 10 characters."
    if not re.search(r"[A-Za-z]", raw) or not re.search(r"\d", raw):
        return "Password must contain both a letter and a number."
    if raw.lower() in {"password12", "loyola1234", "12345678910"}:
        return "That password is too common."
    return None


# --- session tokens ---------------------------------------------------------


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- CSRF -------------------------------------------------------------------


def csrf_for(session_token: str) -> str:
    return hmac.new(settings.secret_key.encode(), (session_token + "|csrf").encode(), hashlib.sha256).hexdigest()[:32]


def csrf_valid(session_token: str | None, supplied: str | None) -> bool:
    if not session_token or not supplied:
        return False
    return hmac.compare_digest(csrf_for(session_token), supplied)


# --- device fingerprint -----------------------------------------------------


def device_fingerprint(user_agent: str | None, client_hint: str | None, ip: str | None) -> str:
    """Coarse, privacy-preserving device signal.

    It is deliberately weak — enough to notice one browser farming five accounts,
    not enough to track anyone across the internet. Only the hash is stored.
    """
    basis = "|".join([(user_agent or "")[:180], (client_hint or "")[:120], (ip or "").rsplit(".", 1)[0]])
    return hashlib.sha256((basis + settings.secret_key).encode()).hexdigest()[:32]


# --- signed media URLs ------------------------------------------------------

# Keep one URL stable long enough for clients and HTTP caches to reuse it. The
# route accepts tokens for 24 hours, so rotating every 12 hours leaves a full
# overlap window without turning media URLs into permanent public identifiers.
MEDIA_URL_ROTATION_SECONDS = 12 * 60 * 60


@functools.lru_cache(maxsize=4096)
def _sign_media_for_window(path: str, window: int) -> str:
    del window  # It is intentionally part of the cache key only.
    return _serializer.dumps(path)


def sign_media(path: str) -> str:
    window = int(time.time() // MEDIA_URL_ROTATION_SECONDS)
    return _sign_media_for_window(path, window)


def unsign_media(token: str, max_age: int = 3600) -> str | None:
    try:
        return _serializer.loads(token, max_age=max_age)
    except BadSignature:
        return None


# --- at-rest encryption for verification artefacts --------------------------


def _fernet() -> Fernet:
    key = settings.media_key
    if not key:
        # Deterministic dev fallback so a machine without LOYOLA_MEDIA_KEY still
        # runs. Production always sets the env var (see deploy/README).
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_to_file(data: bytes, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_fernet().encrypt(data))
    os.chmod(dest, 0o600)
    return str(dest)


def decrypt_file(path: str | Path) -> bytes | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return _fernet().decrypt(p.read_bytes())
    except (InvalidToken, OSError):
        return None


def shred(path: str | Path) -> bool:
    """Overwrite then unlink, so the ciphertext does not linger in free blocks."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        size = p.stat().st_size
        with open(p, "r+b") as fh:
            fh.write(os.urandom(size))
            fh.flush()
            os.fsync(fh.fileno())
        p.unlink()
        return True
    except OSError:
        try:
            p.unlink()
            return True
        except OSError:
            return False


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
