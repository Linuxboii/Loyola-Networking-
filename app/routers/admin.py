"""Admin console: the verification review queue, roles, OCR tuning, health.

The review queue deliberately shows a redacted view — the portrait crop, the
extracted fields, the automated checks — and never the full card image unless a
reviewer explicitly escalates. Every artefact opened is written to the access
log with the reviewer's id.
"""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.config import settings
from app.deps import Admin, DbDep, Moderator, client_ip, verify_csrf
from app.models import (
    AppSetting,
    DeviceFingerprint,
    PiiAccessLog,
    ReputationEvent,
    User,
    VerificationRecord,
    utcnow,
)
from app.ocr import DEFAULT_TEMPLATE, tesseract_available
from app.services import antiabuse, media, verification
from app.services import moderation as mod
from app.services import reputation as rep
from app.services.settings_store import invalidate, ocr_template, set_setting
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])

ROLES = ["moderator", "admin", "club_officer", "placement_cell"]


@router.get("")
async def dashboard(request: Request, db: DbDep, user: Admin):
    async def count(model, *where):
        return int((await db.execute(select(func.count(model.id)).where(*where))).scalar_one() or 0)

    ok, tess = tesseract_available()
    stats = {
        "users_total": await count(User),
        "users_verified": await count(User, User.tier >= 2),
        "users_pending": await count(VerificationRecord, VerificationRecord.status == "in_review"),
        "signups_7d": await count(User, User.created_at >= utcnow() - dt.timedelta(days=7)),
        "active_7d": await count(User, User.last_active_at >= utcnow() - dt.timedelta(days=7)),
        "rep_events": await count(ReputationEvent),
        "artifacts_pending_purge": await count(
            VerificationRecord,
            VerificationRecord.artifacts_purged_at.is_(None),
            VerificationRecord.card_image_path.is_not(None),
        ),
    }
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "title": "Admin",
            "stats": stats,
            "storage": media.storage_report(),
            "tesseract": tess if ok else None,
            "retention_days": settings.id_image_retention_days,
        },
    )


# --- verification review ----------------------------------------------------


@router.get("/verifications")
async def verification_queue(request: Request, db: DbDep, user: Moderator, show: str = "in_review"):
    stmt = select(VerificationRecord)
    if show == "in_review":
        stmt = stmt.where(VerificationRecord.status == "in_review")
    elif show == "rejected":
        stmt = stmt.where(VerificationRecord.status == "rejected")
    else:
        stmt = stmt.where(VerificationRecord.status == "approved")

    records = list(
        (await db.execute(stmt.order_by(VerificationRecord.created_at.asc()).limit(50))).scalars().all()
    )
    people = {
        u.id: u
        for u in (
            await db.execute(select(User).where(User.id.in_([r.user_id for r in records] or [0])))
        ).scalars().all()
    }
    devices = {}
    for record in records:
        devices[record.id] = await antiabuse.accounts_on_device(db, record.device_fp)

    return templates.TemplateResponse(
        request,
        "admin/verifications.html",
        {
            "title": "Verification queue",
            "records": records,
            "people": people,
            "devices": devices,
            "show": show,
        },
    )


