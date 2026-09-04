from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def chat_request_parameter_surface(policy: dict[str, Any], *, max_output_tokens: int | None) -> dict[str, dict[str, Any]]:
    """사용자가 Gateway request에서 직접 조정할 수 있는 chat parameter surface."""
    supported = set(policy.get("supported_parameters", []))
    tool_policy = policy.get("tool_calling", {}) if isinstance(policy.get("tool_calling", {}), dict) else {}
    response_policy = policy.get("response_format", {}) if isinstance(policy.get("response_format", {}), dict) else {}
    json_schema_policy = response_policy.get("json_schema", {}) if isinstance(response_policy.get("json_schema", {}), dict) else {}
    logprobs_policy = policy.get("logprobs", {}) if isinstance(policy.get("logprobs", {}), dict) else {}
    top_logprobs_policy = policy.get("top_logprobs", {}) if isinstance(policy.get("top_logprobs", {}), dict) else {}
    logit_bias_policy = policy.get("logit_bias", {}) if isinstance(policy.get("logit_bias", {}), dict) else {}
    max_tools = int(tool_policy.get("max_tools", 16))
    parallel_tools_enabled = tool_policy.get("allow_parallel_tool_calls") is True
    max_n = int(policy.get("max_n", 1))
    definitions: dict[str, dict[str, Any]] = {
        "temperature": {"type": "number", "min": 0, "max": 2},
        # max_completion_tokens는 OpenAI가 max_tokens를 대체한 이름이며 같은 한도를
        # 가리킨다. 별도 knob이 아니므로 별칭으로만 알린다.
        "max_tokens": {
            "type": "integer",
            "min": 1,
            "aliases": ["max_completion_tokens"],
            **({"max": int(max_output_tokens)} if max_output_tokens is not None else {}),
        },
        "top_p": {"type": "number", "min_exclusive": 0, "max": 1},
        "top_k": {"type": "integer", "min": -1},
        "min_p": {"type": "number", "min": 0, "max": 1},
        "presence_penalty": {"type": "number", "min": -2, "max": 2},
        "frequency_penalty": {"type": "number", "min": -2, "max": 2},
        "repetition_penalty": {"type": "number", "min_exclusive": 0, "max": 2},
        "stop": {"type": "string_or_string_array", "max_items": 8},
        "seed": {"type": "integer", "min": 0},
        "n": {"type": "integer", "min": 1, "max": max_n},
        "stream": {"type": "boolean"},
        "stream_options": {"type": "object", "properties": {"include_usage": {"type": "boolean"}}, "additional_properties": False},
        "tools": {"type": "array", "min_items": 1, "max_items": max_tools},
        "tool_choice": {"type": "string_or_function_choice", "allowed": ["auto", "none", "required"]},
        "parallel_tool_calls": {"type": "boolean", "const": parallel_tools_enabled},
        "reasoning": {"type": "boolean", "default": False, "mode": "request_opt_in"},
        "response_format": {
            "type": "object",
            "allowed_types": list(response_policy.get("types", ["text", "json_object", "json_schema"])),
            "json_object": {
                "require_json_instruction": bool(response_policy.get("json_object", {}).get("require_json_instruction", True))
                if isinstance(response_policy.get("json_object", {}), dict)
                else True
            },
            "json_schema": {
                "max_schema_bytes": int(json_schema_policy.get("max_schema_bytes", 16384)),
                "max_depth": int(json_schema_policy.get("max_depth", 8)),
                "max_total_properties": int(json_schema_policy.get("max_total_properties", 64)),
                "require_root_object": json_schema_policy.get("require_root_object", True) is True,
                "require_additional_properties_false": json_schema_policy.get("require_additional_properties_false", True) is True,
                "strict": dict(json_schema_policy.get("strict", {"allowed": True, "require_true": False})),
            },
        },
        "logprobs": {
            "type": "boolean",
            "default": logprobs_policy.get("default", False) is True,
            "allow_stream": logprobs_policy.get("allow_stream", True) is True,
        },
        "top_logprobs": {
            "type": "integer",
            "min": int(top_logprobs_policy.get("min", 0)),
            "max": int(top_logprobs_policy.get("max", 10)),
            "requires": {"logprobs": top_logprobs_policy.get("requires_logprobs", True) is True},
        },
        "logit_bias": {
            "type": "object",
            "max_entries": int(logit_bias_policy.get("max_entries", 256)),
            "value_min": int(logit_bias_policy.get("min_bias", -100)),
            "value_max": int(logit_bias_policy.get("max_bias", 100)),
            "token_id_semantics": str(logit_bias_policy.get("token_id_semantics", "served_model_tokenizer")),
        },
    }
    return {name: definitions[name] for name in policy.get("supported_parameters", []) if name in definitions and name in supported}


