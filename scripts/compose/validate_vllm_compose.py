from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit("Missing dependency: PyYAML. Run: python -m pip install --requirement requirements.lock") from exc

try:
    from jinja2 import Environment, TemplateSyntaxError
except ImportError as exc:
    raise SystemExit("Missing dependency: Jinja2. Run: python -m pip install --requirement requirements.lock") from exc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.domain import ModelRegistry
COMPOSE_PATH = ROOT / "ops/compose/full-stack.private-network.yaml"
SERVING_PATH = ROOT / "configs/model_serving.yaml"
CATALOG_PATH = ROOT / "configs/model_catalog.yaml"
GPU_BUDGETS_PATH = ROOT / "configs/gpu_budgets.yaml"
MAIN_MODEL_PROFILES_PATH = ROOT / "configs/main_model_profiles.yaml"
GEMMA4_CHAT_TEMPLATE_PATH = ROOT / "configs/gemma4_chat_template.jinja"

COMPOSE_SCALAR_ARGS = (
    "model",
    "served_model_name",
    "port",
    "revision",
    "max_model_len",
    "max_num_seqs",
    "max_num_batched_tokens",
    "gpu_memory_utilization",
    "optimization_level",
)

COMPOSE_JSON_ARGS = (
    "compilation_config",
)

def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


_COMPOSE_VAR_DEFAULT = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}$")


def resolve_compose_value(value: str) -> str:
    """Resolve Docker Compose ${VAR:-default} substitutions using os.environ then default."""
    m = _COMPOSE_VAR_DEFAULT.match(value)
    if m:
        return os.environ.get(m.group(1), m.group(2))
    return value


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
                result[key] = resolve_compose_value(command[idx + 1])
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


def normalize_json_arg(value: object, field: str, service: str) -> str:
    if value is None:
        raise SystemExit(f"{service}: --{field.replace('_','-')} is missing")
    try:
        return json.dumps(json.loads(str(value)), separators=(",", ":"), sort_keys=True)
    except Exception as exc:
        raise SystemExit(f"{service}: --{field.replace('_','-')} must be valid JSON, got {value!r}") from exc


def expected_compose_args(cfg: dict[str, Any], runtime: Any) -> dict[str, str]:
    values: dict[str, Any] = {
        "model": cfg.get("runtime_model_path", runtime.upstream_model_id),
        "served_model_name": runtime.served_model_name,
        "port": runtime.port,
    }
    for key in COMPOSE_SCALAR_ARGS:
        if key in {"model", "served_model_name", "port"}:
            continue
        if key in cfg:
            values[key] = cfg[key]
    return {key: str(value) for key, value in values.items()}


def validate_production_compose_no_build_blocks(
    compose_path: Path = COMPOSE_PATH,
) -> list[str]:
    """배포 compose가 source build 대신 사전 빌드 artifact만 소비하게 한다."""
    errors: list[str] = []

    compose = load_yaml(compose_path)

    for svc_name, svc in compose.get("services", {}).items():
        if "build" in svc:
            errors.append(
                f"ops/compose/full-stack.private-network.yaml service '{svc_name}' has a "
                f"'build' block. Use `make build-image` (scripts/build/build_platform_image.sh) "
                f"to build locally instead."
            )

    return errors


def validate_main_llm_bootstrap_image(compose: dict[str, Any]) -> list[str]:
    """Keep the static Compose bootstrap image aligned with the model-profile fallback.

    Compose needs an image before admin-sidecar can apply the active profile.  The
    profile catalog is the source of that fallback; the Compose interpolation is
    deliberately a projection so an empty AUDIO_VLLM_IMAGE has identical meaning
    in both paths.
    """
    errors: list[str] = []
    catalog = load_yaml(MAIN_MODEL_PROFILES_PATH)
    default_profile_id = catalog.get("default_profile")
    runtime_image = catalog.get("runtime", {}).get("image")
    profile = catalog.get("profiles", {}).get(default_profile_id, {})
    profile_image = profile.get("image")
    compose_image = compose.get("services", {}).get("main-llm-vllm", {}).get("image")

    if not isinstance(default_profile_id, str) or not isinstance(runtime_image, str):
        return ["configs/main_model_profiles.yaml must declare default_profile and runtime.image"]
    if profile_image != "${AUDIO_VLLM_IMAGE}":
        errors.append(
            f"default main-model profile {default_profile_id} must use ${{AUDIO_VLLM_IMAGE}} "
            "so CI/deploy can inject its immutable image digest"
        )
    expected_compose_image = f"${{AUDIO_VLLM_IMAGE:-{runtime_image}}}"
    if compose_image != expected_compose_image:
        errors.append(
            "main-llm-vllm.image must project the default profile fallback exactly: "
            f"expected {expected_compose_image!r}, got {compose_image!r}"
        )

    # 실제 command와 Gateway 계약은 같은 Profile이 소유한다. 모든 후보 Profile에서
    # context 한도와 output 한도가 조용히 갈라지지 않는지만 확인한다.
    for profile_id, item in catalog.get("profiles", {}).items():
        if not isinstance(item, dict):
            continue
        policy = item.get("gateway_policy", {})
        limits = policy.get("request_limits", {}) if isinstance(policy, dict) else {}
        args = command_args(item.get("command", []))
        if str(args.get("max_model_len")) != str(limits.get("max_model_len")):
            errors.append(
                f"main-model profile {profile_id} --max-model-len must match "
                "gateway_policy.request_limits.max_model_len"
            )
        try:
            if int(policy.get("max_output_tokens", 0)) > int(limits.get("max_model_len", 0)):
                errors.append(
                    f"main-model profile {profile_id} gateway max_output_tokens exceeds max_model_len"
                )
        except (TypeError, ValueError):
            errors.append(f"main-model profile {profile_id} gateway token limits must be integers")
    return errors


