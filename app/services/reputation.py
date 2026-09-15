"""The reputation engine.

Rules, straight from the PRD, and the reasoning behind the ones that needed a
judgement call:

* **Event sourced.** Every point-affecting action appends an immutable
  ``ReputationEvent``. ``User.rep_*`` is a cache recomputed from the ledger, so
  disputes, retroactive rule changes and the user-facing ledger all stay honest.
* **Totals are a plain sum of the pillars.** The PRD gives pillar *weights*
  (30/25/20/15/10) and monthly *caps* (300/250/200/150/100) — the caps are those
  weights, already applied. Appendix A confirms it: its worked example adds the
  raw pillar points to 334. Weighting a second time at the total would
  double-count, so the weights live in the caps and in the radar chart.
* **Points are calibrated against Appendix A.** Eight upvoted answers = +96, so
  an answer upvote is 12; two accepted answers = +40, so acceptance is 20; five
  endorsements = +35, so an endorsement is 7. Those numbers are not invented.
* **Provisional for 48 hours.** Points settle on a timer, which is what makes
  velocity holds and silent ring-nullification possible without clawing back
  points a user has already seen as final.
"""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    PILLAR_MONTHLY_CAPS,
    REP_TIERS,
    Notification,
    ReputationEvent,
    User,
    utcnow,
)

# --- point table ------------------------------------------------------------
# reason -> (pillar, points). Calibrated against PRD Appendix A.
POINTS: dict[str, tuple[str, float]] = {
    # Academic Helpfulness
    "answer_upvote": ("academic", 12),
    "answer_accepted": ("academic", 20),
    "question_upvote": ("academic", 5),
    "resource_upvote": ("academic", 3),
    "resource_published": ("academic", 8),
    "interview_experience": ("academic", 40),
    # Build & Ship
    "project_published": ("build", 25),
    "project_artifact_verified": ("build", 35),
    "skill_endorsed": ("build", 7),
    "project_upvote": ("build", 4),
    # Community Service
    "event_volunteered": ("service", 50),
    "event_organised": ("service", 40),
    "event_attended": ("service", 5),
    "mentor_session_completed": ("service", 20),
    "study_room_hosted": ("service", 8),
    "lostfound_resolved": ("service", 10),
    # Constructive Participation
    "post_upvote": ("participation", 2),
    "comment_upvote": ("participation", 2),
    "poll_created": ("participation", 3),
    "answer_posted": ("participation", 2),
    # Trust & Conduct
    "verified_tier2": ("conduct", 15),
    "account_month": ("conduct", 1),
    "role_verified": ("conduct", 10),
    # Negative signals
    "content_removed": ("conduct", -15),
    "report_upheld": ("conduct", -25),
    "plagiarism_verified": ("conduct", -50),
    "harassment_upheld": ("conduct", -40),
    "vote_retracted": ("participation", 0),
}

NEGATIVE_REASONS = {r for r, (_p, v) in POINTS.items() if v < 0}


def tier_for(total: float) -> str:
    name = REP_TIERS[0][0]
    for label, threshold in REP_TIERS:
        if total >= threshold:
            name = label
    return name


def tier_floor(total: float) -> float:
    floor = 0
    for _label, threshold in REP_TIERS:
        if total >= threshold:
            floor = threshold
    return float(floor)


def tier_index(name: str) -> int:
    for i, (label, _t) in enumerate(REP_TIERS):
        if label == name:
            return i
    return 0


def next_tier(total: float) -> tuple[str, float] | None:
    for label, threshold in REP_TIERS:
        if total < threshold:
            return label, float(threshold)
    return None


# --- caps -------------------------------------------------------------------


async def _month_earned(db: AsyncSession, user_id: int, pillar: str) -> float:
    """Positive points already booked in this calendar month for one pillar."""
    now = utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(ReputationEvent.points), 0)).where(
        ReputationEvent.user_id == user_id,
        ReputationEvent.pillar == pillar,
        ReputationEvent.created_at >= start,
        ReputationEvent.state.in_(("provisional", "settled")),
        ReputationEvent.points > 0,
    )
    return float((await db.execute(stmt)).scalar_one() or 0)


# --- anti-gaming ------------------------------------------------------------


async def _velocity_hold(db: AsyncSession, user_id: int, incoming: float) -> bool:
    """A sudden spike over the user's own 30-day baseline parks the points for
    review rather than paying them out (PRD 6.3)."""
    now = utcnow()
    day_start = now - dt.timedelta(days=1)
    month_start = now - dt.timedelta(days=30)

    today = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(ReputationEvent.points), 0)).where(
                    ReputationEvent.user_id == user_id,
                    ReputationEvent.created_at >= day_start,
                    ReputationEvent.points > 0,
                )
            )
        ).scalar_one()
        or 0
    )
    month = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(ReputationEvent.points), 0)).where(
                    ReputationEvent.user_id == user_id,
                    ReputationEvent.created_at >= month_start,
                    ReputationEvent.points > 0,
                )
            )
        ).scalar_one()
        or 0
    )
    baseline = month / 30.0
    # A brand-new account has no baseline; give it a generous free allowance so
    # an enthusiastic first day is not mistaken for farming.
    if baseline < 3:
        return (today + incoming) > 220
    return (today + incoming) > baseline * 5.0


