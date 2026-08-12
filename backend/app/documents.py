"""Document classification and document-specific field extraction.

Two things live here that the generic OCR in ``ocr.py`` cannot do:

1. A **controlled vocabulary** (``DocumentClass``). Free-form document types are
   unsearchable and unauditable — "Kenyan ID", "kenya national id", "NATIONAL
   ID" and "id" are one document to a person and four to a database. The finder
   still types whatever they like into ``FoundId.document_type``; this vocabulary
   is what the system reasons about.

2. **Per-document extractors.** A Kenyan national ID has an eight-digit serial
   and a specific set of labels; a passport has a machine-readable zone with a
   published checksum; a student ID has a registration number in an
   institution-specific format. A single set of regexes that tries to serve all
   of them does worse on each than a small extractor written for one.

The hard rule, from §10 and §43: **nothing here invents identity data.** Every
value returned traces to characters Tesseract actually read off the image. When a
field is not on the document, or is not legible, the extractor returns ``None``.
It never fills a gap with a plausible-looking value, never completes a partial
number to the expected length, and never guesses a nationality from a language.
An empty field costs the finder ten seconds of typing; a fabricated one puts a
name on a record that no document ever carried.

Confidence is reported per field, not just per document, because the two differ
enormously in practice: an MRZ line whose checksum validates is near-certain even
when the printed text above it is unreadable glare.
"""
from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)


class DocumentClass(enum.StrEnum):
    """The controlled vocabulary.

    Deliberately short. Each member is here because it changes how the document
    is read or how a claim on it should be handled — not to be exhaustive about
    the world's paperwork.

    ``OTHER`` is for a document that was recognised as an identity document but
    matches none of the classes; ``UNKNOWN`` means classification did not
    succeed. Keeping them distinct matters: ``OTHER`` says "we looked and it is
    something else", ``UNKNOWN`` says "we could not tell", and only the second
    one is a reason to ask the finder to check the photo.
    """

    KENYAN_NATIONAL_ID = "kenyan_national_id"
    PASSPORT = "passport"
    STUDENT_ID = "student_id"
    VISA = "visa"
    DRIVER_LICENSE = "driver_license"
    NATIONAL_ID = "national_id"
    RESIDENCE_PERMIT = "residence_permit"
    HEALTH_CARD = "health_card"
    OTHER = "other"
    UNKNOWN = "unknown"


# Which classes carry a portrait we can use for the selfie check. Used only to
# set expectations in the UI — the actual answer for any given document is
# whether ``face_embedding`` was extracted, never this table.
CLASSES_WITH_PORTRAIT: frozenset[str] = frozenset(
    {
        DocumentClass.KENYAN_NATIONAL_ID,
        DocumentClass.PASSPORT,
        DocumentClass.STUDENT_ID,
        DocumentClass.DRIVER_LICENSE,
        DocumentClass.NATIONAL_ID,
        DocumentClass.RESIDENCE_PERMIT,
    }
)


@dataclass(frozen=True)
class Field:
    """One extracted value, with where it came from and how sure we are.

    ``source`` is the extractor that produced it (``"mrz"``, ``"label"``,
    ``"pattern"``). It is kept because §12 requires knowing which fields a
    machine produced, and because "the MRZ checksum validated" and "a regex
    matched a line" are not the same claim about a value.
    """

    value: str
    confidence: float
    source: str


@dataclass
class Classification:
    """What the classifier concluded, and why."""

    document_class: str
    confidence: float
    # The regex/keyword hits that led here, for a human debugging a misread.
    evidence: list[str] = field(default_factory=list)


