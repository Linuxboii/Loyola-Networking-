"""Feed assembly and ranking.

PRD 7.1 sets a hard constraint that shapes this whole module: **no
engagement-maximising optimisation**. So the ranking function has no dwell time,
no click-through, no notion of "this kept someone scrolling". It scores
relevance — how recent, how close to you, how well regarded the author — and
chronological order is the default rather than the fallback.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Sequence

from sqlalchemy import Float, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import REP_TIERS, Group, GroupMember, Post, User, Vote, utcnow

PAGE_SIZE = 20


def _tier_rank_case():
    """Author standing as a small ordinal, for a modest ranking nudge."""
    whens = {label: float(i) for i, (label, _t) in enumerate(REP_TIERS)}
    return case(whens, value=User.rep_tier, else_=0.0)


async def visible_group_ids(db: AsyncSession, user_id: int) -> list[int]:
    rows = (
        await db.execute(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
    ).scalars().all()
    return list(rows)


async def fetch(
    db: AsyncSession,
    viewer: User,
    *,
    mode: str = "latest",
    kind: str | None = None,
    tag: str | None = None,
    group_id: int | None = None,
    author_id: int | None = None,
    page: int = 1,
) -> tuple[list[Post], bool]:
    """Return one page of posts plus whether another page exists."""
    page = max(1, page)
    stmt = (
        select(Post)
        .options(joinedload(Post.author))
        .where(Post.status == "active")
    )

    if group_id is not None:
        stmt = stmt.where(Post.group_id == group_id)
    else:
        # Group posts stay inside their group unless you are a member.
        member_of = await visible_group_ids(db, viewer.id)
        if member_of:
            stmt = stmt.where(or_(Post.group_id.is_(None), Post.group_id.in_(member_of)))
        else:
            stmt = stmt.where(Post.group_id.is_(None))

    if kind:
        stmt = stmt.where(Post.kind == kind)
    if tag:
        stmt = stmt.where(Post.tags.any(tag))
    if author_id:
        stmt = stmt.where(Post.author_id == author_id)

    if mode == "foryou":
        now = utcnow()
        age_hours = func.greatest(
            cast(func.extract("epoch", literal(now) - Post.created_at) / 3600.0, Float), 0.0
        )
        # Recency decays smoothly over about three days.
        recency = 1.0 / (1.0 + age_hours / 18.0)

        interests = list(viewer.interests or [])
        tag_match = case((Post.tags.overlap(interests), 1.0), else_=0.0) if interests else literal(0.0)

        same_batch = case((User.batch_year == viewer.batch_year, 1.0), else_=0.0)
        same_dept = case((User.department == viewer.department, 1.0), else_=0.0)

        # Engagement *velocity*, not engagement volume: a post that drew a few
        # considered replies today outranks one that accumulated votes slowly.
        velocity = cast(Post.score + Post.comment_count, Float) / (1.0 + age_hours / 6.0)
        velocity = func.least(velocity, 8.0)

        official = case((Post.is_official.is_(True), 1.2), else_=0.0)

        score = (
            recency * 3.0
            + _tier_rank_case() * 0.35
            + tag_match * 1.4
            + same_batch * 0.7
            + same_dept * 0.5
            + velocity * 0.25
            + official
        )
        stmt = stmt.join(User, User.id == Post.author_id).order_by(score.desc(), Post.created_at.desc())
    else:
        stmt = stmt.order_by(Post.created_at.desc())

    stmt = stmt.limit(PAGE_SIZE + 1).offset((page - 1) * PAGE_SIZE)
    rows = list((await db.execute(stmt)).unique().scalars().all())
    has_more = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_more


async def my_votes(
    db: AsyncSession, user_id: int, target_type: str, ids: Sequence[int]
) -> dict[int, int]:
    """Which of these did I already vote on, and which way?"""
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(Vote.target_id, Vote.value).where(
                Vote.voter_id == user_id,
                Vote.target_type == target_type,
                Vote.target_id.in_(list(ids)),
            )
        )
    ).all()
    return {int(tid): int(val) for tid, val in rows}


async def trending_tags(db: AsyncSession, days: int = 7, limit: int = 12) -> list[tuple[str, int]]:
    since = utcnow() - dt.timedelta(days=days)
    tag = func.unnest(Post.tags).label("tag")
    stmt = (
        select(tag, func.count().label("n"))
        .where(Post.created_at >= since, Post.status == "active")
        .group_by(tag)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [(str(t), int(n)) for t, n in (await db.execute(stmt)).all()]


async def suggested_groups(db: AsyncSession, user: User, limit: int = 5) -> list[Group]:
    joined = await visible_group_ids(db, user.id)
    stmt = select(Group).where(Group.status == "active")
    if joined:
        stmt = stmt.where(Group.id.not_in(joined))
    stmt = stmt.order_by(Group.member_count.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def campus_stats(db: AsyncSession) -> dict[str, Any]:
    from app.models import Answer, Event, Question

    verified = int(
        (await db.execute(select(func.count(User.id)).where(User.tier >= 2))).scalar_one() or 0
    )
    questions = int((await db.execute(select(func.count(Question.id)))).scalar_one() or 0)
    answered = int(
        (
            await db.execute(
                select(func.count(func.distinct(Answer.question_id))).where(Answer.status == "active")
            )
        ).scalar_one()
        or 0
    )
    upcoming = int(
        (
            await db.execute(
                select(func.count(Event.id)).where(
                    Event.starts_at >= utcnow(), Event.status == "active"
                )
            )
        ).scalar_one()
        or 0
    )
    return {
        "verified_members": verified,
        "questions": questions,
        "answered": answered,
        "answer_rate": round(100 * answered / questions) if questions else 0,
        "upcoming_events": upcoming,
    }
