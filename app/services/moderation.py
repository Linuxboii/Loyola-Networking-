"""Automated filters, the report queue, enforcement and appeals.

Tier 1 of the PRD's tiered response is here: cheap lexical filters that catch the
obvious cases so human moderators only see the hard ones. The word lists are
deliberately short in source and **extensible at runtime** via
``app_settings['moderation_lists']`` — campus slang, and the Telugu/Hindi
transliterations that matter most here, are best maintained by the moderators
who actually read the reports, not frozen into a deployment.

Crisis handling is the one path that never punishes: a post that reads as
distress is routed to the counsellor contact and the author is shown support
resources. It is never auto-removed and never auto-suspended.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Answer,
    Appeal,
    Comment,
    CrisisFlag,
    ModerationAction,
    ModQueueVote,
    Notification,
    Post,
    Question,
    Report,
    User,
    utcnow,
)
from app.services import reputation as rep

# --- lexical filters --------------------------------------------------------

# Base list: common English profanity plus the Hinglish transliterations that
# show up constantly in Indian campus chat. Moderators extend this at runtime.
DEFAULT_PROFANITY = [
    "fuck", "shit", "bitch", "bastard", "asshole", "dick", "cunt", "slut", "whore",
    "chutiya", "chutiye", "bhenchod", "behenchod", "madarchod", "madarchod",
    "gandu", "gaandu", "lauda", "lodu", "harami", "kamina", "kutta", "kutti",
    "randi", "bsdk", "mc", "bc",
]

# Terms whose presence is treated as a hard block rather than a soft flag.
DEFAULT_HARD_BLOCK = [
    "kill yourself", "kys", "go die",
]

DEFAULT_CRISIS = [
    "kill myself", "want to die", "end my life", "suicide", "suicidal",
    "self harm", "self-harm", "cutting myself", "no reason to live",
    "cant go on", "can't go on", "better off dead", "end it all",
]

# Free-text opinions about named faculty are prohibited outright (PRD 7.9.6):
# the course-feedback module is course-only, and this is the feature most likely
# to get the app thrown off campus if it slips.
FACULTY_TITLES = ["prof", "professor", "dr", "sir", "madam", "ma'am", "lecturer", "hod"]
FACULTY_OPINION = [
    "worst", "useless", "terrible", "hopeless", "pathetic", "stupid", "idiot",
    "hates", "biased", "corrupt", "drunk", "creepy", "pervert", "harasses",
]

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def _normalise(text: str) -> str:
    t = (text or "").lower().translate(LEET)
    # Collapse character padding used to dodge filters: f-u-c-k, f u c k.
    t = re.sub(r"(?<=\b\w)[\s\-_.*]+(?=\w\b)", "", t)
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)
    return re.sub(r"[^a-z\s']+", " ", t)


def _contains_term(norm: str, term: str) -> bool:
    if " " in term:
        return term in norm
    return re.search(rf"\b{re.escape(term)}\b", norm) is not None


def screen(text: str, lists: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Classify a piece of text. Returns the verdict plus what triggered it."""
    lists = lists or {}
    profanity = lists.get("profanity") or DEFAULT_PROFANITY
    hard = lists.get("hard_block") or DEFAULT_HARD_BLOCK
    crisis = lists.get("crisis") or DEFAULT_CRISIS

    norm = _normalise(text)
    hits_profanity = [t for t in profanity if _contains_term(norm, t)]
    hits_hard = [t for t in hard if _contains_term(norm, t)]
    hits_crisis = [t for t in crisis if _contains_term(norm, t)]

    faculty_named = any(_contains_term(norm, t) for t in FACULTY_TITLES)
    faculty_opinion = faculty_named and any(_contains_term(norm, t) for t in FACULTY_OPINION)

    verdict = "allow"
    if hits_crisis:
        # Crisis wins over everything: a distressed post that also swears must
        # be routed for support, never removed for language.
        verdict = "crisis"
    elif hits_hard:
        verdict = "block"
    elif faculty_opinion:
        verdict = "review"
    elif hits_profanity:
        verdict = "flag"

    return {
        "verdict": verdict,
        "profanity": hits_profanity,
        "hard_block": hits_hard,
        "crisis": hits_crisis,
        "faculty_opinion": faculty_opinion,
    }


