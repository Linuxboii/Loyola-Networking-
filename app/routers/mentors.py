"""Mentor Match: seniors opt in, juniors request short 1:1 sessions.

Both sides must confirm a session happened before Community Service points are
awarded. One-sided confirmation would make this the easiest pillar to farm.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, can, verify_csrf
from app.models import MentorProfile, MentorSession, User, utcnow
from app.services import antiabuse, notify
from app.services import reputation as rep
from app.templating import templates

router = APIRouter(prefix="/mentors", tags=["mentors"])


@router.get("")
async def index(request: Request, db: DbDep, user: Reader, domain: str = ""):
    stmt = (
        select(MentorProfile)
        .options(joinedload(MentorProfile.user))
        .join(User, User.id == MentorProfile.user_id)
        .where(MentorProfile.active.is_(True), User.status == "active")
    )
    if domain:
        stmt = stmt.where(MentorProfile.domains.any(domain.lower()))
    mentors = list((await db.execute(stmt.order_by(User.rep_total.desc()))).unique().scalars().all())

    mine = (
        await db.execute(select(MentorProfile).where(MentorProfile.user_id == user.id))
    ).scalar_one_or_none()

    my_sessions = list(
        (
            await db.execute(
                select(MentorSession)
                .where(or_(MentorSession.mentor_id == user.id, MentorSession.mentee_id == user.id))
                .order_by(MentorSession.created_at.desc())
                .limit(25)
            )
        ).scalars().all()
    )
    people = {
        u.id: u
        for u in (
            await db.execute(
                select(User).where(
                    User.id.in_(
                        [s.mentor_id for s in my_sessions] + [s.mentee_id for s in my_sessions] or [0]
                    )
                )
            )
        ).scalars().all()
    }

    return templates.TemplateResponse(
        request,
        "mentors/index.html",
        {
            "title": "Mentors",
            "mentors": mentors,
            "mine": mine,
            "domain": domain,
            "my_sessions": my_sessions,
            "people": people,
        },
    )


@router.post("/opt-in")
async def opt_in(
    request: Request,
    db: DbDep,
    user: Verified,
    domains: str = Form(""),
    blurb: str = Form(""),
    capacity: str = Form("4"),
    active: str = Form("on"),
):
    await verify_csrf(request)
    if not can(user, "mentor"):
        raise HTTPException(status_code=403, detail="Mentoring unlocks at Established standing.")

    parsed = [d.strip().lstrip("#").lower() for d in (domains or "").replace(",", " ").split()][:8]
    profile = (
        await db.execute(select(MentorProfile).where(MentorProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = MentorProfile(user_id=user.id)
        db.add(profile)
    profile.domains = parsed
    profile.blurb = (blurb or "").strip()[:1000]
    profile.capacity_per_month = max(1, min(20, int(capacity) if capacity.isdigit() else 4))
    profile.active = active == "on"
    return RedirectResponse("/mentors", status_code=303)


@router.post("/{mentor_id}/request")
async def request_session(
    request: Request,
    db: DbDep,
    user: Verified,
    mentor_id: int,
    topic: str = Form(...),
    note: str = Form(""),
    when: str = Form(""),
):
    await verify_csrf(request)
    if mentor_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot mentor yourself.")

    profile = (
        await db.execute(select(MentorProfile).where(MentorProfile.user_id == mentor_id))
    ).scalar_one_or_none()
    if profile is None or not profile.active:
        raise HTTPException(status_code=404, detail="That mentor is not taking requests.")

    allowed, _ = await antiabuse.hit_rate_limit(db, f"mentorreq:{user.id}", 5, dt.timedelta(days=7))
    if not allowed:
        raise HTTPException(status_code=429, detail="Five mentor requests a week is the limit.")

    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    booked = int(
        (
            await db.execute(
                select(func.count(MentorSession.id)).where(
                    MentorSession.mentor_id == mentor_id,
                    MentorSession.created_at >= month_start,
                    MentorSession.status.in_(("requested", "accepted", "completed")),
                )
            )
        ).scalar_one()
        or 0
    )
    if booked >= profile.capacity_per_month:
        raise HTTPException(status_code=400, detail="This mentor is fully booked this month.")

    scheduled = None
    if when:
        try:
            scheduled = dt.datetime.fromisoformat(when).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            scheduled = None

    session = MentorSession(
        mentor_id=mentor_id,
        mentee_id=user.id,
        topic=topic.strip()[:160],
        note=(note or "").strip()[:1000],
        scheduled_at=scheduled,
    )
    db.add(session)
    await notify.push(
        db,
        mentor_id,
        kind="mentor",
        title=f"{user.full_name} asked for a mentoring session",
        body=topic[:200],
        link="/mentors",
    )
    return RedirectResponse("/mentors", status_code=303)


@router.post("/sessions/{session_id}/respond")
async def respond(
    request: Request, db: DbDep, user: Verified, session_id: int, action: str = Form(...)
):
    await verify_csrf(request)
    session = await db.get(MentorSession, session_id)
    if session is None:
        raise HTTPException(status_code=404)
    if session.mentor_id != user.id:
        raise HTTPException(status_code=403, detail="That request is not yours to answer.")
    if action not in {"accepted", "declined"}:
        raise HTTPException(status_code=400, detail="Unknown action.")

    session.status = action
    await notify.push(
        db,
        session.mentee_id,
        kind="mentor",
        title=f"Your mentoring request was {action}",
        body=session.topic[:200],
        link="/mentors",
    )
    return RedirectResponse("/mentors", status_code=303)


@router.post("/sessions/{session_id}/confirm")
async def confirm(request: Request, db: DbDep, user: Verified, session_id: int):
    """Both sides confirm; points land only when the second one does."""
    await verify_csrf(request)
    session = await db.get(MentorSession, session_id)
    if session is None:
        raise HTTPException(status_code=404)
    if user.id not in {session.mentor_id, session.mentee_id}:
        raise HTTPException(status_code=403, detail="That session is not yours.")
    if session.status not in {"accepted", "completed"}:
        raise HTTPException(status_code=400, detail="Accept the session first.")

    if user.id == session.mentor_id:
        session.mentor_confirmed = True
    else:
        session.mentee_confirmed = True

    if session.mentor_confirmed and session.mentee_confirmed and not session.credited:
        session.status = "completed"
        session.credited = True
        profile = (
            await db.execute(select(MentorProfile).where(MentorProfile.user_id == session.mentor_id))
        ).scalar_one_or_none()
        if profile:
            profile.sessions_done += 1
        await rep.award(
            db,
            session.mentor_id,
            "mentor_session_completed",
            source_type="mentor_session",
            source_id=session.id,
            actor_id=session.mentee_id,
            detail=session.topic[:120],
        )
        await notify.push(
            db,
            session.mentor_id,
            kind="mentor",
            title="Mentoring session confirmed by both sides",
            body="Community Service points have been added to your ledger.",
            link="/me/reputation",
        )
    else:
        other = session.mentee_id if user.id == session.mentor_id else session.mentor_id
        await notify.push(
            db,
            other,
            kind="mentor",
            title=f"{user.full_name} confirmed your session",
            body="Confirm it too and the points are credited.",
            link="/mentors",
        )
    return RedirectResponse("/mentors", status_code=303)
