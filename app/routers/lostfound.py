"""Lost & found. Trivial to build, disproportionately used."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, verify_csrf
from app.models import LostFound, utcnow
from app.services import antiabuse, media, notify
from app.services import reputation as rep
from app.templating import templates

router = APIRouter(prefix="/lost-found", tags=["lostfound"])


@router.get("")
async def index(request: Request, db: DbDep, user: Reader, kind: str = "", q: str = ""):
    stmt = (
        select(LostFound)
        .options(joinedload(LostFound.reporter))
        .where(LostFound.status == "open")
    )
    if kind in {"lost", "found"}:
        stmt = stmt.where(LostFound.kind == kind)
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                LostFound.title.ilike(needle),
                LostFound.description.ilike(needle),
                LostFound.location.ilike(needle),
            )
        )
    items = list((await db.execute(stmt.order_by(LostFound.created_at.desc()).limit(80))).unique().scalars().all())

    resolved = list(
        (
            await db.execute(
                select(LostFound)
                .options(joinedload(LostFound.reporter))
                .where(LostFound.status == "resolved")
                .order_by(LostFound.created_at.desc())
                .limit(10)
            )
        ).unique().scalars().all()
    )

    return templates.TemplateResponse(
        request,
        "lostfound/index.html",
        {"title": "Lost and found", "items": items, "resolved": resolved, "kind": kind, "q": q},
    )


@router.post("/new")
async def create(
    request: Request,
    db: DbDep,
    user: Verified,
    kind: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    location: str = Form(""),
    happened_on: str = Form(""),
    photo: UploadFile | None = File(None),
):
    await verify_csrf(request)
    if kind not in {"lost", "found"}:
        raise HTTPException(status_code=400, detail="Say whether it was lost or found.")

    allowed, _ = await antiabuse.hit_rate_limit(db, f"lostfound:{user.id}", 8, dt.timedelta(days=1))
    if not allowed:
        raise HTTPException(status_code=429, detail="That is a lot of posts for one day.")

    when = None
    if happened_on:
        try:
            when = dt.date.fromisoformat(happened_on)
        except ValueError:
            when = None

    image_path = None
    if photo is not None and photo.filename:
        data = await photo.read()
        if data:
            if not media.is_image(data):
                raise HTTPException(status_code=400, detail="That attachment is not an image.")
            image_path = media.save_public(data, subdir="lostfound")

    item = LostFound(
        reporter_id=user.id,
        kind=kind,
        title=title.strip()[:160],
        description=(description or "").strip()[:2000],
        location=(location or "").strip()[:120],
        happened_on=when,
        image_path=image_path,
    )
    db.add(item)
    await db.flush()
    return RedirectResponse("/lost-found", status_code=303)


@router.post("/{item_id}/resolve")
async def resolve(
    request: Request, db: DbDep, user: Verified, item_id: int, helper: str = Form("")
):
    """Close the listing, and credit whoever actually returned the thing."""
    await verify_csrf(request)
    item = await db.get(LostFound, item_id)
    if item is None:
        raise HTTPException(status_code=404)
    if item.reporter_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your listing.")

    item.status = "resolved"

    handle = (helper or "").strip().lstrip("@").lower()
    if handle:
        from app.models import User

        person = (await db.execute(select(User).where(User.handle == handle))).scalar_one_or_none()
        if person and person.id != user.id:
            await rep.award(
                db,
                person.id,
                "lostfound_resolved",
                source_type="lostfound",
                source_id=item.id,
                actor_id=user.id,
                detail=f"Returned: {item.title[:80]}",
            )
            await notify.push(
                db,
                person.id,
                kind="lostfound",
                title=f"{user.full_name} credited you for returning something",
                body=item.title[:180],
                link="/lost-found",
            )
    return RedirectResponse("/lost-found", status_code=303)


@router.post("/{item_id}/delete")
async def delete(request: Request, db: DbDep, user: Verified, item_id: int):
    await verify_csrf(request)
    item = await db.get(LostFound, item_id)
    if item is None:
        raise HTTPException(status_code=404)
    if item.reporter_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your listing.")
    item.status = "removed"
    return RedirectResponse("/lost-found", status_code=303)
