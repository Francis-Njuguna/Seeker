# Seeker — Lost &amp; Found for ID Cards and Documents

A full-stack MVP for matching found ID documents with people who lost them. Built with FastAPI, Postgres, Tesseract OCR, and React.

## Features

### Finder flow
- **Post a found ID**: Upload a photo, OCR extracts name/ID number/document type, finder confirms the values before posting
- **Exact matching**: New postings automatically fan out to waiting lost-ID alerts
- **Privacy by default**: Unredacted details and photos are never shown to seekers until a claim is approved

### Seeker flow
- **Exact-match search**: Search by ID number or full name (no fuzzy matching — one character off returns no match)
- **Redacted preview**: Seekers see only the last two characters of the ID number and initials+length of each name token, plus a heavily blurred photo
- **Claim lifecycle**: To prove ownership, the seeker must reproduce the full ID number (which they only see a two-character tail of). The finder reviews and approves the claim, which advances it to `AWAITING_PAYMENT` — the unredacted record is revealed once the recovery fee is confirmed paid.

### No-match / alert flow
- **Lost-ID alerts**: If a search returns nothing, the seeker can register a standing alert (name + ID number)
- **Automatic notification**: When a matching document is posted later, the seeker gets notified by email (or in-app if SMTP is not configured)
- **Immediate match**: Posting a found ID checks existing alerts and creates matches instantly

### Payments and finder earnings
Approving a claim does **not** hand over the document — it advances the match to `AWAITING_PAYMENT`. The seeker pays a recovery fee, and only the payment provider's webhook moves the match to `CLAIMED` and reveals the unredacted record.

- **Two providers**: `mock` is a self-contained sandbox needing no credentials (the default, and what the tests exercise); `loop` talks to the NCBA Loop sandbox once API keys are set.
- **Revenue split**: Each payment is split between the platform and the finder by `PLATFORM_SHARE_RATIO` (default 0.5). The split is integer arithmetic with the remainder going to the finder, so the shares always sum exactly to the amount.
- **Finder earnings**: A confirmed payment writes an `Earning` row for the finder, surfaced through `/api/earnings/summary` and `/api/earnings/documents`.
- **Webhooks are signature-checked**: HMAC-SHA256 over the raw body against `LOOP_WEBHOOK_SECRET`. If no secret is configured the check is skipped and logged loudly — sandbox-friendly, but noisy rather than silent in production.

### Claimant verification
Beyond reproducing the full ID number, a seeker can prove ownership two other ways:

- **Document image**: Upload your own photo of the document; the server OCRs it, compares it to the finder's record, and discards the image. Attempts are capped.
- **Face match**: Compare a selfie against the face on the found document (`FACE_MATCH_THRESHOLD`, default 0.6 — the `face_recognition` library default, not a value tuned on this data).

The finder still makes the final call; the verification method and score are shown alongside each incoming claim so an automated match is never mistaken for proof.

### Admin
`/api/admin/*` exposes platform stats, a transaction ledger, and claim/flagged-claim review queues. These are **API-only** — there is no admin UI in the frontend, and the routes require a user whose `role` is `admin`, which is assigned directly in the database (there is no self-service path to it).

## Stack

- **Backend**: FastAPI + SQLAlchemy (async) + asyncpg
- **Database**: Postgres 16
- **OCR**: Tesseract via pytesseract (containerized)
- **Frontend**: React 18 + React Router 7 + Vite
- **Deployment**: Docker Compose

## Running the app

### Prerequisites
- Docker Desktop running
- Git (to clone if needed)

### 1. Start the backend and database

```powershell
# From the project root
docker compose up -d db backend
```

The backend runs on **http://localhost:8000**. Health check: open http://localhost:8000/api/health in a browser.

Postgres is exposed on **host port 55432** (not 5432) to avoid clashing with any local Postgres instance.

### 2. Start the frontend

The frontend runs directly on your host (not in Docker) so hot reload is instant.

```powershell
cd frontend
npm install
npm run dev
```

The app opens at **http://localhost:5173**. The Vite dev server proxies `/api/*` requests to the backend automatically.

### 3. Use the app

