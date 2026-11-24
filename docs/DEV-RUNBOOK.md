# Seeker — Developer & Demo Runbook

Living operational notes. Codebase facts live in `AGENTS.md` ( forthcoming ),
decisions in `DECISIONS.md`; this file is about *running the thing* on this
machine, especially under exhibition conditions.

## Verified facts (M1, 2026-11-17)

### Boot sequence

```powershell
# 1. Docker Desktop must be running first (engine, not just the app window).
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
# then poll: docker info  (ready when exit code 0)

# 2. Database first, WAIT for healthy — crash recovery after an abrupt machine
#    shutdown replays WAL for minutes and outlasts compose's healthcheck window.
docker compose up -d db
# poll: docker inspect seeker-db-1 --format "{{.State.Health.Status}}"  -> healthy

# 3. Backend WITHOUT the file watcher (see "watchfiles" below).
docker compose run -d --service-ports backend uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. Frontend
docker compose up -d frontend   # vite at http://localhost:5173

# 5. Probe with curl.exe — NOT Invoke-WebRequest (see below).
curl.exe -s --max-time 20 http://127.0.0.1:8000/api/health
```

### Environment landmines (all encountered live on 2026-11-17)

| Symptom | Cause | Remedy |
|---|---|---|
| `backend` exits: `socket.gaierror ... name resolution` | Docker embedded DNS corrupted after engine churn | `docker compose down` (volumes persist) then `up` again — full network rebuild |
| Backend crash-loop: `WatchfilesRustInternalError ... os error 5` | compose dev command uses `--reload`; watchfiles dies on Windows bind mounts | Run uvicorn without `--reload` (run container with `--service-ports`), or edit compose for demo mode |
| `dependency failed to start: container seeker-db-1 is unhealthy` | Postgres crash-recovery (WAL replay + fsync) exceeds the 20-retry healthcheck | Wait for `healthy` before starting backend; consider `start_period` in compose |
| Health probe "connection closed unexpectedly" | PowerShell `Invoke-WebRequest` flakiness on this box | Use `curl.exe`; retry once before diagnosing |
| Engine dies entirely (pipe gone, no processes) | Docker Desktop instability on this machine — observed 4× in one day | Restart Docker Desktop, wait for engine, `up -d db`, wait healthy, then services |
| Port 8000 already allocated | Ad-hoc `compose run --service-ports` container holds it | `docker rm -f` the `backend-run-*` container before `up` |

### Test suite

```powershell
# Full suite (~30 min on this machine), official baseline:
docker compose run --rm -e SEEKER_TESTING=1 `
  -e TEST_DATABASE_URL=postgresql+asyncpg://seeker:seeker@db:5432/seeker_test `
  backend python -m pytest tests/ -q --tb=short

# Never touch backend source or containers while it runs — source is bind-mounted.
```

**Engine-restart signature:** if the suite output stops mid-progress and every
container shows `Exited (255)`, Docker Desktop restarted — that is an engine
crash, not test failures, and the `--rm` test container is already gone.
Recovery: start `db`, wait for `healthy`, then rerun in **chunks** so a crash
costs one file rather than the whole suite:

```powershell
docker compose run --rm -e SEEKER_TESTING=1 `
  -e TEST_DATABASE_URL=postgresql+asyncpg://seeker:seeker@db:5432/seeker_test `
  backend python -m pytest tests/test_payments.py -q --tb=short
```

`test-payments` → `test-earnings + test-admin` → `test-matches + test-search`
keeps each run short enough to survive the machine's engine instability.

Baseline lineage:
- `backend/baseline.txt` — Aug 12 (203 passed, 1 skipped; pre-verification-ladder-rework)
- `backend/baseline-2026-11-17.txt` — current machine, post-rework contract

### The post-August contract every test must satisfy

The verification-ladder rework (commit `68f9d32` era) changed three product
behaviours; tests written before it assert the old ones:

1. **Typed number = fallback evidence.** A claim proven only by typing the ID
   number is recorded as `manual_fallback`. Finder approval then parks the
   match at `claim_requested` with `finder_approved_at` set; an admin decision
   (`POST /api/admin/claims/{match_id}/decision`) is required before
   `awaiting_payment` and checkout.
2. **Name search requires name + DOB.** `/api/search` with `name` alone no
   longer matches; results report `matched_on: "name_and_date_of_birth"`.
3. **Escrow.** The payment webhook marks the transaction paid and moves the
   match to `claimed`, but writes **no** `Earning` row. The finder's share is
   written only when the finder confirms the owner's receipt code at
   `POST /api/deliveries/{match_id}/confirm`. `POST /api/matches/{id}/complete`
   is a read-compatible legacy route that 409s until delivery is `delivered`.

Test-fixture consequences:
- `pay_for(match_id, seeker, meetup_quote=True)` plans a free meetup so the
  webhook issues a receipt code — required by any test that asserts earnings.
- `post_found` supplies DOB (name search) and a pickup address (quotes).
- In `SEEKER_TESTING` mode users are phone-verified at registration and
  `initiate` auto-creates a free meetup delivery order when none is quoted.

### Golden path (17-step live-API verification script)

`golden_walk.py` (session temp; to be promoted into `backend/scripts/` in M2)
drives register → OTP → post (photo) → search → lock → stranger-invisibility →
claim → finder approval → admin review (fallback claims) → quote → mock pay →
receipt code → handover → completion → wallet = KES 100 → post-state checks.

Demo-relevant behavior verified: typed-number claims are `manual_fallback` and
pass through the admin queue — the admin console is on the happy path, which
makes the exhibition's "trust operations" beat part of the main flow.

### Machine hygiene

- Docker Desktop here dies roughly daily; budget 5–10 min of recovery time and
  never assume a crashed background job means code failure.
- A 599 MB mp4 once reached git history and blocked pushes; `*.mp4` et al are
  repo-wide ignored — keep demo video out of the repo.
