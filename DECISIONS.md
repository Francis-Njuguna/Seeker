# Seeker — Decisions

Why the product behaves the way it does. `AGENTS.md` covers *how the code works*;
this file covers *what was decided and why*, so settled questions are not
relitigated.

Each section is marked **DECIDED** (the user chose it) or **DEFAULTED** (taken by
an agent because the question went unanswered, and correctable on request).

---

## 1. Refund policy when a paid recovery fails — DECIDED

**The KES 200 recovery fee splits into a refundable half and a non-refundable half.**

- **Platform share (KES 100) — refundable.** Returned to the owner when a paid
  recovery fails to deliver the document.
- **Finder share (KES 100) — non-refundable**, provided the finder can show they
  dispatched the document.
- Courier fee: unresolved, see open questions below.

The failure this addresses: the owner pays, then the finder ghosts or the courier
loses the document. Money has moved, the document has not.

### Why not the alternatives

- *Full KES 200 back to the owner* — punishes a finder who genuinely handed the
  document over and lost it to a courier. The finder did the work.
- *No automatic refund, admin decides each case* — rejected as the sole mechanism
  because it does not scale and leaves the owner with no stated entitlement.
- *Courier fee only, recovery fee non-refundable* — leaves the owner paying the
  platform for a service that did not happen.

### What the current code does instead

Nothing. There is no refund path anywhere in the backend — grep for
refund/reversal/chargeback returns no implementation. Today the only outcome of a
failed recovery is that the finder never enters the receipt code, so their KES 100
stays frozen in escrow indefinitely and the owner's KES 200 is unrecoverable.

### Consequences this decision creates (not yet built)

1. **The finder's KES 100 cannot currently be released without the owner.**
   `Earning` rows are written only by `deliveries.confirm_handover`
   (`backend/app/routers/deliveries.py:180`), which requires the owner's receipt
   code. If the document never arrives, the owner never surrenders the code, so
   "the finder keeps their share" has no code path. An admin-forced release on
   proof of dispatch is required and does not exist.

2. **"Can show they dispatched" has no evidential basis today.**
   `dispatch_delivery` (`backend/app/routers/deliveries.py:159`) is finder-only,
   takes no evidence, and mints its own tracking reference
   (`SEEK-<hex>`). It records an assertion, not proof. A finder can mark
   IN_TRANSIT without touching the document.

3. **There is no rail to pay a refund out on.** The only payout code in the
   repository (`backend/app/routers/wallet.py:148`) refuses with 503 whenever
   `otp_provider != "sandbox"`, because Daraja B2C is not integrated. Note the
   coupling: the *payout* guard is keyed on the **OTP provider** setting, so
   wiring up real SMS would also switch withdrawals from "instantly marked paid"
   to "always 503". Worth untangling before either goes live.

4. `DeliveryStatus.DISPUTED` (`backend/app/models.py:204`) is defined and never
   assigned anywhere. It is the natural hook for this flow.

### Open questions

- Who or what initiates a refund — owner request, admin action, or an automatic
  timer after dispatch with no confirmation?
- Who absorbs the courier fee when the courier is the party that failed? No
  courier payout leg exists, so the fee is collected and never disbursed.
- Is a refund paid back over M-Pesa B2C (not integrated) or recorded as a manual
  operator obligation until it is?

---

## 2. Proof of dispatch — DEFAULTED

**A real courier tracking number, entered by the finder, is what makes the
finder's KES 100 non-refundable.** Staffed meetups are exempt (nothing is
dispatched), and the finder's share there rests on the receipt code as it does
today.

Taken as a default because the question went unanswered. Correctable.

### Why this default

It is the cheapest to reverse and the one required under almost every other
answer — an admin adjudicating a dispute wants a tracking reference whatever the
policy is. Implementation is additive: a nullable column plus validation on
`dispatch_delivery`, foreclosing nothing.

- *Handover photo* — rejected as the default. It proves a parcel existed, not that
  it was ever lodged with a courier, and it would have Seeker storing more images
  of people's identity documents, cutting against the retention posture the rest
  of the codebase maintains (`backend/app/retention.py`). Also expensive to
  reverse once images are being collected.
- *Meetup-only guarantee* — rejected as the default because it withdraws a promise
  from finders who use couriers, which is harder to walk back than a field is to
  add.
- *Admin judgement from the message thread alone* — rejected as the sole mechanism
  because it gives the admin nothing external to check.

### The cost this default accepts — stated plainly

