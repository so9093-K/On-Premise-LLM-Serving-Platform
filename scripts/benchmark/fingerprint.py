"""실행 환경 지문을 수집한다(ADR-0026 8절).

지문이 없으면 6개월 뒤 숫자를 해석할 수 없다. 그래서 결과 schema가 이 값들을
required로 잡고 있고, 여기서 채우지 못하면 결과가 검증을 통과하지 못한다.

값은 저장소의 기존 Source of Truth에서만 읽는다. 모델 id와 revision을 여기서
다시 적지 않는다 -- target별 profile catalog가 소유한다.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any

from scripts.benchmark.contract import ROOT, load_yaml_mapping

_MACOS_RUNTIME = ROOT / "configs" / "macos_mlx_runtime.yaml"
_MAIN_PROFILES = ROOT / "configs" / "main_model_profiles.yaml"
_TARGETS = ROOT / "configs" / "deployment_targets.yaml"


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or len(commit) < 7:
        raise RuntimeError("cannot resolve git commit; benchmark results without it are not comparable")
    return commit


def _deployment_target() -> tuple[str, dict[str, Any]]:
    target_id = os.getenv("DEPLOYMENT_TARGET", "").strip()
    if not target_id:
        raise RuntimeError("DEPLOYMENT_TARGET is not set; run `make setup` or export it")
    document = load_yaml_mapping(_TARGETS)
    targets = document.get("targets", document)
    if target_id not in targets:
        raise RuntimeError(f"unknown DEPLOYMENT_TARGET {target_id!r}")
    return target_id, targets[target_id]


def _mlx_profile() -> tuple[dict[str, Any], dict[str, Any]]:
    document = load_yaml_mapping(_MACOS_RUNTIME)
    profile_id = os.getenv("MAIN_LLM_STATIC_PROFILE", "").strip() or str(document["default_profile"])
    return document["profiles"][profile_id], document.get("runtime") or {}


def _vllm_profile() -> tuple[dict[str, Any], dict[str, Any]]:
    document = load_yaml_mapping(_MAIN_PROFILES)
    profiles = document.get("profiles") or {}
    profile_id = os.getenv("MAIN_LLM_BOOT_PROFILE", "").strip() or str(document.get("default_profile", ""))
    if profile_id not in profiles:
        raise RuntimeError(f"cannot resolve main model profile {profile_id!r}")
    return profiles[profile_id], {}


def _nvidia_gpu() -> dict[str, Any] | None:
    """nvidia-smi가 있을 때만 GPU 지문을 채운다.

    macOS는 통합 메모리라 GPU 개수 개념이 없다. 그 경우 None을 돌려주고
    evaluator가 per_gpu 파생값을 계산하지 않는다.
    """
    if not shutil.which("nvidia-smi"):
        return None
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=False,
    )
    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not rows:
        return None
    name, memory_mib, driver = (part.strip() for part in rows[0].split(","))
    return {
        "model": name,
        "count": len(rows),
        "memory_bytes": int(float(memory_mib) * 1024 * 1024),
        "driver_version": driver,
    }


def collect() -> dict[str, Any]:
    target_id, target = _deployment_target()
    backend = str(target.get("runtime_backend", ""))
    if backend == "mlx-vlm":
        profile, runtime = _mlx_profile()
        runtime_flags = {
            "max_kv_size": runtime.get("max_kv_size"),
            "max_generation_tokens": runtime.get("max_generation_tokens"),
            "max_concurrency": runtime.get("max_concurrency"),
            "speculative_decoding": (runtime.get("speculative_decoding") or {}).get("kind")
            if (runtime.get("speculative_decoding") or {}).get("enabled") else None,
            "turboquant": (runtime.get("turboquant") or {}).get("enabled"),
        }
    else:
        profile, runtime_flags = _vllm_profile()
        runtime_flags = {
            "max_model_len": profile.get("max_model_len"),
            "gpu_memory_utilization": profile.get("gpu_memory_utilization"),
        }

    fingerprint: dict[str, Any] = {
        "git_commit": _git_commit(),
        "deployment_target": target_id,
        "runtime_backend": backend,
        "model_id": str(profile["model_id"]),
        "model_revision": str(profile["revision"]),
        "platform_image": os.getenv("PLATFORM_IMAGE", "").strip() or "unknown",
        "runtime_flags": runtime_flags,
        "host": {
            "platform": f"{platform.system()}/{platform.machine()}",
            "cpu": platform.processor() or platform.machine(),
        },
    }
    served = profile.get("served_model_name")
    if served:
        fingerprint["served_model_name"] = str(served)
    runtime_image = os.getenv("VLLM_IMAGE", "").strip()
    if runtime_image and backend != "mlx-vlm":
        fingerprint["runtime_image"] = runtime_image
    gpu = _nvidia_gpu()
    if gpu is not None:
        fingerprint["gpu"] = gpu
    return fingerprint
