"""Behavioural tests for the reputation engine and its anti-gaming rules.

These drive the service layer directly against a scratch schema. They check the
rules the PRD is specific about — the point values in Appendix A, monthly caps,
diminishing returns, provisional settlement, decay floors, ring nullification —
because these are the parts where a quiet bug looks like nothing at all until
somebody games their way to Pillar.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select, text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Answer,
    Post,
    Question,
    ReputationEvent,
    User,
    Vote,
    VotePairStat,
    utcnow,
)
from app.security import hash_password  # noqa: E402
from app.services import antiabuse  # noqa: E402
from app.services import reputation as rep  # noqa: E402
from app.services.voting import VoteError, cast_vote  # noqa: E402

FAILURES: list[str] = []
COUNT = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global COUNT
    COUNT += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{'' if ok else '  -> ' + detail}")
    if not ok:
        FAILURES.append(label)


async def make_user(db, handle: str, tier_name: str = "Newcomer", age_days: int = 60) -> User:
    user = User(
        handle=handle,
        full_name=handle.title(),
        password_hash=hash_password("test-password-1234"),
        tier=2,
        rep_tier=tier_name,
        created_at=utcnow() - dt.timedelta(days=age_days),
        last_active_at=utcnow(),
    )
    db.add(user)
    await db.flush()
    return user


async def reset(db) -> None:
    for table in (
        "votes", "vote_pair_stats", "reputation_events", "answers", "questions",
        "posts", "comments", "notifications", "harassment_signals", "sessions",
        "device_fingerprints", "rate_limits",
    ):
        await db.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    await db.execute(delete(User).where(User.handle.like("rt%")))
    await db.commit()


async def main() -> int:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        await reset(db)

        author = await make_user(db, "rtauthor")
        voter = await make_user(db, "rtvoter")
        trusted = await make_user(db, "rttrusted", tier_name="Trusted")
        newbie = await make_user(db, "rtnewbie", age_days=1)
        await db.commit()

        # ---------------------------------------------------- point values
        question = Question(author_id=voter.id, title="A test question here", body="Body text")
        db.add(question)
        await db.flush()
        answer = Answer(question_id=question.id, author_id=author.id, body="An answer")
        db.add(answer)
        await db.flush()
        await db.commit()

        result = await cast_vote(db, voter, "answer", answer.id, 1)
        await db.commit()
        await db.refresh(author)
        check(result["score"] == 1, "upvote increments the visible score")
        check(
            abs(author.rep_academic - 12) < 0.01,
            "an upvoted answer is worth 12 (PRD Appendix A: 8 upvotes = +96)",
            f"got {author.rep_academic}",
        )

        # Appendix A: two accepted answers = +40, so acceptance is 20.
        from app.services.voting import accept_answer

        await accept_answer(db, question, answer, voter)
        await db.commit()
        await db.refresh(author)
        check(
            abs(author.rep_academic - 32) < 0.01,
            "accepting an answer adds 20 (12 + 20 = 32)",
            f"got {author.rep_academic}",
        )

        # ------------------------------------------------------- self-vote
        try:
            await cast_vote(db, author, "answer", answer.id, 1)
            check(False, "self-voting is refused")
        except VoteError:
            check(True, "self-voting is refused")
        await db.rollback()
        # A rollback expires every loaded attribute, so anything still held has
        # to be re-read before it is touched again.
        for obj in (author, voter, trusted, newbie, question, answer):
            await db.refresh(obj)

        # --------------------------------------------- diminishing returns
        weights = []
        for n in (0, 1, 3, 10):
            weights.append(rep.vote_weight(voter, n))
        check(
            weights[0] > weights[1] > weights[2] > weights[3],
            "repeat votes from the same person are worth progressively less",
            str(weights),
        )
        check(weights[3] < weights[0] * 0.45, "the tenth repeat vote is worth under half the first", str(weights))

        # ------------------------------------------------------ tier weight
        w_trusted = rep.vote_weight(trusted, 0)
        w_normal = rep.vote_weight(voter, 0)
        w_newbie = rep.vote_weight(newbie, 0)
        check(abs(w_trusted - 1.5) < 0.01, "a Trusted member's vote carries 1.5x", str(w_trusted))
        check(abs(w_normal - 1.0) < 0.01, "an ordinary vote carries 1.0x", str(w_normal))
        check(abs(w_newbie - 0.5) < 0.01, "a brand-new account's vote carries 0.5x", str(w_newbie))

        # ------------------------------------------------------ provisional
        events = list(
            (
                await db.execute(
                    select(ReputationEvent).where(ReputationEvent.user_id == author.id)
                )
            ).scalars().all()
        )
        check(
            all(e.state == "provisional" for e in events),
            "new points start provisional, not settled",
            str([e.state for e in events]),
        )
        check(
            all(e.settles_at is not None for e in events),
            "provisional points carry a settlement time",
        )

        for e in events:
            e.settles_at = utcnow() - dt.timedelta(minutes=1)
        await db.commit()
        settled = await rep.settle_due(db)
        await db.commit()
        check(settled == len(events), f"settlement promotes due points ({settled})")

        # ------------------------------------------------------ monthly cap
        for _ in range(40):
            await rep.award(db, author.id, "answer_upvote", settle_immediately=True)
        await db.commit()
        await db.refresh(author)
        check(
            author.rep_academic <= 300.01,
            "the Academic pillar cannot exceed its 300/month cap",
            f"got {author.rep_academic}",
        )
        capped = list(
            (
                await db.execute(
                    select(ReputationEvent).where(
                        ReputationEvent.user_id == author.id, ReputationEvent.capped_amount > 0
                    )
                )
            ).scalars().all()
        )
        check(len(capped) > 0, "the ledger records what the cap withheld, rather than hiding it")

        # ------------------------------------------------------------ tiers
        check(rep.tier_for(0) == "Newcomer", "0 points is Newcomer")
        check(rep.tier_for(99) == "Newcomer", "99 points is still Newcomer")
        check(rep.tier_for(100) == "Contributor", "100 points is Contributor")
        check(rep.tier_for(334) == "Contributor", "Appendix A's worked total of 334 lands on Contributor")
        check(rep.tier_for(500) == "Established", "500 points is Established")
        check(rep.tier_for(1500) == "Trusted", "1500 points is Trusted")
        check(rep.tier_for(4000) == "Pillar", "4000 points is Pillar")

        # ------------------------------------------------- velocity spike hold
        # The author has just taken 40 awards in seconds while the cap was being
        # tested, which is exactly the shape of farming. The next award should be
        # parked rather than paid.
        spike_event = await rep.award(db, author.id, "post_upvote", source_type="post", source_id=99999)
        await db.commit()
        check(
            spike_event is not None and spike_event.state == "held",
            "a sudden spike above the user's baseline holds the points for review",
            spike_event.state if spike_event else "no event",
        )
        held_total = float(
            (
                await db.execute(
                    select(ReputationEvent.points).where(ReputationEvent.id == spike_event.id)
                )
            ).scalar_one()
        )
        await db.refresh(author)
        check(
            held_total > 0 and author.rep_participation == 0,
            "held points are recorded in the ledger but do not count towards the score",
            f"held {held_total}, participation {author.rep_participation}",
        )

        # ------------------------------------------- removal withdraws points
        # A separate, calm account, so the velocity rule does not mask the result.
        poster = await make_user(db, "rtposter")
        await db.commit()
        post = Post(author_id=poster.id, kind="text", body="A post")
        db.add(post)
        await db.flush()
        await db.commit()

        before = poster.rep_participation
        await rep.award(db, poster.id, "post_upvote", source_type="post", source_id=post.id)
        await db.commit()
        await db.refresh(poster)
        gained = poster.rep_participation - before
        check(gained > 0, "an upvoted post earns participation points", str(gained))

        await rep.void_events(db, source_type="post", source_id=post.id, reason_note="removed")
        await db.commit()
        await db.refresh(poster)
        check(
            abs(poster.rep_participation - before) < 0.01,
            "removing content withdraws the points it earned",
            f"{before} -> {poster.rep_participation}",
        )

        # --------------------------------------------- conduct can go negative
        fresh = await make_user(db, "rtconduct")
        await db.commit()
        await rep.award(db, fresh.id, "report_upheld", settle_immediately=True)
        await db.commit()
        await db.refresh(fresh)
        check(fresh.rep_conduct < 0, "an upheld report pushes conduct negative", str(fresh.rep_conduct))
        for _ in range(10):
            await rep.award(db, fresh.id, "plagiarism_verified", settle_immediately=True)
        await db.commit()
        await db.refresh(fresh)
        check(
            fresh.rep_conduct >= -100.01,
            "conduct is floored at -100 so one bad week cannot erase a year",
            str(fresh.rep_conduct),
        )

        # ---------------------------------------------------- freeze on abuse
        for _ in range(2):
            await rep.award(db, fresh.id, "harassment_upheld", settle_immediately=True)
        await db.commit()
        frozen = await rep.freeze_if_needed(db, fresh.id)
        await db.commit()
        await db.refresh(fresh)
        check(frozen and fresh.rep_frozen, "two upheld harassment reports freeze the score")

        # ------------------------------------------------------ ring detection
        a = await make_user(db, "rtringa")
        b = await make_user(db, "rtringb")
        await db.commit()
        for _ in range(8):
            await antiabuse.note_vote_pair(db, a.id, b.id)
            await antiabuse.note_vote_pair(db, b.id, a.id)
        await db.commit()
        pairs = await antiabuse.detect_rings(db)
        check(
            any({x, y} == {a.id, b.id} for x, y in pairs),
            "a reciprocal voting pair is detected",
            str(pairs),
        )

        await rep.award(db, b.id, "answer_upvote", actor_id=a.id, source_type="answer_vote", source_id=1)
        await db.commit()
        await db.refresh(b)
        before_b = b.rep_academic
        nulled = await rep.nullify_votes_between(db, a.id, b.id)
        await db.commit()
        await db.refresh(b)
        check(nulled > 0 and b.rep_academic < before_b, "ring points are quietly withdrawn", str(nulled))

        # -------------------------------------------------------------- decay
        idler = await make_user(db, "rtidle")
        idler.last_active_at = utcnow() - dt.timedelta(days=45)
        await db.commit()
        await rep.award(db, idler.id, "interview_experience", settle_immediately=True)
        await db.commit()
        await db.refresh(idler)
        before_decay = idler.rep_total
        idler.last_active_at = utcnow() - dt.timedelta(days=45)
        await db.commit()
        await rep.apply_decay(db)
        await db.commit()
        await db.refresh(idler)
        check(
            idler.rep_total < before_decay,
            "an inactive account decays",
            f"{before_decay} -> {idler.rep_total}",
        )
        check(
            idler.rep_total >= rep.tier_floor(before_decay),
            "decay is floored at the tier entry threshold",
            f"{idler.rep_total} vs floor {rep.tier_floor(before_decay)}",
        )

        # --------------------------------------------------------- total = sum
        await db.refresh(author)
        pillar_sum = (
            author.rep_academic + author.rep_build + author.rep_service
            + author.rep_participation + author.rep_conduct
        )
        check(
            abs(author.rep_total - pillar_sum) < 0.01,
            "the total is the plain sum of the pillars, matching Appendix A",
            f"{author.rep_total} vs {pillar_sum}",
        )

        await reset(db)

    await engine.dispose()
    print(f"\n{COUNT - len(FAILURES)}/{COUNT} reputation checks passed")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
