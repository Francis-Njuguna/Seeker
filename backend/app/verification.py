"""Claimant-side document verification.

A seeker who says a posted document is theirs can upload their own photo of it
(a student ID, a national ID, whatever the record is). OCR runs on that upload
and the extracted fields are compared against the finder's stored record.

Two things this is *not*:

* It is not identity proofing. Nothing here checks that the person holding the
  phone is the person on the card, and a determined forger passes easily. It
  raises the cost of a speculative claim, nothing more.
* It is not a replacement for the finder's decision. A pass is evidence the
  finder sees; the finder still approves or declines.

The claimant's upload is never persisted. It is decoded in memory, OCR'd, and
discarded — storing a second copy of someone's ID would double the blast radius
of a breach for no benefit, since only the comparison outcome matters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PIL import Image

from app.normalize import normalize_id_number, normalize_name
from app.ocr import OcrUnavailableError, extract_fields

logger = logging.getLogger(__name__)

# The ID number is the decisive field: it is what matching runs on, and it is
# what the preview withholds. Name agreement is corroborating evidence only.
ID_NUMBER_WEIGHT = 0.75
NAME_WEIGHT = 0.25

# A verified result needs the ID number to agree. Name-only agreement scores
# below this on purpose — names collide, numbers do not.
PASS_THRESHOLD = 0.75

MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class VerificationOutcome:
    passed: bool
    score: float
    detail: str
    document_type: str | None
    id_number_matched: bool
    name_matched: bool
    readable: bool


def _name_tokens_match(extracted: str, expected: str) -> bool:
    """Compare names token-wise, ignoring order and extra middle names.

    OCR routinely drops a middle name or reverses surname order depending on
    the card layout, so exact string equality rejects genuine matches. Requiring
    every token of the shorter name to appear in the longer one tolerates that
    without becoming a loose fuzzy match.
    """
    got = {t for t in normalize_name(extracted).split() if len(t) > 1}
    want = {t for t in normalize_name(expected).split() if len(t) > 1}
    if not got or not want:
        return False
    shorter, longer = (got, want) if len(got) <= len(want) else (want, got)
    return shorter.issubset(longer)


def compare_against_record(
    image: Image.Image, *, expected_id_number: str, expected_holder_name: str
) -> VerificationOutcome:
    """OCR the claimant's upload and score it against the finder's record.

    Raises ``OcrUnavailableError`` if Tesseract is missing, which callers must
    surface as a retryable condition rather than a verification failure — a
    broken OCR binary is not evidence against the claimant.
    """
    fields = extract_fields(image)

    extracted_id = normalize_id_number(fields.get("id_number") or "")
    expected_id = normalize_id_number(expected_id_number)
    id_matched = bool(extracted_id) and extracted_id == expected_id

    extracted_name = fields.get("holder_name") or ""
    name_matched = _name_tokens_match(extracted_name, expected_holder_name)
    readable = bool(extracted_id or extracted_name)

    score = (ID_NUMBER_WEIGHT if id_matched else 0.0) + (
        NAME_WEIGHT if name_matched else 0.0
    )

    if id_matched and name_matched:
        detail = "The ID number and name on your document match the record."
    elif id_matched:
        detail = (
            "The ID number matches. The name could not be read clearly enough "
            "to confirm."
        )
    elif name_matched:
        detail = (
            "The name matches, but the ID number on your photo could not be "
            "read or does not match. Try a sharper, well-lit photo."
        )
    elif not extracted_id and not extracted_name:
        detail = (
            "Nothing readable was found on that photo. Try a sharper image "
            "with the whole document in frame."
        )
    else:
        detail = "The document you uploaded does not match this record."

    return VerificationOutcome(
        passed=score >= PASS_THRESHOLD,
        score=round(score, 3),
        detail=detail,
        document_type=fields.get("document_type"),
        id_number_matched=id_matched,
        name_matched=name_matched,
        readable=readable,
    )


__all__ = [
    "MAX_ATTEMPTS",
    "PASS_THRESHOLD",
    "OcrUnavailableError",
    "VerificationOutcome",
    "compare_against_record",
]
