"""Finder wallet and OTP-confirmed M-Pesa withdrawals."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import require_verified_phone
from app.models import (
    Earning,
    FoundId,
    Match,
    OtpChallenge,
    Transaction,
    TransactionStatus,
    User,
    Withdrawal,
    WithdrawalStatus,
    utcnow,
)
from app.otp import consume_challenge, create_challenge, normalize_kenyan_phone
from app.schemas import (
    OtpChallengeOut,
    OtpVerifyRequest,
    WalletSummary,
    WithdrawalOut,
    WithdrawalRequest,
    WithdrawalStartOut,
)

router = APIRouter(prefix="/api/wallet", tags=["wallet"])
MINIMUM_WITHDRAWAL = 10000


async def _totals(db: AsyncSession, user_id: uuid.UUID) -> tuple[int, int, int]:
    earned = int(
        await db.scalar(
            select(func.coalesce(func.sum(Earning.amount), 0)).where(Earning.finder_user_id == user_id)
        ) or 0
    )
    reserved = int(
        await db.scalar(
            select(func.coalesce(func.sum(Withdrawal.amount), 0))
            .where(Withdrawal.user_id == user_id)
            .where(Withdrawal.status.in_([
                WithdrawalStatus.OTP_PENDING,
                WithdrawalStatus.PROCESSING,
                WithdrawalStatus.PAID,
            ]))
        ) or 0
    )
    withdrawn = int(
        await db.scalar(
            select(func.coalesce(func.sum(Withdrawal.amount), 0))
            .where(Withdrawal.user_id == user_id)
            .where(Withdrawal.status == WithdrawalStatus.PAID)
        ) or 0
    )
    return max(0, earned - reserved), earned, withdrawn


@router.get("", response_model=WalletSummary)
async def wallet_summary(
    user: User = Depends(require_verified_phone), db: AsyncSession = Depends(get_db)
) -> WalletSummary:
    available, _, withdrawn = await _totals(db, user.id)
    pending = int(
        await db.scalar(
            select(func.coalesce(func.sum(Transaction.finder_share), 0))
            .join(Match, Match.id == Transaction.match_id)
            .join(FoundId, FoundId.id == Match.found_id_id)
            .where(FoundId.finder_user_id == user.id)
            .where(Transaction.status == TransactionStatus.PAID)
            .where(~select(Earning.id).where(Earning.transaction_id == Transaction.id).exists())
        ) or 0
    )
    return WalletSummary(
        available=available,
        pending=pending,
        withdrawn=withdrawn,
        minimum_withdrawal=MINIMUM_WITHDRAWAL,
        currency=settings.payment_currency,
    )


@router.post("/withdrawals", response_model=WithdrawalStartOut)
async def start_withdrawal(
    payload: WithdrawalRequest,
    user: User = Depends(require_verified_phone),
    db: AsyncSession = Depends(get_db),
) -> WithdrawalStartOut:
    available, _, _ = await _totals(db, user.id)
    if payload.amount < MINIMUM_WITHDRAWAL:
        raise HTTPException(status_code=400, detail="Minimum withdrawal is KES 100")
    if payload.amount > available:
        raise HTTPException(status_code=409, detail="Insufficient available balance")
    phone = normalize_kenyan_phone(payload.phone_number)
    withdrawal = Withdrawal(
        user_id=user.id,
        phone_number=phone,
        amount=payload.amount,
        currency=settings.payment_currency,
        status=WithdrawalStatus.OTP_PENDING,
    )
    db.add(withdrawal)
    await db.flush()
    challenge, code = await create_challenge(
        db, user=user, phone_number=phone, purpose="withdrawal"
    )
    await db.refresh(withdrawal)
    return WithdrawalStartOut(
        withdrawal=WithdrawalOut.model_validate(withdrawal),
        otp=OtpChallengeOut(
            challenge_id=challenge.id,
            expires_at=challenge.expires_at,
            sandbox_code=code if settings.otp_provider == "sandbox" else None,
        ),
    )


@router.post("/withdrawals/{withdrawal_id}/confirm", response_model=WithdrawalOut)
async def confirm_withdrawal(
    withdrawal_id: uuid.UUID,
    payload: OtpVerifyRequest,
    user: User = Depends(require_verified_phone),
    db: AsyncSession = Depends(get_db),
) -> WithdrawalOut:
    withdrawal = await db.get(Withdrawal, withdrawal_id)
    if withdrawal is None or withdrawal.user_id != user.id:
        raise HTTPException(status_code=404, detail="Withdrawal not found")
    if withdrawal.status != WithdrawalStatus.OTP_PENDING:
        raise HTTPException(status_code=409, detail="Withdrawal is not awaiting confirmation")
    challenge = await db.get(OtpChallenge, payload.challenge_id)
    if challenge is None or challenge.phone_number != withdrawal.phone_number:
        raise HTTPException(status_code=404, detail="OTP challenge not found")
    await consume_challenge(
        db,
        challenge=challenge,
        user=user,
        code=payload.code,
        purpose="withdrawal",
    )

    if settings.otp_provider != "sandbox":
        raise HTTPException(
            status_code=503,
            detail="Production M-Pesa B2C payout credentials are not configured",
        )

    # Sandbox payout is immediate. Production must never mark money paid until
    # a Daraja B2C result callback confirms it.
    withdrawal.status = WithdrawalStatus.PAID
    withdrawal.provider_reference = f"SANDBOX-B2C-{withdrawal.id.hex[:12].upper()}"
    withdrawal.completed_at = utcnow()
    await db.commit()
    await db.refresh(withdrawal)
    return WithdrawalOut.model_validate(withdrawal)


@router.get("/withdrawals", response_model=list[WithdrawalOut])
async def list_withdrawals(
    user: User = Depends(require_verified_phone), db: AsyncSession = Depends(get_db)
) -> list[WithdrawalOut]:
    result = await db.execute(
        select(Withdrawal)
        .where(Withdrawal.user_id == user.id)
        .order_by(Withdrawal.created_at.desc())
    )
    return [WithdrawalOut.model_validate(row) for row in result.scalars().all()]
