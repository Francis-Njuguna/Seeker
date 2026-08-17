"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import Base, SessionLocal, engine
from app.retention import run_retention_sweep
from app.routers import (
    admin,
    auth,
    deliveries,
    earnings,
    found_ids,
    lost_alerts,
    matches,
    messages,
    notifications,
    payments,
    search,
    wallet,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# create_all only creates missing *tables*, so columns added to an existing
# model are invisible to it. Until this project takes on Alembic, new columns
# are applied here with IF NOT EXISTS, which is idempotent and safe to re-run.
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("matches", "verification_status", "VARCHAR(32) NOT NULL DEFAULT 'unverified'"),
    ("matches", "verification_method", "VARCHAR(32)"),
    ("matches", "verification_document_type", "VARCHAR(64)"),
    ("matches", "verification_score", "DOUBLE PRECISION"),
    ("matches", "verification_detail", "TEXT"),
    ("matches", "verification_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("matches", "verified_at", "TIMESTAMPTZ"),
    ("users", "role", "VARCHAR(32) NOT NULL DEFAULT 'seeker'"),
    ("users", "phone_number", "VARCHAR(20)"),
    ("users", "phone_verified_at", "TIMESTAMPTZ"),
    ("users", "preferred_language", "VARCHAR(8) NOT NULL DEFAULT 'en'"),
    ("found_ids", "face_embedding", "BYTEA"),
    ("found_ids", "face_embedding_model", "VARCHAR(64)"),
    (
        "matches",
        "face_verification_status",
        "VARCHAR(32) NOT NULL DEFAULT 'unverified'",
    ),
    ("matches", "face_confidence", "DOUBLE PRECISION"),
    ("matches", "face_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("matches", "face_verified_at", "TIMESTAMPTZ"),
    ("matches", "flagged_for_review", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("matches", "flag_reason", "TEXT"),
    # Claim lock bookkeeping. Expiry is measured from claim_started_at, so an
    # existing row that predates this column falls back to created_at rather
    # than becoming un-expirable — see ``claims.expire_stale_claims``.
    ("matches", "claim_started_at", "TIMESTAMPTZ"),
    ("matches", "claim_expired_at", "TIMESTAMPTZ"),
    # Structured document classification, alongside the free-form document_type
    # the finder typed. Kept separate so an unrecognised document keeps the
    # finder's own words instead of being coerced into the vocabulary.
    ("found_ids", "document_class", "VARCHAR(48)"),
    ("found_ids", "classification_confidence", "DOUBLE PRECISION"),
    ("found_ids", "classification_source", "VARCHAR(32)"),
    ("found_ids", "ocr_fields", "TEXT"),
    ("found_ids", "holder_date_of_birth", "DATE"),
    ("found_ids", "reference_face_detected", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("found_ids", "pickup_address", "TEXT"),
    ("found_ids", "pickup_area", "VARCHAR(255)"),
    ("found_ids", "images_purged_at", "TIMESTAMPTZ"),
    ("found_ids", "police_transfer_due_at", "TIMESTAMPTZ"),
    ("found_ids", "police_transferred_at", "TIMESTAMPTZ"),
    ("found_ids", "police_reference", "VARCHAR(128)"),
    ("matches", "admin_review_required", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("matches", "finder_approved_at", "TIMESTAMPTZ"),
    ("matches", "admin_approved_at", "TIMESTAMPTZ"),
    ("matches", "admin_reviewed_by", "UUID"),
    ("matches", "admin_review_note", "TEXT"),
    ("matches", "fraud_confirmed_at", "TIMESTAMPTZ"),
    ("transactions", "recovery_fee", "INTEGER NOT NULL DEFAULT 20000"),
    ("transactions", "delivery_fee", "INTEGER NOT NULL DEFAULT 0"),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MVP schema bootstrap. A production deployment would run Alembic
    # migrations here instead.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, column, ddl in ADDED_COLUMNS:
            await conn.execute(
                text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}')
            )
    logger.info("database schema ready")
    retention_task = None
    if os.getenv("SEEKER_TESTING") != "1":
        async def retention_loop() -> None:
            while True:
                try:
                    async with SessionLocal() as session:
                        await run_retention_sweep(session)
                except Exception:  # noqa: BLE001 - keep the service alive and retry
                    logger.exception("retention sweep failed")
                await asyncio.sleep(6 * 60 * 60)

        retention_task = asyncio.create_task(retention_loop())
    yield
    if retention_task is not None:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(
    title="Seeker",
    description="Lost and found for ID cards and important documents.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(found_ids.router)
app.include_router(search.router)
app.include_router(lost_alerts.router)
app.include_router(matches.router)
app.include_router(deliveries.router)
app.include_router(messages.router)
app.include_router(notifications.router)
app.include_router(payments.router)
app.include_router(admin.router)
app.include_router(earnings.router)
app.include_router(wallet.router)


@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
