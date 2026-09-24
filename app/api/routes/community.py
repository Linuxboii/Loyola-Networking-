"""Mobile community controls: follows, badges, and scoped administration."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.deps import DbDep, Verified
from app.api.schemas import user_card
from app.models import AdminGrant, CommunityProfile, Follow, Notification, User, VerificationRecord, utcnow
from app.services import media, verification
from app.services.community_controls import active_actions, can_administer

router = APIRouter(tags=["api:community"])


class RoleChange(BaseModel):
    enabled: bool


class GrantIn(BaseModel):
    actions: list[str] = Field(min_length=1, max_length=3)
    expires_at: dt.datetime | None = None


class DecisionIn(BaseModel):
    outcome: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=500)


async def _actions(db, user: User) -> set[str]:
    rows = list((await db.execute(select(AdminGrant).where(AdminGrant.user_id == user.id, AdminGrant.revoked_at.is_(None)))).scalars())
    granted: set[str] = set()
    for row in rows:
        granted.update(active_actions(list(row.actions or []), row.expires_at))
    return granted


async def _need(db, user: User, action: str) -> None:
    if not can_administer(user, await _actions(db, user), action):
        raise HTTPException(403, "This administrator action was not delegated to you.")


@router.get("/people/{handle}/community")
async def community_profile(handle: str, db: DbDep, user: Verified):
    target = (await db.execute(select(User).where(User.handle == handle.lower()))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "No such member.")
    profile = await db.get(CommunityProfile, target.id)
    outgoing = (await db.execute(select(Follow).where(Follow.follower_id == user.id, Follow.following_id == target.id))).scalar_one_or_none()
    follows_you = int((await db.execute(select(func.count(Follow.id)).where(Follow.follower_id == target.id, Follow.following_id == user.id, Follow.status == "accepted"))).scalar_one() or 0)
    followers = int((await db.execute(select(func.count(Follow.id)).where(Follow.following_id == target.id, Follow.status == "accepted"))).scalar_one() or 0)
    following = int((await db.execute(select(func.count(Follow.id)).where(Follow.follower_id == target.id, Follow.status == "accepted"))).scalar_one() or 0)
    return {"is_og": bool(profile and profile.is_og), "verified": target.tier >= 2, "follow_state": outgoing.status if outgoing else "none", "following": bool(outgoing and outgoing.status == "accepted"), "follows_you": bool(follows_you), "followers": followers, "following_count": following}


@router.post("/people/{handle}/follow")
async def toggle_follow(handle: str, db: DbDep, user: Verified):
    target = (await db.execute(select(User).where(User.handle == handle.lower()))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "No such member.")
    if target.id == user.id:
        raise HTTPException(400, "You cannot follow yourself.")
    row = (await db.execute(select(Follow).where(Follow.follower_id == user.id, Follow.following_id == target.id))).scalar_one_or_none()
    if row:
        await db.delete(row)
        return {"state": "none", "following": False}
    db.add(Follow(follower_id=user.id, following_id=target.id, status="pending"))
    db.add(Notification(user_id=target.id, kind="follow", title=f"@{user.handle} requested to follow you", body="Approve or reject the request from your profile.", link="/me/followers"))
    return {"state": "pending", "following": False}


def _follow_card(person: User, row: Follow, viewer: User) -> dict:
    return {"follow_id": row.id, "status": row.status, "user": user_card(person, viewer_is_moderator=viewer.is_moderator)}


@router.get("/me/follows")
async def my_follows(db: DbDep, user: Verified):
    incoming = list((await db.execute(select(Follow, User).join(User, User.id == Follow.follower_id).where(Follow.following_id == user.id))).all())
    outgoing = list((await db.execute(select(Follow, User).join(User, User.id == Follow.following_id).where(Follow.follower_id == user.id))).all())
    return {
        "requests": [_follow_card(person, row, user) for row, person in incoming if row.status == "pending"],
        "followers": [_follow_card(person, row, user) for row, person in incoming if row.status == "accepted"],
        "following": [_follow_card(person, row, user) for row, person in outgoing if row.status == "accepted"],
        "outgoing_requests": [_follow_card(person, row, user) for row, person in outgoing if row.status == "pending"],
    }


@router.post("/me/follow-requests/{follow_id}/{action}")
async def decide_follow(follow_id: int, action: str, db: DbDep, user: Verified):
    if action not in {"approve", "reject"}:
        raise HTTPException(422, "Action must be approve or reject.")
    row = await db.get(Follow, follow_id)
    if row is None or row.following_id != user.id or row.status != "pending":
        raise HTTPException(404, "No such follow request.")
    if action == "reject":
        await db.delete(row)
        return {"state": "rejected"}
    row.status = "accepted"
    db.add(Notification(user_id=row.follower_id, kind="follow", title=f"@{user.handle} approved your follow request", body="You can now see them in your following list.", link=f"/people/{user.handle}"))
    return {"state": "accepted"}


@router.delete("/me/followers/{follow_id}")
async def remove_follower(follow_id: int, db: DbDep, user: Verified):
    row = await db.get(Follow, follow_id)
    if row is None or row.following_id != user.id or row.status != "accepted":
        raise HTTPException(404, "No such follower.")
    await db.delete(row)
    return Response(status_code=204)


@router.get("/admin/verification-queue")
async def verification_queue(db: DbDep, user: Verified):
    await _need(db, user, "approve_verification")
    records = list((await db.execute(select(VerificationRecord).where(VerificationRecord.status == "in_review").order_by(VerificationRecord.created_at.asc()).limit(50))).scalars())
    people = {p.id: p for p in (await db.execute(select(User).where(User.id.in_([r.user_id for r in records] or [0])))).scalars()}
    return {"records": [{"id": r.id, "status": r.status, "confidence": r.confidence, "handle": people[r.user_id].handle, "full_name": people[r.user_id].full_name, "extracted": dict(r.extracted or {}), "checks": dict(r.checks or {}), "has_card": bool(r.card_image_path), "has_selfie": bool(r.selfie_path), "has_card_face": bool(r.card_face_path)} for r in records]}


@router.get("/admin/verification-queue/{record_id}/artifact/{kind}")
async def verification_artifact(record_id: int, kind: str, request: Request, db: DbDep, user: Verified):
    await _need(db, user, "approve_verification")
    record = await db.get(VerificationRecord, record_id)
    if record is None or kind not in {"card", "selfie", "card_face"}:
        raise HTTPException(404, "No such verification image.")
    path = {"card": record.card_image_path, "selfie": record.selfie_path, "card_face": record.card_face_path}[kind]
    data = await media.read_verification(db, path, record_id=record.id, artifact=kind, actor_id=user.id, reason="in-app verification review", ip=request.client.host if request.client else None)
    if not data:
        raise HTTPException(404, "This verification image is no longer available.")
    ext = media.sniff(data) or "jpeg"
    mime = "image/jpeg" if ext == "jpg" else f"image/{ext}"
    return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store"})


@router.post("/admin/verification-queue/{record_id}")
async def decide(record_id: int, payload: DecisionIn, db: DbDep, user: Verified):
    await _need(db, user, "approve_verification")
    record = await db.get(VerificationRecord, record_id)
    if record is None:
        raise HTTPException(404, "No such verification.")
    if payload.outcome == "approve":
        await verification.approve(db, record, user.id, payload.note)
    else:
        await verification.reject(db, record, user.id, payload.note or "Verification rejected.")
    return {"status": record.status}


@router.get("/admin/members")
async def members(db: DbDep, user: Verified, q: str = ""):
    actions = await _actions(db, user)
    if not user.is_super_admin and not ({"manage_moderators", "manage_og"} & actions):
        raise HTTPException(403, "Member management was not delegated to you.")
    stmt = select(User).where(User.handle != "loyola_moderator")
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where((User.handle.ilike(needle)) | (User.full_name.ilike(needle)))
    rows = list((await db.execute(stmt.order_by(User.full_name).limit(100))).scalars())
    profiles = {p.user_id: p for p in (await db.execute(select(CommunityProfile).where(CommunityProfile.user_id.in_([r.id for r in rows] or [0])))).scalars()}
    return {"members": [{"id": row.id, "handle": row.handle, "full_name": row.full_name, "roles": list(row.roles or []), "is_og": bool(profiles.get(row.id) and profiles[row.id].is_og)} for row in rows]}

@router.post("/admin/members/{user_id}/moderator")
async def set_moderator(user_id: int, payload: RoleChange, db: DbDep, user: Verified):
    await _need(db, user, "manage_moderators")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "No such member.")
    roles = set(target.roles or [])
    roles.add("moderator") if payload.enabled else roles.discard("moderator")
    target.roles = sorted(roles)
    return {"roles": target.roles}


@router.post("/admin/members/{user_id}/og")
async def set_og(user_id: int, payload: RoleChange, db: DbDep, user: Verified):
    await _need(db, user, "manage_og")
    profile = await db.get(CommunityProfile, user_id)
    if profile is None:
        profile = CommunityProfile(user_id=user_id, is_og=payload.enabled)
        db.add(profile)
    else:
        profile.is_og = payload.enabled
    return {"is_og": payload.enabled}


@router.post("/admin/members/{user_id}/grant")
async def grant_admin(user_id: int, payload: GrantIn, db: DbDep, user: Verified):
    if not user.is_super_admin:
        raise HTTPException(403, "Only Super Admin can delegate administrator access.")
    if payload.expires_at and payload.expires_at <= utcnow():
        raise HTTPException(400, "Expiry must be in the future.")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "No such member.")
    target.roles = sorted(set(target.roles or []) | {"admin"})
    db.add(AdminGrant(user_id=target.id, granted_by_id=user.id, actions=list(active_actions(payload.actions, payload.expires_at)), expires_at=payload.expires_at))
    return {"actions": payload.actions, "expires_at": payload.expires_at}