1. Open http://localhost:5173
2. Register an account (any email works; no verification)
3. **Finder flow**: Post Found ID → upload a photo (or skip the photo), confirm the extracted fields, submit
4. **Seeker flow**: Search → enter the same ID number or name → redacted preview appears → start a claim by entering the full ID number
5. **Finder decision**: The finder sees "Claims awaiting your decision" on the Matches page → approve or decline
6. **Alert flow**: Search for something that doesn't exist → register a lost-ID alert → have another user post that ID → the seeker gets notified

## Running tests

Tests run inside the backend container against a separate `seeker_test` database.

```powershell
docker compose run --rm backend python -m pytest -v
```

**Test coverage** (last run: 170 passed, 2 failed, 8 skipped):
- ✅ auth, search (exact + redacted previews), alerts (no-match flow), matches (claim lifecycle), payments (split arithmetic, webhook signatures, mock provider), earnings, admin, normalization, found-ID CRUD
- ⚠️ 2 failing: `test_admin.py::test_stats_group_documents_and_matches_by_status` and `test_earnings.py::test_an_unclaimed_document_appears_with_no_match_and_no_earning`. Both assert on a document/match status string (expected `available`, got `posted`), so the label the API returns and the label the tests expect have drifted apart. Neither is a frontend issue.
- ⏭️ 8 skipped: face-matching tests that need the `face_recognition` backend, which is not installed in the test image.

The suite takes ~16 minutes, mostly in bcrypt hashing and OCR.

## Project structure

```
backend/
  app/
    routers/        # FastAPI route modules (auth, found_ids, search, lost_alerts,
                    #   matches, notifications, payments, earnings, admin)
    models.py       # SQLAlchemy ORM (User, FoundId, LostAlert, Match, Notification,
                    #   Transaction, Earning)
    schemas.py      # Pydantic request/response models
    ocr.py          # Tesseract wrapper + field extraction heuristics
    face.py         # Face encoding + selfie comparison
    verification.py # Claimant verification (document OCR compare, attempt caps)
    payments.py     # Provider adapters (mock, loop), revenue split, webhook signatures
    storage.py      # Image validation + blur generation
    normalize.py    # Canonical forms for exact matching + redaction
    matching.py     # Match engine (exact ID number match only)
    notifications.py# Email + in-app notifications
    security.py     # Password hashing + JWT
    deps.py         # Shared FastAPI dependencies (current user, admin guard)
    database.py     # Async session factory
    config.py       # Settings (env-based)
    main.py         # FastAPI app + CORS
  tests/            # Pytest suite (fixtures, integration tests)
  Dockerfile
  requirements.txt

frontend/
  public/
    img/            # Photography used across the UI (see public/CREDITS.md)
    fonts/          # Self-hosted Inter + Instrument Serif woff2
  src/
    pages/          # Dashboard, Login, Register, PostFound, Search, Alerts, Matches
    components/     # Nav, RedactedPreview, Icon (inline SVG set), Brand (+ AuthLayout)
    auth.jsx        # Auth context + token storage
    App.jsx         # Router
    styles.css      # Single-file CSS (design tokens + all component styles)
  index.html
  vite.config.js
  package.json

docker-compose.yml  # Postgres + backend + frontend services
```

## Configuration

Backend environment variables (set in `docker-compose.yml` or a `.env` file):

- `DATABASE_URL` — Postgres connection string
- `JWT_SECRET` — Used to sign access tokens
- `UPLOAD_DIR` — Where uploaded images and blurred derivatives are stored
- `SMTP_HOST` — If empty, emails are logged instead of sent (MVP default)
- `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` — SMTP config for production
- `SEARCH_RATE_LIMIT` / `SEARCH_RATE_WINDOW_SECONDS` — Throttle enumeration attacks

Payments:

- `PAYMENT_PROVIDER` — `mock` (default, no credentials) or `loop`
- `PAYMENT_AMOUNT_MINOR` — Recovery fee in minor units (default 20000)
- `PAYMENT_CURRENCY` — Currency code (default `KES`)
- `PLATFORM_SHARE_RATIO` — Platform's cut of each payment (default 0.5; the remainder goes to the finder)
- `LOOP_API_URL`, `LOOP_API_KEY` — NCBA Loop credentials, only needed when `PAYMENT_PROVIDER=loop`
- `LOOP_WEBHOOK_SECRET` — HMAC key for webhook signatures. If unset, verification is skipped and logged.
- `PUBLIC_BASE_URL` — Base URL the provider builds callback and checkout links against

