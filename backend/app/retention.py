"""Data retention and unclaimed-document custody deadlines."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import FoundId, FoundIdStatus, User, utcnow
from app.notifications import notify
from app.storage import InvalidImageError, resolve_path

logger = logging.getLogger(__name__)


def _delete_image(relative_path: str | None) -> None:
    if not relative_path:
        return
    try:
        resolve_path(relative_path).unlink(missing_ok=True)
    except (InvalidImageError, OSError):
        logger.warning("could not purge stored image %s", relative_path, exc_info=True)


async def run_retention_sweep(db: AsyncSession) -> dict[str, int]:
    """Purge returned-document images and remove stale unclaimed records from search."""
    now = utcnow()
    returned_cutoff = now - timedelta(days=settings.retention_returned_document_days)
    unclaimed_cutoff = now - timedelta(days=settings.retention_unclaimed_document_days)

    returned = list((await db.scalars(
        select(FoundId).where(
            FoundId.status == FoundIdStatus.RETURNED,
            FoundId.updated_at <= returned_cutoff,
            FoundId.images_purged_at.is_(None),
        )
    )).all())
    for document in returned:
        _delete_image(document.image_path)
        _delete_image(document.image_blurred_path)
        document.image_path = None
        document.image_blurred_path = None
        document.face_embedding = None
        document.face_embedding_model = None
        document.reference_face_detected = False
        document.images_purged_at = now

    unclaimed = list((await db.scalars(
        select(FoundId).where(
            FoundId.status.in_([FoundIdStatus.POSTED, FoundIdStatus.MATCHED]),
            FoundId.created_at <= unclaimed_cutoff,
            FoundId.police_transfer_due_at.is_(None),
        )
    )).all())
    for document in unclaimed:
        document.status = FoundIdStatus.ARCHIVED
        document.police_transfer_due_at = now
        finder = await db.get(User, document.finder_user_id)
        if finder is not None:
            await notify(
                db,
                user_id=finder.id,
                email=finder.email,
                kind="police_transfer_due",
                title="Transfer the unclaimed document to police",
                body=(
                    "This document has remained unclaimed for 90 days and is no longer searchable. "
                    "Hand it to a police station and record the police reference in Seeker."
                ),
            )

    await db.commit()
    return {"images_purged": len(returned), "police_transfers_due": len(unclaimed)}
