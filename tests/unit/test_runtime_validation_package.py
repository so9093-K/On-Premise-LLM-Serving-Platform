"""runtime_validation 패키지의 실제 live validation 보조 결정 함수만 검증한다."""

from __future__ import annotations

import json

from ai_model_serving.runtime_validation import render_vllm_command


def test_render_vllm_command_stays_openai_compatible() -> None:
    cfg = {
        "name": "example/model",
        "served_model_name": "local-main",
        "port": 9401,
        "max_model_len": 8192,
        "max_num_seqs": 1,
        "max_num_batched_tokens": 4096,
        "gpu_memory_utilization": 0.58,
        "optimization_level": 3,
        "compilation_config": {"mode": 3},
        "tensor_parallel_size": 1,
        "dtype": "half",
        "quantization": "awq",
        "trust_remote_code": True,
        "runtime_features": {
            "prefix_caching": {"enabled": True, "hash_algo": "sha256_cbor"},
            "tool_calling": {"enabled": True, "tool_call_parser": "gemma4"},
            "structured_outputs": {"enabled": True, "backend": "xgrammar", "disable_any_whitespace": True, "enable_in_reasoning": False},
        },
    }
    command = render_vllm_command("main_llm", cfg)
    assert command[:3] == ["python", "-m", "vllm.entrypoints.openai.api_server"]
    assert "--served-model-name" in command
    assert "local-main" in command
    assert "--enable-prefix-caching" in command
    assert "--enable-auto-tool-choice" in command
    assert command[command.index("--optimization-level") + 1] == "3"
    assert command[command.index("--compilation-config") + 1] == '{"mode":3}'
    structured_index = command.index("--structured-outputs-config")
    assert json.loads(command[structured_index + 1]) == {"backend": "xgrammar", "disable_any_whitespace": True, "enable_in_reasoning": False}


def test_render_vllm_command_respects_model_config_quantization() -> None:
    cfg = {
        "name": "example/fp8-compressed",
        "served_model_name": "local-main",
        "port": 9401,
        "max_model_len": 32768,
        "max_num_seqs": 1,
        "max_num_batched_tokens": 32768,
        "gpu_memory_utilization": 0.66,
        "tensor_parallel_size": 1,
        "dtype": "auto",
        "quantization": "compressed-tensors",
        "quantization_source": "model_config",
        "optimization_level": 3,
    }
    command = render_vllm_command("main_llm", cfg)
    assert "--quantization" not in command
    assert "--optimization-level" in command
