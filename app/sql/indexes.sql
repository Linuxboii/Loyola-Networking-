-- Indexes and expressions the ORM cannot declare.
-- Every statement is idempotent; main.py runs this file on each boot.
-- The separator between statements is a semicolon followed immediately by two
-- dashes, which keeps multi-line statement bodies intact. Do not write that
-- sequence inside a comment, or the file splits in the wrong place.

CREATE EXTENSION IF NOT EXISTS pg_trgm
;--

CREATE EXTENSION IF NOT EXISTS btree_gin
;--

-- People search: fuzzy name matching for "who knows Flutter" style lookups.
CREATE INDEX IF NOT EXISTS ix_users_handle_trgm
    ON users USING gin (handle gin_trgm_ops)
;--

CREATE INDEX IF NOT EXISTS ix_users_dept_batch
    ON users (department, batch_year) WHERE tier >= 2
;--

-- Q&A full text. Expression index so search.py's to_tsvector call is an index
-- scan rather than a sequential one over every question ever asked.
CREATE INDEX IF NOT EXISTS ix_questions_fts
    ON questions USING gin (to_tsvector('english', title || ' ' || body))
;--

CREATE INDEX IF NOT EXISTS ix_questions_title_trgm
    ON questions USING gin (title gin_trgm_ops)
;--

CREATE INDEX IF NOT EXISTS ix_questions_open
    ON questions (created_at DESC) WHERE status = 'active' AND answer_count = 0
;--

CREATE INDEX IF NOT EXISTS ix_answers_question_active
    ON answers (question_id, score DESC) WHERE status = 'active'
;--

-- Feed: the hot path. Partial index keeps removed content out of the scan.
CREATE INDEX IF NOT EXISTS ix_posts_active_recent
    ON posts (created_at DESC) WHERE status = 'active'
;--

CREATE INDEX IF NOT EXISTS ix_posts_group_recent
    ON posts (group_id, created_at DESC) WHERE status = 'active'
;--

CREATE INDEX IF NOT EXISTS ix_comments_post
    ON comments (post_id, created_at) WHERE status = 'active'
;--

-- Reputation ledger: the per-user timeline and the monthly cap query.
CREATE INDEX IF NOT EXISTS ix_repevent_user_pillar_month
    ON reputation_events (user_id, pillar, created_at DESC)
;--

CREATE INDEX IF NOT EXISTS ix_repevent_settling
    ON reputation_events (settles_at) WHERE state = 'provisional'
;--

CREATE INDEX IF NOT EXISTS ix_repevent_source
    ON reputation_events (source_type, source_id)
;--

-- Moderation queue and the transparency report.
CREATE INDEX IF NOT EXISTS ix_reports_open
    ON reports (created_at) WHERE status IN ('open', 'in_review')
;--

CREATE INDEX IF NOT EXISTS ix_harassment_recent
    ON harassment_signals (subject_user_id, created_at DESC)
;--

-- Verification: the review queue and the 30-day purge sweep.
CREATE INDEX IF NOT EXISTS ix_verification_queue
    ON verification_records (created_at) WHERE status = 'in_review'
;--

CREATE INDEX IF NOT EXISTS ix_verification_purge
    ON verification_records (purge_after) WHERE artifacts_purged_at IS NULL
;--

-- Resource vault browsing.
CREATE INDEX IF NOT EXISTS ix_resources_browse
    ON resources (course, semester, subject) WHERE status = 'active'
;--

CREATE INDEX IF NOT EXISTS ix_resources_title_trgm
    ON resources USING gin (title gin_trgm_ops)
;--

-- Events calendar and opportunity deadlines.
CREATE INDEX IF NOT EXISTS ix_events_upcoming
    ON events (starts_at) WHERE status = 'active'
;--

CREATE INDEX IF NOT EXISTS ix_opportunities_live
    ON opportunities (created_at DESC) WHERE status = 'active'
;--

CREATE INDEX IF NOT EXISTS ix_notifications_unread
    ON notifications (user_id, created_at DESC) WHERE read_at IS NULL
;--

CREATE INDEX IF NOT EXISTS ix_sessions_live
    ON sessions (user_id, expires_at) WHERE revoked_at IS NULL
;--

-- Rate limit rows are disposable; this keeps the cleanup cheap.
CREATE INDEX IF NOT EXISTS ix_ratelimit_window
    ON rate_limits (window_start)
;--

-- One account per roll number, stated at the database level because the PRD
-- makes it non-negotiable. The partial form allows many NULLs pre-verification.
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_roll_number
    ON users (roll_number) WHERE roll_number IS NOT NULL
;--

-- Private actor attribution for shared @mod content.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS moderator_actor_id BIGINT REFERENCES users(id) ON DELETE SET NULL
;--
ALTER TABLE comments ADD COLUMN IF NOT EXISTS moderator_actor_id BIGINT REFERENCES users(id) ON DELETE SET NULL
;--
ALTER TABLE answers ADD COLUMN IF NOT EXISTS moderator_actor_id BIGINT REFERENCES users(id) ON DELETE SET NULL
;--
CREATE INDEX IF NOT EXISTS ix_posts_moderator_actor ON posts (moderator_actor_id) WHERE moderator_actor_id IS NOT NULL
;--
CREATE INDEX IF NOT EXISTS ix_comments_moderator_actor ON comments (moderator_actor_id) WHERE moderator_actor_id IS NOT NULL
;--
CREATE INDEX IF NOT EXISTS ix_answers_moderator_actor ON answers (moderator_actor_id) WHERE moderator_actor_id IS NOT NULL
;--

ALTER TABLE follows ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'accepted'
;--
CREATE INDEX IF NOT EXISTS ix_follows_status ON follows (status)
;--