@dataclass
class ExtractedDocument:
    """Everything read off one document image.

    ``fields`` holds only what was actually found. A caller asking for a field
    that is not there gets ``None`` — there is no default value for someone's
    date of birth.
    """

    classification: Classification
    fields: dict[str, Field] = field(default_factory=dict)
    ocr_confidence: float | None = None
    raw_text: str = ""

    def value(self, name: str) -> str | None:
        found = self.fields.get(name)
        return found.value if found else None

    def confidence_of(self, name: str) -> float | None:
        found = self.fields.get(name)
        return found.confidence if found else None

    def as_dict(self) -> dict:
        """Serialisable form for ``FoundId.ocr_fields``.

        Raw text is excluded: it is stored in its own column, and duplicating a
        full OCR dump of someone's ID into a second column is the opposite of
        data minimisation.
        """
        return {
            "document_class": self.classification.document_class,
            "classification_confidence": round(self.classification.confidence, 3),
            "classification_evidence": self.classification.evidence,
            "ocr_confidence": (
                round(self.ocr_confidence, 2) if self.ocr_confidence is not None else None
            ),
            "fields": {
                name: {
                    "value": f.value,
                    "confidence": round(f.confidence, 3),
                    "source": f.source,
                }
                for name, f in self.fields.items()
            },
        }


# --- classification ---------------------------------------------------------
#
# Ordered most-specific first. Kenyan national ID must precede the generic
# national ID, and visa must precede passport: a visa is pasted *inside* a
# passport, so the word "PASSPORT" appears on the page either way and a
# passport-first order would classify every visa as a passport.

# Named separately from the rules table because ``_class_for_mrz`` consults it
# directly. Reaching into ``_RULES`` by index would silently break the moment a
# rule is inserted above it.
_RESIDENCE_PERMIT_PATTERN = re.compile(
    r"RESIDENCE\s*PERMIT|RESIDENT\s*PERMIT|WORK\s*PERMIT|ALIEN\s*CARD", re.I
)

_RULES: list[tuple[str, float, re.Pattern[str]]] = [
    (
        DocumentClass.KENYAN_NATIONAL_ID,
        0.95,
        re.compile(
            r"REPUBLIC\s+OF\s+KENYA|JAMHURI\s+YA\s+KENYA|"
            r"SERIAL\s*(?:NUMBER|NO)|HUDUMA\s*NAMBA|"
            r"DISTRICT\s+OF\s+BIRTH|PLACE\s+OF\s+ISSUE",
            re.I,
        ),
    ),
    (
        DocumentClass.VISA,
        0.9,
        re.compile(
            r"\bVISA\b|ENTRY\s+PERMIT|MULTIPLE\s+ENTR|SINGLE\s+ENTR|"
            r"DURATION\s+OF\s+STAY|VISA\s+(?:TYPE|CLASS|CATEGORY)",
            re.I,
        ),
    ),
    (
        DocumentClass.PASSPORT,
        0.9,
        # Printed words only. An earlier version also tried to spot an MRZ first
        # line with ``^P[<A-Z][A-Z]{3}``, which under re.I|re.M matches ordinary
        # English words: "PLACE OF ISSUE" on a Kenyan ID scored as a passport
        # (P + L + ACE) and dragged a certain classification down to a coin flip.
        # The heuristic was redundant anyway — ``parse_mrz`` runs before this
        # table and settles the question from the document code when a real zone
        # is present. A zone too garbled to validate is not evidence worth acting
        # on, so there is nothing left for a loose pattern to contribute.
        re.compile(r"\bPASSPORT\b|\bPASSEPORT\b", re.I),
    ),
    (
        DocumentClass.STUDENT_ID,
        0.85,
        re.compile(
            r"STUDENT\s*(?:ID|CARD|IDENTITY)|\bUNIVERSITY\b|\bCOLLEGE\b|"
            r"\bPOLYTECHNIC\b|\bINSTITUTE\b|(?:REG|ADM|ADMISSION)[ \t]*(?:NO|NUMBER)|"
            r"FACULTY|\bSCHOOL\s+OF\b",
            re.I,
        ),
    ),
    (
        DocumentClass.DRIVER_LICENSE,
        0.85,
        re.compile(
            r"DRIVER'?S?\s*(?:LIC|LICEN[SC]E)|DRIVING\s*LICEN[SC]E|"
            r"\bDL\b|\bCDL\b|CLASS(?:ES)?\s+OF\s+VEHICLE",
            re.I,
        ),
    ),
    (
        DocumentClass.RESIDENCE_PERMIT,
        0.8,
        _RESIDENCE_PERMIT_PATTERN,
    ),
    (
        DocumentClass.HEALTH_CARD,
        0.8,
        re.compile(r"HEALTH\s*(?:CARD|INSURANCE)|\bNHIF\b|MEDICARE|\bNHS\b", re.I),
    ),
    (
        DocumentClass.NATIONAL_ID,
        0.75,
        re.compile(r"NATIONAL\s*ID|IDENTITY\s*CARD|IDENTIFICATION\s*CARD|CITIZEN\s*ID", re.I),
    ),
]

