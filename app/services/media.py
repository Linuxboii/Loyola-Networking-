"""File storage.

Two stores, deliberately separated (PRD 9: "separate, tighter-permission bucket
for verification artifacts"):

* ``media_root`` — avatars, post images, resource files. Served through a signed
  URL so nothing is guessable, but stored as ordinary bytes.
* ``verification_root`` — ID cards and selfies. Encrypted at rest with Fernet,
  mode 0600, every read written to ``pii_access_log``, and shredded 30 days
  after the verification completes.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PiiAccessLog
from app.security import content_hash, decrypt_file, encrypt_to_file, shred

IMAGE_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"RIFF": "webp",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}

DOC_MAGIC = {
    b"%PDF-": "pdf",
}

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sniff(data: bytes) -> str | None:
    """Trust the bytes, never the filename or the client's content-type."""
    for magic, ext in IMAGE_MAGIC.items():
        if data.startswith(magic):
            if ext == "webp" and data[8:12] != b"WEBP":
                continue
            return ext
    for magic, ext in DOC_MAGIC.items():
        if data.startswith(magic):
            return ext
    return None


def is_image(data: bytes) -> bool:
    ext = sniff(data)
    return ext in {"jpg", "png", "webp", "gif"}


def safe_filename(name: str, fallback: str = "file") -> str:
    base = SAFE_NAME.sub("_", (name or "").strip())[-90:].strip("._-")
    return base or fallback


def _dated_dir(root: Path) -> Path:
    today = dt.date.today()
    return root / f"{today.year:04d}" / f"{today.month:02d}"


def save_public(data: bytes, ext: str | None = None, subdir: str = "img") -> str:
    """Store a public asset. Returns a path relative to ``media_root``."""
    ext = ext or sniff(data) or "bin"
    folder = _dated_dir(settings.media_root / subdir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_urlsafe(16)}.{ext}"
    (folder / name).write_bytes(data)
    return str((folder / name).relative_to(settings.media_root)).replace("\\", "/")


def read_public(rel_path: str) -> bytes | None:
    # Contain path traversal: the resolved file must stay under media_root.
    target = (settings.media_root / rel_path).resolve()
    try:
        target.relative_to(settings.media_root.resolve())
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target.read_bytes()


def delete_public(rel_path: str) -> bool:
    target = (settings.media_root / rel_path).resolve()
    try:
        target.relative_to(settings.media_root.resolve())
    except ValueError:
        return False
    if target.is_file():
        target.unlink()
        return True
    return False


# --- verification artefacts -------------------------------------------------


def save_verification(data: bytes, kind: str, record_ref: str) -> tuple[str, str]:
    """Encrypt and store an ID card or selfie. Returns (path, sha256 of plaintext)."""
    folder = _dated_dir(settings.verification_root)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{record_ref}-{kind}-{secrets.token_urlsafe(8)}.enc"
    path = folder / name
    encrypt_to_file(data, path)
    return str(path), content_hash(data)


async def read_verification(
    db: AsyncSession,
    path: str | None,
    *,
    record_id: int,
    artifact: str,
    actor_id: int | None,
    escalated: bool = False,
    reason: str | None = None,
    ip: str | None = None,
) -> bytes | None:
    """Decrypt an artefact **and record who looked at it**.

    The access log is not optional bookkeeping — it is the control that makes
    "only the extracted fields persist" a claim anyone can audit.
    """
    db.add(
        PiiAccessLog(
            actor_id=actor_id,
            record_id=record_id,
            artifact=artifact,
            escalated=escalated,
            reason=reason,
            ip=ip,
        )
    )
    if not path:
        return None
    return decrypt_file(path)


def purge_verification(*paths: str | None) -> int:
    removed = 0
    for p in paths:
        if p and shred(p):
            removed += 1
    return removed


def storage_report() -> dict[str, Any]:
    def folder_size(root: Path) -> tuple[int, int]:
        count = 0
        total = 0
        if root.exists():
            for f in root.rglob("*"):
                if f.is_file():
                    count += 1
                    total += f.stat().st_size
        return count, total

    m_count, m_bytes = folder_size(settings.media_root)
    v_count, v_bytes = folder_size(settings.verification_root)
    return {
        "media_files": m_count,
        "media_mb": round(m_bytes / 1_048_576, 1),
        "verification_files": v_count,
        "verification_mb": round(v_bytes / 1_048_576, 1),
    }
