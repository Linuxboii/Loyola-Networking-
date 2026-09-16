"""The identity gate.

Adapted from PRD 5.2 for this deployment: there is no phone OTP and no college
email, so the ID card carries the entire burden. The account anchor is the roll
number extracted from the card, enforced unique at the database level.

    sign up (handle + password)        -> Tier 0, sees nothing
    ID card capture -> OCR + checks    -> Tier 1, read-only, 72h window
    selfie capture  -> reviewer        -> Tier 2, full access
    role grant by admin                -> Tier 3

Automated face matching was deliberately left out: on a 1 vCPU box it would add
a model, several hundred MB of peak RAM and seconds of latency per signup, in
exchange for a decision a human makes in three seconds from the same two images.
The selfie is still captured, encrypted and shown to the reviewer beside the
card portrait — the check happens, a human just makes it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import secrets
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    TIER_PROVISIONAL,
    TIER_VERIFIED,
    Notification,
    User,
    VerificationAttempt,
    VerificationRecord,
    utcnow,
)
from app.ocr import read_id_card
from app.services import media
from app.services import reputation as rep
from app.services.moderation import log_action
from app.services.settings_store import ocr_template

# One worker: the box has one core, and this keeps OpenCV and Tesseract out of
# the uvicorn process entirely. Created lazily, and torn down once the last
# in-flight card is done — an idle OCR worker holds ~130 MB resident, which is
# a lot of a 1.9 GB box to spend on something used a few times a day. The
# ~0.6s respawn is invisible next to a 5-second card read.
_executor: ProcessPoolExecutor | None = None
_inflight = 0
_lock = asyncio.Lock()


def get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=1)
    return _executor


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


async def run_ocr(image_bytes: bytes, template: dict[str, Any]) -> dict[str, Any]:
    """Off-thread, off-process OCR so the single worker keeps serving requests."""
    global _inflight
    loop = asyncio.get_running_loop()
    async with _lock:
        _inflight += 1
        executor = get_executor()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                executor, read_id_card, image_bytes, template, settings.ocr_autopass_confidence
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "error": "ocr_timeout", "fields": {}, "confidence": {}, "overall": 0.0}
    except Exception as exc:  # a crashed worker must not take the request with it
        async with _lock:
            shutdown_executor()
        return {"ok": False, "error": f"ocr_failed: {exc}", "fields": {}, "confidence": {}, "overall": 0.0}
    finally:
        async with _lock:
            _inflight -= 1
            if _inflight <= 0:
                _inflight = 0
                shutdown_executor()


# --- rate limiting ----------------------------------------------------------


async def attempts_this_week(db: AsyncSession, user_id: int, device_fp: str | None) -> int:
    since = utcnow() - dt.timedelta(days=7)
    clauses = [VerificationAttempt.created_at >= since]
    stmt = select(func.count(VerificationAttempt.id)).where(
        *clauses,
        (VerificationAttempt.user_id == user_id)
        | ((VerificationAttempt.device_fp == device_fp) if device_fp else False),
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def record_attempt(
    db: AsyncSession, user_id: int | None, device_fp: str | None, ip: str | None, outcome: str
) -> None:
    db.add(VerificationAttempt(user_id=user_id, device_fp=device_fp, ip=ip, outcome=outcome))
    await db.flush()


# --- checks -----------------------------------------------------------------

FAIL_REASONS = {
    "unreadable_image": "We could not read that image. Try again in better light.",
    "image_too_small": "That photo is too small. Hold the card closer to the camera.",
    "no_text_found": "No text was found on the card. Make sure the front of the card fills the frame.",
    "ocr_timeout": "Reading the card took too long. Please try again.",
    "blurry": "The photo is blurry. Hold steady and try again.",
    "too_dark": "The photo is too dark. Move somewhere brighter.",
    "too_bright": "The photo is washed out. Move away from direct light.",
    "glare": "There is glare on the card. Tilt it slightly and try again.",
    "no_roll": "We could not find a roll number on the card.",
    "roll_format": "The roll number we read does not match the expected format.",
    "roll_taken": "That roll number is already registered to another account.",
    "expired": "This card has expired.",
    "issuer": "This does not look like a card issued by the campus.",
    "low_confidence": "We could not read the card clearly enough to verify it automatically.",
}


async def evaluate(db: AsyncSession, ocr: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Apply PRD 5.2 step 4 validations to an OCR result."""
    checks: dict[str, Any] = {}
    hard_fail: list[str] = []
    soft_fail: list[str] = []

    if not ocr.get("ok"):
        return {
            "checks": {"ocr": False},
            "hard_fail": [ocr.get("error") or "no_text_found"],
            "soft_fail": [],
            "decision": "reject",
        }

    quality = ocr.get("quality", {})
    for problem in quality.get("problems", []):
        if problem in {"blurry", "too_dark", "too_bright"}:
            soft_fail.append(problem)
    checks["quality"] = not soft_fail

    checks["issuer"] = bool(ocr.get("issuer_ok"))
    if not checks["issuer"]:
        soft_fail.append("issuer")

    fields = ocr.get("fields", {})
    roll = (fields.get("roll_number") or "").strip().upper()
    checks["roll_present"] = bool(roll)
    if not roll:
        hard_fail.append("no_roll")
    else:
        taken = (
            await db.execute(select(User.id).where(User.roll_number == roll, User.id != user_id))
        ).scalar_one_or_none()
        checks["roll_unique"] = taken is None
        if taken is not None:
            hard_fail.append("roll_taken")

    expiry_raw = fields.get("expiry")
    if expiry_raw:
        try:
            expiry = dt.date.fromisoformat(expiry_raw)
            checks["not_expired"] = expiry >= dt.date.today()
            if not checks["not_expired"]:
                # An expired card is a hard fail only when it was printed on the
                # card. An expiry we merely inferred from the batch range is a
                # guess, and guesses should not lock a real student out.
                if ocr.get("method", {}).get("expiry", "").startswith("inferred"):
                    soft_fail.append("expired")
                else:
                    hard_fail.append("expired")
        except ValueError:
            checks["not_expired"] = None
    else:
        checks["not_expired"] = None

    overall = float(ocr.get("overall") or 0.0)
    checks["confidence"] = overall
    if overall < settings.ocr_autopass_confidence:
        soft_fail.append("low_confidence")

    if hard_fail:
        decision = "reject"
    elif soft_fail:
        decision = "review"
    else:
        decision = "pass"

    return {"checks": checks, "hard_fail": hard_fail, "soft_fail": soft_fail, "decision": decision}


