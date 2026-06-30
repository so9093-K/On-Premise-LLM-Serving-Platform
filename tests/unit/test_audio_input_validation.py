"""Audio input validation + dynamic per-profile modality guards.

Audio is accepted ONLY when the active main-model profile deploys it. The
single most important guarantee here is the negative one: with the current
text+image model active (no `audio` modality), an `input_audio` part is rejected
exactly as before -- the audio feature is inert until a profile deploys it.
"""

from __future__ import annotations

import base64
import math
import io
import wave

import pytest

from ai_model_serving.api.routers.gateway_inference import _active_input_modalities
from ai_model_serving.contracts.chat_request import validate_chat_request
from ai_model_serving.errors import ServiceError
from ai_model_serving.media_samples import TINY_JPEG_1X1_B64

AUDIO_LIMITS = dict(
    max_audio_inputs=1,
    allowed_audio_formats=("wav", "mp3", "flac", "ogg", "m4a", "mp4", "aac"),
    max_audio_bytes=25_000_000,
)
IMAGE_LIMITS = dict(
    max_image_inputs=1,
    allowed_image_url_schemes=("data",),
    max_image_bytes=7_000_000,
    max_image_pixels=6_422_528,
    allowed_image_mime_types=(
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/avif",
        "image/jp2",
        "image/gif",
        "image/bmp",
        "image/tiff",
        "image/x-tiff",
    ),
)
VIDEO_LIMITS = dict(
    max_video_inputs=1,
    allowed_video_url_schemes=("data",),
    allowed_video_mime_types=(
        "video/mp4",
        "video/webm",
        "video/x-matroska",
        "video/quicktime",
        "video/jpeg",
        "video/x-msvideo",
        "video/avi",
        "video/gif",
    ),
    max_video_bytes=50_000_000,
    max_video_frames=32,
    max_video_frame_pixels=6_422_528,
)
TEXT_IMAGE = ("text", "image")
TEXT_IMAGE_AUDIO = ("text", "image", "audio")
TEXT_IMAGE_AUDIO_VIDEO = ("text", "image", "audio", "video")
TINY_GIF_1X1_B64 = "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
TINY_BMP_1X1_B64 = "Qk1GAAAAAAAAADYAAAAoAAAAAQAAAAEAAAABABgAAAAAABAAAADEDgAAxA4AAAAAAAAAAAAA////AA=="
TINY_AVIF_1X1_B64 = "AAAAGGZ0eXBhdmlmAAAAAGF2aWZtaWYxAAAAFGlzcGUAAAAAAAAAAQAAAAE="
TINY_JP2_1X1_B64 = "AAAADGpQICANCocKAAAAFGZ0eXBqcDIgAAAAAGpwMiAAAAAeanAyaAAAABZpaGRyAAAAAQAAAAEAAwcHAAAAAA=="
TINY_TIFF_1X1_B64 = "SUkqAAgAAAACAAABBAABAAAAAQAAAAEBBAABAAAAAQAAAAAAAAA="
MULTIPAGE_TIFF_1X1_B64 = "SUkqAAgAAAACAAABBAABAAAAAQAAAAEBBAABAAAAAQAAAAgAAAA="
SUBIFD_TIFF_1X1_B64 = "SUkqAAgAAAADAAABBAABAAAAAQAAAAEBBAABAAAAAQAAAEoBBAABAAAAgAAAAAAAAAA="
ANIMATED_GIF_3_FRAME_B64 = (
    "R0lGODlhEAAQAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAAEAAQAAAIHQABCBxIsKDBgwgTKlzIsKHDhxAjSpxI"
    "saLFgQEBACH5BAEKAAEALAAAAAAQABAAgQD/AAAAAAAAAAAAAAgdAAEIHEiwoMGDCBMqXMiwocOHECNKnEixosWBAQEAIfkEAQoAAQAsAAAAABAAEACB"
    "AAD/AAAAAAAAAAAACB0AAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYEBAQA7"
)


def _wav_b64(seconds: float = 0.1) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * int(16000 * seconds))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _audio_payload(data_b64: str, fmt: str = "wav", *, count: int = 1):
    parts = [{"type": "text", "text": "describe this"}]
    parts += [{"type": "input_audio", "input_audio": {"data": data_b64, "format": fmt}}] * count
    return {"model": "local-main", "messages": [{"role": "user", "content": parts}]}


def _video_payload(url: str, *, count: int = 1):
    parts = [{"type": "text", "text": "describe this"}]
    parts += [{"type": "video_url", "video_url": {"url": url}}] * count
    return {"model": "local-main", "messages": [{"role": "user", "content": parts}]}


def _validate(payload, modalities):
    return validate_chat_request(
        payload,
        expected_model="local-main",
        allowed_input_modalities=modalities,
        **IMAGE_LIMITS,
        **AUDIO_LIMITS,
        **VIDEO_LIMITS,
    )


def test_audio_part_accepted_when_profile_deploys_audio():
    _validate(_audio_payload(_wav_b64()), TEXT_IMAGE_AUDIO)


