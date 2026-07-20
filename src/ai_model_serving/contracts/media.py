from __future__ import annotations

import base64
import binascii
import struct
from typing import Any
from urllib.parse import urlparse

from ..errors import ServiceError
from .common import field_param, reject_unknown_fields


def _decode_media_base64(data: str) -> bytes:
    """Decode media base64 with the same tolerance as the downstream runtime.

    The validating gateway must not be stricter than vLLM: standard base64
    decoders ignore insignificant whitespace, so newline-wrapped payloads (the
    default of the ``base64`` CLI and MIME encoders) are valid input. Strip ASCII
    whitespace before decoding, but keep the alphabet and padding strict
    (``validate=True``) so genuinely malformed data still fails fast and early.
    """
    return base64.b64decode("".join(data.split()), validate=True)


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
        # 손실(lossy) 비트스트림 프레임 헤더는 20바이트 RIFF/VP8 청크 헤더 뒤에서 시작한다.
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


def _avif_dimensions(decoded: bytes) -> tuple[int, int] | None:
    if not _is_iso_bmff(decoded):
        return None
    ftyp_size = int.from_bytes(decoded[:4], "big")
    if ftyp_size < 16 or ftyp_size > len(decoded) or decoded[8:12] not in {b"avif", b"avis"}:
        return None
    brands = decoded[8:ftyp_size]
    if b"avif" not in brands and b"avis" not in brands:
        return None

    search_from = 0
    while True:
        marker = decoded.find(b"ispe", search_from)
        if marker < 4:
            return None
        box_start = marker - 4
        box_size = int.from_bytes(decoded[box_start:marker], "big")
        if box_size >= 20 and box_start + box_size <= len(decoded):
            width = int.from_bytes(decoded[marker + 8:marker + 12], "big")
            height = int.from_bytes(decoded[marker + 12:marker + 16], "big")
            return width, height
        search_from = marker + 4


def _jp2_dimensions(decoded: bytes) -> tuple[int, int] | None:
    if decoded.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n"):
        marker = decoded.find(b"ihdr")
        if marker >= 4:
            box_start = marker - 4
            box_size = int.from_bytes(decoded[box_start:marker], "big")
            if box_size >= 22 and box_start + box_size <= len(decoded):
                height = int.from_bytes(decoded[marker + 4:marker + 8], "big")
                width = int.from_bytes(decoded[marker + 8:marker + 12], "big")
                return width, height
    if decoded.startswith(b"\xff\x4f\xff\x51") and len(decoded) >= 42:
        xsiz = int.from_bytes(decoded[8:12], "big")
        ysiz = int.from_bytes(decoded[12:16], "big")
        xosiz = int.from_bytes(decoded[16:20], "big")
        yosiz = int.from_bytes(decoded[20:24], "big")
        return xsiz - xosiz, ysiz - yosiz
    return None


def _gif_metadata(decoded: bytes) -> tuple[int, int, int, float] | None:
    """Return (width, height, frame_count, duration_seconds), or None if unparseable.

    duration_seconds sums each frame's Graphic Control Extension delay (label
    0xF9, 1/100s units) -- the animation's actual playback time, independent of
    how many raw frames the encoder used to represent it. A short clip encoded
    at a high frame rate has many frames but a small duration; frame count alone
    is not a proxy for playback time.
    """
    if len(decoded) < 14 or decoded[:6] not in {b"GIF87a", b"GIF89a"}:
        return None
    width = int.from_bytes(decoded[6:8], "little")
    height = int.from_bytes(decoded[8:10], "little")
    packed = decoded[10]
    index = 13
    if packed & 0x80:
        index += 3 * (2 ** ((packed & 0x07) + 1))
    if index > len(decoded):
        return None
    frames = 0
    duration_centiseconds = 0
    while index < len(decoded):
        marker = decoded[index]
        index += 1
        if marker == 0x3B:
            break
        if marker == 0x21:
            if index >= len(decoded):
                return None
            label = decoded[index]
            index += 1
            while index < len(decoded):
                block_size = decoded[index]
                index += 1
                if block_size == 0:
                    break
                # Graphic Control Extension 데이터는 [packed, delay_lo, delay_hi,
                # transparent_index] 형태이다; delay(1/100초 단위)는 적용 대상인 다음 이미지보다 앞에 온다.
                if label == 0xF9 and block_size >= 4 and index + 4 <= len(decoded):
                    duration_centiseconds += int.from_bytes(decoded[index + 1:index + 3], "little")
                index += block_size
            if index > len(decoded):
                return None
            continue
        if marker == 0x2C:
            if index + 9 > len(decoded):
                return None
            frame_width = int.from_bytes(decoded[index + 4:index + 6], "little")
            frame_height = int.from_bytes(decoded[index + 6:index + 8], "little")
            if frame_width < 1 or frame_height < 1:
                return None
            frames += 1
            local_packed = decoded[index + 8]
            index += 9
            if local_packed & 0x80:
                index += 3 * (2 ** ((local_packed & 0x07) + 1))
            if index >= len(decoded):
                return None
            index += 1
            while index < len(decoded):
                block_size = decoded[index]
                index += 1
                if block_size == 0:
                    break
                index += block_size
            if index > len(decoded):
                return None
            continue
        return None
    return (width, height, frames, duration_centiseconds / 100.0)


