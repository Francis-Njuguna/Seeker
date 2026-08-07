"""Face verification for selfie-against-posted-ID checks.

Like document-image OCR verification, this is honest about what it is: a similarity
measure against one reference photo, not identity proofing. A claimant who passes
this check supplied an image in which *a* face resembles the face on the posted
document — that is evidence in their favor, but it is not biometric authentication,
and the finder still decides the claim.

Privacy: the claimant's selfie is decoded in memory, reduced to an embedding for
comparison, and dropped. It is never written to disk, and the selfie embedding is
never stored — only the verdict (passed, confidence) persists on the match.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Any

from PIL import Image

# Import guard: face_recognition pulls in dlib, a native extension, and numpy is
# only present as its dependency. Both are optional — without them selfie checks
# return 503 and the ID-number and document-image claim paths keep working, so
# the app still runs. Nothing at module scope may assume either is importable.
try:  # pragma: no cover - import guard
    import numpy as np
    import face_recognition
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    face_recognition = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np  # noqa: F811


class FaceUnavailableError(RuntimeError):
    """Raised when no face backend is available."""


EUCLIDEAN_THRESHOLD = 0.6  # Library default; may be tuned
MODEL_NAME = "face_recognition"  # For the face_embedding_model column


def embedding_available() -> bool:
    """Whether a face backend is ready."""
    return face_recognition is not None and np is not None


def _require_backend():
    if not embedding_available():
        raise FaceUnavailableError(
            "Face verification is unavailable: face_recognition library not found"
        )


def extract_embedding(image: Image.Image) -> "np.ndarray | None":
    """Extract the 128-d embedding for the first face in the image.

    Returns ``None`` when no face is detectable. Does not raise on multi-face
    images; it picks the first.
    """
    _require_backend()
    rgb = np.array(image.convert("RGB"))
    encodings = face_recognition.face_encodings(rgb, num_jitters=1)
    return encodings[0] if encodings else None


def serialize(embedding: "np.ndarray") -> bytes:
    """Pack the 128 float64 values into 1024 bytes for the LargeBinary column.

    struct rather than ``np.tobytes`` so the encoding is explicit and readable
    back without numpy present.
    """
    return struct.pack(f"{len(embedding)}d", *embedding)


def deserialize(data: bytes) -> "np.ndarray":
    """Unpack from the DB back to a numpy array."""
    _require_backend()
    return np.array(struct.unpack(f"{len(data) // 8}d", data))


@dataclass(frozen=True)
class FaceOutcome:
    """Result of comparing a selfie against the known embedding."""

    passed: bool
    confidence: float  # 0–1, derived from euclidean distance
    distance: float  # raw euclidean
    detail: str
    face_found_in_selfie: bool

    @property
    def as_percentage(self) -> int:
        """Confidence as an integer 0–100 for the UI."""
        return round(self.confidence * 100)


def compare(
    known_embedding: "np.ndarray",
    selfie: Image.Image,
    *,
    threshold: float | None = None,
) -> FaceOutcome:
    """Compare the claimant's selfie against the embedding from the posted ID.

    Threshold defaults to EUCLIDEAN_THRESHOLD from config. Distance is clamped to
    [0, threshold] then inverted to produce a 0–1 confidence: 0.0 distance → 100%
    confidence, threshold distance → 0% confidence.
    """
    _require_backend()
    threshold = threshold if threshold is not None else EUCLIDEAN_THRESHOLD

    selfie_embedding = extract_embedding(selfie)
    if selfie_embedding is None:
        return FaceOutcome(
            passed=False,
            confidence=0.0,
            distance=999.0,  # sentinel
            detail="No face detected in selfie",
            face_found_in_selfie=False,
        )

    distance = float(np.linalg.norm(known_embedding - selfie_embedding))
    clamped = min(distance, threshold)
    confidence = 1.0 - (clamped / threshold)
    passed = distance < threshold

    detail = (
        f"Face match confidence {round(confidence * 100)}% "
        f"(distance {distance:.3f}, threshold {threshold})"
    )
    return FaceOutcome(
        passed=passed,
        confidence=confidence,
        distance=distance,
        detail=detail,
        face_found_in_selfie=True,
    )


def load_and_compare(
    known_bytes: bytes, selfie_raw: bytes, *, threshold: float | None = None
) -> FaceOutcome:
    """Convenience: deserialize the known embedding and decode the selfie in one call."""
    known = deserialize(known_bytes)
    selfie = Image.open(BytesIO(selfie_raw))
    return compare(known, selfie, threshold=threshold)
