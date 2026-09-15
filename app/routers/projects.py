"""Project board and team finder."""
from __future__ import annotations

import re

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, can, verify_csrf
from app.models import REP_TIERS, Application, Project, ProjectRole, User
from app.services import notify
from app.services import reputation as rep
from app.services import search as search_service
from app.services.reputation import tier_index
from app.templating import templates

router = APIRouter(prefix="/projects", tags=["projects"])

REPO_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "huggingface.co")


def _skills(raw: str | None) -> list[str]:
    out: list[str] = []
    for s in (raw or "").replace(",", " ").split():
        s = s.strip().lstrip("#").lower()
        if s and s not in out and len(s) <= 40:
            out.append(s)
    return out[:10]


def _looks_verifiable(project: Project) -> bool:
    """'Verifiable artifact' from PRD 6.2 means something a stranger can open.

    We check the shape of the links rather than fetching them: outbound requests
    from the verification path would be an SSRF surface for no real gain.
    """
    repo = (project.repo_url or "").lower()
    has_repo = any(host in repo for host in REPO_HOSTS)
    has_demo = bool(project.demo_url and re.match(r"^https?://", project.demo_url))
    has_video = bool(project.video_url and re.match(r"^https?://", project.video_url))
    return has_repo or (has_demo and has_video) or (has_demo and len(project.description or "") > 400)


@router.get("")
async def index(
    request: Request, db: DbDep, user: Reader, q: str = "", skill: str = "", show: str = "open"
):
    projects = await search_service.projects(
        db, q.strip(), skill=skill.strip() or None, open_only=(show == "open"), limit=60
    )
    my_applications = {
        int(pid)
        for pid in (
            await db.execute(select(Application.project_id).where(Application.applicant_id == user.id))
        ).scalars().all()
    }
    return templates.TemplateResponse(
        request,
        "projects/index.html",
        {
            "title": "Project board",
            "projects": projects,
            "q": q,
            "skill": skill,
            "show": show,
            "my_applications": my_applications,
        },
    )


@router.get("/new")
async def new_form(request: Request, db: DbDep, user: Verified):
    if not can(user, "post_project"):
        raise HTTPException(status_code=403, detail="Posting projects unlocks at Contributor standing.")
    return templates.TemplateResponse(request, "projects/new.html", {"title": "Post a project"})


