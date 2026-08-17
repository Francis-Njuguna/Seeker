"""Private exact-match search returning redacted previews."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import claims
from app.database import get_db
from app.deps import enforce_search_rate_limit, to_preview
from app.matching import find_by_id_number, find_by_name_and_dob, get_or_create_match
from app.models import MatchSource, User
from app.schemas import SearchQuery, SearchResult

router = APIRouter(prefix="/api/search", tags=["search"])

NO_MATCH_SUGGESTION = (
    "No match right now. Register an alert and Seeker will notify you if a "
    "matching document is posted later."
)


@router.post("", response_model=SearchResult)
async def search(
    payload: SearchQuery,
    user: User = Depends(enforce_search_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> SearchResult:
    if not payload.id_number and not (payload.name and payload.date_of_birth):
        raise HTTPException(
            status_code=400,
            detail="Provide a document number, or the full name and date of birth",
        )

    await claims.expire_stale_claims(db)

    if payload.id_number:
        found = await find_by_id_number(db, payload.id_number, payload.document_type)
        if found is None:
            return SearchResult(found=False, suggestion=NO_MATCH_SUGGESTION)
        match, _ = await get_or_create_match(
            db,
            found_id=found,
            seeker_user_id=user.id,
            source=MatchSource.SEARCH,
            matched_on="id_number",
        )
        await db.commit()
        return SearchResult(
            found=True,
            matched_on="id_number",
            results=[to_preview(found)],
            match_ids=[match.id],
            suggestion="Confirm the redacted details, then start ownership verification.",
        )

    candidates = await find_by_name_and_dob(
        db,
        name=payload.name or "",
        date_of_birth=payload.date_of_birth,
        document_type=payload.document_type,
    )
    if not candidates:
        return SearchResult(found=False, suggestion=NO_MATCH_SUGGESTION)

    matches = []
    for candidate in candidates:
        match, _ = await get_or_create_match(
            db,
            found_id=candidate,
            seeker_user_id=user.id,
            source=MatchSource.SEARCH,
            matched_on="name_and_date_of_birth",
        )
        matches.append(match)
    await db.commit()
    return SearchResult(
        found=True,
        matched_on="name_and_date_of_birth",
        results=[to_preview(found) for found in candidates],
        match_ids=[match.id for match in matches],
        suggestion=(
            "A private exact match was found. Review the redacted preview and "
            "complete ownership verification to continue."
        ),
    )
