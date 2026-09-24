"""Every persisted entity in Loyola Networking.

Design notes that matter:

* Reputation is **event sourced**. ``ReputationEvent`` rows are immutable; the
  numbers on ``User`` are a cache that ``services.reputation`` recomputes. Never
  mutate a user's score directly ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â append an event.
* Votes are polymorphic (``target_type`` + ``target_id``) so one table and one
  anti-gaming code path covers posts, comments, questions and answers.
* Verification artefacts live on disk encrypted; only paths, extracted fields and
  a hash live here, and ``VerificationRecord.purge_after`` drives their deletion.
* Moderation actions and PII access are append-only audit tables. Nothing in the
  app updates or deletes them.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


TS = DateTime(timezone=True)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, server_default=func.now(), index=True)


# ---------------------------------------------------------------------------
# Constants (kept as plain strings ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no native PG enums, so they stay alterable)
# ---------------------------------------------------------------------------

TIER_UNVERIFIED = 0
TIER_PROVISIONAL = 1
TIER_VERIFIED = 2
TIER_ROLE = 3

PILLARS = ("academic", "build", "service", "participation", "conduct")
PILLAR_LABELS = {
    "academic": "Academic Helpfulness",
    "build": "Build & Ship",
    "service": "Community Service",
    "participation": "Constructive Participation",
    "conduct": "Trust & Conduct",
}
PILLAR_WEIGHTS = {"academic": 0.30, "build": 0.25, "service": 0.20, "participation": 0.15, "conduct": 0.10}
PILLAR_MONTHLY_CAPS = {"academic": 300, "build": 250, "service": 200, "participation": 150, "conduct": 100}

REP_TIERS = [
    ("Newcomer", 0),
    ("Contributor", 100),
    ("Established", 500),
    ("Trusted", 1500),
    ("Pillar", 4000),
]

REPORT_CATEGORIES = [
    ("harassment", "Harassment or bullying"),
    ("spam", "Spam"),
    ("misinformation", "Misinformation"),
    ("academic_dishonesty", "Academic dishonesty"),
    ("impersonation", "Impersonation"),
    ("explicit", "Explicit content"),
    ("other", "Other"),
]

POST_TYPES = ("text", "image", "media", "poll", "link", "project", "event", "notice")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # The roll number is the account anchor and the one-account-per-student
    # guarantee from PRD 5.3. Nullable until verification extracts it.
    roll_number: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True)
    handle: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    # Identity fields below come from the verified ID card and are not user editable.
    full_name: Mapped[str] = mapped_column(String(120))
    course: Mapped[Optional[str]] = mapped_column(String(80))
    department: Mapped[Optional[str]] = mapped_column(String(80))
    batch_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    card_expiry: Mapped[Optional[dt.date]] = mapped_column(Date)
    photo_path: Mapped[Optional[str]] = mapped_column(String(255))

    # User editable profile.
    bio: Mapped[str] = mapped_column(Text, default="")
    interests: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    links: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    tier: Mapped[int] = mapped_column(Integer, default=TIER_UNVERIFIED, index=True)
    tier1_expires_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)  # moderator, admin, super_admin, club_officer
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|suspended|banned
    suspended_until: Mapped[Optional[dt.datetime]] = mapped_column(TS)

    # Reputation cache ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â authoritative source is reputation_events.
    rep_total: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    rep_academic: Mapped[float] = mapped_column(Float, default=0.0)
    rep_build: Mapped[float] = mapped_column(Float, default=0.0)
    rep_service: Mapped[float] = mapped_column(Float, default=0.0)
    rep_participation: Mapped[float] = mapped_column(Float, default=0.0)
    rep_conduct: Mapped[float] = mapped_column(Float, default=0.0)
    rep_tier: Mapped[str] = mapped_column(String(20), default="Newcomer", index=True)
    rep_frozen: Mapped[bool] = mapped_column(Boolean, default=False)

    # Privacy controls (PRD 7.2).
    hide_from_search: Mapped[bool] = mapped_column(Boolean, default=False)
    hide_activity: Mapped[bool] = mapped_column(Boolean, default=False)
    endorse_policy: Mapped[str] = mapped_column(String(16), default="anyone")  # anyone|batch|nobody

    recovery_email: Mapped[Optional[str]] = mapped_column(String(160))
    last_active_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, index=True)
    consent_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    deletion_requested_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("tier between 0 and 3", name="ck_users_tier"),
        Index("ix_users_name_trgm", "full_name", postgresql_using="gin", postgresql_ops={"full_name": "gin_trgm_ops"}),
    )

    @property
    def is_moderator(self) -> bool:
        return "moderator" in (self.roles or []) or "admin" in (self.roles or [])

    @property
    def is_admin(self) -> bool:
        return "admin" in (self.roles or []) or self.is_super_admin

    @property
    def is_super_admin(self) -> bool:
        return "super_admin" in (self.roles or [])

    @property
    def display_year(self) -> str:
        if not self.batch_year:
            return ""
        return f"Batch of {self.batch_year}"


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_fp: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    expires_at: Mapped[dt.datetime] = mapped_column(TS, index=True)
    revoked_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class DeviceFingerprint(Base, TimestampMixin):
    """Links a browser fingerprint to accounts, so one person farming several
    accounts is visible to moderators and their mutual votes get discounted."""

    __tablename__ = "device_fingerprints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fp_hash: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)

    __table_args__ = (UniqueConstraint("fp_hash", "user_id", name="uq_device_user"),)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class VerificationRecord(Base, TimestampMixin):
    __tablename__ = "verification_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending | needs_selfie | in_review | approved | rejected | expired

    # OCR output.
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_words: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Encrypted artefact paths. Cleared by the retention job.
    card_image_path: Mapped[Optional[str]] = mapped_column(String(255))
    card_face_path: Mapped[Optional[str]] = mapped_column(String(255))
    selfie_path: Mapped[Optional[str]] = mapped_column(String(255))
    card_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    artifacts_purged_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    purge_after: Mapped[Optional[dt.datetime]] = mapped_column(TS, index=True)

    reviewer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[Optional[str]] = mapped_column(String(20))
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    device_fp: Mapped[Optional[str]] = mapped_column(String(64))
    ip: Mapped[Optional[str]] = mapped_column(String(64))


class VerificationAttempt(Base, TimestampMixin):
    """Rate-limit ledger: PRD 5.3 caps verification attempts per identity/week."""

    __tablename__ = "verification_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_fp: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(20))


class PiiAccessLog(Base, TimestampMixin):
    """Every read of a verification artefact, per PRD 5.3 'access-logged'."""

    __tablename__ = "pii_access_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    record_id: Mapped[int] = mapped_column(BigInteger, index=True)
    artifact: Mapped[str] = mapped_column(String(32))  # card|selfie|card_face
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    ip: Mapped[Optional[str]] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class Post(Base, TimestampMixin):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    moderator_actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="text", index=True)
    title: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    media: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    link_url: Mapped[Optional[str]] = mapped_column(String(500))

    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))

    is_official: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|removed|hidden
    removed_reason: Mapped[Optional[str]] = mapped_column(String(255))

    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    edited_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)

    author: Mapped[User] = relationship(lazy="joined", foreign_keys=[author_id])

    __table_args__ = (
        Index("ix_posts_feed", "status", "created_at"),
        Index("ix_posts_tags", "tags", postgresql_using="gin"),
    )


class PollOption(Base):
    __tablename__ = "poll_options"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer, default=0)
    votes: Mapped[int] = mapped_column(Integer, default=0)


class PollVote(Base, TimestampMixin):
    __tablename__ = "poll_votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    option_id: Mapped[int] = mapped_column(ForeignKey("poll_options.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_pollvote_once"),)


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    moderator_actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    removed_reason: Mapped[Optional[str]] = mapped_column(String(255))
    score: Mapped[int] = mapped_column(Integer, default=0)

    author: Mapped[User] = relationship(lazy="joined", foreign_keys=[author_id])


class Question(Base, TimestampMixin):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    subject: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    semester: Mapped[Optional[str]] = mapped_column(String(20), index=True)

    # The Anonymous Doubt Box (PRD 7.9.1): hidden from readers, never from the
    # system ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â author_id is always populated so abuse stays traceable.
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False)

    accepted_answer_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    removed_reason: Mapped[Optional[str]] = mapped_column(String(255))
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    answer_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)

    author: Mapped[User] = relationship(lazy="joined", foreign_keys=[author_id])

    __table_args__ = (Index("ix_questions_tags", "tags", postgresql_using="gin"),)


class Answer(Base, TimestampMixin):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    moderator_actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    body: Mapped[str] = mapped_column(Text)
    is_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    removed_reason: Mapped[Optional[str]] = mapped_column(String(255))
    score: Mapped[int] = mapped_column(Integer, default=0)

    author: Mapped[User] = relationship(lazy="joined", foreign_keys=[author_id])


class Vote(Base, TimestampMixin):
    """Polymorphic vote. One row per (voter, target). ``weight`` is the applied
    reputation weight at cast time, after tier weighting and diminishing returns."""

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    voter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(16), index=True)  # post|comment|question|answer
    target_id: Mapped[int] = mapped_column(BigInteger, index=True)
    value: Mapped[int] = mapped_column(Integer)  # +1 / -1
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    device_fp: Mapped[Optional[str]] = mapped_column(String(64))
    nullified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    __table_args__ = (
        UniqueConstraint("voter_id", "target_type", "target_id", name="uq_vote_once"),
        CheckConstraint("value in (-1, 1)", name="ck_vote_value"),
        CheckConstraint("voter_id <> author_id", name="ck_no_self_vote"),
    )


# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------


class ReputationEvent(Base, TimestampMixin):
    """Immutable ledger row. Never updated except to move ``state`` forward."""

    __tablename__ = "reputation_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pillar: Mapped[str] = mapped_column(String(20), index=True)
    points: Mapped[float] = mapped_column(Numeric(10, 2))
    raw_points: Mapped[float] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String(64))
    detail: Mapped[Optional[str]] = mapped_column(String(255))
    source_type: Mapped[Optional[str]] = mapped_column(String(24))
    source_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    actor_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    state: Mapped[str] = mapped_column(String(16), default="provisional", index=True)
    # provisional | settled | void | held
    settles_at: Mapped[Optional[dt.datetime]] = mapped_column(TS, index=True)
    capped_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    __table_args__ = (Index("ix_repevent_user_created", "user_id", "created_at"),)


class VotePairStat(Base):
    """Rolling reciprocity counter used to spot vote-trading rings cheaply."""

    __tablename__ = "vote_pair_stats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    voter_id: Mapped[int] = mapped_column(BigInteger, index=True)
    author_id: Mapped[int] = mapped_column(BigInteger, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    last_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("voter_id", "author_id", name="uq_vote_pair"),)


# ---------------------------------------------------------------------------
# Skills & endorsements
# ---------------------------------------------------------------------------


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(50))
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class UserSkill(Base, TimestampMixin):
    __tablename__ = "user_skills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    endorsement_count: Mapped[int] = mapped_column(Integer, default=0)
    endorsement_weight: Mapped[float] = mapped_column(Float, default=0.0)

    skill: Mapped[Skill] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("user_id", "skill_id", name="uq_user_skill"),)


class Endorsement(Base, TimestampMixin):
    __tablename__ = "endorsements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    endorser_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (
        UniqueConstraint("endorser_id", "user_id", "skill_id", name="uq_endorse_once"),
        CheckConstraint("endorser_id <> user_id", name="ck_no_self_endorse"),
    )


# ---------------------------------------------------------------------------
# Groups, clubs, events
# ---------------------------------------------------------------------------


class Group(Base, TimestampMixin):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="interest", index=True)  # interest|club
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    banner_path: Mapped[Optional[str]] = mapped_column(String(255))
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    club_rep: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)


class GroupMember(Base, TimestampMixin):
    __tablename__ = "group_members"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # member|officer|admin

    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[str] = mapped_column(String(160), default="")
    starts_at: Mapped[dt.datetime] = mapped_column(TS, index=True)
    ends_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    capacity: Mapped[Optional[int]] = mapped_column(Integer)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    checkin_secret: Mapped[str] = mapped_column(String(32))
    rsvp_count: Mapped[int] = mapped_column(Integer, default=0)
    checkin_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    host: Mapped[User] = relationship(lazy="joined")


class Rsvp(Base, TimestampMixin):
    __tablename__ = "rsvps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(16), default="going")  # going|maybe|cancelled
    ticket_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    checked_in_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    role: Mapped[str] = mapped_column(String(16), default="attendee")  # attendee|volunteer

    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_rsvp_once"),)


# ---------------------------------------------------------------------------
# Project board
# ---------------------------------------------------------------------------


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    repo_url: Mapped[Optional[str]] = mapped_column(String(400))
    demo_url: Mapped[Optional[str]] = mapped_column(String(400))
    video_url: Mapped[Optional[str]] = mapped_column(String(400))
    skills_needed: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    min_rep_tier: Mapped[str] = mapped_column(String(20), default="Newcomer")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|closed|removed
    artifact_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    owner: Mapped[User] = relationship(lazy="joined")

    __table_args__ = (Index("ix_projects_skills", "skills_needed", postgresql_using="gin"),)


class ProjectRole(Base):
    __tablename__ = "project_roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(80))
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    slots: Mapped[int] = mapped_column(Integer, default=1)
    filled: Mapped[int] = mapped_column(Integer, default=0)


class Application(Base, TimestampMixin):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("project_roles.id", ondelete="SET NULL"))
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    applicant: Mapped[User] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("project_id", "applicant_id", name="uq_apply_once"),)


# ---------------------------------------------------------------------------
# Resource vault
# ---------------------------------------------------------------------------


class Resource(Base, TimestampMixin):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    course: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    semester: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="notes")  # notes|paper|manual|cheatsheet
    file_path: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(180))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("resources.id", ondelete="SET NULL"))
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    origin_attested: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|takedown|removed

    uploader: Mapped[User] = relationship(lazy="joined")


class ResourceVote(Base, TimestampMixin):
    __tablename__ = "resource_votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    __table_args__ = (UniqueConstraint("resource_id", "user_id", name="uq_resource_vote"),)


class TakedownRequest(Base, TimestampMixin):
    __tablename__ = "takedown_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    requester_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    claimant: Mapped[str] = mapped_column(String(160), default="")
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolution: Mapped[Optional[str]] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Mentorship, lost & found, placements, study rooms
# ---------------------------------------------------------------------------


class MentorProfile(Base, TimestampMixin):
    __tablename__ = "mentor_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    domains: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    blurb: Mapped[str] = mapped_column(Text, default="")
    capacity_per_month: Mapped[int] = mapped_column(Integer, default=4)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sessions_done: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(lazy="joined")


class MentorSession(Base, TimestampMixin):
    __tablename__ = "mentor_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mentor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mentee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(Text, default="")
    scheduled_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(16), default="requested", index=True)
    # requested | accepted | declined | completed | cancelled
    mentor_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    mentee_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    credited: Mapped[bool] = mapped_column(Boolean, default=False)


class LostFound(Base, TimestampMixin):
    __tablename__ = "lost_found"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)  # lost|found
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    happened_on: Mapped[Optional[dt.date]] = mapped_column(Date)
    image_path: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|resolved|removed

    reporter: Mapped[User] = relationship(lazy="joined")


class Opportunity(Base, TimestampMixin):
    """Placement & internship wall entries."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    poster_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(120), index=True)
    role_title: Mapped[str] = mapped_column(String(140))
    kind: Mapped[str] = mapped_column(String(20), default="internship", index=True)
    # internship | placement | referral | hackathon
    location: Mapped[str] = mapped_column(String(120), default="")
    stipend: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[Optional[str]] = mapped_column(String(500))
    deadline: Mapped[Optional[dt.date]] = mapped_column(Date, index=True)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    poster: Mapped[User] = relationship(lazy="joined")


