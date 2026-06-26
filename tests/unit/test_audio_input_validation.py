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

AUDIO_LIMITS = dict(
    max_audio_inputs=1,
    allowed_audio_formats=("wav", "mp3", "flac", "ogg"),
    max_audio_bytes=25_000_000,
)
TEXT_IMAGE = ("text", "image")
TEXT_IMAGE_AUDIO = ("text", "image", "audio")


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


def _validate(payload, modalities):
    return validate_chat_request(
        payload,
        expected_model="local-main",
        allowed_input_modalities=modalities,
        max_image_inputs=1,
        **AUDIO_LIMITS,
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
        _validate(_audio_payload(_wav_b64(), fmt="aac"), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "input_audio.format" in str(exc.value)


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


def test_too_many_audio_parts_rejected():
    with pytest.raises(ServiceError) as exc:
        _validate(_audio_payload(_wav_b64(), count=2), TEXT_IMAGE_AUDIO)
    assert exc.value.status_code == 422
    assert "audio content part(s) are allowed" in str(exc.value)


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


def test_request_body_limit_can_carry_configured_audio_limit(monkeypatch):
    # The raw HTTP cap must not reject valid max-size audio before decoded
    # audio validation can return the precise contract error/success.
    from ai_model_serving.settings import load_settings

    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    settings = load_settings()
    assert settings.main_llm is not None
    required_body_bytes = math.ceil(settings.main_llm.max_audio_bytes * 4 / 3) + 100_000
    assert settings.max_request_body_bytes >= required_body_bytes


def test_active_input_modalities_extracts_deployed_input():
    snapshot = {"active_profile": {"capabilities": {"deployed_input": ["text", "image", "audio"]}}}
    assert _active_input_modalities(snapshot) == ("text", "image", "audio")


def test_backend_audio_canary_is_a_valid_input_audio_part():
    # The boot canary the backend sends must itself satisfy the gateway's audio
    # validation (format/magic/size), or validation and runtime would disagree.
    from ai_model_serving.docker_main_model_backend import _AUDIO_CANARY_WAV_B64

    _validate(_audio_payload(_AUDIO_CANARY_WAV_B64), TEXT_IMAGE_AUDIO)


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
