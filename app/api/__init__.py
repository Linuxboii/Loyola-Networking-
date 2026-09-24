"""JSON API for the Loyola Networking mobile clients.

The web app is server-rendered; this package exposes the same domain services
over JSON so the Android client is a first-class citizen rather than a scraper.
Everything here is versioned under ``/api/v1`` and authenticated with the same
opaque session token the browser stores in a cookie — presented as
``Authorization: Bearer <token>``.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    auth,
    community,
    events,
    feed,
    groups,
    meta,
    notifications,
    people,
    qa,
    updates,
    verify,
)

api_router = APIRouter(prefix="/api/v1")

for module in (auth, meta, feed, qa, people, community, groups, events, notifications, verify, updates):
    api_router.include_router(module.router)

__all__ = ["api_router"]