async def load_lists(db: AsyncSession) -> dict[str, list[str]]:
    from app.services.settings_store import get_setting

    stored = await get_setting(db, "moderation_lists", {})
    out: dict[str, list[str]] = {}
    for key, base in (
        ("profanity", DEFAULT_PROFANITY),
        ("hard_block", DEFAULT_HARD_BLOCK),
        ("crisis", DEFAULT_CRISIS),
    ):
        extra = stored.get(key) or []
        out[key] = list(dict.fromkeys([*base, *[str(x).lower() for x in extra]]))
    return out


# --- target resolution ------------------------------------------------------

TARGETS = {"post": Post, "comment": Comment, "question": Question, "answer": Answer}


async def load_target(db: AsyncSession, target_type: str, target_id: int):
    model = TARGETS.get(target_type)
    if model is None:
        return None
    return await db.get(model, target_id)


def target_author_id(obj) -> int | None:
    return getattr(obj, "author_id", None)


def target_excerpt(obj) -> str:
    for attr in ("title", "body"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)[:280]
    return ""


def target_link(target_type: str, obj) -> str:
    if target_type == "post":
        return f"/p/{obj.id}"
    if target_type == "comment":
        return f"/p/{obj.post_id}#c{obj.id}"
    if target_type == "question":
        return f"/qa/{obj.id}"
    if target_type == "answer":
        return f"/qa/{obj.question_id}#a{obj.id}"
    return "/"


# --- audit ------------------------------------------------------------------


async def log_action(
    db: AsyncSession,
    *,
    action: str,
    target_type: str,
    target_id: int,
    actor_id: int | None,
    actor_kind: str = "moderator",
    subject_user_id: int | None = None,
    reason: str = "",
    report_id: int | None = None,
    appealable: bool = True,
    meta: dict[str, Any] | None = None,
) -> ModerationAction:
    entry = ModerationAction(
        actor_id=actor_id,
        actor_kind=actor_kind,
        action=action,
        target_type=target_type,
        target_id=target_id,
        subject_user_id=subject_user_id,
        reason=reason,
        report_id=report_id,
        appealable=appealable,
        meta=meta or {},
    )
    db.add(entry)
    await db.flush()
    return entry


# --- crisis -----------------------------------------------------------------


async def raise_crisis(
    db: AsyncSession, user_id: int, target_type: str, target_id: int, excerpt: str
) -> CrisisFlag:
    flag = CrisisFlag(
        user_id=user_id, target_type=target_type, target_id=target_id, excerpt=excerpt[:500]
    )
    db.add(flag)
    db.add(
        Notification(
            user_id=user_id,
            kind="support",
            title="Support is available",
            body=(
                f"If you are going through something difficult, you do not have to handle it alone. "
                f"{settings.counsellor_contact}"
            ),
            link="/support",
        )
    )
    await db.flush()
    return flag


# --- enforcement ------------------------------------------------------------


async def remove_content(
    db: AsyncSession,
    target_type: str,
    target_id: int,
    *,
    actor_id: int | None,
    reason: str,
    actor_kind: str = "moderator",
    report_id: int | None = None,
) -> ModerationAction | None:
    obj = await load_target(db, target_type, target_id)
    if obj is None or getattr(obj, "status", "") == "removed":
        return None

    obj.status = "removed"
    obj.removed_reason = reason[:255]
    author_id = target_author_id(obj)

    # Points earned from removed content are withdrawn, and the author takes a
    # conduct hit (PRD 6.2/6.3).
    await rep.void_events(db, source_type=target_type, source_id=target_id, reason_note="removed")
    if author_id:
        await rep.award(
            db,
            author_id,
            "content_removed",
            source_type=target_type,
            source_id=target_id,
            actor_id=actor_id,
            detail=reason[:200],
            settle_immediately=True,
        )

    action = await log_action(
        db,
        action="remove_content",
        target_type=target_type,
        target_id=target_id,
        actor_id=actor_id,
        actor_kind=actor_kind,
        subject_user_id=author_id,
        reason=reason,
        report_id=report_id,
    )
    if author_id:
        db.add(
            Notification(
                user_id=author_id,
                kind="moderation",
                title="Your content was removed",
                body=f"Reason: {reason}. You can appeal this decision.",
                link=f"/moderation/appeal/{action.id}",
            )
        )
    await db.flush()
    return action


