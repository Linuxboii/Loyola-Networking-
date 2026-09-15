"""Resource vault: notes, question papers, lab manuals, cheat sheets.

The copyright guardrail from PRD 7.8 is enforced at upload rather than left to
policy text: uploaders must attest the material is their own work or a
college-issued paper, obvious textbook filenames are refused outright, and
anyone can file a takedown that pulls the file immediately pending review.
"""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import settings
from app.deps import DbDep, Reader, Verified, verify_csrf
from app.models import Resource, ResourceVote, TakedownRequest, utcnow
from app.services import antiabuse, media, notify
from app.services import moderation as mod
from app.services import reputation as rep
from app.services import search as search_service
from app.templating import templates

router = APIRouter(prefix="/vault", tags=["vault"])

KINDS = [
    ("notes", "Notes"),
    ("paper", "Past question paper"),
    ("manual", "Lab manual"),
    ("cheatsheet", "Cheat sheet"),
]

# Filenames that are almost always a scanned textbook rather than student notes.
COPYRIGHT_SMELL = re.compile(
    r"(textbook|solution[s]?[ _-]?manual|schaum|pearson|mcgraw|wiley|oreilly|o'reilly"
    r"|tata[ _-]?mcgraw|himalaya[ _-]?publish|s[ _-]?chand|galgotia|full[ _-]?book"
    r"|\b\d+(st|nd|rd|th)[ _-]?edition\b)",
    re.I,
)

ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "webp"}


@router.get("")
async def index(
    request: Request,
    db: DbDep,
    user: Reader,
    q: str = "",
    course: str = "",
    semester: str = "",
    subject: str = "",
    kind: str = "",
):
    resources = await search_service.resources(
        db,
        q.strip(),
        course=course.strip() or None,
        semester=semester.strip() or None,
        subject=subject.strip() or None,
        kind=kind or None,
        limit=80,
    )
    my_votes = {
        int(rid)
        for rid in (
            await db.execute(select(ResourceVote.resource_id).where(ResourceVote.user_id == user.id))
        ).scalars().all()
    }
    return templates.TemplateResponse(
        request,
        "vault/index.html",
        {
            "title": "Resource vault",
            "resources": resources,
            "q": q,
            "course": course,
            "semester": semester,
            "subject": subject,
            "kind": kind,
            "kinds": KINDS,
            "my_votes": my_votes,
        },
    )


@router.get("/upload")
async def upload_form(request: Request, db: DbDep, user: Verified):
    return templates.TemplateResponse(
        request, "vault/upload.html", {"title": "Share notes", "kinds": KINDS}
    )


@router.post("/upload")
async def upload(
    request: Request,
    db: DbDep,
    user: Verified,
    title: str = Form(...),
    description: str = Form(""),
    course: str = Form(""),
    semester: str = Form(""),
    subject: str = Form(""),
    kind: str = Form("notes"),
    attest: str = Form(""),
    replaces: str = Form(""),
    document: UploadFile = File(...),
):
    await verify_csrf(request)

    if attest != "yes":
        raise HTTPException(
            status_code=400,
            detail="You must confirm this is your own work or a college-issued paper.",
        )

    allowed, _ = await antiabuse.hit_rate_limit(db, f"vault:{user.id}", 10, dt.timedelta(days=1))
    if not allowed:
        raise HTTPException(status_code=429, detail="Ten uploads a day is the limit.")

    filename = media.safe_filename(document.filename or "document")
    if COPYRIGHT_SMELL.search(filename) or COPYRIGHT_SMELL.search(title):
        raise HTTPException(
            status_code=400,
            detail="That looks like published course material. Only original notes and "
            "college-issued papers can be shared here.",
        )

    data = await document.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"Files must be under {settings.max_upload_mb} MB.")

    ext = media.sniff(data)
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Upload a PDF or an image.")

    parent_id = None
    version = 1
    if replaces.isdigit():
        parent = await db.get(Resource, int(replaces))
        if parent and parent.uploader_id == user.id:
            parent_id = parent.id
            version = parent.version + 1
            parent.status = "superseded"

    resource = Resource(
        uploader_id=user.id,
        title=title.strip()[:180],
        description=(description or "").strip()[:2000],
        course=(course or "").strip()[:80] or None,
        semester=(semester or "").strip()[:20] or None,
        subject=(subject or "").strip()[:100] or None,
        kind=kind if kind in {k for k, _ in KINDS} else "notes",
        file_path=media.save_public(data, ext=ext, subdir="vault"),
        file_name=filename,
        file_size=len(data),
        version=version,
        parent_id=parent_id,
        origin_attested=True,
    )
    db.add(resource)
    await db.flush()

    await rep.award(
        db, user.id, "resource_published", source_type="resource", source_id=resource.id,
        detail=resource.title[:120],
    )
    return RedirectResponse(f"/vault/{resource.id}", status_code=303)