def _gif_dimensions(decoded: bytes) -> tuple[int, int] | None:
    metadata = _gif_metadata(decoded)
    if metadata is None:
        return None
    width, height, _frames, _duration_seconds = metadata
    return width, height


def _bmp_dimensions(decoded: bytes) -> tuple[int, int] | None:
    if len(decoded) < 26 or not decoded.startswith(b"BM"):
        return None
    dib_header_size = int.from_bytes(decoded[14:18], "little")
    if dib_header_size == 12:
        if len(decoded) < 26:
            return None
        width = int.from_bytes(decoded[18:20], "little")
        height = int.from_bytes(decoded[20:22], "little")
        return width, height
    if dib_header_size >= 40:
        if len(decoded) < 26:
            return None
        width = int.from_bytes(decoded[18:22], "little", signed=True)
        height = int.from_bytes(decoded[22:26], "little", signed=True)
        return width, abs(height)
    return None


def _tiff_dimensions(decoded: bytes) -> tuple[int, int] | None:
    if len(decoded) < 8:
        return None
    if decoded[:4] == b"II*\x00":
        byteorder = "little"
    elif decoded[:4] == b"MM\x00*":
        byteorder = "big"
    else:
        return None

    ifd_offset = int.from_bytes(decoded[4:8], byteorder)
    if ifd_offset < 8 or ifd_offset + 2 > len(decoded):
        return None
    entry_count = int.from_bytes(decoded[ifd_offset:ifd_offset + 2], byteorder)
    entries_start = ifd_offset + 2
    entries_end = entries_start + entry_count * 12
    next_ifd_offset_position = entries_end
    if entries_end > len(decoded) or next_ifd_offset_position + 4 > len(decoded):
        return None

    width: int | None = None
    height: int | None = None
    for index in range(entry_count):
        offset = entries_start + index * 12
        tag = int.from_bytes(decoded[offset:offset + 2], byteorder)
        field_type = int.from_bytes(decoded[offset + 2:offset + 4], byteorder)
        count = int.from_bytes(decoded[offset + 4:offset + 8], byteorder)
        value_field = decoded[offset + 8:offset + 12]
        if tag == 330:
            return None
        if tag not in {256, 257} or count != 1:
            continue
        if field_type == 3:
            value = int.from_bytes(value_field[:2], byteorder)
        elif field_type == 4:
            value = int.from_bytes(value_field, byteorder)
        else:
            return None
        if tag == 256:
            width = value
        else:
            height = value

    next_ifd_offset = int.from_bytes(decoded[next_ifd_offset_position:next_ifd_offset_position + 4], byteorder)
    if next_ifd_offset != 0:
        return None
    if width is None or height is None:
        return None
    return width, height


def _image_dimensions(decoded: bytes) -> tuple[int, int] | None:
    for parser in (
        _jpeg_dimensions,
        _png_dimensions,
        _webp_dimensions,
        _avif_dimensions,
        _jp2_dimensions,
        _gif_dimensions,
        _bmp_dimensions,
        _tiff_dimensions,
    ):
        result = parser(decoded)
        if result is not None:
            return result
    return None


@field_param("image_url")
def _validate_data_image_url(
    url: str,
    *,
    max_image_bytes: int,
    max_image_pixels: int,
    allowed_image_mime_types: set[str],
) -> None:
    header, sep, encoded = url.partition(",")
    if sep != "," or not header.startswith("data:image/"):
        raise ServiceError("VALIDATION_ERROR", "image_url.url must be a data:image/...;base64,... URL.", False, 422)
    media_type = header[5:].split(";", 1)[0].lower()
    if allowed_image_mime_types and media_type not in allowed_image_mime_types:
        allowed = ", ".join(sorted(allowed_image_mime_types))
        raise ServiceError("VALIDATION_ERROR", f"image_url MIME type is {media_type}; use one of: {allowed}.", False, 422)
    if ";base64" not in header.lower():
        raise ServiceError("VALIDATION_ERROR", "image_url data images must include ';base64' and a base64 payload.", False, 422)
    try:
        decoded = _decode_media_base64(encoded)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError("VALIDATION_ERROR", "image_url data image must contain valid base64 after the comma.", False, 422) from exc
    if max_image_bytes and len(decoded) > max_image_bytes:
        raise ServiceError("VALIDATION_ERROR", f"image_url decoded image is {len(decoded)} bytes; reduce it to {max_image_bytes} bytes or fewer.", False, 422)
    if media_type == "image/gif":
        metadata = _gif_metadata(decoded)
        if metadata is None:
            raise ServiceError("VALIDATION_ERROR", "image_url image dimensions could not be read safely; send a supported single static image.", False, 422)
        _width, _height, frame_count, _duration_seconds = metadata
        if frame_count > 1:
            raise ServiceError(
                "VALIDATION_ERROR",
                "animated image/gif is not supported as image input; use video_url with data:video/gif for motion analysis.",
                False,
                422,
            )
    dimensions = _image_dimensions(decoded)
    if dimensions is None:
        raise ServiceError("VALIDATION_ERROR", "image_url image dimensions could not be read safely; send a supported single static image.", False, 422)
    width, height = dimensions
    if width < 1 or height < 1:
        raise ServiceError("VALIDATION_ERROR", "image_url image dimensions must be positive.", False, 422)
    if max_image_pixels and width * height > max_image_pixels:
        raise ServiceError("VALIDATION_ERROR", f"image_url image has {width * height} pixels; resize it to {max_image_pixels} pixels or fewer.", False, 422)


# _audio_format_matches()가 magic byte로 검증할 수 있는 포맷들. 설정된
# `allowed_audio_formats`(model_serving.yaml)는 반드시 이 집합의 부분집합으로
# 유지되어야 한다: config에서는 허용되지만 여기에 없는 포맷은 magic check에서
# 항상 실패하여 조용히 거부된다. 두 곳을 함께 확장해야 한다.
SNIFFABLE_AUDIO_FORMATS: frozenset[str] = frozenset({"wav", "flac", "ogg", "mp3", "m4a", "mp4", "aac"})
SNIFFABLE_VIDEO_MIME_TYPES: frozenset[str] = frozenset({
    "video/mp4",
    "video/webm",
    "video/x-matroska",
    "video/quicktime",
    "video/jpeg",
    "video/x-msvideo",
    "video/avi",
    "video/gif",
})


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


