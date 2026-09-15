"""Sign up, sign in, sign out.

No phone OTP and no college email, per the brief. An account is therefore just a
handle and a password until the ID card proves who is behind it — which is why
Tier 0 can see nothing at all.
"""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, DbDep, RequireUser, client_ip, verify_csrf
from app.models import Session as SessionModel
from app.models import User, utcnow
from app.security import (
    device_fingerprint,
    hash_password,
    needs_rehash,
    new_session_token,
    password_problem,
    token_hash,
    verify_password,
)
from app.services import antiabuse
from app.templating import templates

router = APIRouter(tags=["auth"])

HANDLE_RE = re.compile(r"^[a-z0-9_]{3,24}$")
RESERVED = {
    "admin", "root", "moderator", "mod", "staff", "official", "loyola",
    "support", "help", "api", "static", "media", "me", "system", "null",
}


def _fp(request: Request) -> str:
    return device_fingerprint(
        request.headers.get("user-agent"),
        request.headers.get("accept-language"),
        client_ip(request),
    )


async def start_session(db, user: User, request: Request, response: Response) -> str:
    raw = new_session_token()
    fp = _fp(request)
    db.add(
        SessionModel(
            user_id=user.id,
            token_hash=token_hash(raw),
            device_fp=fp,
            ip=client_ip(request)[:64],
            user_agent=(request.headers.get("user-agent") or "")[:255],
            expires_at=utcnow() + dt.timedelta(days=settings.session_days),
        )
    )
    await antiabuse.record_device(db, user.id, fp)
    response.set_cookie(
        settings.session_cookie,
        raw,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.base_url.startswith("https"),
        path="/",
    )
    return raw


@router.get("/signup")
async def signup_form(request: Request, user: CurrentUser):
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "auth/signup.html", {"title": "Create your account"})


@router.post("/signup")
async def signup(
    request: Request,
    db: DbDep,
    handle: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    consent: str = Form(""),
):
    handle = (handle or "").strip().lower()
    full_name = (full_name or "").strip()
    errors: list[str] = []

    if not HANDLE_RE.match(handle):
        errors.append("Username must be 3-24 characters: lowercase letters, numbers or underscores.")
    elif handle in RESERVED:
        errors.append("That username is reserved.")
    if len(full_name) < 3:
        errors.append("Enter your full name as printed on your ID card.")
    if password != password2:
        errors.append("The two passwords do not match.")
    problem = password_problem(password or "")
    if problem:
        errors.append(problem)
    if consent != "yes":
        errors.append("You must accept the privacy notice to continue.")

    if not errors:
        taken = (await db.execute(select(User.id).where(User.handle == handle))).scalar_one_or_none()
        if taken:
            errors.append("That username is taken.")

    # An IP creating accounts in bulk is the cheapest signal we have.
    allowed, _ = await antiabuse.hit_rate_limit(
        db, f"signup:ip:{client_ip(request)}", limit=5, window=dt.timedelta(hours=6)
    )
    if not allowed:
        errors.append("Too many accounts created from this connection. Try again later.")

    if errors:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"title": "Create your account", "errors": errors, "handle": handle, "full_name": full_name},
            status_code=400,
        )

    user = User(
        handle=handle,
        full_name=full_name[:120],
        password_hash=hash_password(password),
        tier=0,
        consent_at=utcnow(),
    )
    db.add(user)
    await db.flush()

    response = RedirectResponse("/verify", status_code=303)
    await start_session(db, user, request, response)
    return response


@router.get("/login")
async def login_form(request: Request, user: CurrentUser, next: str = "/"):
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "auth/login.html", {"title": "Sign in", "next": next})


@router.post("/login")
async def login(
    request: Request,
    db: DbDep,
    identifier: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    identifier = (identifier or "").strip()
    ip = client_ip(request)

    # Throttle by identifier and by address: neither alone is enough.
    ok_ip, _ = await antiabuse.hit_rate_limit(db, f"login:ip:{ip}", 20, dt.timedelta(minutes=15))
    ok_id, _ = await antiabuse.hit_rate_limit(
        db, f"login:id:{identifier.lower()}", 8, dt.timedelta(minutes=15)
    )
    if not (ok_ip and ok_id):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Sign in", "errors": ["Too many attempts. Wait a few minutes and try again."]},
            status_code=429,
        )

    stmt = select(User).where(
        (User.handle == identifier.lower()) | (User.roll_number == identifier.upper())
    )
    user = (await db.execute(stmt)).scalar_one_or_none()

    if user is None or not verify_password(user.password_hash, password):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Sign in", "errors": ["Those details do not match an account."], "next": next},
            status_code=401,
        )
    if user.status == "banned":
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Sign in", "errors": ["This account has been permanently suspended."]},
            status_code=403,
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    target = next if next.startswith("/") and not next.startswith("//") else "/"
    if user.tier == 0:
        target = "/verify"
    response = RedirectResponse(target, status_code=303)
    await start_session(db, user, request, response)
    return response


@router.post("/logout")
async def logout(request: Request, db: DbDep, user: RequireUser, _csrf: None = None):
    await verify_csrf(request)
    raw = request.cookies.get(settings.session_cookie)
    if raw:
        row = (
            await db.execute(select(SessionModel).where(SessionModel.token_hash == token_hash(raw)))
        ).scalar_one_or_none()
        if row:
            row.revoked_at = utcnow()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie, path="/")
    return response


@router.get("/settings/account")
async def account_settings(request: Request, db: DbDep, user: RequireUser):
    sessions = list(
        (
            await db.execute(
                select(SessionModel)
                .where(SessionModel.user_id == user.id, SessionModel.revoked_at.is_(None))
                .order_by(SessionModel.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
    )
    current = token_hash(request.cookies.get(settings.session_cookie) or "")
    return templates.TemplateResponse(
        request,
        "auth/account.html",
        {"title": "Account", "sessions": sessions, "current_hash": current},
    )


@router.post("/settings/password")
async def change_password(
    request: Request,
    db: DbDep,
    user: RequireUser,
    current: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
):
    await verify_csrf(request)
    errors = []
    if not verify_password(user.password_hash, current):
        errors.append("Your current password is not correct.")
    if password != password2:
        errors.append("The two new passwords do not match.")
    problem = password_problem(password or "")
    if problem:
        errors.append(problem)

    if errors:
        return templates.TemplateResponse(
            request, "auth/account.html", {"title": "Account", "errors": errors, "sessions": []}, status_code=400
        )

    user.password_hash = hash_password(password)
    # Changing a password ends every other session, which is the whole point.
    keep = token_hash(request.cookies.get(settings.session_cookie) or "")
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

    return RedirectResponse("/settings/account?changed=1", status_code=303)


@router.post("/settings/sessions/{session_id}/revoke")
async def revoke_session(request: Request, db: DbDep, user: RequireUser, session_id: int):
    await verify_csrf(request)
    row = await db.get(SessionModel, session_id)
    if row and row.user_id == user.id:
        row.revoked_at = utcnow()
    return RedirectResponse("/settings/account", status_code=303)


@router.post("/settings/recovery-email")
async def set_recovery_email(
    request: Request, db: DbDep, user: RequireUser, email: str = Form("")
):
    await verify_csrf(request)
    email = (email or "").strip()
    # Stored unverified and used for nothing but a future reset flow — we say so
    # plainly in the UI rather than implying it has been checked.
    user.recovery_email = email[:160] or None
    return RedirectResponse("/settings/account?saved=1", status_code=303)