class InterviewExperience(Base, TimestampMixin):
    __tablename__ = "interview_experiences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(120), index=True)
    role_title: Mapped[str] = mapped_column(String(140))
    verdict: Mapped[str] = mapped_column(String(24), default="pending")  # selected|rejected|pending
    rounds: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    body: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    author: Mapped[User] = relationship(lazy="joined")


class StudyRoom(Base, TimestampMixin):
    __tablename__ = "study_rooms"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    mode: Mapped[str] = mapped_column(String(12), default="physical")  # physical|virtual
    location: Mapped[str] = mapped_column(String(200), default="")
    starts_at: Mapped[dt.datetime] = mapped_column(TS, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    capacity: Mapped[int] = mapped_column(Integer, default=8)
    member_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    host: Mapped[User] = relationship(lazy="joined")


class StudyRoomMember(Base, TimestampMixin):
    __tablename__ = "study_room_members"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("study_rooms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_member"),)


# ---------------------------------------------------------------------------
# Moderation & safety
# ---------------------------------------------------------------------------


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(20), index=True)
    target_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_author_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(16), default="user")  # user|auto
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    # open | in_review | upheld | dismissed | escalated | crisis
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    resolved_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    resolved_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)

    __table_args__ = (UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_report_once"),)


class ModQueueVote(Base, TimestampMixin):
    """Trusted+ community moderation: three consistent votes resolve a report."""

    __tablename__ = "mod_queue_votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    moderator_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    vote: Mapped[str] = mapped_column(String(12))  # remove|keep|escalate
    note: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("report_id", "moderator_id", name="uq_modvote_once"),)


