"""SQLAlchemy models.

Tables: users, found_ids, lost_alerts, matches, transactions, earnings,
notifications.

Status columns are plain VARCHAR validated by the StrEnums below rather than
native Postgres ENUM types. That is deliberate: new states can be added to the
Python enums without an ``ALTER TYPE`` migration on a live table — which is
exactly how the payment states below were added.
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
    LargeBinary,
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


class UserRole(enum.StrEnum):
    """Roles are functional, not exclusive.

    Everyone can both find and seek; this only decides which dashboard is
    offered. ``ADMIN`` is assigned out of band — there is no self-service path
    to it.
    """

    SEEKER = "seeker"
    FINDER = "finder"
    ADMIN = "admin"


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

    An approved claim lands on ``AWAITING_PAYMENT`` and only reaches ``CLAIMED``
    once the payment provider's webhook confirms the money arrived. Because full
    record access is keyed on CLAIMED/COMPLETED, parking an approved claim here
    is what withholds the document details until payment clears.
    """

    MATCHED = "matched"
    CLAIM_REQUESTED = "claim_requested"
    AWAITING_PAYMENT = "awaiting_payment"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MatchSource(enum.StrEnum):
    SEARCH = "search"
    ALERT_AUTO = "alert_auto"


class VerificationStatus(enum.StrEnum):
    """Outcome of the claimant's document check.

    ``UNVERIFIED`` is the default: the seeker has not uploaded their own
    document yet. ``FAILED`` is kept rather than reset to unverified so repeated
    mismatches are visible to the finder deciding the claim.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"


class VerificationMethod(enum.StrEnum):
    ID_NUMBER_TYPED = "id_number_typed"
    DOCUMENT_IMAGE = "document_image"
    FACE_SELFIE = "face_selfie"


class FaceVerificationStatus(enum.StrEnum):
    """Outcome of comparing the claimant's selfie to the face on the record.

    ``NO_FACE_ON_RECORD`` is kept distinct from ``FAILED``: the claimant did
    nothing wrong, the posted photo simply had no detectable face to compare
    against, and they should be sent down another proof path rather than told
    they failed.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    NO_FACE_ON_RECORD = "no_face_on_record"
    NO_FACE_IN_SELFIE = "no_face_in_selfie"


class TransactionStatus(enum.StrEnum):
    PENDING = "pending"
    INITIATED = "initiated"
    PAID = "paid"
    FAILED = "failed"


UUIDCol = PgUUID(as_uuid=True)
TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.SEEKER, index=True)
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

    # Face embedding derived from the posted photo, used to check a claimant's
    # selfie. Computed once at upload. The face is already inside an image we
    # store, so this adds no new class of data, and an embedding is one-way —
    # the photo cannot be reconstructed from it.
    face_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    face_embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)

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

    # Claimant-side verification. The seeker uploads their own copy of the
    # document; OCR on it is compared against the finder's record. Images are
    # never stored: only the outcome is kept.
    verification_status: Mapped[str] = mapped_column(
        String(32), default=VerificationStatus.UNVERIFIED
    )
    verification_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_document_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    verification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_attempts: Mapped[int] = mapped_column(default=0)
    verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    # Selfie check against the face on the posted document. The selfie itself is
    # never stored; only this verdict survives the request.
    face_verification_status: Mapped[str] = mapped_column(
        String(32), default=FaceVerificationStatus.UNVERIFIED
    )
    face_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    face_attempts: Mapped[int] = mapped_column(default=0)
    face_verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    # A failed face check does not silently vanish: it queues for a human.
    flagged_for_review: Mapped[bool] = mapped_column(default=False, index=True)
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    claim_requested_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    found_id: Mapped[FoundId] = relationship(back_populates="matches")
    lost_alert: Mapped[LostAlert | None] = relationship(back_populates="matches")

    __table_args__ = (
        UniqueConstraint("found_id_id", "seeker_user_id", name="uq_match_found_seeker"),
    )


class Transaction(Base):
    """A claim payment.

    Amounts are integers in the currency's minor unit (200 KES is stored as
    20000). Floats have no place in a ledger.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    match_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    payer_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    finder_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    amount: Mapped[int] = mapped_column()
    platform_share: Mapped[int] = mapped_column()
    finder_share: Mapped[int] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="KES")

    status: Mapped[str] = mapped_column(
        String(32), default=TransactionStatus.PENDING, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="mock")

    # Unique on purpose: this is what makes webhook replay idempotent, and
    # providers do retry.
    loop_reference: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    loop_raw_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class Earning(Base):
    """What a finder is owed for one returned document.

    Derivable from ``transactions``, but written explicitly when payment clears
    so there is a durable record of what was owed and why, independent of any
    later change to how the split is calculated.
    """

    __tablename__ = "earnings"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=_uuid)
    finder_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), unique=True
    )
    match_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    found_id_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("found_ids.id", ondelete="CASCADE"), index=True
    )

    amount: Mapped[int] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="KES")

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


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
