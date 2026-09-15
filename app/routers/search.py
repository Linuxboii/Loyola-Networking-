"""Unified search across every module."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.deps import DbDep, Reader
from app.services import search as search_service
from app.templating import templates

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(request: Request, db: DbDep, user: Reader, q: str = "", scope: str = "all"):
    q = (q or "").strip()
    results: dict = {}
    if q:
        if scope == "people":
            results = {"people": await search_service.people(db, q, viewer=user, limit=50)}
        elif scope == "questions":
            results = {"questions": await search_service.questions(db, q, limit=50)}
        elif scope == "resources":
            results = {"resources": await search_service.resources(db, q, limit=50)}
        elif scope == "projects":
            results = {"projects": await search_service.projects(db, q, open_only=False, limit=50)}
        elif scope == "opportunities":
            results = {"opportunities": await search_service.opportunities(db, q, limit=50)}
        else:
            results = await search_service.everything(db, q, user)

    total = sum(len(v) for v in results.values())
    return templates.TemplateResponse(
        request,
        "search/index.html",
        {
            "title": f"Search: {q}" if q else "Search",
            "q": q,
            "scope": scope,
            "results": results,
            "total": total,
        },
    )
