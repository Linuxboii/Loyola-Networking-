"""Small operator tasks that have no UI, because they must not have one.

Usage:
    python scripts/manage.py create-admin <handle> "<Full Name>" [password]
    python scripts/manage.py grant <handle> <role>[,<role>...]
    python scripts/manage.py purge-test-accounts <prefix>
    python scripts/manage.py stats
"""
from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Session as SessionModel,
)
from app.models import (  # noqa: E402
    User,
    VerificationRecord,
    utcnow,
)
from app.security import hash_password  # noqa: E402
from app.services import media  # noqa: E402


async def create_admin(handle: str, full_name: str, password: str | None) -> None:
    """Create a fully-verified operator account.

    This is the one account that does not go through the ID gate — somebody has
    to be able to approve the first verification, and there is nobody above them
    to do it. Every use of it is still written to the moderation audit log.
    """
    password = password or secrets.token_urlsafe(12)
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.handle == handle.lower()))
        ).scalar_one_or_none()
        if existing:
            existing.roles = ["admin", "moderator"]
            existing.tier = 3
            existing.password_hash = hash_password(password)
            existing.status = "active"
            await db.commit()
            print(f"updated existing account @{handle}")
        else:
            db.add(
                User(
                    handle=handle.lower(),
                    full_name=full_name,
                    password_hash=hash_password(password),
                    tier=3,
                    roles=["admin", "moderator"],
                    consent_at=utcnow(),
                    rep_tier="Newcomer",
                )
            )
            await db.commit()
            print(f"created @{handle}")
        print(f"password: {password}")
        print("Change it after first sign-in at /settings/account")


async def grant(handle: str, roles: str) -> None:
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.handle == handle.lower()))).scalar_one_or_none()
        if user is None:
            print(f"no account @{handle}")
            return
        user.roles = [r.strip() for r in roles.split(",") if r.strip()]
        if user.roles and user.tier >= 2:
            user.tier = 3
        await db.commit()
        print(f"@{handle} roles: {user.roles}")


async def purge_test_accounts(prefix: str) -> None:
    """Remove throwaway accounts and shred any verification artefacts they left."""
    if len(prefix) < 4:
        print("refusing: give a prefix of at least 4 characters")
        return
    async with SessionLocal() as db:
        users = list(
            (await db.execute(select(User).where(User.handle.like(f"{prefix}%")))).scalars().all()
        )
        for user in users:
            records = list(
                (
                    await db.execute(
                        select(VerificationRecord).where(VerificationRecord.user_id == user.id)
                    )
                ).scalars().all()
            )
            for record in records:
                media.purge_verification(
                    record.card_image_path, record.card_face_path, record.selfie_path
                )
            await db.execute(delete(SessionModel).where(SessionModel.user_id == user.id))
            await db.delete(user)
            print(f"removed @{user.handle} ({len(records)} verification records shredded)")
        await db.commit()
        print(f"{len(users)} accounts removed")


async def stats() -> None:
    async with SessionLocal() as db:
        total = (await db.execute(select(func.count(User.id)))).scalar_one()
        verified = (await db.execute(select(func.count(User.id)).where(User.tier >= 2))).scalar_one()
        pending = (
            await db.execute(
                select(func.count(VerificationRecord.id)).where(
                    VerificationRecord.status == "in_review"
                )
            )
        ).scalar_one()
        print(f"accounts: {total}   verified: {verified}   awaiting review: {pending}")
        print(f"storage: {media.storage_report()}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "create-admin" and len(sys.argv) >= 4:
        asyncio.run(create_admin(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None))
    elif cmd == "grant" and len(sys.argv) == 4:
        asyncio.run(grant(sys.argv[2], sys.argv[3]))
    elif cmd == "purge-test-accounts" and len(sys.argv) == 3:
        asyncio.run(purge_test_accounts(sys.argv[2]))
    elif cmd == "stats":
        asyncio.run(stats())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
