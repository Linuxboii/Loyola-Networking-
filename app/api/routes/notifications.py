"""Notifications. Polled by the app; deliberately not a push firehose."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.schemas import notification_out
from app.deps import DbDep, Reader
from app.models import Notification, utcnow
from app.services import notify

router = APIRouter(prefix="/notifications", tags=["api:notifications"])


@router.get("")
async def index(db: DbDep, user: Reader, limit: int = Query(50, ge=1, le=100)):
    rows = await notify.recent(db, user.id, limit=limit)
    return {
        "unread": await notify.unread_count(db, user.id),
        "notifications": [notification_out(n) for n in rows],
    }


@router.get("/unread-count")
async def unread(db: DbDep, user: Reader):
    """Cheap enough for the app to poll while it is in the foreground."""
    return {"unread": await notify.unread_count(db, user.id)}


@router.post("/read", status_code=204)
async def mark_all_read(db: DbDep, user: Reader):
    await notify.mark_all_read(db, user.id)
    return Response(status_code=204)


@router.post("/{notification_id}/read", status_code=204)
async def mark_one_read(notification_id: int, db: DbDep, user: Reader):
    row = await db.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "No such notification.")
    if row.read_at is None:
        row.read_at = utcnow()
    return Response(status_code=204)