@router.post("/new")
async def create(
    request: Request,
    db: DbDep,
    user: Verified,
    title: str = Form(...),
    summary: str = Form(""),
    description: str = Form(""),
    repo_url: str = Form(""),
    demo_url: str = Form(""),
    video_url: str = Form(""),
    skills_needed: str = Form(""),
    min_rep_tier: str = Form("Newcomer"),
    roles: str = Form(""),
):
    await verify_csrf(request)
    if not can(user, "post_project"):
        raise HTTPException(status_code=403, detail="Posting projects unlocks at Contributor standing.")

    title = (title or "").strip()
    if len(title) < 4:
        raise HTTPException(status_code=400, detail="Give the project a name.")

    def clean_url(value: str) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        return value[:400]

    project = Project(
        owner_id=user.id,
        title=title[:160],
        summary=(summary or "").strip()[:300],
        description=(description or "").strip()[:8000],
        repo_url=clean_url(repo_url),
        demo_url=clean_url(demo_url),
        video_url=clean_url(video_url),
        skills_needed=_skills(skills_needed),
        min_rep_tier=min_rep_tier if min_rep_tier in {t for t, _ in REP_TIERS} else "Newcomer",
    )
    project.artifact_verified = _looks_verifiable(project)
    db.add(project)
    await db.flush()

    for line in (roles or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # "Backend developer: python, postgres x2"
        slots = 1
        match = re.search(r"x\s*(\d+)\s*$", line, re.I)
        if match:
            slots = max(1, min(20, int(match.group(1))))
            line = line[: match.start()].strip()
        name, _, skill_part = line.partition(":")
        db.add(
            ProjectRole(
                project_id=project.id,
                title=name.strip()[:80] or "Contributor",
                skills=_skills(skill_part),
                slots=slots,
            )
        )

    await rep.award(
        db,
        user.id,
        "project_artifact_verified" if project.artifact_verified else "project_published",
        source_type="project",
        source_id=project.id,
        detail=project.title[:120],
    )
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/{project_id}")
async def detail(request: Request, db: DbDep, user: Reader, project_id: int):
    project = (
        await db.execute(
            select(Project).options(joinedload(Project.owner)).where(Project.id == project_id)
        )
    ).unique().scalar_one_or_none()
    if project is None or project.status == "removed":
        raise HTTPException(status_code=404)

    roles = list(
        (await db.execute(select(ProjectRole).where(ProjectRole.project_id == project_id))).scalars().all()
    )
    is_owner = project.owner_id == user.id
    applications = []
    if is_owner or user.is_moderator:
        applications = list(
            (
                await db.execute(
                    select(Application)
                    .options(joinedload(Application.applicant))
                    .where(Application.project_id == project_id)
                    .order_by(Application.created_at.desc())
                )
            ).unique().scalars().all()
        )

    mine = (
        await db.execute(
            select(Application).where(
                Application.project_id == project_id, Application.applicant_id == user.id
            )
        )
    ).scalar_one_or_none()

    eligible = tier_index(user.rep_tier) >= tier_index(project.min_rep_tier)

    return templates.TemplateResponse(
        request,
        "projects/detail.html",
        {
            "title": project.title,
            "project": project,
            "roles": roles,
            "is_owner": is_owner,
            "applications": applications,
            "my_application": mine,
            "eligible": eligible,
        },
    )


@router.post("/{project_id}/apply")
async def apply(
    request: Request,
    db: DbDep,
    user: Verified,
    project_id: int,
    message: str = Form(""),
    role_id: str = Form(""),
):
    await verify_csrf(request)
    if not can(user, "join_project_board"):
        raise HTTPException(status_code=403, detail="Joining project boards unlocks at Contributor standing.")

    project = await db.get(Project, project_id)
    if project is None or project.status != "open":
        raise HTTPException(status_code=404, detail="That project is not taking applications.")
    if project.owner_id == user.id:
        raise HTTPException(status_code=400, detail="It is your own project.")
    if tier_index(user.rep_tier) < tier_index(project.min_rep_tier):
        raise HTTPException(
            status_code=403, detail=f"This project asks for {project.min_rep_tier} standing."
        )

    existing = (
        await db.execute(
            select(Application).where(
                Application.project_id == project_id, Application.applicant_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="You have already applied.")

    rid = None
    if role_id.isdigit():
        rid = int(role_id)

    db.add(
        Application(
            project_id=project_id,
            role_id=rid,
            applicant_id=user.id,
            message=(message or "").strip()[:2000],
        )
    )
    await notify.push(
        db,
        project.owner_id,
        kind="application",
        title=f"{user.full_name} applied to {project.title}",
        body=(message or "")[:200],
        link=f"/projects/{project_id}",
    )
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/{project_id}/applications/{application_id}")
async def decide(
    request: Request,
    db: DbDep,
    user: Verified,
    project_id: int,
    application_id: int,
    decision: str = Form(...),
):
    await verify_csrf(request)
    project = await db.get(Project, project_id)
    application = await db.get(Application, application_id)
    if project is None or application is None or application.project_id != project_id:
        raise HTTPException(status_code=404)
    if project.owner_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your project.")
    if decision not in {"accepted", "declined"}:
        raise HTTPException(status_code=400, detail="Unknown decision.")

    application.status = decision
    if decision == "accepted" and application.role_id:
        role = await db.get(ProjectRole, application.role_id)
        if role:
            role.filled = min(role.slots, role.filled + 1)

    await notify.push(
        db,
        application.applicant_id,
        kind="application",
        title=f"Your application to {project.title} was {decision}",
        link=f"/projects/{project_id}",
    )
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/{project_id}/status")
async def set_status(
    request: Request, db: DbDep, user: Verified, project_id: int, status: str = Form(...)
):
    await verify_csrf(request)
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404)
    if project.owner_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your project.")
    if status not in {"open", "closed"}:
        raise HTTPException(status_code=400, detail="Unknown status.")
    project.status = status
    return RedirectResponse(f"/projects/{project_id}", status_code=303)
