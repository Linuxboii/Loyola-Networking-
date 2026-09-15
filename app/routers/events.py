"""Events: create, RSVP, QR ticket, check-in.

Check-in is what makes the Community Service pillar trustworthy — points for
volunteering are only awarded against a scanned ticket, not a self-report.
"""
from __future__ import annotations

import base64
import datetime as dt
import hmac
import io
import secrets
from hashlib import sha256

import qrcode
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, can, verify_csrf
from app.models import Event, Group, GroupMember, Rsvp, User, utcnow
from app.services import notify
from app.services import reputation as rep
from app.templating import templates

router = APIRouter(prefix="/events", tags=["events"])


def ticket_code(event_id: int, user_id: int, secret: str) -> str:
    digest = hmac.new(secret.encode(), f"{event_id}:{user_id}".encode(), sha256).hexdigest()
    return f"{event_id}-{user_id}-{digest[:10]}".upper()


def ticket_valid(code: str, event: Event) -> int | None:
    """Returns the user id encoded in a ticket, or None if it does not verify."""
    try:
        event_id, user_id, _sig = code.strip().upper().split("-")
        eid, uid = int(event_id), int(user_id)
    except (ValueError, AttributeError):
        return None
    if eid != event.id:
        return None
    if not hmac.compare_digest(ticket_code(eid, uid, event.checkin_secret), code.strip().upper()):
        return None
    return uid


@router.get("")
async def index(request: Request, db: DbDep, user: Reader, when: str = "upcoming"):
    stmt = select(Event).options(joinedload(Event.host)).where(Event.status == "active")
    if when == "past":
        stmt = stmt.where(Event.starts_at < utcnow()).order_by(Event.starts_at.desc())
    else:
        stmt = stmt.where(Event.starts_at >= utcnow() - dt.timedelta(hours=4)).order_by(Event.starts_at)

    events = list((await db.execute(stmt.limit(100))).unique().scalars().all())
    my_rsvps = {
        int(eid)
        for eid in (
            await db.execute(
                select(Rsvp.event_id).where(Rsvp.user_id == user.id, Rsvp.state == "going")
            )
        ).scalars().all()
    }

    by_day: dict[str, list[Event]] = {}
    for event in events:
        by_day.setdefault(event.starts_at.strftime("%A, %d %B"), []).append(event)

    return templates.TemplateResponse(
        request,
        "events/index.html",
        {"title": "Campus events", "by_day": by_day, "when": when, "my_rsvps": my_rsvps, "count": len(events)},
    )


@router.get("/new")
async def new_form(request: Request, db: DbDep, user: Verified):
    if not can(user, "host_event"):
        raise HTTPException(status_code=403, detail="Hosting events unlocks at Established standing.")
    groups = list(
        (
            await db.execute(
                select(Group)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .where(GroupMember.user_id == user.id, GroupMember.role.in_(("admin", "officer")))
            )
        ).scalars().all()
    )
    return templates.TemplateResponse(
        request, "events/new.html", {"title": "Host an event", "groups": groups}
    )


