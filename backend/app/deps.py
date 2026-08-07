"""Shared dependencies: current user, redaction helper, search rate limiting."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import FoundId, User
from app.normalize import redact_id_number, redact_name
from app.schemas import FoundIdOwner, FoundIdPreview
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )
    return user


def to_preview(found: FoundId) -> FoundIdPreview:
    """Build the redacted view a seeker is allowed to see."""
    return FoundIdPreview(
        id=found.id,
        document_type=found.document_type,
        holder_name_redacted=redact_name(found.holder_name),
        id_number_redacted=redact_id_number(found.id_number),
        found_location=found.found_location,
        status=found.status,
        created_at=found.created_at,
        blurred_image_url=(
            f"/api/found-ids/{found.id}/image/blurred" if found.image_blurred_path else None
        ),
    )


def to_owner(found: FoundId) -> FoundIdOwner:
    """Build the unredacted view, with the full-image URL attached.

    Only hand the result to the finder or a seeker with an approved claim; the
    access check lives in ``_viewer_has_full_access``.
    """
    out = FoundIdOwner.model_validate(found)
    out.image_url = f"/api/found-ids/{found.id}/image" if found.image_path else None
    return out


class SlidingWindowLimiter:
    """In-process sliding-window limiter.

    Sufficient for a single-instance MVP. A multi-instance deployment needs
    this moved to Redis, since each process keeps its own counters.
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


search_limiter = SlidingWindowLimiter(
    settings.search_rate_limit, settings.search_rate_window_seconds
)


async def enforce_search_rate_limit(
    request: Request, user: User = Depends(get_current_user)
) -> User:
    """Throttle search per user.

    Exact-match search on an ID number is an enumeration oracle: without a
    limit, an attacker could confirm which ID numbers exist in the system.
    """
    if not search_limiter.check(str(user.id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many searches. Please wait a moment and try again.",
        )
    return user


def ensure_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid identifier") from exc


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate admin-only routes.

    Admin is assigned out of band; there is no self-service path to it.
    """
    from app.models import UserRole

    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
