"""Admin dashboard: platform-wide stats, the ledger, and the review queue.

Every route here is behind ``require_admin``. Responses may carry unredacted
holder names — that is the point of the role, and this dependency is the only
gate that permits it.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app import claims
from app.models import (
    FoundId,
    FoundIdStatus,
    LostAlert,
    Match,
    MatchStatus,
    Transaction,
    TransactionStatus,
    User,
    utcnow,
)
from app.notifications import notify
from app.schemas import AdminReviewDecision, MatchOut

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminStats(BaseModel):
    total_users: int
    total_found_ids: int
    total_lost_alerts: int
    total_matches: int
    total_transactions: int
    paid_transactions: int
    # Minor units, like everything else in the ledger.
    platform_revenue: int
    finder_payouts: int
    gross_volume: int
    currency: str
    claims_completed: int
    flagged_for_review: int
    found_ids_by_status: dict[str, int]
    matches_by_status: dict[str, int]


class AdminTransactionRow(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    status: str
    provider: str
    amount: int
    platform_share: int
    finder_share: int
    currency: str
    reference: str | None
    created_at: str
    paid_at: str | None
    payer_email: str | None
    finder_email: str | None
    document_type: str | None
    holder_name: str | None


class AdminClaimRow(BaseModel):
    match_id: uuid.UUID
    status: str
    verification_method: str | None
    verification_status: str
    face_verification_status: str
    face_confidence: float | None
    flagged_for_review: bool
    flag_reason: str | None
    seeker_email: str | None
    finder_email: str | None
    document_type: str
    holder_name: str
    created_at: str
    claimed_at: str | None
    payment_status: str | None
    admin_review_required: bool
    finder_approved_at: str | None


async def _count(db: AsyncSession, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def _group_count(db: AsyncSession, column) -> dict[str, int]:
    result = await db.execute(select(column, func.count()).group_by(column))
    return {str(status): int(count) for status, count in result.all()}


@router.get("/stats", response_model=AdminStats)
async def get_stats(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> AdminStats:
    """Platform totals. Revenue counts only transactions the webhook confirmed."""
    from app.config import settings

    paid = TransactionStatus.PAID

    platform_revenue = int(
        await db.scalar(
            select(func.coalesce(func.sum(Transaction.platform_share), 0)).where(
                Transaction.status == paid
            )
        )
        or 0
    )
    finder_payouts = int(
        await db.scalar(
            select(func.coalesce(func.sum(Transaction.finder_share), 0)).where(
                Transaction.status == paid
            )
        )
        or 0
    )
    gross_volume = int(
        await db.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.status == paid
            )
        )
        or 0
    )
    paid_count = int(
        await db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.status == paid)
        )
        or 0
    )
    completed = int(
        await db.scalar(
            select(func.count())
            .select_from(Match)
            .where(Match.status == MatchStatus.COMPLETED)
        )
        or 0
    )
    flagged = int(
        await db.scalar(
            select(func.count())
            .select_from(Match)
            .where(Match.flagged_for_review.is_(True))
        )
        or 0
    )

    return AdminStats(
        total_users=await _count(db, User),
        total_found_ids=await _count(db, FoundId),
        total_lost_alerts=await _count(db, LostAlert),
        total_matches=await _count(db, Match),
        total_transactions=await _count(db, Transaction),
        paid_transactions=paid_count,
        platform_revenue=platform_revenue,
        finder_payouts=finder_payouts,
        gross_volume=gross_volume,
        currency=settings.payment_currency,
        claims_completed=completed,
        flagged_for_review=flagged,
        found_ids_by_status=await _group_count(db, FoundId.status),
        matches_by_status=await _group_count(db, Match.status),
    )


@router.get("/transactions", response_model=list[AdminTransactionRow])
async def list_transactions(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[AdminTransactionRow]:
    """The full ledger, joined to the people and document involved."""
    payer = User.__table__.alias("payer")
    finder = User.__table__.alias("finder")

    stmt = (
        select(
            Transaction,
            payer.c.email.label("payer_email"),
            finder.c.email.label("finder_email"),
            FoundId.document_type,
            FoundId.holder_name,
        )
        .join(payer, payer.c.id == Transaction.payer_user_id, isouter=True)
        .join(finder, finder.c.id == Transaction.finder_user_id, isouter=True)
        .join(Match, Match.id == Transaction.match_id, isouter=True)
        .join(FoundId, FoundId.id == Match.found_id_id, isouter=True)
        .order_by(Transaction.created_at.desc())
    )
    result = await db.execute(stmt)

    return [
        AdminTransactionRow(
            id=txn.id,
            match_id=txn.match_id,
            status=txn.status,
            provider=txn.provider,
            amount=txn.amount,
            platform_share=txn.platform_share,
            finder_share=txn.finder_share,
            currency=txn.currency,
            reference=txn.loop_reference,
            created_at=txn.created_at.isoformat(),
            paid_at=txn.paid_at.isoformat() if txn.paid_at else None,
            payer_email=payer_email,
            finder_email=finder_email,
            document_type=document_type,
            holder_name=holder_name,
        )
        for txn, payer_email, finder_email, document_type, holder_name in result.all()
    ]


async def _claim_rows(
    db: AsyncSession, *, flagged_only: bool
) -> list[AdminClaimRow]:
    seeker = User.__table__.alias("seeker")
    finder = User.__table__.alias("finder")

    stmt = (
        select(
            Match,
            FoundId,
            seeker.c.email.label("seeker_email"),
            finder.c.email.label("finder_email"),
            Transaction.status.label("payment_status"),
        )
        .join(FoundId, FoundId.id == Match.found_id_id)
        .join(seeker, seeker.c.id == Match.seeker_user_id, isouter=True)
        .join(finder, finder.c.id == FoundId.finder_user_id, isouter=True)
        .join(Transaction, Transaction.match_id == Match.id, isouter=True)
        .order_by(Match.created_at.desc())
    )
    if flagged_only:
        stmt = stmt.where(Match.flagged_for_review.is_(True))

    result = await db.execute(stmt)
    return [
        AdminClaimRow(
            match_id=match.id,
            status=match.status,
            verification_method=match.verification_method,
            verification_status=match.verification_status,
            face_verification_status=match.face_verification_status,
            face_confidence=match.face_confidence,
            flagged_for_review=match.flagged_for_review,
            flag_reason=match.flag_reason,
            seeker_email=seeker_email,
            finder_email=finder_email,
            document_type=found.document_type,
            holder_name=found.holder_name,
            created_at=match.created_at.isoformat(),
            claimed_at=match.claimed_at.isoformat() if match.claimed_at else None,
            payment_status=payment_status,
            admin_review_required=match.admin_review_required,
            finder_approved_at=(match.finder_approved_at.isoformat() if match.finder_approved_at else None),
        )
        for match, found, seeker_email, finder_email, payment_status in result.all()
    ]


@router.get("/claims", response_model=list[AdminClaimRow])
async def list_claims(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[AdminClaimRow]:
    """Every claim with how identity was proven and how it turned out."""
    return await _claim_rows(db, flagged_only=False)


@router.get("/flagged", response_model=list[AdminClaimRow])
async def list_flagged(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[AdminClaimRow]:
    """The manual-review queue: claims a failed face check flagged for a human."""
    return await _claim_rows(db, flagged_only=True)


@router.post("/claims/{match_id}/decision", response_model=MatchOut)
async def review_claim(
    match_id: uuid.UUID,
    payload: AdminReviewDecision,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    match = await db.get(Match, match_id)
    found = await db.get(FoundId, match.found_id_id) if match else None
    if match is None or found is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if not match.admin_review_required or match.finder_approved_at is None:
        raise HTTPException(status_code=409, detail="Claim is not ready for admin review")
    if match.status != MatchStatus.CLAIM_REQUESTED:
        raise HTTPException(status_code=409, detail="Claim has already been resolved")

    match.admin_reviewed_by = admin.id
    match.admin_review_note = payload.note
    seeker = await db.get(User, match.seeker_user_id)
    if payload.approve:
        match.admin_approved_at = utcnow()
        match.flagged_for_review = False
        match.status = MatchStatus.AWAITING_PAYMENT
        title = "Admin review approved - payment required"
        body = "Admin approved the manual review. Choose fulfilment and complete payment."
        kind = "admin_review_approved"
    else:
        match.status = MatchStatus.REJECTED
        await claims.release(db, found=found, to_status=FoundIdStatus.MATCHED)
        title = "Admin review declined"
        body = "Seeker could not approve the ownership evidence. No fraud penalty is applied automatically."
        kind = "admin_review_rejected"
    if seeker:
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
