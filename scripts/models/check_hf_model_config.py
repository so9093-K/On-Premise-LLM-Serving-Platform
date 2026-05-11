from __future__ import annotations

import argparse
import json
import platform
import sys
import traceback
from dataclasses import asdict, dataclass
from typing import Any


DEFAULT_MODEL = "kakaocorp/kanana-safeguard-prompt-2.1b"


@dataclass(frozen=True)
class ConfigShape:
    model: str
    config_class: str
    model_type: str | None
    architectures: list[str]
    hidden_size: int | None
    num_attention_heads: int | None
    num_key_value_heads: int | None
    head_dim: int | None
    hidden_size_divisible_by_attention_heads: bool | None
    attention_projection_width: int | None
    attention_projection_matches_hidden_size: bool | None
    requires_runtime_head_dim_support: bool


def classify_exception(exc: BaseException) -> str:
    text = " ".join(str(part) for part in [type(exc).__name__, exc])
    lower = text.lower()
    if "hidden size" in lower and "attention heads" in lower:
        return "CONFIG_VALIDATION_HIDDEN_HEAD_MISMATCH"
    if "gated repo" in lower or "access to model" in lower or "401" in lower or "403" in lower:
        return "HUGGINGFACE_AUTH_OR_LICENSE"
    if "connection" in lower or "timed out" in lower or "name resolution" in lower:
        return "NETWORK_OR_HF_HUB_UNAVAILABLE"
    if "is not a local folder" in lower or "does not appear to have a file named config.json" in lower:
        return "MODEL_ID_OR_CACHE_MISSING"
    return "CONFIG_LOAD_FAILED"


def interpretation_for_failure(classification: str) -> str:
    if classification == "CONFIG_VALIDATION_HIDDEN_HEAD_MISMATCH":
        return (
            "Config load failed at Transformers architecture validation. "
            "transformers 4.52.0–4.52.3 introduced LlamaConfig.validate_architecture "
            "which rejects hidden_size not divisible by num_attention_heads even when "
            "explicit head_dim is set. "
            "huggingface_hub >= 1.13.0 strengthened init_with_validate enforcement so "
            "AutoConfig.from_pretrained calls validate_architecture at load time — the "
            "combination of transformers 4.52.0–4.52.3 and huggingface_hub >= 1.13.0 "
            "triggers this error. Fix: upgrade transformers to >= 4.52.4 "
            "(upstream patch for explicit head_dim support). Do NOT downgrade "
            "huggingface_hub to 0.x; that is a major-version regression."
        )
    return (
        "Failure happened while loading config only, before vLLM engine startup, "
        "bitsandbytes weight loading, GPU allocation, or KV-cache initialization."
    )


def shape_from_config(model: str, config: Any) -> ConfigShape:
    hidden_size = getattr(config, "hidden_size", None)
    num_attention_heads = getattr(config, "num_attention_heads", None)
    head_dim = getattr(config, "head_dim", None)
    projection_width = None
    projection_matches = None
    divisible = None
    if isinstance(hidden_size, int) and isinstance(num_attention_heads, int) and num_attention_heads > 0:
        divisible = hidden_size % num_attention_heads == 0
    if isinstance(head_dim, int) and isinstance(num_attention_heads, int):
        projection_width = head_dim * num_attention_heads
        if isinstance(hidden_size, int):
            projection_matches = projection_width == hidden_size
    return ConfigShape(
        model=model,
        config_class=type(config).__name__,
        model_type=getattr(config, "model_type", None),
        architectures=list(getattr(config, "architectures", []) or []),
        hidden_size=hidden_size,
        num_attention_heads=num_attention_heads,
        num_key_value_heads=getattr(config, "num_key_value_heads", None),
        head_dim=head_dim,
        hidden_size_divisible_by_attention_heads=divisible,
        attention_projection_width=projection_width,
        attention_projection_matches_hidden_size=projection_matches,
        requires_runtime_head_dim_support=divisible is False and isinstance(head_dim, int),
    )


def interpretation_for_shape(shape: ConfigShape) -> str:
    if shape.requires_runtime_head_dim_support:
        return (
            "Config loaded in Transformers, but hidden_size is not divisible by num_attention_heads. "
            "This model requires the serving runtime to honor explicit head_dim; a vLLM image that "
            "validates Llama configs with hidden_size / num_attention_heads may fail before "
            "bitsandbytes or GPU allocation starts."
        )
    return "Config loaded successfully. If vLLM still fails, continue with vLLM/bitsandbytes/GPU isolation."


def print_human_versions(transformers: Any, huggingface_hub: Any) -> None:
    print(f"python={platform.python_version()}")
    print(f"transformers={transformers.__version__}")
    print(f"huggingface_hub={huggingface_hub.__version__}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Load a Hugging Face model config with Transformers only. "
            "This separates config-loader failures from vLLM, bitsandbytes, GPU, and KV-cache failures."
        )
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--no-trust-remote-code", action="store_true")
    parser.add_argument("--local-files-only", action="store_true", help="Hugging Face Hub에 접속하지 않고 local cache만 사용합니다.")
    parser.add_argument("--json", action="store_true", help="기계가 읽기 쉬운 JSON 결과를 출력합니다.")
    parser.add_argument("--traceback", action="store_true", help="실패 시 전체 Python traceback을 출력합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import huggingface_hub
        import transformers
        from transformers import AutoConfig
    except ModuleNotFoundError as exc:
        print(
            "missing optional dependency: transformers/huggingface_hub. "
            "Run this inside the vLLM image or install a diagnostic env, for example: "
            "python3 -m pip install 'transformers>=4.52.4' huggingface_hub",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    trust_remote_code = not args.no_trust_remote_code
    if not args.json:
        print_human_versions(transformers, huggingface_hub)
        print(f"model={args.model}")
        print(f"trust_remote_code={trust_remote_code}")
        print(f"local_files_only={args.local_files_only}")

    try:
        config = AutoConfig.from_pretrained(
            args.model,
            trust_remote_code=trust_remote_code,
            local_files_only=args.local_files_only,
        )
    except Exception as exc:
        classification = classify_exception(exc)
        result = {
            "ok": False,
            "stage": "transformers_auto_config",
            "classification": classification,
            "model": args.model,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "interpretation": interpretation_for_failure(classification),
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"status=failed classification={classification}", file=sys.stderr)
            print(result["interpretation"], file=sys.stderr)
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            if args.traceback:
                traceback.print_exception(exc, file=sys.stderr)
        return 1

    shape = shape_from_config(args.model, config)
    result = {
        "ok": True,
        "stage": "transformers_auto_config",
        "shape": asdict(shape),
        "interpretation": interpretation_for_shape(shape),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("status=ok")
        for key, value in asdict(shape).items():
            print(f"{key}={value}")
        if shape.hidden_size_divisible_by_attention_heads is False:
            print(
                "note=hidden_size is not divisible by num_attention_heads; this is only safe if the runtime honors explicit head_dim.",
                file=sys.stderr,
            )
            print(
                "classification=CONFIG_LOADS_BUT_REQUIRES_RUNTIME_HEAD_DIM_SUPPORT",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
