"""Academic Q&A — the module the PRD calls the deepest moat.

Includes the Anonymous Doubt Box: anonymous to readers, never to the system.
``Question.author_id`` is always populated, so abuse stays traceable and
punishable; only the rendering hides it.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, can, verify_csrf
from app.models import Answer, Question, utcnow
from app.services import antiabuse, feed as feed_service, notify
from app.services import moderation as mod
from app.services import reputation as rep
from app.services import search as search_service
from app.services.voting import VoteError, accept_answer
from app.templating import templates

router = APIRouter(prefix="/qa", tags=["qa"])


def _tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for t in (raw or "").replace(",", " ").split():
        t = t.strip().lstrip("#").lower()
        if t and t not in out and len(t) <= 40:
            out.append(t)
    return out[:5]


@router.get("")
async def index(
    request: Request,
    db: DbDep,
    user: Reader,
    q: str = "",
    subject: str = "",
    semester: str = "",
    tag: str = "",
    filter: str = "",
    page: int = 1,
):
    page = max(1, page)
    per_page = 25
    questions = await search_service.questions(
        db,
        q.strip(),
        subject=subject.strip() or None,
        semester=semester.strip() or None,
        tag=tag.strip() or None,
        unanswered=(filter == "unanswered"),
        limit=per_page + 1,
        offset=(page - 1) * per_page,
    )
    has_more = len(questions) > per_page
    questions = questions[:per_page]

    subjects = list(
        (
            await db.execute(
                select(Question.subject)
                .where(Question.subject.is_not(None), Question.status == "active")
                .group_by(Question.subject)
                .order_by(func.count(Question.id).desc())
                .limit(20)
            )
        ).scalars().all()
    )

    return templates.TemplateResponse(
        request,
        "qa/index.html",
        {
            "title": "Questions",
            "questions": questions,
            "has_more": has_more,
            "page": page,
            "q": q,
            "subject": subject,
            "semester": semester,
            "tag": tag,
            "filter": filter,
            "subjects": subjects,
            "my_votes": await feed_service.my_votes(db, user.id, "question", [x.id for x in questions]),
        },
    )


@router.get("/ask")
async def ask_form(request: Request, db: DbDep, user: Verified, title: str = ""):
    duplicates = await search_service.find_duplicates(db, title) if title else []
    return templates.TemplateResponse(
        request,
        "qa/ask.html",
        {"title": "Ask a question", "prefill_title": title, "duplicates": duplicates},
    )


@router.post("/ask")
async def ask(
    request: Request,
    db: DbDep,
    user: Verified,
    title: str = Form(...),
    body: str = Form(...),
    subject: str = Form(""),
    semester: str = Form(""),
    tags: str = Form(""),
    anonymous: str = Form(""),
):
    await verify_csrf(request)
    title = (title or "").strip()
    body = (body or "").strip()
    if len(title) < 10:
        raise HTTPException(status_code=400, detail="Give the question a real title — at least 10 characters.")
    if len(body) < 15:
        raise HTTPException(status_code=400, detail="Add some detail so people can actually answer.")

    allowed, _ = await antiabuse.hit_rate_limit(db, f"ask:{user.id}", 15, dt.timedelta(days=1))
    if not allowed:
        raise HTTPException(status_code=429, detail="That is a lot of questions for one day.")

    lists = await mod.load_lists(db)
    screen = mod.screen(f"{title}\n{body}", lists)
    if screen["verdict"] == "block":
        raise HTTPException(status_code=400, detail="That question breaks the community rules.")

    question = Question(
        author_id=user.id,
        title=title[:250],
        body=body,
        subject=(subject or "").strip()[:80] or None,
        semester=(semester or "").strip()[:20] or None,
        tags=_tags(tags),
        is_anonymous=(anonymous == "on"),
    )
    db.add(question)
    await db.flush()

    if screen["verdict"] == "crisis":
        await mod.raise_crisis(db, user.id, "question", question.id, body)

    return RedirectResponse(f"/qa/{question.id}", status_code=303)


@router.get("/{question_id}")
async def detail(request: Request, db: DbDep, user: Reader, question_id: int):
    question = (
        await db.execute(
            select(Question).options(joinedload(Question.author)).where(Question.id == question_id)
        )
    ).unique().scalar_one_or_none()
    if question is None or (question.status != "active" and not user.is_moderator):
        raise HTTPException(status_code=404)

    if question.duplicate_of_id:
        return RedirectResponse(f"/qa/{question.duplicate_of_id}?from={question.id}", status_code=303)

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

    related = await search_service.find_duplicates(db, question.title, limit=4)
    related = [r for r in related if r.id != question.id]

    return templates.TemplateResponse(
        request,
        "qa/detail.html",
        {
            "title": question.title,
            "question": question,
            "answers": answers,
            "related": related,
            "my_votes": await feed_service.my_votes(db, user.id, "question", [question.id]),
            "my_answer_votes": await feed_service.my_votes(db, user.id, "answer", [a.id for a in answers]),
        },
    )


@router.post("/{question_id}/answers")
async def answer(
    request: Request, db: DbDep, user: Verified, question_id: int, body: str = Form(...)
):
    await verify_csrf(request)
    question = await db.get(Question, question_id)
    if question is None or question.status != "active":
        raise HTTPException(status_code=404)

    body = (body or "").strip()
    if len(body) < 10:
        raise HTTPException(status_code=400, detail="An answer needs more than a few words.")

    lists = await mod.load_lists(db)
    screen = mod.screen(body, lists)
    if screen["verdict"] == "block":
        raise HTTPException(status_code=400, detail="That answer breaks the community rules.")

    row = Answer(question_id=question_id, author_id=user.id, body=body)
    db.add(row)
    question.answer_count = (question.answer_count or 0) + 1
    await db.flush()

    if screen["verdict"] == "crisis":
        await mod.raise_crisis(db, user.id, "answer", row.id, body)

    await rep.award(db, user.id, "answer_posted", source_type="answer", source_id=row.id)

    if not question.is_anonymous or True:
        # The asker is notified either way — anonymity hides them from readers,
        # not from their own notifications.
        await notify.push(
            db,
            question.author_id,
            kind="answer",
            title=f"{user.full_name} answered your question",
            body=question.title[:180],
            link=f"/qa/{question_id}#a{row.id}",
            skip_if_self=user.id,
        )
    return RedirectResponse(f"/qa/{question_id}#a{row.id}", status_code=303)


@router.post("/{question_id}/accept/{answer_id}")
async def accept(request: Request, db: DbDep, user: Verified, question_id: int, answer_id: int):
    await verify_csrf(request)
    question = await db.get(Question, question_id)
    row = await db.get(Answer, answer_id)
    if question is None or row is None:
        raise HTTPException(status_code=404)
    try:
        await accept_answer(db, question, row, user)
    except VoteError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    await notify.push(
        db,
        row.author_id,
        kind="accepted",
        title="Your answer was accepted",
        body=question.title[:180],
        link=f"/qa/{question_id}#a{answer_id}",
        skip_if_self=user.id,
    )
    return RedirectResponse(f"/qa/{question_id}#a{answer_id}", status_code=303)


@router.post("/{question_id}/duplicate")
async def mark_duplicate(
    request: Request, db: DbDep, user: Verified, question_id: int, canonical_id: int = Form(...)
):
    """Merge a duplicate into the canonical question. Trusted+ only."""
    await verify_csrf(request)
    if not can(user, "merge_duplicates"):
        raise HTTPException(status_code=403, detail="Merging duplicates needs Trusted standing.")
    if question_id == canonical_id:
        raise HTTPException(status_code=400, detail="A question cannot duplicate itself.")

    question = await db.get(Question, question_id)
    canonical = await db.get(Question, canonical_id)
    if question is None or canonical is None:
        raise HTTPException(status_code=404)
    if canonical.duplicate_of_id:
        raise HTTPException(status_code=400, detail="That target is itself marked as a duplicate.")

    question.duplicate_of_id = canonical_id
    await mod.log_action(
        db,
        action="merge_duplicate",
        target_type="question",
        target_id=question_id,
        actor_id=user.id,
        subject_user_id=question.author_id,
        reason=f"Merged into #{canonical_id}",
        meta={"canonical_id": canonical_id},
    )
    await notify.push(
        db,
        question.author_id,
        kind="qa",
        title="Your question was merged with an existing one",
        body=canonical.title[:180],
        link=f"/qa/{canonical_id}",
        skip_if_self=user.id,
    )
    return RedirectResponse(f"/qa/{canonical_id}", status_code=303)


@router.post("/{question_id}/close")
async def close_question(request: Request, db: DbDep, user: Verified, question_id: int):
    await verify_csrf(request)
    question = await db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404)
    if question.author_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your question.")
    question.status = "removed"
    question.removed_reason = "Closed by author" if question.author_id == user.id else "Removed by moderator"
    await rep.void_events(db, source_type="question", source_id=question.id, reason_note="question closed")
    return RedirectResponse("/qa", status_code=303)


@router.get("/archive/{semester}")
async def archive(request: Request, db: DbDep, user: Reader, semester: str):
    """The semester-wise archive. This is the thing that compounds."""
    questions = await search_service.questions(db, "", semester=semester, limit=200)
    by_subject: dict[str, list] = {}
    for question in questions:
        by_subject.setdefault(question.subject or "General", []).append(question)
    return templates.TemplateResponse(
        request,
        "qa/archive.html",
        {"title": f"Semester {semester} archive", "semester": semester, "by_subject": by_subject},
    )
