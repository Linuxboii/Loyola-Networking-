"""Request bodies and response shapes for the JSON API.

Responses are built by the ``*_out`` helpers rather than by ORM-mode models: the
mobile client wants a flattened, denormalised view (author inline, my_vote
inline, signed media URLs already resolved) and hand-written serializers make
that explicit instead of hiding it behind lazy-loading surprises.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models import PILLAR_LABELS, User
from app.security import sign_media


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class SignupIn(BaseModel):
    handle: str = Field(min_length=3, max_length=24)
    full_name: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=10, max_length=200)
    consent: bool = True


class LoginIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class PasswordChangeIn(BaseModel):
    current: str
    password: str = Field(min_length=10, max_length=200)


class PostIn(BaseModel):
    kind: str = Field(default="text", pattern="^(text|link|poll|media|question)$")
    title: Optional[str] = Field(default=None, max_length=200)
    body: str = Field(default="", max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=6)
    link_url: Optional[str] = Field(default=None, max_length=500)
    group_id: Optional[int] = None
    media: list[str] = Field(default_factory=list, max_length=4)
    poll_options: list[str] = Field(default_factory=list, max_length=6)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    parent_id: Optional[int] = None


class VoteIn(BaseModel):
    target_type: str = Field(pattern="^(post|comment|question|answer)$")
    target_id: int
    value: int = Field(ge=-1, le=1)


class QuestionIn(BaseModel):
    title: str = Field(min_length=10, max_length=250)
    body: str = Field(min_length=10, max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=6)
    subject: Optional[str] = Field(default=None, max_length=80)
    semester: Optional[str] = Field(default=None, max_length=20)
    is_anonymous: bool = False


class AnswerIn(BaseModel):
    body: str = Field(min_length=10, max_length=8000)


class ProfileIn(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=1000)
    interests: Optional[list[str]] = Field(default=None, max_length=12)
    links: Optional[dict[str, str]] = None
    hide_from_search: Optional[bool] = None
    hide_activity: Optional[bool] = None
    endorse_policy: Optional[str] = Field(default=None, pattern="^(anyone|batch|nobody)$")


class GroupIn(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    description: str = Field(default="", max_length=2000)
    kind: str = Field(default="interest", pattern="^(interest|club)$")


class EventIn(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=4000)
    venue: str = Field(default="", max_length=160)
    starts_at: dt.datetime
    ends_at: Optional[dt.datetime] = None
    capacity: Optional[int] = Field(default=None, ge=1, le=10000)
    tags: list[str] = Field(default_factory=list, max_length=6)
    group_id: Optional[int] = None


class RsvpIn(BaseModel):
    state: str = Field(default="going", pattern="^(going|maybe|cancelled)$")


class ReportIn(BaseModel):
    target_type: str = Field(pattern="^(post|comment|question|answer)$")
    target_id: int
    reason: str = Field(min_length=3, max_length=60)
    detail: str = Field(default="", max_length=1000)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def media_url(rel_path: str | None) -> str | None:
    """A short-lived signed URL, so media stays behind the verification wall."""
    if not rel_path:
        return None
    return f"/media/{sign_media(rel_path)}"


def iso(value: dt.datetime | dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def user_card(user: User | None, *, anonymous: bool = False) -> dict[str, Any]:
    """The compact author block embedded in every piece of content."""
    if anonymous or user is None:
        return {
            "id": None,
            "handle": None,
            "full_name": "Anonymous",
            "photo_url": None,
            "rep_tier": None,
            "rep_total": 0,
            "batch_year": None,
            "department": None,
            "is_moderator": False,
            "anonymous": True,
        }
    return {
        "id": user.id,
        "handle": user.handle,
        "full_name": user.full_name,
        "photo_url": media_url(user.photo_path),
        "rep_tier": user.rep_tier,
        "rep_total": round(user.rep_total or 0.0, 1),
        "batch_year": user.batch_year,
        "department": user.department,
        "course": user.course,
        "is_moderator": user.is_moderator,
        "anonymous": False,
    }


def me_out(user: User, *, unread: int = 0, capabilities: dict[str, bool] | None = None) -> dict[str, Any]:
    return {
        **user_card(user),
        "roll_number": user.roll_number,
        "bio": user.bio or "",
        "interests": list(user.interests or []),
        "links": dict(user.links or {}),
        "tier": user.tier,
        "tier_label": ("Unverified", "Provisional", "Verified", "Role account")[max(0, min(3, user.tier))],
        "tier1_expires_at": iso(user.tier1_expires_at),
        "status": user.status,
        "roles": list(user.roles or []),
        "is_admin": user.is_admin,
        "hide_from_search": user.hide_from_search,
        "hide_activity": user.hide_activity,
        "endorse_policy": user.endorse_policy,
        "recovery_email": user.recovery_email,
        "rep_pillars": {
            key: round(getattr(user, f"rep_{key}", 0.0) or 0.0, 1) for key in PILLAR_LABELS
        },
        "unread_notifications": unread,
        "capabilities": capabilities or {},
        "created_at": iso(user.created_at),
    }


def post_out(post, *, my_vote: int = 0, poll: list[dict[str, Any]] | None = None,
             my_poll_option: int | None = None) -> dict[str, Any]:
    return {
        "id": post.id,
        "kind": post.kind,
        "title": post.title,
        "body": post.body or "",
        "tags": list(post.tags or []),
        "link_url": post.link_url,
        "media": [media_url(m) if isinstance(m, str) else m for m in (post.media or [])],
        "group_id": post.group_id,
        "is_official": post.is_official,
        "score": post.score,
        "comment_count": post.comment_count,
        "created_at": iso(post.created_at),
        "edited_at": iso(post.edited_at),
        "author": user_card(post.author),
        "my_vote": my_vote,
        "poll": poll,
        "my_poll_option": my_poll_option,
    }


def comment_out(comment, *, my_vote: int = 0) -> dict[str, Any]:
    return {
        "id": comment.id,
        "post_id": comment.post_id,
        "parent_id": comment.parent_id,
        "body": comment.body,
        "score": comment.score,
        "created_at": iso(comment.created_at),
        "author": user_card(comment.author),
        "my_vote": my_vote,
    }


def question_out(question, *, my_vote: int = 0) -> dict[str, Any]:
    return {
        "id": question.id,
        "title": question.title,
        "body": question.body,
        "tags": list(question.tags or []),
        "subject": question.subject,
        "semester": question.semester,
        "is_anonymous": question.is_anonymous,
        "accepted_answer_id": question.accepted_answer_id,
        "score": question.score,
        "answer_count": question.answer_count,
        "view_count": question.view_count,
        "created_at": iso(question.created_at),
        "author": user_card(question.author, anonymous=question.is_anonymous),
        "my_vote": my_vote,
    }


def answer_out(answer, *, my_vote: int = 0) -> dict[str, Any]:
    return {
        "id": answer.id,
        "question_id": answer.question_id,
        "body": answer.body,
        "is_accepted": answer.is_accepted,
        "score": answer.score,
        "created_at": iso(answer.created_at),
        "author": user_card(answer.author),
        "my_vote": my_vote,
    }


def group_out(group, *, membership: str | None = None) -> dict[str, Any]:
    return {
        "id": group.id,
        "slug": group.slug,
        "name": group.name,
        "description": group.description or "",
        "kind": group.kind,
        "is_official": group.is_official,
        "banner_url": media_url(group.banner_path),
        "member_count": group.member_count,
        "club_rep": round(group.club_rep or 0.0, 1),
        "my_role": membership,
    }


def event_out(event, *, my_rsvp: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description or "",
        "venue": event.venue or "",
        "starts_at": iso(event.starts_at),
        "ends_at": iso(event.ends_at),
        "capacity": event.capacity,
        "tags": list(event.tags or []),
        "is_official": event.is_official,
        "rsvp_count": event.rsvp_count,
        "checkin_count": event.checkin_count,
        "group_id": event.group_id,
        "host": user_card(event.host),
        "my_rsvp": my_rsvp,
    }


def notification_out(note) -> dict[str, Any]:
    return {
        "id": note.id,
        "kind": note.kind,
        "title": note.title,
        "body": note.body or "",
        "link": note.link or "/",
        "read": note.read_at is not None,
        "created_at": iso(note.created_at),
    }


def profile_out(user: User, *, skills: list[dict[str, Any]] | None = None,
                is_me: bool = False, counts: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        **user_card(user),
        "bio": user.bio or "",
        "interests": list(user.interests or []),
        "links": dict(user.links or {}),
        "tier": user.tier,
        "status": user.status,
        "display_year": user.display_year,
        "rep_pillars": {
            key: round(getattr(user, f"rep_{key}", 0.0) or 0.0, 1) for key in PILLAR_LABELS
        },
        "skills": skills or [],
        "counts": counts or {},
        "is_me": is_me,
        "joined_at": iso(user.created_at),
    }
