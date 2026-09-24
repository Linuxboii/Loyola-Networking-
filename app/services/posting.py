"""Shared write-path for posts and comments.

The HTML router and the JSON API must enforce *identical* rules ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â rate limits,
capability gates, the moderation screen, crisis escalation, reputation awards.
Keeping that logic in the routers meant two copies drifting apart, so it lives
here and both callers get the same behaviour for free. Routers keep only their
own concerns: parsing their input format and rendering their output format.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import NEWCOMER_POSTS_PER_DAY, can
from app.models import POST_TYPES, Comment, GroupMember, PollOption, Post, Report, User
from app.services import antiabuse, notify
from app.services.community_controls import mentioned_handles
from app.services import moderation as mod
from app.services import reputation as rep


class PostingError(Exception):
    """A rule stopped the write. ``status`` is the HTTP code to surface."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def normalise_tags(raw: str | Iterable[str] | None) -> list[str]:
    """Accept '#a, b c' or ['a','b'] and return at most six clean lowercase tags."""
    if raw is None:
        return []
    parts: list[str]
    if isinstance(raw, str):
        parts = [t for t in raw.replace(",", " ").split()]
    else:
        parts = [str(t) for t in raw]
    seen: list[str] = []
    for part in parts:
        tag = part.strip().lstrip("#").lower()
        if tag and tag not in seen and len(tag) <= 40:
            seen.append(tag)
    return seen[:6]


async def _moderator_actor(db: AsyncSession) -> User:
    actor = (await db.execute(select(User).where(User.handle == "loyola_moderator"))).scalar_one_or_none()
    if actor is None:
        raise PostingError(503, "Moderator identity is initializing. Please retry shortly.")
    return actor

async def _guard_rate_and_capability(db: AsyncSession, user: User, kind: str) -> None:
    if not can(user, "unlimited_posting"):
        allowed, _ = await antiabuse.hit_rate_limit(
            db, f"post:{user.id}", NEWCOMER_POSTS_PER_DAY, dt.timedelta(days=1)
        )
        if not allowed:
            raise PostingError(
                429,
                f"Newcomers can post {NEWCOMER_POSTS_PER_DAY} times a day. "
                "Reach Contributor for unlimited posting.",
            )
    if kind == "poll" and not can(user, "create_poll"):
        raise PostingError(403, "Polls unlock at Contributor standing.")
    if kind == "notice" and not (user.is_moderator or "club_officer" in (user.roles or [])):
        raise PostingError(403, "Only verified office-bearers can post notices.")


async def _screen_or_raise(db: AsyncSession, text: str, block_message: str) -> dict[str, Any]:
    lists = await mod.load_lists(db)
    verdict = mod.screen(text, lists)
    if verdict["verdict"] == "block":
        raise PostingError(400, block_message)
    return verdict


