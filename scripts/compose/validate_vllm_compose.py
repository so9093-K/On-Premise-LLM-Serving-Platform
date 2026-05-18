from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit("Missing dependency: PyYAML. Run: python -m pip install --requirement requirements.lock") from exc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.domain import ModelRegistry
COMPOSE_PATH = ROOT / "ops/compose/full-stack.private-network.yaml"
SERVING_PATH = ROOT / "configs/model_serving.yaml"
CATALOG_PATH = ROOT / "configs/model_catalog.yaml"
GPU_BUDGETS_PATH = ROOT / "configs/gpu_budgets.yaml"
MODEL_CARD_DIR = ROOT / "model_cards"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def command_args(command: Any) -> dict[str, str | bool]:
    if not isinstance(command, list):
        raise SystemExit(f"vLLM command must be a list, got {type(command).__name__}")
    result: dict[str, str | bool] = {}
    idx = 0
    while idx < len(command):
        token = command[idx]
        if not isinstance(token, str):
            raise SystemExit(f"vLLM command token must be str: {token!r}")
        if token.startswith("--"):
            key = token[2:].replace("-", "_")
            if idx + 1 < len(command) and isinstance(command[idx + 1], str) and not command[idx + 1].startswith("--"):
                result[key] = command[idx + 1]
                idx += 2
            else:
                result[key] = True
                idx += 1
        else:
            idx += 1
    return result


def as_int(value: object, field: str, service: str) -> int:
    try:
        return int(str(value))
    except Exception as exc:
        raise SystemExit(f"{service}: {field} must be an integer, got {value!r}") from exc


def as_float(value: object, field: str, service: str) -> float:
    try:
        return float(str(value))
    except Exception as exc:
        raise SystemExit(f"{service}: {field} must be a float, got {value!r}") from exc


def validate_alignment() -> None:
    compose = load_yaml(COMPOSE_PATH)
    serving_doc = load_yaml(SERVING_PATH)
    catalog_doc = load_yaml(CATALOG_PATH)
    gpu_budgets = load_yaml(GPU_BUDGETS_PATH)
    avoid_above = float(gpu_budgets["gpu"]["total_gpu_memory_utilization"]["avoid_above"])
    registry = ModelRegistry(catalog_doc, serving_doc)
    services = compose["services"]
    errors: list[str] = []
    total_gpu_util = 0.0

    for runtime in registry.iter_runtime_services():
        if runtime.backend != "vllm":
            continue
        service_name = runtime.compose_service_name
        service = services.get(service_name)
        if not service:
            errors.append(f"missing service: {service_name or runtime.service_key}")
            continue
        if runtime.logical_id is None:
            errors.append(f"{runtime.service_key}: runtime service is not linked to a catalog logical_id")
            continue
        args = command_args(service.get("command"))
        cfg = runtime.config
        card_path = MODEL_CARD_DIR / f"{runtime.logical_id}.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card_policy = card.get("project_runtime_policy", {})
        catalog_policy = catalog_doc["models"][runtime.logical_id].get("project_runtime_policy", {})

        expected_model = str(cfg.get("runtime_model_path", runtime.upstream_model_id))
        expected = {
            "model": expected_model,
            "served_model_name": runtime.served_model_name,
            "port": str(runtime.port),
            "max_model_len": str(cfg["max_model_len"]),
            "max_num_seqs": str(cfg["max_num_seqs"]),
            "max_num_batched_tokens": str(cfg["max_num_batched_tokens"]),
            "gpu_memory_utilization": str(cfg["gpu_memory_utilization"]),
        }
        for key, expected_value in expected.items():
            actual = str(args.get(key))
            if actual != expected_value:
                errors.append(f"{service_name}: --{key.replace('_','-')}={actual} does not match ModelRegistry projection {expected_value}")

        if cfg.get("runner") == "pooling" and str(args.get("runner")) != "pooling":
            errors.append(f"{service_name}: embedding service must use --runner pooling")
        if runtime.logical_id == "local-colbert-ko":
            if args.get("model") == runtime.upstream_model_id:
                errors.append(f"{service_name}: must not pass the non-loadable Hugging Face repo root as --model")
            for key in ["tokenizer", "convert"]:
                if str(args.get(key)) != str(cfg.get(key)):
                    errors.append(f"{service_name}: --{key} must match ColBERT vLLM native config")
            pooler_task = args.get("pooler_config.task")
            if pooler_task != "token_embed":
                errors.append(f"{service_name}: ColBERT late interaction requires --pooler-config.task token_embed")

        max_model_len = as_int(args.get("max_model_len"), "max_model_len", service_name)
        max_batched = as_int(args.get("max_num_batched_tokens"), "max_num_batched_tokens", service_name)
        runner = args.get("runner", cfg.get("runner"))
        if runner == "pooling" and max_batched < max_model_len:
            errors.append(
                f"{service_name}: pooling runtime requires max_num_batched_tokens >= max_model_len "
                f"({max_batched} < {max_model_len})"
            )

        util = as_float(args.get("gpu_memory_utilization"), "gpu_memory_utilization", service_name)
        total_gpu_util += util

        if runtime.role in {"risk_prompt_detector", "risk_policy_detector"}:
            if args.get("quantization") != "bitsandbytes" or args.get("load_format") != "bitsandbytes":
                errors.append(f"{service_name}: risk detector compose defaults must keep bitsandbytes quantization/load-format")
            if cfg.get("quantization") != "bitsandbytes" or cfg.get("load_format") != "bitsandbytes":
                errors.append(f"{service_name}: configs/model_serving.yaml must keep bitsandbytes for risk detectors")
            if cfg.get("max_output_tokens") != 1:
                errors.append(f"{service_name}: risk detector max_output_tokens must remain 1")

        # Keep the three runtime policy sources aligned where they all declare the value.
        for key in ["max_model_len", "max_num_seqs", "max_num_batched_tokens", "gpu_memory_utilization"]:
            for source_name, source in [("model_catalog", catalog_policy), ("model_card", card_policy)]:
                if key in source and str(source[key]) != str(cfg[key]):
                    errors.append(f"{runtime.logical_id}: {source_name}.{key}={source[key]} does not match model_serving {cfg[key]}")

    if total_gpu_util >= avoid_above:
        errors.append(
            "total configured gpu_memory_utilization must stay below "
            f"configs/gpu_budgets.yaml avoid_above: {total_gpu_util:.3f} >= {avoid_above:.3f}"
        )

    if errors:
        raise SystemExit("vLLM compose validation failed:\n- " + "\n- ".join(errors))

    print("vLLM compose validation completed")


if __name__ == "__main__":
    validate_alignment()
