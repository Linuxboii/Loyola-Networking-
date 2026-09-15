"""Rate limiting, vote-ring detection and brigading signals.

All of it runs on Postgres. The PRD's scale target is ~5,000 users, which is
comfortably a single-instance problem — adding Redis would buy nothing here but
another process competing for 1.9 GB of RAM.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DeviceFingerprint,
    HarassmentSignal,
    RateLimit,
    Report,
    Vote,
    VotePairStat,
    utcnow,
)


async def hit_rate_limit(
    db: AsyncSession, bucket: str, limit: int, window: dt.timedelta
) -> tuple[bool, int]:
    """Fixed-window counter. Returns (allowed, remaining).

    Fixed windows can let through up to 2x at a boundary; for "5 posts a day"
    and "3 verification attempts a week" that is irrelevant, and it costs one
    upsert instead of a sorted set.
    """
    now = utcnow()
    seconds = max(1, int(window.total_seconds()))
    epoch = int(now.timestamp()) // seconds * seconds
    window_start = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)

    stmt = (
        pg_insert(RateLimit)
        .values(bucket=bucket, window_start=window_start, count=1)
        .on_conflict_do_update(
            index_elements=[RateLimit.bucket, RateLimit.window_start],
            set_={"count": RateLimit.__table__.c.count + 1},
        )
        .returning(RateLimit.count)
    )
    count = int((await db.execute(stmt)).scalar_one())
    return (count <= limit), max(0, limit - count)


async def peek_rate_limit(db: AsyncSession, bucket: str, window: dt.timedelta) -> int:
    now = utcnow()
    seconds = max(1, int(window.total_seconds()))
    epoch = int(now.timestamp()) // seconds * seconds
    window_start = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
    value = (
        await db.execute(
            select(RateLimit.count).where(
                RateLimit.bucket == bucket, RateLimit.window_start == window_start
            )
        )
    ).scalar_one_or_none()
    return int(value or 0)


async def record_device(db: AsyncSession, user_id: int, fp: str | None) -> None:
    if not fp:
        return
    stmt = (
        pg_insert(DeviceFingerprint)
        .values(fp_hash=fp, user_id=user_id, seen_count=1, last_seen_at=utcnow())
        .on_conflict_do_update(
            index_elements=[DeviceFingerprint.fp_hash, DeviceFingerprint.user_id],
            set_={
                "seen_count": DeviceFingerprint.__table__.c.seen_count + 1,
                "last_seen_at": utcnow(),
            },
        )
    )
    await db.execute(stmt)


async def accounts_on_device(db: AsyncSession, fp: str | None) -> int:
    if not fp:
        return 0
    return int(
        (
            await db.execute(
                select(func.count(func.distinct(DeviceFingerprint.user_id))).where(
                    DeviceFingerprint.fp_hash == fp
                )
            )
        ).scalar_one()
        or 0
    )


async def same_device_vote(db: AsyncSession, voter_fp: str | None, author_id: int) -> bool:
    """Is the voter using a browser that has also signed into the author's
    account? Votes like that get discounted, per PRD 6.3."""
    if not voter_fp:
        return False
    found = (
        await db.execute(
            select(DeviceFingerprint.id).where(
                DeviceFingerprint.fp_hash == voter_fp, DeviceFingerprint.user_id == author_id
            )
        )
    ).first()
    return found is not None


async def prior_votes(db: AsyncSession, voter_id: int, author_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Vote.id)).where(
                    Vote.voter_id == voter_id,
                    Vote.author_id == author_id,
                    Vote.value == 1,
                    Vote.nullified.is_(False),
                )
            )
        ).scalar_one()
        or 0
    )


async def note_vote_pair(db: AsyncSession, voter_id: int, author_id: int) -> None:
    stmt = (
        pg_insert(VotePairStat)
        .values(voter_id=voter_id, author_id=author_id, count=1, last_at=utcnow())
        .on_conflict_do_update(
            index_elements=[VotePairStat.voter_id, VotePairStat.author_id],
            set_={"count": VotePairStat.__table__.c.count + 1, "last_at": utcnow()},
        )
    )
    await db.execute(stmt)


RECIPROCAL_MIN = 6
RECIPROCAL_RATIO = 0.7


async def detect_rings(db: AsyncSession, limit: int = 200) -> list[tuple[int, int]]:
    """Find reciprocal pairs: A upvotes B a lot and B upvotes A a lot.

    This is the cheap 80% of graph analysis. A full cycle search over a
    5,000-node graph is possible but unnecessary — nearly every real ring on a
    campus network is a pair or a triangle, and triangles show up as three
    reciprocal pairs.
    """
    a = VotePairStat.__table__.alias("a")
    b = VotePairStat.__table__.alias("b")
    stmt = (
        select(a.c.voter_id, a.c.author_id, a.c.count, b.c.count)
        .select_from(a.join(b, (a.c.voter_id == b.c.author_id) & (a.c.author_id == b.c.voter_id)))
        .where(a.c.voter_id < a.c.author_id)
        .where(a.c.count >= RECIPROCAL_MIN, b.c.count >= RECIPROCAL_MIN)
        .limit(limit)
    )
    found: list[tuple[int, int]] = []
    for voter_id, author_id, ca, cb in (await db.execute(stmt)).all():
        lo, hi = min(ca, cb), max(ca, cb)
        if hi and lo / hi >= RECIPROCAL_RATIO:
            found.append((int(voter_id), int(author_id)))
    return found


async def mark_pair_flagged(db: AsyncSession, a_id: int, b_id: int) -> None:
    for x, y in ((a_id, b_id), (b_id, a_id)):
        await db.execute(
            VotePairStat.__table__.update()
            .where(VotePairStat.voter_id == x, VotePairStat.author_id == y)
            .values(flagged=True)
        )


# --- targeted harassment ----------------------------------------------------

BRIGADE_WINDOW = dt.timedelta(hours=6)
BRIGADE_DISTINCT_ACTORS = 4


async def note_negative_interaction(
    db: AsyncSession,
    subject_user_id: int,
    actor_user_id: int,
    kind: str,
    source_type: str | None = None,
    source_id: int | None = None,
) -> None:
    if subject_user_id == actor_user_id:
        return
    db.add(
        HarassmentSignal(
            subject_user_id=subject_user_id,
            actor_user_id=actor_user_id,
            kind=kind,
            source_type=source_type,
            source_id=source_id,
        )
    )


async def brigading_subjects(db: AsyncSession) -> list[tuple[int, int]]:
    """Users drawing negative attention from several accounts at once.

    Individual reports miss this entirely: no single downvote is actionable, but
    nine of them inside an hour aimed at one first-year is the pattern that
    matters (PRD 8, targeted-harassment detection).
    """
    since = utcnow() - BRIGADE_WINDOW
    stmt = (
        select(
            HarassmentSignal.subject_user_id,
            func.count(func.distinct(HarassmentSignal.actor_user_id)).label("actors"),
        )
        .where(HarassmentSignal.created_at >= since)
        .group_by(HarassmentSignal.subject_user_id)
        .having(func.count(func.distinct(HarassmentSignal.actor_user_id)) >= BRIGADE_DISTINCT_ACTORS)
    )
    return [(int(uid), int(n)) for uid, n in (await db.execute(stmt)).all()]


async def open_reports_against(db: AsyncSession, user_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Report.id)).where(
                    Report.target_author_id == user_id, Report.status.in_(("open", "in_review"))
                )
            )
        ).scalar_one()
        or 0
    )
