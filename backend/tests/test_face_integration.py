"""Face recognition against real photographs, end to end.

``test_face.py`` covers the arithmetic — how a distance becomes a verdict — using
hand-built vectors. That is fast and deterministic, but it proves nothing about
whether dlib can actually tell two people apart. These tests close that gap: every
one of them runs a real JPEG through the real detector and the real ResNet encoder.

Nothing here is mocked. If ``face_recognition`` or the fixture photographs are
absent the module skips with an actionable message, because a skipped test that
says why is honest and a passing test that mocked the engine is not.

Fixture provenance and licensing: ``tests/fixtures/faces/README.md``.
"""
from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app import face

FIXTURES = Path(__file__).parent / "fixtures" / "faces"

SINGLE_FACE = FIXTURES / "obama.jpg"  # one face
SAME_PERSON = FIXTURES / "obama2.jpg"  # same person, different photograph
OTHER_PERSON = FIXTURES / "biden.jpg"  # a different person
TWO_FACES = FIXTURES / "two_people.jpg"  # two faces

_REQUIRED = (SINGLE_FACE, SAME_PERSON, OTHER_PERSON, TWO_FACES)

# Both conditions are module-level: without an engine or without photographs there
# is no real pipeline to exercise, and a mocked stand-in would defeat the purpose.
pytestmark = [
    pytest.mark.skipif(
        not face.embedding_available(),
        reason=(
            "face_recognition/dlib not installed. Install with: "
            "pip install dlib-bin face_recognition_models && "
            "pip install --no-deps face-recognition"
        ),
    ),
    pytest.mark.skipif(
        not all(path.exists() for path in _REQUIRED),
        reason=(
            f"face fixture photographs missing from {FIXTURES}. "
            "See that directory's README.md for the download command."
        ),
    ),
]


def load(path: Path) -> Image.Image:
    return Image.open(path)


# --- detection --------------------------------------------------------------


def test_a_portrait_yields_exactly_one_face() -> None:
    assert face.detect_faces(load(SINGLE_FACE)) == 1


def test_an_image_with_two_people_reports_both() -> None:
    """The detector must see both, so the caller can refuse the ambiguity."""
    assert face.detect_faces(load(TWO_FACES)) == 2


def test_a_photograph_with_no_person_in_it_yields_no_faces() -> None:
    blank = Image.new("RGB", (400, 400), (180, 150, 130))
    assert face.detect_faces(blank) == 0


# --- embeddings -------------------------------------------------------------


def test_the_encoder_returns_a_128_dimension_vector() -> None:
    embedding = face.extract_embedding(load(SINGLE_FACE))
    assert embedding is not None
    assert embedding.shape == (128,)


def test_encoding_the_same_photograph_twice_is_deterministic() -> None:
    """Same input, same vector — otherwise a stored embedding would rot."""
    import numpy as np

    first = face.extract_embedding(load(SINGLE_FACE))
    second = face.extract_embedding(load(SINGLE_FACE))
    assert np.allclose(first, second)


def test_an_image_with_no_face_encodes_to_nothing() -> None:
    assert face.extract_embedding(Image.new("RGB", (400, 400), (200, 190, 180))) is None


def test_two_faces_are_refused_rather_than_silently_picking_one() -> None:
    """The security property: a claimant standing beside the rightful holder
    must not be able to pass by having *a* matching face in frame."""
    with pytest.raises(face.MultiFaceError):
        face.extract_embedding(load(TWO_FACES))


def test_a_stored_embedding_survives_the_database_round_trip() -> None:
    """Serialize, restore, and compare — a lossy encoding would silently drift
    every stored reference away from the selfies it is checked against."""
    import numpy as np

    original = face.extract_embedding(load(SINGLE_FACE))
    packed = face.serialize(original)
    assert len(packed) == 1024  # 128 float64

    restored = face.deserialize(packed)
    assert np.allclose(original, restored)
    # And it still compares as the same person after the round trip.
    assert face.compare(restored, load(SINGLE_FACE)).passed


# --- matching ---------------------------------------------------------------


def test_the_same_person_in_a_different_photograph_matches() -> None:
    known = face.extract_embedding(load(SINGLE_FACE))
    outcome = face.compare(known, load(SAME_PERSON))

    assert outcome.passed
    assert outcome.face_found_in_selfie
    assert outcome.distance < face.EUCLIDEAN_THRESHOLD
    assert outcome.confidence > 0


def test_a_different_person_does_not_match() -> None:
    known = face.extract_embedding(load(SINGLE_FACE))
    outcome = face.compare(known, load(OTHER_PERSON))

    assert not outcome.passed
    assert outcome.face_found_in_selfie  # a face *was* found; it is the wrong one
    assert outcome.distance > face.EUCLIDEAN_THRESHOLD