# Mapping from the free-form ``document_type`` a finder typed to the vocabulary.
# Used when there is no image to classify — the finder's own words are evidence,
# just weaker evidence than the document itself, which is why the confidence
# assigned below is lower than any OCR rule above.
_FINDER_SYNONYMS: dict[str, str] = {
    "kenyan_national_id": DocumentClass.KENYAN_NATIONAL_ID,
    "kenya_national_id": DocumentClass.KENYAN_NATIONAL_ID,
    "kenyan_id": DocumentClass.KENYAN_NATIONAL_ID,
    "huduma": DocumentClass.KENYAN_NATIONAL_ID,
    "national_id": DocumentClass.NATIONAL_ID,
    "id_card": DocumentClass.NATIONAL_ID,
    "identity_card": DocumentClass.NATIONAL_ID,
    "passport": DocumentClass.PASSPORT,
    "student_id": DocumentClass.STUDENT_ID,
    "student_card": DocumentClass.STUDENT_ID,
    "school_id": DocumentClass.STUDENT_ID,
    "visa": DocumentClass.VISA,
    "entry_permit": DocumentClass.VISA,
    "driver_license": DocumentClass.DRIVER_LICENSE,
    "drivers_license": DocumentClass.DRIVER_LICENSE,
    "driving_licence": DocumentClass.DRIVER_LICENSE,
    "driving_license": DocumentClass.DRIVER_LICENSE,
    "residence_permit": DocumentClass.RESIDENCE_PERMIT,
    "work_permit": DocumentClass.RESIDENCE_PERMIT,
    "alien_card": DocumentClass.RESIDENCE_PERMIT,
    "health_card": DocumentClass.HEALTH_CARD,
    "nhif": DocumentClass.HEALTH_CARD,
}


def _class_for_mrz(mrz: Mrz, text: str) -> str:
    """Classify from the MRZ document code, per ICAO 9303 §4.

    The first character of the code is authoritative and beats any keyword: ``P``
    is a passport, ``V`` a visa, and ``I``/``A``/``C`` an identity card or
    residence permit. This is why a visa pasted inside a passport classifies
    correctly — the page has "PASSPORT" printed on it, but the visa's own zone
    says ``V``.
    """
    code = (mrz.document_code or "").upper()
    kind = code[:1]

    if kind == "V":
        return DocumentClass.VISA
    if kind == "P":
        return DocumentClass.PASSPORT
    if kind in ("I", "A", "C"):
        if mrz.issuing_state.upper() == "KEN":
            return DocumentClass.KENYAN_NATIONAL_ID
        if _RESIDENCE_PERMIT_PATTERN.search(text):
            return DocumentClass.RESIDENCE_PERMIT
        return DocumentClass.NATIONAL_ID

    # A valid zone in a code we do not recognise. It is certainly a travel
    # document, so OTHER rather than UNKNOWN — we looked, and it is something
    # else. Guessing PASSPORT here would be inventing a fact.
    return DocumentClass.OTHER


def classify(text: str) -> Classification:
    """Classify a document from its OCR text.

    Scores every rule rather than returning the first hit, so a Kenyan passport
    (which matches both KENYAN_NATIONAL_ID's "REPUBLIC OF KENYA" and PASSPORT)
    is decided by weight of evidence instead of by rule order alone.
    """
    if not text or not text.strip():
        return Classification(DocumentClass.UNKNOWN, 0.0, ["no text recognised"])

    # The MRZ is checked first, and outranks every keyword rule. A zone whose
    # check digits validate is the strongest evidence any of this has access to:
    # it survives glare, a torn corner, and a language we have no keywords for.
    # It is deliberately consulted *before* the "no rule matched" bail-out — a
    # TD1 identity card carries no printed word we recognise, so keyword-first
    # ordering would return UNKNOWN while holding a validated MRZ in hand.
    mrz = parse_mrz(text)
    if mrz is not None:
        return Classification(
            _class_for_mrz(mrz, text),
            0.98,
            [
                f"{mrz.format} machine-readable zone parsed; "
                f"check digits validated: "
                f"{', '.join(k for k, ok in mrz.checks_passed.items() if ok) or 'none'}"
            ],
        )

    hits: list[tuple[str, float, str]] = []
    for doc_class, weight, pattern in _RULES:
        found = pattern.findall(text)
        if not found:
            continue
        sample = next(
            (f if isinstance(f, str) else " ".join(f) for f in found if f), doc_class
        )
        # More distinct matches is stronger evidence, with diminishing returns —
        # one keyword could be a coincidence, three is a layout.
        boost = min(0.04 * (len(found) - 1), 0.05)
        hits.append((doc_class, min(weight + boost, 0.99), " ".join(sample.split())[:60]))

    if not hits:
        return Classification(
            DocumentClass.UNKNOWN, 0.0, ["no classification rule matched"]
        )

    hits.sort(key=lambda h: h[1], reverse=True)
    best_class, best_score, _ = hits[0]

    # Competing classes of similar strength mean genuine ambiguity. Report the
    # winner but lower the confidence, so the caller flags it for review rather
    # than treating a coin-flip as settled.
    rivals = [h for h in hits[1:] if h[0] != best_class and best_score - h[1] < 0.1]
    if rivals:
        best_score = max(0.5, best_score - 0.15)

    return Classification(
        best_class, best_score, [f"{c}: {ev}" for c, _, ev in hits[:4]]
    )


def classify_from_finder_input(document_type: str) -> Classification:
    """Map the finder's free-form type onto the vocabulary.

    Used when no image was uploaded. Confidence is capped well below any OCR
    rule: a person naming a document is weaker evidence than the document
    itself, and the gap should be visible to anything that reads the score.
    """
    key = re.sub(r"[^a-z0-9]+", "_", (document_type or "").strip().lower()).strip("_")
    if not key:
        return Classification(DocumentClass.UNKNOWN, 0.0, ["no document type given"])
    mapped = _FINDER_SYNONYMS.get(key)
    if mapped:
        return Classification(mapped, 0.6, [f"finder described it as '{document_type}'"])
    # Recognised as *something*, just not one of ours. OTHER, not UNKNOWN.
    return Classification(
        DocumentClass.OTHER, 0.3, [f"unrecognised document type '{document_type}'"]
    )


# --- machine-readable zone --------------------------------------------------
#
# ICAO 9303. TD3 (passports) is two 44-character lines; TD1 (many ID cards and
# some visas) is three 30-character lines. The MRZ is the most reliable thing on
# the document by a wide margin: fixed-width fields in OCR-B, plus check digits
# that let us *prove* a field was read correctly instead of hoping.

_MRZ_WEIGHTS = (7, 3, 1)
_MRZ_LINE = re.compile(r"^[A-Z0-9<]{28,46}$")


def _mrz_char_value(char: str) -> int:
    if char == "<":
        return 0
    if char.isdigit():
        return int(char)
    return ord(char) - 55  # 'A' -> 10


def mrz_check_digit(payload: str) -> int:
    """ICAO 9303 check digit: weighted 7-3-1 sum, mod 10."""
    return (
        sum(
            _mrz_char_value(char) * _MRZ_WEIGHTS[i % 3]
            for i, char in enumerate(payload)
        )
        % 10
    )


def _checksum_ok(payload: str, digit: str) -> bool:
    if not digit.isdigit():
        return False
    return mrz_check_digit(payload) == int(digit)


def _mrz_date(raw: str, *, kind: str) -> str | None:
    """YYMMDD to a full ISO date, or None.

    The MRZ gives two year digits, so the century has to be resolved. It is
    resolved from the *meaning* of the field rather than guessed, using the
    windowing rule ICAO 9303 assumes:

    * a date of birth cannot be in the future, so ``yy`` above the current
      2-digit year belongs to the 1900s;
    * an expiry (or issue) date on a document in circulation is near the present,
      so it belongs to the 2000s.

    ``kind`` is required rather than defaulted. A default would silently pick a
    century for whichever caller forgot to pass one, and being wrong by a hundred
    years on someone's date of birth is exactly the kind of invented data this
    module refuses to produce.
    """
    if not re.fullmatch(r"\d{6}", raw):
        return None
    yy, month, day = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None

    current_yy = _reference_year() % 100
    if kind == "birth":
        century = 1900 if yy > current_yy else 2000
    elif kind == "expiry":
        century = 2000
    else:  # pragma: no cover - guarded by the callers below
        raise ValueError(f"unknown date kind {kind!r}")

    year = century + yy
    # Reject a date the calendar does not have (31 February). Only real dates.
    try:
        date(year, month, day)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _reference_year() -> int:
    """Today's year, isolated so tests can pin it.

    Century resolution depends on "now", which makes any test asserting a
    resolved year quietly time-dependent. One seam here is cheaper than a
    frozen-clock dependency across the suite.
    """
    return date.today().year


def _mrz_names(field_value: str) -> tuple[str | None, str | None]:
    """Split the MRZ name field into (surname, given names).

    ``<<`` separates surname from given names; single ``<`` separates words.
    """
    parts = field_value.split("<<", 1)
    surname = parts[0].replace("<", " ").strip()
    given = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""
    return (surname or None), (given or None)


@dataclass(frozen=True)
class Mrz:
    """A parsed, checksum-validated machine-readable zone."""

    document_code: str
    issuing_state: str
    surname: str | None
    given_names: str | None
    document_number: str
    nationality: str
    date_of_birth: str | None
    sex: str | None
    expiry_date: str | None
    format: str
    # Which of the individual check digits validated. All-true is a strong
    # guarantee; partial means the OCR garbled part of the zone, and only the
    # validated fields should be trusted.
    checks_passed: dict[str, bool] = field(default_factory=dict)

    @property
    def all_checks_passed(self) -> bool:
        return bool(self.checks_passed) and all(self.checks_passed.values())


def _candidate_mrz_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        # Tesseract reads the OCR-B filler character inconsistently; normalise
        # the usual substitutions before matching. Only '<'-like glyphs and
        # spacing are touched — never letters or digits, which would be inventing
        # data rather than repairing a known font artefact.
        candidate = raw.strip().upper().replace(" ", "")
        candidate = re.sub(r"[«‹〈≪]", "<", candidate)
        if _MRZ_LINE.fullmatch(candidate):
            lines.append(candidate)
    return lines


def _parse_td3(line1: str, line2: str) -> Mrz | None:
    """Two 44-character lines: passports, and visas in MRV-A format."""
    line1 = line1.ljust(44, "<")[:44]
    line2 = line2.ljust(44, "<")[:44]

    document_number = line2[0:9].replace("<", "")
    checks = {
        "document_number": _checksum_ok(line2[0:9], line2[9]),
        "date_of_birth": _checksum_ok(line2[13:19], line2[19]),
        "expiry_date": _checksum_ok(line2[21:27], line2[27]),
    }
    # The composite digit covers the whole of line 2; a visa MRV has no
    # composite, so its absence is not a failure.
    composite_source = line2[0:10] + line2[13:20] + line2[21:43]
    if line2[43].isdigit():
        checks["composite"] = _checksum_ok(composite_source, line2[43])

    # Require the document number to validate. Without it there is nothing here
    # worth trusting, and a half-read MRZ that we present as authoritative is
    # worse than no MRZ at all.
    if not checks["document_number"] or not document_number:
        return None

    surname, given = _mrz_names(line1[5:44])
    return Mrz(
        document_code=line1[0:2].replace("<", ""),
        issuing_state=line1[2:5].replace("<", ""),
        surname=surname,
        given_names=given,
        document_number=document_number,
        nationality=line2[10:13].replace("<", ""),
        date_of_birth=(
            _mrz_date(line2[13:19], kind="birth") if checks["date_of_birth"] else None
        ),
        sex=line2[20] if line2[20] in ("M", "F") else None,
        expiry_date=(
            _mrz_date(line2[21:27], kind="expiry") if checks["expiry_date"] else None
        ),
        format="TD3",
        checks_passed=checks,
    )


def _parse_td1(line1: str, line2: str, line3: str) -> Mrz | None:
    """Three 30-character lines: ID cards, residence permits, some visas."""
    line1 = line1.ljust(30, "<")[:30]
    line2 = line2.ljust(30, "<")[:30]
    line3 = line3.ljust(30, "<")[:30]

    document_number = line1[5:14].replace("<", "")
    checks = {
        "document_number": _checksum_ok(line1[5:14], line1[14]),
        "date_of_birth": _checksum_ok(line2[0:6], line2[6]),
        "expiry_date": _checksum_ok(line2[8:14], line2[14]),
    }
    composite_source = line1[5:30] + line2[0:7] + line2[8:15] + line2[18:29]
    if line2[29].isdigit():
        checks["composite"] = _checksum_ok(composite_source, line2[29])

    if not checks["document_number"] or not document_number:
        return None

    surname, given = _mrz_names(line3)
    return Mrz(
        document_code=line1[0:2].replace("<", ""),
        issuing_state=line1[2:5].replace("<", ""),
        surname=surname,
        given_names=given,
        document_number=document_number,
        nationality=line2[15:18].replace("<", ""),
        date_of_birth=(
            _mrz_date(line2[0:6], kind="birth") if checks["date_of_birth"] else None
        ),
        sex=line2[7] if line2[7] in ("M", "F") else None,
        expiry_date=(
            _mrz_date(line2[8:14], kind="expiry") if checks["expiry_date"] else None
        ),
        format="TD1",
        checks_passed=checks,
    )


def parse_mrz(text: str) -> Mrz | None:
    """Find and parse a machine-readable zone. ``None`` if there isn't a valid one.

    Returns ``None`` rather than a partial result on checksum failure. A garbled
    MRZ presented as authoritative is the exact failure mode this module exists
    to avoid: the check digits are there so we do not have to guess, and ignoring
    them to salvage a value throws away the only real guarantee on the document.
    """
    lines = _candidate_mrz_lines(text)
    if len(lines) < 2:
        return None

    # TD3 first: two 44-char lines, the passport case.
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if 43 <= len(a) <= 46 and 43 <= len(b) <= 46:
            parsed = _parse_td3(a, b)
            if parsed is not None:
                return parsed

    # TD1: three 30-char lines.
    for i in range(len(lines) - 2):
        a, b, c = lines[i], lines[i + 1], lines[i + 2]
        if all(28 <= len(x) <= 31 for x in (a, b, c)):
            parsed = _parse_td1(a, b, c)
            if parsed is not None:
                return parsed

    return None


__all__ = [
    "CLASSES_WITH_PORTRAIT",
    "Classification",
    "DocumentClass",
    "ExtractedDocument",
    "Field",
    "Mrz",
    "classify",
    "classify_from_finder_input",
    "mrz_check_digit",
    "parse_mrz",
]
