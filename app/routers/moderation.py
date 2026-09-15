"""Reporting, the moderation queue, enforcement, appeals and crisis routing."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select

from app.config import settings
from app.deps import DbDep, Moderator, Reader, Verified, can, verify_csrf
from app.models import (
    REPORT_CATEGORIES,
    Appeal,
    CrisisFlag,
    ModerationAction,
    ModQueueVote,
    Report,
    Resource,
    TakedownRequest,
    User,
    utcnow,
)
from app.services import antiabuse, notify
from app.services import moderation as mod
from app.services.reputation import tier_index
from app.templating import templates

router = APIRouter(tags=["moderation"])


# --- reporting --------------------------------------------------------------


@router.get("/report")
async def report_form(request: Request, db: DbDep, user: Reader, type: str = "", id: int = 0):
    target = await mod.load_target(db, type, id) if type and id else None
    if target is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "moderation/report.html",
        {
            "title": "Report content",
            "target_type": type,
            "target_id": id,
            "excerpt": mod.target_excerpt(target),
            "categories": REPORT_CATEGORIES,
        },
    )


@router.post("/report")
async def submit_report(
    request: Request,
    db: DbDep,
    user: Verified,
    target_type: str = Form(...),
    target_id: int = Form(...),
    category: str = Form(...),
    detail: str = Form(""),
):
    await verify_csrf(request)
    if category not in {c for c, _ in REPORT_CATEGORIES}:
        raise HTTPException(status_code=400, detail="Pick a category.")

    target = await mod.load_target(db, target_type, target_id)
    if target is None:
        raise HTTPException(status_code=404)

    allowed, _ = await antiabuse.hit_rate_limit(db, f"report:{user.id}", 20, dt.timedelta(days=1))
    if not allowed:
        raise HTTPException(status_code=429, detail="Twenty reports a day is the limit.")

    existing = (
        await db.execute(
            select(Report).where(
                Report.reporter_id == user.id,
                Report.target_type == target_type,
                Report.target_id == target_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return RedirectResponse("/moderation/my-reports?dup=1", status_code=303)

    author_id = mod.target_author_id(target)
    # Established+ flags carry more weight, per PRD 6.4.
    weight = 2.0 if tier_index(user.rep_tier) >= tier_index("Established") else 1.0

    db.add(
        Report(
            reporter_id=user.id,
            target_type=target_type,
            target_id=target_id,
            target_author_id=author_id,
            category=category,
            detail=(detail or "").strip()[:2000],
            weight=weight,
        )
    )
    if author_id:
        await antiabuse.note_negative_interaction(
            db, author_id, user.id, "report", target_type, target_id
        )
    return RedirectResponse("/moderation/my-reports?filed=1", status_code=303)


@router.get("/moderation/my-reports")
async def my_reports(request: Request, db: DbDep, user: Reader):
    reports = list(
        (
            await db.execute(
                select(Report)
                .where(Report.reporter_id == user.id)
                .order_by(Report.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
    )
    return templates.TemplateResponse(
        request,
        "moderation/my_reports.html",
        {"title": "Reports you filed", "reports": reports},
    )


# --- the queue --------------------------------------------------------------


@router.get("/moderation")
async def queue(request: Request, db: DbDep, user: Moderator, show: str = "open"):
    stmt = select(Report)
    if show == "open":
        stmt = stmt.where(Report.status.in_(("open", "in_review")))
    elif show == "escalated":
        stmt = stmt.where(Report.status == "escalated")
    else:
        stmt = stmt.where(Report.status.in_(("upheld", "dismissed")))

    reports = list(
        (await db.execute(stmt.order_by(Report.weight.desc(), Report.created_at.asc()).limit(60))).scalars().all()
    )

    enriched = []
    for report in reports:
        target = await mod.load_target(db, report.target_type, report.target_id)
        author = await db.get(User, report.target_author_id) if report.target_author_id else None
        my_vote = (
            await db.execute(
                select(ModQueueVote).where(
                    ModQueueVote.report_id == report.id, ModQueueVote.moderator_id == user.id
                )
            )
        ).scalar_one_or_none()
        votes = list(
            (await db.execute(select(ModQueueVote).where(ModQueueVote.report_id == report.id))).scalars().all()
        )
        enriched.append(
            {
                "report": report,
                "excerpt": mod.target_excerpt(target) if target else "(content deleted)",
                "link": mod.target_link(report.target_type, target) if target else "#",
                "author": author,
                "my_vote": my_vote,
                "votes": votes,
                "removed": target is not None and getattr(target, "status", "") == "removed",
            }
        )

    crisis = list(
        (
            await db.execute(
                select(CrisisFlag).where(CrisisFlag.status == "open").order_by(CrisisFlag.created_at.desc())
            )
        ).scalars().all()
    )
    takedowns = list(
        (
            await db.execute(
                select(TakedownRequest).where(TakedownRequest.status == "open").order_by(TakedownRequest.created_at)
            )
        ).scalars().all()
    )
    appeals = list(
        (
            await db.execute(select(Appeal).where(Appeal.status == "open").order_by(Appeal.created_at))
        ).scalars().all()
    )

    return templates.TemplateResponse(
        request,
        "moderation/queue.html",
        {
            "title": "Moderation queue",
            "items": enriched,
            "show": show,
            "crisis": crisis,
            "takedowns": takedowns,
            "appeals": appeals,
            "is_staff": user.is_moderator,
            "counsellor": settings.counsellor_contact,
        },
    )


@router.post("/moderation/{report_id}/vote")
async def queue_vote(
    request: Request,
    db: DbDep,
    user: Moderator,
    report_id: int,
    vote: str = Form(...),
    note: str = Form(""),
):
    """Community moderation: three consistent votes decide it."""
    await verify_csrf(request)
    report = await db.get(Report, report_id)
    if report is None or report.status not in {"open", "in_review"}:
        raise HTTPException(status_code=404)
    if vote not in {"remove", "keep", "escalate"}:
        raise HTTPException(status_code=400, detail="Unknown vote.")
    if report.target_author_id == user.id:
        raise HTTPException(status_code=403, detail="You cannot moderate a report about your own content.")

    existing = (
        await db.execute(
            select(ModQueueVote).where(
                ModQueueVote.report_id == report_id, ModQueueVote.moderator_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.vote = vote
        existing.note = (note or "")[:1000]
    else:
        db.add(
            ModQueueVote(
                report_id=report_id, moderator_id=user.id, vote=vote, note=(note or "")[:1000]
            )
        )
    report.status = "in_review"
    await db.flush()
    await mod.tally_queue_votes(db, report, user.id)
    return RedirectResponse("/moderation", status_code=303)


@router.post("/moderation/{report_id}/decide")
async def decide(
    request: Request,
    db: DbDep,
    user: Moderator,
    report_id: int,
    outcome: str = Form(...),
    note: str = Form(""),
):
    """Staff moderators resolve directly, without waiting for consensus."""
    await verify_csrf(request)
    if not user.is_moderator:
        raise HTTPException(status_code=403, detail="Only appointed moderators can decide directly.")
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404)
    if outcome not in {"upheld", "dismissed"}:
        raise HTTPException(status_code=400, detail="Unknown outcome.")

    await mod.resolve_report(db, report, outcome=outcome, actor_id=user.id, note=(note or "")[:2000])
    return RedirectResponse("/moderation", status_code=303)


@router.post("/moderation/users/{user_id}/suspend")
async def suspend(
    request: Request,
    db: DbDep,
    user: Moderator,
    user_id: int,
    days: int = Form(3),
    reason: str = Form(...),
):
    await verify_csrf(request)
    if not user.is_moderator:
        raise HTTPException(status_code=403, detail="Only appointed moderators can suspend.")
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot suspend yourself.")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    if target.is_admin:
        raise HTTPException(status_code=403, detail="Admins cannot be suspended from here.")

    await mod.suspend_user(db, user_id, days=max(1, min(365, days)), actor_id=user.id, reason=reason[:1000])
    return RedirectResponse("/moderation", status_code=303)


@router.post("/moderation/crisis/{flag_id}")
async def handle_crisis(
    request: Request, db: DbDep, user: Moderator, flag_id: int, note: str = Form("")
):
    """Mark a distress flag as handled.

    Nothing here removes content or suspends anyone — that is the entire design.
    A student in trouble gets a person, not a penalty.
    """
    await verify_csrf(request)
    flag = await db.get(CrisisFlag, flag_id)
    if flag is None:
        raise HTTPException(status_code=404)
    flag.status = "handled"
    flag.handled_by = user.id
    flag.handled_at = utcnow()
    flag.note = (note or "")[:2000]
    await mod.log_action(
        db,
        action="crisis_handled",
        target_type=flag.target_type,
        target_id=flag.target_id,
        actor_id=user.id,
        subject_user_id=flag.user_id,
        reason="Routed to counsellor contact",
        appealable=False,
    )
    return RedirectResponse("/moderation", status_code=303)


@router.post("/moderation/takedowns/{takedown_id}")
async def decide_takedown(
    request: Request,
    db: DbDep,
    user: Moderator,
    takedown_id: int,
    outcome: str = Form(...),
    note: str = Form(""),
):
    await verify_csrf(request)
    row = await db.get(TakedownRequest, takedown_id)
    if row is None:
        raise HTTPException(status_code=404)
    resource = await db.get(Resource, row.resource_id)
    if resource is None:
        raise HTTPException(status_code=404)

    if outcome == "upheld":
        row.status = "upheld"
        resource.status = "removed"
        await notify.push(
            db,
            resource.uploader_id,
            kind="moderation",
            title="Your upload was removed after a takedown",
            body=(note or row.reason)[:300],
            link="/vault",
        )
    else:
        row.status = "dismissed"
        resource.status = "active"
        await notify.push(
            db,
            resource.uploader_id,
            kind="moderation",
            title="The takedown against your upload was dismissed",
            body="It is visible again.",
            link=f"/vault/{resource.id}",
        )
    row.resolution = (note or "")[:2000]
    await mod.log_action(
        db,
        action=f"takedown_{row.status}",
        target_type="resource",
        target_id=resource.id,
        actor_id=user.id,
        subject_user_id=resource.uploader_id,
        reason=(note or "")[:500],
    )
    return RedirectResponse("/moderation", status_code=303)


# --- appeals ----------------------------------------------------------------


@router.get("/moderation/appeal/{action_id}")
async def appeal_form(request: Request, db: DbDep, user: Reader, action_id: int):
    action = await db.get(ModerationAction, action_id)
    if action is None or action.subject_user_id != user.id:
        raise HTTPException(status_code=404)
    if not action.appealable:
        raise HTTPException(status_code=400, detail="That decision cannot be appealed.")

    existing = (
        await db.execute(
            select(Appeal).where(Appeal.action_id == action_id, Appeal.user_id == user.id)
        )
    ).scalar_one_or_none()
    return templates.TemplateResponse(
        request,
        "moderation/appeal.html",
        {"title": "Appeal a decision", "action": action, "existing": existing},
    )


@router.post("/moderation/appeal/{action_id}")
async def submit_appeal(
    request: Request, db: DbDep, user: Reader, action_id: int, reason: str = Form(...)
):
    await verify_csrf(request)
    action = await db.get(ModerationAction, action_id)
    if action is None or action.subject_user_id != user.id or not action.appealable:
        raise HTTPException(status_code=404)

    existing = (
        await db.execute(
            select(Appeal).where(Appeal.action_id == action_id, Appeal.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="You have already appealed this.")

    db.add(Appeal(action_id=action_id, user_id=user.id, reason=(reason or "").strip()[:4000]))
    return RedirectResponse("/moderation/my-appeals", status_code=303)


@router.get("/moderation/my-appeals")
async def my_appeals(request: Request, db: DbDep, user: Reader):
    appeals = list(
        (
            await db.execute(
                select(Appeal).where(Appeal.user_id == user.id).order_by(Appeal.created_at.desc())
            )
        ).scalars().all()
    )
    actions = {
        a.id: a
        for a in (
            await db.execute(
                select(ModerationAction).where(
                    ModerationAction.id.in_([x.action_id for x in appeals] or [0])
                )
            )
        ).scalars().all()
    }
    return templates.TemplateResponse(
        request,
        "moderation/my_appeals.html",
        {"title": "Your appeals", "appeals": appeals, "actions": actions},
    )


@router.post("/moderation/appeals/{appeal_id}/decide")
async def decide_appeal(
    request: Request,
    db: DbDep,
    user: Moderator,
    appeal_id: int,
    outcome: str = Form(...),
    note: str = Form(""),
):
    """Appeals go to a different moderator than the one who acted — enforced,
    not merely advised (PRD 8)."""
    await verify_csrf(request)
    appeal = await db.get(Appeal, appeal_id)
    if appeal is None or appeal.status != "open":
        raise HTTPException(status_code=404)

    action = await db.get(ModerationAction, appeal.action_id)
    if action and action.actor_id == user.id and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="You made the original decision. A different moderator must review this appeal.",
        )
    if outcome not in {"upheld", "denied"}:
        raise HTTPException(status_code=400, detail="Unknown outcome.")

    await mod.decide_appeal(
        db, appeal, upheld=(outcome == "upheld"), reviewer_id=user.id, note=(note or "")[:2000]
    )
    return RedirectResponse("/moderation", status_code=303)


@router.get("/moderation/audit")
async def audit_log(request: Request, db: DbDep, user: Moderator, page: int = 1):
    """Immutable record of every moderation action: who, what, when, why."""
    page = max(1, page)
    per_page = 60
    actions = list(
        (
            await db.execute(
                select(ModerationAction)
                .order_by(ModerationAction.created_at.desc())
                .limit(per_page + 1)
                .offset((page - 1) * per_page)
            )
        ).scalars().all()
    )
    has_more = len(actions) > per_page
    actors = {
        u.id: u
        for u in (
            await db.execute(
                select(User).where(
                    User.id.in_([a.actor_id for a in actions if a.actor_id] or [0])
                )
            )
        ).scalars().all()
    }
    return templates.TemplateResponse(
        request,
        "moderation/audit.html",
        {
            "title": "Audit log",
            "actions": actions[:per_page],
            "actors": actors,
            "has_more": has_more,
            "page": page,
        },
    )
