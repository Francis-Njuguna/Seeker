"""Phone normalization and short-lived OTP challenges."""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OtpChallenge, User, utcnow

logger = logging.getLogger(__name__)


def normalize_kenyan_phone(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if digits.startswith("254") and len(digits) == 12:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 10:
        return f"+254{digits[1:]}"
    if len(digits) == 9 and digits[0] in {"1", "7"}:
        return f"+254{digits}"
    raise HTTPException(status_code=400, detail="Enter a valid Kenyan phone number")


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


async def create_challenge(
    db: AsyncSession, *, user: User, phone_number: str, purpose: str
) -> tuple[OtpChallenge, str]:
    phone = normalize_kenyan_phone(phone_number)
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = OtpChallenge(
        user_id=user.id,
        phone_number=phone,
        purpose=purpose,
        code_hash=_hash(code),
        expires_at=utcnow() + timedelta(minutes=settings.otp_ttl_minutes),
    )
    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    # The sandbox sender is deliberately explicit. A real SMS adapter should
    # replace this log line and must never return the code to the browser.
    logger.info("sandbox OTP phone=%s purpose=%s code=%s", phone, purpose, code)
    return challenge, code


async def consume_challenge(
    db: AsyncSession,
    *,
    challenge: OtpChallenge,
    user: User,
    code: str,
    purpose: str,
) -> None:
    if challenge.user_id != user.id or challenge.purpose != purpose:
        raise HTTPException(status_code=404, detail="OTP challenge not found")
    if challenge.consumed_at is not None:
        raise HTTPException(status_code=409, detail="OTP has already been used")
    if challenge.expires_at <= utcnow():
        raise HTTPException(status_code=410, detail="OTP has expired")
    if challenge.attempts >= settings.otp_max_attempts:
        raise HTTPException(status_code=429, detail="Too many incorrect OTP attempts")
    if not secrets.compare_digest(challenge.code_hash, _hash(code.strip())):
        challenge.attempts += 1
        await db.commit()
        raise HTTPException(status_code=400, detail="Incorrect OTP")
    challenge.consumed_at = utcnow()
    await db.flush()