**It raises the cost of faking a dispatch; it does not prevent it.** A finder can
enter a fabricated or recycled tracking number and nothing in Seeker will catch
it. There is no courier API integration — `_quote`
(`backend/app/routers/deliveries.py:44`) returns deterministic sandbox quotes for
Posta Kenya / Super Metro Parcel — so verification is a human opening the
courier's own tracking page. Until a courier integration exists, the
non-refundable half rests on manual admin checking, and a determined bad actor
gets one fraud through per tracking number before anyone notices.

This is accepted knowingly: the alternative defaults each cost more (new PII
storage, or a withdrawn promise) and are harder to undo.

---

## 3. Who initiates a refund — DEFAULTED

**Both, and neither pays out automatically:**

1. **Owner-initiated dispute.** The owner marks "the document never arrived",
   which moves the delivery to `DeliveryStatus.DISPUTED`
   (`backend/app/models.py:204`, currently defined and never assigned) and flags
   the match for admin review.
2. **Automatic timer.** N days after dispatch with no receipt-code confirmation,
   a sweep flags the match for admin. It **flags only — it never refunds.**
3. **An admin decides.** Every actual movement of money is a human decision.

Taken as a default because the question went unanswered. Correctable.

### Why this default

- The owner is the only party who knows the document did not arrive, so they must
  be able to say so.
- The timer covers the owner who gives up and never comes back — otherwise a
  ghosted recovery is invisible and the finder's escrow is frozen forever.
- Routing both into the existing `flagged_for_review` / admin-queue machinery
  (`backend/app/routers/admin.py`) reuses the established pattern for anything
  money- or fraud-adjacent rather than inventing a parallel one.

**Fully automatic self-serve refund was rejected outright.** A refund is
irreversible and Seeker cannot distinguish a lying owner from a ghosting finder.
The codebase already resolves this exact tension the same way — `verify_face`
deliberately does *not* release the claim lock on a failed selfie, flagging for a
human instead (`backend/app/routers/matches.py:529`), and
`claims.expire_stale_claims` deliberately excludes attempts-exhausted claims so a
suspicious collapse reaches an admin rather than the next claimant
(`backend/app/claims.py:191`). Auto-flag plus human confirm is the house style for
anything with no undo.

*Admin-only with out-of-band support contact* was rejected because it leaves the
owner with no in-product entitlement and no record of the complaint.

### The cost this default accepts

Refunds become **operations-bound**: they move at the speed of a human working a
queue, and an unstaffed queue means a wronged owner waits indefinitely. That is
accepted as strictly better than an automated system paying out on an unverifiable
claim, but it is a real staffing obligation, not a free choice — and it should be
named in the launch checklist alongside the police/Posta operating procedures.

### Not yet decided (deliberately deferred)

- **N**, the timer length. Needs a real courier delivery-time distribution to set
  defensibly; picking a number now would be invention. Suggest starting at 14 days
  and tuning once there is delivery data.
- **Courier fee liability** when the courier is the failing party. No courier
  payout leg exists at all (the fee is collected and never disbursed), so this
  cannot be settled coherently until courier contracts exist.
- **Refund rail.** Daraja B2C is not integrated
  (`backend/app/routers/wallet.py:148` hard-refuses outside sandbox), so an
  approved refund can only be recorded as an operator obligation, not paid. The
  ledger entry must therefore be built to outlive the missing rail: record the
  refund as owed, settle it when B2C exists.

---

## 4. Finder trust and possession evidence — DEFAULTED

**A photo is required on every posted found document. No image, no listing.**

Taken as a default because the question went unanswered. Correctable.

### The hole this closes

Posting requires only a verified phone number (`require_verified_phone` on
`create_found_id`, `backend/app/routers/found_ids.py:92`). Verified by grep:

- No KYC on the finder, and no per-account cap on open posts.
- Nothing anywhere checks that the finder physically holds the document.
- `image` is optional (`UploadFile | None = File(default=None)`) and OCR failure
  never blocks a post (`found_ids.py:148`).
- `matches.fraud_confirmed_at` (`backend/app/models.py:401`) is defined and
  **never assigned anywhere** — the schema anticipates fraud that no code detects.

So the cheapest fraud in Seeker today needs no document at all: type a real name
and ID number copied off any document seen once, post with no photo, wait for a
desperate owner to search that exact number, collect KES 100. The owner pays
before any evidence the document exists.

### Why this default

