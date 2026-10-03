"""Optional decode validation adapter; no processing, persistence, OCR or HTTP."""

import io
from typing import BinaryIO

from core.processing.errors import ProcessingInputError
from core.processing.image import ImageHeader, read_image_header

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_SECONDS = 60
IMAGE_MEMORY = 1024 * 1024 * 1024


class ImageError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def inspect_image(stream: BinaryIO, declared: str) -> tuple[bytes, str, ImageHeader]:
    data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageError("input_size_limit")
    mime = (
        "image/png"
        if data.startswith(b"\x89PNG\r\n\x1a\n")
        else ("image/jpeg" if data.startswith(b"\xff\xd8") else "")
    )
    if not mime:
        raise ImageError("unsupported_format")
    if declared not in {"", "application/octet-stream", "image/png", "image/jpeg"}:
        raise ImageError("unsupported_format")
    if declared not in {"", "application/octet-stream", mime}:
        raise ImageError("mime_mismatch")
    try:
        header = read_image_header(io.BytesIO(data), mime)
    except ProcessingInputError:
        raise ImageError("invalid_image") from None
    if header.width * header.height > MAX_IMAGE_PIXELS:
        raise ImageError("pixel_limit")
    return data, mime, header


def validate_pixels(data: bytes, header: ImageHeader) -> None:
    # Lazy, outside core and only in the bounded child. No OCR package required.
    try:
        from PIL import Image
    except ImportError:
        raise ImageError("image_decoder_unavailable") from None
    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as image:
            if image.size != (header.width, header.height) or getattr(image, "n_frames", 1) != 1:
                raise ImageError("unsupported_format")
            image.verify()
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as image:
            image.load()
    except (OSError, ValueError, SyntaxError):
        raise ImageError("invalid_image") from None
