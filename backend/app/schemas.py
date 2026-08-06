"""Request and response schemas.

Note the split between ``FoundIdPreview`` (redacted, what a seeker sees) and
``FoundIdOwner`` (full, only ever returned to the finder who posted it or a
seeker whose claim was approved).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth -------------------------------------------------------------------

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(min_length=1, max_length=120)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
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


class FoundIdOwner(ORMModel):
    """Unredacted view for the finder, or a seeker with an approved claim."""

    id: uuid.UUID
    document_type: str
    holder_name: str
    id_number: str
    found_location: str | None
    found_note: str | None
    status: str
    ocr_confidence: float | None
    fields_edited_by_finder: bool
    created_at: datetime
    updated_at: datetime
    image_url: str | None = None


# --- search -----------------------------------------------------------------

class SearchQuery(BaseModel):
    id_number: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=255)

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
    created_at: datetime
    claim_requested_at: datetime | None
    claimed_at: datetime | None


class MatchWithPreview(BaseModel):
    match: MatchOut
    preview: FoundIdPreview


class ClaimRequest(BaseModel):
    """Ownership proof. Must reproduce the full ID number, not just the tail."""

    id_number: str = Field(min_length=3, max_length=128)
    message: str | None = Field(default=None, max_length=1000)


class ClaimDecision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


# --- notifications ----------------------------------------------------------

class NotificationOut(ORMModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    match_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime
