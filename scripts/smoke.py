"""End-to-end smoke test.

Drives the real app through the real database: sign up, read an ID card through
the real OCR pipeline, approve it, then exercise every GET route and the main
write paths. Run it against a scratch database, never production data.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from httpx import ASGITransport, AsyncClient  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(f"{label} {detail}")


async def csrf(client: AsyncClient, path: str = "/") -> str:
    r = await client.get(path)
    text = r.text
    marker = 'name="csrf" content="'
    i = text.find(marker)
    if i == -1:
        marker = 'name="csrf" value="'
        i = text.find(marker)
    if i == -1:
        return ""
    start = i + len(marker)
    return text[start : text.index('"', start)]


async def main() -> int:
    from app.main import app

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)

        # ---------------------------------------------------------- signup
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=False
        ) as c:
            token = await csrf(c, "/signup")
            r = await c.post(
                "/signup",
                data={
                    "csrf": token,
                    "handle": "smoketest",
                    "full_name": "Ananya Rajesh Kumar",
                    "password": "smoke-test-1234",
                    "password2": "smoke-test-1234",
                    "consent": "yes",
                },
            )
            check(r.status_code == 303, "POST /signup", f"got {r.status_code} {r.text[:200]}")

            # Tier 0 must see nothing at all.
            r = await c.get("/", follow_redirects=False)
            check(
                r.status_code == 303 and "/verify" in r.headers.get("location", ""),
                "Tier 0 hard wall on /",
                f"got {r.status_code} -> {r.headers.get('location')}",
            )

            r = await c.get("/verify")
            check(r.status_code == 200, "GET /verify", str(r.status_code))

            # -------------------------------------------------- ID card OCR
            card = Path("/tmp/loyola-cards/card_clean.jpg")
            if not card.exists():
                print("  !!    no test card at /tmp/loyola-cards — run make_test_card.py")
                return 2

            token = await csrf(c, "/verify")
            r = await c.post(
                "/verify/card",
                data={"csrf": token, "live_capture": "1"},
                files={"card": ("card.jpg", card.read_bytes(), "image/jpeg")},
                timeout=120,
            )
            check(
                r.status_code == 303,
                "POST /verify/card (real OCR)",
                f"got {r.status_code} {r.text[:300]}",
            )

            r = await c.get("/verify/confirm")
            check(r.status_code == 200, "GET /verify/confirm", str(r.status_code))
            check("21BSC1042" in r.text, "OCR extracted the roll number into the UI")
            check("Ananya Rajesh Kumar" in r.text, "OCR extracted the name into the UI")

            token = await csrf(c, "/verify/confirm")
            r = await c.post(
                "/verify/selfie",
                data={"csrf": token},
                files={"selfie": ("selfie.jpg", card.read_bytes(), "image/jpeg")},
            )
            check(r.status_code == 303, "POST /verify/selfie", str(r.status_code))

            # Tier 1: may read, must not write.
            r = await c.get("/", follow_redirects=False)
            check(r.status_code == 200, "Tier 1 can read the feed", str(r.status_code))

            token = await csrf(c, "/")
            r = await c.post("/posts", data={"csrf": token, "kind": "text", "body": "should fail"})
            check(
                r.status_code in (303, 403) and "/verify" in r.headers.get("location", "/verify"),
                "Tier 1 cannot post",
                f"got {r.status_code}",
            )

        # ------------------------------------------------- approve + promote
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import User
        from app.services import verification

        async with SessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.handle == "smoketest"))
            ).scalar_one()
            record = await verification.latest_record(db, user.id)
            check(record is not None and record.status == "in_review", "record is in review")
            record_id = record.id
            await verification.approve(db, record, None, "smoke test")
            user.roles = ["admin", "moderator"]
            user.rep_total = 5000
            user.rep_tier = "Pillar"
            await db.commit()

        # ------------------------------------------------------- full sweep
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=True
        ) as c:
            token = await csrf(c, "/login")
            r = await c.post(
                "/login",
                data={"csrf": token, "identifier": "smoketest", "password": "smoke-test-1234"},
            )
            check(r.status_code == 200, "POST /login", str(r.status_code))

            pages = [
                "/", "/?mode=foryou", "/?kind=notice",
                "/qa", "/qa/ask", "/qa?filter=unanswered", "/qa/archive/IV",
                "/people", "/people?skill=python", "/u/smoketest",
                "/me/edit", "/me/reputation",
                "/groups", "/groups/new",
                "/events", "/events/new",
                "/projects", "/projects/new",
                "/vault", "/vault/upload",
                "/mentors", "/lost-found", "/opportunities",
                "/opportunities/interviews/new",
                "/study-rooms", "/search", "/search?q=test",
                "/notifications", "/directory", "/digest", "/wrapped",
                "/transparency", "/about", "/rules", "/privacy", "/support",
                "/settings/account",
                "/moderation", "/moderation/audit", "/moderation/my-reports",
                "/moderation/my-appeals",
                "/admin", "/admin/verifications", "/admin/people", "/admin/ocr",
                "/admin/lists", "/admin/devices", "/admin/privacy-log",
                f"/admin/verifications/{record_id}",
                "/healthz",
            ]
            for path in pages:
                r = await c.get(path)
                check(r.status_code == 200, f"GET {path}", f"got {r.status_code} {r.text[:160]}")

            # ------------------------------------------------- write paths
            token = await csrf(c, "/")
            r = await c.post(
                "/posts",
                data={
                    "csrf": token,
                    "kind": "text",
                    "body": "Smoke test post about #dbms and placements.",
                    "tags": "dbms placements",
                },
            )
            check(r.status_code == 200, "POST /posts", str(r.status_code))

            r = await c.post(
                "/qa/ask",
                data={
                    "csrf": token,
                    "title": "How do I connect to the lab Postgres box?",
                    "body": "It refuses my password every single time and I have tried twice.",
                    "subject": "DBMS",
                    "semester": "IV",
                    "tags": "postgres lab",
                },
            )
            check(r.status_code == 200, "POST /qa/ask", str(r.status_code))
            check("lab Postgres" in r.text, "question renders after posting")

            r = await c.post(
                "/qa/ask",
                data={
                    "csrf": token,
                    "title": "Anonymous doubt about pointers in C",
                    "body": "I do not understand double pointers at all and feel stupid asking.",
                    "anonymous": "on",
                },
            )
            check(r.status_code == 200, "POST /qa/ask anonymous", str(r.status_code))
            check("Asked anonymously" in r.text, "anonymous question hides the author")

            # Self-voting must be impossible.
            from app.models import Question

            async with SessionLocal() as db:
                q = (
                    await db.execute(select(Question).order_by(Question.id.desc()).limit(1))
                ).scalar_one()
                qid = q.id
            r = await c.post(f"/vote/question/{qid}", data={"csrf": token, "value": "1"})
            check(r.status_code == 400, "self-vote is refused", f"got {r.status_code}")

            r = await c.post(
                "/vault/upload",
                data={
                    "csrf": token,
                    "title": "Schaum's Outline full textbook",
                    "attest": "yes",
                    "kind": "notes",
                },
                files={"document": ("book.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
            check(r.status_code == 400, "copyright guardrail blocks a textbook upload", str(r.status_code))

            r = await c.post(
                "/vault/upload",
                data={"csrf": token, "title": "My DBMS unit 3 notes", "attest": "yes", "kind": "notes"},
                files={"document": ("notes.pdf", b"%PDF-1.4 fake notes", "application/pdf")},
            )
            check(r.status_code == 200, "POST /vault/upload (own notes)", str(r.status_code))

            r = await c.post("/vault/upload", data={"csrf": token, "title": "No attestation"},
                             files={"document": ("n.pdf", b"%PDF-1.4 x", "application/pdf")})
            check(r.status_code == 400, "upload without attestation is refused", str(r.status_code))

            # CSRF must actually be enforced.
            r = await c.post("/posts", data={"csrf": "wrong", "kind": "text", "body": "nope"})
            check(r.status_code == 403, "bad CSRF token is rejected", f"got {r.status_code}")

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
