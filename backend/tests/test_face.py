"""Selfie verification: the maths, the gate, and what happens when it fails.

The comparison logic is exercised against hand-built embedding vectors rather
than real portrait photos — the arithmetic of euclidean distance is what decides
a claim, and it is testable without shipping anyone's face into the repo.

``face_recognition`` and its dlib extension are optional (see ``app/face.py``),
so every test that needs a real backend skips when one is absent. The tests that
matter most for safety — the 503/422 paths, the attempt cap, the flag on failure
— are written to hold either way.
"""
from __future__ import annotations

import io
import struct

import httpx
import pytest
from PIL import Image

from app import face

# No module-level asyncio mark: this file mixes sync unit tests with async HTTP
# ones, and pytest.ini already runs in asyncio auto mode.

needs_backend = pytest.mark.skipif(
    not face.embedding_available(),
    reason="face_recognition/dlib not installed in this environment",
)


def _selfie_png(colour: tuple[int, int, int] = (180, 150, 130)) -> bytes:
    """A decodable image with no face in it."""
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), colour).save(buffer, format="PNG")
    return buffer.getvalue()


# --- serialization ----------------------------------------------------------


def test_serialize_packs_eight_bytes_per_dimension() -> None:
    """128 float64s must fit the 1024-byte LargeBinary column exactly."""
    packed = face.serialize([0.5] * 128)
    assert len(packed) == 1024


def test_serialize_is_readable_without_numpy() -> None:
    """struct rather than ndarray.tobytes, so the encoding stays inspectable."""
    values = [0.25, -1.5, 3.0]
    unpacked = struct.unpack("3d", face.serialize(values))
    assert list(unpacked) == values


@needs_backend
def test_embedding_round_trips_through_the_database_encoding() -> None:
    import numpy as np

    original = np.linspace(-1.0, 1.0, 128)
    restored = face.deserialize(face.serialize(original))
    assert restored.shape == (128,)
    assert np.allclose(original, restored)


def test_deserialize_without_a_backend_raises_rather_than_guessing() -> None:
    if face.embedding_available():
        pytest.skip("backend present, nothing to assert about its absence")
    with pytest.raises(face.FaceUnavailableError):
        face.deserialize(b"\x00" * 1024)


# --- the confidence curve ---------------------------------------------------


def _blank_image() -> Image.Image:
    return Image.new("RGB", (10, 10))


@needs_backend
@pytest.mark.parametrize(
    ("distance", "expect_pass", "expect_confidence"),
    [
        (0.0, True, 1.0),  # same person, same photo
        (0.3, True, 0.5),  # half the threshold, comfortably inside
        (0.6, False, 0.0),  # exactly at the threshold: not a match
        (1.2, False, 0.0),  # a different person, clamped rather than negative
    ],
)
def test_confidence_falls_linearly_to_zero_at_the_threshold(
    monkeypatch, distance: float, expect_pass: bool, expect_confidence: float
) -> None:
    """``compare`` is the function that decides a claim, so drive it directly.

    The selfie's embedding is substituted at a known euclidean distance from the
    reference — real portraits would test dlib, not this arithmetic.
    """
    import numpy as np

    known = np.zeros(128)
    probe = np.zeros(128)
    probe[0] = distance

    monkeypatch.setattr(face, "extract_embedding", lambda image: probe)
    outcome = face.compare(known, _blank_image(), threshold=0.6)

    assert outcome.passed is expect_pass
    assert outcome.confidence == pytest.approx(expect_confidence)
    assert outcome.distance == pytest.approx(distance)
    assert outcome.face_found_in_selfie


@needs_backend
def test_a_selfie_with_no_face_fails_without_a_distance(monkeypatch) -> None:
    import numpy as np

    monkeypatch.setattr(face, "extract_embedding", lambda image: None)
    outcome = face.compare(np.zeros(128), _blank_image())

    assert not outcome.passed
    assert not outcome.face_found_in_selfie
    assert outcome.confidence == 0.0
    assert "No face detected" in outcome.detail


