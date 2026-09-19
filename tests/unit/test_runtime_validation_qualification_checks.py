from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.validation.runtime.live_checks import LiveRuntimeChecks


class _Registry:
    def public_logical_ids(self) -> list[str]:
        return ["local-main"]

    def runtime_service(self, key: str) -> SimpleNamespace:
        served = "local-main" if key == "main_llm" else key
        return SimpleNamespace(served_model_name=served)


class _Http:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def json(self, method: str, url: str, payload=None, **kwargs):
        self.calls.append((method, url, payload))
        if url.endswith("/v1/models"):
            return (
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "local-main",
                            "object": "model",
                            "created": 1,
                            "owned_by": "local",
                            "input_modalities": ["text", "image", "audio", "video"],
                            "request_parameters": {},
                            "request_limits": {},
                        }
                    ],
                },
                1,
            )
        if url.endswith("/models"):
            return 200, {"data": [{"id": "local-main"}]}, 1
        return (
            200,
            {
                "object": "chat.completion",
                "model": "local-main",
                "choices": [{"message": {"content": "OK"}}],
            },
            1,
        )


def _checks() -> tuple[LiveRuntimeChecks, _Http]:
    config = SimpleNamespace(
        root=Path("."),
        gateway_base="http://gateway",
        risk_base="http://risk",
        prometheus_base="http://prometheus",
        grafana_base="http://grafana",
        vllm_bases={"main_llm": "http://main/v1"},
        model_serving={
            "models": {
                "main_llm": {
                    "served_model_name": "local-main",
                }
            }
        },
    )
    http = _Http()
    return (
        LiveRuntimeChecks(
            config=config,
            registry=_Registry(),
            monitoring={},
            http=http,
        ),
        http,
    )


def test_main_model_runtime_checks_emit_stable_qualification_ids() -> None:
    checks, _ = _checks()

    models = checks.check_models()
    runtime_models = checks.check_vllm_models("main_llm", "http://main/v1")
    text_chat = checks.check_chat()

    assert models.qualification_check_id == "main_model.gateway.models"
    assert models.details["main_model_input_modalities"] == [
        "text",
        "image",
        "audio",
        "video",
    ]
    assert runtime_models.qualification_check_id == "main_model.runtime.models"
    assert text_chat.qualification_check_id == "main_model.chat.text"


def test_media_canaries_emit_stable_ids_and_use_checked_in_data_fixtures() -> None:
    checks, http = _checks()

    image = checks.check_chat_image()
    audio = checks.check_chat_audio()
    video = checks.check_chat_video()

    assert image.qualification_check_id == "main_model.chat.image"
    assert audio.qualification_check_id == "main_model.chat.audio"
    assert video.qualification_check_id == "main_model.chat.video"

    image_part = http.calls[-3][2]["messages"][0]["content"][1]
    audio_part = http.calls[-2][2]["messages"][0]["content"][1]
    video_part = http.calls[-1][2]["messages"][0]["content"][1]

    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert audio_part["type"] == "input_audio"
    assert audio_part["input_audio"]["format"] == "m4a"
    assert audio_part["input_audio"]["data"]
    assert video_part["type"] == "video_url"
    assert video_part["video_url"]["url"].startswith("data:video/mp4;base64,")
