"""Notification centre."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.deps import DbDep, Reader, verify_csrf
from app.services import notify
from app.templating import templates

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
async def index(request: Request, db: DbDep, user: Reader):
    items = await notify.recent(db, user.id, limit=80)
    unread = [n for n in items if n.read_at is None]
    await notify.mark_all_read(db, user.id)
    return templates.TemplateResponse(
        request,
        "notifications/index.html",
        {
            "title": "Alerts",
            "items": items,
            "unread_ids": {n.id for n in unread},
        },
    )


@router.post("/notifications/read")
async def mark_read(request: Request, db: DbDep, user: Reader):
    await verify_csrf(request)
    await notify.mark_all_read(db, user.id)
    if request.headers.get("HX-Request"):
        return JSONResponse({"ok": True})
    return RedirectResponse("/notifications", status_code=303)