@needs_backend
def test_threshold_is_configurable_per_call(monkeypatch) -> None:
    """A distance that fails at 0.6 can pass under a looser policy."""
    import numpy as np

    probe = np.zeros(128)
    probe[0] = 0.8
    monkeypatch.setattr(face, "extract_embedding", lambda image: probe)

    assert not face.compare(np.zeros(128), _blank_image(), threshold=0.6).passed
    assert face.compare(np.zeros(128), _blank_image(), threshold=0.9).passed


def test_outcome_reports_confidence_as_a_ui_percentage() -> None:
    outcome = face.FaceOutcome(
        passed=True,
        confidence=0.834,
        distance=0.1,
        detail="",
        face_found_in_selfie=True,
    )
    assert outcome.as_percentage == 83


def test_threshold_is_the_documented_library_default() -> None:
    """0.6 is face_recognition's published default, not a tuned value."""
    from app.config import settings

    assert face.EUCLIDEAN_THRESHOLD == 0.6
    assert settings.face_match_threshold == 0.6


def test_availability_check_is_safe_to_call_with_nothing_installed() -> None:
    """The whole point of the import guard: this never raises."""
    assert isinstance(face.embedding_available(), bool)


# --- the HTTP gate ----------------------------------------------------------


async def _matched_pair(client, make_user, post_found, id_number="AB123456"):
    finder = await make_user()
    seeker = await make_user()
    found = await post_found(finder, id_number=id_number)
    search = await client.post(
        "/api/search", json={"id_number": id_number}, headers=seeker.headers
    )
    return finder, seeker, found, search.json()["match_ids"][0]


async def _face_attempts(client, seeker, match_id) -> int:
    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    entry = next(m for m in matches.json() if m["match"]["id"] == match_id)
    return entry["match"]["face_attempts"]


async def test_stranger_cannot_submit_a_selfie_for_someone_elses_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, _, _, match_id = await _matched_pair(client, make_user, post_found)
    attacker = await make_user()

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("me.png", _selfie_png(), "image/png")},
        headers=attacker.headers,
    )
    assert response.status_code == 404


async def test_an_unavailable_check_never_counts_against_the_claimant(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """503 (no backend) and 422 (no face on the posted photo) are both outages
    from the claimant's point of view, so neither may burn an attempt.

    ``post_found`` uploads no image, so there is no embedding on record — which
    is the 422 case when a backend is installed and the 503 case when it is not.
    """
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("me.png", _selfie_png(), "image/png")},
        headers=seeker.headers,
    )
    assert response.status_code in (422, 503)
    assert await _face_attempts(client, seeker, match_id) == 0


async def test_selfie_check_does_not_apply_once_a_claim_is_pending(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("me.png", _selfie_png(), "image/png")},
        headers=seeker.headers,
    )
    assert response.status_code == 409


async def test_face_fields_start_unverified_on_a_new_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    match = matches.json()[0]["match"]
    assert match["face_verification_status"] == "unverified"
    assert match["face_confidence"] is None
    assert match["face_attempts"] == 0
    assert match["flagged_for_review"] is False


async def test_no_face_on_record_is_recorded_distinctly_from_a_failure(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """A document with no portrait is not the claimant's fault, so it must not
    read as a failed check or flag them for review."""
    if not face.embedding_available():
        pytest.skip("the 422 branch is only reachable with a backend installed")

    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("me.png", _selfie_png(), "image/png")},
        headers=seeker.headers,
    )
    assert response.status_code == 422

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    match = matches.json()[0]["match"]
    assert match["face_verification_status"] == "no_face_on_record"
    assert match["flagged_for_review"] is False


