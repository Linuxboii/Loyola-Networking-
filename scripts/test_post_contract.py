"""Regression checks for the Android post-create contract.

Run with ``python scripts/test_post_contract.py``.  This stays database-free so
the payload that a released phone sends is checked before any live rollout.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.schemas import PostIn, post_out  # noqa: E402
from app.models import POST_TYPES  # noqa: E402


def main() -> int:
    # Build 2 calls an image post after its upload has succeeded.  Rejecting the
    # discriminator here produces a FastAPI 422 before posting.create_post runs.
    legacy_image = PostIn(kind="image", body="Photo from campus", media=["posts/photo.jpg"])
    if legacy_image.kind != "image":
        print("FAIL legacy Android image payload was not preserved")
        return 1
    if "media" not in POST_TYPES:
        print("FAIL canonical media post type is not allowed")
        return 1

    # API-created posts store media with metadata. Mobile clients require
    # signed URL strings rather than raw metadata objects.
    post = SimpleNamespace(
        id=1, kind="media", title=None, body="Photo from campus", tags=[],
        link_url=None, media=[{"path": "posts/photo.jpg", "type": "image"}],
        group_id=None, is_official=False, score=0, comment_count=0,
        created_at=None, edited_at=None, author=None,
    )
    returned_media = post_out(post)["media"]
    if len(returned_media) != 1 or not isinstance(returned_media[0], str) or not returned_media[0].startswith("/media/"):
        print("FAIL object-backed post media was not serialized as a signed URL")
        return 1
    print("ok   legacy Android image payload is accepted for normalization")
    print("ok   canonical media post type remains allowed")
    print("ok   object-backed post media is serialized as a signed URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
