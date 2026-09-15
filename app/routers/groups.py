"""Clubs and interest groups.

Club reputation aggregates from its events and its members' activity, which is
the point: it gives a club a reason to actually run things rather than exist as
a dormant page.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, can, verify_csrf
from app.models import Event, Group, GroupMember, Post, User, utcnow
from app.services import feed as feed_service
from app.services import media, notify
from app.services import moderation as mod
from app.templating import templates

router = APIRouter(prefix="/groups", tags=["groups"])


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:60] or "group"


async def _membership(db, group_id: int, user_id: int) -> GroupMember | None:
    return (
        await db.execute(
            select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        )
    ).scalar_one_or_none()


@router.get("")
async def index(request: Request, db: DbDep, user: Reader, kind: str = ""):
    stmt = select(Group).where(Group.status == "active")
    if kind in {"club", "interest"}:
        stmt = stmt.where(Group.kind == kind)
    groups = list(
        (await db.execute(stmt.order_by(Group.is_official.desc(), Group.member_count.desc()))).scalars().all()
    )
    mine = {
        int(gid)
        for gid in (
            await db.execute(select(GroupMember.group_id).where(GroupMember.user_id == user.id))
        ).scalars().all()
    }
    return templates.TemplateResponse(
        request,
        "groups/index.html",
        {"title": "Groups and clubs", "groups": groups, "mine": mine, "kind": kind},
    )


@router.get("/new")
async def new_form(request: Request, db: DbDep, user: Verified):
    if not can(user, "create_group"):
        raise HTTPException(
            status_code=403, detail="Creating groups unlocks at Established standing."
        )
    return templates.TemplateResponse(request, "groups/new.html", {"title": "Start a group"})


@router.post("/new")
async def create(
    request: Request,
    db: DbDep,
    user: Verified,
    name: str = Form(...),
    description: str = Form(""),
    kind: str = Form("interest"),
):
    await verify_csrf(request)
    if not can(user, "create_group"):
        raise HTTPException(status_code=403, detail="Creating groups unlocks at Established standing.")

    name = (name or "").strip()
    if len(name) < 3:
        raise HTTPException(status_code=400, detail="Give the group a name.")

    slug = slugify(name)
    clash = (await db.execute(select(Group.id).where(Group.slug == slug))).scalar_one_or_none()
    if clash:
        slug = f"{slug}-{utcnow().strftime('%H%M%S')}"

    group = Group(
        slug=slug,
        name=name[:100],
        description=(description or "").strip()[:2000],
        kind="club" if kind == "club" else "interest",
        owner_id=user.id,
        member_count=1,
    )
    db.add(group)
    await db.flush()
    db.add(GroupMember(group_id=group.id, user_id=user.id, role="admin"))
    return RedirectResponse(f"/groups/{group.slug}", status_code=303)


@router.get("/{slug}")
async def detail(request: Request, db: DbDep, user: Reader, slug: str, page: int = 1):
    group = (await db.execute(select(Group).where(Group.slug == slug))).scalar_one_or_none()
    if group is None or group.status != "active":
        raise HTTPException(status_code=404)

    membership = await _membership(db, group.id, user.id)
    posts, has_more = await feed_service.fetch(db, user, group_id=group.id, page=page)
    members = list(
        (
            await db.execute(
                select(User)
                .join(GroupMember, GroupMember.user_id == User.id)
                .where(GroupMember.group_id == group.id)
                .order_by(GroupMember.role, User.rep_total.desc())
                .limit(40)
            )
        ).scalars().all()
    )
    events = list(
        (
            await db.execute(
                select(Event)
                .where(Event.group_id == group.id, Event.status == "active", Event.starts_at >= utcnow())
                .order_by(Event.starts_at)
                .limit(5)
            )
        ).scalars().all()
    )

    return templates.TemplateResponse(
        request,
        "groups/detail.html",
        {
            "title": group.name,
            "group": group,
            "membership": membership,
            "posts": posts,
            "has_more": has_more,
            "page": page,
            "members": members,
            "events": events,
            "my_votes": await feed_service.my_votes(db, user.id, "post", [p.id for p in posts]),
        },
    )


@router.post("/{slug}/join")
async def join(request: Request, db: DbDep, user: Verified, slug: str):
    await verify_csrf(request)
    group = (await db.execute(select(Group).where(Group.slug == slug))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404)

    existing = await _membership(db, group.id, user.id)
    if existing:
        if existing.role == "admin" and group.owner_id == user.id:
            raise HTTPException(status_code=400, detail="Hand the group over before leaving it.")
        await db.delete(existing)
        group.member_count = max(0, group.member_count - 1)
    else:
        db.add(GroupMember(group_id=group.id, user_id=user.id))
        group.member_count += 1
    return RedirectResponse(f"/groups/{slug}", status_code=303)


@router.post("/{slug}/settings")
async def settings(
    request: Request,
    db: DbDep,
    user: Verified,
    slug: str,
    description: str = Form(""),
    banner: UploadFile | None = File(None),
):
    await verify_csrf(request)
    group = (await db.execute(select(Group).where(Group.slug == slug))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404)
    membership = await _membership(db, group.id, user.id)
    if not (membership and membership.role in {"admin", "officer"}) and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only group admins can change this.")

    group.description = (description or "").strip()[:2000]
    if banner is not None and banner.filename:
        data = await banner.read()
        if data and media.is_image(data):
            if group.banner_path:
                media.delete_public(group.banner_path)
            group.banner_path = media.save_public(data, subdir="groups")
    return RedirectResponse(f"/groups/{slug}", status_code=303)


@router.post("/{slug}/members/{user_id}/role")
async def set_role(
    request: Request, db: DbDep, user: Verified, slug: str, user_id: int, role: str = Form(...)
):
    await verify_csrf(request)
    group = (await db.execute(select(Group).where(Group.slug == slug))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404)
    if group.owner_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only the group owner can change roles.")
    if role not in {"member", "officer", "admin"}:
        raise HTTPException(status_code=400, detail="Unknown role.")

    membership = await _membership(db, group.id, user_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="They are not a member.")
    membership.role = role
    await notify.push(
        db,
        user_id,
        kind="group",
        title=f"You are now {role} of {group.name}",
        link=f"/groups/{slug}",
    )
    return RedirectResponse(f"/groups/{slug}", status_code=303)


async def recompute_club_reputation(db, group_id: int) -> float:
    """A club's standing is the sum of what its members and events achieved.

    Averaged rather than totalled, so a large dormant club does not outrank a
    small relentless one purely on headcount.
    """
    member_avg = float(
        (
            await db.execute(
                select(func.coalesce(func.avg(User.rep_total), 0))
                .join(GroupMember, GroupMember.user_id == User.id)
                .where(GroupMember.group_id == group_id)
            )
        ).scalar_one()
        or 0
    )
    event_count = int(
        (
            await db.execute(
                select(func.count(Event.id)).where(Event.group_id == group_id, Event.status == "active")
            )
        ).scalar_one()
        or 0
    )
    checkins = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(Event.checkin_count), 0)).where(Event.group_id == group_id)
            )
        ).scalar_one()
        or 0
    )
    score = round(member_avg * 0.4 + event_count * 25 + checkins * 2, 2)
    group = await db.get(Group, group_id)
    if group:
        group.club_rep = score
    return score