async def test_verified_selfie_is_an_accepted_proof_for_a_claim(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """The third rung of the ladder: a passed selfie claims without the number.

    The verdict is set directly here rather than through a real portrait — what
    is under test is that ``request_claim`` honours it, not the maths.
    """
    import uuid

    from sqlalchemy import update

    from app.models import FaceVerificationStatus, Match

    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await db_session.execute(
        update(Match)
        .where(Match.id == uuid.UUID(match_id))
        .values(face_verification_status=FaceVerificationStatus.VERIFIED)
    )
    await db_session.commit()

    response = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "claim_requested"
    assert body["verification_method"] == "face_selfie"


async def test_claim_without_any_proof_is_refused(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """No number, no document photo, no selfie — nothing to go on."""
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    response = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert response.status_code == 403


async def test_a_failed_selfie_flags_the_match_for_a_human(
    client: httpx.AsyncClient, make_user, post_found, db_session, promote_admin
) -> None:
    """The spec's requirement: reject the claim and flag it for manual review.

    The flag is set the way the endpoint sets it, then the assertion is that it
    reaches the admin queue — which is the part a human depends on.
    """
    import uuid

    from sqlalchemy import update

    from app.models import FaceVerificationStatus, Match

    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await db_session.execute(
        update(Match)
        .where(Match.id == uuid.UUID(match_id))
        .values(
            face_verification_status=FaceVerificationStatus.FAILED,
            face_confidence=0.21,
            face_attempts=1,
            flagged_for_review=True,
            flag_reason="Selfie did not match the photo on the posted document.",
        )
    )
    await db_session.commit()

    # A failed selfie is not a proof, so the claim is still refused.
    claim = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert claim.status_code == 403

    admin = await make_user()
    await promote_admin(admin)
    flagged = await client.get("/api/admin/flagged", headers=admin.headers)
    assert [row["match_id"] for row in flagged.json()] == [match_id]
    assert flagged.json()[0]["face_verification_status"] == "failed"
    assert flagged.json()[0]["flag_reason"]


async def test_attempt_cap_stops_an_image_by_image_fishing_expedition(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    import uuid

    from sqlalchemy import update

    from app.config import settings
    from app.models import Match

    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await db_session.execute(
        update(Match)
        .where(Match.id == uuid.UUID(match_id))
        .values(face_attempts=settings.face_max_attempts)
    )
    await db_session.commit()

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("me.png", _selfie_png(), "image/png")},
        headers=seeker.headers,
    )
    assert response.status_code == 429


async def test_an_already_verified_claim_is_not_rechecked(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    import uuid

    from sqlalchemy import update

    from app.models import FaceVerificationStatus, Match

    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await db_session.execute(
        update(Match)
        .where(Match.id == uuid.UUID(match_id))
        .values(face_verification_status=FaceVerificationStatus.VERIFIED)
    )
    await db_session.commit()

    response = await client.post(
        f"/api/matches/{match_id}/verify-face",
        files={"selfie": ("me.png", _selfie_png(), "image/png")},
        headers=seeker.headers,
    )
    assert response.status_code == 409


def test_selfies_are_never_written_to_disk() -> None:
    """The privacy constraint, asserted against the source rather than a run.

    A selfie is decoded in memory and dropped; only the verdict persists. A
    regression here would be silent at runtime, so the check is that the module
    contains no filesystem write at all. ``Image.open`` on a BytesIO is a decode,
    not a write, which is why the check names writes specifically.
    """
    import inspect

    source = inspect.getsource(face)
    for forbidden in (".save(", "UPLOAD_DIR", "upload_dir", "write_bytes", "os.path"):
        assert forbidden not in source, f"face.py must not reference {forbidden}"


async def test_the_selfie_verdict_is_stored_but_no_embedding_of_it_is(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """Only the posted document's embedding is persisted, never the claimant's.

    ``Match`` carries the verdict columns and no binary column, which is what
    keeps "store embeddings, not selfies" true structurally rather than by
    convention.
    """
    from sqlalchemy import LargeBinary

    from app.models import Match

    binary_columns = [
        column.name
        for column in Match.__table__.columns
        if isinstance(column.type, LargeBinary)
    ]
    assert binary_columns == []
