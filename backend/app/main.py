"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import Base, engine
from app.routers import (
    admin,
    auth,
    earnings,
    found_ids,
    lost_alerts,
    matches,
    notifications,
    payments,
    search,
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
    yield
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
app.include_router(notifications.router)
app.include_router(payments.router)
app.include_router(admin.router)
app.include_router(earnings.router)


@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