def _embedding_request_parameters(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """사용자가 Gateway request에서 직접 조정할 수 있는 embedding parameter surface."""
    supported = set(policy.get("supported_parameters", []))
    definitions: dict[str, dict[str, Any]] = {
        "dimensions": {"type": "integer", "enum": [int(item) for item in policy.get("dimensions", [])]},
        "encoding_format": {"type": "string", "enum": [str(item) for item in policy.get("encoding_formats", ["float"])]},
        "truncate_prompt_tokens": {
            "type": "integer",
            "one_of": [
                {"const": -1},
                {"min": 1, "max": int(policy.get("max_truncate_prompt_tokens", 2048))},
            ],
        },
    }
    return {name: definitions[name] for name in policy.get("supported_parameters", []) if name in definitions and name in supported}


def _request_parameter_surface(
    *,
    capabilities: tuple[str, ...],
    serving_cfg: dict[str, Any],
    max_output_tokens: int | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """모델 조회용으로 사용자 조정 가능 parameter와 runtime 고정 parameter를 반환한다."""
    policy = serving_cfg.get("request_parameter_policy", {}) if isinstance(serving_cfg.get("request_parameter_policy", {}), dict) else {}
    if any(capability.startswith("risk.") for capability in capabilities):
        fixed = policy.get("fixed_parameters", {}) if isinstance(policy.get("fixed_parameters", {}), dict) else {}
        return {}, dict(fixed)
    if any(capability.startswith("chat.completions") for capability in capabilities):
        return chat_request_parameter_surface(policy, max_output_tokens=max_output_tokens), {}
    if "embeddings" in capabilities:
        return _embedding_request_parameters(policy), {}
    return {}, {}

# 모달리티별로 공개하는 한도 키. 값은 gateway_policy.request_limits의 내부 키에서
# 가져오되, 공개 이름은 여기서 따로 정한다 -- request_parameter_policy를 날것으로
# 내보내지 않고 chat_request_parameter_surface로 표면을 만드는 것과 같은 이유다.
# 내부 키 이름에 공개 계약이 묶이면 내부 정리가 곧 파괴적 변경이 된다.
_LIMIT_SURFACE: dict[str, dict[str, str]] = {
    "image": {
        "max_inputs": "max_image_inputs",
        "max_bytes": "max_image_bytes",
        "max_pixels": "max_image_pixels",
        "allowed_mime_types": "allowed_image_mime_types",
        "allowed_url_schemes": "allowed_image_url_schemes",
    },
    "audio": {
        "max_inputs": "max_audio_inputs",
        "max_bytes": "max_audio_bytes",
        "allowed_formats": "allowed_audio_formats",
    },
    "video": {
        "max_inputs": "max_video_inputs",
        "max_bytes": "max_video_bytes",
        "max_frames": "max_video_frames",
        "max_frame_pixels": "max_video_frame_pixels",
        "max_duration_seconds": "max_video_duration_seconds",
        "allowed_mime_types": "allowed_video_mime_types",
        "allowed_url_schemes": "allowed_video_url_schemes",
    },
}


def chat_request_limit_surface(
    limits: dict[str, Any],
    *,
    input_modalities: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """비텍스트 입력에 대해 Gateway가 강제하는 한도를 모달리티별로 공개한다.

    ``request_parameters``는 사용자가 조정하는 knob의 표면이고, 이것은 조정할 수
    없는 콘텐츠 제약이라 자리를 나눈다 -- 이미지는 파라미터가 아니라
    ``messages[].content`` 안으로 들어온다.

    받는 modality만 담는다. 프로필마다 값이 다르고(26B는 audio/video가 없다)
    호출자는 활성 프로필의 값을 그대로 본다. 그래서 클라이언트가 자기 쪽에
    한도를 복제해 둘 이유가 없어진다 -- 복제본은 프로필이 바뀌는 순간 조용히
    낡는다.
    """
    accepted = {str(modality) for modality in input_modalities}
    surface: dict[str, dict[str, Any]] = {}
    for modality, fields in _LIMIT_SURFACE.items():
        if modality not in accepted:
            continue
        published = {
            public_name: limits[internal_name]
            for public_name, internal_name in fields.items()
            if limits.get(internal_name) is not None
        }
        if published:
            surface[modality] = published
    return surface
