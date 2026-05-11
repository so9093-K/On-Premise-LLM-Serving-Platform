from __future__ import annotations

import base64
import binascii
import struct
from typing import Any
from urllib.parse import urlparse

from ..errors import ServiceError
from .common import reject_unknown_fields


def _image_url_scheme(url: str) -> str:
    if url.startswith("data:image/"):
        return "data"
    return urlparse(url).scheme.lower()


def _png_dimensions(decoded: bytes) -> tuple[int, int] | None:
    if len(decoded) >= 24 and decoded.startswith(b"\x89PNG\r\n\x1a\n") and decoded[12:16] == b"IHDR":
        return struct.unpack(">II", decoded[16:24])
    return None


def _jpeg_dimensions(decoded: bytes) -> tuple[int, int] | None:
    if len(decoded) < 4 or not decoded.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(decoded):
        if decoded[index] != 0xFF:
            index += 1
            continue
        marker = decoded[index + 1]
        index += 2
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(decoded):
            return None
        segment_length = int.from_bytes(decoded[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(decoded):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if segment_length >= 7:
                height = int.from_bytes(decoded[index + 3:index + 5], "big")
                width = int.from_bytes(decoded[index + 5:index + 7], "big")
                return width, height
            return None
        index += segment_length
    return None


def _webp_dimensions(decoded: bytes) -> tuple[int, int] | None:
    if len(decoded) < 30 or not (decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP"):
        return None
    chunk = decoded[12:16]
    if chunk == b"VP8X" and len(decoded) >= 30:
        width = 1 + int.from_bytes(decoded[24:27], "little")
        height = 1 + int.from_bytes(decoded[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(decoded) >= 30:
        # Lossy bitstream frame header starts after the 20-byte RIFF/VP8 chunk header.
        if decoded[23:26] == b"\x9d\x01*":
            width = int.from_bytes(decoded[26:28], "little") & 0x3FFF
            height = int.from_bytes(decoded[28:30], "little") & 0x3FFF
            return width, height
    if chunk == b"VP8L" and len(decoded) >= 25 and decoded[20] == 0x2F:
        bits = int.from_bytes(decoded[21:25], "little")
        width = 1 + (bits & 0x3FFF)
        height = 1 + ((bits >> 14) & 0x3FFF)
        return width, height
    return None


def _image_dimensions(decoded: bytes, media_type: str) -> tuple[int, int] | None:
    if media_type == "image/png":
        return _png_dimensions(decoded)
    if media_type == "image/jpeg":
        return _jpeg_dimensions(decoded)
    if media_type == "image/webp":
        return _webp_dimensions(decoded)
    return None


def _validate_data_image_url(
    url: str,
    *,
    max_image_bytes: int,
    max_image_pixels: int,
    allowed_image_mime_types: set[str],
) -> None:
    header, sep, encoded = url.partition(",")
    if sep != "," or not header.startswith("data:image/"):
        raise ServiceError("VALIDATION_ERROR", "image_url.url must be a valid data:image URL.", False, 422)
    media_type = header[5:].split(";", 1)[0].lower()
    if allowed_image_mime_types and media_type not in allowed_image_mime_types:
        allowed = ", ".join(sorted(allowed_image_mime_types))
        raise ServiceError("VALIDATION_ERROR", f"image_url MIME type must be one of: {allowed}.", False, 422)
    if ";base64" not in header.lower():
        raise ServiceError("VALIDATION_ERROR", "image_url data images must be base64 encoded.", False, 422)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError("VALIDATION_ERROR", "image_url data image must contain valid base64.", False, 422) from exc
    if max_image_bytes and len(decoded) > max_image_bytes:
        raise ServiceError("VALIDATION_ERROR", f"image_url decoded image must be {max_image_bytes} bytes or fewer.", False, 422)
    dimensions = _image_dimensions(decoded, media_type)
    if dimensions is None:
        raise ServiceError("VALIDATION_ERROR", "image_url image dimensions could not be read safely.", False, 422)
    width, height = dimensions
    if width < 1 or height < 1:
        raise ServiceError("VALIDATION_ERROR", "image_url image dimensions must be positive.", False, 422)
    if max_image_pixels and width * height > max_image_pixels:
        raise ServiceError("VALIDATION_ERROR", f"image_url image dimensions must contain {max_image_pixels} pixels or fewer.", False, 422)


def _validate_content_part(
    part: Any,
    *,
    allowed_modalities: set[str],
    allowed_image_url_schemes: set[str],
    max_image_bytes: int,
    max_image_pixels: int,
    allowed_image_mime_types: set[str],
) -> str:
    if not isinstance(part, dict):
        raise ServiceError("VALIDATION_ERROR", "message content parts must be objects.", False, 422)
    part_type = part.get("type")
    if part_type == "text":
        reject_unknown_fields(part, {"type", "text"}, "text content part")
        if "text" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "text content parts are not enabled for this model.", False, 422)
        if not isinstance(part.get("text"), str):
            raise ServiceError("VALIDATION_ERROR", "text content parts require a string text field.", False, 422)
        return "text"
    if part_type == "image_url":
        reject_unknown_fields(part, {"type", "image_url"}, "image_url content part")
        if "image" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "image content parts are not enabled for this model.", False, 422)
        image_url = part.get("image_url")
        if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
            raise ServiceError("VALIDATION_ERROR", "image_url content parts require image_url.url.", False, 422)
        reject_unknown_fields(image_url, {"url", "detail"}, "image_url")
        if "detail" in image_url and image_url["detail"] not in {"auto", "low", "high"}:
            raise ServiceError("VALIDATION_ERROR", "image_url.detail must be auto, low, or high when provided.", False, 422)
        url = image_url["url"]
        scheme = _image_url_scheme(url)
        if scheme not in allowed_image_url_schemes:
            allowed = ", ".join(sorted(allowed_image_url_schemes)) or "none"
            raise ServiceError("VALIDATION_ERROR", f"image_url.url scheme must be one of: {allowed}.", False, 422)
        if scheme == "data":
            _validate_data_image_url(
                url,
                max_image_bytes=max_image_bytes,
                max_image_pixels=max_image_pixels,
                allowed_image_mime_types=allowed_image_mime_types,
            )
        return "image"
    raise ServiceError("VALIDATION_ERROR", "message content part type must be text or image_url.", False, 422)


def validate_message_content(
    content: Any,
    *,
    allowed_modalities: set[str],
    max_image_inputs: int,
    allowed_image_url_schemes: set[str],
    max_image_bytes: int,
    max_image_pixels: int,
    allowed_image_mime_types: set[str],
) -> int:
    if isinstance(content, str):
        if "text" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "string chat content is not enabled for this model.", False, 422)
        return 0
    if not isinstance(content, list) or not content:
        raise ServiceError("VALIDATION_ERROR", "message content must be a string or non-empty content part array.", False, 422)
    image_count = 0
    for part in content:
        modality = _validate_content_part(
            part,
            allowed_modalities=allowed_modalities,
            allowed_image_url_schemes=allowed_image_url_schemes,
            max_image_bytes=max_image_bytes,
            max_image_pixels=max_image_pixels,
            allowed_image_mime_types=allowed_image_mime_types,
        )
        if modality == "image":
            image_count += 1
    if image_count > max_image_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_image_inputs} image content part(s) are allowed.", False, 422)
    return image_count
