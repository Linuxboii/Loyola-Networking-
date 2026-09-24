"""Bootstrap data the app fetches once on launch, plus campus-wide stats."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import group_out
from app.config import settings
from app.deps import CAPABILITIES, CurrentUser, DbDep, Reader
from app.models import POST_TYPES, REP_TIERS
from app.services import feed as feed_service
from app.services import releases as release_service

router = APIRouter(tags=["api:meta"])

# Bumping this tells older builds to prompt for an update rather than fail in
# confusing ways when an endpoint changes shape.
API_VERSION = 1

# The floor itself lives with the releases, not here: raising it is part of
# publishing the build that replaces the broken one, so the two cannot drift.
# See app/services/releases.py and scripts/publish_release.py --min-supported.


@router.get("/config")
async def client_config(user: CurrentUser):
    """Unauthenticated: everything a fresh install needs before sign-in."""
    latest = release_service.latest_release()
    return {
        "api_version": API_VERSION,
        "min_supported_build": release_service.minimum_supported_build(),
        "latest_build": latest.build if latest else None,
        "latest_version": latest.version if latest else None,
        "update_endpoint": "/api/v1/updates/android/latest",
        "app_name": settings.app_name,
        "campus_name": settings.campus_name,
        "post_types": list(POST_TYPES),
        "reputation_tiers": [{"name": name, "at": threshold} for name, threshold in REP_TIERS],
        "capabilities": list(CAPABILITIES),
        "max_upload_mb": settings.max_upload_mb,
        "support": {
            "counsellor": settings.counsellor_contact,
            "grievance_officer": settings.grievance_officer,
            "grievance_email": settings.grievance_email,
        },
        "authenticated": user is not None,
    }


@router.get("/home")
async def home_sidebar(db: DbDep, user: Reader):
    """The chrome around the feed: trending tags, groups to join, campus stats."""
    return {
        "trending_tags": [{"tag": tag, "count": count} for tag, count in await feed_service.trending_tags(db)],
        "suggested_groups": [group_out(g) for g in await feed_service.suggested_groups(db, user)],
        "stats": await feed_service.campus_stats(db),
    }