@router.get("/{resource_id}")
async def detail(request: Request, db: DbDep, user: Reader, resource_id: int):
    resource = (
        await db.execute(
            select(Resource).options(joinedload(Resource.uploader)).where(Resource.id == resource_id)
        )
    ).unique().scalar_one_or_none()
    if resource is None or (resource.status == "removed" and not user.is_moderator):
        raise HTTPException(status_code=404)

    versions = list(
        (
            await db.execute(
                select(Resource)
                .where((Resource.parent_id == resource_id) | (Resource.id == resource.parent_id))
                .order_by(Resource.version.desc())
            )
        ).scalars().all()
    )
    voted = (
        await db.execute(
            select(ResourceVote).where(
                ResourceVote.resource_id == resource_id, ResourceVote.user_id == user.id
            )
        )
    ).scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "vault/detail.html",
        {
            "title": resource.title,
            "resource": resource,
            "versions": versions,
            "voted": voted is not None,
            "is_image": resource.file_path.rsplit(".", 1)[-1].lower() in {"png", "jpg", "jpeg", "webp"},
        },
    )


@router.get("/{resource_id}/download")
async def download(db: DbDep, user: Reader, resource_id: int):
    resource = await db.get(Resource, resource_id)
    if resource is None or resource.status not in {"active", "superseded"}:
        raise HTTPException(status_code=404)

    data = media.read_public(resource.file_path)
    if data is None:
        raise HTTPException(status_code=404)
    resource.download_count = (resource.download_count or 0) + 1

    ext = resource.file_path.rsplit(".", 1)[-1].lower()
    ctype = {"pdf": "application/pdf", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    safe = media.safe_filename(resource.file_name or f"resource.{ext}")
    return Response(
        content=data,
        media_type=ctype,
        headers={"Content-Disposition": f'inline; filename="{safe}"'},
    )


@router.post("/{resource_id}/vote")
async def vote(request: Request, db: DbDep, user: Verified, resource_id: int):
    await verify_csrf(request)
    resource = await db.get(Resource, resource_id)
    if resource is None or resource.status != "active":
        raise HTTPException(status_code=404)
    if resource.uploader_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot vote on your own upload.")

    existing = (
        await db.execute(
            select(ResourceVote).where(
                ResourceVote.resource_id == resource_id, ResourceVote.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        resource.score = max(0, resource.score - 1)
        await rep.void_events(
            db, source_type="resource_vote", source_id=existing.id, reason_note="vote withdrawn"
        )
    else:
        row = ResourceVote(resource_id=resource_id, user_id=user.id)
        db.add(row)
        resource.score += 1
        await db.flush()
        await rep.award(
            db,
            resource.uploader_id,
            "resource_upvote",
            multiplier=rep.vote_weight(user, 0),
            source_type="resource_vote",
            source_id=row.id,
            actor_id=user.id,
            detail=resource.title[:120],
        )
    return RedirectResponse(f"/vault/{resource_id}", status_code=303)


@router.post("/{resource_id}/takedown")
async def takedown(
    request: Request,
    db: DbDep,
    user: Verified,
    resource_id: int,
    reason: str = Form(...),
    claimant: str = Form(""),
):
    """A takedown pulls the file immediately, then a moderator decides.

    Taking it down first is the right default: the cost of a wrongly hidden set
    of notes is a day's inconvenience, and the cost of hosting infringing
    material while a queue clears is the app being thrown off campus.
    """
    await verify_csrf(request)
    resource = await db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(status_code=404)

    db.add(
        TakedownRequest(
            resource_id=resource_id,
            requester_id=user.id,
            claimant=(claimant or "").strip()[:160],
            reason=(reason or "").strip()[:2000],
        )
    )
    resource.status = "takedown"
    await mod.log_action(
        db,
        action="takedown_requested",
        target_type="resource",
        target_id=resource_id,
        actor_id=user.id,
        subject_user_id=resource.uploader_id,
        reason=reason[:500],
    )
    await notify.push(
        db,
        resource.uploader_id,
        kind="moderation",
        title="A takedown was filed against your upload",
        body="It is hidden while a moderator reviews it.",
        link=f"/vault/{resource_id}",
    )
    return RedirectResponse(f"/vault/{resource_id}", status_code=303)