@router.post("/new")
async def create(
    request: Request,
    db: DbDep,
    user: Verified,
    title: str = Form(...),
    description: str = Form(""),
    venue: str = Form(""),
    starts_at: str = Form(...),
    ends_at: str = Form(""),
    capacity: str = Form(""),
    group_id: str = Form(""),
    tags: str = Form(""),
):
    await verify_csrf(request)
    if not can(user, "host_event"):
        raise HTTPException(status_code=403, detail="Hosting events unlocks at Established standing.")

    try:
        start = dt.datetime.fromisoformat(starts_at).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="That start time is not valid.")
    end = None
    if ends_at:
        try:
            end = dt.datetime.fromisoformat(ends_at).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            end = None
    if end and end <= start:
        raise HTTPException(status_code=400, detail="The event cannot end before it starts.")

    gid = None
    if group_id:
        try:
            gid = int(group_id)
        except ValueError:
            gid = None
        if gid:
            membership = (
                await db.execute(
                    select(GroupMember).where(
                        GroupMember.group_id == gid,
                        GroupMember.user_id == user.id,
                        GroupMember.role.in_(("admin", "officer")),
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                raise HTTPException(status_code=403, detail="You do not run that group.")

    event = Event(
        host_id=user.id,
        group_id=gid,
        title=title.strip()[:160],
        description=(description or "").strip()[:4000],
        venue=(venue or "").strip()[:160],
        starts_at=start,
        ends_at=end,
        capacity=int(capacity) if capacity.isdigit() else None,
        tags=[t.strip().lstrip("#").lower() for t in (tags or "").replace(",", " ").split()][:5],
        checkin_secret=secrets.token_urlsafe(16),
        is_official=bool(gid),
    )
    db.add(event)
    await db.flush()
    return RedirectResponse(f"/events/{event.id}", status_code=303)


@router.get("/{event_id}")
async def detail(request: Request, db: DbDep, user: Reader, event_id: int):
    event = (
        await db.execute(select(Event).options(joinedload(Event.host)).where(Event.id == event_id))
    ).unique().scalar_one_or_none()
    if event is None or event.status != "active":
        raise HTTPException(status_code=404)

    my_rsvp = (
        await db.execute(select(Rsvp).where(Rsvp.event_id == event_id, Rsvp.user_id == user.id))
    ).scalar_one_or_none()
    attendees = list(
        (
            await db.execute(
                select(User)
                .join(Rsvp, Rsvp.user_id == User.id)
                .where(Rsvp.event_id == event_id, Rsvp.state == "going")
                .limit(60)
            )
        ).scalars().all()
    )
    group = await db.get(Group, event.group_id) if event.group_id else None
    is_host = event.host_id == user.id or user.is_moderator

    return templates.TemplateResponse(
        request,
        "events/detail.html",
        {
            "title": event.title,
            "event": event,
            "my_rsvp": my_rsvp,
            "attendees": attendees,
            "group": group,
            "is_host": is_host,
            "is_full": bool(event.capacity and event.rsvp_count >= event.capacity),
        },
    )


@router.post("/{event_id}/rsvp")
async def rsvp(request: Request, db: DbDep, user: Verified, event_id: int, role: str = Form("attendee")):
    await verify_csrf(request)
    event = await db.get(Event, event_id)
    if event is None or event.status != "active":
        raise HTTPException(status_code=404)

    existing = (
        await db.execute(select(Rsvp).where(Rsvp.event_id == event_id, Rsvp.user_id == user.id))
    ).scalar_one_or_none()

    if existing:
        if existing.checked_in_at:
            raise HTTPException(status_code=400, detail="You have already checked in.")
        if existing.state == "going":
            existing.state = "cancelled"
            event.rsvp_count = max(0, event.rsvp_count - 1)
        else:
            if event.capacity and event.rsvp_count >= event.capacity:
                raise HTTPException(status_code=400, detail="This event is full.")
            existing.state = "going"
            event.rsvp_count += 1
        return RedirectResponse(f"/events/{event_id}", status_code=303)

    if event.capacity and event.rsvp_count >= event.capacity:
        raise HTTPException(status_code=400, detail="This event is full.")

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
    return RedirectResponse(f"/events/{event_id}", status_code=303)


@router.get("/{event_id}/ticket.png")
async def ticket_qr(db: DbDep, user: Verified, event_id: int):
    row = (
        await db.execute(select(Rsvp).where(Rsvp.event_id == event_id, Rsvp.user_id == user.id))
    ).scalar_one_or_none()
    if row is None or row.state != "going":
        raise HTTPException(status_code=404)

    img = qrcode.make(row.ticket_code, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/{event_id}/checkin")
async def checkin_desk(request: Request, db: DbDep, user: Verified, event_id: int):
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    if event.host_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="Only the host runs check-in.")

    checked = int(
        (
            await db.execute(
                select(func.count(Rsvp.id)).where(
                    Rsvp.event_id == event_id, Rsvp.checked_in_at.is_not(None)
                )
            )
        ).scalar_one()
        or 0
    )
    return templates.TemplateResponse(
        request,
        "events/checkin.html",
        {"title": f"Check-in · {event.title}", "event": event, "checked": checked},
    )


@router.post("/{event_id}/checkin")
async def do_checkin(
    request: Request, db: DbDep, user: Verified, event_id: int, code: str = Form(...)
):
    """Scan a ticket. Points for volunteering are awarded here and only here."""
    await verify_csrf(request)
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    if event.host_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="Only the host runs check-in.")

    attendee_id = ticket_valid(code, event)
    message = None
    ok = False
    if attendee_id is None:
        message = "That ticket does not belong to this event."
    else:
        row = (
            await db.execute(
                select(Rsvp).where(Rsvp.event_id == event_id, Rsvp.user_id == attendee_id)
            )
        ).scalar_one_or_none()
        attendee = await db.get(User, attendee_id)
        if row is None or attendee is None:
            message = "No RSVP found for that ticket."
        elif row.checked_in_at:
            message = f"{attendee.full_name} was already checked in at {row.checked_in_at.strftime('%H:%M')}."
        else:
            row.checked_in_at = utcnow()
            event.checkin_count += 1
            reason = "event_volunteered" if row.role == "volunteer" else "event_attended"
            await rep.award(
                db,
                attendee_id,
                reason,
                source_type="rsvp",
                source_id=row.id,
                actor_id=user.id,
                detail=event.title[:120],
            )
            ok = True
            message = f"{attendee.full_name} checked in."

    checked = int(
        (
            await db.execute(
                select(func.count(Rsvp.id)).where(
                    Rsvp.event_id == event_id, Rsvp.checked_in_at.is_not(None)
                )
            )
        ).scalar_one()
        or 0
    )
    return templates.TemplateResponse(
        request,
        "events/checkin.html",
        {
            "title": f"Check-in · {event.title}",
            "event": event,
            "checked": checked,
            "notice": message if ok else None,
            "errors": [message] if not ok else None,
        },
    )


@router.post("/{event_id}/close")
async def close_event(request: Request, db: DbDep, user: Verified, event_id: int):
    """Wrap up an event and credit the organiser for actually running it."""
    await verify_csrf(request)
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    if event.host_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="Only the host can close this.")
    if event.starts_at > utcnow():
        raise HTTPException(status_code=400, detail="You cannot close an event before it happens.")

    event.status = "closed"
    if event.checkin_count > 0:
        await rep.award(
            db,
            event.host_id,
            "event_organised",
            source_type="event",
            source_id=event.id,
            detail=f"{event.title[:80]} — {event.checkin_count} checked in",
        )
    if event.group_id:
        from app.routers.groups import recompute_club_reputation

        await recompute_club_reputation(db, event.group_id)

    await notify.push(
        db,
        event.host_id,
        kind="event",
        title=f"{event.title} wrapped up",
        body=f"{event.checkin_count} people checked in.",
        link=f"/events/{event.id}",
    )
    return RedirectResponse(f"/events/{event_id}", status_code=303)
