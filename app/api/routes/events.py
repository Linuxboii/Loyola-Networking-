"""Campus events: listing, RSVP, tickets and host-side check-in."""
from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.schemas import EventIn, RsvpIn, event_out, user_card
from app.deps import DbDep, Reader, Verified, can
from app.models import Event, GroupMember, Rsvp, User, utcnow
from app.routers.events import ticket_code, ticket_valid
from app.services import reputation as rep

router = APIRouter(prefix="/events", tags=["api:events"])


async def _my_rsvp(db, event_id: int, user_id: int) -> dict | None:
    row = (
        await db.execute(select(Rsvp).where(Rsvp.event_id == event_id, Rsvp.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "state": row.state,
        "role": row.role,
        "ticket_code": row.ticket_code,
        "checked_in_at": row.checked_in_at.isoformat() if row.checked_in_at else None,
    }


@router.get("")
async def index(db: DbDep, user: Reader, when: str = Query("upcoming", pattern="^(upcoming|past)$")):
    stmt = select(Event).options(joinedload(Event.host)).where(Event.status == "active")
    if when == "upcoming":
        stmt = stmt.where(Event.starts_at >= utcnow()).order_by(Event.starts_at.asc())
    else:
        stmt = stmt.where(Event.starts_at < utcnow()).order_by(Event.starts_at.desc())
    events = list((await db.execute(stmt.limit(60))).unique().scalars().all())

    mine = {
        int(eid): state
        for eid, state in (
            await db.execute(select(Rsvp.event_id, Rsvp.state).where(Rsvp.user_id == user.id))
        ).all()
    }
    return {
        "events": [
            event_out(e, my_rsvp=({"state": mine[e.id]} if e.id in mine else None)) for e in events
        ]
    }


@router.get("/{event_id}")
async def detail(event_id: int, db: DbDep, user: Reader):
    event = (
        await db.execute(select(Event).options(joinedload(Event.host)).where(Event.id == event_id))
    ).unique().scalar_one_or_none()
    if event is None:
        raise HTTPException(404, "No such event.")
    attendees = list(
        (
            await db.execute(
                select(User)
                .join(Rsvp, Rsvp.user_id == User.id)
                .where(Rsvp.event_id == event_id, Rsvp.state == "going")
                .limit(50)
            )
        ).scalars().all()
    )
    return {
        **event_out(event, my_rsvp=await _my_rsvp(db, event_id, user.id)),
        "attendees": [user_card(a) for a in attendees],
        "is_host": event.host_id == user.id,
    }


@router.post("", status_code=201)
async def create(payload: EventIn, db: DbDep, user: Verified):
    if not can(user, "host_event"):
        raise HTTPException(403, "Hosting events unlocks at Established standing.")

    start = payload.starts_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    end = payload.ends_at
    if end is not None:
        if end.tzinfo is None:
            end = end.replace(tzinfo=dt.timezone.utc)
        if end <= start:
            raise HTTPException(422, "The event cannot end before it starts.")

    if payload.group_id is not None:
        membership = (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == payload.group_id,
                    GroupMember.user_id == user.id,
                    GroupMember.role.in_(("admin", "officer")),
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(403, "You do not run that group.")

    event = Event(
        host_id=user.id,
        group_id=payload.group_id,
        title=payload.title.strip()[:160],
        description=payload.description.strip()[:4000],
        venue=payload.venue.strip()[:160],
        starts_at=start,
        ends_at=end,
        capacity=payload.capacity,
        tags=[t.strip().lstrip("#").lower() for t in payload.tags][:5],
        checkin_secret=secrets.token_urlsafe(16),
        is_official=payload.group_id is not None,
    )
    db.add(event)
    await db.flush()
    await db.refresh(event, ["host"])
    return event_out(event)


@router.post("/{event_id}/rsvp")
async def rsvp(event_id: int, payload: RsvpIn, db: DbDep, user: Verified, role: str = "attendee"):
    event = await db.get(Event, event_id)
    if event is None or event.status != "active":
        raise HTTPException(404, "No such event.")

    existing = (
        await db.execute(select(Rsvp).where(Rsvp.event_id == event_id, Rsvp.user_id == user.id))
    ).scalar_one_or_none()

    if existing is not None:
        if existing.checked_in_at:
            raise HTTPException(400, "You have already checked in.")
        if payload.state == "cancelled" or existing.state == "going":
            if existing.state == "going":
                event.rsvp_count = max(0, event.rsvp_count - 1)
            existing.state = "cancelled"
        else:
            if event.capacity and event.rsvp_count >= event.capacity:
                raise HTTPException(409, "This event is full.")
            existing.state = "going"
            event.rsvp_count += 1
    else:
        if event.capacity and event.rsvp_count >= event.capacity:
            raise HTTPException(409, "This event is full.")
        db.add(
            Rsvp(
                event_id=event_id,
                user_id=user.id,
                state="going",
                role="volunteer" if role == "volunteer" else "attendee",
                ticket_code=ticket_code(event_id, user.id, event.checkin_secret),
            )
        )
        event.rsvp_count += 1
    await db.flush()
    return {"rsvp": await _my_rsvp(db, event_id, user.id), "rsvp_count": event.rsvp_count}


@router.post("/{event_id}/checkin")
async def checkin(event_id: int, db: DbDep, user: Verified, code: str = Query(min_length=4)):
    """Host-side ticket scan. Attendance reputation is awarded here and only here."""
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "No such event.")
    if event.host_id != user.id and not user.is_moderator:
        raise HTTPException(403, "Only the host runs check-in.")

    attendee_id = ticket_valid(code, event)
    if attendee_id is None:
        raise HTTPException(400, "That ticket does not belong to this event.")

    row = (
        await db.execute(select(Rsvp).where(Rsvp.event_id == event_id, Rsvp.user_id == attendee_id))
    ).scalar_one_or_none()
    attendee = await db.get(User, attendee_id)
    if row is None or attendee is None:
        raise HTTPException(404, "No RSVP found for that ticket.")
    if row.checked_in_at:
        return {
            "ok": False,
            "message": f"{attendee.full_name} was already checked in at "
            f"{row.checked_in_at.strftime('%H:%M')}.",
        }

    row.checked_in_at = utcnow()
    event.checkin_count += 1
    await rep.award(
        db,
        attendee_id,
        "event_volunteered" if row.role == "volunteer" else "event_attended",
        source_type="rsvp",
        source_id=row.id,
        actor_id=user.id,
        detail=event.title[:120],
    )
    await db.flush()
    return {"ok": True, "message": f"{attendee.full_name} checked in.", "attendee": user_card(attendee)}
