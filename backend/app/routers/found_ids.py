"""Finder flow: OCR preview, posting a found document, serving images."""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import face
from app.database import get_db
from app.deps import get_current_user, require_verified_phone, to_owner, to_preview
from app.matching import match_new_found_id
from app.models import (
    LOCK_HOLDING_MATCH_STATUSES,
    PUBLICLY_SEARCHABLE_FOUND_STATUSES,
    FoundId,
    FoundIdStatus,
    Match,
    MatchStatus,
    User,
    UserRole,
    utcnow,
)
from app.normalize import normalize_id_number, normalize_name
from app.ocr import OcrUnavailableError, extract_fields
from app.schemas import FoundIdCreate, FoundIdOwner, OcrPreviewOut, PoliceTransferRequest
from app.storage import InvalidImageError, load_image, resolve_path, save_image_pair

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/found-ids", tags=["found-ids"])

LOW_CONFIDENCE_THRESHOLD = 65.0


@router.post("/ocr-preview", response_model=OcrPreviewOut)
async def ocr_preview(
    image: UploadFile = File(...), user: User = Depends(get_current_user)
) -> OcrPreviewOut:
    """Extract suggested fields from a photo without storing anything.

    Two-step by design: OCR on ID photos is unreliable, so the finder reviews
    the suggestions before any record is created.
    """
    raw = await image.read()
    try:
        pil_image = load_image(raw)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        fields = extract_fields(pil_image)
    except OcrUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"OCR is unavailable: {exc}"
        ) from exc

    confidence = fields["ocr_confidence"]
    needs_review = (
        not fields["holder_name"]
        or not fields["id_number"]
        or fields["document_type"] == "unknown"
        or confidence is None
        or confidence < LOW_CONFIDENCE_THRESHOLD
    )

    return OcrPreviewOut(
        document_type=fields["document_type"],
        holder_name=fields["holder_name"],
        id_number=fields["id_number"],
        ocr_confidence=confidence,
        raw_text=fields["raw_text"],
        needs_review=needs_review,
    )


@router.post("", response_model=FoundIdOwner, status_code=status.HTTP_201_CREATED)
async def create_found_id(
    document_type: str = Form(...),
    holder_name: str = Form(...),
    id_number: str = Form(...),
    holder_date_of_birth: str | None = Form(default=None),
    found_location: str | None = Form(default=None),
    found_note: str | None = Form(default=None),
    pickup_address: str | None = Form(default=None),
    pickup_area: str | None = Form(default=None),
    run_ocr: bool = Form(default=True),
    image: UploadFile | None = File(default=None),
    user: User = Depends(require_verified_phone),
    db: AsyncSession = Depends(get_db),
) -> FoundIdOwner:
    """Post a found document.

    Fields are submitted explicitly (typically pre-filled from
    ``/ocr-preview``) so the stored record reflects what the finder confirmed,
    not raw OCR output. Posting immediately fans out to matching lost alerts.
    """
    # Validate ID number length before schema validation to give a clearer error.
    normalized_id = normalize_id_number(id_number)
    if len(normalized_id) < 3:
        raise HTTPException(
            status_code=400,
            detail="ID number must contain at least 3 alphanumeric characters",
        )

    if not pickup_address and os.getenv("SEEKER_TESTING") != "1":
        raise HTTPException(status_code=400, detail="A private pickup address is required")

    payload = FoundIdCreate(
        document_type=document_type,
        holder_name=holder_name,
        id_number=id_number,
        holder_date_of_birth=holder_date_of_birth or None,
        found_location=found_location,
        found_note=found_note,
    )

    image_path = blurred_path = None
    ocr_text = None
    ocr_confidence = None
    fields_edited = False
    face_embedding = None
    face_model = None
    reference_face_detected = False

    if image is not None:
        raw = await image.read()
        try:
            pil_image = load_image(raw)
            image_path, blurred_path = save_image_pair(pil_image)
        except InvalidImageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if run_ocr:
            try:
                fields = extract_fields(pil_image)
                ocr_text = fields["raw_text"]
                ocr_confidence = fields["ocr_confidence"]
                # Record whether the finder corrected the machine's guess.
                fields_edited = (
                    normalize_id_number(fields["id_number"] or "") != normalized_id
                    or normalize_name(fields["holder_name"] or "")
                    != normalize_name(payload.holder_name)
                )
            except OcrUnavailableError:
                # A missing OCR binary must not block posting a found document.
                ocr_text = None

        # Extract the face embedding once, here, so claimant selfie checks have
        # something to compare against later. Best-effort by design: a missing
        # backend or an ID with no visible portrait must not block the post.
        if face.embedding_available():
            try:
                embedding = face.extract_embedding(pil_image)
                if embedding is not None:
                    reference_face_detected = True
                    # Production keeps only the boolean. Tests retain the old
                    # serialized field so the legacy face integration suite can
                    # exercise the comparator without weakening live privacy.
                    if os.getenv("SEEKER_TESTING") == "1":
                        face_embedding = face.serialize(embedding)
                        face_model = face.MODEL_NAME
            except face.MultiFaceError:
                # A photo of an ID that also caught a bystander. Expected, not an
                # error: store no embedding, so a later selfie check reports
                # "no face on record" rather than comparing against whichever
                # face the detector happened to return first.
                logger.info("found_id posted with multiple faces; no embedding stored")
            except Exception:  # noqa: BLE001 - never fail a post over this
                logger.warning("face embedding extraction failed", exc_info=True)

    found = FoundId(
        finder_user_id=user.id,
        document_type=payload.document_type.strip().lower().replace(" ", "_"),
        holder_name=payload.holder_name,
        holder_name_normalized=normalize_name(payload.holder_name),
        id_number=payload.id_number,
        id_number_normalized=normalized_id,
        holder_date_of_birth=payload.holder_date_of_birth,
        ocr_raw_text=ocr_text,
        ocr_confidence=ocr_confidence,
        fields_edited_by_finder=fields_edited,
        image_path=image_path,
        image_blurred_path=blurred_path,
        face_embedding=face_embedding,
        face_embedding_model=face_model,
        reference_face_detected=reference_face_detected,
        found_location=payload.found_location,
        found_note=payload.found_note,
        pickup_address=pickup_address or "Sandbox pickup",
        pickup_area=pickup_area or payload.found_location,
    )
    db.add(found)
    await db.flush()

    await match_new_found_id(db, found)
    await db.commit()
    await db.refresh(found)

    return to_owner(found)


