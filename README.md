# Seeker

Seeker is a mobile-responsive recovery service for lost Kenyan identity documents. One verified account can use separate Owner and Finder workspaces.

## Implemented MVP

- Kenyan phone registration and OTP verification, with a visible sandbox code.
- English or Kiswahili preference stored on the account.
- Finder posting for national IDs, passports, licences, certificates, cards and other documents.
- Private pickup address separated from the approximate public pickup area.
- Exact search by document number, or full name plus date of birth.
- Server-side redaction of names, document numbers and document images.
- Document-copy OCR and transient selfie comparison; production posting does not persist face embeddings.
- Finder review plus admin review for fallback and high-risk claims.
- Courier or staffed public meetup recovery quote.
- KES 200 recovery fee plus a separately calculated courier fee.
- M-Pesa Daraja STK Push adapter and a self-contained mock payment sandbox.
- KES 100 platform share and KES 100 finder reward. Courier money is not split.
- Finder reward held until the finder enters the owner's one-time receipt code.
- Private claim-scoped messaging.
- Finder wallet, KES 100 minimum withdrawal and OTP verification for every payout number.
- Returned-document image purge after 30 days.
- Unclaimed documents removed from search after 90 days and tracked for police transfer.
- Admin UI for transactions, claims and manual-review decisions.

## Recovery Flow

1. A finder posts the document details, public area and exact private pickup address.
2. An owner searches exact document-specific details and reserves a possible match.
3. The owner provides a document copy and selfie where possible. Fallback evidence is manually reviewed.
4. The finder approves or rejects the claim. High-risk/fallback claims also require admin approval.
5. The owner selects courier or a staffed public meetup and receives a quote.
6. The owner pays KES 200 plus any courier charge.
7. Payment unlocks handover details and a private receipt code for the owner.
8. The finder dispatches or hands over the document.
9. After receiving the document, the owner gives the code to the finder.
10. The finder enters the code, completing recovery and releasing KES 100 to the wallet.

## Run Locally

Prerequisites: Docker Desktop and Node.js 22.

Start PostgreSQL and the FastAPI backend:

```powershell
docker compose up -d db backend
```

Seeker's backend uses `http://localhost:8000`. PostgreSQL uses host port `55432`.

Start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` to port 8000.

## Sandbox Payments

The default `PAYMENT_PROVIDER=mock` exercises the real payment state transition without moving money. The UI completes the mock callback automatically.

For Daraja sandbox STK Push, copy the values from `backend/.env.example` into the environment used by Docker and set:

```text
PAYMENT_PROVIDER=mpesa
MPESA_CONSUMER_KEY=...
MPESA_CONSUMER_SECRET=...
MPESA_SHORTCODE=...
MPESA_PASSKEY=...
MPESA_CALLBACK_URL=https://your-public-host/api/payments/webhook
```

The callback must be publicly reachable. `localhost` cannot receive Safaricom callbacks.

## Tests

The focused MVP contract test covers exact search, manual review, delivery quote, payment, receipt-code escrow, wallet and OTP withdrawal:

```powershell
docker compose run --rm -e SEEKER_TESTING=1 `
  -e TEST_DATABASE_URL=postgresql+asyncpg://seeker:seeker@db:5432/seeker_test `
  backend python -m pytest tests/test_mvp_recovery.py -q
```

Build the frontend with:

```powershell
cd frontend
npm run build
```

## Production Launch Blockers

This repository is a working sandbox MVP, not yet authorized for public production handling of real identity documents or money. Before public launch, Seeker still needs:

- A contracted SMS provider wired into `OTP_PROVIDER`; sandbox OTP must be disabled.
- Daraja production STK credentials and an HTTPS callback host.
- Daraja B2C credentials and callback processing for finder withdrawals. Production mode currently refuses to mark a payout paid without this integration.
- Contracted courier APIs, service-level terms and refund/reconciliation procedures. Current quotes are deterministic sandbox quotes for Posta Kenya/Super Metro Parcel.
- Written agreements and operating procedures with police stations, Posta branches and partner shops.
- Legal review under Kenya's Data Protection Act, terms of service, privacy notice, consent language and incident response.
- A proper migration system such as Alembic, encrypted secret management, backups, monitoring, audit logs and a shared rate limiter.
- End-to-end Kiswahili translations; the preference is stored, but most current interface copy is English.
- A tested dispute/refund workflow for courier and finder failures.

Do not switch to real public traffic until these blockers are closed.