@field_param("input_audio")
def _validate_input_audio(
    part: Any,
    *,
    allowed_audio_formats: set[str],
    max_audio_bytes: int,
) -> None:
    reject_unknown_fields(part, {"type", "input_audio"}, "input_audio content part")
    audio = part.get("input_audio")
    if not isinstance(audio, dict):
        raise ServiceError("VALIDATION_ERROR", "input_audio content parts require an input_audio object with data and format.", False, 422)
    reject_unknown_fields(audio, {"data", "format"}, "input_audio")
    fmt = audio.get("format")
    if not isinstance(fmt, str) or (allowed_audio_formats and fmt not in allowed_audio_formats):
        allowed = ", ".join(sorted(allowed_audio_formats)) or "none"
        actual = fmt if isinstance(fmt, str) else type(fmt).__name__
        raise ServiceError("VALIDATION_ERROR", f"input_audio.format is {actual}; use one of: {allowed}.", False, 422)
    data = audio.get("data")
    if not isinstance(data, str) or not data:
        raise ServiceError("VALIDATION_ERROR", "input_audio.data must be a non-empty raw base64 string.", False, 422)
    if data.lstrip().startswith("data:"):
        # input_audio.data는 data: URL을 포함하는 image_url/video_url과 달리 raw
        # base64이다. 여기에 data: 접두사가 있으면 base64가 잘못된 것이 아니라
        # 필드 형식 자체가 잘못된 것이다.
        raise ServiceError("VALIDATION_ERROR", "input_audio.data must be raw base64 (no data: URL prefix).", False, 422)
    try:
        decoded = _decode_media_base64(data)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError("VALIDATION_ERROR", "input_audio.data must contain valid base64 raw data.", False, 422) from exc
    if not decoded:
        raise ServiceError("VALIDATION_ERROR", "input_audio.data must not be empty.", False, 422)
    if max_audio_bytes and len(decoded) > max_audio_bytes:
        raise ServiceError("VALIDATION_ERROR", f"input_audio decoded audio is {len(decoded)} bytes; reduce it to {max_audio_bytes} bytes or fewer.", False, 422)
    if not _audio_format_matches(fmt, decoded):
        raise ServiceError("VALIDATION_ERROR", f"input_audio.data does not look like a valid {fmt} stream; send {fmt} bytes or set input_audio.format to the actual file format.", False, 422)


def _video_url_scheme(url: str) -> str:
    if url.startswith("data:video/"):
        return "data"
    return urlparse(url).scheme.lower()


def _video_format_matches(media_type: str, decoded: bytes) -> bool:
    if media_type in {"video/mp4", "video/quicktime"}:
        return _is_iso_bmff(decoded)
    if media_type in {"video/webm", "video/x-matroska"}:
        return decoded.startswith(b"\x1a\x45\xdf\xa3")
    if media_type == "video/jpeg":
        return _jpeg_dimensions(decoded) is not None
    if media_type in {"video/x-msvideo", "video/avi"}:
        return len(decoded) >= 12 and decoded.startswith(b"RIFF") and decoded[8:12] == b"AVI "
    if media_type == "video/gif":
        return _gif_metadata(decoded) is not None
    return False


def _decode_video_frame_sequence(encoded: str) -> list[bytes]:
    frames: list[bytes] = []
    for frame_index, frame_b64 in enumerate(encoded.split(",")):
        if not frame_b64:
            raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} must not be empty; remove the empty frame or send valid frame base64.", False, 422)
        try:
            frames.append(_decode_media_base64(frame_b64))
        except (binascii.Error, ValueError) as exc:
            raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} must contain valid base64.", False, 422) from exc
    return frames


# GIF 프레임 수만으로는 재생 시간을 알 수 없으므로(인코더는 frame rate를 자유롭게
# 다르게 설정할 수 있다), max_video_duration_seconds처럼 "너무 길지 않은지"를
# 판단하는 게이트로 쓸 수 없다. 다만 duration-only 체크만으로는 걸러지지 않는
# 퇴화된(degenerate) 인코딩(거의 0에 가까운 delay, 많은 프레임 수)에 대한 대략적인
# decode-cost 안전장치 역할은 한다. 이 상한값은 정상적인 애니메이션 frame rate로
# max_video_duration_seconds 길이만큼 인코딩된 정상적인 클립은 절대 걸리지 않을
# 만큼 넉넉하다 -- 실제 게이트는 duration 체크다.
_GIF_FRAME_COUNT_FPS_CEILING = 30