def test_the_threshold_separates_the_two_cases_with_room_to_spare() -> None:
    """Guards the threshold itself, not just which side of it a photo lands on.

    If a future model or preprocessing change squeezed these two distances
    together, every test above could still pass while the system became unable
    to tell anyone apart in practice. Asserting on the gap catches that.
    """
    known = face.extract_embedding(load(SINGLE_FACE))
    same = face.compare(known, load(SAME_PERSON)).distance
    different = face.compare(known, load(OTHER_PERSON)).distance

    assert same < face.EUCLIDEAN_THRESHOLD < different
    assert different - same > 0.2


def test_comparing_a_photograph_against_itself_is_a_perfect_match() -> None:
    known = face.extract_embedding(load(SINGLE_FACE))
    outcome = face.compare(known, load(SINGLE_FACE))

    assert outcome.passed
    assert outcome.distance == pytest.approx(0.0, abs=1e-9)
    assert outcome.confidence == pytest.approx(1.0)


def test_a_selfie_with_two_faces_is_refused_by_compare() -> None:
    known = face.extract_embedding(load(SINGLE_FACE))
    with pytest.raises(face.MultiFaceError):
        face.compare(known, load(TWO_FACES))


def test_a_selfie_with_no_face_is_a_clean_negative_not_a_crash() -> None:
    known = face.extract_embedding(load(SINGLE_FACE))
    outcome = face.compare(known, Image.new("RGB", (400, 400), (180, 150, 130)))

    assert not outcome.passed
    assert not outcome.face_found_in_selfie
    assert outcome.confidence == 0.0


def test_a_tighter_threshold_can_reject_a_genuine_match() -> None:
    """Threshold is policy, not a constant — the same pair flips with it."""
    known = face.extract_embedding(load(SINGLE_FACE))

    assert face.compare(known, load(SAME_PERSON), threshold=0.6).passed
    assert not face.compare(known, load(SAME_PERSON), threshold=0.05).passed


def test_a_downscaled_selfie_still_matches() -> None:
    """_prepare() shrinks anything over MAX_DIMENSION. A resize must not push a
    genuine claimant over the threshold, or large uploads would fail at random."""
    known = face.extract_embedding(load(SINGLE_FACE))
    big = load(SAME_PERSON)
    upscaled = big.resize((big.width * 2, big.height * 2))

    assert face.compare(known, upscaled).passed


def test_load_and_compare_accepts_raw_bytes_from_an_upload() -> None:
    """The path the endpoint actually takes: packed embedding + raw upload bytes."""
    known = face.serialize(face.extract_embedding(load(SINGLE_FACE)))

    assert face.load_and_compare(known, SAME_PERSON.read_bytes()).passed
    assert not face.load_and_compare(known, OTHER_PERSON.read_bytes()).passed


# --- through the API --------------------------------------------------------


def _jpeg_bytes(path: Path) -> bytes:
    return path.read_bytes()


async def _post_found_with_photo(client, user, photo: Path, id_number: str) -> dict:
    """Post a found document carrying a real portrait, so an embedding is stored."""
    response = await client.post(
        "/api/found-ids",
        data={
            "document_type": "national_id",
            "holder_name": "Jane Alice Doe",
            "id_number": id_number,
            "found_location": "Central Station",
            "run_ocr": "false",
        },
        files={"image": (photo.name, _jpeg_bytes(photo), "image/jpeg")},
        headers=user.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _match_for(client, seeker, id_number: str) -> str:
    search = await client.post(
        "/api/search", json={"id_number": id_number}, headers=seeker.headers
    )
    assert search.status_code == 200, search.text
    return search.json()["match_ids"][0]


async def test_a_genuine_selfie_verifies_the_claim_through_the_api(
    client: httpx.AsyncClient, make_user, promote_admin
) -> None:
    """The whole pipeline: upload a portrait, store its embedding, then verify a
    different photograph of the same person against it over HTTP."""
    finder = await make_user()
    seeker = await make_user()
    await _post_found_with_photo(client, finder, SINGLE_FACE, "FACE0001")
    match_id = await _match_for(client, seeker, "FACE0001")

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("selfie.jpg", _jpeg_bytes(SAME_PERSON), "image/jpeg")},
        headers=seeker.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is True
    assert body["status"] == "verified"
    assert body["face_found_in_selfie"] is True
    assert body["flagged_for_review"] is False

    # And the verdict is now a usable proof for the claim itself.
    claim = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["verification_method"] == "face_selfie"


