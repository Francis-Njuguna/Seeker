"""SQLAlchemy models: users, found_ids, lost_alerts, matches, notifications.

Status columns are plain VARCHAR validated by the StrEnums below rather than
native Postgres ENUM types. That is deliberate: payment states can be added to
the Python enums later without an ``ALTER TYPE`` migration on a live table.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FoundIdStatus(enum.StrEnum):
    POSTED = "posted"
    MATCHED = "matched"
    CLAIM_REQUESTED = "claim_requested"
    CLAIMED = "claimed"
    RETURNED = "returned"
    ARCHIVED = "archived"


class LostAlertStatus(enum.StrEnum):
    ACTIVE = "active"
    MATCHED = "matched"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class MatchStatus(enum.StrEnum):
    """Lifecycle of a match.

    ``AWAITING_PAYMENT`` is reserved and unused in the MVP. It exists so a
    payment step can be inserted between CLAIM_REQUESTED and CLAIMED later
    without touching the schema.
    """

    MATCHED = "matched"
    CLAIM_REQUESTED = "claim_requested"
    AWAITING_PAYMENT = "awaiting_payment"  # reserved for future payment flow
    CLAIMED = "claimed"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MatchSource(enum.StrEnum):
    SEARCH = "search"
    ALERT_AUTO = "alert_auto"


UUIDCol = PgUUID(as_uuid=True)
TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    found_ids: Mapped[list[FoundId]] = relationship(back_populates="finder")
    lost_alerts: Mapped[list[LostAlert]] = relationship(back_populates="seeker")


class FoundId(Base):
    """An ID document someone found and posted."""

    __tablename__ = "found_ids"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    finder_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    document_type: Mapped[str] = mapped_column(String(64))
    holder_name: Mapped[str] = mapped_column(String(255))
    holder_name_normalized: Mapped[str] = mapped_column(String(255), index=True)
    id_number: Mapped[str] = mapped_column(String(128))
    id_number_normalized: Mapped[str] = mapped_column(String(128), index=True)

    # OCR provenance
    ocr_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    fields_edited_by_finder: Mapped[bool] = mapped_column(default=False)

    # Stored images. The blurred derivative is what unauthenticated previews use.
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_blurred_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    found_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    found_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), default=FoundIdStatus.POSTED, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    finder: Mapped[User] = relationship(back_populates="found_ids")
    matches: Mapped[list[Match]] = relationship(back_populates="found_id")


class LostAlert(Base):
    """A standing request: notify me if this document turns up."""

    __tablename__ = "lost_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    seeker_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    holder_name: Mapped[str] = mapped_column(String(255))
    holder_name_normalized: Mapped[str] = mapped_column(String(255), index=True)
    id_number: Mapped[str] = mapped_column(String(128))
    id_number_normalized: Mapped[str] = mapped_column(String(128), index=True)

    status: Mapped[str] = mapped_column(
        String(32), default=LostAlertStatus.ACTIVE, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    last_notified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    seeker: Mapped[User] = relationship(back_populates="lost_alerts")
    matches: Mapped[list[Match]] = relationship(back_populates="lost_alert")

    __table_args__ = (
        UniqueConstraint(
            "seeker_user_id", "id_number_normalized", name="uq_alert_seeker_idnum"
        ),
    )


class Match(Base):
    """Link between a found document and the seeker who claims it."""

    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    found_id_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("found_ids.id", ondelete="CASCADE"), index=True
    )
    lost_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lost_alerts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    seeker_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    source: Mapped[str] = mapped_column(String(32), default=MatchSource.SEARCH)
    matched_on: Mapped[str] = mapped_column(String(32), default="id_number")

    status: Mapped[str] = mapped_column(
        String(32), default=MatchStatus.MATCHED, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    claim_requested_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    found_id: Mapped[FoundId] = relationship(back_populates="matches")
    lost_alert: Mapped[LostAlert | None] = relationship(back_populates="matches")

    __table_args__ = (
        UniqueConstraint("found_id_id", "seeker_user_id", name="uq_match_found_seeker"),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("matches.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    email_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


Index("ix_found_ids_idnum_status", FoundId.id_number_normalized, FoundId.status)
Index("ix_lost_alerts_idnum_status", LostAlert.id_number_normalized, LostAlert.status)
