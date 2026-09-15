"""Casting a vote — the single place every anti-gaming rule is applied.

You rate the artifact; reputation accrues to its author (PRD 6.1). There is no
path in this codebase that rates a person directly, and that is enforced here by
construction: ``cast_vote`` only accepts a content target.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Answer, Comment, Post, Question, User, Vote
from app.services import antiabuse
from app.services import reputation as rep

TARGETS: dict[str, Any] = {
    "post": Post,
    "comment": Comment,
    "question": Question,
    "answer": Answer,
}

# Which ledger reason an upvote on each surface earns its author.
UPVOTE_REASON = {
    "post": "post_upvote",
    "comment": "comment_upvote",
    "question": "question_upvote",
    "answer": "answer_upvote",
}


class VoteError(Exception):
    pass


async def cast_vote(
    db: AsyncSession,
    voter: User,
    target_type: str,
    target_id: int,
    value: int,
    *,
    device_fp: str | None = None,
) -> dict[str, Any]:
    """Apply or toggle a vote. Returns the new score and the voter's state."""
    model = TARGETS.get(target_type)
    if model is None or value not in (1, -1):
        raise VoteError("Unknown vote target.")

    target = await db.get(model, target_id)
    if target is None or getattr(target, "status", "active") != "active":
        raise VoteError("That content is no longer available.")

    author_id = getattr(target, "author_id", None)
    if author_id is None:
        raise VoteError("That content has no author.")
    if author_id == voter.id:
        # Self-voting is impossible, not merely discouraged (PRD 6.3). The
        # database agrees: votes carry a CHECK (voter_id <> author_id).
        raise VoteError("You cannot vote on your own contribution.")

    existing = (
        await db.execute(
            select(Vote).where(
                Vote.voter_id == voter.id,
                Vote.target_type == target_type,
                Vote.target_id == target_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.value == value:
            # Toggle off.
            target.score = max(0, (target.score or 0) - existing.value) if existing.value > 0 else (target.score or 0) + 1
            await db.delete(existing)
            await rep.void_events(
                db, source_type=f"{target_type}_vote", source_id=existing.id, reason_note="vote withdrawn"
            )
            await db.flush()
            return {"score": target.score, "my_vote": 0}
        # Flip direction.
        target.score = (target.score or 0) + (value - existing.value)
        existing.value = value
        await db.flush()
        if value == -1:
            await antiabuse.note_negative_interaction(
                db, author_id, voter.id, "downvote", target_type, target_id
            )
        return {"score": target.score, "my_vote": value}

    prior = await antiabuse.prior_votes(db, voter.id, author_id)
    weight = rep.vote_weight(voter, prior)

    # A vote from a browser that has also signed into the author's account is
    # almost certainly the same person. Counted for display, worth nothing.
    if await antiabuse.same_device_vote(db, device_fp, author_id):
        weight = 0.0

    vote = Vote(
        voter_id=voter.id,
        author_id=author_id,
        target_type=target_type,
        target_id=target_id,
        value=value,
        weight=weight,
        device_fp=device_fp,
    )
    db.add(vote)
    target.score = (target.score or 0) + value
    await db.flush()

    if value == 1:
        await antiabuse.note_vote_pair(db, voter.id, author_id)
        if weight > 0:
            await rep.award(
                db,
                author_id,
                UPVOTE_REASON[target_type],
                multiplier=weight,
                source_type=f"{target_type}_vote",
                source_id=vote.id,
                actor_id=voter.id,
                detail=f"Upvote on your {target_type}",
            )
    else:
        await antiabuse.note_negative_interaction(
            db, author_id, voter.id, "downvote", target_type, target_id
        )

    return {"score": target.score, "my_vote": value}


async def accept_answer(db: AsyncSession, question: Question, answer: Answer, actor: User) -> None:
    """Mark the answer that solved it. Only the asker or a moderator may."""
    if question.author_id != actor.id and not actor.is_moderator:
        raise VoteError("Only the person who asked can accept an answer.")
    if answer.question_id != question.id:
        raise VoteError("That answer belongs to a different question.")

    if question.accepted_answer_id and question.accepted_answer_id != answer.id:
        previous = await db.get(Answer, question.accepted_answer_id)
        if previous:
            previous.is_accepted = False
            await rep.void_events(
                db, source_type="answer_accept", source_id=previous.id, reason_note="acceptance moved"
            )

    answer.is_accepted = True
    question.accepted_answer_id = answer.id
    await db.flush()

    if answer.author_id != actor.id:
        await rep.award(
            db,
            answer.author_id,
            "answer_accepted",
            source_type="answer_accept",
            source_id=answer.id,
            actor_id=actor.id,
            detail=f"Accepted answer: {question.title[:80]}",
        )
