"""Bounded validation for images uploaded as Katherine bot avatars."""
from __future__ import annotations

from io import BytesIO
import warnings
from typing import Any

AVATAR_IMAGE_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
}
MAX_AVATAR_PIXELS = 20_000_000
MAX_AVATAR_FRAMES = 100


def validate_avatar_image_bytes(
    content: bytes,
    mime_type: str,
    *,
    max_bytes: int = 512 * 1024,
) -> dict[str, Any]:
    """Verify real image format, size and decode limits before an avatar upload."""
    expected_format = AVATAR_IMAGE_FORMATS.get(str(mime_type or "").split(";", 1)[0].lower())
    if expected_format is None:
        raise ValueError("La imagen debe ser PNG, JPG, WebP o GIF.")
    if not isinstance(content, bytes) or not content:
        raise ValueError("El archivo de imagen está vacío o no es válido.")
    if len(content) > max(1, int(max_bytes)):
        raise ValueError("La imagen supera el límite permitido.")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow no está disponible para validar la imagen.") from exc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                actual_format = str(image.format or "").upper()
                width, height = image.size
                frame_count = int(getattr(image, "n_frames", 1) or 1)
                if actual_format != expected_format:
                    raise ValueError("El formato real de la imagen no coincide con su tipo MIME.")
                if width <= 0 or height <= 0 or width * height > MAX_AVATAR_PIXELS:
                    raise ValueError("La imagen excede el máximo de resolución permitido.")
                if frame_count > MAX_AVATAR_FRAMES:
                    raise ValueError("La imagen animada supera el límite de fotogramas permitido.")
                image.verify()

            with Image.open(BytesIO(content)) as image:
                for frame_index in range(frame_count):
                    image.seek(frame_index)
                    image.load()
    except ValueError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        Image.UnidentifiedImageError,
        OSError,
        SyntaxError,
        EOFError,
    ) as exc:
        raise ValueError("El archivo no es una imagen válida o está dañado.") from exc

    return {
        "format": actual_format,
        "width": width,
        "height": height,
        "frames": frame_count,
        "bytes": len(content),
    }
