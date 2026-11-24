"""Match lifecycle: review a redacted preview, claim, prove, approve.

The status ladder is matched -> claim_pending -> claim_requested ->
awaiting_payment -> claimed -> completed.

``claim_pending`` is the lock. Starting a claim takes the document out of public
search **before** the claimant has proved anything, because proving takes several
requests (read the OCR fields, then submit a selfie) and the document must not
stay claimable by someone else while they run. Approval by the finder does not
release the record either: the claim parks at ``AWAITING_PAYMENT`` until the
payment webhook confirms the fee, and because full access is keyed on
CLAIMED/COMPLETED that withholds the details automatically.

So the lock and the proof are separate gates, in that order:

* ``POST /{id}/start-claim`` — takes the lock. No proof required. Atomic.
* ``POST /{id}/verify-identity`` / ``verify-face`` — the proof, done under lock.
* ``POST /{id}/claim`` — requires proof; sends the claim to the finder.

A seeker can prove ownership three ways — typing the full ID number, uploading
their own photo of the document, or passing a selfie check against the face on
the posted photo. Any one is enough; the finder still decides. ``/claim`` also
accepts a claimant who never called ``start-claim`` and takes the lock for them
in the same request, so the one-step flow still works.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import claims, face
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, to_owner, to_preview
from app.models import (
    FaceVerificationStatus,
    DeliveryOrder,
    DeliveryStatus,
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
    ClaimStartOut,
    FaceVerificationOut,
    IdentityVerificationOut,
    MatchOut,
    MatchWithPreview,
)
from app.storage import InvalidImageError, load_image, resolve_path
from app.verification import (
    MAX_ATTEMPTS,
    OcrUnavailableError,
    compare_against_record,
)

router = APIRouter(prefix="/api/matches", tags=["matches"])

HIGH_RISK_DOCUMENT_TYPES = {
    "passport", "bank_card", "birth_certificate", "death_certificate",
    "title_deed", "medical_record",
}

# Statuses at which the seeker has earned the unredacted record.
FULL_ACCESS_STATUSES = (MatchStatus.CLAIMED, MatchStatus.COMPLETED)

# Statuses at which the claimant may still submit proof. CLAIM_PENDING is the
# normal case — they hold the lock and are working through the steps. MATCHED and
# REJECTED are kept so a seeker can verify before locking anything, which is how
# the flow worked before the lock existed and what several tests exercise.
PROVABLE_STATUSES = (
    MatchStatus.MATCHED,
    MatchStatus.CLAIM_PENDING,
    MatchStatus.REJECTED,
    MatchStatus.EXPIRED,
)


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


async def _own_match(
    db: AsyncSession, match_id: uuid.UUID, user: User
) -> tuple[Match, FoundId]:
    """Load a match the caller is the seeker on.

    A match belonging to someone else returns 404, not 403: confirming that a
    given match id exists would leak that *some* document is under claim, which
    is exactly what the search-hiding rule is there to prevent.
    """
    match, found = await _load_match(db, match_id)
    if match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match does not exist")
    return match, found


def _next_step(match: Match, found: FoundId) -> str:
    """The one thing the claimant should do next, in their words."""
    if match.verification_status == VerificationStatus.VERIFIED:
        return "submit_claim"
    if match.face_verification_status == FaceVerificationStatus.VERIFIED:
        return "submit_claim"
    if (found.reference_face_detected or found.face_embedding) and match.face_attempts < settings.face_max_attempts:
        return "selfie"
    return "id_number_or_document"


@router.get("/mine", response_model=list[MatchWithPreview])
async def list_my_matches(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[MatchWithPreview]:
    """Matches where the caller is the seeker, with redacted previews.

    Deliberately *not* filtered by document visibility. A claimant who holds the
    lock must keep seeing their own document to finish the claim — §5. Public
    search is where the document disappears; the claimant's own workspace is not.
    """
    # Sweep first so a claimant who let the clock run out sees the truth here
    # rather than a countdown that already ended.
    await claims.expire_stale_claims(db)
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


@router.get("/finder", response_model=list[MatchWithPreview])
async def list_finder_matches(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[MatchWithPreview]:
    result = await db.execute(
        select(Match, FoundId)
        .join(FoundId, FoundId.id == Match.found_id_id)
        .where(FoundId.finder_user_id == user.id)
        .where(Match.status != MatchStatus.MATCHED)
        .order_by(Match.created_at.desc())
    )
    return [
        MatchWithPreview(
            match=MatchOut.model_validate(match),
            preview=to_preview(found),
            full=to_owner(found),
        )
        for match, found in result.all()
    ]


@router.post("/{match_id}/start-claim", response_model=ClaimStartOut)
async def start_claim(
    match_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClaimStartOut:
    """Take the exclusive claim lock, then begin proving ownership.

    This is the step that removes the document from public search. It requires no
    proof at all, on purpose: the proof steps are separate requests, and leaving
    the document claimable while they run is precisely the hole this closes. What
    the lock buys is time to prove, not the document.

    Idempotent for the holder — calling it twice returns the same lock rather
    than erroring, because a refreshed browser should not look like a conflict.
    """
    # Expire abandoned locks before testing availability, so a document held by
    # someone who walked away is claimable now rather than after some sweep.
    await claims.expire_stale_claims(db)

    match, found = await _own_match(db, match_id, user)

    if match.status in claims.LOCK_HOLDING_MATCH_STATUSES:
        if match.status != MatchStatus.CLAIM_PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"This claim has already moved past its start (status '{match.status}')",
            )
        # Already ours. Report the existing lock unchanged.
        return ClaimStartOut(
            match=MatchOut.model_validate(match),
            document=to_preview(found),
            expires_at=claims.claim_deadline(match.claim_started_at or match.created_at),
            verification_required=True,
            next_step=_next_step(match, found),
        )

    if match.status not in claims.STARTABLE_MATCH_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"A claim cannot be started from status '{match.status}'",
        )

    outcome = await claims.acquire(db, match=match, found=found)
    if not outcome.acquired:
        # 409, not 403: the claimant is entitled to try, they just lost the race.
        raise HTTPException(status_code=409, detail=outcome.reason)

    match.status = MatchStatus.CLAIM_PENDING
    match.claim_started_at = utcnow()
    match.claim_expired_at = None
    await db.commit()
    await db.refresh(match)

    return ClaimStartOut(
        match=MatchOut.model_validate(match),
        document=to_preview(found),
        expires_at=claims.claim_deadline(match.claim_started_at),
        verification_required=True,
        next_step=_next_step(match, found),
    )


@router.post("/{match_id}/abandon", response_model=MatchOut)
async def abandon_claim(
    match_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Give up a claim and return the document to public search immediately.

    Without this, a seeker who realises the document is not theirs has no way to
    release it and it sits locked until the timeout — which punishes everyone
    else for their honesty.

    Only a claim still at ``CLAIM_PENDING`` can be abandoned. Once the finder has
    been asked to decide, withdrawing is the finder's call, not a silent release.
    """
    match, found = await _own_match(db, match_id, user)

    if match.status != MatchStatus.CLAIM_PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Only a claim in progress can be abandoned (status '{match.status}')",
        )

    match.status = MatchStatus.EXPIRED
    match.claim_expired_at = utcnow()
    # The claimant withdrew; nothing here needs a human. Clear any flag so the
    # admin queue is not padded with resolved abandonments.
    match.flagged_for_review = False
    match.flag_reason = None
    await claims.release(db, found=found, to_status=FoundIdStatus.POSTED)
    await db.commit()
    await db.refresh(match)
    return MatchOut.model_validate(match)