def vote_weight(voter: User, prior_votes_same_author: int) -> float:
    """Tier weighting plus logarithmic diminishing returns (PRD 6.3).

    The Nth upvote from the same voter on the same author is worth less, which
    is what makes vote-trading rings unprofitable without having to detect them.
    """
    weight = 1.0
    idx = tier_index(voter.rep_tier)
    if idx >= tier_index("Trusted"):
        weight *= 1.5
    age = utcnow() - (voter.created_at or utcnow())
    if age < dt.timedelta(days=14) and idx == 0:
        weight *= 0.5
    if prior_votes_same_author > 0:
        weight *= 1.0 / (1.0 + math.log1p(prior_votes_same_author))
    return round(max(0.05, min(1.5, weight)), 4)


# --- awarding ---------------------------------------------------------------


async def award(
    db: AsyncSession,
    user_id: int,
    reason: str,
    *,
    multiplier: float = 1.0,
    source_type: str | None = None,
    source_id: int | None = None,
    actor_id: int | None = None,
    detail: str | None = None,
    settle_immediately: bool = False,
) -> ReputationEvent | None:
    """Append one ledger row, applying caps and holds. Returns None if the
    action carries no points or the user's reputation is frozen."""
    if reason not in POINTS:
        return None

    pillar, base = POINTS[reason]
    raw = base * multiplier
    if raw == 0:
        return None

    user = await db.get(User, user_id)
    if user is None:
        return None
    # A frozen account still accrues penalties — it just cannot earn.
    if user.rep_frozen and raw > 0:
        return None

    capped_amount = 0.0
    points = raw
    if raw > 0:
        cap = PILLAR_MONTHLY_CAPS.get(pillar, 10_000)
        earned = await _month_earned(db, user_id, pillar)
        room = max(0.0, cap - earned)
        if raw > room:
            capped_amount = raw - room
            points = room
        if points <= 0:
            # Cap reached: still record it, at zero, so the ledger explains why.
            points = 0.0
    else:
        # The conduct pillar is the only one allowed to go negative, and it is
        # bounded at -100 so a bad week cannot erase a year of contribution.
        floor = -float(PILLAR_MONTHLY_CAPS.get(pillar, 100))
        current = getattr(user, f"rep_{pillar}", 0.0)
        if current + points < floor:
            points = min(0.0, floor - current)

    state = "settled" if (settle_immediately or points <= 0) else "provisional"
    settles_at = None
    if state == "provisional":
        settles_at = utcnow() + dt.timedelta(hours=settings.provisional_settle_hours)
        if await _velocity_hold(db, user_id, points):
            state = "held"

    event = ReputationEvent(
        user_id=user_id,
        pillar=pillar,
        points=Decimal(str(round(points, 2))),
        raw_points=Decimal(str(round(raw, 2))),
        reason=reason,
        detail=detail,
        source_type=source_type,
        source_id=source_id,
        actor_id=actor_id,
        state=state,
        settles_at=settles_at,
        capped_amount=Decimal(str(round(capped_amount, 2))),
    )
    db.add(event)
    await db.flush()
    await recompute(db, user_id)
    return event


async def void_events(
    db: AsyncSession,
    *,
    source_type: str,
    source_id: int,
    reason_note: str = "source removed",
) -> int:
    """Withdraw points tied to content that was later removed (PRD 6.2:
    participation decays if content is removed)."""
    rows = (
        await db.execute(
            select(ReputationEvent).where(
                ReputationEvent.source_type == source_type,
                ReputationEvent.source_id == source_id,
                ReputationEvent.state.in_(("provisional", "settled", "held")),
            )
        )
    ).scalars().all()
    users: set[int] = set()
    for ev in rows:
        ev.state = "void"
        ev.detail = (ev.detail or "") + f" [{reason_note}]"
        users.add(ev.user_id)
    await db.flush()
    for uid in users:
        await recompute(db, uid)
    return len(rows)


async def nullify_votes_between(db: AsyncSession, voter_id: int, author_id: int) -> int:
    """Silently strip the value of a reciprocal pair's votes.

    The PRD is explicit that detection is never announced — telling a ring it was
    caught just teaches it to be subtler.
    """
    rows = (
        await db.execute(
            select(ReputationEvent).where(
                ReputationEvent.user_id == author_id,
                ReputationEvent.actor_id == voter_id,
                ReputationEvent.state.in_(("provisional", "held")),
            )
        )
    ).scalars().all()
    for ev in rows:
        ev.state = "void"
        ev.detail = (ev.detail or "") + " [adjusted]"
    if rows:
        await db.flush()
        await recompute(db, author_id)
    return len(rows)


# --- recomputation ----------------------------------------------------------


