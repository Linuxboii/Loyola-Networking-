"""Scheduled maintenance.

APScheduler runs in-process. On a single-core box a separate worker process
would double the memory footprint to run jobs that take milliseconds; these are
staggered so they never collide with each other or with a request spike.
"""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import TransparencySnapshot, utcnow
from app.services import antiabuse, moderation, notify, verification
from app.services import reputation as rep

log = logging.getLogger("loyola.jobs")


async def _run(name: str, fn) -> None:
    try:
        async with SessionLocal() as db:
            result = await fn(db)
            await db.commit()
        if result:
            log.info("job %s: %s", name, result)
    except Exception:
        log.exception("job %s failed", name)


async def settle_reputation() -> None:
    await _run("settle_reputation", rep.settle_due)


async def decay_reputation() -> None:
    await _run("decay_reputation", rep.apply_decay)


async def purge_id_artifacts() -> None:
    await _run("purge_id_artifacts", verification.purge_expired_artifacts)


async def queued_local_ocr() -> None:
    """Run the compact local model only when a human-review card is queued."""
    await _run("queued_local_ocr", verification.run_queued_local_ocr)

async def expire_provisional() -> None:
    await _run("expire_provisional", verification.expire_provisional)


async def detect_vote_rings() -> None:
    """Nullify reciprocal voting silently, never announcing detection."""

    async def job(db):
        pairs = await antiabuse.detect_rings(db)
        adjusted = 0
        for a_id, b_id in pairs:
            adjusted += await rep.nullify_votes_between(db, a_id, b_id)
            adjusted += await rep.nullify_votes_between(db, b_id, a_id)
            await antiabuse.mark_pair_flagged(db, a_id, b_id)
        return f"{len(pairs)} pairs, {adjusted} events adjusted" if pairs else None

    await _run("detect_vote_rings", job)


async def detect_brigading() -> None:
    """Open a moderator report when several accounts pile on one person."""

    async def job(db):
        from app.models import Report

        subjects = await antiabuse.brigading_subjects(db)
        opened = 0
        for user_id, actor_count in subjects:
            existing = (
                await db.execute(
                    select(Report).where(
                        Report.target_type == "user",
                        Report.target_id == user_id,
                        Report.source == "auto",
                        Report.status.in_(("open", "in_review")),
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue
            db.add(
                Report(
                    reporter_id=user_id,
                    target_type="user",
                    target_id=user_id,
                    target_author_id=user_id,
                    category="harassment",
                    detail=(
                        f"Automated signal: {actor_count} accounts directed negative activity at this "
                        f"member within 6 hours. Possible brigading."
                    ),
                    source="auto",
                    weight=2.0,
                    status="open",
                )
            )
            opened += 1
        return f"{opened} brigading reports opened" if opened else None

    await _run("detect_brigading", job)


async def weekly_digest() -> None:
    await _run("weekly_digest", notify.build_digest)


async def monthly_transparency() -> None:
    """Publish last month's moderation numbers in-app (PRD 8)."""

    async def job(db):
        today = utcnow().date().replace(day=1)
        last_month_end = dt.datetime.combine(today, dt.time.min, tzinfo=dt.timezone.utc)
        prev = (today - dt.timedelta(days=1)).replace(day=1)
        last_month_start = dt.datetime.combine(prev, dt.time.min, tzinfo=dt.timezone.utc)
        period = prev.strftime("%Y-%m")

        existing = (
            await db.execute(
                select(TransparencySnapshot).where(TransparencySnapshot.period == period)
            )
        ).scalar_one_or_none()
        if existing:
            return None
        data = await moderation.transparency_for(db, last_month_start, last_month_end)
        db.add(TransparencySnapshot(period=period, data=data))
        return f"published {period}"

    await _run("monthly_transparency", job)


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="Asia/Kolkata")

    # Settling is the only frequent job; it is a single indexed UPDATE.
    sched.add_job(settle_reputation, IntervalTrigger(minutes=15), id="settle", max_instances=1)
    sched.add_job(expire_provisional, IntervalTrigger(minutes=30), id="expire_tier1", max_instances=1)
    sched.add_job(detect_vote_rings, CronTrigger(hour=3, minute=10), id="rings")
    sched.add_job(detect_brigading, IntervalTrigger(hours=1), id="brigading", max_instances=1)
    sched.add_job(purge_id_artifacts, CronTrigger(hour=3, minute=30), id="purge_ids")
    sched.add_job(queued_local_ocr, IntervalTrigger(seconds=settings.queued_ocr_poll_seconds), id="queued_local_ocr", max_instances=1)
    sched.add_job(decay_reputation, CronTrigger(hour=4, minute=0), id="decay")
    sched.add_job(weekly_digest, CronTrigger(day_of_week="mon", hour=8, minute=0), id="digest")
    sched.add_job(monthly_transparency, CronTrigger(day=1, hour=5, minute=0), id="transparency")
    return sched
