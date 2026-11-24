# M2 — Implementation contract

Scope approved via the Phase 5 roadmap. Every item maps to a numbered decision in
`DECISIONS.md`; where that file leaves something open, this document says so
instead of inventing policy. **No schema migrations framework** (no Alembic in
this cycle): new tables ship in `app/models.py` + `Base.metadata.create_all`
runs at boot as it already does.

Status key: **A** = mandatory photo (§4), **C** = proof of dispatch (§2),
**B** = dispute flow (§1 + §3), **D** = `seed_dev.py` repair.

---

## A. Photo is required on every posted document (§4)

**Rule.** `POST /api/found-ids` without an `image` file returns `400
"A photo of the document is required"`, except when `SEEKER_TESTING=1`
(suite posts text-only fixtures; guard is env-read per request, matching the
`pickup_address` precedent at `found_ids.py:109`).

**Files.** `backend/app/routers/found_ids.py` (guard), `frontend/src/pages/PostFoundPage.*`
(file input gets `required`, submit blocked client-side too).

**Tests (+2).** Post with no image → 400; post with image → 201 and both image
derivatives exist (the existing golden-path walk already covers the happy side).

**Demo beat.** Posting shows the camera/upload step as unavoidable — "no
listing without the document in hand".

---

## C. Courier dispatch requires a tracking reference (§2)

**Rule.** `POST /api/deliveries/{match_id}/dispatch` accepts
`{"tracking_reference": "..."}`. Courier orders **must** supply a non-empty
reference; metup orders must not require one. The current auto-minted
`SEEK-<hex>` is removed for courier (it was an assertion, not evidence).
A supplied reference is trimmed, length-capped (80), stored verbatim.

**Files.** `backend/app/routers/deliveries.py` (`dispatch_delivery`), plus a
`DispatchRequest` model in `app/schemas.py`; `frontend` dispatch control gains
the field for courier orders only.

**Tests (+2, plus one guard).** Courier without reference → 400/422; courier
with reference → stored and returned; meetup without reference → 200 and no
reference needed.

**Accepted cost (from §2, stated, not smuggled).** A fabricated number passes.
Verification is a human opening the courier's tracking page — no API exists.

---

## B. Dispute flow (§1 + §3)

**Invariant: no money moves without an admin.** The owner can escalate; a timer
can flag; only an admin decision writes a ledger effect. Nothing auto-refunds.

### B1. Owner-initiated dispute

`POST /api/deliveries/{match_id}/dispute` — owner (seeker) only.

- Body: `{"reason": "..."}` (10–500 chars).
- Allowed only while `order.status in {READY_FOR_HANDOVER, IN_TRANSIT}` and the
  transaction is `PAID`; otherwise `409`. Already-disputed → `409`.
- Effect: `order.status = DISPUTED`, `order.disputed_at = now`,
  `match.flagged_for_review = True`, incident reason stored (audit/notification
  carries it), both parties notified.
- A dispute never releases the record, never deletes the match, never pays.

### B2. Timer sweep (flag only)

`flag_stale_dispatches(db)` runs lazily on admin queue reads (same pattern as
`claims.expire_stale_claims`) with `DISPUTE_TIMER_DAYS` (default **14**,
env-overridable). Criteria: `order.status == IN_TRANSIT`, `dispatched_at` older
than N days, no `delivered_at`, transaction `PAID` → flag the match for review.
**It writes no refund and no earning.**

### B3. Admin resolution

`POST /api/admin/disputes/{match_id}/resolve` — admin only.

- Body: `{"note": "...", "refund_platform": bool, "release_finder": bool}`.
- `refund_platform=true` → create exactly one `RefundObligation`
  (`amount = txn.platform_share`, status `owed`, admin id, note). This is the
  §1 refundable half; it is **recorded as owed**, never paid — B2C does not
  exist and `wallet.py` hard-refuses outside sandbox.
- `release_finder=true` → write exactly one `Earning` for the finder
  (`amount = txn.finder_share`) attributed to the admin decision. This is the
  §1 "finder keeps their share on proof of dispatch" path that
  `confirm_handover` cannot serve when the owner ghosts.
- Both flags true is the §1 default outcome when dispatch is proven: owner
  refunded the platform half, finder keeps their half.
- Either flag false leaves that side untouched. Repeat resolution is `409`
  (or idempotent no-op — decide in tests), and every resolution records the
  admin, timestamp, and note on the match.
- Resolving clears `flagged_for_review` and moves the order out of `DISPUTED`
  (`CANCELLED` is the honest terminal state here; add only if absent).

### B4. Ledger visibility

Admin ledger gains the refund obligations (amount, currency, match, status,
created_at, resolution note) so "refund owed" is auditable while the payout
rail is missing. Amounts in minor units, as everywhere else.

### B5. Adversarial tests (~10)

1. Owner disputes an in-transit paid delivery → `DISPUTED`, flagged, reason recorded.
2. Stranger → 404 (not 403: do not confirm the match exists).
3. Finder → 404 (owner-only endpoint).
4. Unpaid claim → 409.
5. Delivered order → 409.
6. Second dispute → 409.
7. Non-admin resolve → 403.
8. `refund_platform` writes exactly one obligation; no `Earning` row; no wallet movement.
9. `release_finder` writes exactly one `Earning` equal to the finder share; replay does not double-credit.
10. Timer sweep at N days flags but still refunds nothing.
11. Resolution clears the flagged queue and cannot be replayed.

### B6. Deliberately left open (mirrors `DECISIONS.md`)

- **N** is a config default (14), not a claim of correctness — tune with real
  delivery data.
- **Courier fee liability** stays unresolved; the dispute ledger records the
  recovery-fee halves only, never the courier leg.
- **Refund rail** stays absent; `RefundObligation` is built to outlive it.

---

## D. `seed_dev.py` repair

The guard added in `68f9d32` requires a verified phone on posting; the seed
script predates it and 403s. Fix by driving the sandbox OTP flow exactly as
`golden_walk.py` does (`/api/auth/otp/request` → `/api/auth/otp/verify` with the
returned `sandbox_code`) before posting, and promote its admin user through the
same path the golden walk uses. Result: one command rebuilds a demo-ready
dataset (finder, seeker, admin, one post, one paid claim, one handover).

---

## Order of work

1. A (smallest, closes the zero-cost fraud hole)
2. C (additive column already exists; no schema change)
3. B (new model + endpoints + tests)
4. D (script repair; unblocks reliable demo data)
5. Frontend wiring (M3) for A, C, B surfaces.

Each step lands with its own tests and its own commit; the suite must stay green
between steps.
