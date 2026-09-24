"""End-to-end smoke test for the /api/v1 tree.

Runs against a live server. It creates throwaway accounts (handle prefix
``smoke_``), promotes them past the verification wall directly in the database —
the wall is exercised separately, before promotion — then drives every endpoint
the Android app calls and asserts the shapes it relies on.

    python scripts/api_smoke.py [--base http://127.0.0.1:8011]

Clean up afterwards with:

    python scripts/manage.py purge-test --prefix smoke_
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, update  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import RateLimit, User  # noqa: E402

PASSWORD = "smoketest1234"
FAILURES: list[str] = []
CHECKS = 0


class ApiError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body


def call(
    base: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict | None = None,
    expect: int | tuple[int, ...] = (200, 201, 204),
) -> dict | list | None:
    url = f"{base}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    expected = (expect,) if isinstance(expect, int) else expect
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, body = response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode()

    if status not in expected:
        raise ApiError(status, body)
    return json.loads(body) if body.strip() else None


def check(label: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


async def clear_signup_limits() -> None:
    """Drop this machine's signup/login throttle buckets.

    The limiter is doing its job — five accounts per address per six hours —
    which would otherwise make the second run of this script fail for the right
    reason at the wrong time.
    """
    from sqlalchemy import delete, or_

    async with SessionLocal() as db:
        await db.execute(
            delete(RateLimit).where(
                or_(
                    RateLimit.bucket.like("signup:%"),
                    RateLimit.bucket.like("login:%"),
                )
            )
        )
        await db.commit()
    # Each helper gets its own event loop via asyncio.run, and the engine's
    # pooled connections belong to the loop that opened them. Disposing here is
    # what keeps the next asyncio.run from inheriting dead sockets.
    await engine.dispose()


async def promote(handles: list[str], tier: int = 2, rep: float = 120.0, rep_tier: str = "Established") -> None:
    """Skip the ID-card wall for the accounts under test."""
    async with SessionLocal() as db:
        await db.execute(
            update(User)
            .where(User.handle.in_(handles))
            .values(tier=tier, rep_total=rep, rep_tier=rep_tier, roll_number=None)
        )
        await db.commit()
    await engine.dispose()


async def read_handle_ids(handles: list[str]) -> dict[str, int]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(User.handle, User.id).where(User.handle.in_(handles)))).all()
    return {h: i for h, i in rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    api = f"{base}/api/v1"

    suffix = secrets.token_hex(3)
    alice_handle = f"smoke_a_{suffix}"
    bob_handle = f"smoke_b_{suffix}"

    asyncio.run(clear_signup_limits())

    print("\n[1] unauthenticated config")
    config = call(api, "GET", "/config")
    check("config returns api_version", isinstance(config.get("api_version"), int))
    check("config lists post types", bool(config.get("post_types")))
    check("config reports signed-out", config.get("authenticated") is False)

    print("\n[2] signup")
    alice = call(
        api,
        "POST",
        "/auth/signup",
        payload={"handle": alice_handle, "full_name": "Alice Smoke", "password": PASSWORD},
        expect=201,
    )
    bob = call(
        api,
        "POST",
        "/auth/signup",
        payload={"handle": bob_handle, "full_name": "Bob Smoke", "password": PASSWORD},
        expect=201,
    )
    check("signup returns a token", bool(alice.get("token")))
    check("new account starts at tier 0", alice["user"]["tier"] == 0)
    a_token, b_token = alice["token"], bob["token"]

    print("\n[3] the verification wall holds before promotion")
    try:
        call(api, "GET", "/feed", token=a_token, expect=200)
        check("tier 0 blocked from the feed", False, "feed returned 200")
    except ApiError as exc:
        body = json.loads(exc.body or "{}")
        check("tier 0 blocked from the feed", exc.status == 403)
        check("block carries a machine-readable code", body.get("code") == "verification_required")

    print("\n[4] duplicate handle and bad password are rejected")
    try:
        call(
            api,
            "POST",
            "/auth/signup",
            payload={"handle": alice_handle, "full_name": "Clash", "password": PASSWORD},
            expect=201,
        )
        check("duplicate handle rejected", False)
    except ApiError as exc:
        check("duplicate handle rejected", exc.status == 409)
    try:
        call(api, "POST", "/auth/login", payload={"identifier": alice_handle, "password": "wrong"})
        check("bad password rejected", False)
    except ApiError as exc:
        check("bad password rejected", exc.status == 401)

    asyncio.run(promote([alice_handle, bob_handle]))

    print("\n[5] login and identity")
    relogin = call(api, "POST", "/auth/login", payload={"identifier": alice_handle, "password": PASSWORD})
    a_token = relogin["token"]
    me = call(api, "GET", "/auth/me", token=a_token)
    check("me reflects the promoted tier", me["tier"] == 2)
    check("me exposes capabilities", isinstance(me.get("capabilities"), dict) and me["capabilities"])
    check("me exposes reputation pillars", len(me.get("rep_pillars") or {}) == 5)

    print("\n[6] posting and the feed")
    post = call(
        api,
        "POST",
        "/posts",
        token=a_token,
        payload={
            "kind": "text",
            "title": "Smoke test post",
            "body": "Does the API hold together end to end?",
            "tags": ["#SmokeTest", "api"],
        },
        expect=201,
    )
    check("post echoes its author", post["author"]["handle"] == alice_handle)
    check("tags are normalised", post["tags"] == ["smoketest", "api"])
    post_id = post["id"]

    # Android build 2 sent ``image`` after a successful /media upload. The
    # server accepts this legacy payload and stores the canonical media kind.
    legacy_image_post = call(
        api, "POST", "/posts", token=a_token,
        payload={
            "kind": "image",
            "body": "Legacy Android photo-post contract.",
            "media": ["posts/smoke-legacy-image.jpg"],
        },
        expect=201,
    )
    check("legacy Android image posts normalize to media", legacy_image_post["kind"] == "media")

    feed = call(api, "GET", "/feed?mode=latest", token=b_token)
    check("post appears in the feed", any(p["id"] == post_id for p in feed["posts"]))
    foryou = call(api, "GET", "/feed?mode=foryou", token=b_token)
    check("for-you ranking returns posts", isinstance(foryou["posts"], list))

    print("\n[7] comments, votes and self-vote refusal")
    comment = call(
        api, "POST", f"/posts/{post_id}/comments", token=b_token, payload={"body": "Looks alive."}, expect=201
    )
    check("comment records its author", comment["author"]["handle"] == bob_handle)

    vote = call(api, "POST", "/vote", token=b_token, payload={"target_type": "post", "target_id": post_id, "value": 1})
    check("upvote raises the score", vote["score"] >= 1, str(vote))
    check("upvote is reflected back", vote["my_vote"] == 1)
    try:
        call(api, "POST", "/vote", token=a_token, payload={"target_type": "post", "target_id": post_id, "value": 1})
        check("self-vote refused", False)
    except ApiError as exc:
        check("self-vote refused", exc.status == 400)

    detail = call(api, "GET", f"/posts/{post_id}", token=b_token)
    check("detail carries my vote", detail["post"]["my_vote"] == 1)
    check("detail carries the comment", any(c["id"] == comment["id"] for c in detail["comments"]))

    print("\n[8] questions and answers")
    question = call(
        api,
        "POST",
        "/qa/questions",
        token=a_token,
        payload={
            "title": "How does the smoke test reach the answer path?",
            "body": "Asking so the answer endpoint has something to attach to.",
            "tags": ["exams"],
            "subject": "CS",
        },
        expect=201,
    )
    answer = call(
        api,
        "POST",
        f"/qa/questions/{question['id']}/answers",
        token=b_token,
        payload={"body": "By posting this answer, which is long enough to pass validation."},
        expect=201,
    )
    accepted = call(api, "POST", f"/qa/questions/{question['id']}/accept/{answer['id']}", token=a_token)
    check("answer can be accepted", accepted["accepted_answer_id"] == answer["id"])
    qdetail = call(api, "GET", f"/qa/questions/{question['id']}", token=b_token)
    check("accepted answer is marked", qdetail["answers"][0]["is_accepted"] is True)

    print("\n[9] anonymous questions hide the asker")
    anon = call(
        api,
        "POST",
        "/qa/questions",
        token=a_token,
        payload={
            "title": "Can the doubt box really stay anonymous?",
            "body": "This one is posted anonymously to check the serializer.",
            "is_anonymous": True,
        },
        expect=201,
    )
    check("anonymous question hides the handle", anon["author"]["handle"] is None)
    check("anonymous question is labelled", anon["author"]["anonymous"] is True)

    print("\n[10] groups")
    group = call(
        api,
        "POST",
        "/groups",
        token=a_token,
        payload={"name": f"Smoke Club {suffix}", "description": "A club for smoke tests.", "kind": "interest"},
        expect=201,
    )
    joined = call(api, "POST", f"/groups/{group['slug']}/join", token=b_token)
    check("joining a group counts the member", joined["joined"] is True and joined["member_count"] == 2)
    gdetail = call(api, "GET", f"/groups/{group['slug']}", token=b_token)
    check("group detail lists members", len(gdetail["members"]) == 2)

    print("\n[11] notifications reached the post author")
    notes = call(api, "GET", "/notifications", token=a_token)
    check("comment produced a notification", any(n["kind"] == "comment" for n in notes["notifications"]))
    unread = call(api, "GET", "/notifications/unread-count", token=a_token)
    check("unread count is positive", unread["unread"] >= 1)
    call(api, "POST", "/notifications/read", token=a_token, expect=204)
    check("mark-all-read clears the count", call(api, "GET", "/notifications/unread-count", token=a_token)["unread"] == 0)

    print("\n[12] profiles, skills and search")
    updated = call(
        api,
        "PATCH",
        "/me",
        token=a_token,
        payload={"bio": "Smoke tester.", "interests": ["#Robotics", "api"], "links": {"site": "https://example.org", "bad": "javascript:alert(1)"}},
    )
    check("bio saved", updated["bio"] == "Smoke tester.")
    check("interests normalised", updated["interests"] == ["robotics", "api"])
    check("unsafe link dropped", "bad" not in updated["links"])

    call(api, "POST", "/me/skills?name=Flutter", token=a_token, expect=201)
    profile = call(api, "GET", f"/people/{alice_handle}", token=b_token)
    check("skill appears on the profile", any(s["name"].lower() == "flutter" for s in profile["skills"]))
    check("profile counts posts", profile["counts"]["posts"] >= 1)

    people = call(api, "GET", "/people?q=Smoke", token=b_token)
    check("directory finds the account", any(p["handle"] == alice_handle for p in people["people"]))
    search = call(api, "GET", "/search?q=smoke", token=b_token)
    check("search returns every bucket", {"people", "questions", "resources", "projects"} <= set(search))

    print("\n[13] home chrome and reputation ledger")
    home = call(api, "GET", "/home", token=a_token)
    check("home returns campus stats", "verified_members" in home["stats"])
    check("home returns trending tags", isinstance(home["trending_tags"], list))
    ledger = call(api, "GET", "/me/reputation", token=b_token)
    check("ledger reports a tier", bool(ledger["tier"]))
    check("answering earned reputation", any(e["reason"] for e in ledger["events"]), str(ledger["events"])[:200])

    print("\n[14] verification status for an unverified account")
    charlie_handle = f"smoke_c_{suffix}"
    charlie = call(
        api,
        "POST",
        "/auth/signup",
        payload={"handle": charlie_handle, "full_name": "Charlie Smoke", "password": PASSWORD},
        expect=201,
    )
    status = call(api, "GET", "/verify/status", token=charlie["token"])
    check("verification tells the app what to do next", status["next_step"] == "capture_card")
    check("verification reports the attempt budget", status["attempts_allowed"] >= 1)

    print("\n[15] moderation: reporting content")
    report = call(
        api,
        "POST",
        "/reports",
        token=b_token,
        payload={"target_type": "post", "target_id": post_id, "reason": "spam", "detail": "smoke test report"},
        expect=201,
    )
    check("report accepted", report["status"] == "received")
    try:
        call(
            api,
            "POST",
            "/reports",
            token=b_token,
            payload={"target_type": "post", "target_id": post_id, "reason": "spam"},
            expect=201,
        )
        check("duplicate report refused", False)
    except ApiError as exc:
        check("duplicate report refused", exc.status == 409)

    print("\n[16] deletion and logout")
    call(api, "DELETE", f"/posts/{post_id}", token=a_token, expect=204)
    try:
        call(api, "GET", f"/posts/{post_id}", token=b_token)
        check("deleted post is gone", False)
    except ApiError as exc:
        check("deleted post is gone", exc.status == 404)

    call(api, "POST", "/auth/logout", token=a_token, expect=204)
    try:
        call(api, "GET", "/auth/me", token=a_token)
        check("revoked token stops working", False)
    except ApiError as exc:
        check("revoked token stops working", exc.status == 401)

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("API smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
