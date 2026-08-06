"""Notifications: in-app rows plus best-effort email.

Email is fire-and-forget. If SMTP is not configured the message is logged, so
the MVP runs with no external services and the in-app feed still works.
"""
from __future__ import annotations

import logging
import uuid
from email.message import EmailMessage

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Notification

logger = logging.getLogger(__name__)


async def _send_email(to: str, subject: str, body: str) -> str:
    if not settings.smtp_host:
        logger.info("EMAIL (not sent, no SMTP configured) to=%s subject=%s", to, subject)
        return "logged"

    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_use_tls,
            timeout=10,
        )
        return "sent"
    except Exception as exc:  # noqa: BLE001 - never fail the request on email
        logger.warning("email delivery failed to=%s error=%s", to, exc)
        return "failed"


async def notify(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    email: str,
    kind: str,
    title: str,
    body: str,
    match_id: uuid.UUID | None = None,
) -> Notification:
    """Record an in-app notification and attempt an email."""
    email_status = await _send_email(email, title, body)
    notification = Notification(
        user_id=user_id,
        match_id=match_id,
        kind=kind,
        title=title,
        body=body,
        email_status=email_status,
    )
    db.add(notification)
    await db.flush()
    return notification
