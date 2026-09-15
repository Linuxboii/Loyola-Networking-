"""First-boot seeding: skills vocabulary and default runtime settings."""
from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, Skill
from app.services.settings_store import set_setting

SEED_SKILLS = [
    "Python", "Java", "C", "C++", "JavaScript", "TypeScript", "Flutter", "Dart",
    "React", "Node.js", "Django", "FastAPI", "Spring Boot", "Android", "iOS",
    "HTML/CSS", "Tailwind", "SQL", "PostgreSQL", "MongoDB", "Firebase",
    "Machine Learning", "Deep Learning", "Data Science", "Pandas", "NumPy",
    "Computer Vision", "NLP", "Power BI", "Excel", "Tableau", "R",
    "UI Design", "UX Research", "Figma", "Photoshop", "Illustrator",
    "Video Editing", "Premiere Pro", "After Effects", "Photography",
    "Content Writing", "Copywriting", "Public Speaking", "Debate", "Anchoring",
    "Event Management", "Sponsorship", "Social Media", "Marketing",
    "Accounting", "Tally", "Finance", "Stock Markets", "Economics",
    "Networking", "Linux", "Docker", "Git", "Cloud/AWS", "Cybersecurity",
    "Arduino", "IoT", "Robotics", "3D Modelling", "AutoCAD",
    "Music", "Dance", "Theatre", "Sketching", "Sports Coaching",
]


def slugify(value: str) -> str:
    """Slugify a skill name.

    Naive slugification collapses "C" and "C++" onto the same slug, which is a
    real collision in a list of programming languages — so the characters that
    carry the distinction are spelled out before they get stripped.
    """
    text = value.lower().strip()
    text = text.replace("++", "pp").replace("#", "sharp")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug[:50] or "skill"


async def ensure_seed_settings(db: AsyncSession) -> None:
    existing = int((await db.execute(select(func.count(Skill.id)))).scalar_one() or 0)
    if existing == 0:
        seen: set[str] = set()
        for name in SEED_SKILLS:
            slug = slugify(name)
            if slug in seen:
                continue
            seen.add(slug)
            db.add(Skill(slug=slug, name=name))
        await db.flush()

    # Seed the OCR template row so the admin console has something to edit,
    # without overwriting a template an admin has already tuned.
    has_template = await db.get(AppSetting, "ocr_template")
    if has_template is None:
        from app.ocr import DEFAULT_TEMPLATE

        await set_setting(db, "ocr_template", DEFAULT_TEMPLATE)

    if await db.get(AppSetting, "moderation_lists") is None:
        await set_setting(db, "moderation_lists", {"profanity": [], "hard_block": [], "crisis": []})

    if await db.get(AppSetting, "campus") is None:
        await set_setting(
            db,
            "campus",
            {
                "departments": [
                    "Computer Science", "Computer Applications", "Commerce",
                    "Business Administration", "Life Sciences", "Physics",
                    "Mathematics", "Statistics", "English", "Economics",
                    "Electronics", "Microbiology", "Biotechnology", "Psychology",
                ],
                "semesters": ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"],
            },
        )


async def ensure_skill(db: AsyncSession, name: str) -> Skill:
    slug = slugify(name)
    stmt = (
        pg_insert(Skill)
        .values(slug=slug, name=name.strip()[:50], usage_count=0)
        .on_conflict_do_nothing(index_elements=[Skill.slug])
    )
    await db.execute(stmt)
    skill = (await db.execute(select(Skill).where(Skill.slug == slug))).scalar_one()
    return skill
