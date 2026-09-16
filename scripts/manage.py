"""Small operator tasks that have no UI, because they must not have one.

Usage:
    python scripts/manage.py create-admin <handle> "<Full Name>" [password]
    python scripts/manage.py grant <handle> <role>[,<role>...]
    python scripts/manage.py purge-test-accounts <prefix>
    python scripts/manage.py reset [--keep <handle>[,<handle>...]] [--yes]
    python scripts/manage.py reset-attempts [<handle>]
    python scripts/manage.py verify <handle> <roll-number> [--name "..."] [--course "..."]
                                    [--dept "..."] [--batch 2027] [--note "..."] [--force x]
    python scripts/manage.py stats
"""
from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, text  # noqa: E402

from app.db import Base, SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Session as SessionModel,
)
from app.models import (  # noqa: E402
    User,
    VerificationAttempt,
    VerificationRecord,
    utcnow,
)
from app.security import hash_password  # noqa: E402
from app.services import media  # noqa: E402

# Tables a reset leaves alone: the accounts it was told to keep, the skills
# vocabulary and the runtime settings (which carry the tuned OCR template).
RESET_KEEP_TABLES = {"users", "skills", "app_settings"}


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


async def reset_attempts(handle: str | None) -> None:
    """Clear the weekly verification rate-limit ledger.

    The ledger is keyed on the account *and* the device fingerprint, so a
    student who hit the cap on a shared phone stays capped even after their own
    row is cleared. Deleting the rows is therefore the whole reset: there is no
    counter to zero, and the next attempt starts a fresh week.
    """
    async with SessionLocal() as db:
        if handle:
            user = (
                await db.execute(select(User).where(User.handle == handle.lower()))
            ).scalar_one_or_none()
            if user is None:
                print(f"no account @{handle}")
                return
            stmt = delete(VerificationAttempt).where(VerificationAttempt.user_id == user.id)
            scope = f"@{user.handle}"
        else:
            stmt = delete(VerificationAttempt)
            scope = "every account"
        removed = (await db.execute(stmt)).rowcount or 0
        await db.commit()
        print(f"cleared {removed} verification attempt(s) for {scope}")


async def reset(keep: list[str], confirmed: bool) -> None:
    """Wipe the app back to an empty campus, keeping the named accounts.

    Every content table is truncated and every account outside ``keep`` is
    deleted, along with its verification artefacts — the stored ID-card crops
    are real student photographs, so they are shredded from disk, not orphaned.

    The skills vocabulary and ``app_settings`` survive: re-seeding those costs a
    restart, and ``app_settings`` holds the OCR template an admin has tuned.
    """
    keep_handles = [h.strip().lower() for h in keep if h.strip()]
    tables = [t.name for t in Base.metadata.sorted_tables if t.name not in RESET_KEEP_TABLES]

    async with SessionLocal() as db:
        doomed = list(
            (
                await db.execute(select(User).where(User.handle.notin_(keep_handles)))
                if keep_handles
                else await db.execute(select(User))
            ).scalars().all()
        )
        kept = list(
            (await db.execute(select(User).where(User.handle.in_(keep_handles)))).scalars().all()
        ) if keep_handles else []
        records = list((await db.execute(select(VerificationRecord))).scalars().all())

        print(f"keep    : {', '.join('@' + u.handle for u in kept) or '(nothing)'}")
        print(f"delete  : {', '.join('@' + u.handle for u in doomed) or '(no accounts)'}")
        print(f"truncate: {len(tables)} content tables")
        print(f"shred   : {len(records)} verification records and their images")

        missing = set(keep_handles) - {u.handle for u in kept}
        if missing:
            print(f"refusing: no such account(s): {', '.join('@' + h for h in sorted(missing))}")
            return
        if not confirmed:
            print("\ndry run — nothing was changed. Re-run with --yes to apply.")
            return

        shredded = 0
        for record in records:
            shredded += media.purge_verification(
                record.card_image_path, record.card_face_path, record.selfie_path
            )

        # One statement: TRUNCATE is transactional in Postgres, so a failure
        # anywhere leaves the campus exactly as it was.
        await db.execute(
            text("TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
        )
        if doomed:
            await db.execute(delete(User).where(User.id.in_([u.id for u in doomed])))

        # A kept admin walks out of the reset with a clean slate too: the
        # reputation cache is derived from reputation_events, which no longer
        # exist, and a stale cached score would be a lie.
        for user in kept:
            user.rep_total = user.rep_academic = user.rep_build = 0.0
            user.rep_service = user.rep_participation = user.rep_conduct = 0.0
            user.rep_tier = "Newcomer"
            user.rep_frozen = False
            user.last_active_at = utcnow()

        await db.commit()
        print(f"\ndone — {len(doomed)} accounts removed, {shredded} image files shredded")

    await stats()


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


async def verify_manual(handle: str, roll: str, fields: dict[str, str]) -> None:
    """Verify a student at the office counter, with no card photo.

    The route of last resort for a faded card or one that is with the office for
    correction. It enforces the same one-account-per-roll-number rule as OCR and
    lands in the same moderation audit log.
    """
    from app.services import verification

    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.handle == handle.lower()))
        ).scalar_one_or_none()
        if user is None:
            print(f"no such account: @{handle}")
            return

        batch = None
        if fields.get("batch"):
            try:
                batch = int(fields["batch"])
            except ValueError:
                print("--batch must be a year, e.g. 2027")
                return

        try:
            await verification.verify_manually(
                db,
                user,
                actor_id=None,
                roll_number=roll,
                full_name=fields.get("name"),
                course=fields.get("course"),
                department=fields.get("dept"),
                batch_year=batch,
                note=fields.get("note", ""),
                allow_reverify="force" in fields,
            )
        except ValueError as exc:
            print(f"refused: {exc}")
            return

        await db.commit()
        print(f"@{user.handle} verified as {user.roll_number} (tier {user.tier})")


def _flags(args: list[str]) -> dict[str, str]:
    """Parse the trailing --key value pairs of the verify command."""
    out: dict[str, str] = {}
    index = 0
    while index < len(args) - 1:
        if args[index].startswith("--"):
            out[args[index][2:]] = args[index + 1]
            index += 2
        else:
            index += 1
    return out


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
    elif cmd == "reset":
        args = sys.argv[2:]
        keep: list[str] = []
        if "--keep" in args:
            i = args.index("--keep")
            if i + 1 >= len(args):
                print("--keep needs a comma-separated list of handles")
                return 2
            keep = args[i + 1].split(",")
        asyncio.run(reset(keep, confirmed="--yes" in args))
    elif cmd == "reset-attempts":
        asyncio.run(reset_attempts(sys.argv[2] if len(sys.argv) > 2 else None))
    elif cmd == "verify" and len(sys.argv) >= 4:
        asyncio.run(verify_manual(sys.argv[2], sys.argv[3], _flags(sys.argv[4:])))
    elif cmd == "stats":
        asyncio.run(stats())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
