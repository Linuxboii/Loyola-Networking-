"""Questions and answers — the academic half of the network."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.api.schemas import AnswerIn, QuestionIn, answer_out, question_out
from app.deps import DbDep, Reader, Verified, can
from app.models import Answer, Question
from app.services import antiabuse, feed as feed_service, notify, search
from app.services import moderation as mod
from app.services import posting
from app.services import reputation as rep
from app.services.voting import VoteError, accept_answer

router = APIRouter(prefix="/qa", tags=["api:qa"])

PAGE = 20


@router.get("/questions")
async def list_questions(
    db: DbDep,
    user: Reader,
    q: str = "",
    subject: str | None = None,
    semester: str | None = None,
    scope: str = Query("all", pattern="^(all|unanswered|mine|accepted)$"),
    page: int = Query(1, ge=1, le=500),
):
    stmt = (
        select(Question)
        .options(joinedload(Question.author))
        .where(Question.status == "active")
    )
    if subject:
        stmt = stmt.where(Question.subject == subject)
    if semester:
        stmt = stmt.where(Question.semester == semester)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(Question.title).like(needle), func.lower(Question.body).like(needle))
        )
    if scope == "unanswered":
        stmt = stmt.where(Question.answer_count == 0)
    elif scope == "mine":
        stmt = stmt.where(Question.author_id == user.id)
    elif scope == "accepted":
        stmt = stmt.where(Question.accepted_answer_id.is_not(None))

    stmt = stmt.order_by(Question.created_at.desc()).limit(PAGE + 1).offset((page - 1) * PAGE)
    rows = list((await db.execute(stmt)).unique().scalars().all())
    has_more = len(rows) > PAGE
    rows = rows[:PAGE]
    votes = await feed_service.my_votes(db, user.id, "question", [r.id for r in rows])
    return {
        "page": page,
        "has_more": has_more,
        "questions": [question_out(r, my_vote=votes.get(r.id, 0)) for r in rows],
    }


@router.get("/questions/{question_id}")
async def question_detail(question_id: int, db: DbDep, user: Reader):
    question = (
        await db.execute(
            select(Question).options(joinedload(Question.author)).where(Question.id == question_id)
        )
    ).unique().scalar_one_or_none()
    if question is None or (question.status != "active" and not user.is_moderator):
        raise HTTPException(404, "That question is no longer available.")

    question.view_count = (question.view_count or 0) + 1
    answers = list(
        (
            await db.execute(
                select(Answer)
                .options(joinedload(Answer.author))
                .where(Answer.question_id == question_id, Answer.status == "active")
                .order_by(Answer.is_accepted.desc(), Answer.score.desc(), Answer.created_at.asc())
            )
        ).unique().scalars().all()
    )
    q_votes = await feed_service.my_votes(db, user.id, "question", [question.id])
    a_votes = await feed_service.my_votes(db, user.id, "answer", [a.id for a in answers])
    return {
        "question": question_out(question, my_vote=q_votes.get(question.id, 0)),
        "answers": [answer_out(a, my_vote=a_votes.get(a.id, 0)) for a in answers],
        "can_accept": question.author_id == user.id or can(user, "mark_verified_answer"),
    }


@router.post("/questions", status_code=201)
async def ask(payload: QuestionIn, db: DbDep, user: Verified):
    title = payload.title.strip()
    body = payload.body.strip()
    if len(title) < 10:
        raise HTTPException(422, "Give the question a real title — at least 10 characters.")
    if len(body) < 15:
        raise HTTPException(422, "Add some detail so people can actually answer.")

    allowed, _ = await antiabuse.hit_rate_limit(db, f"ask:{user.id}", 15, dt.timedelta(days=1))
    if not allowed:
        raise HTTPException(429, "That is a lot of questions for one day.")

    lists = await mod.load_lists(db)
    screen = mod.screen(f"{title}\n{body}", lists)
    if screen["verdict"] == "block":
        raise HTTPException(400, "That question breaks the community rules.")

    question = Question(
        author_id=user.id,
        title=title[:250],
        body=body,
        subject=(payload.subject or "").strip()[:80] or None,
        semester=(payload.semester or "").strip()[:20] or None,
        tags=posting.normalise_tags(payload.tags),
        is_anonymous=payload.is_anonymous,
    )
    db.add(question)
    await db.flush()

    if screen["verdict"] == "crisis":
        await mod.raise_crisis(db, user.id, "question", question.id, body)

    await db.refresh(question, ["author"])
    return question_out(question)


@router.get("/duplicates")
async def duplicate_hints(db: DbDep, user: Reader, title: str = Query(min_length=6)):
    """Called as the student types, so near-duplicates surface before posting."""
    rows = await search.find_duplicates(db, title, limit=5)
    return {"questions": [question_out(r) for r in rows]}


@router.post("/questions/{question_id}/answers", status_code=201)
async def answer(question_id: int, payload: AnswerIn, db: DbDep, user: Verified):
    question = await db.get(Question, question_id)
    if question is None or question.status != "active":
        raise HTTPException(404, "That question is no longer available.")

    body = payload.body.strip()
    if len(body) < 10:
        raise HTTPException(422, "An answer needs more than a few words.")

    lists = await mod.load_lists(db)
    screen = mod.screen(body, lists)
    if screen["verdict"] == "block":
        raise HTTPException(400, "That answer breaks the community rules.")

    row = Answer(question_id=question_id, author_id=user.id, body=body)
    db.add(row)
    question.answer_count = (question.answer_count or 0) + 1
    await db.flush()

    if screen["verdict"] == "crisis":
        await mod.raise_crisis(db, user.id, "answer", row.id, body)

    await rep.award(db, user.id, "answer_posted", source_type="answer", source_id=row.id)
    await notify.push(
        db,
        question.author_id,
        kind="answer",
        title=f"{user.full_name} answered your question",
        body=question.title[:180],
        link=f"/qa/{question_id}#a{row.id}",
        skip_if_self=user.id,
    )
    await db.refresh(row, ["author"])
    return answer_out(row)


@router.post("/questions/{question_id}/accept/{answer_id}")
async def accept(question_id: int, answer_id: int, db: DbDep, user: Verified):
    question = await db.get(Question, question_id)
    row = await db.get(Answer, answer_id)
    if question is None or row is None or row.question_id != question_id:
        raise HTTPException(404, "No such answer.")
    try:
        await accept_answer(db, question, row, user)
    except VoteError as exc:
        raise HTTPException(403, str(exc)) from exc

    await notify.push(
        db,
        row.author_id,
        kind="accepted",
        title="Your answer was accepted",
        body=question.title[:180],
        link=f"/qa/{question_id}#a{answer_id}",
        skip_if_self=user.id,
    )
    return {"accepted_answer_id": answer_id}
