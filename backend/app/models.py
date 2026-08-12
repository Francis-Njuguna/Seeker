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


# The only statuses at which a found document is discoverable by the public.
#
# A whitelist, not a blacklist, and deliberately so: this list previously read
# "everything except claimed/returned/archived", which meant CLAIM_REQUESTED —
# added to the enum later — was public by default, and a document under active
# claim stayed searchable by anyone. Enumerating what *is* visible fails closed,
# so a state added tomorrow is hidden until someone decides otherwise here.
#
# Every discovery path must filter on this: see ``matching.find_by_id_number``,
# ``matching.find_by_name``, and the direct-fetch guard in ``routers/found_ids``.
PUBLICLY_SEARCHABLE_FOUND_STATUSES: frozenset[str] = frozenset(
    {FoundIdStatus.POSTED, FoundIdStatus.MATCHED}
)


class LostAlertStatus(enum.StrEnum):
    ACTIVE = "active"
    MATCHED = "matched"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class MatchStatus(enum.StrEnum):
    """Lifecycle of a match.

    ``CLAIM_PENDING`` is the lock-holding state. A seeker who starts a claim
    takes the document out of public search *before* proving anything, because
    the proof steps (OCR review, then a selfie compared against the face on the
    document) are a multi-request workflow and the document must not stay
    claimable by someone else while they run. Proof is required to leave this
    state, not to enter it — see ``routers/matches.py``.

    An approved claim lands on ``AWAITING_PAYMENT`` and only reaches ``CLAIMED``
    once the payment provider's webhook confirms the money arrived. Because full
    record access is keyed on CLAIMED/COMPLETED, parking an approved claim here
    is what withholds the document details until payment clears.
    """

    MATCHED = "matched"
    CLAIM_PENDING = "claim_pending"
    CLAIM_REQUESTED = "claim_requested"
    AWAITING_PAYMENT = "awaiting_payment"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"


# States in which a match holds the exclusive claim lock on its document. While
# any match on a document is in one of these, no other seeker may start a claim
# and the document is absent from public search.
LOCK_HOLDING_MATCH_STATUSES: frozenset[str] = frozenset(
    {
        MatchStatus.CLAIM_PENDING,
        MatchStatus.CLAIM_REQUESTED,
        MatchStatus.AWAITING_PAYMENT,
        MatchStatus.CLAIMED,
        MatchStatus.COMPLETED,
    }
)


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

    ``UNVERIFIED`` is the "selfie required, none submitted yet" state.

    Four of these are deliberately *not* ``FAILED``, because the claimant did
    nothing wrong in any of them and each sends them somewhere different:

    * ``NO_FACE_ON_RECORD`` — the posted photo has no detectable face to compare
      against, so no selfie can ever settle this claim. Another proof path.
    * ``NO_FACE_IN_SELFIE`` — retry with a clearer photo.
    * ``TOO_MANY_FACES`` — the selfie has more than one face, which is ambiguous
      rather than wrong. Recorded distinctly so an operator reading the queue can
      tell "could not be assessed" from "assessed and did not match".
    * ``ATTEMPTS_EXHAUSTED`` — assessed and failed, repeatedly, and now closed to
      further selfies. Distinct from ``FAILED`` so the admin queue shows which
      claims a claimant can still resolve themselves.

    There is no PROCESSING state: the comparison is synchronous within one
    request, so a status meaning "in flight" would never be observable.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    NO_FACE_ON_RECORD = "no_face_on_record"
    NO_FACE_IN_SELFIE = "no_face_in_selfie"
    TOO_MANY_FACES = "too_many_faces"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


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

    # Structured classification from the controlled vocabulary in ``documents.py``,
    # kept alongside ``document_type`` rather than replacing it. The finder types
    # ``document_type`` freely and it stays exactly as they wrote it; this column
    # holds the recognised class, or NULL when nothing was recognised. Collapsing
    # the two would either overwrite the finder's words or force every unusual
    # document into a bucket it does not belong in.
    document_class: Mapped[str | None] = mapped_column(String(48), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "ocr" when the classifier read it off the document, "finder" when the finder
    # chose it. Recorded because §12 requires knowing which fields a machine
    # produced and which a human asserted.
    classification_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # OCR provenance
    ocr_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # JSON blob of the per-field extraction: value, per-field confidence, and
    # which extractor produced it. Stored as TEXT rather than JSONB because it is
    # only ever read back whole, and TEXT needs no migration on older Postgres.
    ocr_fields: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # When the claimant took the exclusive lock on the document. Expiry is
    # measured from here, not from created_at: a match can sit at MATCHED
    # indefinitely without holding anything.
    claim_started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    claim_requested_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    # Set when a sweep expires an abandoned claim, so the reason a document
    # returned to circulation is on the record rather than inferred.
    claim_expired_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

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
