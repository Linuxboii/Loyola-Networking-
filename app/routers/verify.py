"""The verification flow: capture, OCR, selfie, review."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app.config import settings
from app.deps import DbDep, RequireUser, client_ip, verify_csrf
from app.models import TIER_VERIFIED
from app.security import device_fingerprint
from app.services import antiabuse, media, verification
from app.templating import templates

router = APIRouter(tags=["verification"])


def _fp(request: Request) -> str:
    return device_fingerprint(
        request.headers.get("user-agent"),
        request.headers.get("accept-language"),
        client_ip(request),
    )


@router.get("/verify")
async def verify_home(request: Request, db: DbDep, user: RequireUser):
    if user.tier >= TIER_VERIFIED:
        return RedirectResponse("/", status_code=303)

    record = await verification.latest_record(db, user.id)
    attempts = await verification.attempts_this_week(db, user.id, _fp(request))
    return templates.TemplateResponse(
        request,
        "verify/index.html",
        {
            "title": "Verify your identity",
            "record": record,
            "attempts_used": attempts,
            "attempts_allowed": settings.verification_attempts_per_week,
            "retention_days": settings.id_image_retention_days,
        },
    )


@router.post("/verify/card")
async def verify_card(
    request: Request,
    db: DbDep,
    user: RequireUser,
    card: UploadFile = File(...),
    live_capture: str = Form("0"),
):
    await verify_csrf(request)
    fp = _fp(request)
    ip = client_ip(request)

    used = await verification.attempts_this_week(db, user.id, fp)
    if used >= settings.verification_attempts_per_week:
        return templates.TemplateResponse(
            request,
            "verify/index.html",
            {
                "title": "Verify your identity",
                "errors": [
                    f"You have used all {settings.verification_attempts_per_week} verification attempts "
                    "for this week. Contact a moderator if you are stuck."
                ],
                "attempts_used": used,
                "attempts_allowed": settings.verification_attempts_per_week,
                "record": await verification.latest_record(db, user.id),
            },
            status_code=429,
        )

    data = await card.read()
    if len(data) > settings.max_upload_bytes:
        return templates.TemplateResponse(
            request,
            "verify/index.html",
            {"title": "Verify your identity", "errors": ["That image is too large (max 12 MB)."]},
            status_code=413,
        )
    if not media.is_image(data):
        return templates.TemplateResponse(
            request,
            "verify/index.html",
            {"title": "Verify your identity", "errors": ["That file is not an image."]},
            status_code=400,
        )

    result = await verification.submit_card(
        db, user, data, device_fp=fp, ip=ip, live_capture=(live_capture == "1")
    )
    await antiabuse.record_device(db, user.id, fp)

    if result["status"] == "rejected":
        record = result["record"]
        return templates.TemplateResponse(
            request,
            "verify/index.html",
            {
                "title": "Verify your identity",
                "errors": [record.decision_reason or "We could not verify that card."],
                "record": record,
                "ocr_debug": result["ocr"] if settings.debug else None,
                "attempts_used": used + 1,
                "attempts_allowed": settings.verification_attempts_per_week,
            },
            status_code=400,
        )

    return RedirectResponse("/verify/confirm", status_code=303)


@router.get("/verify/confirm")
async def verify_confirm(request: Request, db: DbDep, user: RequireUser):
    """Show what the OCR read, and let the student correct it before review.

    The extracted fields are shown back deliberately: an OCR mistake the student
    can see and flag is a 20-second fix, where a silent one becomes a support
    ticket and a wrong name on a profile that cannot be edited later.
    """
    record = await verification.latest_record(db, user.id)
    if record is None:
        return RedirectResponse("/verify", status_code=303)
    if record.status == "approved":
        return RedirectResponse("/verify/status", status_code=303)
    return templates.TemplateResponse(
        request,
        "verify/confirm.html",
        {"title": "Check what we read", "record": record},
    )


@router.post("/verify/dispute")
async def verify_dispute(
    request: Request, db: DbDep, user: RequireUser, note: str = Form(...)
):
    """The student says the OCR got something wrong. Flag it for the reviewer
    rather than letting them edit identity fields themselves."""
    await verify_csrf(request)
    record = await verification.latest_record(db, user.id)
    if record:
        checks = dict(record.checks or {})
        checks["student_dispute"] = note[:500]
        record.checks = checks
        record.status = "in_review" if record.selfie_path else record.status
    return RedirectResponse("/verify/confirm?noted=1", status_code=303)


@router.post("/verify/selfie")
async def verify_selfie(
    request: Request, db: DbDep, user: RequireUser, selfie: UploadFile = File(...)
):
    await verify_csrf(request)
    record = await verification.latest_record(db, user.id)
    if record is None or record.status not in {"needs_selfie", "in_review"}:
        return RedirectResponse("/verify", status_code=303)

    data = await selfie.read()
    if len(data) > settings.max_upload_bytes or not media.is_image(data):
        return templates.TemplateResponse(
            request,
            "verify/confirm.html",
            {"title": "Check what we read", "record": record, "errors": ["That selfie is not a valid image."]},
            status_code=400,
        )

    await verification.submit_selfie(db, user, record, data)
    return RedirectResponse("/verify/status", status_code=303)


@router.get("/verify/status")
async def verify_status(request: Request, db: DbDep, user: RequireUser):
    record = await verification.latest_record(db, user.id)
    queue_position = None
    if record and record.status == "in_review":
        pending = await verification.pending_queue(db, limit=200)
        for i, item in enumerate(pending, start=1):
            if item.id == record.id:
                queue_position = i
                break
    return templates.TemplateResponse(
        request,
        "verify/status.html",
        {
            "title": "Verification status",
            "record": record,
            "queue_position": queue_position,
            "expires_at": user.tier1_expires_at,
        },
    )


@router.get("/privacy")
async def privacy(request: Request):
    """The consent text, stated plainly, as PRD 5.4 requires."""
    return templates.TemplateResponse(
        request,
        "pages/privacy.html",
        {
            "title": "Privacy and your data",
            "retention_days": settings.id_image_retention_days,
            "grievance_officer": settings.grievance_officer,
            "grievance_email": settings.grievance_email,
        },
    )


@router.get("/support")
async def support(request: Request):
    return templates.TemplateResponse(
        request,
        "pages/support.html",
        {"title": "Support", "counsellor": settings.counsellor_contact},
    )


@router.post("/settings/delete-account")
async def request_deletion(request: Request, db: DbDep, user: RequireUser, confirm: str = Form("")):
    """DPDP Act 2023 requires a deletion request path. Flag it and let a human
    complete it — a self-service hard delete would orphan answers other students
    depend on, so the review decides between anonymise and erase."""
    await verify_csrf(request)
    if confirm.strip().lower() != user.handle:
        return RedirectResponse("/settings/account?delete_error=1", status_code=303)
    from app.models import utcnow as _utcnow

    user.deletion_requested_at = _utcnow()
    user.hide_from_search = True
    from app.services.moderation import log_action

    await log_action(
        db,
        action="deletion_requested",
        target_type="user",
        target_id=user.id,
        actor_id=user.id,
        actor_kind="system",
        subject_user_id=user.id,
        reason="Member requested account deletion",
        appealable=False,
    )
    return RedirectResponse("/settings/account?delete_requested=1", status_code=303)