# --- the flow ---------------------------------------------------------------


async def submit_card(
    db: AsyncSession,
    user: User,
    image_bytes: bytes,
    *,
    device_fp: str | None,
    ip: str | None,
    live_capture: bool,
) -> dict[str, Any]:
    """Process an ID-card capture and move the account to Tier 1 when it passes."""
    template = await ocr_template(db)
    ocr = await run_ocr(image_bytes, template)
    outcome = await evaluate(db, ocr, user.id)

    ref = secrets.token_urlsafe(6)
    card_path = card_hash = None
    face_path = None
    if ocr.get("card_jpeg"):
        card_path, card_hash = media.save_verification(ocr["card_jpeg"], "card", ref)
    else:
        card_path, card_hash = media.save_verification(image_bytes, "card", ref)
    if ocr.get("face_jpeg"):
        face_path, _ = media.save_verification(ocr["face_jpeg"], "cardface", ref)

    record = VerificationRecord(
        user_id=user.id,
        status="pending",
        ocr_text=(ocr.get("text") or "")[:8000],
        ocr_words={"lines": ocr.get("lines", [])[:40]},
        extracted=ocr.get("fields", {}),
        field_confidence=ocr.get("confidence", {}),
        confidence=float(ocr.get("overall") or 0.0),
        checks={
            **outcome["checks"],
            "hard_fail": outcome["hard_fail"],
            "soft_fail": outcome["soft_fail"],
            "live_capture": live_capture,
            "ocr_ms": ocr.get("elapsed_ms"),
            "ocr_passes": ocr.get("passes"),
            "rotation": ocr.get("rotation"),
            "card_detected": ocr.get("card_detected"),
            "face_found": ocr.get("face_found"),
            "best_variant": ocr.get("best_variant"),
        },
        card_image_path=card_path,
        card_face_path=face_path,
        card_hash=card_hash,
        device_fp=device_fp,
        ip=ip,
        purge_after=utcnow() + dt.timedelta(days=settings.id_image_retention_days),
    )
    db.add(record)
    await db.flush()

    fields = ocr.get("fields", {})
    if outcome["decision"] == "reject":
        record.status = "rejected"
        record.decision = "auto_reject"
        record.decision_reason = "; ".join(FAIL_REASONS.get(f, f) for f in outcome["hard_fail"])
        record.decided_at = utcnow()
        await record_attempt(db, user.id, device_fp, ip, "reject")
        await db.flush()
        return {"status": "rejected", "record": record, "ocr": ocr, "outcome": outcome}

    # Provisional identity fields are written now; they are only trusted once a
    # reviewer approves, and the roll number is what makes the account unique.
    roll = (fields.get("roll_number") or "").strip().upper()
    if roll:
        user.roll_number = roll
    if fields.get("full_name"):
        user.full_name = fields["full_name"][:120]
    if fields.get("course"):
        user.course = str(fields["course"])[:80]
    if fields.get("department"):
        user.department = str(fields["department"])[:80]
    if fields.get("batch_year"):
        try:
            user.batch_year = int(fields["batch_year"])
        except (TypeError, ValueError):
            pass
    if fields.get("expiry"):
        try:
            user.card_expiry = dt.date.fromisoformat(fields["expiry"])
        except ValueError:
            pass

    if user.tier < TIER_PROVISIONAL:
        user.tier = TIER_PROVISIONAL
        user.tier1_expires_at = utcnow() + dt.timedelta(hours=settings.provisional_tier_hours)

    record.status = "needs_selfie"
    await record_attempt(db, user.id, device_fp, ip, outcome["decision"])
    await db.flush()
    return {"status": "needs_selfie", "record": record, "ocr": ocr, "outcome": outcome}


