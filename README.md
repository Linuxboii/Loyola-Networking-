# Loyola Networking

A closed, identity-verified social network for Loyola Academy, built from
`loyola-networking-prd.pdf`. Live at **https://loyola.avlokai.com**.

Entry is gated on a college ID card. Inside, members post, ask and answer
academic questions, form groups, run events, find project teams, share notes and
mentor juniors — and earn a reputation that comes from contributing rather than
from being liked.

There are two clients against one backend: the server-rendered web app, and a
native **Android app** in `mobile/`. Both authenticate with the same opaque
session token — the browser keeps it in an httponly cookie, the app sends it as
`Authorization: Bearer`. One sessions table, one revocation path, one expiry
rule. The app exists mainly because verification is a camera problem: the whole
network is gated on photographing an ID card, and that is the one thing a phone
does better than a laptop.

---

## How it differs from the PRD

Three things were settled differently, all deliberately:

**No phone or email verification.** The PRD anchors accounts on a phone number
with an OTP. That was ruled out, so the roll number extracted from the ID card
became the anchor instead, with a database-level unique constraint enforcing one
account per roll number. Login is roll number or username, plus a password.
Password recovery is an admin action after an in-person ID check — there is no
verified address to send a reset link to, and pretending otherwise would be
worse than admitting it.