The blur-and-redact pipeline already exists (`save_image_pair`), so requiring a
photo adds no new privacy surface — it reuses the path that already separates the
public blurred image from the private original. It converts the zero-cost fraud
into one requiring the physical document in hand.

- *Fully refundable until handover* — rejected as the default because it
  contradicts §1, which the user decided: the finder's KES 100 is non-refundable
  on proven dispatch.
- *Per-finder posting cap until first successful recovery* — a good idea and
  **not mutually exclusive** with this one; deferred rather than rejected. It needs
  a trust/reputation model that does not exist yet.
- *Leave as is* — rejected. Phone verification stops nothing here: a SIM costs
  about KES 50, and the admin queue only sees claims that reach review, not
  fabricated listings that never get claimed.

### The cost this default accepts

It blocks a genuine finder whose camera is broken or whose data is too expensive
to upload an image, in a market where that is not a rare edge case. Accepted
because the alternative is a product where inventing listings is free.

It also does **not** prove possession — only that the finder had the document in
front of a camera once. A photo taken and reused later, or a photo of someone
else's photo, still passes. This raises the floor; it is not a fraud solution.


Sections 2 and 3 are DEFAULTED, not chosen. Both are cheap to reverse *now* and
expensive to reverse after code is written against them. Worth an explicit yes/no
before the refund work starts:

- **§2** — proof of dispatch is a courier tracking number the finder types in,
  verified by a human against the courier's site. Accepts that a fabricated
  number will pass unnoticed at least once.
- **§3** — refunds are owner-disputed or timer-flagged, and *always* settled by an
  admin. Nothing pays out automatically. Accepts an ongoing staffing obligation.

The unanswered question underneath both, which is worth deciding before either
is built: **who eats the courier fee when the courier is the party that failed?**
Seeker collects it (`transactions.delivery_fee`) and disburses it to nobody — there
is no courier payout leg in the codebase at all. Until courier contracts exist,
any answer is a guess, so this is deliberately left open rather than defaulted.

---

## 5. Pricing shape — DEFAULTED

**Flat KES 200 recovery fee for every document type, split 50/50, for launch.
Revisit with data, not intuition.**

Taken as a default because the question went unanswered. Correctable.

### What the numbers actually are

- Recovery fee: flat `payment_amount_minor = 20000` (KES 200),
  `platform_share_ratio = 0.5` (`backend/app/config.py:72`). Identical for a
  student ID and a passport.
- Courier fee (`_quote`, `backend/app/routers/deliveries.py:51`): KES 150 if
  "nairobi" appears in the combined pickup/destination string, otherwise KES 300.
  Meetup is free.

So an upcountry recovery costs the owner **KES 500 all-in, 60% of which is
shipping**, while the finder earns KES 100 either way — the same as for a
ten-minute Nairobi handover.

### Why this default

A single memorable price is a genuine trust asset for a service people will
already suspect of being a scam. "KES 200 to get your ID back" is explainable in
one sentence; a tiered table is not. There is also no usage data yet on which
document types actually flow through, so any tier boundary would be invented.

- *Tier by document type* — deferred, not rejected on merit. It is defensible on
  value (a title deed matters more than a gym card) but invites the reading that
  Seeker charges more from people in worse trouble, which is a bad story for this
  particular product. Revisit once real document-type distribution exists.
- *Raise the finder's cut for long-distance recoveries* — the most likely first
  change once there is data. Held back only because `split_amount`
  (`backend/app/payments.py:52`) is currently a pure function of one ratio, and
  distance-varying splits need the ledger to record *which* ratio applied to each
  transaction or historical earnings become unauditable.
- *Subsidise courier out of the platform's KES 100* — rejected for launch: on an
  upcountry job the platform's entire KES 100 is less than the KES 300 courier
  fee, so the subsidy would run at a loss on exactly the recoveries that are
  hardest to serve.

### The cost this default accepts

**Upcountry finders are underpaid relative to effort, and upcountry owners pay
substantially more in total.** Flat pricing is regressive with distance here.
Accepted for launch on the grounds that it is reversible in one config value,
whereas a price *increase* after launch is far harder to walk back than a price
cut.

Flagged risk: if early volume skews upcountry, this default is wrong and should be
revisited immediately rather than at some scheduled review.

---

## 6. Data protection compliance — DEFAULTED (hard launch blocker)

**ODPC registration as a data controller and a published privacy policy are
treated as hard launch blockers, ranked above every technical gap in this
document. No public launch without both.**

