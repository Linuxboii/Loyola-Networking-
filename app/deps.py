"""Request dependencies: who is asking, and what are they allowed to do.

Two independent gates, and conflating them is the classic bug:

* **Verification tier** (0-3) answers *is this a real campus member*. Tier 0 sees
  nothing at all — the PRD calls it a hard wall, so it is enforced in middleware
  rather than per-route.
* **Reputation tier** (Newcomer..Pillar) answers *what has this member earned*.
  It gates capabilities, never visibility.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import Session as SessionModel
from app.models import TIER_PROVISIONAL, TIER_VERIFIED, User, utcnow
from app.security import csrf_valid, token_hash
from app.services.reputation import tier_index

DbDep = Annotated[AsyncSession, Depends(get_db)]

# PRD 6.4 — what each reputation tier unlocks.
CAPABILITIES: dict[str, str] = {
    "post": "Newcomer",
    "comment": "Newcomer",
    "vote": "Newcomer",
    "ask_question": "Newcomer",
    "answer": "Newcomer",
    "create_poll": "Contributor",
    "join_project_board": "Contributor",
    "post_project": "Contributor",
    "unlimited_posting": "Contributor",
    "appear_in_skill_search": "Contributor",
    "create_group": "Established",
    "host_event": "Established",
    "host_study_room": "Established",
    "mentor": "Established",
    "moderation_queue": "Trusted",
    "merge_duplicates": "Trusted",
    "mark_verified_answer": "Trusted",
    "elected_moderator": "Pillar",
}

NEWCOMER_POSTS_PER_DAY = 5


class NeedsVerification(Exception):
    def __init__(self, tier: int) -> None:
        self.tier = tier


async def load_session_user(request: Request, db: AsyncSession) -> User | None:
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        return None
    row = (
        await db.execute(
            select(SessionModel).where(SessionModel.token_hash == token_hash(raw))
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        return None

    user = await db.get(User, row.user_id)
    if user is None or user.status == "banned":
        return None
    if user.status == "suspended":
        if user.suspended_until and user.suspended_until <= utcnow():
            user.status = "active"
            user.suspended_until = None
        else:
            request.state.suspended = True

    # Cheap liveness tracking; reputation decay keys off this.
    if (utcnow() - (user.last_active_at or utcnow())) > dt.timedelta(minutes=10):
        user.last_active_at = utcnow()

    request.state.session_token = raw
    return user


def template_snapshot(user: User) -> SimpleNamespace:
    """A detached, plain-attribute copy of the signed-in user for templates.

    Error handlers render *after* FastAPI has torn the request's session down,
    and a rollback expires every ORM attribute — so touching ``user.tier`` from
    a 403 page raises DetachedInstanceError and turns a clean 403 into a 500.
    Templates therefore read this snapshot, taken while the session is live,
    and routes keep the real ORM object for writes.
    """
    data = {attr.key: getattr(user, attr.key) for attr in inspect(user).mapper.column_attrs}
    snapshot = SimpleNamespace(**data)
    snapshot.is_moderator = user.is_moderator
    snapshot.is_admin = user.is_admin
    snapshot.display_year = user.display_year
    return snapshot


async def current_user(request: Request, db: DbDep) -> User | None:
    if getattr(request.state, "user_loaded", False):
        return getattr(request.state, "user", None)
    user = await load_session_user(request, db)
    request.state.user = user
    request.state.me = template_snapshot(user) if user is not None else None
    request.state.user_loaded = True

    # Chrome counts live on request.state so every template can read them
    # without each route remembering to pass them down.
    request.state.unread = 0
    request.state.open_reports = 0
    if user is not None and user.tier >= TIER_PROVISIONAL:
        from app.services.notify import unread_count

        request.state.unread = await unread_count(db, user.id)
        if user.is_moderator or tier_index(user.rep_tier) >= tier_index("Trusted"):
            from sqlalchemy import func

            from app.models import Report

            request.state.open_reports = int(
                (
                    await db.execute(
                        select(func.count(Report.id)).where(Report.status.in_(("open", "in_review")))
                    )
                ).scalar_one()
                or 0
            )
    return user


CurrentUser = Annotated[User | None, Depends(current_user)]


async def require_user(user: CurrentUser) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return user


RequireUser = Annotated[User, Depends(require_user)]


async def require_reader(user: RequireUser) -> User:
    """Tier 1+: may read. Tier 0 hits the hard wall."""
    if user.tier < TIER_PROVISIONAL:
        raise NeedsVerification(user.tier)
    return user


Reader = Annotated[User, Depends(require_reader)]


async def require_verified(user: RequireUser) -> User:
    """Tier 2+: may write, vote and be discoverable."""
    if user.tier < TIER_VERIFIED:
        raise NeedsVerification(user.tier)
    if user.status == "suspended":
        raise HTTPException(status_code=403, detail="Your account is suspended.")
    return user


Verified = Annotated[User, Depends(require_verified)]


def can(user: User, capability: str) -> bool:
    needed = CAPABILITIES.get(capability)
    if needed is None:
        return True
    if user.is_moderator and capability in {"moderation_queue", "merge_duplicates", "mark_verified_answer"}:
        return True
    return tier_index(user.rep_tier) >= tier_index(needed)


def require_capability(capability: str) -> Callable:
    async def _dep(user: Verified) -> User:
        if not can(user, capability):
            needed = CAPABILITIES.get(capability, "Contributor")
            raise HTTPException(
                status_code=403,
                detail=f"This needs {needed} standing. Keep contributing and it unlocks.",
            )
        return user

    return _dep


async def require_moderator(user: Verified) -> User:
    if not (user.is_moderator or can(user, "moderation_queue")):
        raise HTTPException(status_code=403, detail="Moderator access required.")
    return user


Moderator = Annotated[User, Depends(require_moderator)]


async def require_admin(user: RequireUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


Admin = Annotated[User, Depends(require_admin)]


async def verify_csrf(request: Request) -> None:
    """Every state-changing form carries a token derived from the session."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    token = request.cookies.get(settings.session_cookie)
    form = await request.form()
    supplied = form.get("csrf") or request.headers.get("X-CSRF-Token")
    if not csrf_valid(token, supplied if isinstance(supplied, str) else None):
        raise HTTPException(status_code=403, detail="Your session expired. Reload the page and try again.")


CsrfGuard = Annotated[None, Depends(verify_csrf)]


def client_ip(request: Request) -> str:
    # Behind nginx behind cloudflared, so the first forwarded hop is the client.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
