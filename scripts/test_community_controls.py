"""Behaviour contract for identity, mentions, and delegated administration.

Run with ``python scripts/test_community_controls.py``.  These assertions use
the production policy helpers directly: a regression that re-links anonymous
moderator content, stops deduplicating mentions, or over-grants an admin makes
this script fail.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.community_controls import (  # noqa: E402
    MODERATOR_DISPLAY_NAME,
    active_actions,
    content_identity,
    mentioned_handles,
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok   {label}")


def main() -> int:
    # A future change that stores the caller's user id in a moderator actor
    # would expose the anonymous account link and must fail this contract.
    normal = content_identity(user_id=42, handle="alice", full_name="Alice", as_moderator=False)
    moderator = content_identity(user_id=42, handle="alice", full_name="Alice", as_moderator=True)
    check("normal identity keeps its account", normal.user_id == 42 and normal.handle == "alice")
    check(
        "moderator identity has no account link",
        moderator.user_id is None and moderator.handle is None and moderator.full_name == MODERATOR_DISPLAY_NAME,
    )

    check(
        "mentions are lowercase, unique, and preserve first appearance",
        mentioned_handles("Hi @Alice, @bob! @ALICE and email@domain.com") == ["alice", "bob"],
    )

    now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
    check(
        "active delegated action is allowed",
        active_actions(["approve_verification"], now + dt.timedelta(hours=1), now) == {"approve_verification"},
    )
    check(
        "expired delegated action is denied",
        active_actions(["approve_verification"], now - dt.timedelta(seconds=1), now) == set(),
    )
    check(
        "empty expiry is a non-expiring delegated action",
        active_actions(["manage_moderators"], None, now) == {"manage_moderators"},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
