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


def _image_dimensions(decoded: bytes) -> tuple[int, int] | None:
    for parser in (_jpeg_dimensions, _png_dimensions, _webp_dimensions):
        result = parser(decoded)
        if result is not None:
            return result
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
    dimensions = _image_dimensions(decoded)
    if dimensions is None:
        raise ServiceError("VALIDATION_ERROR", "image_url image dimensions could not be read safely.", False, 422)
    width, height = dimensions
    if width < 1 or height < 1:
        raise ServiceError("VALIDATION_ERROR", "image_url image dimensions must be positive.", False, 422)
    if max_image_pixels and width * height > max_image_pixels:
        raise ServiceError("VALIDATION_ERROR", f"image_url image dimensions must contain {max_image_pixels} pixels or fewer.", False, 422)


# Formats whose magic bytes _audio_format_matches() can verify. The configured
# `allowed_audio_formats` (model_serving.yaml) MUST stay a subset of this set: a
# format allowed in config but absent here would always fail the magic check and
# be silently rejected. Extend both together.
SNIFFABLE_AUDIO_FORMATS: frozenset[str] = frozenset({"wav", "flac", "ogg", "mp3", "m4a", "mp4", "aac"})
SNIFFABLE_VIDEO_MIME_TYPES: frozenset[str] = frozenset({"video/mp4", "video/webm", "video/quicktime", "video/jpeg"})


def _is_iso_bmff(decoded: bytes) -> bool:
    return len(decoded) >= 12 and decoded[4:8] == b"ftyp"


def _audio_format_matches(fmt: str, decoded: bytes) -> bool:
    """Best-effort magic-byte sniff so a declared format cannot misrepresent bytes."""
    if fmt == "wav":
        return len(decoded) >= 12 and decoded.startswith(b"RIFF") and decoded[8:12] == b"WAVE"
    if fmt == "flac":
        return decoded.startswith(b"fLaC")
    if fmt == "ogg":
        return decoded.startswith(b"OggS")
    if fmt == "mp3":
        return decoded.startswith(b"ID3") or (
            len(decoded) >= 2 and decoded[0] == 0xFF and (decoded[1] & 0xE0) == 0xE0
        )
    if fmt in {"m4a", "mp4"}:
        return _is_iso_bmff(decoded)
    if fmt == "aac":
        return len(decoded) >= 2 and decoded[0] == 0xFF and (decoded[1] & 0xF0) == 0xF0
    return False


def _validate_input_audio(
    part: Any,
    *,
    allowed_audio_formats: set[str],
    max_audio_bytes: int,
) -> None:
    reject_unknown_fields(part, {"type", "input_audio"}, "input_audio content part")
    audio = part.get("input_audio")
    if not isinstance(audio, dict):
        raise ServiceError("VALIDATION_ERROR", "input_audio content parts require an input_audio object.", False, 422)
    reject_unknown_fields(audio, {"data", "format"}, "input_audio")
    fmt = audio.get("format")
    if not isinstance(fmt, str) or (allowed_audio_formats and fmt not in allowed_audio_formats):
        allowed = ", ".join(sorted(allowed_audio_formats)) or "none"
        raise ServiceError("VALIDATION_ERROR", f"input_audio.format must be one of: {allowed}.", False, 422)
    data = audio.get("data")
    if not isinstance(data, str) or not data:
        raise ServiceError("VALIDATION_ERROR", "input_audio.data must be a base64-encoded string.", False, 422)
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError("VALIDATION_ERROR", "input_audio.data must contain valid base64.", False, 422) from exc
    if not decoded:
        raise ServiceError("VALIDATION_ERROR", "input_audio.data must not be empty.", False, 422)
    if max_audio_bytes and len(decoded) > max_audio_bytes:
        raise ServiceError("VALIDATION_ERROR", f"input_audio decoded audio must be {max_audio_bytes} bytes or fewer.", False, 422)
    if not _audio_format_matches(fmt, decoded):
        raise ServiceError("VALIDATION_ERROR", f"input_audio.data does not look like a valid {fmt} stream.", False, 422)


def _video_url_scheme(url: str) -> str:
    if url.startswith("data:video/"):
        return "data"
    return urlparse(url).scheme.lower()