async def test_an_impostor_selfie_fails_and_is_flagged_for_a_human(
    client: httpx.AsyncClient, make_user, promote_admin
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await _post_found_with_photo(client, finder, SINGLE_FACE, "FACE0002")
    match_id = await _match_for(client, seeker, "FACE0002")

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("selfie.jpg", _jpeg_bytes(OTHER_PERSON), "image/jpeg")},
        headers=seeker.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is False
    assert body["status"] == "failed"
    assert body["flagged_for_review"] is True

    # A failed check is not a proof, so the claim stays refused.
    claim = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert claim.status_code == 403

    # ...and it reaches the queue a human actually reads.
    admin = await make_user()
    await promote_admin(admin)
    flagged = await client.get("/api/admin/flagged", headers=admin.headers)
    assert [row["match_id"] for row in flagged.json()] == [match_id]


async def test_a_selfie_containing_two_people_is_rejected_without_burning_an_attempt(
    client: httpx.AsyncClient, make_user
) -> None:
    """Ambiguity is not the claimant's failure, so it costs no attempt — the same
    treatment a corrupt upload gets."""
    finder = await make_user()
    seeker = await make_user()
    await _post_found_with_photo(client, finder, SINGLE_FACE, "FACE0003")
    match_id = await _match_for(client, seeker, "FACE0003")

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("selfie.jpg", _jpeg_bytes(TWO_FACES), "image/jpeg")},
        headers=seeker.headers,
    )
    assert response.status_code == 422
    assert "face" in response.json()["detail"].lower()

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    entry = next(m for m in matches.json() if m["match"]["id"] == match_id)
    assert entry["match"]["face_attempts"] == 0
    assert entry["match"]["flagged_for_review"] is False


async def test_a_selfie_with_no_face_is_recorded_distinctly_from_a_mismatch(
    client: httpx.AsyncClient, make_user
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await _post_found_with_photo(client, finder, SINGLE_FACE, "FACE0004")
    match_id = await _match_for(client, seeker, "FACE0004")

    buffer = io.BytesIO()
    Image.new("RGB", (400, 400), (180, 150, 130)).save(buffer, format="JPEG")

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("selfie.jpg", buffer.getvalue(), "image/jpeg")},
        headers=seeker.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is False
    assert body["face_found_in_selfie"] is False
    assert body["status"] == "no_face_in_selfie"


async def test_a_corrupt_upload_is_a_validation_error_not_a_crash(
    client: httpx.AsyncClient, make_user
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await _post_found_with_photo(client, finder, SINGLE_FACE, "FACE0005")
    match_id = await _match_for(client, seeker, "FACE0005")

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("selfie.jpg", b"this is not a JPEG", "image/jpeg")},
        headers=seeker.headers,
    )
    assert response.status_code == 400


async def test_a_document_photo_with_two_faces_stores_no_reference_embedding(
    client: httpx.AsyncClient, make_user
) -> None:
    """Posting must still succeed — a bystander in frame is not a reason to
    refuse a found document — but the ambiguous embedding must not be stored,
    so a later selfie check reports "no face on record" instead of comparing
    against whichever face the detector happened to return first."""
    finder = await make_user()
    seeker = await make_user()
    await _post_found_with_photo(client, finder, TWO_FACES, "FACE0006")
    match_id = await _match_for(client, seeker, "FACE0006")

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("selfie.jpg", _jpeg_bytes(SINGLE_FACE), "image/jpeg")},
        headers=seeker.headers,
    )
    assert response.status_code == 422

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    entry = next(m for m in matches.json() if m["match"]["id"] == match_id)
    assert entry["match"]["face_verification_status"] == "no_face_on_record"
    assert entry["match"]["flagged_for_review"] is False


async def test_no_embedding_is_ever_returned_through_the_api(
    client: httpx.AsyncClient, make_user, promote_admin
) -> None:
    """Biometric data stays server-side. The verdict travels; the vector does not.

    Checked against the raw response text rather than parsed fields, so a future
    schema addition that leaks one fails here.
    """
    finder = await make_user()
    seeker = await make_user()
    await _post_found_with_photo(client, finder, SINGLE_FACE, "FACE0007")
    match_id = await _match_for(client, seeker, "FACE0007")

    verify = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("selfie.jpg", _jpeg_bytes(SAME_PERSON), "image/jpeg")},
        headers=seeker.headers,
    )
    admin = await make_user()
    await promote_admin(admin)

    payloads = [
        verify.text,
        (await client.get("/api/matches/mine", headers=seeker.headers)).text,
        (await client.get("/api/admin/claims", headers=admin.headers)).text,
    ]
    for payload in payloads:
        assert "embedding" not in payload.lower()
