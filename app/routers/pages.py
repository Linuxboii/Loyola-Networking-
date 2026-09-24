"""Media delivery and the standing informational pages."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from app.config import settings
from app.deps import CurrentUser, DbDep, Reader, RequireUser
from app.models import (
    Answer,
    Event,
    Question,
    Resource,
    TransparencySnapshot,
    User,
    utcnow,
)
from app.security import unsign_media
from app.services import feed as feed_service
from app.services import media, moderation, notify
from app.services import releases as release_service
from app.services import reputation as rep
from app.templating import templates

router = APIRouter(tags=["pages"])

CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
}


@router.get("/media/{token}")
async def serve_media(token: str, user: CurrentUser):
    """Signed, time-limited, and only for signed-in members.

    Nothing in this app is publicly addressable — PRD 3 lists "no public web
    profiles" as an explicit non-goal, so even an avatar needs a session.
    """
    if user is None:
        raise HTTPException(status_code=401)
    path = unsign_media(token, max_age=86400)
    if not path:
        raise HTTPException(status_code=404)
    data = media.read_public(path)
    if data is None:
        raise HTTPException(status_code=404)
    ext = path.rsplit(".", 1)[-1].lower()
    return Response(
        content=data,
        media_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/about")
async def about(request: Request, user: CurrentUser):
    return templates.TemplateResponse(
        request, "pages/about.html", {"title": f"About {settings.app_name}"}
    )


@router.get("/rules")
async def rules(request: Request, user: CurrentUser):
    return templates.TemplateResponse(request, "pages/rules.html", {"title": "Community rules"})


@router.get("/download")
async def download_app(request: Request, user: CurrentUser):
    """Where a student gets the Android app, and where an old build gets the new one.

    Open to signed-out visitors on purpose: a phone whose build is too old to
    sign in still has to be able to reach this page.
    """
    return templates.TemplateResponse(
        request,
        "pages/download.html",
        {
            "title": f"Get the {settings.app_name} app",
            "latest": release_service.latest_release(),
            "history": release_service.load_releases()[1:6],
            "min_build": release_service.minimum_supported_build(),
        },
    )


@router.get("/transparency")
async def transparency(request: Request, db: DbDep, user: Reader):
    """Monthly moderation numbers, published in-app (PRD 8)."""
    snapshots = list(
        (
            await db.execute(
                select(TransparencySnapshot).order_by(TransparencySnapshot.period.desc()).limit(12)
            )
        ).scalars().all()
    )
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    live = await moderation.transparency_for(db, month_start, utcnow())
    return templates.TemplateResponse(
        request,
        "pages/transparency.html",
        {
            "title": "Transparency report",
            "snapshots": snapshots,
            "live": live,
            "live_period": month_start.strftime("%B %Y"),
        },
    )


@router.get("/digest")
async def digest(request: Request, db: DbDep, user: Reader):
    """What the weekly digest notification links to."""
    since = utcnow() - dt.timedelta(days=7)
    soon = utcnow() + dt.timedelta(days=7)
    interests = {i.lower() for i in (user.interests or [])}

    unanswered = list(
        (
            await db.execute(
                select(Question)
                .where(Question.created_at >= since, Question.answer_count == 0, Question.status == "active")
                .order_by(Question.created_at.desc())
                .limit(30)
            )
        ).scalars().all()
    )
    mine = [q for q in unanswered if not interests or interests & {t.lower() for t in (q.tags or [])}]

    from app.models import Opportunity

    opportunities = list(
        (
            await db.execute(
                select(Opportunity)
                .where(Opportunity.created_at >= since, Opportunity.status == "active")
                .order_by(Opportunity.created_at.desc())
                .limit(10)
            )
        ).scalars().all()
    )
    events = list(
        (
            await db.execute(
                select(Event)
                .where(Event.starts_at.between(utcnow(), soon), Event.status == "active")
                .order_by(Event.starts_at)
                .limit(10)
            )
        ).scalars().all()
    )

    return templates.TemplateResponse(
        request,
        "pages/digest.html",
        {
            "title": "Your weekly digest",
            "questions": mine[:10] or unanswered[:10],
            "opportunities": opportunities,
            "events": events,
        },
    )


@router.get("/wrapped")
async def wrapped(request: Request, db: DbDep, user: Reader):
    """Semester Wrapped (PRD 7.9.9): a recap of what you actually did.

    Deliberately framed around contribution, not consumption — there is no
    "hours spent" or "posts read" number anywhere in it.
    """
    # Semesters here run Jun-Nov and Dec-May, the usual Indian academic split.
    today = utcnow()
    if 6 <= today.month <= 11:
        start = today.replace(month=6, day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"Jun–Nov {today.year}"
    elif today.month >= 12:
        start = today.replace(month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"Dec {today.year}–May {today.year + 1}"
    else:
        start = today.replace(year=today.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"Dec {today.year - 1}–May {today.year}"

    from app.models import Post, ReputationEvent, Rsvp

    async def count(model, *where):
        return int((await db.execute(select(func.count(model.id)).where(*where))).scalar_one() or 0)

    posts = await count(Post, Post.author_id == user.id, Post.created_at >= start, Post.status == "active")
    answers = await count(
        Answer, Answer.author_id == user.id, Answer.created_at >= start, Answer.status == "active"
    )
    accepted = await count(
        Answer, Answer.author_id == user.id, Answer.created_at >= start, Answer.is_accepted.is_(True)
    )
    questions = await count(Question, Question.author_id == user.id, Question.created_at >= start)
    resources_shared = await count(
        Resource, Resource.uploader_id == user.id, Resource.created_at >= start, Resource.status == "active"
    )
    events_attended = await count(
        Rsvp, Rsvp.user_id == user.id, Rsvp.checked_in_at.is_not(None), Rsvp.created_at >= start
    )

    earned = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(ReputationEvent.points), 0)).where(
                    ReputationEvent.user_id == user.id,
                    ReputationEvent.created_at >= start,
                    ReputationEvent.state.in_(("provisional", "settled")),
                )
            )
        ).scalar_one()
        or 0
    )
    top_pillar = max(
        ("academic", "build", "service", "participation"),
        key=lambda p: getattr(user, f"rep_{p}", 0.0),
    )

    return templates.TemplateResponse(
        request,
        "pages/wrapped.html",
        {
            "title": "Semester Wrapped",
            "period": label,
            "posts": posts,
            "answers": answers,
            "accepted": accepted,
            "questions": questions,
            "resources": resources_shared,
            "events_attended": events_attended,
            "earned": round(earned),
            "top_pillar": top_pillar,
            "breakdown": rep.pillar_breakdown(user),
        },
    )


@router.get("/directory")
async def directory(request: Request, db: DbDep, user: Reader):
    """Who is here, by department and batch."""
    rows = (
        await db.execute(
            select(User.department, User.batch_year, func.count(User.id))
            .where(User.tier >= 2, User.status == "active", User.hide_from_search.is_(False))
            .group_by(User.department, User.batch_year)
            .order_by(User.department, User.batch_year.desc())
        )
    ).all()
    grouped: dict[str, list[tuple[int | None, int]]] = {}
    for dept, batch, n in rows:
        grouped.setdefault(dept or "Unlisted", []).append((batch, int(n)))
    return templates.TemplateResponse(
        request,
        "pages/directory.html",
        {"title": "Campus directory", "grouped": grouped, "stats": await feed_service.campus_stats(db)},
    )
