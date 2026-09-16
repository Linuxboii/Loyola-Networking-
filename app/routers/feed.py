"""The feed, posting, comments and voting."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, client_ip, verify_csrf
from app.models import POST_TYPES, Comment, Group, PollOption, PollVote, Post
from app.security import device_fingerprint
from app.services import antiabuse, feed as feed_service, media, posting
from app.services import moderation as mod
from app.services import reputation as rep
from app.services.voting import VoteError, cast_vote
from app.templating import templates

router = APIRouter(tags=["feed"])


def _fp(request: Request) -> str:
    return device_fingerprint(
        request.headers.get("user-agent"),
        request.headers.get("accept-language"),
        client_ip(request),
    )


@router.get("/")
async def home(
    request: Request,
    db: DbDep,
    user: Reader,
    mode: str = "latest",
    tag: str | None = None,
    kind: str | None = None,
    page: int = 1,
):
    posts, has_more = await feed_service.fetch(
        db, user, mode=mode, tag=tag, kind=kind, page=page
    )
    votes = await feed_service.my_votes(db, user.id, "post", [p.id for p in posts])
    context = {
        "title": "Campus feed",
        "posts": posts,
        "has_more": has_more,
        "page": page,
        "mode": mode,
        "tag": tag,
        "kind": kind,
        "my_votes": votes,
        "trending": await feed_service.trending_tags(db),
        "suggested_groups": await feed_service.suggested_groups(db, user),
        "stats": await feed_service.campus_stats(db),
        "post_types": POST_TYPES,
    }
    if request.headers.get("HX-Request") and page > 1:
        return templates.TemplateResponse(request, "feed/_posts.html", context)
    return templates.TemplateResponse(request, "feed/index.html", context)


@router.post("/posts")
async def create_post(
    request: Request,
    db: DbDep,
    user: Verified,
    kind: str = Form("text"),
    title: str = Form(""),
    body: str = Form(""),
    tags: str = Form(""),
    link_url: str = Form(""),
    group_id: str = Form(""),
    poll_options: str = Form(""),
    image: UploadFile | None = File(None),
):
    await verify_csrf(request)

    gid: int | None = None
    if group_id:
        try:
            gid = int(group_id)
        except ValueError:
            gid = None

    media_items: list[dict] = []
    if image is not None and image.filename:
        data = await image.read()
        if data:
            if not media.is_image(data):
                raise HTTPException(status_code=400, detail="That attachment is not an image.")
            media_items.append({"path": media.save_public(data, subdir="posts"), "type": "image"})

    try:
        post = await posting.create_post(
            db,
            user,
            kind=kind,
            title=title,
            body=body,
            tags=tags,
            link_url=link_url,
            group_id=gid,
            media_items=media_items,
            poll_options=(poll_options or "").splitlines(),
        )
    except posting.PostingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    return RedirectResponse(f"/p/{post.id}", status_code=303)


@router.get("/p/{post_id}")
async def post_detail(request: Request, db: DbDep, user: Reader, post_id: int):
    post = (
        await db.execute(
            select(Post).options(joinedload(Post.author)).where(Post.id == post_id)
        )
    ).unique().scalar_one_or_none()
    if post is None or (post.status != "active" and not user.is_moderator):
        raise HTTPException(status_code=404)

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

    poll_options = []
    my_poll_vote = None
    if post.kind == "poll":
        poll_options = list(
            (
                await db.execute(
                    select(PollOption).where(PollOption.post_id == post.id).order_by(PollOption.position)
                )
            ).scalars().all()
        )
        my_poll_vote = (
            await db.execute(
                select(PollVote.option_id).where(PollVote.post_id == post.id, PollVote.user_id == user.id)
            )
        ).scalar_one_or_none()

    group = await db.get(Group, post.group_id) if post.group_id else None

    return templates.TemplateResponse(
        request,
        "feed/detail.html",
        {
            "title": post.title or f"Post by {post.author.full_name}",
            "post": post,
            "comments": comments,
            "group": group,
            "poll_options": poll_options,
            "poll_total": sum(o.votes for o in poll_options),
            "my_poll_vote": my_poll_vote,
            "my_votes": await feed_service.my_votes(db, user.id, "post", [post.id]),
            "my_comment_votes": await feed_service.my_votes(
                db, user.id, "comment", [c.id for c in comments]
            ),
        },
    )


@router.post("/p/{post_id}/comments")
async def add_comment(
    request: Request,
    db: DbDep,
    user: Verified,
    post_id: int,
    body: str = Form(...),
    parent_id: str = Form(""),
):
    await verify_csrf(request)
    post = await db.get(Post, post_id)
    if post is None or post.status != "active":
        raise HTTPException(status_code=404)

    parent: int | None = None
    if parent_id:
        try:
            parent = int(parent_id)
        except ValueError:
            parent = None

    try:
        comment = await posting.add_comment(db, user, post, body=body, parent_id=parent)
    except posting.PostingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    return RedirectResponse(f"/p/{post_id}#c{comment.id}", status_code=303)


@router.post("/vote/{target_type}/{target_id}")
async def vote(
    request: Request,
    db: DbDep,
    user: Verified,
    target_type: str,
    target_id: int,
    value: int = Form(...),
    style: str = Form("stack"),
):
    await verify_csrf(request)
    allowed, _ = await antiabuse.hit_rate_limit(db, f"vote:{user.id}", 200, dt.timedelta(hours=1))
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many votes in a short window.")

    try:
        result = await cast_vote(db, user, target_type, target_id, value, device_fp=_fp(request))
    except VoteError as exc:
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<span class="vote-error">{exc}</span>', status_code=400)
        raise HTTPException(status_code=400, detail=str(exc))

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request,
            "components/_vote.html",
            {
                "target_type": target_type,
                "target_id": target_id,
                "score": result["score"],
                "my_vote": result["my_vote"],
                # Never trust the posted value into a template branch.
                "vote_style": "inline" if style == "inline" else "stack",
            },
        )
    return JSONResponse(result)


@router.post("/p/{post_id}/poll")
async def vote_poll(
    request: Request, db: DbDep, user: Verified, post_id: int, option_id: int = Form(...)
):
    await verify_csrf(request)
    option = await db.get(PollOption, option_id)
    if option is None or option.post_id != post_id:
        raise HTTPException(status_code=404)

    existing = (
        await db.execute(
            select(PollVote).where(PollVote.post_id == post_id, PollVote.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing:
        previous = await db.get(PollOption, existing.option_id)
        if previous:
            previous.votes = max(0, previous.votes - 1)
        existing.option_id = option_id
    else:
        db.add(PollVote(post_id=post_id, option_id=option_id, user_id=user.id))
    option.votes += 1
    await db.flush()
    return RedirectResponse(f"/p/{post_id}", status_code=303)


@router.post("/p/{post_id}/delete")
async def delete_post(request: Request, db: DbDep, user: Verified, post_id: int):
    await verify_csrf(request)
    post = await db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404)
    if post.author_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your post.")

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
    return RedirectResponse("/", status_code=303)


@router.post("/comments/{comment_id}/delete")
async def delete_comment(request: Request, db: DbDep, user: Verified, comment_id: int):
    await verify_csrf(request)
    comment = await db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404)
    if comment.author_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your comment.")
    comment.status = "removed"
    post = await db.get(Post, comment.post_id)
    if post:
        post.comment_count = max(0, (post.comment_count or 1) - 1)
    await rep.void_events(db, source_type="comment", source_id=comment.id, reason_note="comment deleted")
    return RedirectResponse(f"/p/{comment.post_id}", status_code=303)