@router.post("/{match_id}/claim", response_model=MatchOut)
async def request_claim(
    match_id: uuid.UUID,
    payload: ClaimRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Submit the claim to the finder. Proof of ownership is required here.

    Three proofs, any one sufficient:

    1. Type the full ID number (the preview shows only the last two characters).
    2. Pass the document-image check (``POST /{id}/verify-identity``).
    3. Pass the selfie check against the face on the posted photo
       (``POST /{id}/verify-face``).

    This is the gate §24 describes: no proof, no claim, decided server-side from
    stored verification state. A client that sets its own "verified" flag changes
    nothing here — the branch below reads the database.

    If the caller never called ``start-claim``, the lock is taken atomically in
    this request, so the single-step flow (search, then claim with the ID number)
    still works and still cannot double-lock a document.
    """
    await claims.expire_stale_claims(db)

    match, found = await _own_match(db, match_id, user)

    already_holding = match.status == MatchStatus.CLAIM_PENDING
    if not already_holding and match.status not in claims.STARTABLE_MATCH_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"Claim cannot be started from status '{match.status}'"
        )

    # Proof first, lock second. Checking proof before taking the lock means a
    # claimant who cannot prove anything never removes the document from search
    # — otherwise a stranger could park someone's ID out of reach by guessing.
    typed_number_matches = False
    if payload.id_number:
        if normalize_id_number(payload.id_number) != found.id_number_normalized:
            raise HTTPException(
                status_code=403, detail="The ID number does not match this document"
            )
        typed_number_matches = True

    document_verified = match.verification_status == VerificationStatus.VERIFIED
    face_verified = match.face_verification_status == FaceVerificationStatus.VERIFIED
    has_portrait = bool(found.reference_face_detected or found.face_embedding)
    standard_verified = document_verified and (face_verified if has_portrait else True)
    fallback_evidence = typed_number_matches or document_verified or face_verified

    if standard_verified and has_portrait:
        method = VerificationMethod.DOCUMENT_AND_FACE
    elif standard_verified:
        method = VerificationMethod.DOCUMENT_IMAGE
    elif fallback_evidence:
        method = VerificationMethod.MANUAL_FALLBACK
    else:
        raise HTTPException(
            status_code=403,
            detail=(
                "Submit ownership evidence first. Portrait documents require a "
                "matching document copy and selfie; fallback claims require at "
                "least one check before finder and admin review."
            ),
        )

    if not already_holding:
        outcome = await claims.acquire(db, match=match, found=found)
        if not outcome.acquired:
            raise HTTPException(status_code=409, detail=outcome.reason)
        match.claim_started_at = utcnow()
        match.claim_expired_at = None

    match.verification_method = method
    match.admin_review_required = (
        not standard_verified or found.document_type in HIGH_RISK_DOCUMENT_TYPES
    )
    if match.admin_review_required:
        match.flagged_for_review = True
        match.flag_reason = match.flag_reason or (
            "Fallback or high-risk claim requires admin approval after finder review."
        )
    match.status = MatchStatus.CLAIM_REQUESTED
    match.claim_requested_at = utcnow()
    # The document is already at CLAIM_REQUESTED from the lock; assert it anyway
    # so the one-step path cannot depend on acquire()'s choice of status.
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
            VerificationMethod.DOCUMENT_AND_FACE: (
                "passed document-copy and selfie verification"
            ),
            VerificationMethod.MANUAL_FALLBACK: (
                "submitted evidence that requires admin review"
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
    match, found = await _own_match(db, match_id, user)

    if match.status not in PROVABLE_STATUSES:
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

    match.verification_score = outcome.score
    match.verification_detail = outcome.detail
    match.verification_document_type = outcome.document_type

    if outcome.passed:
        match.verification_attempts += 1
        match.verification_status = VerificationStatus.VERIFIED
        match.verification_method = VerificationMethod.DOCUMENT_IMAGE
        match.verified_at = utcnow()
    elif not outcome.readable:
        # OCR being unable to read the upload is not a mismatch and must not
        # consume a claimant's limited verification attempts.
        match.verification_status = VerificationStatus.INCONCLUSIVE
    else:
        match.verification_attempts += 1
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

    The comparison happens here, on the server, against an embedding derived from
    the posted document at upload time. The client sends an image and receives a
    verdict; it has no say in the outcome and cannot assert one.

    The selfie is decoded in memory, reduced to an embedding for comparison, and
    dropped. It is never written to disk, and the selfie's own embedding is never
    stored — only the verdict survives the request.

    A failure flags the match for manual review rather than silently ending the
    claim: a false rejection should reach a human, not a dead end. It also does
    **not** release the lock. A document whose claim just failed suspiciously is
    the last thing that should be handed straight to the next claimant.
    """
    match, found = await _own_match(db, match_id, user)

    if match.status not in PROVABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Face verification does not apply at status '{match.status}'",
        )

    if match.face_verification_status == FaceVerificationStatus.VERIFIED:
        raise HTTPException(status_code=409, detail="This claim is already verified")

    if match.face_attempts >= settings.face_max_attempts:
        # Record the terminal state so the admin queue can tell "still trying"
        # from "out of attempts" without recomputing the cap.
        if match.face_verification_status != FaceVerificationStatus.ATTEMPTS_EXHAUSTED:
            match.face_verification_status = FaceVerificationStatus.ATTEMPTS_EXHAUSTED
            match.flagged_for_review = True
            match.flag_reason = (
                f"Selfie verification exhausted after {match.face_attempts} "
                f"failed attempts. Needs a human decision."
            )
            await db.commit()
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

    if not (found.reference_face_detected or found.face_embedding):
        # The posted photo had no detectable face. The claimant did nothing
        # wrong, so this is recorded distinctly from a failure and costs no
        # attempt — they should be sent down another proof path.
        match.face_verification_status = FaceVerificationStatus.NO_FACE_ON_RECORD
        await db.commit()
        raise HTTPException(
            status_code=422,
            detail=(
                "No face detected in the posted document. The photo has no "
                "portrait the system can compare against — upload a clearer "
                "photo of the document, or prove ownership with the full ID number."
            ),
        )

    raw = await selfie.read()
    try:
        pil_image = load_image(raw)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        if found.face_embedding:
            known_embedding = face.deserialize(found.face_embedding)
        elif found.image_path:
            reference = load_image(resolve_path(found.image_path).read_bytes())
            known_embedding = face.extract_embedding(reference)
            if known_embedding is None:
                match.face_verification_status = FaceVerificationStatus.NO_FACE_ON_RECORD
                await db.commit()
                raise HTTPException(status_code=422, detail="No face detected in the posted document")
        else:
            known_embedding = None
        if known_embedding is None:
            raise HTTPException(status_code=422, detail="No face detected in the posted document")
        outcome = face.compare(known_embedding, pil_image, threshold=settings.face_match_threshold)
    except face.MultiFaceError as exc:
        # More than one face in the selfie is ambiguous, not a failed match, so
        # it costs no attempt — the same treatment a corrupt upload gets above.
        # Comparing against an arbitrary face would let a claimant stand next to
        # the rightful holder and pass. Recorded so the state is observable, but
        # the claim stays open.
        match.face_verification_status = FaceVerificationStatus.TOO_MANY_FACES
        await db.commit()
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
    elif match.face_attempts >= settings.face_max_attempts:
        match.face_verification_status = FaceVerificationStatus.ATTEMPTS_EXHAUSTED
        match.flagged_for_review = True
        match.flag_reason = (
            f"Selfie did not match the photo on the posted document "
            f"({outcome.detail}). Final attempt {match.face_attempts} of "
            f"{settings.face_max_attempts}; no attempts remain."
        )
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
        # status, so the details stay withheld with no extra check. The lock also
        # stays: AWAITING_PAYMENT is in LOCK_HOLDING_MATCH_STATUSES.
        match.finder_approved_at = utcnow()
        match.status = (
            MatchStatus.CLAIM_REQUESTED
            if match.admin_review_required
            else MatchStatus.AWAITING_PAYMENT
        )
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
        if match.admin_review_required:
            title = "Finder approved - admin review pending"
            body = (
                "The finder approved your fallback or high-risk claim. "
                "Seeker must review it before payment."
            )
            kind = "claim_admin_review"
    else:
        match.status = MatchStatus.REJECTED
        # Release the lock: the finder decided this claimant is not the owner, so
        # the document genuinely belongs back in circulation for other seekers.
        await claims.release(db, found=found, to_status=FoundIdStatus.MATCHED)
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
    delivery = await db.scalar(
        select(DeliveryOrder).where(DeliveryOrder.match_id == match.id)
    )
    if delivery is None or delivery.status != DeliveryStatus.DELIVERED:
        raise HTTPException(
            status_code=409,
            detail="Handover must be confirmed with the receipt code",
        )
    # Delivery confirmation owns the state transition and finder earning. This
    # legacy route remains read-compatible for older clients but cannot bypass
    # the receipt-code escrow release.
    return MatchOut.model_validate(match)
