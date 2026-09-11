#!/usr/bin/env python3
"""Target-aware local lifecycle command.

The public workflow is intentionally small.  This module owns target selection;
the existing scripts remain the implementation layer for individual operations.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.deployment_target import DeploymentTarget, load_deployment_target  # noqa: E402
from ai_model_serving.configuration import load_yaml_mapping  # noqa: E402
from ai_model_serving.access_profile import (  # noqa: E402
    access_profile_mismatches,
    access_profile_names,
    load_access_profile,
)
from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402
from ai_model_serving.settings_parts.env import DEFAULT_ENV_FILENAME, default_env_path  # noqa: E402
from scripts.build.pin_local_vllm_image import (  # noqa: E402
    pin_matching_env_values,
    resolve_local_image_id,
)

TARGETS_PATH = ROOT / "configs" / "deployment_targets.yaml"
SERVICES_PATH = ROOT / "configs" / "services.yaml"
ENV_PATH = default_env_path(ROOT)


def _run(*command: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _env_values() -> dict[str, str]:
    if not ENV_PATH.is_file():
        raise RuntimeError(".env is missing; run `make setup TARGET=<deployment-target>` first")
    return load_strict_env_file(ENV_PATH)


def _target(explicit: str | None, *, require_env: bool = True) -> DeploymentTarget:
    values = _env_values() if ENV_PATH.is_file() else {}
    configured = values.get("DEPLOYMENT_TARGET")
    if explicit and configured and explicit != configured:
        raise RuntimeError(
            f"requested target {explicit!r} differs from .env target {configured!r}; "
            "move the existing .env aside before initializing another target"
        )
    selected = explicit or configured
    if not selected:
        if require_env:
            raise RuntimeError("choose a target once: `make setup TARGET=<deployment-target>`")
        raise RuntimeError("TARGET is required for the first setup")
    return load_deployment_target(TARGETS_PATH, selected)


def _main_profile(target: DeploymentTarget, values: dict[str, str]) -> str:
    key = "MAIN_LLM_STATIC_PROFILE" if target.control_mode == "static" else "MAIN_LLM_BOOT_PROFILE"
    profile = values.get(key)
    if not profile:
        raise RuntimeError(f"{key} is missing from .env; rerun setup for target {target.target_id}")
    return profile


def _print_access(values: dict[str, str]) -> None:
    name = values.get("ACCESS_PROFILE", "").strip()
    if not name:
        print(
            "[platform] access: legacy/custom "
            f"(auth={values.get('AUTH_MODE', '<unset>')} "
            f"exposure={values.get('EXPOSURE_MODE', '<unset>')})"
        )
        return
    profile = load_access_profile(name)
    mismatches = access_profile_mismatches(name, values)
    if mismatches:
        raise RuntimeError(
            f"ACCESS_PROFILE={name!r} drift: " + "; ".join(mismatches)
        )
    print(
        f"[platform] access: {profile.name} — {profile.description} "
        f"(auth={values.get('AUTH_MODE')} exposure={values.get('EXPOSURE_MODE')})"
    )


def _gateway_probe(values: dict[str, str], path: str) -> tuple[str, bool]:
    host = values.get("GATEWAY_BIND_ADDR") or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    services = load_yaml_mapping(SERVICES_PATH).get("services", {})
    gateway = services.get("gateway") if isinstance(services, dict) else None
    if not isinstance(gateway, dict) or "default_host_port" not in gateway:
        raise RuntimeError("configs/services.yaml gateway.default_host_port is missing")
    port = values.get("GATEWAY_PORT") or str(gateway["default_host_port"])
    url = f"http://{host}:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return url, response.status == 200
    except (OSError, urllib.error.URLError):
        return url, False


def _wait_for_gateway(values: dict[str, str], path: str, timeout: int = 60) -> str:
    deadline = time.monotonic() + timeout
    while True:
        url, ready = _gateway_probe(values, path)
        if ready:
            return url
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Gateway did not become healthy within {timeout}s at {url}")
        time.sleep(1)


def setup_target(
    target: DeploymentTarget,
    main_profile: str | None,
    main_base_url: str | None,
    access_profile: str | None,
    confirm_access: bool,
) -> None:
    if target.runtime_backend == "mlx-vlm":
        _run(sys.executable, "scripts/runtime/macos_mlx_runtime.py", "doctor")
    if ENV_PATH.exists():
        command = [
            sys.executable,
            "scripts/config/setup_env.py",
            "--sync-env", "--env-file", DEFAULT_ENV_FILENAME,
            "--deployment-target", target.target_id,
        ]
        if main_profile:
            command += ["--main-profile", main_profile]
        if main_base_url:
            command += ["--main-llm-base-url", main_base_url]
        if access_profile:
            command += ["--access-profile", access_profile]
        if confirm_access:
            command += ["--confirm-access"]
        _run(*command)
        if access_profile:
            current = _env_values()
            if access_profile_mismatches(access_profile, current):
                print(
                    "[platform] access plan complete; existing .env was not changed. "
                    "Review the plan, then rerun with CONFIRM=access."
                )
                return
        print(f"[platform] preserving existing .env for target={target.target_id}")
    else:
        command = [
            sys.executable,
            "scripts/config/setup_env.py",
            "--profile", "compose",
            "--deployment-target", target.target_id,
        ]
        if main_profile:
            command += ["--main-profile", main_profile]
        if main_base_url:
            command += ["--main-llm-base-url", main_base_url]
        if access_profile:
            command += ["--access-profile", access_profile]
        if target.control_mode == "static" and not target.gateway_runtime_host and not main_base_url:
            raise RuntimeError(
                f"static target {target.target_id!r} needs MAIN_URL=http(s)://... on first setup"
            )
        _run(*command)

    if target.runtime_backend == "mlx-vlm":
        _run(sys.executable, "scripts/runtime/macos_mlx_runtime.py", "setup")
    _print_access(_env_values())
    print(f"[platform] setup ready: target={target.target_id}")
    print("[platform] next: make build, then make prepare")


def _registry_digest(image: str) -> bool:
    """Return whether the ref identifies an externally published artifact.

    A bare ``sha256:...`` is a local Docker image ID produced by this lifecycle.
    It must remain rebuildable; only a named registry digest is external input.
    """
    return "@sha256:" in image


def build_target(target: DeploymentTarget, *, no_cache: bool = False) -> None:
    values = _env_values()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    built: list[str] = []
    external: list[str] = []
    platform_image = values.get("PLATFORM_IMAGE", "")
    if _registry_digest(platform_image):
        external.append("platform")
        print(f"[platform] preserving external Platform image: {platform_image}")
    else:
        source_ref = platform_image
        build_ref = platform_image
        if not build_ref or build_ref.startswith("sha256:"):
            build_ref = f"ai-model-serving-platform:{version}"
        build_env = dict(os.environ)
        build_env["PLATFORM_IMAGE"] = build_ref
        if no_cache:
            build_env["PROJECT_BUILD_NO_CACHE"] = "1"
        _run("bash", "scripts/build/build_platform_image.sh", env=build_env)
        built.append("platform")
        if source_ref.startswith("sha256:"):
            new_image_id = resolve_local_image_id(build_ref)
            pin_matching_env_values(
                ENV_PATH,
                source_ref,
                new_image_id,
                keys=("PLATFORM_IMAGE",),
            )
            print(f"[platform] pinned local Platform image: PLATFORM_IMAGE={new_image_id}")

    if target.controllable:
        image = values.get("RISK_VLLM_IMAGE", "")
        if _registry_digest(image):
            external.append("vllm-unified")
            print(f"[platform] preserving external Unified vLLM image: {image}")
        else:
            build_ref = image
            if not build_ref or build_ref.startswith("sha256:"):
                build_ref = f"ai-model-serving-vllm-unified:{version}"
            build_env = dict(os.environ)
            build_env["VLLM_UNIFIED_BUILD_IMAGE"] = build_ref
            if no_cache:
                build_env["PROJECT_BUILD_NO_CACHE"] = "1"
            _run("bash", "scripts/build/build_vllm_unified_image.sh", env=build_env)
            new_image_id = resolve_local_image_id(build_ref)
            updated = pin_matching_env_values(ENV_PATH, image or build_ref, new_image_id)
            print(f"[platform] pinned local Unified image: {','.join(sorted(updated))}={new_image_id}")
            built.append("vllm-unified")

    mode = "rebuilt without cache reuse" if no_cache else "built with cache reuse"
    built_text = ",".join(built) if built else "none"
    external_text = ",".join(external) if external else "none"
    print(
        f"[platform] target artifacts: {mode}; built={built_text}; "
        f"external-preserved={external_text}; target={target.target_id}"
    )
    print("[platform] next: make prepare")


def prepare_target(target: DeploymentTarget) -> None:
    values = _env_values()
    if target.runtime_backend == "mlx-vlm":
        _run(sys.executable, "scripts/runtime/macos_mlx_runtime.py", "prepare")
    elif target.controllable:
        profile = _main_profile(target, values)
        _run(
            sys.executable,
            "scripts/models/prepare_main_model_cache.py",
            "--profile", profile,
            "--env-file", DEFAULT_ENV_FILENAME,
        )
    else:
        print("[platform] external Main runtime is not built or downloaded by this target")
    print(f"[platform] model inputs ready: target={target.target_id}")
    print("[platform] next: make up")


def up_target(target: DeploymentTarget) -> None:
    values = _env_values()
    if values.get("BUILD_PROFILE") == "local":
        _run("bash", "scripts/ops/up_services.sh")
        url = _wait_for_gateway(values, "/health")
        print(f"[platform] ready: app-only gateway={url}")
        return
    metal_started_here = False
    if target.runtime_backend == "mlx-vlm":
        status = subprocess.run(
            [sys.executable, "scripts/runtime/macos_mlx_runtime.py", "status"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        _run(sys.executable, "scripts/runtime/macos_mlx_runtime.py", "start-background")
        metal_started_here = status.returncode != 0
    try:
        if target.control_mode == "static":
            process_env = {**os.environ, "DEPLOYMENT_TARGET": target.target_id}
            _run("bash", "scripts/compose/static_main_compose.sh", "up", "-d", env=process_env)
        else:
            _run("bash", "scripts/compose/compose_up.sh")
            _run("bash", "scripts/ops/ready_full.sh")
        url = _wait_for_gateway(values, "/ready")
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        if metal_started_here:
            subprocess.run(
                [sys.executable, "scripts/runtime/macos_mlx_runtime.py", "stop"],
                cwd=ROOT,
                check=False,
            )
        raise

    print(f"[platform] ready: target={target.target_id} gateway={url}")


def down_target(target: DeploymentTarget) -> None:
    values = _env_values()
    if values.get("BUILD_PROFILE") == "local":
        _run("bash", "scripts/ops/down_services.sh", "--local")
        print("[platform] stopped: app-only")
        return
    compose_error: subprocess.CalledProcessError | None = None
    try:
        if target.control_mode == "static":
            process_env = {**os.environ, "DEPLOYMENT_TARGET": target.target_id}
            _run("bash", "scripts/compose/static_main_compose.sh", "down", env=process_env)
        else:
            _run("bash", "scripts/ops/down_services.sh", "--compose")
    except subprocess.CalledProcessError as exc:
        compose_error = exc
    finally:
        if target.runtime_backend == "mlx-vlm":
            _run(sys.executable, "scripts/runtime/macos_mlx_runtime.py", "stop")
    if compose_error is not None:
        raise compose_error
    print(f"[platform] stopped: target={target.target_id}")


def status_target(target: DeploymentTarget) -> int:
    values = _env_values()
    _print_access(values)
    if values.get("BUILD_PROFILE") == "local":
        result = subprocess.run(
            ["bash", "scripts/ops/status_services.sh", "--local"],
            cwd=ROOT,
            check=False,
        )
        url, gateway_ready = _gateway_probe(values, "/health")
        print(f"[platform] gateway: {'ready' if gateway_ready else 'unavailable'} {url}")
        return 0 if result.returncode == 0 and gateway_ready else 1
    healthy = True
    if target.runtime_backend == "mlx-vlm":
        result = subprocess.run(
            [sys.executable, "scripts/runtime/macos_mlx_runtime.py", "status"],
            cwd=ROOT,
            check=False,
        )
        healthy = result.returncode == 0
    if target.control_mode == "static":
        project = os.environ.get("STATIC_COMPOSE_PROJECT_NAME", "ai-model-serving-static")
        result = subprocess.run(
            [
                "docker", "ps", "-a",
                "--filter", f"label=com.docker.compose.project={project}",
                "--format", "{{.Names}}\t{{.Status}}",
            ],
            cwd=ROOT,
            check=False,
        )
        healthy = result.returncode == 0 and healthy
    else:
        result = subprocess.run(
            ["bash", "scripts/ops/status_services.sh", "--full"],
            cwd=ROOT,
            check=False,
        )
        healthy = result.returncode == 0 and healthy
    url, gateway_ready = _gateway_probe(values, "/ready")
    print(f"[platform] gateway: {'ready' if gateway_ready else 'unavailable'} {url}")
    return 0 if healthy and gateway_ready else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Target-aware local platform lifecycle")
    parser.add_argument(
        "action",
        choices=("setup", "build", "rebuild", "prepare", "up", "down", "status"),
    )
    parser.add_argument("--target")
    parser.add_argument("--main-profile")
    parser.add_argument("--main-base-url")
    parser.add_argument("--access-profile", choices=access_profile_names())
    parser.add_argument("--confirm-access", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.confirm_access and not args.access_profile:
            raise RuntimeError("CONFIRM=access requires ACCESS=local|private|edge")
        if args.action != "setup" and (
            args.main_profile or args.main_base_url or args.access_profile or args.confirm_access
        ):
            raise RuntimeError("MODEL, MAIN_URL, ACCESS and CONFIRM are setup-only options")
        target = _target(args.target, require_env=args.action != "setup")
        if args.action == "setup":
            setup_target(
                target,
                args.main_profile,
                args.main_base_url,
                args.access_profile,
                args.confirm_access,
            )
        elif args.action == "build":
            build_target(target)
        elif args.action == "rebuild":
            build_target(target, no_cache=True)
        elif args.action == "prepare":
            prepare_target(target)
        elif args.action == "up":
            up_target(target)
        elif args.action == "down":
            down_target(target)
        else:
            return status_target(target)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[platform] ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
