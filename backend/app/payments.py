"""Payment providers for claim fees.

Two providers, one interface:

``mock``
    A self-contained sandbox. Generates a reference, exposes a "pay now" endpoint
    that fires the same webhook path the real provider would, and needs no
    credentials. This is the default and what the tests exercise, so the whole
    payment flow — including webhook confirmation — is verifiable offline.

``loop``
    NCBA Loop sandbox. Requires ``loop_api_key``. Kept deliberately thin: it
    initiates a checkout and lets the webhook do the confirming.

Both providers only ever *initiate*. Nothing in this module marks a payment as
paid — that is the webhook's job alone, because a client-side "success" redirect
is a claim about payment, not evidence of it.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class PaymentError(RuntimeError):
    """Raised when a payment cannot be initiated."""


@dataclass(frozen=True)
class InitiationResult:
    """What the provider handed back when we asked to start a payment."""

    reference: str
    checkout_url: str | None
    provider: str
    raw_status: str


def compute_split(amount: int) -> tuple[int, int]:
    """Split an amount into (platform_share, finder_share).

    Integer arithmetic with the remainder going to the finder, so the two shares
    always sum exactly to ``amount`` — no rounding drift, no cent conjured or lost.
    """
    platform = int(amount * settings.platform_share_ratio)
    finder = amount - platform
    return platform, finder


def _mock_initiate(*, match_id: uuid.UUID, amount: int) -> InitiationResult:
    reference = f"MOCK-{uuid.uuid4().hex[:16].upper()}"
    logger.info(
        "mock payment initiated reference=%s match=%s amount=%s",
        reference,
        match_id,
        amount,
    )
    return InitiationResult(
        reference=reference,
        checkout_url=f"{settings.public_base_url}/api/payments/mock/{reference}",
        provider="mock",
        raw_status="initiated",
    )


async def _loop_initiate(
    *, match_id: uuid.UUID, amount: int, payer_email: str
) -> InitiationResult:
    if not settings.loop_api_key:
        raise PaymentError(
            "Loop provider selected but loop_api_key is not configured"
        )

    payload = {
        "amount": amount / 100,
        "currency": settings.payment_currency,
        "reference": f"SEEKER-{match_id}",
        "description": f"Document claim fee for match {match_id}",
        "callback_url": f"{settings.public_base_url}/api/payments/webhook",
        "customer": {"email": payer_email},
    }
    headers = {
        "Authorization": f"Bearer {settings.loop_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            response = await http.post(
                settings.loop_api_url, json=payload, headers=headers
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        raise PaymentError(f"Loop payment gateway unreachable: {exc}") from exc
    except ValueError as exc:
        raise PaymentError("Loop returned a malformed response") from exc

    reference = body.get("reference") or body.get("id")
    if not reference:
        raise PaymentError("Loop response did not include a payment reference")

    return InitiationResult(
        reference=str(reference),
        checkout_url=body.get("checkout_url") or body.get("payment_url"),
        provider="loop",
        raw_status=str(body.get("status", "initiated")),
    )


async def initiate(
    *, match_id: uuid.UUID, amount: int, payer_email: str
) -> InitiationResult:
    """Start a payment with the configured provider."""
    if settings.payment_provider == "loop":
        return await _loop_initiate(
            match_id=match_id, amount=amount, payer_email=payer_email
        )
    return _mock_initiate(match_id=match_id, amount=amount)


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    """Check a webhook's HMAC-SHA256 signature.

    When no secret is configured the check is skipped — that is the sandbox
    default and it is logged, so an unsigned production deployment is at least
    noisy rather than silent.
    """
    if not settings.loop_webhook_secret:
        logger.warning("webhook signature check skipped: no loop_webhook_secret set")
        return True
    if not signature:
        return False
    expected = hmac.new(
        settings.loop_webhook_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# Provider statuses that mean the money actually arrived.
SUCCESS_STATUSES = {"paid", "success", "successful", "completed", "settled"}
FAILURE_STATUSES = {"failed", "cancelled", "canceled", "declined", "expired"}


def classify_status(raw_status: str) -> str:
    """Map a provider status string to one of: paid / failed / pending."""
    lowered = (raw_status or "").strip().lower()
    if lowered in SUCCESS_STATUSES:
        return "paid"
    if lowered in FAILURE_STATUSES:
        return "failed"
    return "pending"