class ModerationAction(Base, TimestampMixin):
    """Append-only. Never updated, never deleted ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â this is the audit trail."""

    __tablename__ = "moderation_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="moderator")  # moderator|system|community
    action: Mapped[str] = mapped_column(String(40), index=True)
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column(BigInteger)
    subject_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    report_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    appealable: Mapped[bool] = mapped_column(Boolean, default=True)


class Appeal(Base, TimestampMixin):
    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("moderation_actions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|upheld|denied
    reviewer_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    decision_note: Mapped[Optional[str]] = mapped_column(Text)
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class HarassmentSignal(Base, TimestampMixin):
    """Brigading detector state: negative interactions aimed at one user."""

    __tablename__ = "harassment_signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # downvote|report|comment
    source_type: Mapped[Optional[str]] = mapped_column(String(20))
    source_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class CrisisFlag(Base, TimestampMixin):
    """Self-harm / distress routing. Non-punitive by construction: raising a flag
    never removes content or suspends an account (PRD 8, crisis handling)."""

    __tablename__ = "crisis_flags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column(BigInteger)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    handled_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    handled_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    note: Mapped[Optional[str]] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

class Follow(Base, TimestampMixin):
    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    following_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|accepted

    __table_args__ = (
        UniqueConstraint("follower_id", "following_id", name="uq_follow_once"),
        CheckConstraint("follower_id <> following_id", name="ck_follow_not_self"),
    )


class CommunityProfile(Base, TimestampMixin):
    __tablename__ = "community_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_og: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class AdminGrant(Base, TimestampMixin):
    __tablename__ = "admin_grants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    actions: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    expires_at: Mapped[Optional[dt.datetime]] = mapped_column(TS, index=True)
    revoked_at: Mapped[Optional[dt.datetime]] = mapped_column(TS, index=True)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(String(400), default="")
    link: Mapped[str] = mapped_column(String(300), default="/")
    read_at: Mapped[Optional[dt.datetime]] = mapped_column(TS, index=True)


class RateLimit(Base):
    __tablename__ = "rate_limits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(120), index=True)
    window_start: Mapped[dt.datetime] = mapped_column(TS, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("bucket", "window_start", name="uq_ratelimit"),)


class AppSetting(Base):
    """Runtime-editable configuration, e.g. OCR templates and the roll-number
    regex, so verification can be corrected without a redeploy."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger)


class TransparencySnapshot(Base, TimestampMixin):
    __tablename__ = "transparency_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
