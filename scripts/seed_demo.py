"""Populate a database with a believable campus, for demos and pilots.

An empty social network demos badly — every screen is an empty state and nobody
can tell whether the ranking, the reputation ledger or the Q&A archive actually
work. This builds a plausible week of activity: thirty students across five
departments, groups, posts, questions with accepted answers, events with RSVPs,
votes that produce real reputation, and the notifications those actions raise.

    python scripts/seed_demo.py              # add the demo campus
    python scripts/seed_demo.py --reset      # remove it first, then rebuild

Every account it creates has the handle prefix ``demo_`` and the password
``campus2026``, so the whole set is easy to find and easy to remove. Never run
this against a database with real students in it.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import random
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Answer,
    Comment,
    Event,
    Group,
    GroupMember,
    Notification,
    Post,
    Question,
    ReputationEvent,
    Rsvp,
    Skill,
    User,
    UserSkill,
    Vote,
    utcnow,
)
from app.security import hash_password  # noqa: E402
from app.services import reputation as rep  # noqa: E402
from app.services.bootstrap import ensure_skill, slugify  # noqa: E402

PREFIX = "demo_"
PASSWORD = "campus2026"

DEPARTMENTS = [
    ("Computer Science", "B.Sc. Computer Science", "CS"),
    ("Electronics", "B.Sc. Electronics", "EC"),
    ("Commerce", "B.Com. Honours", "CO"),
    ("Biotechnology", "B.Sc. Biotechnology", "BT"),
    ("Visual Communication", "B.A. Visual Communication", "VC"),
]

FIRST_NAMES = [
    "Aarav", "Ananya", "Rohan", "Meera", "Karthik", "Divya", "Sameer", "Nisha",
    "Vikram", "Priya", "Arjun", "Sneha", "Rahul", "Ishita", "Adithya", "Kavya",
    "Nikhil", "Tara", "Joseph", "Fatima", "Sanjay", "Lakshmi", "Imran", "Neha",
    "Gautam", "Riya", "Pranav", "Anjali", "Vivek", "Sarah",
]
LAST_NAMES = [
    "Menon", "Reddy", "Sharma", "Iyer", "Fernandes", "Nair", "Rao", "Kapoor",
    "Das", "Pillai", "Verma", "Joseph", "Khan", "Bose", "Mathew",
]

SKILLS = [
    "Flutter", "Python", "Circuit design", "Public speaking", "Data analysis",
    "Graphic design", "Photography", "Accounting", "Lab technique", "Event management",
    "Machine learning", "Content writing", "Video editing", "Embedded C",
]

GROUPS = [
    ("Coding Club", "club", "We meet Thursdays in Lab 3. Anyone can come, bring a laptop or don't."),
    ("Placement Prep", "interest", "Aptitude drills, mock interviews and honest interview experiences."),
    ("Robotics Society", "club", "Building for the inter-college robotics meet. Hardware and firmware both."),
    ("Photography Circle", "interest", "Campus walks on Saturdays. Film and digital, no gatekeeping."),
    ("NSS Volunteers", "club", "Village outreach, blood donation drives and the annual camp."),
]

POSTS = [
    ("Library is open till 10pm during study week",
     "Confirmed with the desk this morning. Second floor stays open, the reading room shuts at 8 as usual. "
     "Bring your ID, they are checking at the gate.",
     ["library", "exams"], "text"),
    ("Lost: blue Casio calculator near Block B",
     "Left it on the bench outside the Electronics lab yesterday around 4pm. It has a sticker of a cat on the back. "
     "Please message me if you picked it up.",
     ["lostfound"], "text"),
    ("Anyone else's semester fee receipt not showing on the portal?",
     "Paid on Monday, still says pending. The accounts office says give it 48 hours but I want to know if this is "
     "just me before I go stand in that queue again.",
     ["fees", "admin"], "text"),
    ("Notes: Data Structures unit 3 (trees and traversals)",
     "Wrote these up while revising. Covers BST insertion/deletion with worked examples and the three traversals "
     "with the recursion traced out line by line. Corrections welcome.",
     ["notes", "datastructures"], "text"),
    ("Internship at a Hyderabad startup — my honest experience",
     "Two months, front-end work, ₹15k stipend. Good mentor, chaotic process, learned more in week one than a "
     "whole semester of theory. Happy to answer questions about the interview.",
     ["placements", "internship"], "text"),
    ("Robotics meet registration closes Friday",
     "Teams of four, one faculty signature needed. We have two slots left on our team if anyone with embedded "
     "experience wants in.",
     ["robotics", "competition"], "text"),
    ("Canteen prices went up again",
     "Meals are ₹10 more since Monday. Not complaining about ₹10, complaining that nobody put up a notice.",
     ["canteen"], "text"),
    ("Free seats in the Thursday Flutter session",
     "Coding Club is running an intro to Flutter this Thursday, 4pm, Lab 3. No prior mobile experience needed, "
     "bring a laptop with Android Studio already installed or you will spend the hour downloading.",
     ["codingclub", "flutter"], "text"),
    ("Blood donation camp — 40 donors needed",
     "NSS is organising with the city blood bank on the 24th. If you have donated in the last three months you are "
     "not eligible. Sign-up sheet is with the NSS office.",
     ["nss", "service"], "text"),
    ("Which elective is actually worth taking next semester?",
     "Looking at Machine Learning vs Digital Marketing. Heard ML is heavy on maths and the DM one is mostly "
     "assignments. Anyone who has done either, what was it really like?",
     ["electives"], "text"),
]

QUESTIONS = [
    ("How is the internal assessment split for third semester CS?",
     "The syllabus PDF says 40 marks internal but does not break it down. Is it two tests plus assignments, or "
     "does the lab record count separately?",
     "Computer Science", ["exams", "internals"],
     "It is 20 for the two class tests (best of two, not average), 10 for assignments and 10 for the lab record. "
     "The lab record part is what people forget — it gets checked in the last week and it is the easiest 10 marks "
     "in the semester."),
    ("Does the library allow laptop use in the reading room?",
     "I keep seeing people with laptops but also a sign saying no electronic devices. Which is it?",
     None, ["library"],
     "Laptops are fine in the reading room, phones are not. The sign is old — it predates the wifi. The only "
     "actual rule they enforce is no charging from the wall sockets in the aisle."),
    ("What paperwork do I need for the industrial visit?",
     "Going on the NRSC visit next month. Someone said there is a consent form and an ID proof requirement.",
     None, ["industrialvisit"],
     "Consent form signed by a parent or guardian, one photocopy of your Aadhaar, and the college ID. Give all "
     "three to your class rep at least a week before — they submit them in one batch, not individually."),
    ("Is attendance counted for guest lectures?",
     "There is a guest lecture on Friday during what would be a free hour. Does skipping it hurt attendance?",
     None, ["attendance"],
     "Not in the attendance register, but departments do keep a separate sign-in for guest lectures and it gets "
     "referenced when they pick students for industry visits. So it costs you nothing today and might cost you "
     "a visit slot later."),
    ("Best way to prepare for campus placement aptitude rounds?",
     "First round is aptitude and I have not touched quantitative reasoning since school. Two months to go.",
     None, ["placements", "aptitude"],
     "Do thirty minutes daily rather than a weekend binge — the round is speed-limited, not difficulty-limited. "
     "Work through the previous years' papers the Placement Prep group has pinned; the question shapes repeat "
     "almost exactly. Time yourself from day one."),
]

EVENTS = [
    ("Intro to Flutter — hands-on session", "Lab 3, Block A", 3, 40,
     "Build and run your first app in ninety minutes. Bring a laptop with Android Studio installed."),
    ("Inter-college Robotics Meet — team briefing", "Seminar Hall", 6, 60,
     "Rules walkthrough, team registration and the parts list. Mandatory for anyone competing."),
    ("Blood donation camp", "Health Centre", 9, 120,
     "In partnership with the city blood bank. Breakfast provided, bring your ID."),
    ("Campus photo walk", "Main gate", 12, 25,
     "Two hours around the older blocks. Any camera, phones included."),
    ("Mock placement interviews", "Placement Cell", 15, 30,
     "Twenty-minute slots with alumni interviewers. Dress as you would for the real thing."),
]

COMMENTS = [
    "This is useful, thanks for writing it up.",
    "Same thing happened to me last semester. It sorted itself out after three days.",
    "Do you have a source for this? Not doubting, just want to show it to my class rep.",
    "Can confirm — checked this morning.",
    "Saving this for later.",
    "Which block is this in?",
    "I asked the office and got a different answer, so probably worth confirming.",
    "Adding to this: the same applies to the evening batch.",
]


async def clear_demo(db) -> int:
    """Remove every demo account and everything it created."""
    ids = list(
        (await db.execute(select(User.id).where(User.handle.like(f"{PREFIX}%")))).scalars().all()
    )
    if not ids:
        return 0

    post_ids = list(
        (await db.execute(select(Post.id).where(Post.author_id.in_(ids)))).scalars().all()
    )
    question_ids = list(
        (await db.execute(select(Question.id).where(Question.author_id.in_(ids)))).scalars().all()
    )

    if post_ids:
        await db.execute(delete(Comment).where(Comment.post_id.in_(post_ids)))
        await db.execute(delete(Post).where(Post.id.in_(post_ids)))
    if question_ids:
        await db.execute(delete(Answer).where(Answer.question_id.in_(question_ids)))
        await db.execute(delete(Question).where(Question.id.in_(question_ids)))

    await db.execute(delete(Vote).where(Vote.voter_id.in_(ids)))
    await db.execute(delete(ReputationEvent).where(ReputationEvent.user_id.in_(ids)))
    await db.execute(delete(Notification).where(Notification.user_id.in_(ids)))
    await db.execute(delete(Rsvp).where(Rsvp.user_id.in_(ids)))
    await db.execute(delete(Event).where(Event.host_id.in_(ids)))
    await db.execute(delete(GroupMember).where(GroupMember.user_id.in_(ids)))
    await db.execute(delete(UserSkill).where(UserSkill.user_id.in_(ids)))
    await db.execute(delete(Group).where(Group.owner_id.in_(ids)))
    await db.execute(delete(User).where(User.id.in_(ids)))
    await db.commit()
    return len(ids)


async def build(db, rng: random.Random) -> dict[str, int]:
    now = utcnow()
    password_hash = hash_password(PASSWORD)

    # --- students ---------------------------------------------------------
    users: list[User] = []
    used_handles: set[str] = set()
    for index in range(30):
        first = FIRST_NAMES[index % len(FIRST_NAMES)]
        last = rng.choice(LAST_NAMES)
        department, course, code = DEPARTMENTS[index % len(DEPARTMENTS)]
        batch = rng.choice([2024, 2025, 2026, 2027])

        handle = f"{PREFIX}{first.lower()}{index:02d}"
        if handle in used_handles:
            handle = f"{handle}{secrets.token_hex(2)}"
        used_handles.add(handle)

        user = User(
            handle=handle,
            full_name=f"{first} {last}",
            password_hash=password_hash,
            roll_number=f"{str(batch)[-2:]}{code}{1000 + index}",
            course=course,
            department=department,
            batch_year=batch,
            tier=2,
            bio=rng.choice([
                "",
                f"{course.split('.')[-1].strip()} student. Ask me about lab work.",
                "Third year. Interested in anything that ships.",
                "Here for the notes archive, staying for the arguments.",
                "Class representative. Bring me your grievances.",
            ]),
            interests=rng.sample(
                ["exams", "placements", "robotics", "notes", "codingclub", "photography", "nss"],
                k=rng.randint(2, 4),
            ),
            consent_at=now - dt.timedelta(days=rng.randint(30, 300)),
            created_at=now - dt.timedelta(days=rng.randint(30, 300)),
            last_active_at=now - dt.timedelta(hours=rng.randint(0, 72)),
        )
        db.add(user)
        users.append(user)
    await db.flush()

    # Verification is what earns the first conduct points.
    for user in users:
        await rep.award(db, user.id, "verified_tier2", settle_immediately=True)

    # --- skills -----------------------------------------------------------
    skill_rows: dict[str, Skill] = {}
    for name in SKILLS:
        skill_rows[name] = await ensure_skill(db, name)
    await db.flush()

    for user in users:
        for name in rng.sample(SKILLS, k=rng.randint(1, 4)):
            skill = skill_rows[name]
            db.add(UserSkill(user_id=user.id, skill_id=skill.id))
            skill.usage_count = (skill.usage_count or 0) + 1
    await db.flush()

    # --- groups -----------------------------------------------------------
    groups: list[Group] = []
    for index, (name, kind, description) in enumerate(GROUPS):
        owner = users[index]
        group = Group(
            slug=slugify(name),
            name=name,
            description=description,
            kind=kind,
            is_official=(kind == "club"),
            owner_id=owner.id,
            member_count=0,
            created_at=now - dt.timedelta(days=rng.randint(40, 200)),
        )
        db.add(group)
        groups.append(group)
    await db.flush()

    for group in groups:
        members = rng.sample(users, k=rng.randint(6, 14))
        if group.owner_id not in {m.id for m in members}:
            members.append(next(u for u in users if u.id == group.owner_id))
        for member in members:
            db.add(
                GroupMember(
                    group_id=group.id,
                    user_id=member.id,
                    role="admin" if member.id == group.owner_id else "member",
                )
            )
        group.member_count = len(members)
    await db.flush()

    # --- posts, comments and votes ---------------------------------------
    posts: list[Post] = []
    for index, (title, body, tags, kind) in enumerate(POSTS):
        author = users[(index * 3) % len(users)]
        post = Post(
            author_id=author.id,
            kind=kind,
            title=title,
            body=body,
            tags=tags,
            media=[],
            created_at=now - dt.timedelta(hours=rng.randint(1, 160)),
        )
        db.add(post)
        posts.append(post)

    # One poll, because the feed should show what a poll looks like.
    poll_author = users[7]
    poll = Post(
        author_id=poll_author.id,
        kind="poll",
        title="Which slot suits everyone for the Flutter session?",
        body="Trying to pick a time that does not clash with labs.",
        tags=["codingclub"],
        media=[],
        created_at=now - dt.timedelta(hours=20),
    )
    db.add(poll)
    await db.flush()

    from app.models import PollOption, PollVote

    options = []
    for position, label in enumerate(["Thursday 4pm", "Friday 2pm", "Saturday morning"]):
        option = PollOption(post_id=poll.id, label=label, position=position, votes=0)
        db.add(option)
        options.append(option)
    await db.flush()

    for voter in rng.sample(users, k=14):
        if voter.id == poll_author.id:
            continue
        option = rng.choice(options)
        db.add(PollVote(post_id=poll.id, option_id=option.id, user_id=voter.id))
        option.votes += 1
    posts.append(poll)
    await db.flush()

    for post in posts:
        for commenter in rng.sample(users, k=rng.randint(0, 4)):
            if commenter.id == post.author_id:
                continue
            db.add(
                Comment(
                    post_id=post.id,
                    author_id=commenter.id,
                    body=rng.choice(COMMENTS),
                    created_at=post.created_at + dt.timedelta(minutes=rng.randint(10, 900)),
                )
            )
            post.comment_count = (post.comment_count or 0) + 1
            db.add(
                Notification(
                    user_id=post.author_id,
                    kind="comment",
                    title=f"{commenter.full_name} commented on your post",
                    body=post.title or post.body[:120],
                    link=f"/p/{post.id}",
                    created_at=post.created_at + dt.timedelta(minutes=rng.randint(10, 900)),
                )
            )
    await db.flush()

    await _vote_on(db, rng, users, posts, "post", "post_upvote", low=2, high=11)

    # --- questions and answers -------------------------------------------
    questions: list[Question] = []
    answers: list[Answer] = []
    for index, (title, body, subject, tags, answer_body) in enumerate(QUESTIONS):
        asker = users[(index * 5 + 2) % len(users)]
        answerer = users[(index * 5 + 9) % len(users)]
        asked_at = now - dt.timedelta(days=rng.randint(1, 30))

        question = Question(
            author_id=asker.id,
            title=title,
            body=body,
            subject=subject,
            tags=tags,
            is_anonymous=(index == 2),
            view_count=rng.randint(12, 140),
            created_at=asked_at,
        )
        db.add(question)
        await db.flush()

        answer = Answer(
            question_id=question.id,
            author_id=answerer.id,
            body=answer_body,
            created_at=asked_at + dt.timedelta(hours=rng.randint(1, 20)),
        )
        db.add(answer)
        question.answer_count = 1
        await db.flush()

        await rep.award(
            db, answerer.id, "answer_posted", source_type="answer", source_id=answer.id,
            settle_immediately=True,
        )

        # Most, but not all, questions have an accepted answer — an archive
        # where everything is resolved does not look like a real one.
        if index % 4 != 3:
            question.accepted_answer_id = answer.id
            answer.is_accepted = True
            await rep.award(
                db, answerer.id, "answer_accepted", source_type="answer", source_id=answer.id,
                actor_id=asker.id, detail=title[:120], settle_immediately=True,
            )
            db.add(
                Notification(
                    user_id=answerer.id,
                    kind="accepted",
                    title="Your answer was accepted",
                    body=title[:180],
                    link=f"/qa/{question.id}",
                )
            )

        questions.append(question)
        answers.append(answer)
    await db.flush()

    await _vote_on(db, rng, users, questions, "question", "question_upvote", low=1, high=7)
    await _vote_on(db, rng, users, answers, "answer", "answer_upvote", low=1, high=6)

    # --- events -----------------------------------------------------------
    events: list[Event] = []
    for index, (title, venue, days_ahead, capacity, description) in enumerate(EVENTS):
        host = users[index]
        group = groups[index % len(groups)]
        event = Event(
            host_id=host.id,
            group_id=group.id,
            title=title,
            description=description,
            venue=venue,
            starts_at=now + dt.timedelta(days=days_ahead, hours=rng.randint(0, 6)),
            ends_at=now + dt.timedelta(days=days_ahead, hours=rng.randint(7, 9)),
            capacity=capacity,
            tags=["campus"],
            is_official=True,
            checkin_secret=secrets.token_urlsafe(16),
            created_at=now - dt.timedelta(days=rng.randint(1, 10)),
        )
        db.add(event)
        events.append(event)
    await db.flush()

    from app.routers.events import ticket_code

    for event in events:
        going = rng.sample(users, k=rng.randint(5, 18))
        for attendee in going:
            db.add(
                Rsvp(
                    event_id=event.id,
                    user_id=attendee.id,
                    state="going",
                    ticket_code=ticket_code(event.id, attendee.id, event.checkin_secret),
                )
            )
        event.rsvp_count = len(going)
        await rep.award(
            db, event.host_id, "event_organised", source_type="event", source_id=event.id,
            detail=event.title[:120], settle_immediately=True,
        )
    await db.flush()

    # Reputation caches are derived, so recompute rather than trusting the
    # running totals the awards left behind.
    for user in users:
        await rep.recompute(db, user.id)
    await db.commit()

    return {
        "users": len(users),
        "groups": len(groups),
        "posts": len(posts),
        "questions": len(questions),
        "events": len(events),
    }


async def _vote_on(db, rng, users, targets, target_type: str, reason: str, *, low: int, high: int) -> None:
    """Cast votes directly. ``services.voting`` is the path a real vote takes;
    seeding bypasses its anti-gaming checks on purpose, because thirty accounts
    created in the same second would otherwise look exactly like a vote ring."""
    for target in targets:
        author_id = target.author_id
        voters = [u for u in rng.sample(users, k=rng.randint(low, high)) if u.id != author_id]
        for voter in voters:
            db.add(
                Vote(
                    voter_id=voter.id,
                    author_id=author_id,
                    target_type=target_type,
                    target_id=target.id,
                    value=1,
                    weight=1.0,
                )
            )
            target.score = (target.score or 0) + 1
            await rep.award(
                db, author_id, reason, source_type=target_type, source_id=target.id,
                actor_id=voter.id, settle_immediately=True,
            )
    await db.flush()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="remove the existing demo campus first")
    parser.add_argument("--seed", type=int, default=20260916, help="random seed, for repeatable demos")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    async with SessionLocal() as db:
        existing = int(
            (
                await db.execute(select(func.count(User.id)).where(User.handle.like(f"{PREFIX}%")))
            ).scalar_one()
            or 0
        )
        if existing and not args.reset:
            print(f"{existing} demo accounts already exist. Re-run with --reset to rebuild them.")
            return 1
        if args.reset and existing:
            removed = await clear_demo(db)
            print(f"removed {removed} demo accounts and their content")

        counts = await build(db, rng)

    print("\nSeeded a demo campus:")
    for key, value in counts.items():
        print(f"  {value:>4}  {key}")
    print(f"\nSign in as any '{PREFIX}…' handle with the password: {PASSWORD}")
    print("Example: demo_aarav00")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
