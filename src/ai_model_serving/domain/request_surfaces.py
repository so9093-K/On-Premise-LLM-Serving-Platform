from __future__ import annotations

from typing import Any


def _chat_request_parameters(policy: dict[str, Any], *, max_output_tokens: int | None) -> dict[str, dict[str, Any]]:
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
        "max_tokens": {"type": "integer", "min": 1, **({"max": int(max_output_tokens)} if max_output_tokens is not None else {})},
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


def _retrieval_request_parameters(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Retrieval request에서 사용자가 직접 조정할 수 있는 parameter surface."""
    supported = set(policy.get("supported_parameters", []))
    if not supported:
        return {}

    definitions: dict[str, dict[str, Any]] = {}

    if "score_mode" in supported:
        cfg = policy.get("score_mode", {})
        if isinstance(cfg, dict):
            defn: dict[str, Any] = {"type": "string"}
            if "enum" in cfg:
                defn["enum"] = [str(v) for v in cfg["enum"]]
            if "default" in cfg:
                defn["default"] = cfg["default"]
            definitions["score_mode"] = defn

    if "top_n" in supported:
        cfg = policy.get("top_n", {})
        if isinstance(cfg, dict):
            defn = {"type": "integer"}
            if "min" in cfg:
                defn["min"] = int(cfg["min"])
            if "max" in cfg:
                defn["max"] = int(cfg["max"])
            if "default" in cfg:
                defn["default"] = cfg["default"]
            definitions["top_n"] = defn

    if "max_tokens_per_query" in supported:
        cfg = policy.get("max_tokens_per_query", {})
        if isinstance(cfg, dict):
            defn = {"type": "integer"}
            if "min" in cfg:
                defn["min"] = int(cfg["min"])
            if "max" in cfg:
                defn["max"] = int(cfg["max"])
            if "default" in cfg:
                defn["default"] = int(cfg["default"])
            definitions["max_tokens_per_query"] = defn

    if "max_tokens_per_doc" in supported:
        cfg = policy.get("max_tokens_per_doc", {})
        if isinstance(cfg, dict):
            defn = {"type": "integer"}
            if "min" in cfg:
                defn["min"] = int(cfg["min"])
            if "max" in cfg:
                defn["max"] = int(cfg["max"])
            if "default" in cfg:
                defn["default"] = int(cfg["default"])
            definitions["max_tokens_per_doc"] = defn

    if "truncate_prompt_tokens" in supported:
        cfg = policy.get("truncate_prompt_tokens", {})
        if isinstance(cfg, dict):
            defn = {"type": "integer"}
            one_of = cfg.get("one_of", [])
            if one_of:
                defn["one_of"] = [dict(item) for item in one_of]
            definitions["truncate_prompt_tokens"] = defn

    if "truncation_side" in supported:
        cfg = policy.get("truncation_side", {})
        if isinstance(cfg, dict):
            defn = {"type": "string"}
            if "enum" in cfg:
                defn["enum"] = list(cfg["enum"])
            if "default" in cfg:
                defn["default"] = cfg["default"]
            definitions["truncation_side"] = defn

    return {name: definitions[name] for name in supported if name in definitions}


def _embedding_request_parameters(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """사용자가 Gateway request에서 직접 조정할 수 있는 embedding parameter surface."""
    supported = set(policy.get("supported_parameters", []))
    definitions: dict[str, dict[str, Any]] = {
        "dimensions": {"type": "integer", "enum": [int(item) for item in policy.get("dimensions", [])]},
        "encoding_format": {"type": "string", "const": "float"},
        "truncate_prompt_tokens": {
            "type": "integer",
            "one_of": [
                {"const": -1},
                {"min": 1, "max": int(policy.get("max_truncate_prompt_tokens", 2048))},
            ],
        },
    }
    return {name: definitions[name] for name in policy.get("supported_parameters", []) if name in definitions and name in supported}


_RETRIEVAL_CAPABILITIES = frozenset({"retrieval_rerank", "retrieval_score"})


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
        return _chat_request_parameters(policy, max_output_tokens=max_output_tokens), {}
    if "embeddings" in capabilities:
        return _embedding_request_parameters(policy), {}
    if any(cap in _RETRIEVAL_CAPABILITIES for cap in capabilities):
        fixed = policy.get("fixed_parameters", {}) if isinstance(policy.get("fixed_parameters", {}), dict) else {}
        return _retrieval_request_parameters(policy), dict(fixed)
    return {}, {}
