"""Tesseract OCR extraction for ID documents.

Field extraction from ID photos is genuinely unreliable: layouts vary by
issuer, phone photos are skewed and glared, and Tesseract has no notion of
which line is a name. Everything here is therefore a *suggestion*. The API
returns these values for the finder to confirm or correct before the record is
created, and records what the finder changed.
"""
from __future__ import annotations

import logging
import re

from PIL import Image, ImageOps

from app.config import settings

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
except ImportError:  # pragma: no cover
    pytesseract = None  # type: ignore[assignment]


DOCUMENT_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("passport", re.compile(r"\bPASSPORT\b|\bP<[A-Z]{3}", re.I)),
    ("driver_license", re.compile(r"DRIVER'?S?\s*(LIC|LICEN[SC]E)|\bDL\b|\bCDL\b", re.I)),
    ("national_id", re.compile(r"NATIONAL\s*ID|IDENTITY\s*CARD|\bNIN\b|CITIZEN\s*ID", re.I)),
    ("residence_permit", re.compile(r"RESIDENCE\s*PERMIT|GREEN\s*CARD|\bPR\s*CARD\b", re.I)),
    ("student_id", re.compile(r"STUDENT\s*(ID|CARD)|UNIVERSITY|COLLEGE", re.I)),
    ("health_card", re.compile(r"HEALTH\s*(CARD|INSURANCE)|MEDICARE|\bNHS\b", re.I)),
    ("social_security", re.compile(r"SOCIAL\s*SECURITY|\bSSN\b", re.I)),
]

# Labelled forms come first: an explicit "ID No: X" beats a bare token.
# These are applied per line. Horizontal whitespace only ([ \t], never \s) —
# \s matches newlines, which would let a label on one line capture the value
# from the next one (e.g. "NATIONAL ID CARD" swallowing the date below it).
LABELLED_ID_PATTERNS = [
    re.compile(
        r"(?:ID|IDENTITY|DOCUMENT|DOC|LICEN[SC]E|LIC|PASSPORT|REG|REGISTRATION)"
        r"[ \t]*(?:NO|NUM|NUMBER|#|N[°o])?\.?[ \t]*[:#-]?[ \t]*"
        r"([A-Z0-9][A-Z0-9\- ]{3,24}[A-Z0-9])",
        re.I,
    ),
    re.compile(r"\bNO\.?[ \t]*[:#-]?[ \t]*([A-Z0-9][A-Z0-9\- ]{3,24}[A-Z0-9])", re.I),
]

# Fallback: a standalone alphanumeric token that looks like a document number.
BARE_ID_PATTERN = re.compile(r"\b(?=[A-Z0-9\-]{6,25}\b)(?=[^\s]*\d)[A-Z0-9][A-Z0-9\-]*\b")

# Lines describing dates never hold the document number.
DATE_LINE_PATTERN = re.compile(r"\b(DATE|BIRTH|DOB|EXPIR\w*|ISSUE[DS]?|VALID)\b", re.I)

# Horizontal-whitespace-only character classes, for the same reason as above.
LABELLED_NAME_PATTERNS = [
    ("surname", re.compile(r"(?:SURNAME|LAST[ \t]*NAME|FAMILY[ \t]*NAME)[ \t]*[:.]?[ \t]*([A-Za-z][A-Za-z'\- ]{1,39})", re.I)),
    ("given", re.compile(r"(?:GIVEN[ \t]*NAMES?|FIRST[ \t]*NAME|FORENAMES?)[ \t]*[:.]?[ \t]*([A-Za-z][A-Za-z'\- ]{1,39})", re.I)),
    ("full", re.compile(r"^[ \t]*NAME[ \t]*[:.]?[ \t]*([A-Za-z][A-Za-z'\- ]{1,39})", re.I)),
]

# Lines that are pure boilerplate and never a person's name.
NAME_STOPWORDS = {
    "PASSPORT", "DRIVER", "LICENSE", "LICENCE", "IDENTITY", "CARD", "NATIONAL",
    "REPUBLIC", "STATE", "UNITED", "STATES", "GOVERNMENT", "DEPARTMENT", "MOTOR",
    "VEHICLES", "AUTHORITY", "MINISTRY", "DATE", "BIRTH", "ISSUE", "EXPIRY",
    "EXPIRES", "SEX", "NATIONALITY", "ADDRESS", "SIGNATURE", "CLASS", "TYPE",
    "CODE", "NUMBER", "ISSUED", "VALID", "UNTIL", "STUDENT", "UNIVERSITY",
    "COLLEGE", "HEALTH", "INSURANCE", "SOCIAL", "SECURITY", "RESIDENCE", "PERMIT",
}


class OcrUnavailableError(RuntimeError):
    pass


