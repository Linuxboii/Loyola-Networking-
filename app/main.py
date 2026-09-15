"""Application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
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


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.debug else None,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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


@app.exception_handler(NeedsVerification)
async def needs_verification_handler(request: Request, exc: NeedsVerification):
    """The hard wall. Tier 0 sees nothing; Tier 1 may read but not write."""
    if request.headers.get("HX-Request"):
        response = JSONResponse({"error": "verification required"}, status_code=403)
        response.headers["HX-Redirect"] = "/verify"
        return response
    return RedirectResponse("/verify", status_code=303)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        request, "errors/404.html", {"title": "Not found"}, status_code=404
    )


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    detail = getattr(exc, "detail", "You do not have access to that.")
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'<div class="flash flash-error">{detail}</div>', status_code=403)
    return templates.TemplateResponse(
        request, "errors/403.html", {"title": "Not allowed", "detail": detail}, status_code=403
    )


@app.exception_handler(401)
async def unauthorized(request: Request, exc):
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
    healthy = db_ok and ok
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "db": db_ok, "tesseract": version if ok else None},
        status_code=200 if healthy else 503,
    )


# --- routers ----------------------------------------------------------------
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