@router.get("/verifications/{record_id}")
async def verification_detail(request: Request, db: DbDep, user: Moderator, record_id: int):
    record = await db.get(VerificationRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404)
    person = await db.get(User, record.user_id)

    duplicates = []
    if record.card_hash:
        duplicates = list(
            (
                await db.execute(
                    select(VerificationRecord).where(
                        VerificationRecord.card_hash == record.card_hash,
                        VerificationRecord.id != record.id,
                    )
                )
            ).scalars().all()
        )

    accesses = list(
        (
            await db.execute(
                select(PiiAccessLog)
                .where(PiiAccessLog.record_id == record_id)
                .order_by(PiiAccessLog.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
    )

    return templates.TemplateResponse(
        request,
        "admin/verification_detail.html",
        {
            "title": f"Review {person.handle if person else record.user_id}",
            "record": record,
            "person": person,
            "duplicates": duplicates,
            "accesses": accesses,
            "devices": await antiabuse.accounts_on_device(db, record.device_fp),
        },
    )


@router.get("/verifications/{record_id}/artifact/{kind}")
async def verification_artifact(
    request: Request, db: DbDep, user: Moderator, record_id: int, kind: str, escalate: str = ""
):
    """Serve one encrypted artefact, logging the access.

    ``card_face`` and ``selfie`` are the redacted default view. Requesting the
    full ``card`` requires ``escalate=1`` and is recorded as an escalation.
    """
    record = await db.get(VerificationRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404)

    path_map = {
        "card_face": record.card_face_path,
        "selfie": record.selfie_path,
        "card": record.card_image_path,
    }
    if kind not in path_map:
        raise HTTPException(status_code=404)
    if kind == "card" and escalate != "1":
        raise HTTPException(
            status_code=403,
            detail="The full card image is only shown on escalation, and the access is logged.",
        )

    data = await media.read_verification(
        db,
        path_map[kind],
        record_id=record_id,
        artifact=kind,
        actor_id=user.id,
        escalated=(kind == "card"),
        reason="moderator review",
        ip=client_ip(request),
    )
    if data is None:
        raise HTTPException(status_code=404, detail="That image has been deleted.")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/verifications/{record_id}/decide")
async def decide_verification(
    request: Request,
    db: DbDep,
    user: Moderator,
    record_id: int,
    outcome: str = Form(...),
    note: str = Form(""),
    roll_number: str = Form(""),
    full_name: str = Form(""),
):
    await verify_csrf(request)
    record = await db.get(VerificationRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404)

    if outcome == "approve":
        person = await db.get(User, record.user_id)
        # A reviewer can correct what OCR misread before approving; this is the
        # only path by which identity fields are ever edited.
        if person:
            corrected = {}
            if roll_number.strip() and roll_number.strip().upper() != (person.roll_number or ""):
                candidate = roll_number.strip().upper()
                clash = (
                    await db.execute(
                        select(User.id).where(User.roll_number == candidate, User.id != person.id)
                    )
                ).scalar_one_or_none()
                if clash:
                    raise HTTPException(
                        status_code=400, detail="Another account already holds that roll number."
                    )
                corrected["roll_number"] = candidate
                person.roll_number = candidate
            if full_name.strip() and full_name.strip() != person.full_name:
                corrected["full_name"] = full_name.strip()[:120]
                person.full_name = corrected["full_name"]
            if corrected:
                extracted = dict(record.extracted or {})
                extracted.update(corrected)
                record.extracted = extracted
                checks = dict(record.checks or {})
                checks["reviewer_corrected"] = list(corrected)
                record.checks = checks

        await verification.approve(db, record, user.id, note[:500])
    elif outcome == "reject":
        await verification.reject(db, record, user.id, note[:500] or "Could not verify this card.")
    else:
        raise HTTPException(status_code=400, detail="Unknown outcome.")

    return RedirectResponse("/admin/verifications", status_code=303)


# --- people -----------------------------------------------------------------


@router.get("/people")
async def people(request: Request, db: DbDep, user: Admin, q: str = ""):
    stmt = select(User)
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            (User.full_name.ilike(needle)) | (User.handle.ilike(needle)) | (User.roll_number.ilike(needle))
        )
    users = list((await db.execute(stmt.order_by(User.created_at.desc()).limit(100))).scalars().all())
    return templates.TemplateResponse(
        request, "admin/people.html", {"title": "People", "users": users, "q": q, "roles": ROLES}
    )


@router.post("/people/{user_id}/roles")
async def set_roles(request: Request, db: DbDep, user: Admin, user_id: int, roles: list[str] = Form([])):
    """Grant Tier 3 role verification. With no college involvement, this is a
    deliberate human decision by an admin, not an automated inference."""
    await verify_csrf(request)
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    if target.id == user.id and "admin" not in roles:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role.")

    clean = [r for r in roles if r in ROLES]
    had_role = bool(target.roles)
    target.roles = clean
    if clean and target.tier >= 2:
        target.tier = 3
        if not had_role:
            await rep.award(db, target.id, "role_verified", settle_immediately=True, detail=", ".join(clean))
    elif not clean and target.tier == 3:
        target.tier = 2

    await mod.log_action(
        db,
        action="roles_changed",
        target_type="user",
        target_id=target.id,
        actor_id=user.id,
        subject_user_id=target.id,
        reason=f"Roles set to: {', '.join(clean) or 'none'}",
        appealable=False,
    )
    return RedirectResponse("/admin/people", status_code=303)


@router.post("/people/{user_id}/unfreeze")
async def unfreeze(request: Request, db: DbDep, user: Admin, user_id: int):
    await verify_csrf(request)
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    target.rep_frozen = False
    target.status = "active"
    target.suspended_until = None
    await mod.log_action(
        db,
        action="unfreeze_user",
        target_type="user",
        target_id=user_id,
        actor_id=user.id,
        subject_user_id=user_id,
        reason="Reviewed and reinstated",
        appealable=False,
    )
    return RedirectResponse("/admin/people", status_code=303)


@router.post("/people/{user_id}/reset-password")
async def reset_password(
    request: Request, db: DbDep, user: Admin, user_id: int, password: str = Form(...)
):
    """There is no email on file to send a reset link to, so recovery is an
    in-person admin action — which is also the strongest identity check we have."""
    await verify_csrf(request)
    from app.security import hash_password, password_problem

    problem = password_problem(password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    target.password_hash = hash_password(password)
    await mod.log_action(
        db,
        action="password_reset",
        target_type="user",
        target_id=user_id,
        actor_id=user.id,
        subject_user_id=user_id,
        reason="Admin reset after identity check",
        appealable=False,
    )
    return RedirectResponse("/admin/people", status_code=303)


# --- OCR tuning -------------------------------------------------------------


@router.get("/ocr")
async def ocr_settings(request: Request, db: DbDep, user: Admin):
    template = await ocr_template(db)
    recent = list(
        (
            await db.execute(
                select(VerificationRecord)
                .order_by(VerificationRecord.created_at.desc())
                .limit(25)
            )
        ).scalars().all()
    )
    avg_conf = float(
        (
            await db.execute(
                select(func.coalesce(func.avg(VerificationRecord.confidence), 0)).where(
                    VerificationRecord.created_at >= utcnow() - dt.timedelta(days=30)
                )
            )
        ).scalar_one()
        or 0
    )
    return templates.TemplateResponse(
        request,
        "admin/ocr.html",
        {
            "title": "ID card reading",
            "template_json": json.dumps(template, indent=2, sort_keys=True),
            "default_json": json.dumps(DEFAULT_TEMPLATE, indent=2, sort_keys=True),
            "recent": recent,
            "avg_confidence": round(avg_conf, 3),
            "autopass": settings.ocr_autopass_confidence,
        },
    )


@router.post("/ocr")
async def save_ocr_settings(request: Request, db: DbDep, user: Admin, template: str = Form(...)):
    """Tune the card template without a redeploy.

    This exists because no specimen Loyola Academy card was available while this
    was built: the defaults are a sensible Indian-college layout, and the first
    real card that comes through is how they get corrected.
    """
    await verify_csrf(request)
    try:
        parsed = json.loads(template)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"That is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="The template must be a JSON object.")

    import re as _re

    pattern = parsed.get("roll_regex")
    if pattern:
        try:
            _re.compile(pattern)
        except _re.error as exc:
            raise HTTPException(status_code=400, detail=f"roll_regex will not compile: {exc}")

    await set_setting(db, "ocr_template", parsed, actor_id=user.id)
    invalidate("ocr_template")
    await mod.log_action(
        db,
        action="ocr_template_updated",
        target_type="setting",
        target_id=0,
        actor_id=user.id,
        reason="OCR template edited",
        appealable=False,
    )
    return RedirectResponse("/admin/ocr?saved=1", status_code=303)


@router.post("/ocr/test")
async def test_ocr(request: Request, db: DbDep, user: Admin, sample: str = Form(...)):
    """Dry-run the extractor against pasted OCR text, to check a regex change
    without re-photographing a card."""
    await verify_csrf(request)
    from app.ocr.extract import Word, extract_fields, overall_confidence

    words = []
    for line_no, line in enumerate((sample or "").splitlines()):
        for i, token in enumerate(line.split()):
            words.append(
                Word(text=token, conf=90.0, line=line_no, block=0, left=i * 60, top=line_no * 30,
                     width=50, height=20)
            )
    template = await ocr_template(db)
    result = extract_fields(words, template)
    result["overall"] = overall_confidence(result)

    return templates.TemplateResponse(
        request,
        "admin/ocr.html",
        {
            "title": "ID card reading",
            "template_json": json.dumps(template, indent=2, sort_keys=True),
            "default_json": json.dumps(DEFAULT_TEMPLATE, indent=2, sort_keys=True),
            "recent": [],
            "avg_confidence": 0,
            "autopass": settings.ocr_autopass_confidence,
            "test_sample": sample,
            "test_result": json.dumps(
                {"fields": result["fields"], "confidence": result["confidence"],
                 "method": result["method"], "issuer_ok": result["issuer_ok"],
                 "overall": result["overall"]},
                indent=2,
            ),
        },
    )


# --- moderation word lists --------------------------------------------------


@router.get("/lists")
async def word_lists(request: Request, db: DbDep, user: Admin):
    stored = await db.get(AppSetting, "moderation_lists")
    return templates.TemplateResponse(
        request,
        "admin/lists.html",
        {
            "title": "Filter word lists",
            "lists": stored.value if stored else {"profanity": [], "hard_block": [], "crisis": []},
        },
    )


@router.post("/lists")
async def save_word_lists(
    request: Request,
    db: DbDep,
    user: Admin,
    profanity: str = Form(""),
    hard_block: str = Form(""),
    crisis: str = Form(""),
):
    await verify_csrf(request)

    def parse(raw: str) -> list[str]:
        return [w.strip().lower() for w in (raw or "").splitlines() if w.strip()][:2000]

    await set_setting(
        db,
        "moderation_lists",
        {"profanity": parse(profanity), "hard_block": parse(hard_block), "crisis": parse(crisis)},
        actor_id=user.id,
    )
    invalidate("moderation_lists")
    return RedirectResponse("/admin/lists?saved=1", status_code=303)


# --- privacy operations -----------------------------------------------------


@router.get("/privacy-log")
async def privacy_log(request: Request, db: DbDep, user: Admin, page: int = 1):
    """Who has opened which ID artefact, and when."""
    page = max(1, page)
    per_page = 80
    rows = list(
        (
            await db.execute(
                select(PiiAccessLog)
                .order_by(PiiAccessLog.created_at.desc())
                .limit(per_page + 1)
                .offset((page - 1) * per_page)
            )
        ).scalars().all()
    )
    actors = {
        u.id: u
        for u in (
            await db.execute(
                select(User).where(User.id.in_([r.actor_id for r in rows if r.actor_id] or [0]))
            )
        ).scalars().all()
    }
    deletion_requests = list(
        (
            await db.execute(
                select(User)
                .where(User.deletion_requested_at.is_not(None))
                .order_by(User.deletion_requested_at)
            )
        ).scalars().all()
    )
    return templates.TemplateResponse(
        request,
        "admin/privacy_log.html",
        {
            "title": "PII access log",
            "rows": rows[:per_page],
            "actors": actors,
            "has_more": len(rows) > per_page,
            "page": page,
            "deletion_requests": deletion_requests,
        },
    )


@router.post("/purge-now")
async def purge_now(request: Request, db: DbDep, user: Admin):
    await verify_csrf(request)
    count = await verification.purge_expired_artifacts(db)
    await mod.log_action(
        db,
        action="artifacts_purged",
        target_type="system",
        target_id=0,
        actor_id=user.id,
        reason=f"Manual purge removed artefacts from {count} records",
        appealable=False,
    )
    return RedirectResponse(f"/admin?purged={count}", status_code=303)


@router.get("/devices")
async def devices(request: Request, db: DbDep, user: Admin):
    """Fingerprints linked to more than one account — the multi-account signal."""
    stmt = (
        select(
            DeviceFingerprint.fp_hash,
            func.count(func.distinct(DeviceFingerprint.user_id)).label("accounts"),
            func.max(DeviceFingerprint.last_seen_at).label("last_seen"),
        )
        .group_by(DeviceFingerprint.fp_hash)
        .having(func.count(func.distinct(DeviceFingerprint.user_id)) > 1)
        .order_by(func.count(func.distinct(DeviceFingerprint.user_id)).desc())
        .limit(50)
    )
    rows = (await db.execute(stmt)).all()
    detail = {}
    for fp, _n, _seen in rows:
        people_rows = (
            await db.execute(
                select(User)
                .join(DeviceFingerprint, DeviceFingerprint.user_id == User.id)
                .where(DeviceFingerprint.fp_hash == fp)
            )
        ).scalars().all()
        detail[fp] = list(people_rows)
    return templates.TemplateResponse(
        request, "admin/devices.html", {"title": "Shared devices", "rows": rows, "detail": detail}
    )
