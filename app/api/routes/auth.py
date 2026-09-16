"""Token auth for the mobile client.

The token returned here is the *same* opaque session token the browser keeps in
an httponly cookie — the Android app simply carries it in an Authorization
header instead. That keeps one sessions table, one revocation path and one
expiry rule across both clients.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.api.schemas import LoginIn, PasswordChangeIn, SignupIn, me_out
from app.config import settings
from app.deps import CAPABILITIES, DbDep, RequireUser, can, client_ip, session_token_from
from app.models import Session as SessionModel
from app.models import User, utcnow
from app.routers.auth import HANDLE_RE, RESERVED, start_session
from app.security import (
    hash_password,
    needs_rehash,
    password_problem,
    token_hash,
    verify_password,
)
from app.services import antiabuse
from app.services.notify import unread_count

router = APIRouter(prefix="/auth", tags=["api:auth"])


def _capability_map(user: User) -> dict[str, bool]:
    return {name: can(user, name) for name in CAPABILITIES}


async def _session_payload(db, user: User, request: Request) -> dict:
    # start_session wants a Response to hang a cookie on; the mobile client
    # ignores the cookie and reads the returned token instead.
    raw = await start_session(db, user, request, Response())
    unread = await unread_count(db, user.id) if user.tier >= 1 else 0
    return {
        "token": raw,
        "expires_at": (utcnow() + dt.timedelta(days=settings.session_days)).isoformat(),
        "user": me_out(user, unread=unread, capabilities=_capability_map(user)),
    }


@router.post("/signup", status_code=201)
async def signup(payload: SignupIn, request: Request, db: DbDep):
    handle = payload.handle.strip().lower()
    full_name = payload.full_name.strip()

    if not HANDLE_RE.match(handle):
        raise HTTPException(422, "Username must be 3-24 characters: lowercase letters, numbers or underscores.")
    if handle in RESERVED:
        raise HTTPException(422, "That username is reserved.")
    if len(full_name) < 3:
        raise HTTPException(422, "Enter your full name as printed on your ID card.")
    problem = password_problem(payload.password)
    if problem:
        raise HTTPException(422, problem)
    if not payload.consent:
        raise HTTPException(422, "You must accept the privacy notice to continue.")

    taken = (await db.execute(select(User.id).where(User.handle == handle))).scalar_one_or_none()
    if taken:
        raise HTTPException(409, "That username is taken.")

    allowed, _ = await antiabuse.hit_rate_limit(
        db, f"signup:ip:{client_ip(request)}", limit=5, window=dt.timedelta(hours=6)
    )
    if not allowed:
        raise HTTPException(429, "Too many accounts created from this connection. Try again later.")

    user = User(
        handle=handle,
        full_name=full_name[:120],
        password_hash=hash_password(payload.password),
        tier=0,
        consent_at=utcnow(),
    )
    db.add(user)
    await db.flush()
    return await _session_payload(db, user, request)


@router.post("/login")
async def login(payload: LoginIn, request: Request, db: DbDep):
    identifier = payload.identifier.strip()
    ip = client_ip(request)

    ok_ip, _ = await antiabuse.hit_rate_limit(db, f"login:ip:{ip}", 20, dt.timedelta(minutes=15))
    ok_id, _ = await antiabuse.hit_rate_limit(
        db, f"login:id:{identifier.lower()}", 8, dt.timedelta(minutes=15)
    )
    if not (ok_ip and ok_id):
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")

    user = (
        await db.execute(
            select(User).where(
                (User.handle == identifier.lower()) | (User.roll_number == identifier.upper())
            )
        )
    ).scalar_one_or_none()

    if user is None or not verify_password(user.password_hash, payload.password):
        raise HTTPException(401, "Those details do not match an account.")
    if user.status == "banned":
        raise HTTPException(403, "This account has been permanently suspended.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    return await _session_payload(db, user, request)


@router.get("/me")
async def me(db: DbDep, user: RequireUser):
    unread = await unread_count(db, user.id) if user.tier >= 1 else 0
    return me_out(user, unread=unread, capabilities=_capability_map(user))


@router.post("/logout", status_code=204)
async def logout(request: Request, db: DbDep, user: RequireUser):
    raw = session_token_from(request)
    if raw:
        row = (
            await db.execute(select(SessionModel).where(SessionModel.token_hash == token_hash(raw)))
        ).scalar_one_or_none()
        if row is not None and row.user_id == user.id:
            row.revoked_at = utcnow()
    return Response(status_code=204)


@router.get("/sessions")
async def list_sessions(request: Request, db: DbDep, user: RequireUser):
    rows = (
        await db.execute(
            select(SessionModel)
            .where(SessionModel.user_id == user.id, SessionModel.revoked_at.is_(None))
            .order_by(SessionModel.created_at.desc())
            .limit(30)
        )
    ).scalars().all()
    current = token_hash(session_token_from(request) or "")
    return [
        {
            "id": row.id,
            "user_agent": row.user_agent,
            "ip": row.ip,
            "created_at": row.created_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "current": row.token_hash == current,
        }
        for row in rows
    ]


@router.post("/sessions/{session_id}/revoke", status_code=204)
async def revoke_session(session_id: int, db: DbDep, user: RequireUser):
    row = await db.get(SessionModel, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "No such session.")
    row.revoked_at = utcnow()
    return Response(status_code=204)


@router.post("/password", status_code=204)
async def change_password(payload: PasswordChangeIn, request: Request, db: DbDep, user: RequireUser):
    if not verify_password(user.password_hash, payload.current):
        raise HTTPException(403, "Your current password is not correct.")
    problem = password_problem(payload.password)
    if problem:
        raise HTTPException(422, problem)

    user.password_hash = hash_password(payload.password)
    keep = token_hash(session_token_from(request) or "")
    rows = (
        await db.execute(
            select(SessionModel).where(
                SessionModel.user_id == user.id,
                SessionModel.revoked_at.is_(None),
                SessionModel.token_hash != keep,
            )
        )
    ).scalars().all()
    for row in rows:
        row.revoked_at = utcnow()
    return Response(status_code=204)
