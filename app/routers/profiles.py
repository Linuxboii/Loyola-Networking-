"""Profiles, skills, endorsements and the personal reputation ledger."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, RequireUser, Verified, can, verify_csrf
from app.models import (
    Answer,
    Endorsement,
    Event,
    Post,
    Project,
    Question,
    Rsvp,
    Skill,
    User,
    UserSkill,
)
from app.services import media, notify
from app.services import reputation as rep
from app.services import search as search_service
from app.services.bootstrap import ensure_skill
from app.templating import templates

router = APIRouter(tags=["profiles"])


@router.get("/people")
async def people(
    request: Request,
    db: DbDep,
    user: Reader,
    q: str = "",
    skill: str = "",
    batch: str = "",
    department: str = "",
    tier: str = "",
):
    batch_year = None
    if batch.isdigit():
        batch_year = int(batch)

    results = await search_service.people(
        db,
        q.strip(),
        viewer=user,
        skill=skill.strip() or None,
        batch=batch_year,
        department=department.strip() or None,
        min_tier=tier or None,
        limit=60,
    )
    top_skills = await search_service.skills(db, "", limit=24)
    departments = list(
        (
            await db.execute(
                select(User.department)
                .where(User.department.is_not(None), User.tier >= 2)
                .group_by(User.department)
                .order_by(User.department)
            )
        ).scalars().all()
    )
    return templates.TemplateResponse(
        request,
        "profiles/people.html",
        {
            "title": "Find people",
            "results": results,
            "q": q,
            "skill": skill,
            "batch": batch,
            "department": department,
            "tier": tier,
            "top_skills": top_skills,
            "departments": departments,
        },
    )


@router.get("/u/{handle}")
async def profile(request: Request, db: DbDep, user: Reader, handle: str):
    person = (
        await db.execute(select(User).where(User.handle == handle.lower()))
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=404)

    is_self = person.id == user.id
    hidden = person.hide_activity and not is_self and not user.is_moderator

    skills = list(
        (
            await db.execute(
                select(UserSkill)
                .options(joinedload(UserSkill.skill))
                .where(UserSkill.user_id == person.id)
                .order_by(UserSkill.endorsement_weight.desc())
            )
        ).unique().scalars().all()
    )
    my_endorsements = set()
    if not is_self:
        my_endorsements = {
            int(sid)
            for sid in (
                await db.execute(
                    select(Endorsement.skill_id).where(
                        Endorsement.endorser_id == user.id, Endorsement.user_id == person.id
                    )
                )
            ).scalars().all()
        }

    posts = answers = projects = events_hosted = []
    if not hidden:
        posts = list(
            (
                await db.execute(
                    select(Post)
                    .options(joinedload(Post.author))
                    .where(Post.author_id == person.id, Post.status == "active", Post.group_id.is_(None))
                    .order_by(Post.created_at.desc())
                    .limit(10)
                )
            ).unique().scalars().all()
        )
        answers = list(
            (
                await db.execute(
                    select(Answer, Question)
                    .join(Question, Question.id == Answer.question_id)
                    .where(Answer.author_id == person.id, Answer.status == "active")
                    .order_by(Answer.score.desc(), Answer.created_at.desc())
                    .limit(8)
                )
            ).all()
        )
        projects = list(
            (
                await db.execute(
                    select(Project)
                    .where(Project.owner_id == person.id, Project.status != "removed")
                    .order_by(Project.created_at.desc())
                    .limit(6)
                )
            ).scalars().all()
        )
        events_hosted = list(
            (
                await db.execute(
                    select(Event)
                    .where(Event.host_id == person.id, Event.status == "active")
                    .order_by(Event.starts_at.desc())
                    .limit(5)
                )
            ).scalars().all()
        )

    attended = int(
        (
            await db.execute(
                select(func.count(Rsvp.id)).where(
                    Rsvp.user_id == person.id, Rsvp.checked_in_at.is_not(None)
                )
            )
        ).scalar_one()
        or 0
    )

    can_endorse = (
        not is_self
        and user.tier >= 2
        and person.endorse_policy != "nobody"
        and not (person.endorse_policy == "batch" and person.batch_year != user.batch_year)
    )

    return templates.TemplateResponse(
        request,
        "profiles/profile.html",
        {
            "title": person.full_name,
            "person": person,
            "is_self": is_self,
            "hidden": hidden,
            "skills": skills,
            "my_endorsements": my_endorsements,
            "can_endorse": can_endorse,
            "posts": posts,
            "answers": answers,
            "projects": projects,
            "events_hosted": events_hosted,
            "attended": attended,
            "breakdown": rep.pillar_breakdown(person),
        },
    )


@router.get("/me/edit")
async def edit_profile(request: Request, db: DbDep, user: Verified):
    skills = list(
        (
            await db.execute(
                select(UserSkill).options(joinedload(UserSkill.skill)).where(UserSkill.user_id == user.id)
            )
        ).unique().scalars().all()
    )
    return templates.TemplateResponse(
        request,
        "profiles/edit.html",
        {
            "title": "Edit your profile",
            "skills": skills,
            "all_skills": await search_service.skills(db, "", limit=200),
        },
    )


@router.post("/me/edit")
async def save_profile(
    request: Request,
    db: DbDep,
    user: Verified,
    bio: str = Form(""),
    interests: str = Form(""),
    github: str = Form(""),
    linkedin: str = Form(""),
    portfolio: str = Form(""),
    hide_from_search: str = Form(""),
    hide_activity: str = Form(""),
    endorse_policy: str = Form("anyone"),
    photo: UploadFile | None = File(None),
):
    await verify_csrf(request)

    user.bio = (bio or "").strip()[:1000]
    user.interests = [
        t.strip().lstrip("#").lower()
        for t in (interests or "").replace(",", " ").split()
        if t.strip()
    ][:12]
    links = {}
    for key, value in (("github", github), ("linkedin", linkedin), ("portfolio", portfolio)):
        value = (value or "").strip()
        if value:
            if not value.startswith(("http://", "https://")):
                value = "https://" + value
            links[key] = value[:300]
    user.links = links
    user.hide_from_search = hide_from_search == "on"
    user.hide_activity = hide_activity == "on"
    user.endorse_policy = endorse_policy if endorse_policy in {"anyone", "batch", "nobody"} else "anyone"

    if photo is not None and photo.filename:
        data = await photo.read()
        if data:
            if not media.is_image(data):
                raise HTTPException(status_code=400, detail="That is not an image.")
            if user.photo_path:
                media.delete_public(user.photo_path)
            user.photo_path = media.save_public(data, subdir="avatars")

    return RedirectResponse(f"/u/{user.handle}", status_code=303)


@router.post("/me/skills")
async def add_skill(request: Request, db: DbDep, user: Verified, name: str = Form(...)):
    await verify_csrf(request)
    name = (name or "").strip()
    if not name:
        return RedirectResponse("/me/edit", status_code=303)

    count = int(
        (await db.execute(select(func.count(UserSkill.id)).where(UserSkill.user_id == user.id))).scalar_one()
        or 0
    )
    if count >= 15:
        raise HTTPException(status_code=400, detail="Fifteen skills is plenty. Remove one first.")

    skill = await ensure_skill(db, name)
    existing = (
        await db.execute(
            select(UserSkill).where(UserSkill.user_id == user.id, UserSkill.skill_id == skill.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(UserSkill(user_id=user.id, skill_id=skill.id))
        skill.usage_count = (skill.usage_count or 0) + 1
    return RedirectResponse("/me/edit", status_code=303)


@router.post("/me/skills/{skill_id}/remove")
async def remove_skill(request: Request, db: DbDep, user: Verified, skill_id: int):
    await verify_csrf(request)
    row = (
        await db.execute(
            select(UserSkill).where(UserSkill.user_id == user.id, UserSkill.skill_id == skill_id)
        )
    ).scalar_one_or_none()
    if row:
        await db.delete(row)
    return RedirectResponse("/me/edit", status_code=303)


@router.post("/u/{handle}/endorse/{skill_id}")
async def endorse(request: Request, db: DbDep, user: Verified, handle: str, skill_id: int):
    """Endorse a specific skill. Weight comes from the endorser's standing, so a
    Trusted member's endorsement counts for more than a brand-new account's —
    that is what stops endorsement swapping from being worth anything."""
    await verify_csrf(request)
    person = (await db.execute(select(User).where(User.handle == handle.lower()))).scalar_one_or_none()
    if person is None or person.id == user.id:
        raise HTTPException(status_code=400, detail="You cannot endorse yourself.")
    if person.endorse_policy == "nobody":
        raise HTTPException(status_code=403, detail="This member has endorsements switched off.")
    if person.endorse_policy == "batch" and person.batch_year != user.batch_year:
        raise HTTPException(status_code=403, detail="This member only accepts endorsements from their batch.")

    target = (
        await db.execute(
            select(UserSkill).where(UserSkill.user_id == person.id, UserSkill.skill_id == skill_id)
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="They have not listed that skill.")

    existing = (
        await db.execute(
            select(Endorsement).where(
                Endorsement.endorser_id == user.id,
                Endorsement.user_id == person.id,
                Endorsement.skill_id == skill_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        target.endorsement_count = max(0, target.endorsement_count - 1)
        target.endorsement_weight = max(0.0, target.endorsement_weight - existing.weight)
        await rep.void_events(
            db, source_type="endorsement", source_id=existing.id, reason_note="endorsement withdrawn"
        )
        return RedirectResponse(f"/u/{handle}", status_code=303)

    weight = rep.vote_weight(user, 0)
    endorsement = Endorsement(
        endorser_id=user.id, user_id=person.id, skill_id=skill_id, weight=weight
    )
    db.add(endorsement)
    target.endorsement_count += 1
    target.endorsement_weight += weight
    await db.flush()

    skill = await db.get(Skill, skill_id)
    await rep.award(
        db,
        person.id,
        "skill_endorsed",
        multiplier=weight,
        source_type="endorsement",
        source_id=endorsement.id,
        actor_id=user.id,
        detail=f"{skill.name if skill else 'Skill'} endorsed by {user.full_name}",
    )
    await notify.push(
        db,
        person.id,
        kind="endorsement",
        title=f"{user.full_name} endorsed your {skill.name if skill else 'skill'}",
        link=f"/u/{person.handle}",
    )
    return RedirectResponse(f"/u/{handle}", status_code=303)


@router.get("/me/reputation")
async def reputation_dashboard(request: Request, db: DbDep, user: Reader, page: int = 1):
    """The full ledger. Every point, where it came from, and what it is worth —
    the PRD calls for no black box, and this is that promise kept."""
    page = max(1, page)
    per_page = 50
    events = await rep.ledger(db, user.id, limit=per_page + 1, offset=(page - 1) * per_page)
    has_more = len(events) > per_page

    return templates.TemplateResponse(
        request,
        "profiles/reputation.html",
        {
            "title": "Your reputation",
            "events": events[:per_page],
            "has_more": has_more,
            "page": page,
            "breakdown": rep.pillar_breakdown(user),
            "capabilities": [
                ("Post, comment and vote", "Newcomer"),
                ("Create polls and join project boards", "Contributor"),
                ("Unlimited posting and skill search", "Contributor"),
                ("Create groups and host events", "Established"),
                ("Community moderation queue", "Trusted"),
                ("Mark verified answers", "Trusted"),
                ("Stand for elected moderator", "Pillar"),
            ],
        },
    )