async def restore_content(
    db: AsyncSession, target_type: str, target_id: int, *, actor_id: int | None, reason: str
) -> ModerationAction | None:
    obj = await load_target(db, target_type, target_id)
    if obj is None:
        return None
    obj.status = "active"
    obj.removed_reason = None
    author_id = target_author_id(obj)
    action = await log_action(
        db,
        action="restore_content",
        target_type=target_type,
        target_id=target_id,
        actor_id=actor_id,
        subject_user_id=author_id,
        reason=reason,
        appealable=False,
    )
    if author_id:
        db.add(
            Notification(
                user_id=author_id,
                kind="moderation",
                title="Your content was restored",
                body=reason,
                link="/notifications",
            )
        )
    await db.flush()
    return action


async def suspend_user(
    db: AsyncSession, user_id: int, *, days: int, actor_id: int | None, reason: str
) -> ModerationAction:
    user = await db.get(User, user_id)
    if user:
        user.status = "suspended"
        user.suspended_until = utcnow() + dt.timedelta(days=days)
    action = await log_action(
        db,
        action="suspend_user",
        target_type="user",
        target_id=user_id,
        actor_id=actor_id,
        subject_user_id=user_id,
        reason=reason,
        meta={"days": days},
    )
    db.add(
        Notification(
            user_id=user_id,
            kind="moderation",
            title=f"Your account is suspended for {days} day(s)",
            body=f"Reason: {reason}. You can appeal this decision.",
            link=f"/moderation/appeal/{action.id}",
        )
    )
    await db.flush()
    return action


# --- report lifecycle -------------------------------------------------------


async def resolve_report(
    db: AsyncSession,
    report: Report,
    *,
    outcome: str,
    actor_id: int | None,
    note: str,
    actor_kind: str = "moderator",
) -> None:
    """outcome: 'upheld' | 'dismissed'."""
    report.status = outcome
    report.resolution = note
    report.resolved_by = actor_id
    report.resolved_at = utcnow()

    if outcome == "upheld":
        await remove_content(
            db,
            report.target_type,
            report.target_id,
            actor_id=actor_id,
            actor_kind=actor_kind,
            reason=note or f"Upheld report: {report.category}",
            report_id=report.id,
        )
        if report.target_author_id:
            reason = "harassment_upheld" if report.category == "harassment" else "report_upheld"
            await rep.award(
                db,
                report.target_author_id,
                reason,
                source_type="report",
                source_id=report.id,
                actor_id=actor_id,
                detail=report.category,
                settle_immediately=True,
            )
            await rep.freeze_if_needed(db, report.target_author_id)

    db.add(
        Notification(
            user_id=report.reporter_id,
            kind="moderation",
            title="Your report was reviewed",
            body=f"Outcome: {outcome}. {note}"[:400],
            link="/moderation/my-reports",
        )
    )
    await db.flush()


CONSENSUS_REQUIRED = 3


async def tally_queue_votes(db: AsyncSession, report: Report, actor_id: int | None) -> str | None:
    """Three consistent Trusted+ votes resolve a report without a staff moderator."""
    rows = (
        await db.execute(select(ModQueueVote).where(ModQueueVote.report_id == report.id))
    ).scalars().all()
    tally: dict[str, int] = {}
    for row in rows:
        tally[row.vote] = tally.get(row.vote, 0) + 1

    if tally.get("escalate", 0) >= 2:
        report.status = "escalated"
        await db.flush()
        return "escalated"
    for vote, outcome in (("remove", "upheld"), ("keep", "dismissed")):
        if tally.get(vote, 0) >= CONSENSUS_REQUIRED:
            await resolve_report(
                db,
                report,
                outcome=outcome,
                actor_id=actor_id,
                note=f"Community consensus ({tally[vote]} of {len(rows)} moderators)",
                actor_kind="community",
            )
            return outcome
    return None


