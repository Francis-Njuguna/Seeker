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
- **Claim lifecycle**: To prove ownership, the seeker must reproduce the full ID number (which they only see a two-character tail of). The finder reviews and approves the claim, then the unredacted record is revealed.

### No-match / alert flow
- **Lost-ID alerts**: If a search returns nothing, the seeker can register a standing alert (name + ID number)
- **Automatic notification**: When a matching document is posted later, the seeker gets notified by email (or in-app if SMTP is not configured)
- **Immediate match**: Posting a found ID checks existing alerts and creates matches instantly

### Payment-ready seam
The `status` column on matches uses a string-backed enum that includes `AWAITING_PAYMENT` between `CLAIM_REQUESTED` and `CLAIMED`. That state is reserved and unused in the MVP — a payment step can be inserted there later without any schema migration.

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

**Test coverage**:
- ✅ 75 passing: auth, search (exact + redacted previews), alerts (no-match flow), matches (claim lifecycle), normalization, found-ID CRUD
- ⚠️ 5 OCR tests fail on synthetic ID cards where the heuristics pick the wrong token. Since OCR output goes through manual review before posting, these are known edge cases and don't block the app.

## Project structure

```
backend/
  app/
    routers/        # FastAPI route modules (auth, found_ids, search, lost_alerts, matches, notifications)
    models.py       # SQLAlchemy ORM (User, FoundId, LostAlert, Match, Notification)
    schemas.py      # Pydantic request/response models
    ocr.py          # Tesseract wrapper + field extraction heuristics
    storage.py      # Image validation + blur generation
    normalize.py    # Canonical forms for exact matching + redaction
    matching.py     # Match engine (exact ID number match only)
    notifications.py# Email + in-app notifications
    security.py     # Password hashing + JWT
    database.py     # Async session factory
    config.py       # Settings (env-based)
    main.py         # FastAPI app + CORS
  tests/            # Pytest suite (fixtures, integration tests)
  Dockerfile
  requirements.txt

frontend/
  src/
    pages/          # Dashboard, Login, Register, PostFound, Search, Alerts, Matches
    components/     # Nav, RedactedPreview
    auth.jsx        # Auth context + token storage
    App.jsx         # Router
    styles.css      # Single-file CSS
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

Frontend environment variables:

- `VITE_API_PROXY` — Backend URL for the dev server proxy (defaults to `http://backend:8000` inside Docker, `http://localhost:8000` on host)

## Assumptions and MVP trade-offs

1. **Exact matching only**: One character off in the ID number returns no match. This is deliberate — fuzzy matching would generate false positives (handing a stranger someone's ID preview). Normalization handles spacing and dashes so cosmetic differences don't break real matches.

2. **OCR is unreliable**: The app treats OCR output as suggestions, not ground truth. The finder reviews and corrects every field before the record is created. The schema tracks whether the finder edited the machine's guess (`fields_edited_by_finder`).

3. **No payments yet**: The `status` ladder has an `AWAITING_PAYMENT` state reserved between `CLAIM_REQUESTED` and `CLAIMED`. That slot is unused in the MVP, so a payment gate can be added later without a schema migration.

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

## Known issues / future work

- OCR heuristics fail when a label is ambiguous (e.g., `DATE OF BIRTH 1990-04-12` gets parsed as an ID number because it has 8 digits). The two-step flow (OCR suggests, finder confirms) mitigates this.
- Uploading an image without OCR (or with OCR disabled) skips the suggestion step entirely, so the flow works even when Tesseract is unavailable.
- The in-process rate limiter resets on restart. Move to Redis for production.
- Email templates are plain text. A production deployment would add HTML templates with branding.
- No audit log. A production deployment would add an `events` table tracking who did what when.

## License

This is an MVP built as a demonstration. No license is specified.