async def submit_selfie(
    db: AsyncSession, user: User, record: VerificationRecord, image_bytes: bytes
) -> VerificationRecord:
    ref = secrets.token_urlsafe(6)
    path, _ = media.save_verification(image_bytes, "selfie", ref)
    record.selfie_path = path
    record.status = "in_review"
    await db.flush()
    db.add(
        Notification(
            user_id=user.id,
            kind="verification",
            title="Your verification is with a reviewer",
            body="You have read-only access until it is approved. This usually takes under 24 hours.",
            link="/verify/status",
        )
    )
    await db.flush()
    return record


async def approve(
    db: AsyncSession, record: VerificationRecord, reviewer_id: int | None, note: str = ""
) -> None:
    user = await db.get(User, record.user_id)
    record.status = "approved"
    record.decision = "approved"
    record.decision_reason = note or "Verified"
    record.decided_at = utcnow()
    record.reviewer_id = reviewer_id
    # The retention clock starts when verification completes (PRD 5.3).
    record.purge_after = utcnow() + dt.timedelta(days=settings.id_image_retention_days)

    if user:
        user.tier = max(user.tier, TIER_VERIFIED)
        user.tier1_expires_at = None
        await rep.award(db, user.id, "verified_tier2", settle_immediately=True, detail="Identity verified")
        db.add(
            Notification(
                user_id=user.id,
                kind="verification",
                title="You are verified",
                body="Full access is open: post, comment, vote and appear in search.",
                link="/",
            )
        )
    await log_action(
        db,
        action="verification_approved",
        target_type="verification",
        target_id=record.id,
        actor_id=reviewer_id,
        subject_user_id=record.user_id,
        reason=note or "Approved",
        appealable=False,
    )
    await db.flush()


async def reject(
    db: AsyncSession, record: VerificationRecord, reviewer_id: int | None, reason: str
) -> None:
    record.status = "rejected"
    record.decision = "rejected"
    record.decision_reason = reason
    record.decided_at = utcnow()
    record.reviewer_id = reviewer_id

    user = await db.get(User, record.user_id)
    if user:
        # Release the roll number so a genuine owner is not locked out by a
        # rejected claim on it.
        user.roll_number = None
        user.tier = 0
        db.add(
            Notification(
                user_id=user.id,
                kind="verification",
                title="Verification was not approved",
                body=f"{reason} You can appeal or try again with a clearer photo.",
                link="/verify",
            )
        )
    await log_action(
        db,
        action="verification_rejected",
        target_type="verification",
        target_id=record.id,
        actor_id=reviewer_id,
        subject_user_id=record.user_id,
        reason=reason,
    )
    await db.flush()


