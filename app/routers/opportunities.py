"""Placement and internship wall, plus interview write-ups.

With no placement-cell account to rely on, "official" is a badge a moderator
grants to a listing after checking it, rather than something a role assumes.
Interview experiences earn real Academic Helpfulness points — they are the
highest-value thing a final-year can leave behind.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.deps import DbDep, Reader, Verified, verify_csrf
from app.models import InterviewExperience, Opportunity, utcnow
from app.services import antiabuse, notify
from app.services import moderation as mod
from app.services import reputation as rep
from app.services import search as search_service
from app.templating import templates

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

KINDS = [
    ("internship", "Internship"),
    ("placement", "Full-time role"),
    ("referral", "Referral I can give"),
    ("hackathon", "Hackathon or contest"),
]


@router.get("")
async def index(
    request: Request, db: DbDep, user: Reader, q: str = "", kind: str = "", official: str = ""
):
    listings = await search_service.opportunities(
        db, q.strip(), kind=kind or None, official_only=(official == "1"), limit=80
    )
    live = [o for o in listings if not o.deadline or o.deadline >= dt.date.today()]
    expired = [o for o in listings if o.deadline and o.deadline < dt.date.today()]

    experiences = list(
        (
            await db.execute(
                select(InterviewExperience)
                .options(joinedload(InterviewExperience.author))
                .where(InterviewExperience.status == "active")
                .order_by(InterviewExperience.created_at.desc())
                .limit(12)
            )
        ).unique().scalars().all()
    )

    return templates.TemplateResponse(
        request,
        "opportunities/index.html",
        {
            "title": "Placements and internships",
            "live": live,
            "expired": expired,
            "experiences": experiences,
            "q": q,
            "kind": kind,
            "official": official,
            "kinds": KINDS,
        },
    )


@router.post("/new")
async def create(
    request: Request,
    db: DbDep,
    user: Verified,
    company: str = Form(...),
    role_title: str = Form(...),
    kind: str = Form("internship"),
    location: str = Form(""),
    stipend: str = Form(""),
    description: str = Form(""),
    apply_url: str = Form(""),
    deadline: str = Form(""),
    skills: str = Form(""),
):
    await verify_csrf(request)
    allowed, _ = await antiabuse.hit_rate_limit(db, f"opp:{user.id}", 10, dt.timedelta(days=1))
    if not allowed:
        raise HTTPException(status_code=429, detail="Ten listings a day is the limit.")

    due = None
    if deadline:
        try:
            due = dt.date.fromisoformat(deadline)
        except ValueError:
            due = None

    url = (apply_url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    listing = Opportunity(
        poster_id=user.id,
        company=company.strip()[:120],
        role_title=role_title.strip()[:140],
        kind=kind if kind in {k for k, _ in KINDS} else "internship",
        location=(location or "").strip()[:120],
        stipend=(stipend or "").strip()[:80],
        description=(description or "").strip()[:6000],
        apply_url=url[:500] or None,
        deadline=due,
        skills=[s.strip().lstrip("#").lower() for s in (skills or "").replace(",", " ").split()][:10],
    )
    db.add(listing)
    await db.flush()
    return RedirectResponse("/opportunities", status_code=303)


@router.post("/{opportunity_id}/verify")
async def mark_official(request: Request, db: DbDep, user: Verified, opportunity_id: int):
    """Moderators vouch for a listing. Nothing else grants the official badge."""
    await verify_csrf(request)
    if not user.is_moderator:
        raise HTTPException(status_code=403, detail="Only moderators can verify a listing.")
    listing = await db.get(Opportunity, opportunity_id)
    if listing is None:
        raise HTTPException(status_code=404)

    listing.is_official = not listing.is_official
    await mod.log_action(
        db,
        action="opportunity_verified" if listing.is_official else "opportunity_unverified",
        target_type="opportunity",
        target_id=listing.id,
        actor_id=user.id,
        subject_user_id=listing.poster_id,
        reason="Checked by moderator",
        appealable=False,
    )
    await notify.push(
        db,
        listing.poster_id,
        kind="opportunity",
        title=("Your listing was verified" if listing.is_official else "Verification removed"),
        body=f"{listing.company} — {listing.role_title}",
        link="/opportunities",
    )
    return RedirectResponse("/opportunities", status_code=303)


@router.post("/{opportunity_id}/close")
async def close(request: Request, db: DbDep, user: Verified, opportunity_id: int):
    await verify_csrf(request)
    listing = await db.get(Opportunity, opportunity_id)
    if listing is None:
        raise HTTPException(status_code=404)
    if listing.poster_id != user.id and not user.is_moderator:
        raise HTTPException(status_code=403, detail="That is not your listing.")
    listing.status = "closed"
    return RedirectResponse("/opportunities", status_code=303)


@router.get("/interviews/new")
async def interview_form(request: Request, db: DbDep, user: Verified):
    return templates.TemplateResponse(
        request, "opportunities/interview.html", {"title": "Share an interview experience"}
    )


@router.post("/interviews/new")
async def create_interview(
    request: Request,
    db: DbDep,
    user: Verified,
    company: str = Form(...),
    role_title: str = Form(...),
    verdict: str = Form("pending"),
    rounds: str = Form(""),
    body: str = Form(""),
):
    await verify_csrf(request)
    body = (body or "").strip()
    if len(body) < 80:
        raise HTTPException(
            status_code=400,
            detail="Write it out properly — what was asked, in what order, and how it went. "
            "Eighty characters is the minimum for this to help anyone.",
        )

    parsed_rounds = []
    for line in (rounds or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, detail = line.partition(":")
        parsed_rounds.append({"name": name.strip()[:80], "detail": detail.strip()[:500]})

    row = InterviewExperience(
        author_id=user.id,
        company=company.strip()[:120],
        role_title=role_title.strip()[:140],
        verdict=verdict if verdict in {"selected", "rejected", "pending"} else "pending",
        rounds=parsed_rounds[:12],
        body=body[:12000],
    )
    db.add(row)
    await db.flush()
    await rep.award(
        db,
        user.id,
        "interview_experience",
        source_type="interview",
        source_id=row.id,
        detail=f"{row.company} — {row.role_title}"[:120],
    )
    return RedirectResponse(f"/opportunities/interviews/{row.id}", status_code=303)


@router.get("/interviews/{experience_id}")
async def interview_detail(request: Request, db: DbDep, user: Reader, experience_id: int):
    row = (
        await db.execute(
            select(InterviewExperience)
            .options(joinedload(InterviewExperience.author))
            .where(InterviewExperience.id == experience_id)
        )
    ).unique().scalar_one_or_none()
    if row is None or (row.status != "active" and not user.is_moderator):
        raise HTTPException(status_code=404)

    similar = list(
        (
            await db.execute(
                select(InterviewExperience)
                .where(
                    InterviewExperience.company.ilike(row.company),
                    InterviewExperience.id != row.id,
                    InterviewExperience.status == "active",
                )
                .limit(5)
            )
        ).scalars().all()
    )
    return templates.TemplateResponse(
        request,
        "opportunities/interview_detail.html",
        {"title": f"{row.company} — {row.role_title}", "experience": row, "similar": similar},
    )
