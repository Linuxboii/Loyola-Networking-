"""Search over people, skills, Q&A, resources and opportunities.

Postgres does all of it — ``to_tsvector`` for prose, ``pg_trgm`` for names and
typo tolerance. The PRD suggests Meilisearch or Typesense; at ~5,000 users and a
1.9 GB box, a second search daemon would cost more RAM than the entire web app
and buy nothing measurable. The indexes live in ``sql/indexes.sql``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import (
    REP_TIERS,
    Opportunity,
    Project,
    Question,
    Resource,
    Skill,
    User,
    UserSkill,
)
from app.services.reputation import tier_index


def _tsquery(q: str):
    """websearch_to_tsquery handles quotes and OR the way users expect."""
    return func.websearch_to_tsquery("english", q)


async def people(
    db: AsyncSession,
    q: str,
    *,
    viewer: User,
    skill: str | None = None,
    batch: int | None = None,
    department: str | None = None,
    min_tier: str | None = None,
    limit: int = 30,
) -> list[User]:
    stmt = select(User).where(
        User.tier >= 2,
        User.status == "active",
        User.hide_from_search.is_(False),
    )

    if q:
        similarity = func.similarity(User.full_name, q)
        stmt = stmt.where(
            or_(
                User.full_name.ilike(f"%{q}%"),
                User.handle.ilike(f"%{q}%"),
                similarity > 0.22,
                User.bio.ilike(f"%{q}%"),
            )
        ).order_by(similarity.desc().nullslast(), User.rep_total.desc())
    else:
        stmt = stmt.order_by(User.rep_total.desc())

    if skill:
        sub = (
            select(UserSkill.user_id)
            .join(Skill, Skill.id == UserSkill.skill_id)
            .where(or_(Skill.slug == skill.lower(), Skill.name.ilike(skill)))
        )
        stmt = stmt.where(User.id.in_(sub))
    if batch:
        stmt = stmt.where(User.batch_year == batch)
    if department:
        stmt = stmt.where(User.department.ilike(f"%{department}%"))
    if min_tier:
        keep = [label for label, _t in REP_TIERS if tier_index(label) >= tier_index(min_tier)]
        stmt = stmt.where(User.rep_tier.in_(keep))

    return list((await db.execute(stmt.limit(limit))).scalars().all())


async def questions(
    db: AsyncSession,
    q: str,
    *,
    subject: str | None = None,
    semester: str | None = None,
    tag: str | None = None,
    unanswered: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> list[Question]:
    stmt = (
        select(Question)
        .options(joinedload(Question.author))
        .where(Question.status == "active", Question.duplicate_of_id.is_(None))
    )
    if q:
        vector = func.to_tsvector(
            "english", func.concat(Question.title, text("' '"), Question.body)
        )
        rank = func.ts_rank(vector, _tsquery(q))
        stmt = stmt.where(
            or_(vector.op("@@")(_tsquery(q)), Question.title.ilike(f"%{q}%"))
        ).order_by(rank.desc(), Question.score.desc())
    else:
        stmt = stmt.order_by(Question.created_at.desc())

    if subject:
        stmt = stmt.where(Question.subject.ilike(f"%{subject}%"))
    if semester:
        stmt = stmt.where(Question.semester == semester)
    if tag:
        stmt = stmt.where(Question.tags.any(tag))
    if unanswered:
        stmt = stmt.where(Question.answer_count == 0)

    return list((await db.execute(stmt.limit(limit).offset(offset))).unique().scalars().all())


async def find_duplicates(db: AsyncSession, title: str, limit: int = 5) -> list[Question]:
    """Surface near-identical questions *before* one more gets asked.

    Trigram similarity on the title is crude but it is what catches "how to
    install oracle for dbms lab" asked forty times a semester.
    """
    if len(title.strip()) < 8:
        return []
    similarity = func.similarity(Question.title, title)
    stmt = (
        select(Question)
        .where(Question.status == "active", Question.duplicate_of_id.is_(None), similarity > 0.35)
        .order_by(similarity.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def resources(
    db: AsyncSession,
    q: str,
    *,
    course: str | None = None,
    semester: str | None = None,
    subject: str | None = None,
    kind: str | None = None,
    limit: int = 40,
) -> list[Resource]:
    stmt = (
        select(Resource)
        .options(joinedload(Resource.uploader))
        .where(Resource.status == "active")
    )
    if q:
        stmt = stmt.where(
            or_(Resource.title.ilike(f"%{q}%"), Resource.description.ilike(f"%{q}%"), Resource.subject.ilike(f"%{q}%"))
        )
    if course:
        stmt = stmt.where(Resource.course.ilike(f"%{course}%"))
    if semester:
        stmt = stmt.where(Resource.semester == semester)
    if subject:
        stmt = stmt.where(Resource.subject.ilike(f"%{subject}%"))
    if kind:
        stmt = stmt.where(Resource.kind == kind)
    stmt = stmt.order_by(Resource.score.desc(), Resource.created_at.desc())
    return list((await db.execute(stmt.limit(limit))).unique().scalars().all())


async def projects(
    db: AsyncSession,
    q: str,
    *,
    skill: str | None = None,
    open_only: bool = True,
    limit: int = 30,
) -> list[Project]:
    stmt = select(Project).options(joinedload(Project.owner)).where(Project.status != "removed")
    if open_only:
        stmt = stmt.where(Project.status == "open")
    if q:
        stmt = stmt.where(
            or_(Project.title.ilike(f"%{q}%"), Project.summary.ilike(f"%{q}%"), Project.description.ilike(f"%{q}%"))
        )
    if skill:
        stmt = stmt.where(Project.skills_needed.any(skill.lower()))
    stmt = stmt.order_by(Project.created_at.desc())
    return list((await db.execute(stmt.limit(limit))).unique().scalars().all())


async def opportunities(
    db: AsyncSession,
    q: str,
    *,
    kind: str | None = None,
    official_only: bool = False,
    limit: int = 40,
) -> list[Opportunity]:
    stmt = select(Opportunity).options(joinedload(Opportunity.poster)).where(Opportunity.status == "active")
    if q:
        stmt = stmt.where(
            or_(
                Opportunity.company.ilike(f"%{q}%"),
                Opportunity.role_title.ilike(f"%{q}%"),
                Opportunity.description.ilike(f"%{q}%"),
            )
        )
    if kind:
        stmt = stmt.where(Opportunity.kind == kind)
    if official_only:
        stmt = stmt.where(Opportunity.is_official.is_(True))
    stmt = stmt.order_by(Opportunity.is_official.desc(), Opportunity.created_at.desc())
    return list((await db.execute(stmt.limit(limit))).unique().scalars().all())


async def skills(db: AsyncSession, q: str, limit: int = 20) -> list[Skill]:
    stmt = select(Skill)
    if q:
        stmt = stmt.where(or_(Skill.name.ilike(f"%{q}%"), Skill.slug.ilike(f"%{q}%")))
    return list((await db.execute(stmt.order_by(Skill.usage_count.desc()).limit(limit))).scalars().all())


async def everything(db: AsyncSession, q: str, viewer: User) -> dict[str, Any]:
    return {
        "people": await people(db, q, viewer=viewer, limit=8),
        "questions": await questions(db, q, limit=8),
        "resources": await resources(db, q, limit=8),
        "projects": await projects(db, q, open_only=False, limit=6),
        "opportunities": await opportunities(db, q, limit=6),
    }
