"""Study rooms: scheduled, topic-tagged group study sessions."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, can, verify_csrf
from app.models import StudyRoom, StudyRoomMember, User, utcnow
from app.services import antiabuse, notify
from app.services import reputation as rep
from app.templating import templates

router = APIRouter(prefix="/study-rooms", tags=["studyrooms"])


@router.get("")
async def index(request: Request, db: DbDep, user: Reader, tag: str = ""):
    stmt = (
        select(StudyRoom)
        .options(joinedload(StudyRoom.host))
        .where(StudyRoom.status == "active", StudyRoom.starts_at >= utcnow() - dt.timedelta(hours=3))
    )
    if tag:
        stmt = stmt.where(StudyRoom.tags.any(tag.lower()))
    rooms = list((await db.execute(stmt.order_by(StudyRoom.starts_at).limit(60))).unique().scalars().all())

    joined = {
        int(rid)
        for rid in (
            await db.execute(select(StudyRoomMember.room_id).where(StudyRoomMember.user_id == user.id))
        ).scalars().all()
    }
    return templates.TemplateResponse(
        request,
        "studyrooms/index.html",
        {"title": "Study rooms", "rooms": rooms, "joined": joined, "tag": tag},
    )


@router.post("/new")
async def create(
    request: Request,
    db: DbDep,
    user: Verified,
    topic: str = Form(...),
    detail: str = Form(""),
    mode: str = Form("physical"),
    location: str = Form(""),
    starts_at: str = Form(...),
    duration_min: str = Form("60"),
    capacity: str = Form("8"),
    tags: str = Form(""),
):
    await verify_csrf(request)
    if not can(user, "host_study_room"):
        raise HTTPException(status_code=403, detail="Hosting study rooms unlocks at Established standing.")

    allowed, _ = await antiabuse.hit_rate_limit(db, f"room:{user.id}", 5, dt.timedelta(days=1))
    if not allowed:
        raise HTTPException(status_code=429, detail="Five rooms a day is the limit.")

    try:
        start = dt.datetime.fromisoformat(starts_at).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="That start time is not valid.")

    room = StudyRoom(
        host_id=user.id,
        topic=topic.strip()[:160],
        detail=(detail or "").strip()[:2000],
        mode="virtual" if mode == "virtual" else "physical",
        location=(location or "").strip()[:200],
        starts_at=start,
        duration_min=max(15, min(480, int(duration_min) if duration_min.isdigit() else 60)),
        capacity=max(2, min(50, int(capacity) if capacity.isdigit() else 8)),
        tags=[t.strip().lstrip("#").lower() for t in (tags or "").replace(",", " ").split()][:5],
        member_count=1,
    )
    db.add(room)
    await db.flush()
    db.add(StudyRoomMember(room_id=room.id, user_id=user.id))
    await rep.award(db, user.id, "study_room_hosted", source_type="study_room", source_id=room.id)
    return RedirectResponse("/study-rooms", status_code=303)


@router.post("/{room_id}/join")
async def join(request: Request, db: DbDep, user: Verified, room_id: int):
    await verify_csrf(request)
    room = await db.get(StudyRoom, room_id)
    if room is None or room.status != "active":
        raise HTTPException(status_code=404)

    existing = (
        await db.execute(
            select(StudyRoomMember).where(
                StudyRoomMember.room_id == room_id, StudyRoomMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()

    if existing:
        if room.host_id == user.id:
            raise HTTPException(status_code=400, detail="Cancel the room instead of leaving it.")
        await db.delete(existing)
        room.member_count = max(0, room.member_count - 1)
    else:
        if room.member_count >= room.capacity:
            raise HTTPException(status_code=400, detail="That room is full.")
        db.add(StudyRoomMember(room_id=room_id, user_id=user.id))
        room.member_count += 1
        await notify.push(
            db,
            room.host_id,
            kind="studyroom",
            title=f"{user.full_name} joined your study room",
            body=room.topic[:180],
            link="/study-rooms",
            skip_if_self=user.id,
        )
    return RedirectResponse("/study-rooms", status_code=303)


@router.post("/{room_id}/cancel")
async def cancel(request: Request, db: DbDep, user: Verified, room_id: int):
    await verify_csrf(request)
    room = await db.get(StudyRoom, room_id)
    if room is None:
        raise HTTPException(status_code=404)
    if room.host_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your room.")

    room.status = "cancelled"
    members = list(
        (
            await db.execute(select(StudyRoomMember.user_id).where(StudyRoomMember.room_id == room_id))
        ).scalars().all()
    )
    await notify.push_many(
        db,
        [m for m in members if m != user.id],
        kind="studyroom",
        title="A study room you joined was cancelled",
        body=room.topic[:180],
        link="/study-rooms",
    )
    await rep.void_events(db, source_type="study_room", source_id=room.id, reason_note="room cancelled")
    return RedirectResponse("/study-rooms", status_code=303)
