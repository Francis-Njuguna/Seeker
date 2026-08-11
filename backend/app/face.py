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
from typing import TYPE_CHECKING

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

# Number of upsampling passes the face detector performs. The library default is
# 1; 2 catches more small faces at some CPU cost. The reference embedding was
# encoded with 1, and detection must be deterministic across document and selfie,
# so the two must agree — they both use this value.
DETECTION_UPSAMPLE = 1

# What "a valid portrait" means for a claim. The reference embedding stored at
# posting time is encoded from the first face, so a selfie is only comparable
# when it contains exactly one face. Multiple faces are ambiguous (which one is
# the claimant?), so they are rejected rather than silently compared against the
# first — a photo with a poster in the background would otherwise sail through.
MAX_FACES = 1

# dlib's detector builds an image pyramid, so keep the largest dimension bounded
# or a 60-megapixel upload allocates one proportionally huge. This is a
# proportional downscale, not a crop — no part of the frame is discarded. Both
# the document photo and the selfie go through this same function, so the two
# embeddings stay directly comparable.
MAX_DIMENSION = 1600

# How many times the encoder re-samples each face (jittered crops, averaged) when
# producing an embedding. 1 means "encode once". Raising it costs linear time for
# a small accuracy gain, and — critically — must match between the document photo
# and the selfie, since jitter changes the resulting vector. Both paths call
# extract_embedding, so they share this value by construction.
NUM_JITTERS = 1


class FaceUnavailableError(RuntimeError):
    """Raised when no face backend is available."""


class MultiFaceError(ValueError):
    """Raised when an image contains more faces than ``MAX_FACES``."""


# The metric is euclidean (L2) distance between two 128-d embeddings, so SMALLER
# means MORE similar — 0.0 is the same image. 0.6 is the threshold dlib's authors
# derived for this model: on the Labeled Faces in the Wild benchmark it yields
# ~99.38% accuracy, and it is what face_recognition.compare_faces defaults to. It
# is not a probability and does not translate to one.
#
# Chosen rather than tuned because tuning needs a labelled set of *this* app's
# images — phone selfies against photographed ID cards — which does not exist yet.
# The library default is the defensible starting point; settings.face_match_threshold
# (FACE_MATCH_THRESHOLD) overrides it once there is data to justify a different
# number. Lower it to demand a closer match (fewer false accepts, more rejected
# legitimate claimants); raise it for the opposite trade.
EUCLIDEAN_THRESHOLD = 0.6
MODEL_NAME = "face_recognition"  # For the face_embedding_model column


def embedding_available() -> bool:
    """Whether a face backend is ready."""
    return face_recognition is not None and np is not None


def _require_backend():
    if not embedding_available():
        raise FaceUnavailableError(
            "Face verification is unavailable: face_recognition library not found"
        )


def _prepare(image: Image.Image) -> "np.ndarray":
    """Convert a PIL image to the RGB uint8 array the detector expects.

    The size guard runs before any conversion so a huge file never gets fully
    decoded into an ndarray.
    """
    if image.width > MAX_DIMENSION or image.height > MAX_DIMENSION:
        image = image.copy()
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    return np.array(image.convert("RGB"), dtype=np.uint8)


def detect_faces(image: Image.Image) -> int:
    """Count the faces in an image. Raises ``FaceUnavailableError`` if the
    backend is missing, so "the engine is down" never reads as "no face"."""
    _require_backend()
    rgb = _prepare(image)
    return len(face_recognition.face_locations(rgb, number_of_times_to_upsample=DETECTION_UPSAMPLE))


def extract_embedding(image: Image.Image) -> "np.ndarray | None":
    """Extract the 128-d embedding for the single face in the image.

    Returns ``None`` when no face is detectable. Raises ``MultiFaceError`` when
    more than one face is present — the caller decides what that means for the
    document photo versus a selfie, instead of this function silently comparing
    against whichever face the detector happened to return first.
    """
    _require_backend()
    rgb = _prepare(image)

    locations = face_recognition.face_locations(
        rgb, number_of_times_to_upsample=DETECTION_UPSAMPLE
    )
    if not locations:
        return None
    if len(locations) > MAX_FACES:
        raise MultiFaceError(
            f"Expected at most {MAX_FACES} face, found {len(locations)}. "
            "Crop the image to one face and try again."
        )

    # Reuse the locations just detected rather than letting face_encodings run
    # detection a second time — same result, half the CPU.
    encodings = face_recognition.face_encodings(
        rgb, known_face_locations=locations, num_jitters=NUM_JITTERS
    )
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

    Raises ``MultiFaceError`` when the selfie contains more than one face.
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
