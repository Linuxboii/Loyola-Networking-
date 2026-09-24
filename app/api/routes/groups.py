"""Groups and clubs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.schemas import GroupIn, group_out, user_card
from app.deps import DbDep, Reader, Verified, can
from app.models import Group, GroupMember, User, utcnow
from app.services.bootstrap import slugify

router = APIRouter(prefix="/groups", tags=["api:groups"])


async def _membership(db, group_id: int, user_id: int) -> GroupMember | None:
    return (
        await db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()


@router.get("")
async def index(db: DbDep, user: Reader, kind: str | None = Query(None, pattern="^(interest|club)$")):
    stmt = select(Group).where(Group.status == "active")
    if kind:
        stmt = stmt.where(Group.kind == kind)
    groups = list((await db.execute(stmt.order_by(Group.member_count.desc()))).scalars().all())

    mine = {
        int(gid): role
        for gid, role in (
            await db.execute(
                select(GroupMember.group_id, GroupMember.role).where(GroupMember.user_id == user.id)
            )
        ).all()
    }
    return {"groups": [group_out(g, membership=mine.get(g.id)) for g in groups]}


@router.get("/{slug}")
async def detail(slug: str, db: DbDep, user: Reader):
    group = (await db.execute(select(Group).where(Group.slug == slug))).scalar_one_or_none()
    if group is None:
        raise HTTPException(404, "No such group.")
    membership = await _membership(db, group.id, user.id)
    members = list(
        (
            await db.execute(
                select(User)
                .join(GroupMember, GroupMember.user_id == User.id)
                .where(GroupMember.group_id == group.id)
                .order_by(User.rep_total.desc())
                .limit(50)
            )
        ).scalars().all()
    )
    return {
        **group_out(group, membership=membership.role if membership else None),
        "members": [user_card(m, viewer_is_moderator=user.is_moderator) for m in members],
        "can_post": membership is not None,
    }


@router.post("", status_code=201)
async def create(payload: GroupIn, db: DbDep, user: Verified):
    if not can(user, "create_group"):
        raise HTTPException(403, "Creating groups unlocks at Established standing.")

    name = payload.name.strip()
    slug = slugify(name)
    clash = (await db.execute(select(Group.id).where(Group.slug == slug))).scalar_one_or_none()
    if clash:
        slug = f"{slug}-{utcnow().strftime('%H%M%S')}"

    group = Group(
        slug=slug,
        name=name[:100],
        description=payload.description.strip()[:2000],
        kind=payload.kind,
        owner_id=user.id,
        member_count=1,
    )
    db.add(group)
    await db.flush()
    db.add(GroupMember(group_id=group.id, user_id=user.id, role="admin"))
    await db.flush()
    return group_out(group, membership="admin")


@router.post("/{slug}/join")
async def toggle_membership(slug: str, db: DbDep, user: Verified):
    group = (await db.execute(select(Group).where(Group.slug == slug))).scalar_one_or_none()
    if group is None:
        raise HTTPException(404, "No such group.")

    existing = await _membership(db, group.id, user.id)
    if existing is not None:
        if existing.role == "admin" and group.owner_id == user.id:
            raise HTTPException(400, "Hand the group over before leaving it.")
        await db.delete(existing)
        group.member_count = max(0, group.member_count - 1)
        joined = False
    else:
        db.add(GroupMember(group_id=group.id, user_id=user.id))
        group.member_count += 1
        joined = True
    await db.flush()
    return {"joined": joined, "member_count": group.member_count}