def preprocess(image: Image.Image) -> Image.Image:
    """Grayscale + autocontrast, upscaling small images.

    Tesseract's accuracy falls off sharply below roughly 300 DPI, so small
    crops are enlarged before recognition.
    """
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    width, height = gray.size
    if max(width, height) < 1000:
        scale = 1000 / max(width, height)
        gray = gray.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    return gray


def run_tesseract(image: Image.Image) -> tuple[str, float | None]:
    """Return ``(raw_text, mean_confidence)``."""
    if pytesseract is None:  # pragma: no cover
        raise OcrUnavailableError("pytesseract is not installed")
    prepared = preprocess(image)
    try:
        text = pytesseract.image_to_string(prepared, lang=settings.ocr_languages)
        data = pytesseract.image_to_data(
            prepared, lang=settings.ocr_languages, output_type=pytesseract.Output.DICT
        )
    except Exception as exc:  # pragma: no cover - depends on local binary
        raise OcrUnavailableError(f"tesseract failed: {exc}") from exc

    confidences = [float(c) for c in data.get("conf", []) if str(c) not in ("-1", "")]
    mean_conf = sum(confidences) / len(confidences) if confidences else None
    return text, mean_conf


def guess_document_type(text: str) -> str:
    for doc_type, pattern in DOCUMENT_TYPE_PATTERNS:
        if pattern.search(text):
            return doc_type
    return "unknown"


def _looks_like_date(token: str) -> bool:
    digits = re.sub(r"\D", "", token)
    return len(digits) in (6, 8) and re.fullmatch(r"[\d\-/.]+", token) is not None


def _is_plausible_id(candidate: str) -> bool:
    """Filter out labels and dates that survived the regex."""
    if not (5 <= len(candidate) <= 25):
        return False
    if not re.search(r"\d", candidate):
        return False
    if _looks_like_date(candidate):
        return False
    # Reject values assembled purely from boilerplate words, e.g. a label that
    # ran into a date ("DATEOFBIRTH19900412").
    letters_only = re.sub(r"\d", "", candidate)
    return not any(word in letters_only for word in ("DATEOFBIRTH", "DATE", "EXPIR"))


def guess_id_number(text: str) -> str | None:
    """Find the document number, scanning one line at a time.

    Line-by-line matters: a label and its value always share a line on an ID
    card, so confining each match to a single line stops "NATIONAL ID CARD"
    from capturing the date of birth printed underneath it.
    """
    lines = text.splitlines()

    for line in lines:
        if DATE_LINE_PATTERN.search(line):
            continue
        for pattern in LABELLED_ID_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            candidate = re.sub(r"[\s-]", "", match.group(1)).upper()
            if _is_plausible_id(candidate):
                return candidate

    # Fallback: the longest bare alphanumeric token on a non-date line.
    # Document numbers are usually the longest such run on the card.
    candidates: list[str] = []
    for line in lines:
        if DATE_LINE_PATTERN.search(line):
            continue
        for token in BARE_ID_PATTERN.findall(line.upper()):
            cleaned = token.replace("-", "")
            if _is_plausible_id(cleaned):
                candidates.append(cleaned)

    return max(candidates, key=len) if candidates else None


def _clean_name_fragment(value: str) -> str:
    return " ".join(value.split()).strip(" -'")


def guess_name(text: str) -> str | None:
    """Find the holder's name from labels, falling back to a plausible line."""
    lines = text.splitlines()
    surname = None
    given = None
    full = None

    for line in lines:
        for kind, pattern in LABELLED_NAME_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            value = _clean_name_fragment(match.group(1))
            if not value:
                continue
            if kind == "surname" and surname is None:
                surname = value
            elif kind == "given" and given is None:
                given = value
            elif kind == "full" and full is None:
                full = value

    if given or surname:
        return " ".join(p for p in [given, surname] if p).title()
    if full:
        return full.title()

    # Fallback: the first line that reads like a person's name.
    for line in lines:
        stripped = " ".join(line.split())
        if not (4 <= len(stripped) <= 40):
            continue
        if re.search(r"\d", stripped):
            continue
        words = [w for w in re.split(r"\s+", stripped) if w.isalpha()]
        if len(words) < 2:
            continue
        if any(w.upper() in NAME_STOPWORDS for w in words):
            continue
        return " ".join(words).title()
    return None


def extract_fields(image: Image.Image) -> dict:
    """Best-effort field extraction. Callers must let the finder confirm."""
    text, confidence = run_tesseract(image)
    return {
        "document_type": guess_document_type(text),
        "holder_name": guess_name(text),
        "id_number": guess_id_number(text),
        "ocr_confidence": confidence,
        "raw_text": text,
    }
