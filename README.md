# Loyola Networking

A closed, identity-verified social network for Loyola Academy, built from
`loyola-networking-prd.pdf`. Live at **https://loyola.avlokai.com**.

Entry is gated on a college ID card. Inside, members post, ask and answer
academic questions, form groups, run events, find project teams, share notes and
mentor juniors — and earn a reputation that comes from contributing rather than
from being liked.

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
| Quality gate | Laplacian blur score, brightness, blown-highlight ratio. Unusable frames are rejected with advice rather than a failure. |
| Card detection | Canny + contour search for the card quadrilateral, then a perspective warp to flatten it. Rejects quads that are essentially the whole frame — that is the photo's border, not the card. |
| Orientation | Tesseract OSD on a downscaled copy, falling back to a horizontal-run-length heuristic when OSD declines (it often does on sparse cards). |
| Binarisation | CLAHE, adaptive, Otsu and an inverted pass, tried in order and built lazily. The inverted pass is what reads white-on-navy institution banners, which every dark-text threshold erases. |
| Extraction | Label-anchored first (`Roll No:`, `Admission No:`, fuzzy-matched), pattern scan second, positional heuristics last. Each field carries its own confidence and the method that produced it. |
| Disambiguation | Tesseract reads `21BSC1042` as `ZIBSCIO4Z`. The reader enumerates substitutions across ambiguous positions and scores each candidate against known roll-number shapes, penalising each flip. |

Measured on the synthetic fixtures: **7/7 correct across clean, perspective-warped,
phone-noise, 90°-rotated, glare and dim captures, plus a second card layout with
a different label and a slashed roll number. 5–7 seconds per card.**

### Tuning it against real cards

Nobody had a specimen Loyola Academy card when this was written, so the defaults
are a sensible Indian-college layout and everything about them is editable at
runtime at **`/admin/ocr`** — no redeploy.

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
  routers/           one module per feature area
  templates/         server-rendered UI
  sql/indexes.sql    indexes the ORM cannot express; applied idempotently at boot
deploy/              systemd unit, nginx vhost, deploy script
scripts/             tests and operator tasks
```

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

## Running it

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
```

Tests:

```bash
./venv/bin/python scripts/make_test_card.py /tmp/loyola-cards   # fixtures
./venv/bin/python scripts/test_ocr.py                           # 7 card variants
./venv/bin/python scripts/test_reputation.py                    # engine + anti-gaming
LOYOLA_BASE_URL=http://test ./venv/bin/python scripts/smoke.py  # every route
./venv/bin/python scripts/live_check.py                         # through real HTTPS
```

`smoke.py` and `test_reputation.py` write to the database — run them against a
scratch schema, not live data.

Logs: `journalctl -u loyola.service -f`

---

## Before this goes near real students

The PRD is right that institutional sign-off comes first. You are processing
student PII, and doing it without the college's approval is the fastest way to
get the app banned from campus. That conversation has not happened yet.

Also outstanding:

- **Real card samples.** Everything above is validated against synthetic cards.
  Photograph a few real ones, check `/admin/ocr`, tighten `roll_regex`.
- **Seed the Q&A archive** before opening it up. An empty network is worse than
  no network; the PRD's departmental pilot plan is the right approach.
- **Recruit moderators** and grant the role before launch, not after the first
  incident.
- **Back up `loyola_db`.** Nothing here sets that up, and it is on the same disk
  as everything else.
