"""Normalization and redaction are the core of matching and privacy."""
from __future__ import annotations

import pytest

from app.normalize import (
    normalize_id_number,
    normalize_name,
    redact_id_number,
    redact_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AB-123 456", "AB123456"),
        ("ab123456", "AB123456"),
        ("  AB123456  ", "AB123456"),
        ("A/B.123-456", "AB123456"),
        ("", ""),
    ],
)
def test_id_number_normalization_is_cosmetic_only(raw: str, expected: str) -> None:
    assert normalize_id_number(raw) == expected


def test_different_id_numbers_stay_different() -> None:
    # Normalization must not collapse genuinely distinct numbers.
    assert normalize_id_number("AB123456") != normalize_id_number("AB123457")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  John   Smith ", "JOHN SMITH"),
        ("john smith", "JOHN SMITH"),
        ("John O'Brien-Smith", "JOHN O'BRIEN-SMITH"),
        ("Jane  A.  Doe", "JANE A DOE"),
    ],
)
def test_name_normalization(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_name_normalization_preserves_token_order() -> None:
    # Exact match, not fuzzy: reversed names must not match.
    assert normalize_name("John Smith") != normalize_name("Smith John")


def test_redact_name_keeps_initials_and_length() -> None:
    assert redact_name("John Michael Smith") == "J*** M****** S****"


def test_redact_name_handles_single_letter_tokens() -> None:
    assert redact_name("Jane A Doe") == "J*** A D**"


def test_redact_id_number_hides_all_but_suffix() -> None:
    assert redact_id_number("AB123456") == "******56"


def test_redact_id_number_normalizes_first() -> None:
    assert redact_id_number("ab-123 456") == "******56"


def test_redact_id_number_on_short_value_reveals_nothing() -> None:
    assert redact_id_number("AB") == "**"


def test_redaction_does_not_leak_full_value() -> None:
    secret = "XY987654321"
    redacted = redact_id_number(secret)
    assert secret not in redacted
    assert redacted.count("*") == len(secret) - 2