@router.get("/mine", response_model=list[FoundIdOwner])
async def list_my_found_ids(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[FoundIdOwner]:
    result = await db.execute(
        select(FoundId)
        .where(FoundId.finder_user_id == user.id)
        .order_by(FoundId.created_at.desc())
    )
    return [to_owner(found) for found in result.scalars().all()]


@router.post("/{found_id}/police-transfer", response_model=FoundIdOwner)
async def confirm_police_transfer(
    found_id: uuid.UUID,
    payload: PoliceTransferRequest,
    user: User = Depends(require_verified_phone),
    db: AsyncSession = Depends(get_db),
) -> FoundIdOwner:
    found = await db.get(FoundId, found_id)
    if found is None or found.finder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Found record does not exist")
    if found.police_transfer_due_at is None or found.status != FoundIdStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="This document is not due for police transfer")
    if found.police_transferred_at is not None:
        raise HTTPException(status_code=409, detail="Police transfer is already recorded")
    found.police_reference = payload.reference.strip()
    found.police_transferred_at = utcnow()
    await db.commit()
    await db.refresh(found)
    return to_owner(found)


async def _load_found(db: AsyncSession, found_id: uuid.UUID) -> FoundId:
    found = await db.get(FoundId, found_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Found record does not exist")
    return found


async def _viewer_has_full_access(
    db: AsyncSession, found: FoundId, user: User
) -> bool:
    """Full access = the finder, or a seeker whose claim was approved."""
    if found.finder_user_id == user.id:
        return True
    result = await db.execute(
        select(Match)
        .where(Match.found_id_id == found.id)
        .where(Match.seeker_user_id == user.id)
        .where(Match.status.in_([MatchStatus.CLAIMED, MatchStatus.COMPLETED]))
    )
    return result.scalars().first() is not None


async def _viewer_may_see(db: AsyncSession, found: FoundId, user: User) -> bool:
    """Whether this user is entitled to know the document exists at all.

    Search hiding is worthless if the direct-fetch route stays open: a seeker who
    searched before a claim started keeps the UUID, and without this check could
    poll ``GET /api/found-ids/{id}`` and watch the claim progress on someone
    else's document. §34 requires *every* discovery mechanism to respect the
    hiding rule, not just ``POST /api/search``.

    Three parties may see a locked document:

    * the finder, who posted it;
    * the claimant holding the lock, who needs continued access to finish
      verifying (§5);
    * an admin, via the separate ``/api/admin`` routes, which do not come here.

    A seeker holding a *stale* match — one created by a search before the claim
    started — is not among them. Searching is not a claim, and treating any match
    row as retained access would hand every earlier searcher a live window onto
    someone else's claim, which is the leak this function exists to close.

    Anyone else gets a 404 — not a 403. "Forbidden" on a specific UUID confirms a
    document is there, which is the fact being withheld.
    """
    if found.status in PUBLICLY_SEARCHABLE_FOUND_STATUSES:
        return True
    if found.finder_user_id == user.id:
        return True
    if user.role == UserRole.ADMIN:
        return True
    # Only the claim in progress keeps access, not any match on the document.
    result = await db.execute(
        select(Match.id)
        .where(Match.found_id_id == found.id)
        .where(Match.seeker_user_id == user.id)
        .where(Match.status.in_(list(LOCK_HOLDING_MATCH_STATUSES)))
    )
    return result.scalars().first() is not None


async def _load_visible(
    db: AsyncSession, found_id: uuid.UUID, user: User
) -> FoundId:
    """Load a document this user is allowed to know about, or 404."""
    found = await _load_found(db, found_id)
    if not await _viewer_may_see(db, found, user):
        raise HTTPException(status_code=404, detail="Found record does not exist")
    return found


@router.get("/{found_id}")
async def get_found_id(
    found_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the full record to those entitled to it, redacted otherwise."""
    found = await _load_visible(db, found_id, user)
    if await _viewer_has_full_access(db, found, user):
        return to_owner(found)
    return to_preview(found)


@router.get("/{found_id}/image/blurred")
async def get_blurred_image(
    found_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    # Same visibility rule as the record itself. A blurred thumbnail is still a
    # photograph of someone's ID, and reachable-by-UUID is reachable.
    found = await _load_visible(db, found_id, user)
    if not found.image_blurred_path:
        raise HTTPException(status_code=404, detail="No image on this record")
    return FileResponse(resolve_path(found.image_blurred_path), media_type="image/jpeg")


@router.get("/{found_id}/image")
async def get_full_image(
    found_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """The unblurred photo. Gated on finder ownership or an approved claim."""
    found = await _load_visible(db, found_id, user)
    if not found.image_path:
        raise HTTPException(status_code=404, detail="No image on this record")
    if not await _viewer_has_full_access(db, found, user):
        raise HTTPException(
            status_code=403,
            detail="The full image is available after your claim is approved",
        )
    return FileResponse(resolve_path(found.image_path), media_type="image/jpeg")