async def verify_manually(
    db: AsyncSession,
    user: User,
    *,
    actor_id: int | None,
    roll_number: str,
    full_name: str | None = None,
    course: str | None = None,
    department: str | None = None,
    batch_year: int | None = None,
    note: str = "",
    allow_reverify: bool = False,
) -> VerificationRecord:
    """Verify a student at the office counter, with no card photo at all.

    Every campus has the cases OCR cannot serve: a faded card, a reissued one
    with no photo, a student whose card is with the office for correction. The
    PRD's guarantee is one account per roll number, not one account per
    successful OCR — so this path exists, is restricted to admins, and is
    audited exactly like an OCR-backed approval.

    ``roll_number`` is unique in the database, so an attempt to attach a roll
    number that already belongs to someone else fails loudly here rather than
    creating a second account for one student.
    """
    roll = (roll_number or "").strip().upper()
    if not roll:
        raise ValueError("A roll number is required.")
    if user.tier >= TIER_VERIFIED and not allow_reverify:
        # Re-running this on a verified account silently rewrites identity
        # fields that are meant to be immutable, so it takes a deliberate
        # override rather than a typo in a handle.
        raise ValueError(
            f"@{user.handle} is already verified as {user.roll_number}. "
            "Pass allow_reverify to correct it."
        )

    clash = (
        await db.execute(select(User).where(User.roll_number == roll, User.id != user.id))
    ).scalar_one_or_none()
    if clash is not None:
        raise ValueError(f"Roll number {roll} already belongs to @{clash.handle}.")

    user.roll_number = roll
    if full_name:
        user.full_name = full_name[:120]
    if course:
        user.course = course[:80]
    if department:
        user.department = department[:80]
    if batch_year:
        user.batch_year = batch_year

    record = VerificationRecord(
        user_id=user.id,
        status="pending",
        confidence=1.0,
        extracted={
            "roll_number": roll,
            "full_name": user.full_name,
            "course": user.course,
            "department": user.department,
            "batch_year": user.batch_year,
        },
        checks={"manual": True, "note": note[:500]},
        # No card image exists, so there is nothing to purge and nothing to
        # decrypt later — the audit trail is the moderation log alone.
        purge_after=None,
    )
    db.add(record)
    await db.flush()

    await approve(db, record, actor_id, note=note or "Verified at the office counter")
    return record


async def latest_record(db: AsyncSession, user_id: int) -> VerificationRecord | None:
    return (
        await db.execute(
            select(VerificationRecord)
            .where(VerificationRecord.user_id == user_id)
            .order_by(VerificationRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def pending_queue(db: AsyncSession, limit: int = 50) -> list[VerificationRecord]:
    rows = (
        await db.execute(
            select(VerificationRecord)
            .where(VerificationRecord.status == "in_review")
            .order_by(VerificationRecord.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def purge_expired_artifacts(db: AsyncSession) -> int:
    """Delete ID images 30 days after verification completes. Non-negotiable."""
    now = utcnow()
    due = (
        await db.execute(
            select(VerificationRecord).where(
                VerificationRecord.purge_after.is_not(None),
                VerificationRecord.purge_after <= now,
                VerificationRecord.artifacts_purged_at.is_(None),
                VerificationRecord.status.in_(("approved", "rejected", "expired")),
            )
        )
    ).scalars().all()

    purged = 0
    for record in due:
        media.purge_verification(record.card_image_path, record.card_face_path, record.selfie_path)
        record.card_image_path = None
        record.card_face_path = None
        record.selfie_path = None
        record.artifacts_purged_at = now
        purged += 1
    if purged:
        await db.flush()
    return purged


async def expire_provisional(db: AsyncSession) -> int:
    """Tier 1 is a 72-hour window; after that, read-only access lapses."""
    now = utcnow()
    users = (
        await db.execute(
            select(User).where(
                User.tier == TIER_PROVISIONAL,
                User.tier1_expires_at.is_not(None),
                User.tier1_expires_at <= now,
            )
        )
    ).scalars().all()
    for user in users:
        user.tier = 0
        db.add(
            Notification(
                user_id=user.id,
                kind="verification",
                title="Your provisional access expired",
                body="Finish verification to regain access.",
                link="/verify",
            )
        )
    if users:
        await db.flush()
    return len(users)