Taken as a default because the question went unanswered. Correctable — but this is
the one section where overriding the default should involve a lawyer, not a
preference.

### Why this outranks the technical gaps

Seeker's database is, in substance, a collection of other people's identity
documents — full names, full ID numbers, photographs, and dates of birth — uploaded
by strangers, about people who never registered and never consented. That is
squarely within the scope of Kenya's Data Protection Act 2019.

Every other gap in this file costs money when it bites. This one is the only
category that can end the business or create personal exposure, and being early or
in beta is not a defence under the Act.

### What exists and what does not

Present, and genuinely good:

- Automated retention (`backend/app/retention.py`): images purged 30 days after
  return, unclaimed documents archived and flagged for police transfer at 90 days,
  notifications 180 days, audit log 730 days (`backend/app/config.py:63`).
- Server-side redaction, blurred public images, private pickup addresses,
  in-memory-only selfie handling, 404-not-403 on foreign records.

Absent — verified by search across the repo:

- **No terms of service.**
- **No privacy policy.**
- **No consent artifact of any kind**, and no lawful-basis statement. Consent from
  the document holder is structurally impossible here (they are absent by
  definition), so the lawful basis has to be something else — legitimate interest
  or a legal obligation to return found property — and that argument needs to be
  made explicitly by someone qualified, not assumed.
- No data subject access/erasure path for the *document holder*, who is the person
  with the strongest claim under the Act and the only party with no account.

### The police assumption

`retention.py:66` notifies the finder to hand an unclaimed document to a police
station and record the reference in Seeker. The product therefore already assumes
an operating relationship with the Kenya Police and a procedure for how a Seeker
user presents a document at a station. As far as this repository shows, that
relationship does not exist. An unclaimed document with nowhere lawful to go is a
compliance problem that grows with volume.

### Recommended sequence

1. Engage a Kenyan data protection lawyer. Everything below depends on their view.
2. Register with the ODPC as a data controller.
3. Publish a privacy policy and terms, with the lawful basis stated.
4. Build a holder-facing erasure/objection route — the gap most likely to generate
   a complaint, because the affected person cannot currently reach Seeker at all.
5. Establish the police procedure before relying on the 90-day transfer.

### Explicit limit

This is an engineering assessment of what the code does and does not contain. It
is **not legal advice**, and the ranking above should be validated by counsel
rather than taken from this file.

---

## Awaiting confirmation

Section 1 is the user's decision. **Sections 2–6 are DEFAULTED** — taken because
the question went unanswered, each cheap to reverse now and progressively more
expensive once code is written against it.

| § | Default taken | Cost it accepts |
|---|---|---|
| 2 | Proof of dispatch = courier tracking number, human-verified | A fabricated number passes unnoticed at least once |
| 3 | Refunds owner-disputed or timer-flagged, **always** admin-settled | Ongoing staffing obligation; unstaffed queue = owner waits |
| 4 | Photo required on every post | Blocks a genuine finder with no working camera |
| 5 | Flat KES 200, 50/50 split, for launch | Regressive with distance: upcountry finders underpaid |
| 6 | ODPC registration + privacy policy are hard launch blockers | Delays launch — deliberately |

### Still genuinely undecided

Left open rather than defaulted, because any answer now would be invention:

- **Who eats the courier fee when the courier is the failing party.** Seeker
  collects `transactions.delivery_fee` and disburses it to nobody — there is no
  courier payout leg in the codebase at all. Unanswerable until courier contracts
  exist.
- **N, the dispatch timer length** (§3). Needs a real courier delivery-time
  distribution. Suggested starting point 14 days.
- **The refund rail.** Daraja B2C is not integrated, so an approved refund can only
  be recorded as an operator obligation. The ledger must be built to outlive the
  missing rail.

### Ranked next work, on these defaults

1. **§6 compliance** — outranks everything technical; blocks launch, not code.
2. **Admin-forced escrow release** — §1 is currently unimplementable without it
   (`confirm_handover` requires the owner's receipt code, which a ghosted owner
   never gives).
3. **Refund ledger + `DeliveryStatus.DISPUTED` wiring** — record refunds as owed
   now, settle when a payout rail exists.
4. **`wallet.py:148` coupling** — the payout guard keys on `otp_provider`, so
   enabling real SMS silently switches withdrawals from "instantly paid" to
   "always 503". Two unrelated switches should not be one switch.
5. **Require photo on post** (§4) — small change, closes the zero-cost fraud.

