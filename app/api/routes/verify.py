"""ID-card verification from the phone.

This is the flow the mobile app exists for: a student photographs their card in
the app, the server OCRs it, and the account moves from Tier 0 (sees nothing) to
Tier 1 (read-only) immediately, then Tier 2 once a reviewer approves the selfie
match. The camera capture is the whole reason a native app beats the web here.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import settings
from app.deps import Admin, DbDep, RequireUser, client_ip
from app.models import User, VerificationRecord
from app.security import device_fingerprint
from app.services import antiabuse, media, verification

router = APIRouter(prefix="/verify", tags=["api:verify"])

# Fields the student is shown back for confirmation. Identity data is never
# editable by the student — they can only flag a mistake for a human reviewer.
SHOWN_FIELDS = ("roll_number", "full_name", "course", "department", "batch_year", "expiry")


def _fp(request: Request) -> str:
    return device_fingerprint(
        request.headers.get("user-agent"),
        request.headers.get("accept-language"),
        client_ip(request),
    )


def _record_out(record: VerificationRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "status": record.status,
        "confidence": round(record.confidence or 0.0, 3),
        "extracted": {key: (record.extracted or {}).get(key) for key in SHOWN_FIELDS},
        "field_confidence": record.field_confidence or {},
        "decision": record.decision,
        "decision_reason": record.decision_reason,
        "created_at": record.created_at.isoformat(),
        "purge_after": record.purge_after.isoformat() if record.purge_after else None,
    }


@router.get("/status")
async def status(request: Request, db: DbDep, user: RequireUser):
    record = await verification.latest_record(db, user.id)
    queue_position = None
    if record is not None and record.status == "in_review":
        queue_position = int(
            (
                await db.execute(
                    select(func.count(VerificationRecord.id)).where(
                        VerificationRecord.status == "in_review",
                        VerificationRecord.created_at <= record.created_at,
                    )
                )
            ).scalar_one()
            or 0
        )
    return {
        "tier": user.tier,
        "record": _record_out(record),
        "queue_position": queue_position,
        "attempts_used": await verification.attempts_this_week(db, user.id, _fp(request)),
        "attempts_allowed": settings.verification_attempts_per_week,
        "retention_days": settings.id_image_retention_days,
        "next_step": _next_step(user.tier, record),
    }


def _next_step(tier: int, record: VerificationRecord | None) -> str:
    if tier >= 2:
        return "done"
    if record is None or record.status == "rejected":
        return "capture_card"
    if record.status == "needs_selfie":
        return "capture_selfie"
    if record.status == "in_review":
        return "wait_for_review"
    return "capture_card"


@router.post("/card")
async def submit_card(
    request: Request,
    db: DbDep,
    user: RequireUser,
    card: UploadFile = File(...),
    live_capture: bool = Form(True),
):
    if user.tier >= 2:
        raise HTTPException(409, "You are already verified.")

    fp = _fp(request)
    used = await verification.attempts_this_week(db, user.id, fp)
    if used >= settings.verification_attempts_per_week:
        raise HTTPException(
            429,
            f"You have used all {settings.verification_attempts_per_week} verification attempts "
            "for this week. Contact a moderator if you are stuck.",
        )

    data = await card.read()
    if not data:
        raise HTTPException(400, "The capture was empty.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"That image is too large (max {settings.max_upload_mb} MB).")
    if not media.is_image(data):
        raise HTTPException(400, "That file is not an image.")

    result = await verification.submit_card(
        db, user, data, device_fp=fp, ip=client_ip(request), live_capture=live_capture
    )
    await antiabuse.record_device(db, user.id, fp)

    record = result["record"]
    payload = {
        "status": result["status"],
        "record": _record_out(record),
        "tier": user.tier,
        "attempts_used": used + 1,
        "attempts_allowed": settings.verification_attempts_per_week,
        "next_step": _next_step(user.tier, record),
    }
    if settings.debug:
        payload["ocr"] = {k: v for k, v in result["ocr"].items() if k not in {"card_jpeg", "face_jpeg"}}
    if result["status"] == "rejected":
        payload["error"] = record.decision_reason or "We could not verify that card."
    return payload


@router.post("/selfie")
async def submit_selfie(db: DbDep, user: RequireUser, selfie: UploadFile = File(...)):
    record = await verification.latest_record(db, user.id)
    if record is None or record.status not in {"needs_selfie", "in_review"}:
        raise HTTPException(409, "Photograph your ID card first.")

    data = await selfie.read()
    if not data or len(data) > settings.max_upload_bytes or not media.is_image(data):
        raise HTTPException(400, "That selfie is not a valid image.")

    await verification.submit_selfie(db, user, record, data)
    return {"status": record.status, "record": _record_out(record), "next_step": "wait_for_review"}


class ManualVerifyIn(BaseModel):
    handle: str = Field(min_length=3, max_length=24)
    roll_number: str = Field(min_length=3, max_length=32)
    full_name: str | None = Field(default=None, max_length=120)
    course: str | None = Field(default=None, max_length=80)
    department: str | None = Field(default=None, max_length=80)
    batch_year: int | None = Field(default=None, ge=1990, le=2100)
    note: str = Field(default="", max_length=500)


@router.post("/manual")
async def manual(payload: ManualVerifyIn, db: DbDep, admin: Admin):
    """Verify a student at the office counter, with no card photo.

    Admin-only, and audited like every other verification decision. It exists
    because some cards will never OCR — faded, reissued, or physically with the
    office — and a student in that position must not be locked out of the
    network their classmates are on.
    """
    person = (
        await db.execute(select(User).where(User.handle == payload.handle.lower()))
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(404, "No such account.")
    if person.tier >= 2:
        raise HTTPException(409, "That account is already verified.")

    try:
        record = await verification.verify_manually(
            db,
            person,
            actor_id=admin.id,
            roll_number=payload.roll_number,
            full_name=payload.full_name,
            course=payload.course,
            department=payload.department,
            batch_year=payload.batch_year,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    return {"status": "approved", "handle": person.handle, "record": _record_out(record)}


class DisputeIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/dispute")
async def dispute(payload: DisputeIn, db: DbDep, user: RequireUser):
    """The OCR misread something. Flag it for the reviewer rather than letting
    the student edit identity fields themselves."""
    record = await verification.latest_record(db, user.id)
    if record is None:
        raise HTTPException(404, "There is nothing to dispute yet.")
    checks = dict(record.checks or {})
    checks["student_dispute"] = payload.note[:500]
    record.checks = checks
    if record.selfie_path:
        record.status = "in_review"
    await db.flush()
    return {"status": record.status, "noted": True}
