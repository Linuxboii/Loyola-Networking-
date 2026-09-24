"""Profiles, the people directory, skills, endorsements and reputation."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.api.schemas import (
    ProfileIn,
    answer_out,
    post_out,
    profile_out,
    question_out,
    user_card,
)
from app.deps import DbDep, Reader, Verified
from app.models import (
    Answer,
    Endorsement,
    PILLAR_LABELS,
    Post,
    Question,
    User,
    UserSkill,
)
from app.services import feed as feed_service, search
from app.services import reputation as rep
from app.services.bootstrap import ensure_skill

router = APIRouter(tags=["api:people"])


async def _skill_block(db, person_id: int, viewer_id: int) -> list[dict]:
    rows = list(
        (
            await db.execute(
                select(UserSkill)
                .options(joinedload(UserSkill.skill))
                .where(UserSkill.user_id == person_id)
                .order_by(UserSkill.endorsement_weight.desc())
            )
        ).unique().scalars().all()
    )
    mine = {
        int(sid)
        for sid in (
            await db.execute(
                select(Endorsement.skill_id).where(
                    Endorsement.endorser_id == viewer_id, Endorsement.user_id == person_id
                )
            )
        ).scalars().all()
    }
    return [
        {
            "id": row.skill_id,
            "name": row.skill.name,
            "slug": row.skill.slug,
            "endorsements": row.endorsement_count,
            "weight": round(row.endorsement_weight or 0.0, 1),
            "endorsed_by_me": row.skill_id in mine,
        }
        for row in rows
    ]


@router.get("/people")
async def directory(
    db: DbDep,
    user: Reader,
    q: str = "",
    skill: str | None = None,
    batch: int | None = None,
    department: str | None = None,
    limit: int = Query(30, ge=1, le=60),
):
    rows = await search.people(
        db, q, viewer=user, skill=skill, batch=batch, department=department, limit=limit
    )
    return {"people": [user_card(r, viewer_is_moderator=user.is_moderator) for r in rows]}


@router.get("/search")
async def search_everything(db: DbDep, user: Reader, q: str = Query(min_length=1, max_length=120)):
    found = await search.everything(db, q, user)
    return {
        "query": q,
        "people": [user_card(p, viewer_is_moderator=user.is_moderator) for p in found["people"]],
        "questions": [question_out(x, viewer_is_moderator=user.is_moderator) for x in found["questions"]],
        "resources": [
            {
                "id": r.id,
                "title": r.title,
                "subject": r.subject,
                "kind": r.kind,
                "score": r.score,
            }
            for r in found["resources"]
        ],
        "projects": [
            {"id": p.id, "title": p.title, "summary": (p.summary or "")[:200], "status": p.status}
            for p in found["projects"]
        ],
        "opportunities": [
            {
                "id": o.id,
                "title": o.role_title,
                "company": o.company,
                "kind": o.kind,
                "location": o.location,
            }
            for o in found["opportunities"]
        ],
    }


@router.get("/people/{handle}")
async def profile(handle: str, db: DbDep, user: Reader):
    person = (
        await db.execute(select(User).where(User.handle == handle.lower()))
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(404, "No such member.")

    is_me = person.id == user.id
    hidden = person.hide_activity and not is_me and not user.is_moderator

    counts = {
        "posts": int(
            (
                await db.execute(
                    select(func.count(Post.id)).where(
                        Post.author_id == person.id, Post.status == "active"
                    )
                )
            ).scalar_one()
            or 0
        ),
        "answers": int(
            (
                await db.execute(
                    select(func.count(Answer.id)).where(
                        Answer.author_id == person.id, Answer.status == "active"
                    )
                )
            ).scalar_one()
            or 0
        ),
        "accepted_answers": int(
            (
                await db.execute(
                    select(func.count(Answer.id)).where(
                        Answer.author_id == person.id, Answer.is_accepted.is_(True)
                    )
                )
            ).scalar_one()
            or 0
        ),
    }

    return {
        **profile_out(
            person,
            skills=await _skill_block(db, person.id, user.id),
            is_me=is_me,
            counts=counts,
            viewer_is_moderator=user.is_moderator,
        ),
        "activity_hidden": hidden,
    }


@router.get("/people/{handle}/posts")
async def profile_posts(handle: str, db: DbDep, user: Reader, page: int = Query(1, ge=1, le=200)):
    person = (
        await db.execute(select(User).where(User.handle == handle.lower()))
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(404, "No such member.")
    if person.hide_activity and person.id != user.id and not user.is_moderator:
        return {"page": page, "has_more": False, "posts": []}

    posts, has_more = await feed_service.fetch(db, user, author_id=person.id, page=page)
    votes = await feed_service.my_votes(db, user.id, "post", [p.id for p in posts])
    return {
        "page": page,
        "has_more": has_more,
        "posts": [post_out(p, my_vote=votes.get(p.id, 0), viewer_is_moderator=user.is_moderator) for p in posts],
    }


@router.get("/people/{handle}/answers")
async def profile_answers(handle: str, db: DbDep, user: Reader):
    person = (
        await db.execute(select(User).where(User.handle == handle.lower()))
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(404, "No such member.")
    if person.hide_activity and person.id != user.id and not user.is_moderator:
        return {"answers": []}

    rows = (
        await db.execute(
            select(Answer, Question)
            .options(joinedload(Answer.author))
            .join(Question, Question.id == Answer.question_id)
            .where(Answer.author_id == person.id, Answer.status == "active")
            .order_by(Answer.created_at.desc())
            .limit(25)
        )
    ).unique().all()
    return {
        "answers": [
            {**answer_out(a, viewer_is_moderator=user.is_moderator), "question": {"id": q.id, "title": q.title}} for a, q in rows
        ]
    }


@router.patch("/me")
async def update_profile(payload: ProfileIn, db: DbDep, user: Verified):
    if payload.handle is not None:
        handle = payload.handle.strip().lower().lstrip("@")
        if not re.fullmatch(r"[a-z0-9_]{3,24}", handle):
            raise HTTPException(422, "Use 3-24 lowercase letters, numbers, or underscores.")
        if handle in {"admin", "administrator", "mod", "moderator", "loyola", "support", "system"}:
            raise HTTPException(422, "That username is reserved.")
        taken = (await db.execute(select(User.id).where(User.handle == handle, User.id != user.id))).scalar_one_or_none()
        if taken is not None:
            raise HTTPException(409, "That username is already taken.")
        user.handle = handle
    if payload.bio is not None:
        user.bio = payload.bio.strip()[:1000]
    if payload.interests is not None:
        cleaned: list[str] = []
        for item in payload.interests:
            tag = item.strip().lstrip("#").lower()[:40]
            if tag and tag not in cleaned:
                cleaned.append(tag)
        user.interests = cleaned[:12]
    if payload.links is not None:
        # Only http(s) links, and only a handful, so the profile cannot become a
        # redirect farm.
        safe = {
            key[:24]: value[:200]
            for key, value in list(payload.links.items())[:6]
            if value.startswith(("http://", "https://"))
        }
        user.links = safe
    if payload.hide_from_search is not None:
        user.hide_from_search = payload.hide_from_search
    if payload.hide_activity is not None:
        user.hide_activity = payload.hide_activity
    if payload.endorse_policy is not None:
        user.endorse_policy = payload.endorse_policy
    await db.flush()
    return profile_out(user, skills=await _skill_block(db, user.id, user.id), is_me=True,
                       viewer_is_moderator=user.is_moderator)


@router.post("/me/skills", status_code=201)
async def add_skill(db: DbDep, user: Verified, name: str = Query(min_length=2, max_length=50)):
    existing = int(
        (await db.execute(select(func.count(UserSkill.id)).where(UserSkill.user_id == user.id)))
        .scalar_one()
        or 0
    )
    if existing >= 12:
        raise HTTPException(409, "Twelve skills is the limit â€” remove one first.")
    skill = await ensure_skill(db, name)
    already = (
        await db.execute(
            select(UserSkill).where(UserSkill.user_id == user.id, UserSkill.skill_id == skill.id)
        )
    ).scalar_one_or_none()
    if already is None:
        db.add(UserSkill(user_id=user.id, skill_id=skill.id))
        skill.usage_count = (skill.usage_count or 0) + 1
        await db.flush()
    return {"skills": await _skill_block(db, user.id, user.id)}


@router.delete("/me/skills/{skill_id}", status_code=204)
async def remove_skill(skill_id: int, db: DbDep, user: Verified):
    row = (
        await db.execute(
            select(UserSkill).where(UserSkill.user_id == user.id, UserSkill.skill_id == skill_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "You do not list that skill.")
    await db.delete(row)
    return Response(status_code=204)


@router.get("/skills")
async def skill_suggestions(db: DbDep, user: Reader, q: str = "", limit: int = Query(20, ge=1, le=50)):
    rows = await search.skills(db, q, limit=limit)
    return {"skills": [{"id": s.id, "name": s.name, "slug": s.slug, "used": s.usage_count} for s in rows]}


@router.get("/me/reputation")
async def my_reputation(db: DbDep, user: Reader, page: int = Query(1, ge=1, le=100)):
    events = await rep.ledger(db, user.id, limit=50, offset=(page - 1) * 50)
    nxt = rep.next_tier(user.rep_total or 0.0)
    return {
        "total": round(user.rep_total or 0.0, 1),
        "tier": user.rep_tier,
        "frozen": user.rep_frozen,
        "floor": round(rep.tier_floor(user.rep_total or 0.0), 1),
        "next_tier": ({"name": nxt[0], "at": round(nxt[1], 1)} if nxt else None),
        "pillars": [
            {"key": key, "label": label, "value": round(getattr(user, f"rep_{key}", 0.0) or 0.0, 1)}
            for key, label in PILLAR_LABELS.items()
        ],
        "events": [
            {
                "id": e.id,
                "reason": e.reason,
                "pillar": e.pillar,
                "points": round(e.points, 2),
                "state": e.state,
                "detail": e.detail,
                "settles_at": e.settles_at.isoformat() if e.settles_at else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }
