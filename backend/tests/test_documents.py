"""Document classification and MRZ parsing.

These are pure-function tests: no database, no Tesseract, no images. The MRZ
strings below are constructed from the ICAO 9303 specification and their check
digits computed by hand-checkable arithmetic, so the tests assert against the
*standard* rather than against whatever this implementation happens to do.

The specimen numbers are ICAO's own documentation examples, not real documents.

The most important tests here are the negative ones. A classifier that labels
confidently and wrongly, or an MRZ parser that returns a plausible number it
misread, is worse than no extraction at all: it puts machine-invented identity
data in front of a claimant who is about to be asked "is this you?". So the
checksum-failure and no-invention cases carry as much weight as the happy path.
"""
from __future__ import annotations

from app.documents import (
    CLASSES_WITH_PORTRAIT,
    DocumentClass,
    classify,
    classify_from_finder_input,
    mrz_check_digit,
    parse_mrz,
)


# --- check digit arithmetic (ICAO 9303 part 3, section 4.9) -----------------


def test_check_digit_matches_the_icao_specimen_document_number() -> None:
    """``D23145890`` is the document number in ICAO 9303's TD1 specimen.

    Worked by hand, weights cycling 7-3-1 and letters valued from A=10:

        D(13)x7=91  2x3=6  3x1=3  1x7=7  4x3=12
        5x1=5  8x7=56  9x3=27  0x1=0     sum=207

    207 mod 10 = 7, which is the digit printed after the number on the specimen.
    """
    assert mrz_check_digit("D23145890") == 7


def test_check_digit_of_the_td3_specimen_document_number() -> None:
    """``L898902C<`` from the TD3 specimen, whose printed check digit is 3.

        L(21)x7=147  8x3=24  9x1=9  8x7=56  9x3=27
        0x1=0  2x7=14  C(12)x3=36  <(0)x1=0   sum=313

    313 mod 10 = 3.
    """
    assert mrz_check_digit("L898902C<") == 3


def test_trailing_filler_cannot_change_the_check_digit() -> None:
    """``<`` is valued zero, so padding a field is arithmetically invisible.

    This matters because a short document number is filler-padded to the field
    width, and the digit is computed over the padded form.
    """
    assert mrz_check_digit("L898902C") == mrz_check_digit("L898902C<")
    assert mrz_check_digit("<<<<<<<<<") == 0


def test_check_digit_letter_values_start_at_ten() -> None:
    # 'A' is 10, weighted 7 -> 70 -> 0 mod 10. 'B' is 11 -> 77 -> 7.
    assert mrz_check_digit("A") == 0
    assert mrz_check_digit("B") == 7


# --- TD3: passports ---------------------------------------------------------
#
# The specimen from ICAO 9303 part 4. Line 2 breaks down as:
#   L898902C<  document number
#   3          its check digit
#   UTO        nationality
#   740812     date of birth (12 Aug 1974)
#   2          its check digit
#   F          sex
#   120415     expiry (15 Apr 2012)
#   9          its check digit
#   ZE184226B  optional data, filler-padded
#   1          its check digit
#   6          composite check digit, over positions 1-10, 14-20 and 22-43

TD3_LINE_1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
TD3_LINE_2 = "L898902C<3UTO7408122F1204159ZE184226B<<<<<16"


def test_td3_passport_parses_every_field() -> None:
    mrz = parse_mrz(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert mrz is not None
    assert mrz.format == "TD3"
    assert mrz.document_code == "P"
    assert mrz.issuing_state == "UTO"
    assert mrz.document_number == "L898902C"
    assert mrz.nationality == "UTO"
    assert mrz.sex == "F"


def test_td3_splits_surname_from_given_names() -> None:
    mrz = parse_mrz(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert mrz is not None
    assert mrz.surname == "ERIKSSON"
    assert mrz.given_names == "ANNA MARIA"


def test_td3_resolves_the_birth_century_into_the_past() -> None:
    """740812 is 1974, not 2074 — nobody's date of birth is in the future."""
    mrz = parse_mrz(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert mrz is not None
    assert mrz.date_of_birth == "1974-08-12"


def test_td3_resolves_the_expiry_century_into_the_2000s() -> None:
    mrz = parse_mrz(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert mrz is not None
    assert mrz.expiry_date == "2012-04-15"


def test_td3_reports_which_checksums_validated() -> None:
    mrz = parse_mrz(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert mrz is not None
    assert mrz.checks_passed["document_number"] is True
    assert mrz.checks_passed["date_of_birth"] is True
    assert mrz.checks_passed["expiry_date"] is True


def test_a_clean_specimen_passes_every_digit_including_the_composite() -> None:
    """The composite digit covers the whole of line 2 at once.

    Asserted separately because it is the one digit that catches a transposition
    *between* fields — each field's own digit can validate while the fields are
    swapped, and only the composite notices.
    """
    mrz = parse_mrz(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert mrz is not None
    assert mrz.checks_passed["composite"] is True
    assert mrz.all_checks_passed is True


def test_td3_survives_being_embedded_in_surrounding_ocr_noise() -> None:
    """The MRZ arrives inside a page of other text, not on its own."""
    text = (
        "PASSPORT\nREPUBLIC OF UTOPIA\nType P  Code UTO\n"
        "Surname ERIKSSON\nGiven names ANNA MARIA\n"
        f"{TD3_LINE_1}\n{TD3_LINE_2}\n"
    )
    mrz = parse_mrz(text)

    assert mrz is not None
    assert mrz.document_number == "L898902C"


def test_td3_tolerates_spaces_tesseract_inserts_into_the_zone() -> None:
    """Tesseract sprays spaces through OCR-B; that is a font artefact, not data."""
    spaced = f"{TD3_LINE_1[:20]} {TD3_LINE_1[20:]}\n{TD3_LINE_2[:15]} {TD3_LINE_2[15:]}"
    mrz = parse_mrz(spaced)

    assert mrz is not None
    assert mrz.document_number == "L898902C"


# --- the refusals -----------------------------------------------------------


def test_a_wrong_document_number_checksum_is_refused_entirely() -> None:
    """The whole zone is rejected, not salvaged.

    The check digit exists so the system does not have to guess. Returning the
    misread number anyway would hand a claimant a document number that no
    document carries.
    """
    corrupted = TD3_LINE_2[:9] + "9" + TD3_LINE_2[10:]  # digit 3 -> 9
    assert parse_mrz(f"{TD3_LINE_1}\n{corrupted}") is None


def test_a_garbled_document_number_is_refused() -> None:
    """Change the number itself; its check digit no longer matches."""
    corrupted = "L898902X<3" + TD3_LINE_2[10:]
    assert parse_mrz(f"{TD3_LINE_1}\n{corrupted}") is None


def test_a_field_whose_checksum_fails_is_dropped_not_guessed() -> None:
    """A bad DOB digit costs the DOB, not the whole zone.

    The document number still validates, so the parse stands — but the field
    that failed comes back as None rather than as the value we could not verify.
    """
    corrupted = TD3_LINE_2[:19] + "7" + TD3_LINE_2[20:]  # DOB check 2 -> 7
    mrz = parse_mrz(f"{TD3_LINE_1}\n{corrupted}")

    assert mrz is not None
    assert mrz.document_number == "L898902C"
    assert mrz.date_of_birth is None
    assert mrz.checks_passed["date_of_birth"] is False
    assert mrz.all_checks_passed is False


def test_an_impossible_calendar_date_is_refused() -> None:
    """31 February validates arithmetically and still is not a date."""
    # 740231 with a recomputed check digit: the checksum passes, the date does not.
    bad_date = "740231"
    digit = mrz_check_digit(bad_date)
    line2 = TD3_LINE_2[:13] + bad_date + str(digit) + TD3_LINE_2[20:]
    mrz = parse_mrz(f"{TD3_LINE_1}\n{line2}")

    assert mrz is not None
    assert mrz.checks_passed["date_of_birth"] is True  # the digit is right
    assert mrz.date_of_birth is None  # the date still is not


def test_a_single_line_is_not_an_mrz() -> None:
    assert parse_mrz(TD3_LINE_2) is None


def test_ordinary_text_contains_no_mrz() -> None:
    assert parse_mrz("REPUBLIC OF KENYA\nNATIONAL IDENTITY CARD\nJANE DOE") is None


def test_empty_text_contains_no_mrz() -> None:
    assert parse_mrz("") is None


# --- TD1: identity cards ----------------------------------------------------


def _td1(
    *,
    document_code: str = "I<",
    state: str = "UTO",
    number: str = "D23145890",
    nationality: str = "UTO",
    birth: str = "740812",
    sex: str = "F",
    expiry: str = "120415",
    names: str = "ERIKSSON<<ANNA<MARIA",
) -> str:
    """Build a checksum-correct TD1 zone.

    Constructed rather than pasted so the checksums are right by computation.
    Building the specimen with the same helper the parser validates against
    would be circular, so only ``mrz_check_digit`` is shared — the field
    *offsets* here are written from the standard independently of the parser.
    """
    line1 = f"{document_code}{state}{number}{mrz_check_digit(number)}".ljust(30, "<")
    line2 = (
        f"{birth}{mrz_check_digit(birth)}{sex}"
        f"{expiry}{mrz_check_digit(expiry)}{nationality}"
    ).ljust(29, "<")
    composite = line1[5:30] + line2[0:7] + line2[8:15] + line2[18:29]
    line2 = line2 + str(mrz_check_digit(composite))
    line3 = names.ljust(30, "<")
    return f"{line1}\n{line2}\n{line3}"


def test_td1_identity_card_parses() -> None:
    mrz = parse_mrz(_td1())

    assert mrz is not None
    assert mrz.format == "TD1"
    assert mrz.document_number == "D23145890"
    assert mrz.date_of_birth == "1974-08-12"
    assert mrz.expiry_date == "2012-04-15"
    assert mrz.surname == "ERIKSSON"
    assert mrz.given_names == "ANNA MARIA"


def test_td1_with_a_broken_number_checksum_is_refused() -> None:
    zone = _td1()
    line1, line2, line3 = zone.splitlines()
    wrong = str((int(line1[14]) + 1) % 10)
    assert parse_mrz(f"{line1[:14]}{wrong}{line1[15:]}\n{line2}\n{line3}") is None


# --- classification ---------------------------------------------------------


def test_a_kenyan_national_id_is_classified_from_its_printed_text() -> None:
    text = (
        "JAMHURI YA KENYA\nREPUBLIC OF KENYA\nNATIONAL IDENTITY CARD\n"
        "SERIAL NUMBER: 123456789\nID NUMBER: 12345678\n"
        "FULL NAMES: JANE ALICE DOE\nDATE OF BIRTH: 12.04.1990\n"
        "DISTRICT OF BIRTH: NAIROBI\nPLACE OF ISSUE: NAIROBI\n"
    )
    result = classify(text)

    assert result.document_class == DocumentClass.KENYAN_NATIONAL_ID
    assert result.confidence > 0.9
    assert result.evidence  # a human can see why


def test_a_kenyan_id_outranks_the_generic_national_id_rule() -> None:
    """Both rules match; the specific one must win."""
    result = classify("REPUBLIC OF KENYA\nNATIONAL IDENTITY CARD\n")
    assert result.document_class == DocumentClass.KENYAN_NATIONAL_ID


def test_ordinary_english_words_do_not_score_as_a_passport() -> None:
    """Regression: "PLACE OF ISSUE" once matched a passport MRZ heuristic.

    The pattern ``^P[<A-Z][A-Z]{3}`` was meant to spot an MRZ first line, but
    under re.I|re.M it matches "PLACE" (P + L + ACE). On a Kenyan ID that
    manufactured a rival class, tripped the ambiguity penalty, and turned a
    certain classification into a reported coin flip — a confident wrong signal
    produced from nothing but a common English word.
    """
    for phrase in ("PLACE OF ISSUE", "PLACE OF BIRTH", "PARENT OR GUARDIAN"):
        result = classify(f"NATIONAL IDENTITY CARD\n{phrase}: NAIROBI\n")
        assert result.document_class != DocumentClass.PASSPORT
        assert "passport" not in " ".join(result.evidence).lower()


def test_a_passport_is_classified_from_its_mrz_document_code() -> None:
    result = classify(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert result.document_class == DocumentClass.PASSPORT
    assert result.confidence > 0.95


def test_a_validated_mrz_outranks_the_printed_keywords() -> None:
    """A Kenyan passport says "REPUBLIC OF KENYA" and is still a passport.

    Keyword-only scoring would call this a national ID, because the Kenyan rule
    carries the highest keyword weight. The MRZ document code settles it.
    """
    kenyan_passport = (
        "REPUBLIC OF KENYA\nJAMHURI YA KENYA\nPASSPORT\n"
        "P<KENOTIENO<<DAVID<OCHIENG<<<<<<<<<<<<<<<<<<\n"
        + _passport_line_2("A1234567<", "KEN", "850101", "M", "300101")
    )
    result = classify(kenyan_passport)

    assert result.document_class == DocumentClass.PASSPORT


def _passport_line_2(
    number: str, nationality: str, birth: str, sex: str, expiry: str
) -> str:
    """A checksum-correct TD3 line 2, built from the standard's field offsets."""
    head = (
        f"{number}{mrz_check_digit(number)}{nationality}"
        f"{birth}{mrz_check_digit(birth)}{sex}"
        f"{expiry}{mrz_check_digit(expiry)}"
    )
    body = head.ljust(43, "<")
    composite = body[0:10] + body[13:20] + body[21:43]
    return body + str(mrz_check_digit(composite))


def test_a_visa_inside_a_passport_is_classified_as_a_visa() -> None:
    """The page says "PASSPORT"; the visa's own zone says V. The zone wins."""
    visa = (
        "VISA\nMULTIPLE ENTRIES\nDURATION OF STAY: 90 DAYS\n"
        "V<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
        + _passport_line_2("L898902C<", "UTO", "740812", "F", "120415")
    )
    result = classify(visa)

    assert result.document_class == DocumentClass.VISA


def test_a_visa_is_classified_from_keywords_without_an_mrz() -> None:
    result = classify(
        "REPUBLIC OF KENYA\nVISA\nVISA TYPE: SINGLE ENTRY\n"
        "DURATION OF STAY: 30 DAYS\nPASSPORT NO: A1234567\n"
    )
    assert result.document_class == DocumentClass.VISA


def test_a_student_id_is_classified() -> None:
    result = classify(
        "UNIVERSITY OF NAIROBI\nSTUDENT IDENTITY CARD\n"
        "REG NO: E21/1234/2020\nFACULTY OF ENGINEERING\nJANE A. DOE\n"
    )
    assert result.document_class == DocumentClass.STUDENT_ID


def test_a_driving_licence_is_classified_in_either_spelling() -> None:
    assert (
        classify("DRIVING LICENCE\nCLASSES OF VEHICLE: B").document_class
        == DocumentClass.DRIVER_LICENSE
    )
    assert (
        classify("DRIVER'S LICENSE\nCLASS C").document_class
        == DocumentClass.DRIVER_LICENSE
    )


def test_a_td1_kenyan_card_is_classified_from_its_issuing_state() -> None:
    result = classify(_td1(document_code="I<", state="KEN", nationality="KEN"))
    assert result.document_class == DocumentClass.KENYAN_NATIONAL_ID


def test_a_td1_card_with_no_recognisable_keyword_still_classifies() -> None:
    """Keyword-first ordering would return UNKNOWN while holding a valid MRZ.

    A TD1 identity card in a language we have no keywords for carries nothing
    the rules table recognises. The zone is still definitive.
    """
    result = classify(_td1(document_code="I<", state="UTO", nationality="UTO"))

    assert result.document_class == DocumentClass.NATIONAL_ID
    assert result.confidence > 0.95


def test_an_unrecognised_mrz_document_code_is_other_not_a_guess() -> None:
    """A valid zone with a code outside the standard's set.

    OTHER, never PASSPORT: it is certainly a travel document, and picking the
    most common class would be inventing a fact the zone does not state.
    """
    result = classify(_td1(document_code="X<", state="UTO", nationality="UTO"))
    assert result.document_class == DocumentClass.OTHER


def test_unrecognisable_text_is_unknown_with_zero_confidence() -> None:
    result = classify("a grocery list\nmilk\nbread\ntwo onions")

    assert result.document_class == DocumentClass.UNKNOWN
    assert result.confidence == 0.0


def test_empty_ocr_output_is_unknown_not_a_guess() -> None:
    for empty in ("", "   ", "\n\n"):
        result = classify(empty)
        assert result.document_class == DocumentClass.UNKNOWN
        assert result.confidence == 0.0


def test_ambiguous_evidence_lowers_the_reported_confidence() -> None:
    """A document matching two classes about equally is not a settled call.

    The winner is still returned — but at a confidence that tells the caller to
    have a human look, rather than presenting a coin-flip as a fact.
    """
    ambiguous = classify("STUDENT ID CARD\nDRIVING LICENCE\nCLASSES OF VEHICLE: B")
    clear = classify("STUDENT ID CARD\nUNIVERSITY OF NAIROBI\nFACULTY OF LAW")

    assert ambiguous.confidence < clear.confidence


# --- classification from the finder's own words -----------------------------


def test_the_finders_words_map_onto_the_vocabulary() -> None:
    for typed, expected in [
        ("passport", DocumentClass.PASSPORT),
        ("Kenyan ID", DocumentClass.KENYAN_NATIONAL_ID),
        ("kenyan_national_id", DocumentClass.KENYAN_NATIONAL_ID),
        ("Student Card", DocumentClass.STUDENT_ID),
        ("driving licence", DocumentClass.DRIVER_LICENSE),
        ("VISA", DocumentClass.VISA),
    ]:
        assert classify_from_finder_input(typed).document_class == expected


def test_the_finders_words_are_weaker_evidence_than_the_document() -> None:
    """A person naming a document is not the document.

    The gap has to be visible in the score, or a caller cannot tell a typed
    guess from a validated read.
    """
    typed = classify_from_finder_input("passport")
    read = classify(f"{TD3_LINE_1}\n{TD3_LINE_2}")

    assert typed.confidence < read.confidence


def test_an_unrecognised_typed_type_is_other_not_a_wrong_class() -> None:
    result = classify_from_finder_input("library card")

    assert result.document_class == DocumentClass.OTHER
    assert result.confidence < 0.5


def test_no_typed_type_is_unknown() -> None:
    assert (
        classify_from_finder_input("").document_class == DocumentClass.UNKNOWN
    )


# --- vocabulary integrity ---------------------------------------------------


def test_every_class_is_a_plain_lowercase_token() -> None:
    """These values go into a database column and a URL query string."""
    for member in DocumentClass:
        assert member.value == member.value.lower()
        assert " " not in member.value


def test_the_portrait_table_only_names_real_classes() -> None:
    valid = {member.value for member in DocumentClass}
    assert set(CLASSES_WITH_PORTRAIT) <= valid


def test_unknown_and_other_carry_no_portrait_expectation() -> None:
    """Neither says anything about whether a face is on the document."""
    assert DocumentClass.UNKNOWN not in CLASSES_WITH_PORTRAIT
    assert DocumentClass.OTHER not in CLASSES_WITH_PORTRAIT
