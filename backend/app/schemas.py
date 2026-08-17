"""Request and response schemas.

Note the split between ``FoundIdPreview`` (redacted, what a seeker sees) and
``FoundIdOwner`` (full, only ever returned to the finder who posted it or a
seeker whose claim was approved).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth -------------------------------------------------------------------

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(min_length=1, max_length=120)
    phone_number: str | None = Field(default=None, max_length=20)
    preferred_language: str = Field(default="en", pattern="^(en|sw)$")


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    role: str
    phone_number: str | None = None
    phone_verified_at: datetime | None = None
    preferred_language: str = "en"
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- found ids --------------------------------------------------------------

class OcrPreviewOut(BaseModel):
    """OCR suggestions. The finder confirms or corrects these."""

    document_type: str
    holder_name: str | None
    id_number: str | None
    ocr_confidence: float | None
    raw_text: str
    needs_review: bool = Field(
        description="True when OCR confidence is low or a field is missing."
    )


class FoundIdCreate(BaseModel):
    document_type: str = Field(min_length=1, max_length=64)
    holder_name: str = Field(min_length=1, max_length=255)
    id_number: str = Field(min_length=3, max_length=128)
    holder_date_of_birth: date | None = None
    found_location: str | None = Field(default=None, max_length=255)
    found_note: str | None = Field(default=None, max_length=2000)

    @field_validator("holder_name", "id_number")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class FoundIdPreview(BaseModel):
    """Redacted view. Enough to recognise, not enough to impersonate."""

    id: uuid.UUID
    document_type: str
    holder_name_redacted: str
    id_number_redacted: str
    found_location: str | None
    status: str
    created_at: datetime
    blurred_image_url: str | None
    # Whether a face was detected on the posted document and an embedding stored.
    # A boolean, never the embedding: the client needs to know if the selfie step
    # is even possible, and nothing more. When false the UI must offer the ID
    # number or document-upload path instead of a selfie dropzone that can only
    # ever fail.
    has_reference_face: bool = False
    # Structured classification, when the document was recognised. Absent rather
    # than "unknown" so the UI can distinguish "not classified" from a class.
    document_class: str | None = None


class FoundIdOwner(ORMModel):
    """Unredacted view for the finder, or a seeker with an approved claim."""

    id: uuid.UUID
    document_type: str
    holder_name: str
    id_number: str
    holder_date_of_birth: date | None = None
    found_location: str | None
    found_note: str | None
    status: str
    ocr_confidence: float | None
    fields_edited_by_finder: bool
    created_at: datetime
    updated_at: datetime
    image_url: str | None = None
    has_reference_face: bool = False
    document_class: str | None = None
    classification_confidence: float | None = None
    classification_source: str | None = None
    images_purged_at: datetime | None = None
    police_transfer_due_at: datetime | None = None
    police_transferred_at: datetime | None = None
    police_reference: str | None = None


class PoliceTransferRequest(BaseModel):
    reference: str = Field(min_length=3, max_length=128)


# --- search -----------------------------------------------------------------

class SearchQuery(BaseModel):
    id_number: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    document_type: str | None = Field(default=None, max_length=64)
    date_of_birth: date | None = None

    @field_validator("id_number", "name")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SearchResult(BaseModel):
    found: bool
    matched_on: str | None = None
    results: list[FoundIdPreview] = Field(default_factory=list)
    match_ids: list[uuid.UUID] = Field(default_factory=list)
    suggestion: str


class OtpRequest(BaseModel):
    phone_number: str = Field(min_length=9, max_length=20)
    purpose: str = Field(default="phone_verification", pattern="^(phone_verification|withdrawal)$")


class OtpVerifyRequest(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=4, max_length=8)


class OtpChallengeOut(BaseModel):
    challenge_id: uuid.UUID
    expires_at: datetime
    sandbox_code: str | None = None


# --- lost alerts ------------------------------------------------------------

class LostAlertCreate(BaseModel):
    holder_name: str = Field(min_length=1, max_length=255)
    id_number: str = Field(min_length=3, max_length=128)
    document_type: str | None = Field(default=None, max_length=64)

    @field_validator("holder_name", "id_number")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class LostAlertOut(ORMModel):
    id: uuid.UUID
    holder_name: str
    id_number: str
    document_type: str | None
    status: str
    created_at: datetime
    last_notified_at: datetime | None


# --- matches ----------------------------------------------------------------

class MatchOut(ORMModel):
    id: uuid.UUID
    found_id_id: uuid.UUID
    lost_alert_id: uuid.UUID | None
    seeker_user_id: uuid.UUID
    source: str
    matched_on: str
    status: str
    verification_status: str
    verification_method: str | None
    verification_document_type: str | None
    verification_score: float | None
    verification_detail: str | None
    verification_attempts: int
    verified_at: datetime | None
    face_verification_status: str
    face_confidence: float | None
    face_attempts: int
    face_verified_at: datetime | None
    flagged_for_review: bool
    flag_reason: str | None
    created_at: datetime
    claim_started_at: datetime | None = None
    claim_requested_at: datetime | None
    claimed_at: datetime | None
    claim_expired_at: datetime | None = None
    admin_review_required: bool = False
    finder_approved_at: datetime | None = None
    admin_approved_at: datetime | None = None
    admin_review_note: str | None = None


class ClaimStartOut(BaseModel):
    """What the claimant gets back after taking the lock.

    ``expires_at`` is server-computed and authoritative. The UI shows a countdown
    from it, but the countdown is cosmetic — the release happens server-side (see
    ``claims.expire_stale_claims``) whether or not a browser is open.
    """

    match: MatchOut
    document: FoundIdPreview
    expires_at: datetime
    verification_required: bool
    next_step: str


class MatchWithPreview(BaseModel):
    """A match plus what the caller is entitled to see of the document.

    ``preview`` is always redacted. ``full`` is populated only once the claim is
    approved, which is what lets the UI swap the blurred thumbnail for the real
    photo without a second round trip.
    """

    match: MatchOut
    preview: FoundIdPreview
    full: FoundIdOwner | None = None


class ClaimRequest(BaseModel):
    """Ownership proof.

    Either reproduce the full ID number, or pass the document-image check first
    (``POST /api/matches/{id}/verify-identity``) and omit the number.
    """

    id_number: str | None = Field(default=None, max_length=128)
    message: str | None = Field(default=None, max_length=1000)


class DeliveryQuoteRequest(BaseModel):
    method: str = Field(pattern="^(courier|meetup)$")
    destination_address: str = Field(min_length=5, max_length=500)


class DeliveryQuoteOut(ORMModel):
    id: uuid.UUID
    method: str
    provider: str | None
    pickup_address: str
    destination_address: str
    suggested_public_location: str | None
    fee: int
    currency: str
    status: str


class DeliveryConfirmRequest(BaseModel):
    code: str = Field(min_length=4, max_length=8)


class DeliveryOut(DeliveryQuoteOut):
    tracking_reference: str | None = None
    delivered_at: datetime | None = None
    handover_code: str | None = None


class IdentityVerificationOut(BaseModel):
    """Result of checking the claimant's own document against the record."""

    verified: bool
    status: str
    score: float
    detail: str
    document_type: str | None
    id_number_matched: bool
    name_matched: bool
    attempts_remaining: int


class FaceVerificationOut(BaseModel):
    """Result of comparing the claimant's selfie against the face on the record."""

    verified: bool
    status: str
    confidence: float
    detail: str
    face_found_in_selfie: bool
    flagged_for_review: bool
    attempts_remaining: int


class ClaimDecision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


class AdminReviewDecision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class MessageOut(ORMModel):
    id: uuid.UUID
    match_id: uuid.UUID
    sender_user_id: uuid.UUID
    body: str
    created_at: datetime
    read_at: datetime | None


class WalletSummary(BaseModel):
    available: int
    pending: int
    withdrawn: int
    minimum_withdrawal: int
    currency: str


class WithdrawalRequest(BaseModel):
    amount: int = Field(gt=0)
    phone_number: str = Field(min_length=9, max_length=20)


class WithdrawalOut(ORMModel):
    id: uuid.UUID
    phone_number: str
    amount: int
    currency: str
    status: str
    provider_reference: str | None
    created_at: datetime


class WithdrawalStartOut(BaseModel):
    withdrawal: WithdrawalOut
    otp: OtpChallengeOut


# --- notifications ----------------------------------------------------------

class NotificationOut(ORMModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    match_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime
