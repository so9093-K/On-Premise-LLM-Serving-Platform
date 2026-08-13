from __future__ import annotations

import json
from typing import Any

VLLM_SCALAR_ARGS: tuple[str, ...] = (
    "served_model_name",
    "host",
    "port",
    "max_model_len",
    "max_num_seqs",
    "max_num_batched_tokens",
    "gpu_memory_utilization",
    "runner",
    "tokenizer",
    "convert",
    "model_impl",
    "tensor_parallel_size",
    "dtype",
    "load_format",
    "optimization_level",
)

VLLM_JSON_ARGS: tuple[str, ...] = (
    "compilation_config",
)

VLLM_BOOL_ARGS: tuple[str, ...] = (
    "trust_remote_code",
    "enforce_eager",
)


def _arg_name(key: str) -> str:
    return f"--{key.replace('_', '-')}"


def _append_scalar_args(cmd: list[str], cfg: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        value = cfg.get(key)
        if value is not None:
            cmd.extend([_arg_name(key), str(value)])


def _append_json_args(cmd: list[str], cfg: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        value = cfg.get(key)
        if value is not None:
            cmd.extend([_arg_name(key), json.dumps(value, separators=(",", ":"), sort_keys=True)])


def _append_bool_args(cmd: list[str], cfg: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if cfg.get(key) is True:
            cmd.append(_arg_name(key))


def _append_runtime_features(cmd: list[str], cfg: dict[str, Any]) -> None:
    features = cfg.get("runtime_features", {}) or {}
    prefix = features.get("prefix_caching", {}) or {}
    if prefix.get("enabled") is True:
        cmd.append("--enable-prefix-caching")
        if prefix.get("hash_algo"):
            cmd.extend(["--prefix-caching-hash-algo", str(prefix["hash_algo"])])

    tool_calling = features.get("tool_calling", {}) or {}
    if tool_calling.get("enabled") is True:
        if tool_calling.get("auto_tool_choice", True):
            cmd.append("--enable-auto-tool-choice")
        if tool_calling.get("tool_call_parser"):
            cmd.extend(["--tool-call-parser", str(tool_calling["tool_call_parser"])])
        if tool_calling.get("reasoning_parser"):
            cmd.extend(["--reasoning-parser", str(tool_calling["reasoning_parser"])])
        if tool_calling.get("chat_template"):
            cmd.extend(["--chat-template", str(tool_calling["chat_template"])])

    chat_template = features.get("chat_template", {}) or {}
    if chat_template.get("content_format"):
        cmd.extend(["--chat-template-content-format", str(chat_template["content_format"])])

    structured_outputs = features.get("structured_outputs", {}) or {}
    if structured_outputs.get("enabled") is True:
        config: dict[str, Any] = {
            "backend": structured_outputs.get("backend", "auto"),
            "enable_in_reasoning": structured_outputs.get("enable_in_reasoning") is True,
        }
        if structured_outputs.get("disable_any_whitespace") is True:
            config["disable_any_whitespace"] = True
        cmd.extend(["--structured-outputs-config", json.dumps(config, separators=(",", ":"))])


def render_vllm_command(key: str, cfg: dict[str, Any]) -> list[str]:
    model_path = cfg.get("runtime_model_path", cfg["name"])
    cmd = [
        "python",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(model_path),
    ]
    render_cfg = {"host": "0.0.0.0", **cfg}
    scalar_keys = tuple(
        key
        for key in VLLM_SCALAR_ARGS
        if key != "runner" or cfg.get("runner") == "pooling"
    )
    _append_scalar_args(cmd, render_cfg, scalar_keys)
    if cfg.get("pooler_config"):
        for name, value in dict(cfg["pooler_config"]).items():
            cmd.extend([f"--pooler-config.{name}", str(value)])
    quantization_source = str(cfg.get("quantization_source", "cli"))
    if cfg.get("quantization") and quantization_source != "model_config":
        cmd.extend(["--quantization", str(cfg["quantization"])])
    _append_json_args(cmd, cfg, VLLM_JSON_ARGS)
    _append_bool_args(cmd, cfg, VLLM_BOOL_ARGS)
    _append_runtime_features(cmd, cfg)
    return cmd