# --- appeals ----------------------------------------------------------------


async def decide_appeal(
    db: AsyncSession, appeal: Appeal, *, upheld: bool, reviewer_id: int, note: str
) -> None:
    appeal.status = "upheld" if upheld else "denied"
    appeal.reviewer_id = reviewer_id
    appeal.decision_note = note
    appeal.decided_at = utcnow()

    action = await db.get(ModerationAction, appeal.action_id)
    if upheld and action:
        if action.action == "remove_content":
            await restore_content(
                db, action.target_type, action.target_id, actor_id=reviewer_id, reason="Appeal upheld"
            )
        elif action.action == "suspend_user" and action.subject_user_id:
            user = await db.get(User, action.subject_user_id)
            if user:
                user.status = "active"
                user.suspended_until = None
        # Reverse the conduct penalty that the original action applied.
        await rep.void_events(
            db, source_type=action.target_type, source_id=action.target_id, reason_note="appeal upheld"
        )
        if action.subject_user_id:
            await rep.recompute(db, action.subject_user_id)

    await log_action(
        db,
        action="appeal_" + appeal.status,
        target_type="appeal",
        target_id=appeal.id,
        actor_id=reviewer_id,
        subject_user_id=appeal.user_id,
        reason=note,
        appealable=False,
    )
    db.add(
        Notification(
            user_id=appeal.user_id,
            kind="moderation",
            title=f"Your appeal was {appeal.status}",
            body=note[:400],
            link="/moderation/my-appeals",
        )
    )
    await db.flush()


# --- transparency -----------------------------------------------------------


async def transparency_for(db: AsyncSession, period_start: dt.datetime, period_end: dt.datetime) -> dict[str, Any]:
    by_category = dict(
        (
            await db.execute(
                select(Report.category, func.count(Report.id))
                .where(Report.created_at >= period_start, Report.created_at < period_end)
                .group_by(Report.category)
            )
        ).all()
    )
    by_status = dict(
        (
            await db.execute(
                select(Report.status, func.count(Report.id))
                .where(Report.created_at >= period_start, Report.created_at < period_end)
                .group_by(Report.status)
            )
        ).all()
    )
    actions = dict(
        (
            await db.execute(
                select(ModerationAction.action, func.count(ModerationAction.id))
                .where(
                    ModerationAction.created_at >= period_start,
                    ModerationAction.created_at < period_end,
                )
                .group_by(ModerationAction.action)
            )
        ).all()
    )
    median_hours = (
        await db.execute(
            select(
                func.percentile_cont(0.5).within_group(
                    func.extract("epoch", Report.resolved_at - Report.created_at) / 3600.0
                )
            ).where(
                Report.resolved_at.is_not(None),
                Report.created_at >= period_start,
                Report.created_at < period_end,
            )
        )
    ).scalar_one_or_none()

    appeals_total = int(
        (
            await db.execute(
                select(func.count(Appeal.id)).where(
                    Appeal.created_at >= period_start, Appeal.created_at < period_end
                )
            )
        ).scalar_one()
        or 0
    )
    appeals_upheld = int(
        (
            await db.execute(
                select(func.count(Appeal.id)).where(
                    Appeal.created_at >= period_start,
                    Appeal.created_at < period_end,
                    Appeal.status == "upheld",
                )
            )
        ).scalar_one()
        or 0
    )
    return {
        "reports_by_category": {str(k): int(v) for k, v in by_category.items()},
        "reports_by_status": {str(k): int(v) for k, v in by_status.items()},
        "actions": {str(k): int(v) for k, v in actions.items()},
        "median_resolution_hours": round(float(median_hours), 2) if median_hours is not None else None,
        "appeals_total": appeals_total,
        "appeals_upheld": appeals_upheld,
    }
