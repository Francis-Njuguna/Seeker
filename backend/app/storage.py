"""Image storage: validate uploads, save originals, derive blurred previews."""
from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image, ImageFilter, UnidentifiedImageError

from app.config import settings

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}
PREVIEW_MAX_SIDE = 640
BLUR_RADIUS = 14


class InvalidImageError(ValueError):
    pass


def _upload_root() -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_image(raw: bytes) -> Image.Image:
    """Decode and validate an uploaded image.

    Rejects anything Pillow cannot identify, which also filters out files that
    merely carry an image extension.
    """
    if not raw:
        raise InvalidImageError("empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidImageError("image exceeds 8 MB limit")
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("file is not a readable image") from exc
    if image.format and image.format.upper() not in ALLOWED_FORMATS:
        raise InvalidImageError(f"unsupported image format: {image.format}")
    return image


def save_image_pair(image: Image.Image) -> tuple[str, str]:
    """Persist the original and a heavily blurred preview.

    Returns ``(original_relative_path, blurred_relative_path)``. The blurred
    copy is the only image ever served to a seeker who has not completed a
    claim, so it is generated once at upload time rather than on demand.
    """
    root = _upload_root()
    key = uuid.uuid4().hex

    rgb = ImageOps.exif_transpose(image).convert("RGB")

    original_name = f"{key}.jpg"
    rgb.save(root / original_name, format="JPEG", quality=88)

    preview = rgb.copy()
    preview.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
    preview = preview.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
    blurred_name = f"{key}_blurred.jpg"
    preview.save(root / blurred_name, format="JPEG", quality=70)

    return original_name, blurred_name


def resolve_path(relative_name: str) -> Path:
    """Resolve a stored filename, refusing anything outside the upload dir."""
    root = _upload_root().resolve()
    candidate = (root / relative_name).resolve()
    if not candidate.is_relative_to(root):
        raise InvalidImageError("invalid image path")
    return candidate
