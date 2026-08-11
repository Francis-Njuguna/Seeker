"""Match lifecycle: review a redacted preview, request a claim, approve it.

The status ladder is matched -> claim_requested -> awaiting_payment -> claimed
-> completed. Approval by the finder does **not** release the record: the claim
parks at ``AWAITING_PAYMENT`` until the payment provider's webhook confirms the
fee, and because full access is keyed on CLAIMED/COMPLETED that withholds the
document details automatically.

A seeker can prove ownership three ways — typing the full ID number, uploading
their own photo of the document, or passing a selfie check against the face on
the posted photo. Any one of them is enough; the finder still decides.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import face
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, to_owner, to_preview
from app.models import (
    FaceVerificationStatus,
    FoundId,
    FoundIdStatus,
    Match,
    MatchStatus,
    User,
    VerificationMethod,
    VerificationStatus,
    utcnow,
)
from app.normalize import normalize_id_number
from app.notifications import notify
from app.schemas import (
    ClaimDecision,
    ClaimRequest,
    FaceVerificationOut,
    IdentityVerificationOut,
    MatchOut,
    MatchWithPreview,
)
from app.storage import InvalidImageError, load_image
from app.verification import (
    MAX_ATTEMPTS,
    OcrUnavailableError,
    compare_against_record,
)

router = APIRouter(prefix="/api/matches", tags=["matches"])

# Statuses at which the seeker has earned the unredacted record.
FULL_ACCESS_STATUSES = (MatchStatus.CLAIMED, MatchStatus.COMPLETED)


def _with_document(match: Match, found: FoundId) -> MatchWithPreview:
    """Attach the full record once the claim has been approved."""
    return MatchWithPreview(
        match=MatchOut.model_validate(match),
        preview=to_preview(found),
        full=to_owner(found) if match.status in FULL_ACCESS_STATUSES else None,
    )


async def _load_match(db: AsyncSession, match_id: uuid.UUID) -> tuple[Match, FoundId]:
    match = await db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match does not exist")
    found = await db.get(FoundId, match.found_id_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Found record does not exist")
    return match, found


@router.get("/mine", response_model=list[MatchWithPreview])
async def list_my_matches(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[MatchWithPreview]:
    """Matches where the caller is the seeker, with redacted previews."""
    result = await db.execute(
        select(Match, FoundId)
        .join(FoundId, FoundId.id == Match.found_id_id)
        .where(Match.seeker_user_id == user.id)
        .order_by(Match.created_at.desc())
    )
    return [_with_document(m, f) for m, f in result.all()]


@router.get("/incoming", response_model=list[MatchWithPreview])
async def list_incoming_claims(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[MatchWithPreview]:
    """Claims awaiting the caller's decision as the finder."""
    result = await db.execute(
        select(Match, FoundId)
        .join(FoundId, FoundId.id == Match.found_id_id)
        .where(FoundId.finder_user_id == user.id)
        .where(Match.status == MatchStatus.CLAIM_REQUESTED)
        .order_by(Match.claim_requested_at.desc())
    )
    # The finder always sees their own document in full: they posted it.
    return [
        MatchWithPreview(
            match=MatchOut.model_validate(m), preview=to_preview(f), full=to_owner(f)
        )
        for m, f in result.all()
    ]


