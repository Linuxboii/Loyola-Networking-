"""Jinja environment: filters and globals every template can rely on."""
from __future__ import annotations

import datetime as dt
import html
import re
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.config import settings
from app.deps import CAPABILITIES, can
from app.models import PILLAR_LABELS, REP_TIERS
from app.security import csrf_for, sign_media
from app.services.reputation import next_tier, tier_index

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# --- filters ----------------------------------------------------------------


def timeago(value: dt.datetime | None) -> str:
    if not value:
        return ""
    now = dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    seconds = (now - value).total_seconds()
    if seconds < 0:
        seconds = abs(seconds)
        suffix = "from now"
    else:
        suffix = "ago"
    for limit, div, unit in (
        (60, 1, "s"),
        (3600, 60, "m"),
        (86400, 3600, "h"),
        (604800, 86400, "d"),
        (2629800, 604800, "w"),
        (31557600, 2629800, "mo"),
    ):
        if seconds < limit:
            n = int(seconds // div)
            return f"{max(n, 1)}{unit} {suffix}"
    return f"{int(seconds // 31557600)}y {suffix}"


def datefmt(value: dt.datetime | dt.date | None, fmt: str = "%d %b %Y") -> str:
    if not value:
        return ""
    return value.strftime(fmt)


def timefmt(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d %b, %I:%M %p").replace(" 0", " ")


_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")
_MENTION_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{3,40})")
_TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z0-9_\-]{2,40})")


def richtext(value: str | None) -> Markup:
    """Escape first, then linkify. Never the other way round."""
    if not value:
        return Markup("")
    safe = html.escape(str(value))
    safe = _URL_RE.sub(
        lambda m: f'<a href="{m.group(1)}" rel="noopener nofollow ugc" target="_blank">{m.group(1)}</a>',
        safe,
    )
    safe = _MENTION_RE.sub(lambda m: f'<a href="/u/{m.group(1)}">@{m.group(1)}</a>', safe)
    safe = _TAG_RE.sub(lambda m: f'<a href="/?tag={m.group(1)}">#{m.group(1)}</a>', safe)
    return Markup(safe.replace("\n", "<br>"))


def excerpt(value: str | None, length: int = 180) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def initials(name: str | None) -> str:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def pluralise(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def media_url(path: str | None, fallback: str = "") -> str:
    if not path:
        return fallback
    return f"/media/{sign_media(path)}"


def tier_colour(tier: str) -> str:
    return {
        "Newcomer": "tier-newcomer",
        "Contributor": "tier-contributor",
        "Established": "tier-established",
        "Trusted": "tier-trusted",
        "Pillar": "tier-pillar",
    }.get(tier, "tier-newcomer")


def tier_progress(user) -> dict[str, Any]:
    nxt = next_tier(user.rep_total or 0)
    if not nxt:
        return {"label": None, "percent": 100, "remaining": 0}
    label, threshold = nxt
    floor = 0.0
    for name, value in REP_TIERS:
        if (user.rep_total or 0) >= value:
            floor = float(value)
    span = max(1.0, threshold - floor)
    done = max(0.0, (user.rep_total or 0) - floor)
    return {
        "label": label,
        "percent": int(min(100, round(100 * done / span))),
        "remaining": int(max(0, round(threshold - (user.rep_total or 0)))),
    }


templates.env.filters.update(
    {
        "timeago": timeago,
        "datefmt": datefmt,
        "timefmt": timefmt,
        "richtext": richtext,
        "excerpt": excerpt,
        "initials": initials,
        "pluralise": pluralise,
        "media_url": media_url,
    }
)

def asset(path: str) -> str:
    """Cache-bust a static file by its own modification time.

    nginx serves /static with a week of caching, which is right for the bytes
    and wrong for a redeploy: without this, a stylesheet change is invisible to
    everyone who visited yesterday until they hard-refresh.
    """
    rel = path.lstrip("/").removeprefix("static/")
    try:
        stamp = int((BASE_DIR / "static" / rel).stat().st_mtime)
    except OSError:
        return path
    return f"{path}?v={stamp}"


templates.env.globals.update(
    {
        "asset": asset,
        "APP_NAME": settings.app_name,
        "CAMPUS": settings.campus_name,
        "PILLAR_LABELS": PILLAR_LABELS,
        "REP_TIERS": REP_TIERS,
        "CAPABILITIES": CAPABILITIES,
        "GRIEVANCE_OFFICER": settings.grievance_officer,
        "GRIEVANCE_EMAIL": settings.grievance_email,
        "COUNSELLOR": settings.counsellor_contact,
        "ID_RETENTION_DAYS": settings.id_image_retention_days,
        "VERIFY_ATTEMPTS": settings.verification_attempts_per_week,
        "can": can,
        "csrf_token": lambda request: csrf_for(request.cookies.get(settings.session_cookie) or ""),
        "tier_index": tier_index,
        "tier_colour": tier_colour,
        "tier_progress": tier_progress,
        "csrf_for": csrf_for,
        "now": lambda: dt.datetime.now(dt.timezone.utc),
    }
)