def _video_format_matches(media_type: str, decoded: bytes) -> bool:
    if media_type in {"video/mp4", "video/quicktime"}:
        return _is_iso_bmff(decoded)
    if media_type == "video/webm":
        return decoded.startswith(b"\x1a\x45\xdf\xa3")
    if media_type == "video/jpeg":
        return _jpeg_dimensions(decoded) is not None
    return False


def _decode_video_frame_sequence(encoded: str) -> list[bytes]:
    frames: list[bytes] = []
    for frame_index, frame_b64 in enumerate(encoded.split(",")):
        if not frame_b64:
            raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} must not be empty.", False, 422)
        try:
            frames.append(base64.b64decode(frame_b64, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} must contain valid base64.", False, 422) from exc
    return frames


def _validate_data_video_url(
    url: str,
    *,
    allowed_video_mime_types: set[str],
    max_video_bytes: int,
    max_video_frames: int,
    max_video_frame_pixels: int,
) -> None:
    header, sep, encoded = url.partition(",")
    if sep != "," or not header.startswith("data:video/"):
        raise ServiceError("VALIDATION_ERROR", "video_url.url must be a valid data:video URL.", False, 422)
    media_type = header[5:].split(";", 1)[0].lower()
    if allowed_video_mime_types and media_type not in allowed_video_mime_types:
        allowed = ", ".join(sorted(allowed_video_mime_types))
        raise ServiceError("VALIDATION_ERROR", f"video_url MIME type must be one of: {allowed}.", False, 422)
    if ";base64" not in header.lower():
        raise ServiceError("VALIDATION_ERROR", "video_url data videos must be base64 encoded.", False, 422)

    if media_type == "video/jpeg":
        frames = _decode_video_frame_sequence(encoded)
        if max_video_frames and len(frames) > max_video_frames:
            raise ServiceError("VALIDATION_ERROR", f"video_url must contain {max_video_frames} frame(s) or fewer.", False, 422)
        total_bytes = 0
        for frame_index, decoded in enumerate(frames):
            total_bytes += len(decoded)
            if not _video_format_matches(media_type, decoded):
                raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} does not look like a valid JPEG frame.", False, 422)
            width, height = _jpeg_dimensions(decoded) or (0, 0)
            if width < 1 or height < 1:
                raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} dimensions must be positive.", False, 422)
            if max_video_frame_pixels and width * height > max_video_frame_pixels:
                raise ServiceError(
                    "VALIDATION_ERROR",
                    f"video_url frame {frame_index} dimensions must contain {max_video_frame_pixels} pixels or fewer.",
                    False,
                    422,
                )
        if max_video_bytes and total_bytes > max_video_bytes:
            raise ServiceError("VALIDATION_ERROR", f"video_url decoded video must be {max_video_bytes} bytes or fewer.", False, 422)
        return

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError("VALIDATION_ERROR", "video_url data video must contain valid base64.", False, 422) from exc
    if not decoded:
        raise ServiceError("VALIDATION_ERROR", "video_url data video must not be empty.", False, 422)
    if max_video_bytes and len(decoded) > max_video_bytes:
        raise ServiceError("VALIDATION_ERROR", f"video_url decoded video must be {max_video_bytes} bytes or fewer.", False, 422)
    if not _video_format_matches(media_type, decoded):
        raise ServiceError("VALIDATION_ERROR", f"video_url.data does not look like a valid {media_type} stream.", False, 422)


def _validate_video_url(
    part: Any,
    *,
    allowed_video_url_schemes: set[str],
    allowed_video_mime_types: set[str],
    max_video_bytes: int,
    max_video_frames: int,
    max_video_frame_pixels: int,
) -> None:
    reject_unknown_fields(part, {"type", "video_url"}, "video_url content part")
    video_url = part.get("video_url")
    if not isinstance(video_url, dict) or not isinstance(video_url.get("url"), str):
        raise ServiceError("VALIDATION_ERROR", "video_url content parts require video_url.url.", False, 422)
    reject_unknown_fields(video_url, {"url"}, "video_url")
    url = video_url["url"]
    scheme = _video_url_scheme(url)
    if scheme not in allowed_video_url_schemes:
        allowed = ", ".join(sorted(allowed_video_url_schemes)) or "none"
        raise ServiceError("VALIDATION_ERROR", f"video_url.url scheme must be one of: {allowed}.", False, 422)
    if scheme == "data":
        _validate_data_video_url(
            url,
            allowed_video_mime_types=allowed_video_mime_types,
            max_video_bytes=max_video_bytes,
            max_video_frames=max_video_frames,
            max_video_frame_pixels=max_video_frame_pixels,
        )