**The ID card OCR is built here, not bought.** No Google Vision, no Textract.
`app/ocr/` is an OpenCV + Tesseract pipeline written for this: card detection and
perspective correction, orientation settling, several binarisations tried in
order, label-anchored field extraction, and scored enumeration of ambiguous
character readings. See [The card reader](#the-card-reader).

**Face matching is a human check.** The PRD asks for automated face match plus a
liveness prompt. On one vCPU with 1.9 GB of RAM, that means a model, a few
hundred MB of peak memory and seconds of latency per signup — to reach a
decision a person makes in three seconds from the same two images. The selfie is
still captured, encrypted and shown to the reviewer beside the portrait cropped
off the card. The check happens; a human makes it.

**No faculty module.** Per instruction, and because no college cooperation was
available: no faculty accounts, no "verified by faculty" marker, no course
feedback module, no enrolment CSV cross-check, and no official notice channel
for an admin office. Tier 3 is student club office-bearers and moderators,
granted by an admin in the console.

---

## The card reader

`app/ocr/` — three modules, no network calls, no model downloads.

| Stage | What happens |
|---|---|
| Quality gate | Laplacian blur score, brightness and a localised-hotspot glare measure, all taken on the flattened card rather than the whole frame — measured across the desk as well, a plain grey background dragged brightness down and made every white card look like it was covered in reflections. Unusable frames are rejected with advice rather than a failure. |
| Card detection | Canny + contour search for the card quadrilateral, then a perspective warp to flatten it. Rejects quads that are essentially the whole frame — that is the photo's border, not the card. |
| Orientation | Tesseract OSD on a downscaled copy, falling back to a horizontal-run-length heuristic when OSD declines (it often does on sparse cards). |
| Binarisation | CLAHE, adaptive, Otsu and an inverted pass, tried in order and built lazily. The inverted pass is what reads white-on-navy institution banners, which every dark-text threshold erases. |
| Issuer banner | Read from the uncropped frame at two resolutions, then from the cropped card. Resolution is what decides it: a 640px band is plenty when the card fills the frame and illegible on a phone snapshot where it does not. A missed banner costs a 45% confidence penalty *and* defeats the grid's early exit, so one unreadable header used to turn a five-second read into an eighteen-second one that still went to manual review. |
| Extraction | Label-anchored first (`UID NO:`, `Roll No:`, `Admission No:`, fuzzy-matched), pattern scan second, positional heuristics last. Each field carries its own confidence and the method that produced it. |
| Disambiguation | Tesseract reads `21BSC1042` as `ZIBSCIO4Z`. The reader enumerates substitutions across ambiguous positions and scores each candidate against known roll-number shapes, penalising each flip. A long all-digit run (the Loyola UID is twelve digits) skips enumeration — flipping digits into letters there can only invent a worse reading. |

Measured on the synthetic fixtures: **10/10 correct across clean,
perspective-warped, phone-noise, 90°-rotated, glare and dim captures, a second
generic layout with a slashed roll number, and the Loyola specimen layout.
5–10 seconds per card**, every one of them clearing the 0.72 autopass
threshold on its own.

### The Loyola card

The defaults are now cut to the real specimen rather than a generic layout:

```
LOYOLA ACADEMY (Autonomous)
DEGREE & PG COLLEGE
A College with Potential for Excellence
UID NO :111725039001
Name    : PARAYIL JOHN SHIBU
Course  : B.Sc. Comp.Sci. & Cog .Sys.
          ACSC 2025 2026
          NCSC 2026 2027
          DCSC 2027 2028
VALID UPTO APRIL 2028
```

Four things there defeat a generic reader, and each is handled:

- **`UID NO` is the identifier label**, and the value is a bare 12-digit number.
  `uid no` / `uid number` / `uid` lead the alias list, and a `^\d{12}$` shape
  scores above every alphanumeric roll pattern.
- **The course is clumped** — `Comp.Sci. & Cog .Sys.`, stray space and all. It is
  normalised to `B.SC. COMP.SCI. & COG.SYS.`, and a year-code row welded onto
  the end of it by a tight crop is cut off rather than swallowed.
- **There is no batch label.** The three programme-code rows carry the span, and
  their years are *space-separated* (`2025 2026`), which the punctuated range
  regex cannot see. They are read into a new `year_codes` field, and the batch
  becomes first-start to last-end — `2025-2028`, graduation year 2028.
- **The expiry is a month, not a date.** `APRIL 2028` resolves to 2028-04-30:
  the card is good *through* April, so the last of the month is the honest read.

Tesseract also welds short labels (`UIDNO 111725039001`), which the value
splitter now handles, and the card is normally photographed sideways — that was
already covered by the OSD orientation pass.

### Tuning it against other cards

Everything above is editable at runtime at **`/admin/ocr`** — no redeploy — so a
second issuer or a changed UID format does not need a code change.

The single highest-value change is `roll_regex`. The default is loose on
purpose. Once the real format is known, tighten it — `^\d{2}[A-Z]{2,5}\d{4}$`,
say — and misreads largely stop, because the candidate enumeration then leaves
exactly one legal survivor. The same page has a text box that runs the extractor
against pasted card text so a regex change can be checked without
re-photographing anything.

---

## Reputation

Event-sourced. Every point-affecting action appends an immutable
`reputation_event`; the numbers on `users` are a cache recomputed from that
ledger. Scores are never stored as a mutable integer, so disputes, corrections
and retroactive rule changes all stay tractable.

Point values are calibrated against the PRD's own worked example: eight upvoted
answers is +96, so an answer upvote is 12; two accepted answers is +40, so
acceptance is 20; five endorsements is +35, so an endorsement is 7.

Totals are the plain sum of the five pillars. The PRD gives both weights
(30/25/20/15/10) and monthly caps (300/250/200/150/100) — the caps *are* those
weights, already applied, and Appendix A confirms it by adding raw pillar points
to 334. Weighting again at the total would double-count.

Anti-gaming, all enforced and all tested: self-voting is impossible at the
database level; repeat votes from the same person decay logarithmically; a
Trusted member's vote is worth 1.5x and a two-week-old account's 0.5x; points sit
provisional for 48 hours; a spike above the user's own 30-day baseline parks
points in a `held` state; reciprocal voting pairs are detected and quietly
nullified without announcing detection; votes from a browser that has signed
into the author's account are worth nothing.

Every point is itemised for the member at `/me/reputation`, including what a cap
withheld and why.

---

## Privacy

The ID card is the most sensitive thing this app touches, so:

- Card and selfie are **encrypted at rest** (Fernet), mode 0600, in a directory
  separate from all other media.
- Every read is written to `pii_access_log` with the reviewer's id. Reviewers
  get a cropped portrait by default; opening the full card requires an explicit
  escalation and is logged as one.
- Images are **shredded 30 days after verification completes** — overwritten,
  then unlinked. A nightly job does it; `/admin` has a manual trigger.
- Only extracted fields and a one-way hash of the card survive. The hash is what
  catches the same card being submitted twice.
- Nothing is publicly addressable. Every page and every image requires a session.
- `/privacy` states all of this to members in plain language, and DPDP Act 2023
  obligations (consent, purpose limitation, deletion requests, a named grievance
  officer) are wired to real flows rather than described.

---

## Stack and why

FastAPI + Jinja + a little vanilla JavaScript, PostgreSQL, Tesseract. No Node,
no build step, no Redis, no search daemon, no Docker.

That is a response to the hardware: one vCPU, 1.9 GB of RAM, shared with a
running EMS CRM. Postgres full-text and trigram search replaces Meilisearch;
rate limits and caching live in Postgres instead of Redis; APScheduler runs
in-process instead of a second worker. The app idles at ~130 MB, and the OCR
subprocess is torn down after each card rather than left resident.

The front end is server-rendered because most of this campus is on Android over
mobile data. There are no web fonts — a system serif/sans pairing gives real
typographic contrast for zero bytes.

---

## Layout

```
app/
  main.py            app setup, lifespan, error handlers, security headers
  config.py          settings (all overridable by environment)
  models.py          every table — the schema is one file on purpose
  deps.py            auth dependencies; verification tier vs reputation tier
  security.py        passwords, sessions, CSRF, signed URLs, at-rest encryption
  templating.py      Jinja filters and globals
  ocr/               the card reader (imageops, extract, engine)
  services/          reputation, voting, verification, moderation, feed,
                     search, antiabuse, media, notify, jobs
  routers/           one module per feature area (HTML)
  api/               the versioned JSON API the Android app is built against
    schemas.py       request bodies and the response serializers
    routes/          auth, feed, qa, people, groups, events, notifications, verify
  templates/         server-rendered UI
  sql/indexes.sql    indexes the ORM cannot express; applied idempotently at boot
deploy/              systemd unit, nginx vhost, deploy script
scripts/             tests and operator tasks
mobile/              the Android app (Flutter)
  lib/core/          config, HTTP client, session state, theme, formatting
  lib/data/          one repository class — every call the app can make
  lib/models/        plain data classes mirroring the API's JSON
  lib/screens/       feed, Q&A, events, alerts, profile, verification, settings
  lib/widgets/       post card, vote bar, avatars, empty and error states
```

The write path for posts and comments lives in `services/posting.py` and is
shared by both clients, so the rate limits, capability gates, moderation screen
and crisis escalation cannot drift apart between web and mobile.

## Infrastructure on the box

| Piece | Where |
|---|---|
| App | `loyola.service` → uvicorn on `127.0.0.1:8011` |
| Proxy | nginx vhost on `127.0.0.1:8090`, static from `/var/www/loyola/static` |
| Tunnel | `cloudflared-loyola.service` → `loyola.avlokai.com` |
| Database | `loyola_db` owned by `loyola_user` on the 16-ems cluster, port 5433 |
| Media | `/var/lib/loyola/media` (public), `/var/lib/loyola/verification` (encrypted, 0700) |

Isolation is mutual and verified: `loyola_user` cannot connect to `ems_crm` or
`email_automation`, and their owners cannot connect to `loyola_db`. `PUBLIC` was
revoked on all of them after granting each owner explicit rights first, so no
existing EMS behaviour changed.

---

## The JSON API

Everything the Android app can do is `/api/v1/*`, documented live at
`/api/docs`. Authentication is the session token as a bearer header; the same
verification wall applies, and it answers in JSON rather than redirecting:

```
403 {"detail": "Verify your student ID to continue.",
     "code": "verification_required", "tier": 0}
```

The app branches on `code`, never on the wording.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/signup`, `/auth/login`, `/auth/logout`, `GET /auth/me`, `/auth/sessions` |
| Feed | `GET /feed?mode=latest\|foryou`, `POST /posts`, `GET /posts/{id}`, `POST /posts/{id}/comments`, `POST /vote` |
| Q&A | `GET /qa/questions`, `POST /qa/questions`, `POST /qa/questions/{id}/answers`, `/accept/{answer_id}`, `GET /qa/duplicates` |
| People | `GET /people`, `/people/{handle}`, `PATCH /me`, `/me/skills`, `GET /me/reputation` |
| Groups | `GET /groups`, `POST /groups`, `POST /groups/{slug}/join` |
| Events | `GET /events`, `POST /events`, `/events/{id}/rsvp`, `/events/{id}/checkin` |
| Alerts | `GET /notifications`, `/notifications/unread-count`, `POST /notifications/read` |
| Verify | `GET /verify/status`, `POST /verify/card`, `/verify/selfie`, `/verify/dispute`, `/verify/manual` (admin) |
| Meta | `GET /config` (unauthenticated), `GET /home`, `GET /search` |

`GET /config` carries `api_version` and `min_supported_build`, so a server can
tell an old install to update instead of failing in confusing ways.

---

## The Android app

Flutter, Android only, `mobile/`. It talks to the API above and nothing else.

```bash
cd mobile
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8011       # emulator
flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8011   # real phone
flutter build apk --release --dart-define=API_BASE_URL=https://loyola.avlokai.com
```

`10.0.2.2` is how an Android emulator reaches the host's localhost. The compiled
value is only a default — the sign-in screen has a server field, because during
a rollout the address changes more often than the app does.

Cleartext HTTP is allowed in debug builds and refused in release ones except to
loopback (`android/app/src/main/res/xml/network_security_config.xml`). A campus
deployment therefore has to terminate TLS: a session token must not cross the
college wifi in clear.

Notifications are polled, not pushed. That is deliberate — push would mean a
Firebase project for the college to administer, and a campus network does not
need to buzz in anyone's pocket.

```bash
flutter analyze && flutter test
```

---

## Running it

Locally, from a clean checkout:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
docker run -d --name loyola-pg -p 5433:5432   -e POSTGRES_USER=loyola_user -e POSTGRES_PASSWORD=loyola -e POSTGRES_DB=loyola_db   postgres:16
cp .env.example .env            # then fill in the two generated keys it names
.venv/bin/python -m uvicorn app.main:app --port 8011 --reload
```

Tables, indexes and extensions are created on first boot. Tesseract has to be
on `PATH` (or at `LOYOLA_TESSERACT_CMD`) for card reading; without it the app
runs and `/healthz` reports `degraded`, and verification has to go through the
manual path below.

To demo it to anyone, seed a campus first — an empty social network demos
badly, because every screen is an empty state:

```bash
.venv/bin/python scripts/seed_demo.py --reset
# 30 students, 5 groups, 11 posts, 5 questions, 5 events, real reputation
# sign in as demo_aarav00 / campus2026
```

To deploy:

```bash
./deploy/deploy.sh              # push code, restart, verify
./deploy/deploy.sh --deps       # also reinstall dependencies
```

Operator tasks:

```bash
ssh emstech
cd /root/loyola
./venv/bin/python scripts/manage.py stats
./venv/bin/python scripts/manage.py create-admin <handle> "<Full Name>"
./venv/bin/python scripts/manage.py grant <handle> moderator
./venv/bin/python scripts/manage.py purge-test-accounts <prefix>
./venv/bin/python scripts/manage.py reset-attempts [<handle>]
./venv/bin/python scripts/manage.py verify <handle> <roll-number> --name "..." --batch 2027
```

`verify` is the counter path: it verifies a student with no card photo at all,
for the faded cards, the reissued ones and the ones sitting in the office for
correction. It enforces the same one-account-per-roll-number rule as OCR, refuses
an already-verified account unless told otherwise, and lands in the same
moderation audit log. `POST /api/v1/verify/manual` is the same thing for admins
over the API.

The weekly verification cap is `LOYOLA_VERIFICATION_ATTEMPTS_PER_WEEK`
(currently **5**). `reset-attempts` clears the rate-limit ledger — for one
account with a handle, or for everybody without one. The ledger is keyed on
the device fingerprint as well as the account, so clearing the rows is the
whole reset: there is no counter to zero.

Tests:

```bash
./venv/bin/python scripts/make_test_card.py /tmp/loyola-cards   # fixtures
./venv/bin/python scripts/test_ocr.py                           # 10 card variants
./venv/bin/python scripts/test_reputation.py                    # engine + anti-gaming
LOYOLA_BASE_URL=http://test ./venv/bin/python scripts/smoke.py  # every HTML route
./venv/bin/python scripts/api_smoke.py                          # every JSON route
./venv/bin/python scripts/live_check.py                         # through real HTTPS
(cd mobile && flutter analyze && flutter test)                  # the app
```

`api_smoke.py` drives the API the way the app does — signup, the verification
wall, posting, voting, self-vote refusal, accepted answers, anonymous questions,
groups, notifications, search, reporting, deletion and token revocation — and
asserts the response shapes the app parses. 48 checks; run it after any change
to `app/api/`.

`smoke.py` and `test_reputation.py` write to the database — run them against a
scratch schema, not live data.

Logs: `journalctl -u loyola.service -f`

---

## Before this goes near real students

The PRD is right that institutional sign-off comes first. You are processing
student PII, and doing it without the college's approval is the fastest way to
get the app banned from campus. That conversation has not happened yet.

Also outstanding:

- **Real card samples.** The template matches a real specimen, but the pipeline
  itself is still only validated against synthetic renders of it. Photograph a
  batch of real cards and check them at `/admin/ocr` before launch — in
  particular, confirm the UID is twelve digits for every cohort, not just this
  one.
- **Seed the Q&A archive** before opening it up. An empty network is worse than
  no network; the PRD's departmental pilot plan is the right approach.
  `scripts/seed_demo.py` builds a plausible campus for demos, but a pilot wants
  real questions from real students, not thirty fictional ones.
- **TLS before the app ships.** The release build already refuses cleartext, so
  the deployment has to terminate TLS — which it does today through cloudflared.
  Point `API_BASE_URL` at the HTTPS host when building the APK for students.
- **Sign the release build.** `mobile/android/app/build.gradle.kts` still signs
  with the debug key, which is fine for sideloading during a pilot and not fine
  for anything the college distributes.
- **Recruit moderators** and grant the role before launch, not after the first
  incident.
- **Back up `loyola_db`.** Nothing here sets that up, and it is on the same disk
  as everything else.
