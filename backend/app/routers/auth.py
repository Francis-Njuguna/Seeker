"""Registration and login."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.config import settings
from app.models import OtpChallenge, User, utcnow
from app.otp import consume_challenge, create_challenge, normalize_kenyan_phone
from app.schemas import (
    OtpChallengeOut,
    OtpRequest,
    OtpVerifyRequest,
    TokenOut,
    UserCreate,
    UserLogin,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> TokenOut:
    email = payload.email.lower().strip()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="An account with that email exists")

    testing = os.getenv("SEEKER_TESTING") == "1"
    if not payload.phone_number and not testing:
        raise HTTPException(status_code=422, detail="Phone number is required")
    phone = normalize_kenyan_phone(payload.phone_number) if payload.phone_number else None
    if phone:
        phone_owner = await db.execute(select(User).where(User.phone_number == phone))
        if phone_owner.scalars().first() is not None:
            raise HTTPException(status_code=409, detail="That phone number is already registered")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        phone_number=phone,
        preferred_language=payload.preferred_language,
        phone_verified_at=utcnow() if testing else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return TokenOut(
        access_token=create_access_token(user.id), user=UserOut.model_validate(user)
    )


@router.post("/login", response_model=TokenOut)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> TokenOut:
    email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    # Same response for unknown email and wrong password, so the endpoint does
    # not reveal which emails are registered.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return TokenOut(
        access_token=create_access_token(user.id), user=UserOut.model_validate(user)
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/otp/request", response_model=OtpChallengeOut)
async def request_otp(
    payload: OtpRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OtpChallengeOut:
    phone = normalize_kenyan_phone(payload.phone_number)
    if payload.purpose == "phone_verification":
        existing = await db.scalar(
            select(User).where(User.phone_number == phone, User.id != user.id)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="That phone number is already registered")
    challenge, code = await create_challenge(
        db, user=user, phone_number=phone, purpose=payload.purpose
    )
    return OtpChallengeOut(
        challenge_id=challenge.id,
        expires_at=challenge.expires_at,
        sandbox_code=code if settings.otp_provider == "sandbox" else None,
    )


@router.post("/otp/verify", response_model=UserOut)
async def verify_phone_otp(
    payload: OtpVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    challenge = await db.get(OtpChallenge, payload.challenge_id)
    if challenge is None:
        raise HTTPException(status_code=404, detail="OTP challenge not found")
    await consume_challenge(
        db,
        challenge=challenge,
        user=user,
        code=payload.code,
        purpose="phone_verification",
    )
    user.phone_number = challenge.phone_number
    user.phone_verified_at = utcnow()
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)
