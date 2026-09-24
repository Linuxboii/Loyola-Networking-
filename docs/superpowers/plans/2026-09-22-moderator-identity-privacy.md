# Moderator identity and handle privacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task.

**Goal:** Let every moderator publish, reply, and answer as either their own handle or the shared @mod identity while protecting real names from regular users.

**Architecture:** Existing shared `loyola_moderator` remains the public author for @mod content. Nullable `moderator_actor_id` fields on posts, comments, and answers preserve the real actor for moderator-only serialization. Serializers accept the viewer role and return real names only to moderators; notification fan-out resolves all active moderators when content responds to @mod.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, Flutter.

**Global Constraints:** Backwards compatible JSON fields; database change uses idempotent PostgreSQL SQL; no regular-user response exposes `full_name`; backend enforces moderator authorization; publish an updated Android build.

### Task 1: Database and API contract
- Add nullable private moderator actor foreign keys and idempotent migration SQL.
- Extend answers with `as_moderator`; preserve existing post/comment request fields.
- Make serializers viewer-aware and assert that public cards expose only @handles.

### Task 2: Shared identity and notification routing
- Persist real moderator actor when a moderator selects @mod for posts, comments, or answers.
- Fan reply/comment notifications to every active moderator when the target is shared @mod content.
- Add failing contract coverage for the shared identity, answer selection, and private viewer fields, then make it pass.

### Task 3: Mobile and release
- Add the existing identity selector to the Q&A answer composer and send its selection.
- Render handle-only public labels and retain moderator-only private names.
- Run Python, Flutter, and local production-shaped checks; deploy database-compatible backend then publish the APK.