def _validate_data_video_url(
    url: str,
    *,
    allowed_video_mime_types: set[str],
    max_video_bytes: int,
    max_video_frames: int,
    max_video_frame_pixels: int,
    max_video_duration_seconds: float,
) -> None:
    header, sep, encoded = url.partition(",")
    if sep != "," or not header.startswith("data:video/"):
        raise ServiceError("VALIDATION_ERROR", "video_url.url must be a valid data:video URL in data:video/...;base64,... form.", False, 422)
    media_type = header[5:].split(";", 1)[0].lower()
    if allowed_video_mime_types and media_type not in allowed_video_mime_types:
        allowed = ", ".join(sorted(allowed_video_mime_types))
        raise ServiceError("VALIDATION_ERROR", f"video_url MIME type is {media_type}; use one of: {allowed}.", False, 422)
    if ";base64" not in header.lower():
        raise ServiceError("VALIDATION_ERROR", "video_url data videos must include ';base64' and a base64 payload.", False, 422)

    if media_type == "video/jpeg":
        frames = _decode_video_frame_sequence(encoded)
        if max_video_frames and len(frames) > max_video_frames:
            raise ServiceError("VALIDATION_ERROR", f"video_url contains {len(frames)} frame(s); reduce it to {max_video_frames} frame(s) or fewer.", False, 422)
        total_bytes = 0
        for frame_index, decoded in enumerate(frames):
            total_bytes += len(decoded)
            if not _video_format_matches(media_type, decoded):
                raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} does not look like a valid JPEG frame; send JPEG frame bytes.", False, 422)
            width, height = _jpeg_dimensions(decoded) or (0, 0)
            if width < 1 or height < 1:
                raise ServiceError("VALIDATION_ERROR", f"video_url frame {frame_index} dimensions must be positive.", False, 422)
            if max_video_frame_pixels and width * height > max_video_frame_pixels:
                raise ServiceError(
                    "VALIDATION_ERROR",
                    f"video_url frame {frame_index} has {width * height} pixels; resize it to {max_video_frame_pixels} pixels or fewer.",
                    False,
                    422,
                )
        if max_video_bytes and total_bytes > max_video_bytes:
            raise ServiceError("VALIDATION_ERROR", f"video_url decoded video is {total_bytes} bytes; reduce it to {max_video_bytes} bytes or fewer.", False, 422)
        return

    try:
        decoded = _decode_media_base64(encoded)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError("VALIDATION_ERROR", "video_url data video must contain valid base64 after the comma.", False, 422) from exc
    if not decoded:
        raise ServiceError("VALIDATION_ERROR", "video_url data video must not be empty.", False, 422)
    if max_video_bytes and len(decoded) > max_video_bytes:
        raise ServiceError("VALIDATION_ERROR", f"video_url decoded video is {len(decoded)} bytes; reduce it to {max_video_bytes} bytes or fewer.", False, 422)
    if media_type == "video/gif":
        metadata = _gif_metadata(decoded)
        if metadata is None:
            raise ServiceError("VALIDATION_ERROR", "video_url.data does not look like a valid video/gif stream; send GIF bytes with MIME video/gif.", False, 422)
        width, height, frame_count, duration_seconds = metadata
        if frame_count < 1:
            raise ServiceError("VALIDATION_ERROR", "video_url GIF must contain at least one frame.", False, 422)
        if max_video_duration_seconds and duration_seconds > max_video_duration_seconds:
            raise ServiceError(
                "VALIDATION_ERROR",
                f"video_url GIF plays for {duration_seconds:.1f}s; reduce it to {max_video_duration_seconds}s or fewer.",
                False,
                422,
            )
        frame_count_backstop = (
            int(max_video_duration_seconds * _GIF_FRAME_COUNT_FPS_CEILING) if max_video_duration_seconds else max_video_frames
        )
        if frame_count_backstop and frame_count > frame_count_backstop:
            raise ServiceError("VALIDATION_ERROR", f"video_url contains {frame_count} frame(s); reduce it to {frame_count_backstop} frame(s) or fewer.", False, 422)
        if width < 1 or height < 1:
            raise ServiceError("VALIDATION_ERROR", "video_url GIF dimensions must be positive.", False, 422)
        if max_video_frame_pixels and width * height > max_video_frame_pixels:
            raise ServiceError(
                "VALIDATION_ERROR",
                f"video_url GIF has {width * height} pixels; resize it to {max_video_frame_pixels} pixels or fewer.",
                False,
                422,
            )
        return
    if not _video_format_matches(media_type, decoded):
        raise ServiceError("VALIDATION_ERROR", f"video_url.data does not look like a valid {media_type} stream; send bytes that match the declared MIME type.", False, 422)