Verification:

- `FACE_MATCH_THRESHOLD` — Face distance cutoff (default 0.6, the `face_recognition` library default)
- `FACE_MAX_ATTEMPTS` — Face verification attempts allowed per claim (default 5)

Frontend environment variables:

- `VITE_API_PROXY` — Backend URL for the dev server proxy (defaults to `http://backend:8000` inside Docker, `http://localhost:8000` on host)

## Assumptions and MVP trade-offs

1. **Exact matching only**: One character off in the ID number returns no match. This is deliberate — fuzzy matching would generate false positives (handing a stranger someone's ID preview). Normalization handles spacing and dashes so cosmetic differences don't break real matches.

2. **OCR is unreliable**: The app treats OCR output as suggestions, not ground truth. The finder reviews and corrects every field before the record is created. The schema tracks whether the finder edited the machine's guess (`fields_edited_by_finder`).

3. **Payments are sandboxed by default**: `PAYMENT_PROVIDER` defaults to `mock`, which mints a fake reference and exposes `POST /api/payments/mock/{reference}` to simulate the provider calling back. No money moves and no credentials are needed. Switching to `loop` requires real keys, and the webhook secret must be set or signature verification is skipped.

4. **In-process rate limiting**: Search is rate-limited to prevent enumeration (an attacker brute-forcing which ID numbers exist). The limiter is in-process (`collections.deque`), so it resets if the backend restarts and doesn't work across multiple backend replicas. A production deployment would move this to Redis.

5. **Email is optional**: If `SMTP_HOST` is unset, emails are logged instead of sent. This keeps the MVP runnable with zero external dependencies. The in-app notification feed still works.

6. **Single-region deployment assumed**: Notification timestamps are UTC, but the UI displays them in the browser's local timezone. A multi-region deployment would need explicit timezone handling on the backend.

7. **No image optimization**: Uploaded photos are re-encoded as JPEG at 88% quality. A production deployment would add format negotiation (WebP with JPEG fallback) and a CDN.

8. **Tesseract binary is containerized**: OCR depends on the `tesseract` command, which is installed in the backend Docker image. Running the backend outside Docker requires installing Tesseract separately.

## Security notes

- **Redaction is server-side**: The API never returns the raw `holder_name` or `id_number` fields to a seeker until their claim is approved. The redacted forms are computed on the backend and the client never has the unredacted values to leak.
- **Blurred images are pre-rendered**: The blurred preview is generated once at upload time and served from disk. The unblurred photo is only accessible after a claim is approved.
- **Search is rate-limited**: Exact-match search on an ID number is an enumeration oracle (an attacker could confirm which numbers exist). The rate limiter (30 searches per 60 seconds per user) throttles that attack.
- **Passwords are bcrypt-hashed**: `passlib[bcrypt]` with auto-salting. Plaintext passwords never touch the database.
- **JWTs are short-lived**: Access tokens expire after 12 hours (configurable).
- **Payment webhooks verify their signature**: HMAC-SHA256 over the raw request body against `LOOP_WEBHOOK_SECRET`, checked before any state changes.
- **Document verification images are not kept**: An uploaded verification document is OCR'd, compared against the finder's record, and deleted. Verification attempts are capped per claim.

## Known issues / future work

- OCR heuristics fail when a label is ambiguous (e.g., `DATE OF BIRTH 1990-04-12` gets parsed as an ID number because it has 8 digits). The two-step flow (OCR suggests, finder confirms) mitigates this.
- Uploading an image without OCR (or with OCR disabled) skips the suggestion step entirely, so the flow works even when Tesseract is unavailable.
- The in-process rate limiter resets on restart. Move to Redis for production.
- The mock payment provider confirms a transaction only when its simulation endpoint is called. That's fine for demos; a production run must configure the `loop` provider and webhook secret.
- Email templates are plain text. A production deployment would add HTML templates with branding.
- No audit log. A production deployment would add an `events` table tracking who did what when.
- **No admin UI.** The `/api/admin/*` routes exist and are role-gated, but nothing in the React app calls them — an admin has to hit the API directly (e.g. via `/docs`). The same is true of `/api/earnings/*`: finders can't see their earnings in the UI yet.

## License

This is an MVP built as a demonstration. No license is specified.