@router.post("/{match_id}/claim", response_model=MatchOut)
async def request_claim(
    match_id: uuid.UUID,
    payload: ClaimRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Request a claim, proving ownership with the full ID number or document upload.

    The seeker can prove ownership in two ways:
    1. Type the full ID number (the preview only shows the last two characters).
    2. Upload their own photo of the document (see ``POST /{id}/verify-identity``),
       pass the check, and submit a claim without the typed number.
    """
    match, found = await _load_match(db, match_id)
    if match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match does not exist")

    if match.status not in (MatchStatus.MATCHED, MatchStatus.REJECTED):
        raise HTTPException(
            status_code=409, detail=f"Claim cannot be started from status '{match.status}'"
        )

    # Any one of the three proofs is enough.
    if payload.id_number:
        if normalize_id_number(payload.id_number) != found.id_number_normalized:
            raise HTTPException(
                status_code=403, detail="The ID number does not match this document"
            )
        match.verification_method = VerificationMethod.ID_NUMBER_TYPED
    elif match.verification_status == VerificationStatus.VERIFIED:
        match.verification_method = VerificationMethod.DOCUMENT_IMAGE
    elif match.face_verification_status == FaceVerificationStatus.VERIFIED:
        match.verification_method = VerificationMethod.FACE_SELFIE
    else:
        raise HTTPException(
            status_code=403,
            detail=(
                "You must prove ownership first: supply the full ID number, "
                "upload your own photo of the document via /verify-identity, "
                "or pass a selfie check via /verify-face."
            ),
        )

    match.status = MatchStatus.CLAIM_REQUESTED
    match.claim_requested_at = utcnow()
    found.status = FoundIdStatus.CLAIM_REQUESTED

    finder = await db.get(User, found.finder_user_id)
    if finder is not None:
        method_label = {
            VerificationMethod.ID_NUMBER_TYPED: "verified the full ID number",
            VerificationMethod.DOCUMENT_IMAGE: (
                "uploaded and verified their own copy of the document"
            ),
            VerificationMethod.FACE_SELFIE: (
                "passed a selfie check against the photo on the document"
            ),
        }.get(match.verification_method, "verified their identity")
        await notify(
            db,
            user_id=finder.id,
            email=finder.email,
            kind="claim_requested",
            title="Someone is claiming a document you posted",
            body=(
                f"{user.display_name} {method_label} on the "
                f"{found.document_type.replace('_', ' ')} you posted.\n\n"
                + (f"Their message: {payload.message}\n\n" if payload.message else "")
                + "Open Seeker to approve or decline the claim."
            ),
            match_id=match.id,
        )

    await db.commit()
    await db.refresh(match)
    return MatchOut.model_validate(match)


@router.post("/{match_id}/verify-identity", response_model=IdentityVerificationOut)
async def verify_identity(
    match_id: uuid.UUID,
    image: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IdentityVerificationOut:
    """Verify the seeker owns the document by OCR'ing their own photo of it.

    The upload is read in memory and discarded; only the verdict is stored. See
    ``app.verification`` for what this does and does not prove.

    Attempts are capped: without a limit, a claimant could feed in image after
    image until OCR happened to misread one into a pass.
    """
    match, found = await _load_match(db, match_id)
    if match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match does not exist")

    if match.status not in (MatchStatus.MATCHED, MatchStatus.REJECTED):
        raise HTTPException(
            status_code=409,
            detail=f"Verification does not apply at status '{match.status}'",
        )

    if match.verification_status == VerificationStatus.VERIFIED:
        raise HTTPException(
            status_code=409, detail="This claim is already verified"
        )

    if match.verification_attempts >= MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many failed verification attempts. Submit the full ID "
                "number instead, or contact support."
            ),
        )

    raw = await image.read()
    try:
        pil_image = load_image(raw)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        outcome = compare_against_record(
            pil_image,
            expected_id_number=found.id_number,
            expected_holder_name=found.holder_name,
        )
    except OcrUnavailableError as exc:
        # A missing OCR binary is an outage, not evidence against the claimant:
        # do not burn an attempt or record a failure.
        raise HTTPException(
            status_code=503, detail=f"Verification is unavailable: {exc}"
        ) from exc

    match.verification_attempts += 1
    match.verification_score = outcome.score
    match.verification_detail = outcome.detail
    match.verification_document_type = outcome.document_type

    if outcome.passed:
        match.verification_status = VerificationStatus.VERIFIED
        match.verification_method = VerificationMethod.DOCUMENT_IMAGE
        match.verified_at = utcnow()
    else:
        match.verification_status = VerificationStatus.FAILED

    await db.commit()
    await db.refresh(match)

    return IdentityVerificationOut(
        verified=outcome.passed,
        status=match.verification_status,
        score=outcome.score,
        detail=outcome.detail,
        document_type=outcome.document_type,
        id_number_matched=outcome.id_number_matched,
        name_matched=outcome.name_matched,
        attempts_remaining=max(0, MAX_ATTEMPTS - match.verification_attempts),
    )


@router.post("/{match_id}/verify-face", response_model=FaceVerificationOut)
async def verify_face(
    match_id: uuid.UUID,
    selfie: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FaceVerificationOut:
    """Verify the seeker is the person pictured on the posted document.

    The selfie is decoded in memory, reduced to an embedding for comparison, and
    dropped. It is never written to disk, and the selfie's own embedding is never
    stored — only the verdict survives the request.

    A failure flags the match for manual review rather than silently ending the
    claim: a false rejection should reach a human, not a dead end.
    """
    match, found = await _load_match(db, match_id)
    if match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match does not exist")

    if match.status not in (MatchStatus.MATCHED, MatchStatus.REJECTED):
        raise HTTPException(
            status_code=409,
            detail=f"Face verification does not apply at status '{match.status}'",
        )

    if match.face_verification_status == FaceVerificationStatus.VERIFIED:
        raise HTTPException(status_code=409, detail="This claim is already verified")

    if match.face_attempts >= settings.face_max_attempts:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many failed selfie attempts. Submit the full ID number "
                "instead, or contact support."
            ),
        )

    # A missing face backend is an outage, not evidence against the claimant:
    # do not burn an attempt or record a failure. Same reasoning as the
    # OcrUnavailableError branch in verify_identity.
    if not face.embedding_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Selfie verification is unavailable on this deployment. Use the "
                "full ID number or upload a photo of the document instead."
            ),
        )

    if not found.face_embedding:
        # The posted photo had no detectable face. The claimant did nothing
        # wrong, so this is recorded distinctly from a failure and costs no
        # attempt — they should be sent down another proof path.
        match.face_verification_status = FaceVerificationStatus.NO_FACE_ON_RECORD
        await db.commit()
        raise HTTPException(
            status_code=422,
            detail=(
                "The posted photo has no detectable face to compare against. "
                "Use the full ID number or upload a photo of the document."
            ),
        )

    raw = await selfie.read()
    try:
        pil_image = load_image(raw)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        outcome = face.compare(
            face.deserialize(found.face_embedding),
            pil_image,
            threshold=settings.face_match_threshold,
        )
    except face.MultiFaceError as exc:
        # More than one face in the selfie is ambiguous, not a failed match, so
        # it costs no attempt — the same treatment a corrupt upload gets above.
        # Comparing against an arbitrary face would let a claimant stand next to
        # the rightful holder and pass.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except face.FaceUnavailableError as exc:  # pragma: no cover - guarded above
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    match.face_attempts += 1
    match.face_confidence = outcome.confidence

    if not outcome.face_found_in_selfie:
        match.face_verification_status = FaceVerificationStatus.NO_FACE_IN_SELFIE
    elif outcome.passed:
        match.face_verification_status = FaceVerificationStatus.VERIFIED
        match.face_verified_at = utcnow()
        match.verification_method = VerificationMethod.FACE_SELFIE
    else:
        match.face_verification_status = FaceVerificationStatus.FAILED
        match.flagged_for_review = True
        match.flag_reason = (
            f"Selfie did not match the photo on the posted document "
            f"({outcome.detail}). Attempt {match.face_attempts} of "
            f"{settings.face_max_attempts}."
        )

    await db.commit()
    await db.refresh(match)

    return FaceVerificationOut(
        verified=outcome.passed,
        status=match.face_verification_status,
        confidence=outcome.confidence,
        detail=outcome.detail,
        face_found_in_selfie=outcome.face_found_in_selfie,
        flagged_for_review=match.flagged_for_review,
        attempts_remaining=max(0, settings.face_max_attempts - match.face_attempts),
    )


@router.post("/{match_id}/decision", response_model=MatchOut)
async def decide_claim(
    match_id: uuid.UUID,
    payload: ClaimDecision,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Finder approves or declines a claim.

    Approval advances to ``AWAITING_PAYMENT``, not ``CLAIMED``. The record is
    released only once the payment webhook fires — see ``routers/payments.py``.
    """
    match, found = await _load_match(db, match_id)
    if found.finder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match does not exist")
    if match.status != MatchStatus.CLAIM_REQUESTED:
        raise HTTPException(
            status_code=409, detail=f"No pending claim on this match (status '{match.status}')"
        )

    seeker = await db.get(User, match.seeker_user_id)

    if payload.approve:
        # Approval does not release the record. The claim parks here until the
        # payment webhook confirms the fee; FULL_ACCESS_STATUSES excludes this
        # status, so the details stay withheld with no extra check.
        match.status = MatchStatus.AWAITING_PAYMENT
        found.status = FoundIdStatus.CLAIM_REQUESTED

        amount = settings.payment_amount_minor / 100
        title = "Your claim was approved — payment required"
        body = (
            f"The finder approved your claim. To unlock the full record and the "
            f"unblurred photo, pay the {amount:.0f} {settings.payment_currency} "
            f"claim fee from your matches page.\n\n"
            + (f"Note from the finder: {payload.note}" if payload.note else "")
        )
        kind = "claim_approved"
    else:
        match.status = MatchStatus.REJECTED
        # Return the document to the pool so other seekers can still find it.
        found.status = FoundIdStatus.MATCHED
        title = "Your claim was declined"
        body = (
            "The finder declined your claim.\n\n"
            + (f"Reason given: {payload.note}" if payload.note else "")
        )
        kind = "claim_rejected"

    if seeker is not None:
        await notify(
            db,
            user_id=seeker.id,
            email=seeker.email,
            kind=kind,
            title=title,
            body=body,
            match_id=match.id,
        )

    await db.commit()
    await db.refresh(match)
    return MatchOut.model_validate(match)


@router.post("/{match_id}/complete", response_model=MatchOut)
async def complete_match(
    match_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Mark a claimed document as physically handed over. Either party may call."""
    match, found = await _load_match(db, match_id)
    if user.id not in (match.seeker_user_id, found.finder_user_id):
        raise HTTPException(status_code=404, detail="Match does not exist")
    if match.status != MatchStatus.CLAIMED:
        raise HTTPException(
            status_code=409, detail="Only a claimed match can be completed"
        )

    match.status = MatchStatus.COMPLETED
    found.status = FoundIdStatus.RETURNED
    await db.commit()
    await db.refresh(match)
    return MatchOut.model_validate(match)
