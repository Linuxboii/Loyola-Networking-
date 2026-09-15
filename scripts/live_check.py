"""Exercise the deployed site over real HTTPS, through Cloudflare and nginx.

The in-process smoke test cannot catch proxy body limits, proxy read timeouts,
cookie Secure flags or TLS — all of which sit between a student's phone and the
OCR pipeline. This one goes the whole way round.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://loyola.avlokai.com"
CARD = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/loyola-cards/card_phone.jpg")

FAILURES: list[str] = []
COUNT = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global COUNT
    COUNT += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{'' if ok else '  ' + detail}")
    if not ok:
        FAILURES.append(label)


def csrf(client: httpx.Client, path: str) -> str:
    text = client.get(path).text
    marker = 'name="csrf" content="'
    i = text.find(marker)
    if i == -1:
        return ""
    start = i + len(marker)
    return text[start : text.index('"', start)]


def main() -> int:
    handle = "livecheck" + secrets.token_hex(3)
    password = "live-check-" + secrets.token_hex(4)

    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=True) as c:
        r = c.get("/healthz")
        check(r.status_code == 200 and r.json().get("db"), "healthz over HTTPS", r.text[:120])

        token = csrf(c, "/signup")
        check(bool(token), "CSRF token present in the signup page")

        r = c.post(
            "/signup",
            data={
                "csrf": token,
                "handle": handle,
                "full_name": "Ananya Rajesh Kumar",
                "password": password,
                "password2": password,
                "consent": "yes",
            },
        )
        check(r.status_code == 200 and "/verify" in str(r.url), "signup over HTTPS", str(r.url))
        check(
            "loyola_session" in c.cookies,
            "session cookie survives the HTTPS round trip",
            str(dict(c.cookies)),
        )

        if not CARD.exists():
            check(False, "test card present", str(CARD))
            return 1

        token = csrf(c, "/verify")
        r = c.post(
            "/verify/card",
            data={"csrf": token, "live_capture": "1"},
            files={"card": ("card.jpg", CARD.read_bytes(), "image/jpeg")},
        )
        check(
            r.status_code == 200 and "/verify/confirm" in str(r.url),
            "ID card upload + OCR through nginx and Cloudflare",
            f"{r.status_code} {str(r.url)}",
        )
        body = r.text
        check("21BSC1042" in body, "roll number read correctly end to end")
        check("Ananya Rajesh Kumar" in body, "name read correctly end to end")

        token = csrf(c, "/verify/confirm")
        r = c.post(
            "/verify/selfie",
            data={"csrf": token},
            files={"selfie": ("selfie.jpg", CARD.read_bytes(), "image/jpeg")},
        )
        check(r.status_code == 200, "selfie upload", str(r.status_code))
        check("reviewer" in r.text.lower() or "review" in r.text.lower(), "lands on the review-pending page")

        # Tier 1 may read, must not write.
        r = c.get("/")
        check(r.status_code == 200 and "Campus feed" in r.text, "Tier 1 can read the feed")
        r = c.get("/qa")
        check(r.status_code == 200, "Tier 1 can read questions")

        r = c.post("/posts", data={"csrf": csrf(c, "/"), "kind": "text", "body": "should not post"})
        check("/verify" in str(r.url) or r.status_code == 403, "Tier 1 still cannot post", str(r.url))

        # Security headers set by the app middleware must survive the proxy.
        r = c.get("/login")
        headers = {k.lower() for k in r.headers}
        for h in ("content-security-policy", "x-frame-options", "x-content-type-options"):
            check(h in headers, f"{h} header present")

    print(f"\n{COUNT - len(FAILURES)}/{COUNT} live checks passed")
    print(f"test account: {handle}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