@field_param("video_url")
def _validate_video_url(
    part: Any,
    *,
    allowed_video_url_schemes: set[str],
    allowed_video_mime_types: set[str],
    max_video_bytes: int,
    max_video_frames: int,
    max_video_frame_pixels: int,
    max_video_duration_seconds: float = 0,
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
            max_video_duration_seconds=max_video_duration_seconds,
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
    max_video_duration_seconds: float = 0,
) -> str:
    if not isinstance(part, dict):
        raise ServiceError("VALIDATION_ERROR", "message content parts must be objects.", False, 422, param="messages.content")
    part_type = part.get("type")
    if part_type == "text":
        reject_unknown_fields(part, {"type", "text"}, "text content part")
        if "text" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "The active main model profile does not accept text content parts; remove text parts or switch to a profile that supports text.", False, 422, param="messages.content")
        if not isinstance(part.get("text"), str):
            raise ServiceError("VALIDATION_ERROR", "text content parts require a string text field.", False, 422, param="messages.content.text")
        return "text"
    if part_type == "image_url":
        reject_unknown_fields(part, {"type", "image_url"}, "image_url content part")
        if "image" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "The active main model profile does not accept image_url content parts; remove image_url or switch to a profile that supports image input.", False, 422, param="image_url")
        image_url = part.get("image_url")
        if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
            raise ServiceError("VALIDATION_ERROR", "image_url content parts require image_url.url.", False, 422, param="image_url")
        reject_unknown_fields(image_url, {"url", "detail"}, "image_url")
        if "detail" in image_url and image_url["detail"] not in {"auto", "low", "high"}:
            raise ServiceError("VALIDATION_ERROR", "image_url.detail must be auto, low, or high when provided.", False, 422, param="image_url.detail")
        url = image_url["url"]
        scheme = _image_url_scheme(url)
        if scheme not in allowed_image_url_schemes:
            allowed = ", ".join(sorted(allowed_image_url_schemes)) or "none"
            raise ServiceError("VALIDATION_ERROR", f"image_url.url scheme must be one of: {allowed}.", False, 422, param="image_url.url")
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
            raise ServiceError("VALIDATION_ERROR", "audio content parts are not enabled for the active main model profile; remove input_audio or switch to an audio-capable profile.", False, 422, param="input_audio")
        _validate_input_audio(
            part,
            allowed_audio_formats=allowed_audio_formats,
            max_audio_bytes=max_audio_bytes,
        )
        return "audio"
    if part_type == "video_url":
        if "video" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "video content parts are not enabled for the active main model profile; remove video_url or switch to a video-capable profile.", False, 422, param="video_url")
        _validate_video_url(
            part,
            allowed_video_url_schemes=allowed_video_url_schemes,
            allowed_video_mime_types=allowed_video_mime_types,
            max_video_bytes=max_video_bytes,
            max_video_frames=max_video_frames,
            max_video_frame_pixels=max_video_frame_pixels,
            max_video_duration_seconds=max_video_duration_seconds,
        )
        return "video"
    allowed_types = "text or image_url"
    if "audio" in allowed_modalities:
        allowed_types += " or input_audio"
    if "video" in allowed_modalities:
        allowed_types += " or video_url"
    raise ServiceError("VALIDATION_ERROR", f"message content part type must be {allowed_types}.", False, 422, param="messages.content.type")


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
    max_video_duration_seconds: float = 0,
) -> tuple[int, int, int]:
    if isinstance(content, str):
        if "text" not in allowed_modalities:
            raise ServiceError("VALIDATION_ERROR", "string chat content is not enabled for the active main model profile.", False, 422, param="messages.content")
        return 0, 0, 0
    if not isinstance(content, list) or not content:
        raise ServiceError("VALIDATION_ERROR", "message content must be a string or non-empty content part array.", False, 422, param="messages.content")
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
            max_video_duration_seconds=max_video_duration_seconds,
        )
        if modality == "image":
            image_count += 1
        elif modality == "audio":
            audio_count += 1
        elif modality == "video":
            video_count += 1
    if image_count > max_image_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_image_inputs} image content part(s) are allowed.", False, 422, param="image_url")
    if audio_count > max_audio_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_audio_inputs} audio content part(s) are allowed.", False, 422, param="input_audio")
    if video_count > max_video_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_video_inputs} video content part(s) are allowed.", False, 422, param="video_url")
    return image_count, audio_count, video_count
