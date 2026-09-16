"""Feed, posts, comments, votes and media upload for the mobile client."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.schemas import (
    CommentIn,
    PostIn,
    ReportIn,
    VoteIn,
    comment_out,
    media_url,
    post_out,
)
from app.config import settings
from app.deps import DbDep, Reader, Verified, client_ip
from app.models import Comment, PollOption, PollVote, Post, Report
from app.security import device_fingerprint
from app.services import antiabuse, feed as feed_service, media, posting
from app.services import moderation as mod
from app.services import reputation as rep
from app.services.voting import VoteError, cast_vote

router = APIRouter(tags=["api:feed"])


def _fp(request: Request) -> str:
    return device_fingerprint(
        request.headers.get("user-agent"),
        request.headers.get("accept-language"),
        client_ip(request),
    )


async def _poll_block(db, post: Post, user_id: int) -> tuple[list[dict] | None, int | None]:
    if post.kind != "poll":
        return None, None
    options = list(
        (
            await db.execute(
                select(PollOption).where(PollOption.post_id == post.id).order_by(PollOption.position)
            )
        ).scalars().all()
    )
    total = sum(o.votes for o in options) or 0
    mine = (
        await db.execute(
            select(PollVote.option_id).where(PollVote.post_id == post.id, PollVote.user_id == user_id)
        )
    ).scalar_one_or_none()
    block = [
        {
            "id": o.id,
            "label": o.label,
            "votes": o.votes,
            "share": round(100 * o.votes / total) if total else 0,
        }
        for o in options
    ]
    return block, mine


@router.get("/feed")
async def list_feed(
    db: DbDep,
    user: Reader,
    mode: str = Query("latest", pattern="^(latest|foryou)$"),
    tag: str | None = None,
    kind: str | None = None,
    group_id: int | None = None,
    author_id: int | None = None,
    page: int = Query(1, ge=1, le=500),
):
    posts, has_more = await feed_service.fetch(
        db, user, mode=mode, tag=tag, kind=kind, group_id=group_id, author_id=author_id, page=page
    )
    votes = await feed_service.my_votes(db, user.id, "post", [p.id for p in posts])
    return {
        "page": page,
        "has_more": has_more,
        "mode": mode,
        "posts": [post_out(p, my_vote=votes.get(p.id, 0)) for p in posts],
    }


@router.post("/posts", status_code=201)
async def create_post(payload: PostIn, db: DbDep, user: Verified):
    try:
        post = await posting.create_post(
            db,
            user,
            kind=payload.kind,
            title=payload.title or "",
            body=payload.body,
            tags=payload.tags,
            link_url=payload.link_url or "",
            group_id=payload.group_id,
            media_items=[{"path": m, "type": "image"} for m in payload.media],
            poll_options=payload.poll_options,
        )
    except posting.PostingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    poll, mine = await _poll_block(db, post, user.id)
    return post_out(post, poll=poll, my_poll_option=mine)


@router.get("/posts/{post_id}")
async def post_detail(post_id: int, db: DbDep, user: Reader):
    post = (
        await db.execute(select(Post).options(joinedload(Post.author)).where(Post.id == post_id))
    ).unique().scalar_one_or_none()
    if post is None or (post.status != "active" and not user.is_moderator):
        raise HTTPException(404, "That post is no longer available.")

    comments = list(
        (
            await db.execute(
                select(Comment)
                .options(joinedload(Comment.author))
                .where(Comment.post_id == post_id, Comment.status == "active")
                .order_by(Comment.created_at.asc())
            )
        ).unique().scalars().all()
    )
    post_votes = await feed_service.my_votes(db, user.id, "post", [post.id])
    comment_votes = await feed_service.my_votes(db, user.id, "comment", [c.id for c in comments])
    poll, mine = await _poll_block(db, post, user.id)

    return {
        "post": post_out(post, my_vote=post_votes.get(post.id, 0), poll=poll, my_poll_option=mine),
        "comments": [comment_out(c, my_vote=comment_votes.get(c.id, 0)) for c in comments],
    }


@router.post("/posts/{post_id}/comments", status_code=201)
async def create_comment(post_id: int, payload: CommentIn, db: DbDep, user: Verified):
    post = await db.get(Post, post_id)
    if post is None or post.status != "active":
        raise HTTPException(404, "That post is no longer available.")
    try:
        comment = await posting.add_comment(
            db, user, post, body=payload.body, parent_id=payload.parent_id
        )
    except posting.PostingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    await db.refresh(comment, ["author"])
    return comment_out(comment)


@router.post("/vote")
async def vote(payload: VoteIn, request: Request, db: DbDep, user: Verified):
    if payload.value not in (1, -1):
        raise HTTPException(422, "A vote is +1 or -1.")
    allowed, _ = await antiabuse.hit_rate_limit(db, f"vote:{user.id}", 200, dt.timedelta(hours=1))
    if not allowed:
        raise HTTPException(429, "Too many votes in a short window.")
    try:
        return await cast_vote(
            db, user, payload.target_type, payload.target_id, payload.value, device_fp=_fp(request)
        )
    except VoteError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/posts/{post_id}/poll/{option_id}")
async def vote_poll(post_id: int, option_id: int, db: DbDep, user: Verified):
    option = await db.get(PollOption, option_id)
    if option is None or option.post_id != post_id:
        raise HTTPException(404, "No such poll option.")

    existing = (
        await db.execute(
            select(PollVote).where(PollVote.post_id == post_id, PollVote.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.option_id == option_id:
            raise HTTPException(409, "You already picked that option.")
        previous = await db.get(PollOption, existing.option_id)
        if previous is not None:
            previous.votes = max(0, previous.votes - 1)
        existing.option_id = option_id
    else:
        db.add(PollVote(post_id=post_id, option_id=option_id, user_id=user.id))
    option.votes += 1
    await db.flush()

    post = await db.get(Post, post_id)
    poll, mine = await _poll_block(db, post, user.id)
    return {"poll": poll, "my_poll_option": mine}


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(post_id: int, db: DbDep, user: Verified):
    post = await db.get(Post, post_id)
    if post is None:
        raise HTTPException(404, "No such post.")
    if post.author_id != user.id and not user.is_moderator:
        raise HTTPException(403, "That is not your post.")

    post.status = "removed"
    post.removed_reason = "Deleted by author" if post.author_id == user.id else "Removed by moderator"
    await rep.void_events(db, source_type="post", source_id=post.id, reason_note="post deleted")
    if post.author_id != user.id:
        await mod.log_action(
            db,
            action="remove_content",
            target_type="post",
            target_id=post.id,
            actor_id=user.id,
            subject_user_id=post.author_id,
            reason="Removed by moderator",
        )
    return Response(status_code=204)


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(comment_id: int, db: DbDep, user: Verified):
    comment = await db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(404, "No such comment.")
    if comment.author_id != user.id and not user.is_moderator:
        raise HTTPException(403, "That is not your comment.")
    comment.status = "removed"
    post = await db.get(Post, comment.post_id)
    if post is not None:
        post.comment_count = max(0, (post.comment_count or 1) - 1)
    await rep.void_events(db, source_type="comment", source_id=comment.id, reason_note="comment deleted")
    return Response(status_code=204)


@router.post("/media", status_code=201)
async def upload_media(db: DbDep, user: Verified, file: UploadFile = File(...)):
    """Upload an image and get back the stored path plus a signed preview URL.

    The path is what you pass in ``PostIn.media``; the URL is only for showing a
    preview before the post exists.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "The upload was empty.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Images must be under {settings.max_upload_mb} MB.")
    if not media.is_image(data):
        raise HTTPException(400, "That attachment is not an image.")

    allowed, _ = await antiabuse.hit_rate_limit(db, f"upload:{user.id}", 30, dt.timedelta(hours=1))
    if not allowed:
        raise HTTPException(429, "Too many uploads in a short window.")

    path = media.save_public(data, subdir="posts")
    return {"path": path, "url": media_url(path)}


@router.post("/reports", status_code=201)
async def report_content(payload: ReportIn, db: DbDep, user: Verified):
    existing = (
        await db.execute(
            select(Report).where(
                Report.reporter_id == user.id,
                Report.target_type == payload.target_type,
                Report.target_id == payload.target_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, "You already reported that.")

    target = await mod.load_target(db, payload.target_type, payload.target_id)
    if target is None:
        raise HTTPException(404, "There is nothing there to report.")

    db.add(
        Report(
            reporter_id=user.id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            target_author_id=mod.target_author_id(target),
            category=payload.reason[:32],
            detail=payload.detail[:1000],
            source="user",
            status="open",
        )
    )
    await db.flush()
    return {"status": "received"}
