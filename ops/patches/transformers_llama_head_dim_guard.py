#!/usr/bin/env python3
"""Apply and verify the Kanana Llama ``head_dim`` compatibility patch.

The risk vLLM image serves Kakao Kanana safeguard detectors. Kanana Prompt 2.1B
uses an explicit ``head_dim`` projection where ``hidden_size`` is intentionally
not divisible by ``num_attention_heads``. Some Transformers releases validate
Llama configs by using only ``hidden_size % num_attention_heads`` and reject that
configuration before vLLM, bitsandbytes, or GPU allocation starts.

This script keeps the image-level workaround explicit and auditable instead of
embedding an inline site-packages patch directly in the Dockerfile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

PATCH_ID = "transformers_llama_head_dim_guard"
TARGET_RELATIVE_PATH = "transformers/models/llama/configuration_llama.py"
OLD_SNIPPET = "if self.hidden_size % self.num_attention_heads != 0:"
NEW_SNIPPET = 'if getattr(self, "head_dim", None) is None and self.hidden_size % self.num_attention_heads != 0:'
DEFAULT_METADATA_PATH = Path("/usr/local/share/ai-model-serving/patches/transformers_llama_head_dim_guard.json")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def transformers_package_root() -> Path:
    try:
        import transformers  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("transformers is not installed in this image; cannot apply risk vLLM patch") from exc
    package_file = getattr(transformers, "__file__", None)
    if not package_file:
        raise SystemExit("unable to resolve transformers package location")
    return Path(package_file).resolve().parent


def target_path_from_args(path: str | None) -> Path:
    if path:
        return Path(path)
    return transformers_package_root() / "models" / "llama" / "configuration_llama.py"


def read_version(module_name: str) -> str | None:
    try:
        module = __import__(module_name)
    except ModuleNotFoundError:
        return None
    return str(getattr(module, "__version__", "unknown"))


def apply_patch(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"target file does not exist: {path}")
    original = path.read_text(encoding="utf-8")
    original_hash = sha256_text(original)
    if NEW_SNIPPET in original:
        status = "already_applied"
        patched = original
    elif OLD_SNIPPET in original:
        status = "applied" if not dry_run else "would_apply"
        patched = original.replace(OLD_SNIPPET, NEW_SNIPPET, 1)
        if not dry_run:
            path.write_text(patched, encoding="utf-8")
    else:
        candidates = [line for line in original.splitlines() if "hidden_size" in line and "num_attention_heads" in line]
        detail = "\n".join(f"  {line}" for line in candidates[:12])
        raise SystemExit(
            "patch pattern not found — Transformers may have changed; inspect validate_architecture."
            + (f"\nCandidate lines:\n{detail}" if detail else "")
        )
    patched_hash = sha256_text(patched)
    return {
        "patch_id": PATCH_ID,
        "status": status,
        "target_path": str(path),
        "old_snippet_sha256": sha256_text(OLD_SNIPPET),
        "new_snippet_sha256": sha256_text(NEW_SNIPPET),
        "original_file_sha256": original_hash,
        "patched_file_sha256": patched_hash,
        "changed": original_hash != patched_hash,
        "python_version": platform.python_version(),
        "transformers_version": read_version("transformers"),
        "huggingface_hub_version": read_version("huggingface_hub"),
        "applied_at_epoch": int(time.time()),
        "reason": "Kanana explicit head_dim compatibility for risk vLLM image",
        "remove_when": "Upstream Transformers/vLLM honors explicit Llama head_dim without image patch",
    }


def write_metadata(metadata: dict[str, Any], metadata_path: Path) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_patch(path: Path, metadata_path: Path | None = None) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"target file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    if NEW_SNIPPET not in text:
        raise SystemExit(f"{PATCH_ID} is not present in {path}")
    result: dict[str, Any] = {
        "patch_id": PATCH_ID,
        "status": "verified",
        "target_path": str(path),
        "patched_file_sha256": sha256_text(text),
        "transformers_version": read_version("transformers"),
        "huggingface_hub_version": read_version("huggingface_hub"),
    }
    if metadata_path and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        result["metadata_status"] = metadata.get("status")
        result["metadata_file_sha256"] = metadata.get("patched_file_sha256")
        if metadata.get("patched_file_sha256") != result["patched_file_sha256"]:
            raise SystemExit("patch metadata hash does not match current target file")
    elif metadata_path:
        result["metadata_status"] = "missing"
    return result

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply or verify the Kanana Transformers Llama head_dim guard patch.")
    parser.add_argument("--target", help="Override path to configuration_llama.py; defaults to installed Transformers package.")
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA_PATH), help="Patch metadata JSON path.")
    parser.add_argument("--verify", action="store_true", help="Verify the patch and metadata instead of applying it.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be patched without writing target file.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = target_path_from_args(args.target)
    metadata_path = Path(args.metadata) if args.metadata else DEFAULT_METADATA_PATH
    if args.verify:
        result = verify_patch(target, metadata_path)
    else:
        result = apply_patch(target, dry_run=args.dry_run)
        if not args.dry_run:
            write_metadata(result, metadata_path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{PATCH_ID}: {result['status']}")
        print(f"target={result['target_path']}")
        print(f"transformers={result.get('transformers_version')}")
        if metadata_path:
            print(f"metadata={metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
