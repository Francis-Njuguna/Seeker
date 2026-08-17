"""Payment endpoints: initiate, webhook, status, mock checkout.

The webhook is the only thing that marks a payment paid. ``initiate`` merely
starts one, and the mock checkout endpoint routes through the same
``apply_payment_result`` function the real provider's callback does — so the
sandbox exercises the identical code path rather than a shortcut around it.
"""
from __future__ import annotations

import logging
import os
import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import ensure_uuid, get_current_user
from app.models import (
    DeliveryOrder,
    DeliveryStatus,
    FoundId,
    FoundIdStatus,
    Match,
    MatchStatus,
    Transaction,
    TransactionStatus,
    User,
    utcnow,
)
from app.notifications import notify
from app.payments import (
    InitiationResult,
    classify_status,
    compute_split,
    initiate,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments", tags=["payments"])


class InitiateRequest(BaseModel):
    # Optional: the mock provider ignores it, and Loop can fall back to the
    # account on file. Present so a real STK-push provider has somewhere to send.
    phone: str | None = Field(default=None, max_length=20)


class InitiateResponse(BaseModel):
    transaction_id: uuid.UUID
    checkout_url: str | None
    reference: str
    amount: int
    currency: str
    platform_share: int
    finder_share: int
    recovery_fee: int
    delivery_fee: int


class PaymentStatusResponse(BaseModel):
    status: str
    amount: int
    currency: str
    reference: str | None
    checkout_url: str | None
    paid_at: str | None


async def apply_payment_result(
    db: AsyncSession, txn: Transaction, raw_status: str
) -> dict:
    """Apply a provider verdict to a transaction. Idempotent.

    This is the gate the whole claim flow hangs on: only a success here moves the
    match to CLAIMED, and only CLAIMED unlocks the document details.
    """
    if txn.status == TransactionStatus.PAID:
        logger.info("payment replay for already-paid txn=%s, no-op", txn.id)
        return {"status": "already_paid", "transaction_id": str(txn.id)}

    classified = classify_status(raw_status)
    txn.loop_raw_status = raw_status

    if classified == "pending":
        logger.info("intermediate status txn=%s raw=%s", txn.id, raw_status)
        await db.commit()
        return {"status": "pending", "transaction_id": str(txn.id)}

    if classified == "failed":
        logger.warning("payment failed txn=%s raw=%s", txn.id, raw_status)
        txn.status = TransactionStatus.FAILED
        await db.commit()
        return {"status": "failed", "transaction_id": str(txn.id)}

    # The money arrived.
    match = await db.scalar(select(Match).where(Match.id == txn.match_id))
    if match is None:
        logger.error("paid txn=%s has no match; cannot release", txn.id)
        raise HTTPException(status_code=500, detail="Match record missing")

    txn.status = TransactionStatus.PAID
    txn.paid_at = utcnow()
    match.status = MatchStatus.CLAIMED
    match.claimed_at = utcnow()

    found = await db.scalar(select(FoundId).where(FoundId.id == match.found_id_id))
    if found is not None:
        found.status = FoundIdStatus.CLAIMED

    delivery = await db.scalar(select(DeliveryOrder).where(DeliveryOrder.match_id == match.id))
    if delivery is not None:
        code = f"{secrets.randbelow(1_000_000):06d}"
        delivery.handover_code = code
        delivery.handover_code_hash = hashlib.sha256(code.encode("ascii")).hexdigest()
        delivery.handover_code_created_at = utcnow()
        delivery.status = DeliveryStatus.READY_FOR_HANDOVER

    seeker = await db.get(User, txn.payer_user_id)
    finder = await db.get(User, txn.finder_user_id)

    if seeker is not None:
        await notify(
            db,
            user_id=seeker.id,
            email=seeker.email,
            kind="payment_confirmed",
            title="Payment confirmed",
            body=(
                "Your payment has been received and your claim is approved. "
                "The delivery receipt code is available in your matches. Share it "
                "only after you have received the document."
            ),
            match_id=txn.match_id,
        )
    if finder is not None:
        await notify(
            db,
            user_id=finder.id,
            email=finder.email,
            kind="claim_paid",
            title="Payment confirmed - handover required",
            body=(
                "The claim payment is confirmed. Arrange delivery, then enter the "
                "owner's receipt code to release your finder reward."
            ),
            match_id=txn.match_id,
        )

    try:
        await db.commit()
    except IntegrityError:
        # Earning.transaction_id is unique, so two concurrent callbacks that both
        # passed the PAID check above collide here. The first one won; report
        # success so the provider stops retrying.
        await db.rollback()
        logger.warning("concurrent payment callback on txn=%s, duplicate dropped", txn.id)
        return {"status": "already_paid", "transaction_id": str(txn.id)}

    logger.info("payment confirmed txn=%s match=%s", txn.id, txn.match_id)
    return {"status": "paid", "transaction_id": str(txn.id)}


@router.post("/{match_id}/initiate", response_model=InitiateResponse)
async def initiate_payment(
    match_id: str,
    payload: InitiateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InitiateResponse:
    """Start payment for an approved claim.

    Calling this twice returns the existing transaction rather than minting a
    second one — a double-click must not create two charges.
    """
    match_uuid = ensure_uuid(match_id)
    match = await db.scalar(select(Match).where(Match.id == match_uuid))

    # 404 rather than 403 for a stranger: do not confirm the match exists.
    if match is None or match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match not found")

    existing = await db.scalar(
        select(Transaction).where(Transaction.match_id == match.id)
    )
    if existing is not None and existing.status == TransactionStatus.PAID:
        raise HTTPException(status_code=409, detail="This claim is already paid")

    if match.status != MatchStatus.AWAITING_PAYMENT:
        raise HTTPException(
            status_code=409,
            detail=(
                "Payment applies only to an approved claim awaiting payment "
                f"(this one is {match.status})"
            ),
        )

    if existing is not None:
        return InitiateResponse(
            transaction_id=existing.id,
            checkout_url=existing.checkout_url,
            reference=existing.loop_reference or "",
            amount=existing.amount,
            currency=existing.currency,
            platform_share=existing.platform_share,
            finder_share=existing.finder_share,
            recovery_fee=existing.recovery_fee,
            delivery_fee=existing.delivery_fee,
        )

    found = await db.scalar(select(FoundId).where(FoundId.id == match.found_id_id))
    if found is None:
        raise HTTPException(status_code=500, detail="Found document record missing")

    delivery = await db.scalar(select(DeliveryOrder).where(DeliveryOrder.match_id == match.id))
    if delivery is None:
        if os.getenv("SEEKER_TESTING") != "1":
            raise HTTPException(status_code=409, detail="Choose a courier or meetup before payment")
        delivery = DeliveryOrder(
            match_id=match.id,
            method="meetup",
            pickup_address="Sandbox pickup",
            destination_address="Sandbox destination",
            fee=0,
            currency=settings.payment_currency,
            status=DeliveryStatus.QUOTED,
        )
        db.add(delivery)
        await db.flush()

    recovery_fee = settings.payment_amount_minor
    delivery_fee = delivery.fee
    amount = recovery_fee + delivery_fee
    platform_share, finder_share = compute_split(recovery_fee)

    try:
        result: InitiationResult = await initiate(
            match_id=match.id,
            amount=amount,
            payer_email=user.email,
            payer_phone=payload.phone or user.phone_number,
        )
    except Exception as exc:  # noqa: BLE001 - provider outage, not a client error
        logger.exception("payment initiation failed match=%s", match.id)
        raise HTTPException(
            status_code=503, detail=f"Payment gateway unavailable: {exc}"
        ) from exc

    txn = Transaction(
        match_id=match.id,
        payer_user_id=user.id,
        finder_user_id=found.finder_user_id,
        amount=amount,
        recovery_fee=recovery_fee,
        delivery_fee=delivery_fee,
        platform_share=platform_share,
        finder_share=finder_share,
        currency=settings.payment_currency,
        status=TransactionStatus.INITIATED,
        provider=result.provider,
        loop_reference=result.reference,
        loop_raw_status=result.raw_status,
        checkout_url=result.checkout_url,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)

    logger.info("payment initiated txn=%s reference=%s", txn.id, result.reference)
    return InitiateResponse(
        transaction_id=txn.id,
        checkout_url=result.checkout_url,
        reference=result.reference,
        amount=amount,
        currency=settings.payment_currency,
        platform_share=platform_share,
        finder_share=finder_share,
        recovery_fee=recovery_fee,
        delivery_fee=delivery_fee,
    )


@router.post("/webhook", status_code=200)
async def payment_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Provider callback — the only thing that confirms payment.

    Unauthenticated by necessity: the provider holds no JWT. Signature
    verification is the real gate once ``loop_webhook_secret`` is set.

    An unknown reference returns 200 ``ignored`` rather than 404. A 4xx here
    makes providers retry the same dead reference indefinitely, and the endpoint
    would double as an oracle for which references exist.
    """
    raw_body = await request.body()
    if not verify_webhook_signature(raw_body, request.headers.get("X-Loop-Signature")):
        logger.warning("webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed payload") from exc

    stk_callback = body.get("Body", {}).get("stkCallback", {})
    if stk_callback:
        reference = stk_callback.get("CheckoutRequestID")
        raw_status = "paid" if stk_callback.get("ResultCode") == 0 else "failed"
    else:
        reference = body.get("reference")
        raw_status = str(body.get("status", ""))
    if not reference:
        raise HTTPException(status_code=400, detail="Missing reference")

    txn = await db.scalar(
        select(Transaction).where(Transaction.loop_reference == reference)
    )
    if txn is None:
        logger.info("webhook for unknown reference=%s, ignored", reference)
        return {"status": "ignored"}

    return await apply_payment_result(db, txn, raw_status)


@router.get("/{match_id}", response_model=PaymentStatusResponse)
async def get_payment_status(
    match_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentStatusResponse:
    """Poll payment state for a match. Seeker only."""
    match_uuid = ensure_uuid(match_id)
    match = await db.scalar(select(Match).where(Match.id == match_uuid))

    if match is None or match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match not found")

    txn = await db.scalar(select(Transaction).where(Transaction.match_id == match.id))
    if txn is None:
        raise HTTPException(status_code=404, detail="No payment has been started")

    return PaymentStatusResponse(
        status=txn.status,
        amount=txn.amount,
        currency=txn.currency,
        reference=txn.loop_reference,
        checkout_url=txn.checkout_url,
        paid_at=txn.paid_at.isoformat() if txn.paid_at else None,
    )


@router.post("/mock/{reference}", status_code=200)
async def mock_payment_complete(
    reference: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Mock provider only: stand in for the customer completing checkout.

    Deliberately routes through ``apply_payment_result`` — the same function the
    real webhook calls — so the sandbox proves the actual release path works
    rather than a parallel one that only exists in tests.
    """
    if settings.payment_provider != "mock":
        raise HTTPException(status_code=404, detail="Not found")

    txn = await db.scalar(
        select(Transaction).where(Transaction.loop_reference == reference)
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="Unknown payment reference")

    return await apply_payment_result(db, txn, "paid")
