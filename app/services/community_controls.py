"""Small, dependency-free policy helpers for community identity and access."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass


MODERATOR_DISPLAY_NAME = "Loyola Moderator"
_MENTION = re.compile(r"(?<![\w@])@([a-zA-Z0-9_]{3,40})\b")


@dataclass(frozen=True)
class ContentIdentity:
    """The public identity attached to newly created content.

    Moderator mode intentionally carries no user identifier.  The calling
    account is used solely to authorize the request and is never copied into
    the content identity or persisted attribution fields.
    """

    user_id: int | None
    handle: str | None
    full_name: str


def content_identity(*, user_id: int, handle: str, full_name: str, as_moderator: bool) -> ContentIdentity:
    if as_moderator:
        return ContentIdentity(user_id=None, handle=None, full_name=MODERATOR_DISPLAY_NAME)
    return ContentIdentity(user_id=user_id, handle=handle, full_name=full_name)


def mentioned_handles(text: str) -> list[str]:
    """Return unique @handles in first-use order, excluding email addresses."""
    found: list[str] = []
    for match in _MENTION.finditer(text or ""):
        handle = match.group(1).lower()
        if handle not in found:
            found.append(handle)
    return found


def active_actions(actions: list[str], expires_at: dt.datetime | None, now: dt.datetime | None = None) -> set[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
        if expires_at <= now:
            return set()
    return {action for action in actions if action in {"approve_verification", "manage_moderators", "manage_og"}}


def can_administer(user, granted_actions: set[str], action: str, now: dt.datetime | None = None) -> bool:
    """Super admins have every action; delegated admins only have their grant."""
    roles = set(getattr(user, "roles", None) or [])
    return "super_admin" in roles or ("admin" in roles and action in granted_actions)