def test_audio_part_rejected_when_audio_not_deployed():
    # The current text+image model: audio must be refused, unchanged behavior.
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload(_wav_b64()), TEXT_IMAGE)
    assert exc.value.status_code == 422
    assert "audio content parts are not enabled" in str(exc.value)


def test_audio_format_must_be_allowed():
    with pytest.raises(ServiceError) as exc:
        validate_chat_request(
            _audio_payload(_wav_b64(), fmt="aac"),
            expected_model="local-main",
            allowed_input_modalities=TEXT_IMAGE_AUDIO,
            max_image_inputs=1,
            max_audio_inputs=1,
            allowed_audio_formats=("wav",),
            max_audio_bytes=25_000_000,
        )
    assert exc.value.status_code == 422
    assert "input_audio.format" in str(exc.value)


@pytest.mark.parametrize(
    ("fmt", "raw"),
    [
        ("m4a", b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42isom"),
        ("mp4", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"),
        ("aac", b"\xff\xf1\x50\x80\x00\x1f\xfc"),
    ],
)
def test_container_audio_formats_are_accepted_when_magic_matches(fmt, raw):
    _validate(_audio_payload(base64.b64encode(raw).decode("ascii"), fmt=fmt), TEXT_IMAGE_AUDIO)


def test_audio_oversize_rejected():
    payload = _audio_payload(_wav_b64(seconds=0.1))
    with pytest.raises(ServiceError) as exc:
        validate_chat_request(
            payload,
            expected_model="local-main",
            allowed_input_modalities=TEXT_IMAGE_AUDIO,
            max_image_inputs=1,
            max_audio_inputs=1,
            allowed_audio_formats=("wav",),
            max_audio_bytes=16,  # smaller than the canary
        )
    assert exc.value.status_code == 422
    assert "bytes or fewer" in str(exc.value)


def test_audio_magic_must_match_declared_format():
    # Valid WAV bytes but declared as mp3 -> magic mismatch is rejected.
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload(_wav_b64(), fmt="mp3"), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "does not look like a valid mp3" in str(exc.value)


def test_invalid_base64_audio_rejected():
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload("not-base64!!", fmt="wav"), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "valid base64" in str(exc.value)


def test_newline_wrapped_base64_audio_accepted():
    # `base64 file.wav` (CLI) and MIME encoders wrap at 76 columns. The gateway
    # must tolerate this exactly like the downstream runtime, not 422 it.
    clean = _wav_b64()
    wrapped = "\n".join(clean[i:i + 76] for i in range(0, len(clean), 76))
    assert "\n" in wrapped
    _validate(_audio_payload(wrapped, fmt="wav"), TEXT_IMAGE_AUDIO)


def test_whitespace_padded_base64_audio_accepted():
    _validate(_audio_payload("  " + _wav_b64() + "\n", fmt="wav"), TEXT_IMAGE_AUDIO)


def test_unpadded_base64_audio_still_rejected():
    # Missing '=' padding is non-standard and the runtime decoder also rejects it;
    # the gate stays strict here so the error surfaces early and clearly.
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload(_wav_b64().rstrip("="), fmt="wav"), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "valid base64" in str(exc.value)


def test_data_url_prefix_in_input_audio_data_rejected_with_specific_error():
    # input_audio.data is raw base64 (unlike image_url/video_url data: URLs).
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload("data:audio/wav;base64," + _wav_b64(), fmt="wav"), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "raw base64" in str(exc.value)


def test_too_many_audio_parts_rejected():
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload(_wav_b64(), count=2), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "audio content part(s) are allowed" in str(exc.value)


def test_video_part_accepted_when_profile_deploys_video():
    url = f"data:video/jpeg;base64,{TINY_JPEG_1X1_B64}"
    _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)


def test_video_part_rejected_when_video_not_deployed():
    url = f"data:video/jpeg;base64,{TINY_JPEG_1X1_B64}"
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(url), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "video content parts are not enabled" in str(exc.value)


def test_mp4_video_container_is_accepted_when_magic_matches():
    raw = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    url = "data:video/mp4;base64," + base64.b64encode(raw).decode("ascii")
    _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)


@pytest.mark.parametrize("mime,b64", [
    ("image/avif", TINY_AVIF_1X1_B64),
    ("image/jp2", TINY_JP2_1X1_B64),
    ("image/gif", TINY_GIF_1X1_B64),
    ("image/bmp", TINY_BMP_1X1_B64),
    ("image/tiff", TINY_TIFF_1X1_B64),
    ("image/x-tiff", TINY_TIFF_1X1_B64),
])
def test_additional_static_image_parts_are_accepted(mime, b64):
    payload = {
        "model": "local-main",
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}
        ],
    }

    _validate(payload, TEXT_IMAGE_AUDIO_VIDEO)