async def create_post(
    db: AsyncSession,
    user: User,
    *,
    kind: str = "text",
    title: str = "",
    body: str = "",
    tags: str | Iterable[str] | None = None,
    link_url: str = "",
    group_id: int | None = None,
    media_items: list[dict[str, Any]] | None = None,
    poll_options: Iterable[str] | None = None,
    as_moderator: bool = False,
) -> Post:
    if kind not in POST_TYPES:
        kind = "text"
    body = (body or "").strip()
    title = (title or "").strip()
    media_items = media_items or []

    if not body and not title and not media_items and kind != "image":
        raise PostingError(400, "Write something first.")

    await _guard_rate_and_capability(db, user, kind)
    verdict = await _screen_or_raise(
        db, f"{title}\n{body}", "That post breaks the community rules and was not published."
    )

    if group_id is not None:
        membership = (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id, GroupMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise PostingError(403, "Join the group before posting in it.")

    author = user
    if as_moderator:
        if not user.is_moderator:
            raise PostingError(403, "Moderator identity is not available for this account.")
        author = await _moderator_actor(db)

    post = Post(
        author_id=author.id,
        author=author,
        moderator_actor_id=user.id if as_moderator else None,
        kind=kind,
        title=title[:200] or None,
        body=body,
        tags=normalise_tags(tags),
        link_url=(link_url or "").strip()[:500] or None,
        group_id=group_id,
        media=media_items,
        is_official=(kind == "notice"),
    )
    db.add(post)
    await db.flush()

    if kind == "poll":
        options = [o.strip()[:120] for o in (poll_options or []) if o and o.strip()][:8]
        if len(options) < 2:
            raise PostingError(400, "A poll needs at least two options.")
        for index, label in enumerate(options):
            db.add(PollOption(post_id=post.id, label=label, position=index))
        await rep.award(db, user.id, "poll_created", source_type="post", source_id=post.id)

    await _record_verdict(db, user, verdict, "post", post.id, body)
    await _notify_mentions(db, f"{title}\n{body}", author, f"/p/{post.id}")
    await db.flush()
    return post


async def _notify_mentions(db: AsyncSession, text: str, actor: User, link: str) -> None:
    handles = mentioned_handles(text)
    if not handles:
        return
    recipients = list((await db.execute(select(User).where(User.handle.in_(handles)))).scalars().all())
    for recipient in recipients:
        await notify.push(
            db,
            recipient.id,
            kind="mention",
            title=f"{actor.full_name} mentioned you",
            body=text[:200],
            link=link,
            skip_if_self=actor.id,
        )

async def _record_verdict(
    db: AsyncSession, user: User, verdict: dict[str, Any], target_type: str, target_id: int, body: str
) -> None:
    if verdict["verdict"] == "crisis":
        await mod.raise_crisis(db, user.id, target_type, target_id, body)
    elif verdict["verdict"] in {"flag", "review"}:
        db.add(
            Report(
                reporter_id=user.id,
                target_type=target_type,
                target_id=target_id,
                target_author_id=user.id,
                category="other" if verdict["verdict"] == "flag" else "misinformation",
                detail=(
                    f"Automated filter: {verdict['verdict']} "
                    f"({', '.join(verdict.get('profanity') or []) or 'faculty opinion'})"
                ),
                source="auto",
                status="open",
            )
        )


async def add_comment(
    db: AsyncSession, user: User, post: Post, *, body: str, parent_id: int | None = None, as_moderator: bool = False
) -> Comment:
    body = (body or "").strip()
    if not body:
        raise PostingError(400, "Write something first.")
    if post.status != "active":
        raise PostingError(404, "That post is no longer available.")

    allowed, _ = await antiabuse.hit_rate_limit(db, f"comment:{user.id}", 40, dt.timedelta(hours=1))
    if not allowed:
        raise PostingError(429, "You are commenting very fast. Take a breath.")

    verdict = await _screen_or_raise(db, body, "That comment breaks the community rules.")

    parent = await db.get(Comment, parent_id) if parent_id else None
    if parent is not None and parent.post_id != post.id:
        parent = None

    author = user
    if as_moderator:
        if not user.is_moderator:
            raise PostingError(403, "Moderator identity is not available for this account.")
        author = await _moderator_actor(db)
    comment = Comment(
        post_id=post.id,
        parent_id=parent.id if parent else None,
        author_id=author.id,
        author=author,
        moderator_actor_id=user.id if as_moderator else None,
        body=body,
    )
    db.add(comment)
    post.comment_count = (post.comment_count or 0) + 1
    await db.flush()

    await _record_verdict(db, user, verdict, "comment", comment.id, body)

    actor_label = "@mod" if author.handle == "loyola_moderator" else f"@{author.handle}"
    shared_target = post.author.handle == "loyola_moderator" or (
        parent is not None and parent.author.handle == "loyola_moderator"
    )
    link = f"/p/{post.id}#c{comment.id}"
    if shared_target:
        await notify.push_moderators(
            db,
            kind="moderator_reply",
            title=f"{actor_label} received a reply",
            body=body[:200],
            link=link,
            skip_user_id=user.id,
        )
    else:
        await notify.push(
            db,
            post.author_id,
            kind="comment",
            title=f"{actor_label} commented on your post",
            body=body[:200],
            link=link,
            skip_if_self=user.id,
        )
        if parent and parent.author_id not in {user.id, post.author_id}:
            await notify.push(
                db,
                parent.author_id,
                kind="reply",
                title=f"{actor_label} replied to you",
                body=body[:200],
                link=link,
            )
    await db.flush()
    return comment
