"""Audio 입력 검증 + 프로필별 동적 modality 가드.

audio는 active main-model 프로필이 실제로 배포한 경우에만 허용된다. 여기서
가장 중요한 보장은 부정적인 쪽이다: 현재 text+image 모델이 active인 상태(no
`audio` modality)에서는 `input_audio` part가 예전과 똑같이 거부된다 -- audio
기능은 어떤 프로필이 실제로 배포하기 전까지는 비활성 상태다.
"""

from __future__ import annotations

import base64
import math
import io
import wave

import pytest

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
    max_video_frames=60,
    max_video_frame_pixels=12_845_056,
    max_video_duration_seconds=60,
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


def _image_payload(url: str, *, count: int = 1):
    parts = [{"type": "text", "text": "describe this"}]
    parts += [{"type": "image_url", "image_url": {"url": url}}] * count
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
    # 현재 text+image 모델: audio는 거부되어야 한다, 기존과 동일한 동작.
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
    assert exc.value.param == "input_audio"


@pytest.mark.parametrize(
    ("fmt", "raw"),
    [
        # m4a/mp4는 _audio_format_matches()에서 같은 _is_iso_bmff() 분기를 타므로
        # 둘 다 따로 테스트할 필요가 없다 -- m4a 하나로 그 분기를 증명한다.
        ("m4a", b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42isom"),
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
            max_audio_bytes=16,  # canary보다 작게
        )
    assert exc.value.status_code == 422
    assert "bytes or fewer" in str(exc.value)


def test_audio_magic_must_match_declared_format():
    # 유효한 WAV 바이트지만 mp3로 선언됨 -> magic 불일치로 거부된다.
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
    # `base64 file.wav`(CLI)와 MIME 인코더는 76컬럼에서 줄바꿈한다. gateway는
    # 이를 downstream runtime과 똑같이 허용해야 하고, 422로 거부하면 안 된다.
    clean = _wav_b64()
    wrapped = "\n".join(clean[i:i + 76] for i in range(0, len(clean), 76))
    assert "\n" in wrapped
    _validate(_audio_payload(wrapped, fmt="wav"), TEXT_IMAGE_AUDIO)


def test_whitespace_padded_base64_audio_accepted():
    _validate(_audio_payload("  " + _wav_b64() + "\n", fmt="wav"), TEXT_IMAGE_AUDIO)


def test_unpadded_base64_audio_still_rejected():
    # '=' 패딩 누락은 표준이 아니고 runtime 디코더도 이를 거부한다;
    # 그래서 여기 gate도 엄격하게 유지해 에러가 일찍, 명확하게 드러나게 한다.
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload(_wav_b64().rstrip("="), fmt="wav"), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "valid base64" in str(exc.value)


def test_data_url_prefix_in_input_audio_data_rejected_with_specific_error():
    # input_audio.data는 raw base64다(image_url/video_url의 data: URL과 다르다).
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


# image/x-tiff는 별도 케이스로 두지 않는다: _validate_data_image_url()은 gif를 제외하면
# media_type으로 분기하지 않고 _image_dimensions()가 바이트 내용만으로 파서를 순서대로
# 시도하므로, image/tiff와 동일한 _tiff_dimensions() 경로를 그대로 탄다.
@pytest.mark.parametrize("mime,b64", [
    ("image/avif", TINY_AVIF_1X1_B64),
    ("image/jp2", TINY_JP2_1X1_B64),
    ("image/gif", TINY_GIF_1X1_B64),
    ("image/bmp", TINY_BMP_1X1_B64),
    ("image/tiff", TINY_TIFF_1X1_B64),
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


def _build_gif(frame_count: int, delay_centiseconds: int, *, width: int = 1, height: int = 1) -> bytes:
    """구조적으로는 유효하지만 실제로 볼 수 있는 건 아닌 최소한의 GIF89a를 만든다.
    `frame_count`개 프레임 각각의 앞에 주어진 delay(1/100초)를 담은 Graphic
    Control Extension을 붙인다. 이미지 데이터는 dummy sub-block이다 -- _gif_metadata는
    구조(크기, 프레임 수, delay)만 읽고 픽셀은 절대 디코드하지 않는다."""
    header = b"GIF89a"
    lsd = width.to_bytes(2, "little") + height.to_bytes(2, "little") + bytes([0x00, 0x00, 0x00])
    frames = bytearray()
    for _ in range(frame_count):
        gce = bytes([0x21, 0xF9, 0x04, 0x00]) + delay_centiseconds.to_bytes(2, "little") + bytes([0x00, 0x00])
        image_descriptor = (
            bytes([0x2C])
            + (0).to_bytes(2, "little")
            + (0).to_bytes(2, "little")
            + width.to_bytes(2, "little")
            + height.to_bytes(2, "little")
            + bytes([0x80])
        )
        local_color_table = bytes([0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF])
        image_data = bytes([0x02, 0x01, 0x00, 0x00])
        frames += gce + image_descriptor + local_color_table + image_data
    return header + lsd + bytes(frames) + bytes([0x3B])


def _gif_b64(frame_count: int, delay_centiseconds: int) -> str:
    return base64.b64encode(_build_gif(frame_count, delay_centiseconds)).decode("ascii")


def test_gif_video_rejected_when_duration_exceeds_limit():
    # 프레임 10개 x 각 7초 delay = 70초, 60초인 max_video_duration_seconds 초과.
    url = f"data:video/gif;base64,{_gif_b64(10, 700)}"
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "plays for" in str(exc.value)


def test_gif_video_accepted_when_short_duration_despite_many_frames():
    # 짧고 프레임률이 높은 클립: 100프레임 x 0.05초 = 총 5초, 60초에 훨씬
    # 못 미친다 — 원시 프레임 수(100)는 예전의 고정 32프레임 상한을 훨씬
    # 넘는데도 그렇다. 이제 진짜 게이트는 프레임 수가 아니라 재생 시간이다.
    url = f"data:video/gif;base64,{_gif_b64(100, 5)}"
    _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)


def test_gif_video_rejected_by_frame_count_backstop_despite_short_duration():
    # 퇴화된 인코딩: delay가 거의 0인 2000프레임은 재생 시간이 ~0초로 계산되지만
    # downstream에서 실제 디코드 비용은 여전히 든다 -- duration 체크만으로는
    # 통과하겠지만, 프레임 수 backstop(max_video_duration_seconds * fps 상한 =
    # 60*30 = 1800)이 이걸 여전히 잡아내야 한다.
    url = f"data:video/gif;base64,{_gif_b64(2000, 0)}"
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(url), TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "frame(s)" in str(exc.value)


def test_image_gif_is_not_a_video_url_contract():
    with pytest.raises(ServiceError) as exc:
        _validate(_video_payload(f"data:image/gif;base64,{ANIMATED_GIF_3_FRAME_B64}"), TEXT_IMAGE_AUDIO_VIDEO)
    assert exc.value.status_code == 422
    assert "video_url.url scheme" in str(exc.value) or "valid data:video URL" in str(exc.value)


def test_avi_video_container_is_accepted_when_magic_matches():
    # video/avi는 _video_format_matches에서 video/x-msvideo와 같은 분기를 타므로
    # 따로 테스트하지 않는다.
    raw = b"RIFF\x20\x00\x00\x00AVI LIST"
    url = f"data:video/x-msvideo;base64," + base64.b64encode(raw).decode("ascii")
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
    _validate(payload, TEXT_IMAGE)  # 예외가 나면 안 된다


def test_configured_audio_formats_are_all_sniffable():
    # 결합 관계를 가드한다: model_serving.yaml에서 허용된 포맷은 전부
    # magic-byte 체크가 있어야 한다, 안 그러면 runtime에서 조용히 거부된다.
    from ai_model_serving.contracts.media import SNIFFABLE_AUDIO_FORMATS
    from ai_model_serving.settings import load_settings

    configured = set(load_settings().runtime("main_llm").allowed_audio_formats)
    assert configured <= SNIFFABLE_AUDIO_FORMATS, configured - SNIFFABLE_AUDIO_FORMATS


def test_configured_video_mime_types_are_all_sniffable():
    from ai_model_serving.contracts.media import SNIFFABLE_VIDEO_MIME_TYPES
    from ai_model_serving.settings import load_settings

    configured = set(load_settings().runtime("main_llm").allowed_video_mime_types)
    assert configured <= SNIFFABLE_VIDEO_MIME_TYPES, configured - SNIFFABLE_VIDEO_MIME_TYPES


def test_request_body_limit_can_carry_configured_media_limit(monkeypatch):
    # 디코딩된 미디어 검증이 정확한 계약 위반/성공을 판단하기도 전에, 원시 HTTP
    # 크기 상한이 유효한 최대 크기 미디어를 먼저 거부해서는 안 된다.
    from ai_model_serving.settings import load_settings

    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    settings = load_settings()
    main_llm = settings.runtime("main_llm")
    largest_decoded_media = max(main_llm.max_audio_bytes, main_llm.max_video_bytes)
    required_body_bytes = math.ceil(largest_decoded_media * 4 / 3) + 100_000
    assert settings.max_request_body_bytes >= required_body_bytes
