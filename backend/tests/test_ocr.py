"""OCR extraction, exercised against synthetic ID card images.

These render text with PIL and feed it through the real Tesseract binary, so
they cover the actual extraction path rather than a mock. Recognition of
rendered text is far easier than a phone photo of a real card, so the
assertions focus on the parsing logic (which field ends up where) rather than
on raw OCR accuracy.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.ocr import (
    OcrUnavailableError,
    guess_document_type,
    guess_id_number,
    guess_name,
    run_tesseract,
)
from app.storage import InvalidImageError, load_image


def _font(size: int = 34) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_card(lines: list[str], width: int = 1000, height: int = 620) -> Image.Image:
    """Render a high-contrast fake ID card."""
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = _font(34)
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 56
    return image


NATIONAL_ID_LINES = [
    "REPUBLIC OF EXAMPLE",
    "NATIONAL IDENTITY CARD",
    "SURNAME: DOE",
    "GIVEN NAMES: JANE ALICE",
    "ID NO: AB1234567",
    "DATE OF BIRTH: 1990-04-12",
]


# --- pure parsing (no Tesseract) -------------------------------------------


def test_document_type_detection() -> None:
    assert guess_document_type("NATIONAL IDENTITY CARD") == "national_id"
    assert guess_document_type("PASSPORT\nP<GBRDOE<<JANE") == "passport"
    assert guess_document_type("DRIVER LICENSE\nCLASS C") == "driver_license"
    assert guess_document_type("STUDENT ID CARD") == "student_id"
    assert guess_document_type("random unrelated text") == "unknown"


def test_id_number_prefers_labelled_value() -> None:
    text = "NATIONAL ID CARD\nDATE OF BIRTH 1990-04-12\nID NO: AB1234567"
    assert guess_id_number(text) == "AB1234567"


def test_id_number_ignores_dates() -> None:
    text = "DATE OF BIRTH 12/04/1990\nEXPIRES 01/01/2030"
    assert guess_id_number(text) is None


def test_id_number_strips_separators() -> None:
    assert guess_id_number("ID NO: AB-123 4567") == "AB1234567"


def test_name_from_surname_and_given_labels() -> None:
    text = "SURNAME: DOE\nGIVEN NAMES: JANE ALICE"
    assert guess_name(text) == "Jane Alice Doe"


def test_name_from_single_name_label() -> None:
    assert guess_name("NAME: JANE ALICE DOE\nID NO: AB1") == "Jane Alice Doe"


def test_name_fallback_skips_boilerplate_lines() -> None:
    text = "REPUBLIC OF EXAMPLE\nNATIONAL IDENTITY CARD\nJane Alice Doe\n"
    assert guess_name(text) == "Jane Alice Doe"


# --- real Tesseract ---------------------------------------------------------


def test_tesseract_reads_rendered_card() -> None:
    """End-to-end check that the OCR binary is wired up and functioning."""
    image = render_card(NATIONAL_ID_LINES)
    try:
        text, confidence = run_tesseract(image)
    except OcrUnavailableError as exc:
        pytest.fail(f"Tesseract should be installed in this environment: {exc}")

    upper = text.upper()
    assert "IDENTITY" in upper
    assert "AB1234567" in upper.replace(" ", "")
    assert confidence is None or 0 <= confidence <= 100


def test_extraction_pipeline_on_rendered_card() -> None:
    image = render_card(NATIONAL_ID_LINES)
    text, _ = run_tesseract(image)

    assert guess_document_type(text) == "national_id"
    assert guess_id_number(text) == "AB1234567"
    name = guess_name(text)
    assert name is not None and "DOE" in name.upper()


def test_extraction_on_passport_layout() -> None:
    image = render_card(
        [
            "PASSPORT",
            "TYPE P CODE EXA",
            "PASSPORT NO: X12345678",
            "SURNAME: SMITH",
            "GIVEN NAMES: JOHN",
        ]
    )
    text, _ = run_tesseract(image)
    assert guess_document_type(text) == "passport"
    assert guess_id_number(text) == "X12345678"


# --- upload validation ------------------------------------------------------


def test_load_image_accepts_valid_png() -> None:
    buffer = io.BytesIO()
    render_card(["TEST"], width=200, height=100).save(buffer, format="PNG")
    assert load_image(buffer.getvalue()).size == (200, 100)


def test_load_image_rejects_non_image_bytes() -> None:
    with pytest.raises(InvalidImageError):
        load_image(b"this is definitely not an image")


def test_load_image_rejects_empty_upload() -> None:
    with pytest.raises(InvalidImageError):
        load_image(b"")


def test_load_image_rejects_oversized_upload() -> None:
    with pytest.raises(InvalidImageError, match="8 MB"):
        load_image(b"\x89PNG" + b"0" * (9 * 1024 * 1024))
