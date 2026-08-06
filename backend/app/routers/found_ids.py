"""Finder flow: OCR preview, posting a found document, serving images."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, to_preview
from app.matching import match_new_found_id
from app.models import FoundId, Match, MatchStatus, User
from app.normalize import normalize_id_number, normalize_name
from app.ocr import OcrUnavailableError, extract_fields
from app.schemas import FoundIdCreate, FoundIdOwner, OcrPreviewOut
from app.storage import InvalidImageError, load_image, resolve_path, save_image_pair

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
    found_location: str | None = Form(default=None),
    found_note: str | None = Form(default=None),
    run_ocr: bool = Form(default=True),
    image: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
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

    payload = FoundIdCreate(
        document_type=document_type,
        holder_name=holder_name,
        id_number=id_number,
        found_location=found_location,
        found_note=found_note,
    )

    image_path = blurred_path = None
    ocr_text = None
    ocr_confidence = None
    fields_edited = False

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

    found = FoundId(
        finder_user_id=user.id,
        document_type=payload.document_type.strip().lower().replace(" ", "_"),
        holder_name=payload.holder_name,
        holder_name_normalized=normalize_name(payload.holder_name),
        id_number=payload.id_number,
        id_number_normalized=normalized_id,
        ocr_raw_text=ocr_text,
        ocr_confidence=ocr_confidence,
        fields_edited_by_finder=fields_edited,
        image_path=image_path,
        image_blurred_path=blurred_path,
        found_location=payload.found_location,
        found_note=payload.found_note,
    )
    db.add(found)
    await db.flush()

    await match_new_found_id(db, found)
    await db.commit()
    await db.refresh(found)

    out = FoundIdOwner.model_validate(found)
    out.image_url = f"/api/found-ids/{found.id}/image" if found.image_path else None
    return out


@router.get("/mine", response_model=list[FoundIdOwner])
async def list_my_found_ids(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[FoundIdOwner]:
    result = await db.execute(
        select(FoundId)
        .where(FoundId.finder_user_id == user.id)
        .order_by(FoundId.created_at.desc())
    )
    items = []
    for found in result.scalars().all():
        out = FoundIdOwner.model_validate(found)
        out.image_url = f"/api/found-ids/{found.id}/image" if found.image_path else None
        items.append(out)
    return items


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


@router.get("/{found_id}")
async def get_found_id(
    found_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the full record to those entitled to it, redacted otherwise."""
    found = await _load_found(db, found_id)
    if await _viewer_has_full_access(db, found, user):
        out = FoundIdOwner.model_validate(found)
        out.image_url = f"/api/found-ids/{found.id}/image" if found.image_path else None
        return out
    return to_preview(found)


@router.get("/{found_id}/image/blurred")
async def get_blurred_image(
    found_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    found = await _load_found(db, found_id)
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
    found = await _load_found(db, found_id)
    if not found.image_path:
        raise HTTPException(status_code=404, detail="No image on this record")
    if not await _viewer_has_full_access(db, found, user):
        raise HTTPException(
            status_code=403,
            detail="The full image is available after your claim is approved",
        )
    return FileResponse(resolve_path(found.image_path), media_type="image/jpeg")
