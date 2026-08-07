"""Finder earnings: what you posted, what got claimed, what you are owed.

No admin rights involved — every query is scoped to the caller as finder, so a
finder can only ever see their own rows.

Note this reports what is *owed*, not what was disbursed. The platform records
the debt; actual payout to the finder is out of scope.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import (
    Earning,
    FoundId,
    FoundIdStatus,
    Match,
    MatchStatus,
    Transaction,
    TransactionStatus,
    User,
)
from app.normalize import redact_id_number, redact_name

router = APIRouter(prefix="/api/earnings", tags=["earnings"])


class EarningsSummary(BaseModel):
    # Minor units throughout, consistent with the ledger.
    total_earned: int
    pending: int
    currency: str
    documents_posted: int
    documents_claimed: int
    documents_returned: int
    claims_awaiting_payment: int


class EarningDocumentRow(BaseModel):
    found_id: uuid.UUID
    document_type: str
    holder_name_redacted: str
    id_number_redacted: str
    status: str
    created_at: str
    # Populated once a claim exists on the document.
    match_id: uuid.UUID | None
    match_status: str | None
    amount_earned: int
    payment_status: str | None
    paid_at: str | None


@router.get("/summary", response_model=EarningsSummary)
async def get_summary(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> EarningsSummary:
    """Totals for the calling finder.

    ``total_earned`` counts only earnings written when a payment webhook cleared.
    ``pending`` is what is approved but not yet paid — money that will arrive if
    the seeker completes checkout, and will not if they abandon it.
    """
    total_earned = int(
        await db.scalar(
            select(func.coalesce(func.sum(Earning.amount), 0)).where(
                Earning.finder_user_id == user.id
            )
        )
        or 0
    )

    # Pending: transactions on this finder's documents that were initiated but
    # have not been confirmed paid.
    pending = int(
        await db.scalar(
            select(func.coalesce(func.sum(Transaction.finder_share), 0))
            .where(Transaction.finder_user_id == user.id)
            .where(Transaction.status.in_([TransactionStatus.PENDING, TransactionStatus.INITIATED]))
        )
        or 0
    )

    async def count_found(*conditions) -> int:
        stmt = (
            select(func.count())
            .select_from(FoundId)
            .where(FoundId.finder_user_id == user.id)
        )
        for condition in conditions:
            stmt = stmt.where(condition)
        return int(await db.scalar(stmt) or 0)

    awaiting = int(
        await db.scalar(
            select(func.count())
            .select_from(Match)
            .join(FoundId, FoundId.id == Match.found_id_id)
            .where(FoundId.finder_user_id == user.id)
            .where(Match.status == MatchStatus.AWAITING_PAYMENT)
        )
        or 0
    )

    return EarningsSummary(
        total_earned=total_earned,
        pending=pending,
        currency=settings.payment_currency,
        documents_posted=await count_found(),
        documents_claimed=await count_found(FoundId.status == FoundIdStatus.CLAIMED),
        documents_returned=await count_found(FoundId.status == FoundIdStatus.RETURNED),
        claims_awaiting_payment=awaiting,
    )


@router.get("/documents", response_model=list[EarningDocumentRow])
async def list_documents(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[EarningDocumentRow]:
    """Every document this finder posted, with its claim and earning state.

    ID numbers stay redacted: a finder reading an earnings report has no need for
    the full number, even on a document they posted themselves.
    """
    stmt = (
        select(FoundId, Match, Transaction, Earning)
        .join(Match, Match.found_id_id == FoundId.id, isouter=True)
        .join(Transaction, Transaction.match_id == Match.id, isouter=True)
        .join(Earning, Earning.transaction_id == Transaction.id, isouter=True)
        .where(FoundId.finder_user_id == user.id)
        .order_by(FoundId.created_at.desc())
    )
    result = await db.execute(stmt)

    return [
        EarningDocumentRow(
            found_id=found.id,
            document_type=found.document_type,
            holder_name_redacted=redact_name(found.holder_name),
            id_number_redacted=redact_id_number(found.id_number),
            status=found.status,
            created_at=found.created_at.isoformat(),
            match_id=match.id if match else None,
            match_status=match.status if match else None,
            amount_earned=earning.amount if earning else 0,
            payment_status=txn.status if txn else None,
            paid_at=txn.paid_at.isoformat() if txn and txn.paid_at else None,
        )
        for found, match, txn, earning in result.all()
    ]
