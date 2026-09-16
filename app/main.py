"""Application entry point."""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.deps import NeedsVerification
from app.services.verification import shutdown_executor
from app.templating import templates

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("loyola")

BASE_DIR = Path(__file__).resolve().parent
SQL_DIR = BASE_DIR / "sql"


async def _apply_sql_extras() -> None:
    """Indexes and triggers that the ORM cannot express.

    Idempotent (every statement is IF NOT EXISTS / OR REPLACE) so it is safe to
    run on every boot, which keeps deployment to one step.
    """
    path = SQL_DIR / "indexes.sql"
    if not path.exists():
        return
    statements = [s.strip() for s in path.read_text(encoding="utf-8").split(";--") if s.strip()]
    # Each statement gets its own transaction. Sharing one means the first
    # failure aborts the block and every later statement fails with
    # "current transaction is aborted" — which hides the actual error and
    # silently skips every index after it.
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for stmt in statements:
            body = stmt.strip().rstrip(";").strip()
            if not body or body.startswith("--"):
                continue
            try:
                await conn.execute(text(body))
            except Exception as exc:
                first_line = body.split("\n")[0][:90]
                log.warning("index statement failed: %s -- %s", first_line, str(exc).split("\n")[0])


REQUIRED_EXTENSIONS = ("pg_trgm", "btree_gin")


async def _ensure_extensions() -> None:
    """Extensions must exist *before* create_all.

    ``users`` declares a GIN index with ``gin_trgm_ops``, so a first boot against
    a fresh database fails at table creation unless pg_trgm is already there.
    indexes.sql also creates them, but that runs too late to help.
    """
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for name in REQUIRED_EXTENSIONS:
            try:
                await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {name}"))
            except Exception as exc:
                log.warning("could not create extension %s: %s", name, str(exc).split("\n")[0])


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _ensure_extensions()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _apply_sql_extras()

    async with SessionLocal() as db:
        from app.services.bootstrap import ensure_seed_settings

        await ensure_seed_settings(db)
        await db.commit()

    scheduler = None
    if settings.enable_scheduler:
        from app.services.jobs import build_scheduler

        scheduler = build_scheduler()
        scheduler.start()
        log.info("scheduler started with %d jobs", len(scheduler.get_jobs()))

    log.info("%s ready at %s", settings.app_name, settings.base_url)
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        shutdown_executor()
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Campus social network for "
        f"{settings.campus_name}. The `/api/v1` tree is the contract the Android "
        "app is built against; every endpoint takes the same session token the "
        "web app stores in a cookie, presented as `Authorization: Bearer <token>`."
    ),
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# --- CORS -------------------------------------------------------------------
# The Android app is a native client and sends no Origin, so this exists for the
# browser-based tooling around the API: the docs page, a future PWA, and local
# development against a dev server on another port.
CORS_ORIGIN_RE = (
    r"(https://([a-z0-9-]+\.)*loyola\.[a-z.]+|http://localhost:\d+|http://127\.0\.0\.1:\d+)"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ORIGIN_RE,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Requested-With"],
)


def _cors_headers(request: Request) -> dict[str, str]:
    """Error responses bypass the CORS middleware, so they re-add the headers.

    Without this a 403 from the API reaches a browser client as an opaque
    network error instead of the message we carefully wrote.
    """
    origin = request.headers.get("origin", "")
    if origin and re.fullmatch(CORS_ORIGIN_RE, origin):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=(self)")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    return response


def wants_json(request: Request) -> bool:
    """API clients get JSON errors; the browser gets a page or a redirect."""
    return request.url.path.startswith("/api/")


@app.exception_handler(NeedsVerification)
async def needs_verification_handler(request: Request, exc: NeedsVerification):
    """The hard wall. Tier 0 sees nothing; Tier 1 may read but not write."""
    if wants_json(request):
        return JSONResponse(
            {
                "detail": "Verify your student ID to continue.",
                "code": "verification_required",
                "tier": exc.tier,
            },
            status_code=403,
            headers=_cors_headers(request),
        )
    if request.headers.get("HX-Request"):
        response = JSONResponse({"error": "verification required"}, status_code=403)
        response.headers["HX-Redirect"] = "/verify"
        return response
    return RedirectResponse("/verify", status_code=303)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if wants_json(request):
        return JSONResponse(
            {"detail": getattr(exc, "detail", "Not found."), "code": "not_found"},
            status_code=404,
            headers=_cors_headers(request),
        )
    return templates.TemplateResponse(
        request, "errors/404.html", {"title": "Not found"}, status_code=404
    )


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    detail = getattr(exc, "detail", "You do not have access to that.")
    if wants_json(request):
        return JSONResponse(
            {"detail": detail, "code": "forbidden"}, status_code=403, headers=_cors_headers(request)
        )
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'<div class="flash flash-error">{detail}</div>', status_code=403)
    return templates.TemplateResponse(
        request, "errors/403.html", {"title": "Not allowed", "detail": detail}, status_code=403
    )


@app.exception_handler(401)
async def unauthorized(request: Request, exc):
    if wants_json(request):
        return JSONResponse(
            {"detail": getattr(exc, "detail", "Sign in to continue."), "code": "unauthenticated"},
            status_code=401,
            headers=_cors_headers(request),
        )
    if request.headers.get("HX-Request"):
        response = HTMLResponse("", status_code=401)
        response.headers["HX-Redirect"] = "/login"
        return response
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    from app.ocr import tesseract_available

    ok, version = tesseract_available()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        db_ok = True
    except Exception:
        db_ok = False

    # Only the database decides whether this process should take traffic. A
    # missing tesseract stops new students verifying by card — which the manual
    # path covers — but everyone already inside keeps working, so pulling the
    # app out of rotation for it would turn a partial outage into a total one.
    return JSONResponse(
        {
            "status": "ok" if (db_ok and ok) else "degraded" if db_ok else "down",
            "db": db_ok,
            "tesseract": version if ok else None,
        },
        status_code=200 if db_ok else 503,
    )


# --- routers ----------------------------------------------------------------
# The JSON API goes first: its paths are namespaced under /api/v1 and must never
# be shadowed by the HTML routers' catch-alls.
from app.api import api_router  # noqa: E402

app.include_router(api_router)

# Order matters only for `pages`, which owns catch-all informational routes and
# is therefore registered last.
from importlib import import_module  # noqa: E402

ROUTER_MODULES = [
    "auth",
    "verify",
    "feed",
    "profiles",
    "qa",
    "groups",
    "events",
    "projects",
    "vault",
    "mentors",
    "lostfound",
    "opportunities",
    "studyrooms",
    "search",
    "notifications",
    "moderation",
    "admin",
    "pages",
]

for name in ROUTER_MODULES:
    module = import_module(f"app.routers.{name}")
    app.include_router(module.router)
