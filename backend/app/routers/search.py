"""Seeker flow: exact-match search returning redacted previews."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import enforce_search_rate_limit, to_preview
from app.matching import find_by_id_number, find_by_name, get_or_create_match
from app.models import MatchSource, User
from app.schemas import SearchQuery, SearchResult

router = APIRouter(prefix="/api/search", tags=["search"])

NO_MATCH_SUGGESTION = (
    "No match right now. Register a lost-ID alert and we will email you "
    "automatically if someone posts this document later."
)


@router.post("", response_model=SearchResult)
async def search(
    payload: SearchQuery,
    user: User = Depends(enforce_search_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> SearchResult:
    """Exact-match search by ID number or full name.

    A hit creates a match record so the seeker can start a claim, and returns
    only the redacted preview — never the underlying fields.
    """
    if not payload.id_number and not payload.name:
        raise HTTPException(
            status_code=400, detail="Provide either an ID number or a name to search"
        )

    # ID number is the stronger signal, so it wins when both are supplied.
    if payload.id_number:
        found = await find_by_id_number(db, payload.id_number)
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
            suggestion=(
                "Confirm the redacted details look right, then request a claim. "
                "You will need to enter the full ID number to prove ownership."
            ),
        )

    # Name search. Names are not unique, so no match record is created here —
    # the seeker must confirm via ID number to get a claimable match.
    candidates = await find_by_name(db, payload.name or "")
    if not candidates:
        return SearchResult(found=False, suggestion=NO_MATCH_SUGGESTION)

    return SearchResult(
        found=True,
        matched_on="name",
        results=[to_preview(f) for f in candidates],
        match_ids=[],
        suggestion=(
            "Found document(s) posted under that name. Search by the full ID "
            "number to confirm one is yours and start a claim."
        ),
    )