def validate_gemma4_chat_template() -> list[str]:
    """vLLM 기동 전 Gemma 4 템플릿의 Jinja 문법 오류를 확인한다."""
    if not GEMMA4_CHAT_TEMPLATE_PATH.is_file():
        return ["configs/gemma4_chat_template.jinja is missing"]
    try:
        Environment().from_string(GEMMA4_CHAT_TEMPLATE_PATH.read_text(encoding="utf-8"))
    except TemplateSyntaxError as exc:
        return [
            "configs/gemma4_chat_template.jinja has invalid Jinja syntax: "
            f"line {exc.lineno}: {exc.message}"
        ]
    return []


def validate_alignment(
    compose_path: Path = COMPOSE_PATH,
    *,
    effective_compose: dict[str, Any] | None = None,
) -> None:
    compose = effective_compose if effective_compose is not None else load_yaml(compose_path)
    serving_doc = load_yaml(SERVING_PATH)
    catalog_doc = load_yaml(CATALOG_PATH)
    gpu_budgets = load_yaml(GPU_BUDGETS_PATH)
    avoid_above = float(gpu_budgets["gpu"]["total_gpu_memory_utilization"]["avoid_above"])
    registry = ModelRegistry(catalog_doc, serving_doc)
    services = compose["services"]
    errors: list[str] = []
    total_gpu_util = 0.0

    errors.extend(validate_production_compose_no_build_blocks(compose_path))
    errors.extend(validate_main_llm_bootstrap_image(compose))
    errors.extend(validate_gemma4_chat_template())

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
        expected = expected_compose_args(cfg, runtime)
        for key, expected_value in expected.items():
            actual = str(args.get(key))
            if actual != expected_value:
                errors.append(f"{service_name}: --{key.replace('_','-')}={actual} does not match ModelRegistry projection {expected_value}")
        for key in COMPOSE_JSON_ARGS:
            if key in cfg:
                expected_value = json.dumps(cfg[key], separators=(",", ":"), sort_keys=True)
                actual = normalize_json_arg(args.get(key), key, service_name)
                if actual != expected_value:
                    errors.append(f"{service_name}: --{key.replace('_','-')}={actual} does not match ModelRegistry projection {expected_value}")

        if cfg.get("runner") == "pooling" and str(args.get("runner")) != "pooling":
            errors.append(f"{service_name}: embedding service must use --runner pooling")
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

    if total_gpu_util >= avoid_above:
        errors.append(
            "total configured gpu_memory_utilization must stay below "
            f"configs/gpu_budgets.yaml avoid_above: {total_gpu_util:.3f} >= {avoid_above:.3f}"
        )

    if errors:
        raise SystemExit("vLLM compose validation failed:\n- " + "\n- ".join(errors))

    print("vLLM compose validation completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate vLLM runtime policy against a Compose file."
    )
    parser.add_argument("--compose-file", type=Path, default=COMPOSE_PATH)
    parser.add_argument(
        "--effective-config",
        type=Path,
        help="docker compose config output to validate instead of re-interpolating source YAML",
    )
    args = parser.parse_args()
    compose_path = (
        args.compose_file
        if args.compose_file.is_absolute()
        else (ROOT / args.compose_file).resolve()
    )
    effective_compose = load_yaml(args.effective_config) if args.effective_config else None
    validate_alignment(compose_path, effective_compose=effective_compose)