@pytest.mark.parametrize("b64", [MULTIPAGE_TIFF_1X1_B64, SUBIFD_TIFF_1X1_B64])
def test_multi_image_tiff_image_part_is_rejected_as_static_image_contract(b64):
    payload = {
        "model": "local-main",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/tiff;base64,{b64}"}}],
            }
        ],
    }

    with pytest.raises(ServiceError) as exc:
        _validate(payload, TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "image dimensions could not be read safely" in str(exc.value)


def test_animated_gif_image_part_points_client_to_video_gif():
    payload = {
        "model": "local-main",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/gif;base64,{ANIMATED_GIF_3_FRAME_B64}"}}],
            }
        ],
    }

    with pytest.raises(ServiceError) as exc:
        _validate(payload, TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "use video_url with data:video/gif" in str(exc.value)


def test_animated_gif_is_accepted_as_video_gif():
    _validate(_video_payload(f"data:video/gif;base64,{ANIMATED_GIF_3_FRAME_B64}"), TEXT_IMAGE_AUDIO_VIDEO)


def test_image_gif_is_not_a_video_url_contract():
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(f"data:image/gif;base64,{ANIMATED_GIF_3_FRAME_B64}"), TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "video_url.url scheme" in str(exc.value) or "valid data:video URL" in str(exc.value)


@pytest.mark.parametrize("mime", ["video/x-msvideo", "video/avi"])
def test_avi_video_container_is_accepted_when_magic_matches(mime):
    raw = b"RIFF\x20\x00\x00\x00AVI LIST"
    url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)


@pytest.mark.parametrize(("mime", "raw"), [
    ("video/x-matroska", b"\x1a\x45\xdf\xa3"),
])
def test_additional_video_containers_are_accepted_when_magic_matches(mime, raw):
    url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)


def test_avi_video_container_rejected_when_magic_does_not_match():
    url = "data:video/x-msvideo;base64," + base64.b64encode(b"not an avi").decode("ascii")
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "valid video/x-msvideo stream" in str(exc.value)


def test_video_mime_must_be_allowed():
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(f"data:video/x-flv;base64,{TINY_JPEG_1X1_B64}"), TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "video_url MIME type" in str(exc.value)


def test_too_many_video_parts_rejected():
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(f"data:video/jpeg;base64,{TINY_JPEG_1X1_B64}", count=2), TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "video content part(s) are allowed" in str(exc.value)


def test_text_only_request_unaffected_by_audio_params():
    payload = {"model": "local-main", "messages": [{"role": "user", "content": "hello"}]}
    _validate(payload, TEXT_IMAGE)  # must not raise


def test_configured_audio_formats_are_all_sniffable():
    # Guards the coupling: every format allowed in model_serving.yaml must have a
    # magic-byte check, or it would be silently rejected at runtime.
    from ai_model_serving.contracts.media import SNIFFABLE_AUDIO_FORMATS
    from ai_model_serving.settings import load_settings

    configured = set(load_settings().main_llm.allowed_audio_formats)
    assert configured <= SNIFFABLE_AUDIO_FORMATS, configured - SNIFFABLE_AUDIO_FORMATS


def test_configured_video_mime_types_are_all_sniffable():
    from ai_model_serving.contracts.media import SNIFFABLE_VIDEO_MIME_TYPES
    from ai_model_serving.settings import load_settings

    configured = set(load_settings().main_llm.allowed_video_mime_types)
    assert configured <= SNIFFABLE_VIDEO_MIME_TYPES, configured - SNIFFABLE_VIDEO_MIME_TYPES


def test_request_body_limit_can_carry_configured_media_limit(monkeypatch):
    # The raw HTTP cap must not reject valid max-size media before decoded
    # media validation can return the precise contract error/success.
    from ai_model_serving.settings import load_settings

    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    settings = load_settings()
    assert settings.main_llm is not None
    largest_decoded_media = max(settings.main_llm.max_audio_bytes, settings.main_llm.max_video_bytes)
    required_body_bytes = math.ceil(largest_decoded_media * 4 / 3) + 100_000
    assert settings.max_request_body_bytes >= required_body_bytes


def test_active_input_modalities_extracts_deployed_input():
    snapshot = {"active_profile": {"capabilities": {"deployed_input": ["text", "image", "audio", "video"]}}}
    assert _active_input_modalities(snapshot) == ("text", "image", "audio", "video")


def test_backend_audio_canary_is_a_valid_input_audio_part():
    # The boot canary the backend sends must itself satisfy the gateway's audio
    # validation (format/magic/size), or validation and runtime would disagree.
    from ai_model_serving.docker_main_model_backend import _AUDIO_CANARY_M4A_B64

    _validate(_audio_payload(_AUDIO_CANARY_M4A_B64, fmt="m4a"), TEXT_IMAGE_AUDIO)


def test_backend_video_canary_is_a_valid_video_url_part():
    from ai_model_serving.docker_main_model_backend import _VIDEO_CANARY_MP4_B64

    _validate(_video_payload(f"data:video/mp4;base64,{_VIDEO_CANARY_MP4_B64}"), TEXT_IMAGE_AUDIO_VIDEO)


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"active_profile": None},
        {"active_profile": {"capabilities": {}}},
        {"active_profile": {"capabilities": {"deployed_input": "text"}}},
        {"active_profile": {"capabilities": {"deployed_input": ["text", 3]}}},
    ],
)
def test_active_input_modalities_returns_none_on_malformed(snapshot):
    assert _active_input_modalities(snapshot) is None