async def recompute(db: AsyncSession, user_id: int) -> float:
    """Rebuild the cached score from the ledger. Cheap: one grouped query."""
    stmt = (
        select(ReputationEvent.pillar, func.coalesce(func.sum(ReputationEvent.points), 0))
        .where(ReputationEvent.user_id == user_id, ReputationEvent.state.in_(("provisional", "settled")))
        .group_by(ReputationEvent.pillar)
    )
    totals = {pillar: float(value) for pillar, value in (await db.execute(stmt)).all()}

    user = await db.get(User, user_id)
    if user is None:
        return 0.0

    for pillar in ("academic", "build", "service", "participation", "conduct"):
        setattr(user, f"rep_{pillar}", round(totals.get(pillar, 0.0), 2))

    total = round(sum(totals.values()), 2)
    old_tier = user.rep_tier
    user.rep_total = total
    user.rep_tier = tier_for(total)

    if user.rep_tier != old_tier and tier_index(user.rep_tier) > tier_index(old_tier):
        db.add(
            Notification(
                user_id=user.id,
                kind="reputation",
                title=f"You reached {user.rep_tier}",
                body=_unlock_blurb(user.rep_tier),
                link="/me/reputation",
            )
        )
    await db.flush()
    return total


def _unlock_blurb(tier: str) -> str:
    return {
        "Contributor": "Polls, project boards, unlimited posting and skill search are now open to you.",
        "Established": "You can now create interest groups and host events.",
        "Trusted": "The moderation queue and verified-answer marking are now open to you.",
        "Pillar": "You are eligible to stand for elected moderator.",
    }.get(tier, "New abilities unlocked.")


# --- scheduled maintenance --------------------------------------------------


async def settle_due(db: AsyncSession) -> int:
    """Move provisional points to settled once their 48 hours are up."""
    now = utcnow()
    due = (
        await db.execute(
            select(ReputationEvent).where(
                ReputationEvent.state == "provisional",
                ReputationEvent.settles_at.is_not(None),
                ReputationEvent.settles_at <= now,
            )
        )
    ).scalars().all()
    for ev in due:
        ev.state = "settled"
    if due:
        await db.flush()
    return len(due)


async def apply_decay(db: AsyncSession) -> int:
    """2% per month of inactivity, floored at the user's tier entry threshold.

    Implemented as a ledger entry rather than an edit, so the decay is visible
    in the user's own history instead of points quietly evaporating.
    """
    cutoff = utcnow() - dt.timedelta(days=30)
    users = (
        await db.execute(
            select(User).where(
                User.last_active_at < cutoff,
                User.rep_total > 0,
                User.status == "active",
            )
        )
    ).scalars().all()

    touched = 0
    for user in users:
        floor = tier_floor(user.rep_total)
        target = user.rep_total * (1 - settings.decay_percent_per_idle_month / 100.0)
        if target < floor:
            target = floor
        delta = round(target - user.rep_total, 2)
        if delta >= -0.01:
            continue
        # Attribute decay to the largest pillar so the radar chart stays honest.
        pillar = max(
            ("academic", "build", "service", "participation"),
            key=lambda p: getattr(user, f"rep_{p}", 0.0),
        )
        db.add(
            ReputationEvent(
                user_id=user.id,
                pillar=pillar,
                points=Decimal(str(delta)),
                raw_points=Decimal(str(delta)),
                reason="inactivity_decay",
                detail="2% monthly decay while inactive",
                state="settled",
            )
        )
        await db.flush()
        await recompute(db, user.id)
        touched += 1
    return touched


async def freeze_if_needed(db: AsyncSession, user_id: int) -> bool:
    """Two upheld harassment reports freeze the score pending review (PRD 6.3)."""
    count = float(
        (
            await db.execute(
                select(func.count(ReputationEvent.id)).where(
                    ReputationEvent.user_id == user_id,
                    ReputationEvent.reason == "harassment_upheld",
                )
            )
        ).scalar_one()
        or 0
    )
    if count >= 2:
        await db.execute(update(User).where(User.id == user_id).values(rep_frozen=True))
        db.add(
            Notification(
                user_id=user_id,
                kind="moderation",
                title="Your reputation is frozen pending review",
                body="Two harassment reports against you were upheld. A moderator will review your account.",
                link="/moderation/appeals",
            )
        )
        await db.flush()
        return True
    return False


async def ledger(db: AsyncSession, user_id: int, limit: int = 100, offset: int = 0) -> list[ReputationEvent]:
    rows = (
        await db.execute(
            select(ReputationEvent)
            .where(ReputationEvent.user_id == user_id)
            .order_by(ReputationEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows)


def pillar_breakdown(user: User) -> list[dict[str, Any]]:
    """Radar-chart input: raw points plus how full this month's cap is."""
    out = []
    for pillar, cap in PILLAR_MONTHLY_CAPS.items():
        value = float(getattr(user, f"rep_{pillar}", 0.0) or 0.0)
        out.append(
            {
                "pillar": pillar,
                "value": value,
                "cap": cap,
                "ratio": max(0.0, min(1.0, abs(value) / max(cap, 1))),
            }
        )
    return out
