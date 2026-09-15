"""In-app notifications and the weekly Opportunity Digest.

There is no FCM here: this is a web app, and push would mean service-worker
plumbing plus a Firebase dependency for a v1 that nobody has installed yet.
Notifications are in-app, and the digest is a notification rather than an email
because we have no verified address to send to.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Event,
    Notification,
    Opportunity,
    Question,
    User,
    utcnow,
)


async def push(
    db: AsyncSession,
    user_id: int,
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str = "/",
    skip_if_self: int | None = None,
) -> Notification | None:
    if skip_if_self is not None and skip_if_self == user_id:
        return None
    note = Notification(user_id=user_id, kind=kind, title=title[:180], body=body[:400], link=link[:300])
    db.add(note)
    return note


async def push_many(
    db: AsyncSession, user_ids: Iterable[int], *, kind: str, title: str, body: str = "", link: str = "/"
) -> int:
    count = 0
    for uid in set(user_ids):
        db.add(Notification(user_id=uid, kind=kind, title=title[:180], body=body[:400], link=link[:300]))
        count += 1
    return count


async def unread_count(db: AsyncSession, user_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Notification.id)).where(
                    Notification.user_id == user_id, Notification.read_at.is_(None)
                )
            )
        ).scalar_one()
        or 0
    )


async def recent(db: AsyncSession, user_id: int, limit: int = 50) -> list[Notification]:
    rows = (
        await db.execute(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def mark_all_read(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )


async def build_digest(db: AsyncSession) -> int:
    """Weekly summary of what a member actually cares about.

    Scoped to their declared interests so it stays useful rather than becoming
    another broadcast people mute.
    """
    since = utcnow() - dt.timedelta(days=7)
    soon = utcnow() + dt.timedelta(days=7)

    new_opportunities = list(
        (
            await db.execute(
                select(Opportunity).where(Opportunity.created_at >= since, Opportunity.status == "active")
            )
        ).scalars().all()
    )
    upcoming_events = list(
        (
            await db.execute(
                select(Event).where(
                    Event.starts_at.between(utcnow(), soon), Event.status == "active"
                )
            )
        ).scalars().all()
    )
    unanswered = list(
        (
            await db.execute(
                select(Question).where(
                    Question.created_at >= since, Question.answer_count == 0, Question.status == "active"
                )
            )
        ).scalars().all()
    )

    if not (new_opportunities or upcoming_events or unanswered):
        return 0

    users = list(
        (
            await db.execute(select(User).where(User.tier >= 2, User.status == "active"))
        ).scalars().all()
    )

    sent = 0
    for user in users:
        interests = {i.lower() for i in (user.interests or [])}
        mine = [
            q
            for q in unanswered
            if not interests or interests & {t.lower() for t in (q.tags or [])}
        ][:5]
        opps = [
            o
            for o in new_opportunities
            if not interests or not o.skills or interests & {s.lower() for s in (o.skills or [])}
        ][:5]

        bits = []
        if opps:
            bits.append(f"{len(opps)} new opportunit{'y' if len(opps) == 1 else 'ies'}")
        if upcoming_events:
            bits.append(f"{len(upcoming_events)} event{'s' if len(upcoming_events) != 1 else ''} this week")
        if mine:
            bits.append(f"{len(mine)} unanswered question{'s' if len(mine) != 1 else ''} in your areas")
        if not bits:
            continue

        db.add(
            Notification(
                user_id=user.id,
                kind="digest",
                title="Your weekly campus digest",
                body=", ".join(bits).capitalize() + ".",
                link="/digest",
            )
        )
        sent += 1
    return sent