def _validate_content_part(
    part: Any,
    *,
    allowed_modalities: set[str],
    allowed_image_url_schemes: set[str],
    max_image_bytes: int,
    max_image_pixels: int,
    allowed_image_mime_types: set[str],
    allowed_audio_formats: set[str],
    max_audio_bytes: int,
    allowed_video_url_schemes: set[str],
    allowed_video_mime_types: set[str],
    max_video_bytes: int,
    max_video_frames: int,
    max_video_frame_pixels: int,
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
    if part_type == "input_audio":
        if "audio" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "audio content parts are not enabled for this model.", False, 422)
        _validate_input_audio(
            part,
            allowed_audio_formats=allowed_audio_formats,
            max_audio_bytes=max_audio_bytes,
        )
        return "audio"
    if part_type == "video_url":
        if "video" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "video content parts are not enabled for this model.", False, 422)
        _validate_video_url(
            part,
            allowed_video_url_schemes=allowed_video_url_schemes,
            allowed_video_mime_types=allowed_video_mime_types,
            max_video_bytes=max_video_bytes,
            max_video_frames=max_video_frames,
            max_video_frame_pixels=max_video_frame_pixels,
        )
        return "video"
    allowed_types = "text or image_url"
    if "audio" in allowed_modalities:
        allowed_types += " or input_audio"
    if "video" in allowed_modalities:
        allowed_types += " or video_url"
    raise ServiceError("VALIDATION_ERROR", f"message content part type must be {allowed_types}.", False, 422)


def validate_message_content(
    content: Any,
    *,
    allowed_modalities: set[str],
    max_image_inputs: int,
    allowed_image_url_schemes: set[str],
    max_image_bytes: int,
    max_image_pixels: int,
    allowed_image_mime_types: set[str],
    max_audio_inputs: int = 0,
    allowed_audio_formats: set[str] | None = None,
    max_audio_bytes: int = 0,
    max_video_inputs: int = 0,
    allowed_video_url_schemes: set[str] | None = None,
    allowed_video_mime_types: set[str] | None = None,
    max_video_bytes: int = 0,
    max_video_frames: int = 0,
    max_video_frame_pixels: int = 0,
) -> tuple[int, int, int]:
    if isinstance(content, str):
        if "text" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "string chat content is not enabled for this model.", False, 422)
        return 0, 0, 0
    if not isinstance(content, list) or not content:
        raise ServiceError("VALIDATION_ERROR", "message content must be a string or non-empty content part array.", False, 422)
    image_count = 0
    audio_count = 0
    video_count = 0
    for part in content:
        modality = _validate_content_part(
            part,
            allowed_modalities=allowed_modalities,
            allowed_image_url_schemes=allowed_image_url_schemes,
            max_image_bytes=max_image_bytes,
            max_image_pixels=max_image_pixels,
            allowed_image_mime_types=allowed_image_mime_types,
            allowed_audio_formats=allowed_audio_formats or set(),
            max_audio_bytes=max_audio_bytes,
            allowed_video_url_schemes=allowed_video_url_schemes or set(),
            allowed_video_mime_types=allowed_video_mime_types or set(),
            max_video_bytes=max_video_bytes,
            max_video_frames=max_video_frames,
            max_video_frame_pixels=max_video_frame_pixels,
        )
        if modality == "image":
            image_count += 1
        elif modality == "audio":
            audio_count += 1
        elif modality == "video":
            video_count += 1
    if image_count > max_image_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_image_inputs} image content part(s) are allowed.", False, 422)
    if audio_count > max_audio_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_audio_inputs} audio content part(s) are allowed.", False, 422)
    if video_count > max_video_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_video_inputs} video content part(s) are allowed.", False, 422)
    return image_count, audio_count, video_count
