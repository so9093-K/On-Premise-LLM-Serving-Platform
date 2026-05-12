from __future__ import annotations

from typing import Any


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


def render_vllm_command(key: str, cfg: dict[str, Any]) -> list[str]:
    cmd = [
        "python",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(cfg["name"]),
        "--served-model-name",
        str(cfg["served_model_name"]),
        "--host",
        "0.0.0.0",
        "--port",
        str(cfg["port"]),
        "--max-model-len",
        str(cfg["max_model_len"]),
        "--max-num-seqs",
        str(cfg["max_num_seqs"]),
        "--max-num-batched-tokens",
        str(cfg["max_num_batched_tokens"]),
        "--gpu-memory-utilization",
        str(cfg["gpu_memory_utilization"]),
    ]
    if cfg.get("runner") == "pooling":
        cmd.extend(["--runner", "pooling"])
    if cfg.get("tensor_parallel_size"):
        cmd.extend(["--tensor-parallel-size", str(cfg["tensor_parallel_size"])])
    if cfg.get("dtype"):
        cmd.extend(["--dtype", str(cfg["dtype"])])
    quantization_source = str(cfg.get("quantization_source", "cli"))
    if cfg.get("quantization") and quantization_source != "model_config":
        cmd.extend(["--quantization", str(cfg["quantization"])])
    if cfg.get("load_format"):
        cmd.extend(["--load-format", str(cfg["load_format"])])
    if cfg.get("trust_remote_code") is True:
        cmd.append("--trust-remote-code")
    if cfg.get("enforce_eager") is True:
        cmd.append("--enforce-eager")
    _append_runtime_features(cmd, cfg)
    return cmd
